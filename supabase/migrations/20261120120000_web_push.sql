-- Migration: Notificacoes Web Push (flag notificacao_push, push_inscricoes, fila push em notificacoes).
-- Nova migration; nao edita as anteriores. Idempotente. Depende de 20261110120000 (notificacoes, fila de e-mail
-- como modelo) e 20261119120200 (configuracoes_validar com 13 chaves). Contrato: docs/plano-web-push.md.
--
-- Motivo: avisar o aparelho (Web Push, sem Capacitor) dos mesmos eventos do "sino". O push REAPROVEITA a tabela
-- notificacoes como fila de saida, como o e-mail: os destinatarios ja vem dos gatilhos do banco; nada aqui toca
-- regra de negocio nem transicao de estado. Entrega "pelo menos uma vez"; falha de push nunca desfaz operacao.
--
-- 1. configuracoes.notificacao_push boolean not null default false (14a chave; sem dependencias) e
--    configuracoes_validar: copia fiel da versao de 20261119120200 (13 chaves) + a 14a. Admin ativo (AG002),
--    ator zerado, AG010 e uma linha de auditoria por chave alterada preservados.
-- 2. public.push_inscricoes (RLS ligado, SEM policy => deny-by-default; sem nenhum grant a anon/authenticated/
--    service_role: o backend so usa as RPCs). Indice unico por endpoint_hash (sha256 hex do endpoint); no maximo
--    10 inscricoes ATIVAS por usuario; so perfil ATIVO se inscreve; um endpoint pertence a um usuario por vez
--    (reinscrever REATRIBUI). Endpoint e chaves sao dado sensivel: ficam so aqui e so a RPC listar_inscricoes_push
--    os devolve (ao backend, para enviar). DELETE fisico so no purgar_inscricoes_push; TRUNCATE nunca.
-- 3. notificacoes.push_status / push_tentativas / push_enviado_em (+ push_erro_codigo, push_reservado_em, como
--    email_erro_codigo/email_reservado_em). Linhas antigas = 'ignorado'; linhas novas = 'pendente' (DEFAULT da
--    coluna: privado.notificar e registrar_notificacao_sistema nao precisam mudar). Todos os tipos geram push (nao
--    so criticos). Maquina de estados no trigger notificacoes_proteger (copia fiel + bloco push):
--      pendente -> enviando | ignorado;  falhou -> enviando | ignorado;  enviando -> enviado | falhou | ignorado;
--      enviado e ignorado sao terminais; entrar em 'enviando' conta exatamente uma tentativa.
-- 4. RPCs (SECURITY DEFINER, search_path vazio, EXECUTE so service_role):
--      reservar_push_notificacao(p_limite=20)   marcar_push_notificacao(p_id, p_status, p_codigo)
--      registrar_inscricao_push(p_usuario, p_endpoint, p_p256dh, p_auth)   revogar_inscricao_push(p_usuario, p_endpoint)
--      listar_inscricoes_push(p_usuario)        marcar_resultado_inscricao_push(p_id, p_ok, p_expirada)
--      purgar_inscricoes_push()
--
-- Erros novos (AG042+ livres; a ultima migration usou ate AG041):
--   AG042 parametro invalido de push (usuario nulo, endpoint, chaves, status, codigo)
--   AG043 limite de 10 inscricoes ativas por usuario
--   AG044 perfil inexistente ou inativo nao pode se inscrever
--   AG045 inscricao de push nao encontrada
-- Reaproveitados: AG001 transicao invalida do push; AG003 DELETE/TRUNCATE fora do expurgo; AG004 coluna imutavel;
--   AG029 notificacao nao encontrada; AG002/AG010 (configuracoes_validar).
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).
-- LGPD: nenhuma coluna de texto livre; payload do push e montado pelo backend (generico, sem PII).

-- ---------------------------------------------------------------------------
-- 1. configuracoes.notificacao_push + configuracoes_validar (14 chaves)
-- ---------------------------------------------------------------------------
alter table public.configuracoes
  add column if not exists notificacao_push boolean not null default false;

comment on column public.configuracoes.notificacao_push is
  'Recurso opcional: notificacoes Web Push para os aparelhos inscritos (desligado por padrao). Sem dependencias.';

create or replace function public.configuracoes_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator  uuid := new.alterado_por;
  v_chave text;
begin
  new.alterado_por := null;  -- nunca persiste

  if new.quilometragem is not distinct from old.quilometragem
     and new.abastecimento_manutencao is not distinct from old.abastecimento_manutencao
     and new.checklist is not distinct from old.checklist
     and new.mfa is not distinct from old.mfa
     and new.relatorio_email is not distinct from old.relatorio_email
     and new.relatorio_whatsapp is not distinct from old.relatorio_whatsapp
     and new.notificacao_email_criticos is not distinct from old.notificacao_email_criticos
     and new.motorista is not distinct from old.motorista
     and new.motorista_registra is not distinct from old.motorista_registra
     and new.motorista_localizacao is not distinct from old.motorista_localizacao
     and new.vigilante_km is not distinct from old.vigilante_km
     and new.autorizador_decide_propria is not distinct from old.autorizador_decide_propria
     and new.autorizador_km is not distinct from old.autorizador_km
     and new.notificacao_push is not distinct from old.notificacao_push then
    return new;
  end if;

  perform privado.exigir_admin_ativo(v_ator);

  if new.abastecimento_manutencao and not new.quilometragem then
    raise exception 'Abastecimento e manutenção exigem a Quilometragem ligada.'
      using errcode = 'AG010';
  end if;

  if new.motorista_registra and not new.motorista then
    raise exception 'O registro de saída e chegada pelo motorista exige o Perfil Motorista ligado.'
      using errcode = 'AG010';
  end if;

  if new.motorista_localizacao and not new.motorista_registra then
    raise exception 'A localização do motorista exige o registro de saída e chegada pelo motorista ligado.'
      using errcode = 'AG010';
  end if;

  if new.vigilante_km and not new.quilometragem then
    if old.quilometragem and old.vigilante_km then
      -- Caminho inverso (achado B4): desligar a Quilometragem com o KM do vigilante ligado.
      raise exception 'Desligue o KM do vigilante antes de desligar a Quilometragem.'
        using errcode = 'AG010';
    end if;
    raise exception 'O KM do vigilante exige a Quilometragem ligada.'
      using errcode = 'AG010';
  end if;

  if new.autorizador_km and not new.quilometragem then
    if old.quilometragem and old.autorizador_km then
      -- Caminho inverso: desligar a Quilometragem com o KM do autorizador ligado.
      raise exception 'Desligue o KM do autorizador antes de desligar a Quilometragem.'
        using errcode = 'AG010';
    end if;
    raise exception 'O KM do autorizador exige a Quilometragem ligada.'
      using errcode = 'AG010';
  end if;

  for v_chave in
    select chave
      from jsonb_object_keys(to_jsonb(new)) as chave
     where chave = any (array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa',
                              'relatorio_email', 'relatorio_whatsapp', 'notificacao_email_criticos',
                              'motorista', 'motorista_registra', 'motorista_localizacao',
                              'vigilante_km', 'autorizador_decide_propria', 'autorizador_km',
                              'notificacao_push'])
       and (to_jsonb(new) -> chave) is distinct from (to_jsonb(old) -> chave)
  loop
    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
    values ('configuracoes', null, 'configuracao_alterada', v_ator,
            to_jsonb(old) ->> v_chave, to_jsonb(new) ->> v_chave,
            jsonb_build_object('recurso', v_chave));
  end loop;

  return new;
end;
$$;

revoke all on function public.configuracoes_validar() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 2. push_inscricoes
-- ---------------------------------------------------------------------------
create table if not exists public.push_inscricoes (
  id                 uuid primary key default gen_random_uuid(),
  usuario_id         uuid not null references public.perfis (id) on delete restrict,
  endpoint           text not null,
  endpoint_hash      text not null,
  p256dh             text not null,
  auth               text not null,
  criada_em          timestamptz not null default now(),
  ultimo_sucesso_em  timestamptz,
  falhas             integer not null default 0,
  revogada_em        timestamptz,

  constraint ck_push_inscricoes_endpoint check (endpoint ~ '^https://[^[:space:]]+$' and length(endpoint) <= 2048),
  constraint ck_push_inscricoes_hash check (endpoint_hash ~ '^[0-9a-f]{64}$'),
  constraint ck_push_inscricoes_p256dh check (p256dh ~ '^[A-Za-z0-9_-]{16,256}=*$'),
  constraint ck_push_inscricoes_auth check (auth ~ '^[A-Za-z0-9_-]{8,128}=*$'),
  constraint ck_push_inscricoes_falhas check (falhas between 0 and 1000)
);

comment on table public.push_inscricoes is
  'Inscricoes Web Push (endpoint + chaves: dado sensivel). Acesso so por RPC service_role. Max. 10 ativas por usuario; DELETE so no purgar_inscricoes_push.';
comment on column public.push_inscricoes.endpoint_hash is
  'sha256 (hex) do endpoint, calculado pela RPC. Unico: um endpoint pertence a um usuario por vez.';

create unique index if not exists ux_push_inscricoes_endpoint_hash
  on public.push_inscricoes (endpoint_hash);
-- Inscricoes ativas por usuario (contagem do limite e listagem para envio). FK sem indice automatico.
create index if not exists ix_push_inscricoes_usuario_ativas
  on public.push_inscricoes (usuario_id) where revogada_em is null;
-- Expurgo.
create index if not exists ix_push_inscricoes_revogada_em
  on public.push_inscricoes (revogada_em) where revogada_em is not null;

alter table public.push_inscricoes enable row level security;
-- Sem policy de proposito (contrato): RLS ligado + nenhum grant = negado a anon/authenticated.
revoke all on table public.push_inscricoes from public, anon, authenticated, service_role;

-- Protecao: identidade imutavel; limite e perfil ativo na (re)ativacao; DELETE so no expurgo; TRUNCATE nunca.
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
       and ((old.revogada_em is not null and old.revogada_em < now() - interval '30 days')
            or old.falhas >= 5) then
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

drop trigger if exists trg_push_inscricoes_proteger on public.push_inscricoes;
create trigger trg_push_inscricoes_proteger
  before insert or update or delete on public.push_inscricoes
  for each row execute function public.push_inscricoes_proteger();
alter table public.push_inscricoes enable always trigger trg_push_inscricoes_proteger;

drop trigger if exists trg_push_inscricoes_sem_truncate on public.push_inscricoes;
create trigger trg_push_inscricoes_sem_truncate
  before truncate on public.push_inscricoes
  for each statement execute function public.bloquear_alteracao();
alter table public.push_inscricoes enable always trigger trg_push_inscricoes_sem_truncate;

-- ---------------------------------------------------------------------------
-- 3. notificacoes: colunas push_* (antigas = ignorado; novas = pendente)
-- ---------------------------------------------------------------------------
-- Adiciona com default 'ignorado' (preenche as linhas existentes) e so depois troca o default para 'pendente'.
alter table public.notificacoes
  add column if not exists push_status text not null default 'ignorado',
  add column if not exists push_tentativas integer not null default 0,
  add column if not exists push_enviado_em timestamptz,
  add column if not exists push_erro_codigo text,
  add column if not exists push_reservado_em timestamptz;

alter table public.notificacoes alter column push_status set default 'pendente';

alter table public.notificacoes drop constraint if exists ck_notificacoes_push_status;
alter table public.notificacoes add constraint ck_notificacoes_push_status
  check (push_status in ('pendente', 'enviando', 'enviado', 'falhou', 'ignorado'));
alter table public.notificacoes drop constraint if exists ck_notificacoes_push_tentativas;
alter table public.notificacoes add constraint ck_notificacoes_push_tentativas
  check (push_tentativas between 0 and 100);
alter table public.notificacoes drop constraint if exists ck_notificacoes_push_enviado_em_coerente;
alter table public.notificacoes add constraint ck_notificacoes_push_enviado_em_coerente
  check ((push_status = 'enviado') = (push_enviado_em is not null));
alter table public.notificacoes drop constraint if exists ck_notificacoes_push_erro_formato;
alter table public.notificacoes add constraint ck_notificacoes_push_erro_formato
  check (push_erro_codigo is null or push_erro_codigo ~ '^[A-Za-z0-9_.-]{1,40}$');

comment on column public.notificacoes.push_status is
  'Fila do Web Push: pendente, enviando, enviado, falhou, ignorado. Linhas anteriores a feature = ignorado.';

-- Fila de push (pendente + retentativas + enviando travado). Pequeno: so linhas em andamento.
create index if not exists ix_notificacoes_push_fila
  on public.notificacoes (criada_em) where push_status in ('pendente', 'falhou', 'enviando');

-- notificacoes_proteger: copia fiel da versao de 20261110120000 + bloco da maquina de estados do push.
create or replace function public.notificacoes_proteger()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'DELETE' then
    if coalesce(current_setting('sisveiculos.expurgo_notificacoes', true), '') = '1'
       and old.criada_em < now() - interval '30 days' then
      return old;
    end if;
    raise exception 'Operação % proibida em notificacoes: o histórico só é removido pelo expurgo.', tg_op
      using errcode = 'AG003';
  end if;

  -- UPDATE ------------------------------------------------------------------
  if new.id is distinct from old.id
     or new.usuario_id is distinct from old.usuario_id
     or new.tipo is distinct from old.tipo
     or new.requisicao_id is distinct from old.requisicao_id
     or new.referencia_id is distinct from old.referencia_id
     or new.codigo is distinct from old.codigo
     or new.critica is distinct from old.critica
     or new.criada_em is distinct from old.criada_em then
    raise exception 'Campos de identidade da notificação são imutáveis.' using errcode = 'AG004';
  end if;

  -- lida_em: null -> agora (carimbo do servidor); depois imutavel.
  if old.lida_em is not null then
    if new.lida_em is distinct from old.lida_em then
      raise exception 'lida_em já gravado não pode mudar.' using errcode = 'AG004';
    end if;
  elsif new.lida_em is not null then
    new.lida_em := clock_timestamp();
  end if;

  -- E-mail: nunca nasce depois (null <-> valor) e segue a maquina de estados.
  if (old.email_status is null) <> (new.email_status is null) then
    raise exception 'O estado de e-mail não pode ser criado nem removido depois.' using errcode = 'AG004';
  end if;
  if new.email_status is not null then
    if new.email_status = old.email_status then
      if new.email_tentativas < old.email_tentativas
         or (old.email_status in ('enviado', 'ignorado')
             and (new.email_tentativas, new.email_enviado_em, new.email_erro_codigo, new.email_reservado_em)
                 is distinct from
                 (old.email_tentativas, old.email_enviado_em, old.email_erro_codigo, old.email_reservado_em)) then
        raise exception 'Estado de e-mail terminal ou tentativas decrescentes.' using errcode = 'AG001';
      end if;
    else
      if not ((old.email_status = 'pendente' and new.email_status in ('enviando', 'ignorado'))
           or (old.email_status = 'falhou'   and new.email_status in ('enviando', 'ignorado'))
           or (old.email_status = 'enviando' and new.email_status in ('enviado', 'falhou', 'ignorado'))) then
        raise exception 'Transição inválida do e-mail: % -> %.', old.email_status, new.email_status
          using errcode = 'AG001';
      end if;
      -- Entrar em 'enviando' conta exatamente uma tentativa; as demais transicoes nao mexem nelas.
      if new.email_tentativas is distinct from
         old.email_tentativas + (case when new.email_status = 'enviando' then 1 else 0 end) then
        raise exception 'Contagem de tentativas de e-mail inconsistente.' using errcode = 'AG001';
      end if;
    end if;
  end if;

  -- Push (NOVO): push_status nunca e nulo; mesma maquina de estados do e-mail.
  if new.push_status = old.push_status then
    if new.push_tentativas < old.push_tentativas
       or (old.push_status in ('enviado', 'ignorado')
           and (new.push_tentativas, new.push_enviado_em, new.push_erro_codigo, new.push_reservado_em)
               is distinct from
               (old.push_tentativas, old.push_enviado_em, old.push_erro_codigo, old.push_reservado_em)) then
      raise exception 'Estado de push terminal ou tentativas decrescentes.' using errcode = 'AG001';
    end if;
  else
    if not ((old.push_status = 'pendente' and new.push_status in ('enviando', 'ignorado'))
         or (old.push_status = 'falhou'   and new.push_status in ('enviando', 'ignorado'))
         or (old.push_status = 'enviando' and new.push_status in ('enviado', 'falhou', 'ignorado'))) then
      raise exception 'Transição inválida do push: % -> %.', old.push_status, new.push_status
        using errcode = 'AG001';
    end if;
    if new.push_tentativas is distinct from
       old.push_tentativas + (case when new.push_status = 'enviando' then 1 else 0 end) then
      raise exception 'Contagem de tentativas de push inconsistente.' using errcode = 'AG001';
    end if;
  end if;

  return new;
end;
$$;

revoke all on function public.notificacoes_proteger() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- 4. RPCs da fila de push
-- ---------------------------------------------------------------------------
-- reservar_push_notificacao: devolve jsonb [{id, tipo, usuario_id, requisicao_id, codigo, critica}] e move
-- pendente/falhou(<3 tentativas, 2 min apos a ultima reserva) -> enviando, contando a tentativa. FOR UPDATE SKIP
-- LOCKED: workers simultaneos nunca recebem a mesma linha. Antes de reservar:
--  * enviando travado ha mais de 15 min => falhou ('timeout');
--  * flag desligada => tudo que estava pendente/falhou(<3) vira ignorado ('desligado') e devolve [];
--  * pendente/falhou(<3) inelegivel (perfil inativo ou criada ha 24 h ou mais) => ignorado ('inelegivel').
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

-- marcar_push_notificacao: resultado do envio. p_status 'enviado' | 'falhou' | 'ignorado'. 'falhou' e 'ignorado'
-- aceitam p_codigo (^[A-Za-z0-9_.-]{1,40}$; 'falhou' sem codigo vira 'desconhecido'). Transicao validada pelo
-- trigger (AG001). Inexistente => AG029. Retorno: {"id", "push_status", "push_tentativas"}
create or replace function public.marcar_push_notificacao(
  p_id      uuid,
  p_status  text,
  p_codigo  text default null
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_cod  text := p_codigo;
  v_tent integer;
begin
  if p_status is null or p_status not in ('enviado', 'falhou', 'ignorado') then
    raise exception 'Status de push da notificação inválido.' using errcode = 'AG042';
  end if;
  if p_status = 'falhou' then
    v_cod := coalesce(v_cod, 'desconhecido');
  elsif p_status = 'enviado' then
    v_cod := null;
  end if;
  if v_cod is not null and v_cod !~ '^[A-Za-z0-9_.-]{1,40}$' then
    raise exception 'Código de erro do push inválido.' using errcode = 'AG042';
  end if;

  perform 1 from public.notificacoes n where n.id = p_id for update;
  if not found then
    raise exception 'Notificação não encontrada.' using errcode = 'AG029';
  end if;

  update public.notificacoes n
     set push_status = p_status,
         push_erro_codigo = v_cod,
         push_enviado_em = case when p_status = 'enviado' then clock_timestamp() else null end
   where n.id = p_id
  returning n.push_tentativas into v_tent;

  return jsonb_build_object('id', p_id, 'push_status', p_status, 'push_tentativas', v_tent);
end;
$$;

-- ---------------------------------------------------------------------------
-- 5. RPCs de inscricao
-- ---------------------------------------------------------------------------
-- registrar_inscricao_push: cria ou REATRIBUI a inscricao do endpoint ao usuario (zera falhas e reativa).
-- Retorno: {"id", "reatribuida"} (reatribuida = o endpoint ja existia). Erros: AG042 parametros, AG044 perfil
-- inativo, AG043 limite de 10 ativas. Serializada por endpoint (advisory lock) e, no trigger, por usuario.
create or replace function public.registrar_inscricao_push(
  p_usuario  uuid,
  p_endpoint text,
  p_p256dh   text,
  p_auth     text
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_hash text;
  v_id   uuid;
begin
  if p_usuario is null
     or p_endpoint is null or p_endpoint !~ '^https://[^[:space:]]+$' or length(p_endpoint) > 2048
     or p_p256dh is null or p_p256dh !~ '^[A-Za-z0-9_-]{16,256}=*$'
     or p_auth is null or p_auth !~ '^[A-Za-z0-9_-]{8,128}=*$' then
    raise exception 'Dados da inscrição de push inválidos.' using errcode = 'AG042';
  end if;

  v_hash := encode(sha256(convert_to(p_endpoint, 'UTF8')), 'hex');
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.push_endpoint:' || v_hash, 0));

  select i.id into v_id from public.push_inscricoes i where i.endpoint_hash = v_hash for update;
  if found then
    update public.push_inscricoes i
       set usuario_id = p_usuario, p256dh = p_p256dh, auth = p_auth, falhas = 0, revogada_em = null
     where i.id = v_id;
    return jsonb_build_object('id', v_id, 'reatribuida', true);
  end if;

  insert into public.push_inscricoes (usuario_id, endpoint, endpoint_hash, p256dh, auth)
  values (p_usuario, p_endpoint, v_hash, p_p256dh, p_auth)
  returning id into v_id;
  return jsonb_build_object('id', v_id, 'reatribuida', false);
end;
$$;

-- revogar_inscricao_push: revoga SO a inscricao do proprio usuario (IDOR: endpoint de outro e como inexistente).
-- Idempotente. Retorno: {"revogadas": int}
create or replace function public.revogar_inscricao_push(p_usuario uuid, p_endpoint text)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_n integer;
begin
  if p_usuario is null or p_endpoint is null or length(p_endpoint) > 2048 then
    raise exception 'Dados da inscrição de push inválidos.' using errcode = 'AG042';
  end if;
  update public.push_inscricoes i
     set revogada_em = clock_timestamp()
   where i.usuario_id = p_usuario
     and i.endpoint_hash = encode(sha256(convert_to(p_endpoint, 'UTF8')), 'hex')
     and i.revogada_em is null;
  get diagnostics v_n = row_count;
  return jsonb_build_object('revogadas', v_n);
end;
$$;

-- listar_inscricoes_push: inscricoes ATIVAS de um perfil ATIVO, para o backend enviar.
-- Retorno: [{id, endpoint, p256dh, auth}] (jsonb array). Unico lugar que devolve endpoint/chaves.
create or replace function public.listar_inscricoes_push(p_usuario uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_res jsonb;
begin
  if p_usuario is null then
    raise exception 'Usuário da inscrição de push não informado.' using errcode = 'AG042';
  end if;
  select coalesce(jsonb_agg(jsonb_build_object('id', i.id, 'endpoint', i.endpoint, 'p256dh', i.p256dh,
                                               'auth', i.auth) order by i.criada_em, i.id), '[]'::jsonb)
    into v_res
    from public.push_inscricoes i
    join public.perfis p on p.id = i.usuario_id and p.ativo
   where i.usuario_id = p_usuario and i.revogada_em is null;
  return v_res;
end;
$$;

-- marcar_resultado_inscricao_push: resultado do envio a UMA inscricao.
--   p_ok            => ultimo_sucesso_em = agora, falhas = 0
--   nao ok, expirada (404/410 do servico push) => revoga na hora
--   nao ok          => falhas + 1; ao chegar a 5 falhas seguidas a inscricao e revogada (aparelho morto)
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
  else
    update public.push_inscricoes i
       set falhas = least(i.falhas + 1, 1000),
           revogada_em = case
                           when i.revogada_em is not null then i.revogada_em
                           when coalesce(p_expirada, false) or i.falhas + 1 >= 5 then clock_timestamp()
                         end
     where i.id = p_id
    returning i.falhas, i.revogada_em into v_falhas, v_revogada;
  end if;

  return jsonb_build_object('id', p_id, 'falhas', v_falhas, 'revogada', v_revogada is not null);
end;
$$;

-- purgar_inscricoes_push: remove revogadas ha mais de 30 dias e as com 5 falhas ou mais. Retorna o total removido.
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
  -- Variavel LOCAL: vale so nesta transacao e e desligada logo apos o DELETE.
  perform set_config('sisveiculos.expurgo_push', '1', true);
  delete from public.push_inscricoes
   where (revogada_em is not null and revogada_em < now() - interval '30 days') or falhas >= 5;
  get diagnostics v_n = row_count;
  perform set_config('sisveiculos.expurgo_push', '', true);
  return v_n;
end;
$$;

-- ---------------------------------------------------------------------------
-- 6. Permissoes: EXECUTE so service_role
-- ---------------------------------------------------------------------------
revoke all on function public.reservar_push_notificacao(integer) from public, anon, authenticated;
revoke all on function public.marcar_push_notificacao(uuid, text, text) from public, anon, authenticated;
revoke all on function public.registrar_inscricao_push(uuid, text, text, text) from public, anon, authenticated;
revoke all on function public.revogar_inscricao_push(uuid, text) from public, anon, authenticated;
revoke all on function public.listar_inscricoes_push(uuid) from public, anon, authenticated;
revoke all on function public.marcar_resultado_inscricao_push(uuid, boolean, boolean) from public, anon, authenticated;
revoke all on function public.purgar_inscricoes_push() from public, anon, authenticated;

grant execute on function public.reservar_push_notificacao(integer) to service_role;
grant execute on function public.marcar_push_notificacao(uuid, text, text) to service_role;
grant execute on function public.registrar_inscricao_push(uuid, text, text, text) to service_role;
grant execute on function public.revogar_inscricao_push(uuid, text) to service_role;
grant execute on function public.listar_inscricoes_push(uuid) to service_role;
grant execute on function public.marcar_resultado_inscricao_push(uuid, boolean, boolean) to service_role;
grant execute on function public.purgar_inscricoes_push() to service_role;

-- ---------------------------------------------------------------------------
-- 7. Agendamento CONDICIONAL do expurgo com pg_cron (nunca falha a migration)
-- ---------------------------------------------------------------------------
do $$
begin
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    begin
      perform cron.schedule('purgar-inscricoes-push', '41 3 * * *', 'select public.purgar_inscricoes_push()');
    exception when others then
      raise notice 'pg_cron presente, mas o agendamento do expurgo de push falhou; agende manualmente.';
    end;
  else
    raise notice 'pg_cron ausente: expurgo de inscricoes push nao agendado.';
  end if;
end
$$;

-- ---------------------------------------------------------------------------
-- Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v_oid   oid;
  v_def   text;
  v_chave text;
  v_nome  text;
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'configuracoes'
                    and column_name = 'notificacao_push' and data_type = 'boolean'
                    and is_nullable = 'NO' and column_default = 'false') then
    raise exception 'configuracoes.notificacao_push deve ser boolean not null default false.';
  end if;

  v_oid := to_regprocedure('public.configuracoes_validar()');
  v_def := pg_get_functiondef(v_oid);
  foreach v_chave in array array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa', 'relatorio_email', 'relatorio_whatsapp', 'notificacao_email_criticos', 'motorista', 'motorista_registra', 'motorista_localizacao', 'vigilante_km', 'autorizador_decide_propria', 'autorizador_km', 'notificacao_push']
  loop
    if position('''' || v_chave || '''' in v_def) = 0 then
      raise exception 'configuracoes_validar nao audita %.', v_chave;
    end if;
  end loop;
  if position('Desligue o KM do vigilante' in v_def) = 0
     or position('Desligue o KM do autorizador' in v_def) = 0 then
    raise exception 'configuracoes_validar sem as mensagens do caminho inverso (regressao).';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.configuracoes'::regclass
                  and tgname = 'trg_configuracoes_validar' and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'trg_configuracoes_validar ausente ou desabilitado.';
  end if;

  if not exists (select 1 from pg_class where oid = 'public.push_inscricoes'::regclass and relrowsecurity) then
    raise exception 'push_inscricoes sem RLS.';
  end if;
  if exists (select 1 from pg_policy where polrelid = 'public.push_inscricoes'::regclass) then
    raise exception 'push_inscricoes nao deve ter policy (deny-by-default).';
  end if;
  foreach v_nome in array array['anon', 'authenticated', 'service_role'] loop
    if has_table_privilege(v_nome, 'public.push_inscricoes', 'select')
       or has_table_privilege(v_nome, 'public.push_inscricoes', 'insert')
       or has_table_privilege(v_nome, 'public.push_inscricoes', 'update')
       or has_table_privilege(v_nome, 'public.push_inscricoes', 'delete') then
      raise exception 'push_inscricoes com grant indevido para %.', v_nome;
    end if;
  end loop;
  if not exists (select 1 from pg_indexes where schemaname = 'public' and tablename = 'push_inscricoes'
                  and indexname = 'ux_push_inscricoes_endpoint_hash' and indexdef like 'CREATE UNIQUE%') then
    raise exception 'ux_push_inscricoes_endpoint_hash ausente.';
  end if;
  if exists (select 1 from public.notificacoes where push_status is null) then
    raise exception 'notificacoes com push_status nulo.';
  end if;
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'notificacoes'
                    and column_name = 'push_status' and column_default like '''pendente''%') then
    raise exception 'notificacoes.push_status deve ter default pendente.';
  end if;

  foreach v_nome in array array[
    'public.reservar_push_notificacao(integer)', 'public.marcar_push_notificacao(uuid,text,text)',
    'public.registrar_inscricao_push(uuid,text,text,text)', 'public.revogar_inscricao_push(uuid,text)',
    'public.listar_inscricoes_push(uuid)', 'public.marcar_resultado_inscricao_push(uuid,boolean,boolean)',
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

  v_def := pg_get_functiondef('public.notificacoes_proteger()'::regprocedure);
  if position('push_status' in v_def) = 0 or position('AG003' in v_def) = 0 or position('email_status' in v_def) = 0 then
    raise exception 'notificacoes_proteger sem o bloco de push ou regressao do e-mail.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.notificacoes'::regclass
                  and tgname = 'trg_notificacoes_proteger' and tgenabled = 'A' and not tgisinternal) then
    raise exception 'trg_notificacoes_proteger ausente ou nao sempre ativo.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.push_inscricoes'::regclass
                  and tgname = 'trg_push_inscricoes_proteger' and tgenabled = 'A' and not tgisinternal) then
    raise exception 'trg_push_inscricoes_proteger ausente ou nao sempre ativo.';
  end if;
end
$$;
