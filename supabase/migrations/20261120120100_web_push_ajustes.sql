-- Migration: Ajustes do Web Push apos revisao (achados M2 lado banco e B7).
-- Nova migration; NAO edita 20261120120000_web_push.sql. Idempotente (create or replace, drop if exists).
--
-- Motivo:
--  M2. Falha transitoria (5xx, 429, timeout, rede) nao pode revogar nem apagar inscricao valida.
--      * marcar_resultado_inscricao_push mantem a assinatura (p_id, p_ok, p_expirada); p_expirada passa a ter 3 valores
--        quando p_ok = false:
--          true  => 404/410 do servico push: revoga na hora (falhas + 1);
--          false => erro DEFINITIVO (400, 401, 403, 413): falhas + 1; ao chegar a 5 revoga;
--          null  => erro TRANSITORIO: nada muda (nem falhas, nem revogacao).
--        Chamadas antigas (false, false) seguem contando falha, como antes.
--      * inscricao com falhas >= 5 e REVOGADA (revogada_em = now()), nunca apagada na hora;
--      * DELETE fisico so de inscricao revogada ha mais de 30 dias (trigger e purgar_inscricoes_push).
--  B7. Notificacao com push_status = 'falhou' e push_tentativas >= 3 nunca saia do estado e ficava no indice da
--      fila. Agora reservar_push_notificacao a marca 'ignorado' (codigo 'esgotado') e o indice da fila deixa de
--      inclui-la.
--
-- Mantidos: RLS sem policy e sem grant na tabela, RPCs SECURITY DEFINER com search_path vazio, EXECUTE so service_role.
-- Fuso: timestamptz (UTC; exibicao em America/Sao_Paulo). Sem codigos de erro novos.

-- ---------------------------------------------------------------------------
-- 1. push_inscricoes_proteger: DELETE so de revogada ha mais de 30 dias (copia fiel, sem o "or falhas >= 5")
-- ---------------------------------------------------------------------------
create or replace function public.push_inscricoes_proteger()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ativa boolean;
  v_n     integer;
begin
  if tg_op = 'DELETE' then
    if coalesce(current_setting('sisveiculos.expurgo_push', true), '') = '1'
       and old.revogada_em is not null and old.revogada_em < now() - interval '30 days' then
      return old;
    end if;
    raise exception 'Operação % proibida em push_inscricoes: só o expurgo remove inscrições.', tg_op
      using errcode = 'AG003';
  end if;

  if tg_op = 'UPDATE' then
    if new.id is distinct from old.id
       or new.endpoint is distinct from old.endpoint
       or new.endpoint_hash is distinct from old.endpoint_hash
       or new.criada_em is distinct from old.criada_em then
      raise exception 'Identidade da inscrição de push é imutável.' using errcode = 'AG004';
    end if;
  end if;

  -- Ativacao: insercao ativa, reativacao (revogada -> ativa) ou troca de dono de uma ativa.
  if new.revogada_em is null
     and (tg_op = 'INSERT' or old.revogada_em is not null or old.usuario_id is distinct from new.usuario_id) then
    select p.ativo into v_ativa from public.perfis p where p.id = new.usuario_id;
    if not coalesce(v_ativa, false) then
      raise exception 'Somente usuário ativo pode se inscrever em notificações push.' using errcode = 'AG044';
    end if;
    -- Serializa inscricoes simultaneas do mesmo usuario (o limite nao pode ser furado em concorrencia).
    perform pg_advisory_xact_lock(hashtextextended('sisveiculos.push_inscricoes:' || new.usuario_id::text, 0));
    select count(*) into v_n from public.push_inscricoes x
     where x.usuario_id = new.usuario_id and x.revogada_em is null and x.id <> new.id;
    if v_n >= 10 then
      raise exception 'Limite de 10 aparelhos com notificações push por usuário.' using errcode = 'AG043';
    end if;
  end if;

  return new;
end;
$$;

revoke all on function public.push_inscricoes_proteger() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 2. marcar_resultado_inscricao_push: falha so conta quando o erro e definitivo
-- ---------------------------------------------------------------------------
-- p_ok = true                       => ultimo_sucesso_em = agora, falhas = 0
-- p_ok = false, p_expirada = true   => revoga na hora (404/410), falhas + 1
-- p_ok = false, p_expirada = false  => erro definitivo: falhas + 1; ao chegar a 5 a inscricao e REVOGADA
-- p_ok = false, p_expirada = null   => erro transitorio: nada muda
-- Inexistente => AG045. Retorno: {"id", "falhas", "revogada"}
create or replace function public.marcar_resultado_inscricao_push(
  p_id        uuid,
  p_ok        boolean,
  p_expirada  boolean default false
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_falhas   integer;
  v_revogada timestamptz;
begin
  if p_id is null or p_ok is null then
    raise exception 'Parâmetros do resultado de push inválidos.' using errcode = 'AG042';
  end if;

  perform 1 from public.push_inscricoes i where i.id = p_id for update;
  if not found then
    raise exception 'Inscrição de push não encontrada.' using errcode = 'AG045';
  end if;

  if p_ok then
    update public.push_inscricoes i
       set ultimo_sucesso_em = clock_timestamp(), falhas = 0
     where i.id = p_id
    returning i.falhas, i.revogada_em into v_falhas, v_revogada;
  elsif p_expirada is null then
    -- Erro transitorio (5xx, 429, timeout, rede): a inscricao nao e culpada; nada muda.
    select i.falhas, i.revogada_em into v_falhas, v_revogada
      from public.push_inscricoes i where i.id = p_id;
  else
    update public.push_inscricoes i
       set falhas = least(i.falhas + 1, 1000),
           revogada_em = case
                           when i.revogada_em is not null then i.revogada_em
                           when p_expirada or i.falhas + 1 >= 5 then clock_timestamp()
                         end
     where i.id = p_id
    returning i.falhas, i.revogada_em into v_falhas, v_revogada;
  end if;

  return jsonb_build_object('id', p_id, 'falhas', v_falhas, 'revogada', v_revogada is not null);
end;
$$;

-- ---------------------------------------------------------------------------
-- 3. purgar_inscricoes_push: revoga as ativas com 5 falhas ou mais; apaga SO revogadas ha mais de 30 dias
-- ---------------------------------------------------------------------------
create or replace function public.purgar_inscricoes_push()
returns integer
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_n integer;
begin
  -- Remanescentes do desenho anterior: ativa com 5 falhas ou mais e REVOGADA agora (apagada so apos 30 dias).
  update public.push_inscricoes
     set revogada_em = clock_timestamp()
   where revogada_em is null and falhas >= 5;

  -- Variavel LOCAL: vale so nesta transacao e e desligada logo apos o DELETE.
  perform set_config('sisveiculos.expurgo_push', '1', true);
  delete from public.push_inscricoes
   where revogada_em is not null and revogada_em < now() - interval '30 days';
  get diagnostics v_n = row_count;
  perform set_config('sisveiculos.expurgo_push', '', true);
  return v_n;
end;
$$;

-- ---------------------------------------------------------------------------
-- 4. reservar_push_notificacao: falhou com 3+ tentativas vira ignorado ('esgotado')
-- ---------------------------------------------------------------------------
-- Copia fiel da versao de 20261120120000 + passo "esgotado" logo apos o timeout do enviando travado (assim o
-- travado na 3a tentativa tambem e encerrado na mesma chamada) e antes do desvio da flag desligada.
create or replace function public.reservar_push_notificacao(p_limite integer default 20)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_limite integer := least(greatest(coalesce(p_limite, 20), 1), 100);
  v_ligado boolean;
  v_res    jsonb;
begin
  select coalesce(c.notificacao_push, false) into v_ligado from public.configuracoes c where c.id;

  update public.notificacoes n
     set push_status = 'falhou', push_erro_codigo = 'timeout'
   where n.id in (select x.id from public.notificacoes x
                   where x.push_status = 'enviando'
                     and x.push_reservado_em < clock_timestamp() - interval '15 minutes'
                   for update skip locked);

  -- B7: tentativas esgotadas (3) nao saem mais do estado 'falhou'; encerra como ignorado/esgotado.
  update public.notificacoes n
     set push_status = 'ignorado', push_erro_codigo = 'esgotado'
   where n.id in (select x.id from public.notificacoes x
                   where x.push_status = 'falhou' and x.push_tentativas >= 3
                   for update skip locked);

  if not coalesce(v_ligado, false) then
    update public.notificacoes n
       set push_status = 'ignorado', push_erro_codigo = 'desligado'
     where n.id in (select x.id from public.notificacoes x
                     where x.push_status = 'pendente' or (x.push_status = 'falhou' and x.push_tentativas < 3)
                     for update skip locked);
    return '[]'::jsonb;
  end if;

  update public.notificacoes n
     set push_status = 'ignorado', push_erro_codigo = 'inelegivel'
   where n.id in (select x.id from public.notificacoes x
                   where (x.push_status = 'pendente' or (x.push_status = 'falhou' and x.push_tentativas < 3))
                     and (x.criada_em < clock_timestamp() - interval '24 hours'
                          or not exists (select 1 from public.perfis p where p.id = x.usuario_id and p.ativo))
                   for update skip locked);

  with c as (
    select x.id
      from public.notificacoes x
     where x.push_status = 'pendente'
        or (x.push_status = 'falhou' and x.push_tentativas < 3
            and x.push_reservado_em < clock_timestamp() - interval '2 minutes')
     order by x.criada_em, x.id
     limit v_limite
       for update skip locked
  ),
  u as (
    update public.notificacoes n
       set push_status = 'enviando',
           push_tentativas = n.push_tentativas + 1,
           push_reservado_em = clock_timestamp(),
           push_erro_codigo = null
      from c
     where n.id = c.id
    returning n.id, n.tipo, n.usuario_id, n.requisicao_id, n.codigo, n.critica, n.criada_em
  )
  select coalesce(jsonb_agg(jsonb_build_object(
           'id', u.id, 'tipo', u.tipo, 'usuario_id', u.usuario_id, 'requisicao_id', u.requisicao_id,
           'codigo', u.codigo, 'critica', u.critica) order by u.criada_em, u.id), '[]'::jsonb)
    into v_res
    from u;
  return v_res;
end;
$$;

-- Regularizacao unica das linhas ja esgotadas (a transicao falhou -> ignorado e valida no trigger).
update public.notificacoes
   set push_status = 'ignorado', push_erro_codigo = 'esgotado'
 where push_status = 'falhou' and push_tentativas >= 3;

-- ---------------------------------------------------------------------------
-- 5. Indices da fila
-- ---------------------------------------------------------------------------
-- Fila: pendente, falhou com retentativa restante e enviando (para detectar travado). Sem as esgotadas.
drop index if exists public.ix_notificacoes_push_fila;
create index ix_notificacoes_push_fila
  on public.notificacoes (criada_em)
  where push_status = 'pendente' or push_status = 'enviando'
     or (push_status = 'falhou' and push_tentativas < 3);

-- Apoio ao passo "esgotado": so guarda linhas de passagem (reservar as encerra na chamada seguinte).
create index if not exists ix_notificacoes_push_esgotadas
  on public.notificacoes (id)
  where push_status = 'falhou' and push_tentativas >= 3;

-- ---------------------------------------------------------------------------
-- 6. Permissoes (reafirmadas; create or replace preserva, mas a migration e autocontida)
-- ---------------------------------------------------------------------------
revoke all on function public.reservar_push_notificacao(integer) from public, anon, authenticated;
revoke all on function public.marcar_resultado_inscricao_push(uuid, boolean, boolean) from public, anon, authenticated;
revoke all on function public.purgar_inscricoes_push() from public, anon, authenticated;
grant execute on function public.reservar_push_notificacao(integer) to service_role;
grant execute on function public.marcar_resultado_inscricao_push(uuid, boolean, boolean) to service_role;
grant execute on function public.purgar_inscricoes_push() to service_role;

-- ---------------------------------------------------------------------------
-- Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v_nome text;
  v_oid  oid;
  v_def  text;
begin
  foreach v_nome in array array[
    'public.reservar_push_notificacao(integer)', 'public.marcar_resultado_inscricao_push(uuid,boolean,boolean)',
    'public.purgar_inscricoes_push()'] loop
    v_oid := to_regprocedure(v_nome);
    if v_oid is null then
      raise exception 'RPC % ausente.', v_nome;
    end if;
    if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
       or not has_function_privilege('service_role', v_oid, 'execute') then
      raise exception 'RPC % com permissao errada.', v_nome;
    end if;
    if not exists (select 1 from pg_proc p where p.oid = v_oid and p.prosecdef
                    and p.proconfig is not null and 'search_path=""' = any (p.proconfig)) then
      raise exception 'RPC % deve ser SECURITY DEFINER com search_path vazio.', v_nome;
    end if;
  end loop;

  v_def := pg_get_functiondef('public.push_inscricoes_proteger()'::regprocedure);
  if position('falhas' in v_def) > 0 or position('AG003' in v_def) = 0 then
    raise exception 'push_inscricoes_proteger ainda permite apagar por falhas (regressao M2).';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.push_inscricoes'::regclass
                  and tgname = 'trg_push_inscricoes_proteger' and tgenabled = 'A' and not tgisinternal) then
    raise exception 'trg_push_inscricoes_proteger ausente ou nao sempre ativo.';
  end if;

  v_def := pg_get_functiondef('public.purgar_inscricoes_push()'::regprocedure);
  if position('falhas >= 5' in v_def) = 0 or position('30 days' in v_def) = 0 then
    raise exception 'purgar_inscricoes_push sem a revogacao por falhas ou sem os 30 dias.';
  end if;

  if not exists (select 1 from pg_indexes where schemaname = 'public' and tablename = 'notificacoes'
                  and indexname = 'ix_notificacoes_push_fila' and indexdef like '%push_tentativas < 3%') then
    raise exception 'ix_notificacoes_push_fila deve excluir as esgotadas.';
  end if;
  if exists (select 1 from public.notificacoes where push_status = 'falhou' and push_tentativas >= 3) then
    raise exception 'Restaram notificacoes de push esgotadas em falhou.';
  end if;
  if not exists (select 1 from pg_class where oid = 'public.push_inscricoes'::regclass and relrowsecurity)
     or exists (select 1 from pg_policy where polrelid = 'public.push_inscricoes'::regclass) then
    raise exception 'push_inscricoes deve ter RLS e nenhuma policy.';
  end if;
end
$$;
