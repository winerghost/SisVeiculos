-- Migration 20261107122440: endurecimento de acessos_login (revisao de seguranca da 122430).
-- Nova migration; NAO edita a 122430 (ja aplicada). Idempotente. Nao toca em `auditoria`/`perfis`.
-- A tabela acessos_login tem 0 linhas na aplicacao inicial; mesmo assim as CHECKs sao recriadas
-- sem NOT VALID (em reexecucao, validam as linhas existentes, que ja cumprem as regras).
--
-- Mudancas:
--  M1  ua_familia: lista FECHADA de Familia/Sistema, validada pela MESMA funcao no CHECK e na RPC
--      (acessos_login_ua_valida). Antes, o regex aceitava qualquer texto "Letras/Letras", p.ex.
--      'Joao Silva/Maria Souza' (nome de pessoa) - brecha de dado pessoal.
--  M2  purgar_acessos_login: p_meses minimo 12 (AG020 abaixo; maximo 1200). O piso de 6 meses no
--      gatilho (acessos_login_proteger) FICA, como defesa em profundidade.
--  B1  registrar_acesso_login exige sessao REAL do Auth: auth.sessions(id = p_session,
--      user_id = p_usuario); senao AG020. Regra do instante gravado: SEMPRE auth.sessions.created_at
--      quando nao nulo (p_ocorrido_em e so dado de entrada, validado na janela de -7 dias/+5 min
--      e usado apenas se created_at for NULL; a coluna do GoTrue e anulavel). Assim o backend nao
--      escolhe a data do login.
--      ACOPLAMENTO: a funcao le o schema INTERNO do Auth (auth.sessions: id, user_id, created_at).
--      Uma versao futura do GoTrue que renomeie/mova essas colunas quebra o registro (erro de
--      coluna inexistente; o backend e best-effort e nao derruba o login). Os testes sobem o
--      GoTrue local e criam a linha, entao a regressao aparece no CI ao subir a imagem do Auth.
--      O dono da funcao (postgres) le auth.sessions (verificado no Supabase local).
--  B2  service_role perde SELECT na tabela: toda leitura vai pelas RPCs SECURITY DEFINER.
--  B6  Piso de prefixo do IP (CHECK da tabela E RPC, mesma funcao acessos_login_ip_valido):
--      IPv4 entre /8 e /24; IPv6 entre /16 e /48. Recusa 0.0.0.0/0, ::/0, /1.. e IP completo.
--  B7  listar_acessos_login: p_offset acima de 100000 => AG020 (evita varredura sem limite).
--      `sessao_ref` (8 hex do sha256 da sessao) e so rotulo VISUAL: colide a partir de ~65 mil
--      linhas (paradoxo do aniversario em 32 bits); nao usar como chave nem para correlacao.
--
-- Erros (mensagens fixas, sem DETAIL, sem valores recebidos): AG019 usuario inexistente/inativo;
-- AG020 dado de acesso invalido (inclui sessao do Auth inexistente/de outro usuario, p_meses
-- fora de 12..1200, lote > 100, offset > 100000). AG021 em diante livres.

-- ---------------------------------------------------------------------------
-- 1. Funcoes de validacao (compartilhadas por CHECK e RPC; ninguem da API executa)
-- ---------------------------------------------------------------------------
create or replace function public.acessos_login_ua_valida(p_ua text)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select p_ua is null
         or (    split_part(p_ua, '/', 1) = any (array['Chrome', 'Firefox', 'Safari', 'Edge', 'Opera',
                                                      'Samsung Internet', 'Mobile Safari', 'Outro'])
             and split_part(p_ua, '/', 2) = any (array['Windows', 'macOS', 'Linux', 'Android', 'iOS',
                                                      'ChromeOS', 'Desconhecido'])
             -- exatamente "Familia/Sistema": nada alem das duas partes (sem 3o segmento).
             and p_ua = split_part(p_ua, '/', 1) || '/' || split_part(p_ua, '/', 2));
$$;

create or replace function public.acessos_login_ip_valido(p_ip cidr)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select p_ip is null
         or (family(p_ip) = 4 and masklen(p_ip) between 8 and 24)
         or (family(p_ip) = 6 and masklen(p_ip) between 16 and 48);
$$;

revoke all on function public.acessos_login_ua_valida(text) from public, anon, authenticated, service_role;
revoke all on function public.acessos_login_ip_valido(cidr) from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 2. CHECKs da tabela usando as funcoes (mesmos nomes de antes)
-- ---------------------------------------------------------------------------
alter table public.acessos_login drop constraint if exists ck_acessos_login_ua_familia;
alter table public.acessos_login
  add constraint ck_acessos_login_ua_familia check (public.acessos_login_ua_valida(ua_familia));

alter table public.acessos_login drop constraint if exists ck_acessos_login_ip_mascarado;
alter table public.acessos_login
  add constraint ck_acessos_login_ip_mascarado check (public.acessos_login_ip_valido(ip_mascarado));

comment on column public.acessos_login.ip_mascarado is
  'cidr mascarado: IPv4 entre /8 e /24, IPv6 entre /16 e /48. IP completo e prefixo vazio sao recusados.';
comment on column public.acessos_login.ua_familia is
  'Familia/Sistema de lista fechada (ex.: Chrome/Windows). Nunca o user-agent bruto nem texto livre.';

-- ---------------------------------------------------------------------------
-- 3. B2: service_role sem SELECT direto (leitura so pelas RPCs SECURITY DEFINER)
-- ---------------------------------------------------------------------------
revoke all on table public.acessos_login from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 4. RPC: registrar (mesma assinatura e retorno; agora valida sessao do Auth)
-- ---------------------------------------------------------------------------
create or replace function public.registrar_acesso_login(
  p_usuario      uuid,
  p_session      uuid,
  p_ocorrido_em  timestamptz,
  p_metodo       text,
  p_aal          text,
  p_ip           text,
  p_ua           text
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ip         cidr;
  v_ativo      boolean;
  v_n          integer;
  v_criada_em  timestamptz;
  v_ocorrido   timestamptz;
begin
  -- Validacao ANTES do INSERT, com mensagens fixas (sem valores recebidos, sem DETAIL).
  if p_usuario is null or p_session is null or p_ocorrido_em is null then
    raise exception 'Dados obrigatórios do acesso ausentes.' using errcode = 'AG020';
  end if;
  if p_metodo is not null and p_metodo not in ('password', 'totp', 'outro') then
    raise exception 'Método de acesso inválido.' using errcode = 'AG020';
  end if;
  if p_aal is not null and p_aal not in ('aal1', 'aal2') then
    raise exception 'Nível de autenticação inválido.' using errcode = 'AG020';
  end if;
  if not public.acessos_login_ua_valida(p_ua) then
    raise exception 'Família do navegador fora da lista permitida.' using errcode = 'AG020';
  end if;
  if p_ip is not null then
    -- Recusa atalhos classful do cast (ex.: '10' viraria 10.0.0.0/8): exige '.', ':' ou '/'.
    if char_length(p_ip) > 43 or p_ip !~ '[.:/]' then
      raise exception 'IP deve ser enviado mascarado.' using errcode = 'AG020';
    end if;
    begin
      v_ip := p_ip::cidr;
    exception when invalid_text_representation then
      -- Captura aqui para a mensagem padrao do Postgres (que repetiria o IP) nao ir ao log.
      raise exception 'IP deve ser enviado mascarado.' using errcode = 'AG020';
    end;
    if not public.acessos_login_ip_valido(v_ip) then
      raise exception 'IP deve ser enviado mascarado.' using errcode = 'AG020';
    end if;
  end if;

  select ativo into v_ativo from public.perfis where id = p_usuario;
  if not found or not v_ativo then
    raise exception 'Usuário inexistente ou inativo.' using errcode = 'AG019';
  end if;

  -- Sessao ja registrada: idempotente, antes da janela e da checagem no Auth (reenvio tardio,
  -- ou sessao ja encerrada no Auth, nao falha).
  -- Filtra tambem por usuario: sessao ja registrada de OUTRO usuario cai na checagem do Auth e
  -- recebe a mesma mensagem AG020 (nao revela que o session_id existe no historico).
  if exists (select 1 from public.acessos_login where session_id = p_session and usuario_id = p_usuario) then
    return false;
  end if;

  if p_ocorrido_em > now() + interval '5 minutes' or p_ocorrido_em < now() - interval '7 days' then
    raise exception 'Momento do acesso fora da janela permitida.' using errcode = 'AG020';
  end if;

  -- B1: a sessao precisa existir no Auth e pertencer ao usuario. Mensagem unica (nao distingue
  -- "inexistente" de "de outro usuario": nao confirma sessoes alheias).
  select s.created_at into v_criada_em
    from auth.sessions s
   where s.id = p_session and s.user_id = p_usuario;
  if not found then
    raise exception 'Sessão de autenticação inválida.' using errcode = 'AG020';
  end if;
  -- Instante gravado: o do Auth quando existir; p_ocorrido_em so se created_at vier NULL.
  v_ocorrido := coalesce(v_criada_em, p_ocorrido_em);

  insert into public.acessos_login (usuario_id, session_id, ocorrido_em, metodo, aal, ip_mascarado, ua_familia)
  values (p_usuario, p_session, v_ocorrido, p_metodo, p_aal, v_ip, p_ua)
  on conflict (session_id) do nothing;
  get diagnostics v_n = row_count;
  return v_n = 1;
end;
$$;

revoke all on function public.registrar_acesso_login(uuid, uuid, timestamptz, text, text, text, text)
  from public, anon, authenticated;
grant execute on function public.registrar_acesso_login(uuid, uuid, timestamptz, text, text, text, text)
  to service_role;

-- ---------------------------------------------------------------------------
-- 5. RPC: listar (teto de offset 100000)
-- ---------------------------------------------------------------------------
-- Contrato inalterado, salvo: p_offset > 100000 => AG020. Limite 1..100 e offset < 0 ajustados.
-- `sessao_ref` e so rotulo visual (8 hex): colisoes a partir de ~65 mil linhas.
create or replace function public.listar_acessos_login(
  p_usuario  uuid default null,
  p_de       timestamptz default null,
  p_ate      timestamptz default null,
  p_limite   integer default 50,
  p_offset   integer default 0
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_limite integer := least(greatest(coalesce(p_limite, 50), 1), 100);
  v_offset integer := greatest(coalesce(p_offset, 0), 0);
  v_res    jsonb;
begin
  if v_offset > 100000 then
    raise exception 'Deslocamento acima do máximo permitido.' using errcode = 'AG020';
  end if;

  with f as (
    select a.id, a.usuario_id, a.session_id, a.ocorrido_em, a.metodo, a.aal,
           a.ip_mascarado, a.ua_familia
      from public.acessos_login a
     where (p_usuario is null or a.usuario_id = p_usuario)
       and (p_de is null or a.ocorrido_em >= p_de)
       and (p_ate is null or a.ocorrido_em < p_ate)
  ),
  pag as (
    select f.id, f.usuario_id, p.nome, f.ocorrido_em, f.metodo, f.aal,
           f.ip_mascarado::text as ip_mascarado, f.ua_familia,
           left(encode(sha256(convert_to(f.session_id::text, 'UTF8')), 'hex'), 8) as sessao_ref
      from f
      join public.perfis p on p.id = f.usuario_id
     order by f.ocorrido_em desc, f.id desc
     limit v_limite offset v_offset
  )
  select jsonb_build_object(
           'total', (select count(*) from f),
           'limite', v_limite,
           'offset', v_offset,
           'itens', coalesce((select jsonb_agg(to_jsonb(pag) order by pag.ocorrido_em desc, pag.id desc)
                                from pag), '[]'::jsonb))
    into v_res;
  return v_res;
end;
$$;

revoke all on function public.listar_acessos_login(uuid, timestamptz, timestamptz, integer, integer)
  from public, anon, authenticated;
grant execute on function public.listar_acessos_login(uuid, timestamptz, timestamptz, integer, integer)
  to service_role;

-- ---------------------------------------------------------------------------
-- 6. RPC: expurgo (p_meses minimo 12)
-- ---------------------------------------------------------------------------
-- O gatilho acessos_login_proteger mantem o piso de 6 meses (defesa em profundidade).
create or replace function public.purgar_acessos_login(p_meses integer default 12)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_n integer;
begin
  if p_meses is null or p_meses < 12 or p_meses > 1200 then
    raise exception 'Prazo de retenção fora do intervalo permitido (12 a 1200 meses).' using errcode = 'AG020';
  end if;

  -- Variavel LOCAL: vale so nesta transacao e e desligada logo apos o DELETE.
  perform set_config('sisveiculos.expurgo_acessos', '1', true);
  delete from public.acessos_login
   where ocorrido_em < now() - make_interval(months => p_meses);
  get diagnostics v_n = row_count;
  perform set_config('sisveiculos.expurgo_acessos', '', true);
  return v_n;
end;
$$;

revoke all on function public.purgar_acessos_login(integer) from public, anon, authenticated;
grant execute on function public.purgar_acessos_login(integer) to service_role;

-- ---------------------------------------------------------------------------
-- 7. Agendamento CONDICIONAL com pg_cron (nunca falha a migration)
-- ---------------------------------------------------------------------------
-- cron.schedule com o mesmo nome atualiza o job (idempotente). Sem pg_cron: so aviso; o expurgo
-- deve ser agendado por job interno do backend (docs/acessos-login.md).
do $$
begin
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    begin
      perform cron.schedule('purgar-acessos-login', '17 3 * * *',
                            'select public.purgar_acessos_login(12)');
    exception when others then
      raise notice 'pg_cron presente, mas o agendamento do expurgo falhou; agende manualmente (docs/acessos-login.md).';
    end;
  else
    raise notice 'pg_cron ausente: expurgo de acessos_login nao agendado (docs/acessos-login.md).';
  end if;
end
$$;

-- ---------------------------------------------------------------------------
-- 8. Trava: a migration falha se sobrar acesso indevido
-- ---------------------------------------------------------------------------
do $$
declare
  r text;
  f text;
begin
  if not (select relrowsecurity from pg_class where oid = 'public.acessos_login'::regclass) then
    raise exception 'public.acessos_login sem RLS.';
  end if;
  -- Nenhum papel da API tem qualquer privilegio na tabela (nem coluna): so RPCs.
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if has_table_privilege(r, 'public.acessos_login', 'select, insert, update, delete, truncate, trigger, references')
       or has_any_column_privilege(r, 'public.acessos_login', 'select, insert, update, references') then
      raise exception '% tem privilegio direto em public.acessos_login.', r;
    end if;
  end loop;

  -- RPCs: so service_role (anon/authenticated/PUBLIC sem EXECUTE).
  foreach f in array array[
    'public.registrar_acesso_login(uuid, uuid, timestamptz, text, text, text, text)',
    'public.listar_acessos_login(uuid, timestamptz, timestamptz, integer, integer)',
    'public.ultimo_acesso_por_usuario(uuid[])',
    'public.purgar_acessos_login(integer)'
  ] loop
    foreach r in array array['anon', 'authenticated'] loop
      if has_function_privilege(r, f::regprocedure, 'execute') then
        raise exception '% executa %.', r, f;
      end if;
    end loop;
    if not has_function_privilege('service_role', f::regprocedure, 'execute') then
      raise exception 'service_role sem EXECUTE em %.', f;
    end if;
    if exists (select 1 from pg_proc p2, aclexplode(coalesce(p2.proacl, acldefault('f', p2.proowner))) a
                where p2.oid = f::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE') then
      raise exception 'PUBLIC executa %.', f;
    end if;
    if not exists (select 1 from pg_proc p2
                    where p2.oid = f::regprocedure and p2.prosecdef
                      and array_to_string(p2.proconfig, ',') like '%search_path=%') then
      raise exception '% sem SECURITY DEFINER ou search_path fixo.', f;
    end if;
  end loop;

  -- Funcoes-gatilho e de validacao: ninguem da API (nem PUBLIC) executa; search_path fixo.
  foreach f in array array[
    'public.acessos_login_proteger()',
    'public.acessos_login_ua_valida(text)',
    'public.acessos_login_ip_valido(cidr)'
  ] loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if has_function_privilege(r, f::regprocedure, 'execute') then
        raise exception '% executa %.', r, f;
      end if;
    end loop;
    if exists (select 1 from pg_proc p2, aclexplode(coalesce(p2.proacl, acldefault('f', p2.proowner))) a
                where p2.oid = f::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE') then
      raise exception 'PUBLIC executa %.', f;
    end if;
    if not exists (select 1 from pg_proc p2
                    where p2.oid = f::regprocedure
                      and array_to_string(p2.proconfig, ',') like '%search_path=%') then
      raise exception '% sem search_path fixo.', f;
    end if;
  end loop;

  -- As CHECKs usam as funcoes (CHECK e RPC nao divergem).
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.acessos_login'::regclass
                    and conname = 'ck_acessos_login_ua_familia'
                    and pg_get_constraintdef(oid) like '%acessos_login_ua_valida%')
     or not exists (select 1 from pg_constraint
                     where conrelid = 'public.acessos_login'::regclass
                       and conname = 'ck_acessos_login_ip_mascarado'
                       and pg_get_constraintdef(oid) like '%acessos_login_ip_valido%') then
    raise exception 'CHECKs de ua_familia/ip_mascarado nao usam as funcoes de validacao.';
  end if;

  -- Premissas da leitura de auth.sessions pelo dono (acoplamento ao schema interno do Auth).
  if not has_table_privilege(current_user, 'auth.sessions', 'select') then
    raise exception 'Dono sem SELECT em auth.sessions.';
  end if;
  if not coalesce((select rolbypassrls from pg_roles where rolname = current_user), false) then
    raise exception 'Dono sem BYPASSRLS: a leitura de auth.sessions pode ser filtrada.';
  end if;
  if (select count(*) from pg_attribute
       where attrelid = 'auth.sessions'::regclass and not attisdropped and attnum > 0
         and attname in ('id', 'user_id', 'created_at')) <> 3 then
    raise exception 'auth.sessions sem as colunas id, user_id e created_at.';
  end if;
end
$$;

-- Faz o PostgREST enxergar as funcoes recriadas.
notify pgrst, 'reload schema';
