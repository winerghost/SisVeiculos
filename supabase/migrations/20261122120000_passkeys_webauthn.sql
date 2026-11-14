-- Migration: Passkeys (WebAuthn), 1a etapa: credenciais, desafios, reautenticacoes, RPCs e auditoria.
-- Nova migration; NAO edita as anteriores. Idempotente. Depende de 20261107120400 (auditoria append-only,
-- bloquear_alteracao), 20261115120400 (lista vigente de 24 valores de ck_auditoria_acao) e 20261121120000
-- (flag configuracoes.passkeys). Contrato: docs/plano-recursos-web-nativos.md, Fase 5.
--
-- Motivo: a Passkey e validada no Flask (assinatura, origem, RP ID) e serve como REAUTENTICACAO para cadastrar
-- e revogar passkeys; o Supabase Auth segue como unico emissor de JWT. O banco guarda so o necessario, com
-- uso unico do desafio, contador monotonico, limite de 10 credenciais ativas e trilha de auditoria sem
-- material criptografico.
--
-- Tabelas (RLS ligado, SEM policy => deny-by-default; REVOKE total de public/anon/authenticated/service_role:
-- o backend so usa as RPCs SECURITY DEFINER, EXECUTE so service_role):
--   credenciais_webauthn      credential_id e chave_publica (base64url) = dado sensivel; sem DELETE fisico
--                             (revogacao = revogada_em); identidade/chave imutaveis; contador nunca regride.
--   desafios_webauthn         desafio de 32 bytes aleatorios do banco, validade 5 min, USO UNICO.
--   reautenticacoes_webauthn  janela de 5 min por (usuario, session_id do JWT).
-- DELETE fisico so em purgar_webauthn (desafios e reautenticacoes vencidos ha > 1 dia). TRUNCATE nunca.
--
-- Auditoria: ck_auditoria_acao ganha 6 valores (24 vigentes preservados -> 30): passkey_cadastrada,
-- passkey_revogada, passkey_autenticada, passkey_falha, passkey_regressao_contador, passkey_desafio_invalido.
-- ck_auditoria_tabela NAO muda: tabela='perfis', registro_id = usuario, ator_id = usuario. detalhes so com
-- {"recurso":"passkey", "credencial": <id interno da linha>} ou {"recurso":"passkey","codigo":<curto>}; NUNCA
-- credential_id, chave, desafio, origem nem contador.
--
-- Codigos de erro: AG050 parametro invalido, AG051 perfil inexistente/inativo, AG052 limite de 10 credenciais
-- ativas, AG053 limite de desafios por hora, AG054 alteracao proibida (identidade/regressao), AG055 credencial
-- ja cadastrada. AG003 = DELETE/TRUNCATE proibido.
-- Binarios: sempre texto base64url (sem padding). Fuso: timestamptz (UTC; exibicao em America/Sao_Paulo).
-- NAO aplicar pelo servidor: supabase db push e do pipeline.

-- ---------------------------------------------------------------------------
-- 1. Auditoria: novas acoes (preserva as 24 existentes)
-- ---------------------------------------------------------------------------
alter table public.auditoria drop constraint if exists ck_auditoria_acao;
alter table public.auditoria
  add constraint ck_auditoria_acao check (acao in (
    'criada', 'transicao',
    'convite_criado', 'convite_usado', 'convite_revogado',
    'perfil_criado', 'perfil_alterado', 'perfil_unidade_alterada', 'admin_semeado',
    'configuracao_alterada',
    'veiculo_criado', 'veiculo_alterado',
    'acao_administrativa', 'acesso_negado',
    'checklist_registrado',
    'foto_perfil_alterada', 'foto_perfil_removida',
    'relatorio_gerado', 'relatorio_enviado',
    'motorista_designado',
    'localizacao_registrada',
    'localizacao_lida',
    'localizacao_expurgada',
    'saudacao_excluida',
    'passkey_cadastrada', 'passkey_revogada', 'passkey_autenticada',
    'passkey_falha', 'passkey_regressao_contador', 'passkey_desafio_invalido'
  ));

-- ---------------------------------------------------------------------------
-- 2. credenciais_webauthn
-- ---------------------------------------------------------------------------
create table if not exists public.credenciais_webauthn (
  id            uuid primary key default gen_random_uuid(),
  usuario_id    uuid not null references public.perfis (id) on delete restrict,
  credential_id text not null,
  chave_publica text not null,
  contador      bigint not null default 0,
  transportes   text[],
  apelido       text,
  criada_em     timestamptz not null default now(),
  ultimo_uso_em timestamptz,
  revogada_em   timestamptz,

  constraint ck_credenciais_webauthn_credential_id check (credential_id ~ '^[A-Za-z0-9_-]{16,1024}$'),
  constraint ck_credenciais_webauthn_chave check (chave_publica ~ '^[A-Za-z0-9_-]{16,4096}$'),
  constraint ck_credenciais_webauthn_contador check (contador >= 0),
  constraint ck_credenciais_webauthn_transportes
    check (transportes is null
           or (cardinality(transportes) <= 5
               and transportes <@ array['usb', 'nfc', 'ble', 'internal', 'hybrid']::text[])),
  constraint ck_credenciais_webauthn_apelido
    check (apelido is null or (char_length(apelido) between 1 and 40 and apelido = btrim(apelido)))
);

comment on table public.credenciais_webauthn is
  'Passkeys (WebAuthn). credential_id e chave_publica (base64url) sao dado sensivel: so a RPC os devolve ao backend. Sem DELETE; revogacao = revogada_em. Max. 10 ativas por usuario.';

create unique index if not exists ux_credenciais_webauthn_credential_id
  on public.credenciais_webauthn (credential_id);
-- Ativas por usuario (limite e listagem). FK sem indice automatico.
create index if not exists ix_credenciais_webauthn_usuario_ativas
  on public.credenciais_webauthn (usuario_id) where revogada_em is null;

alter table public.credenciais_webauthn enable row level security;
-- Sem policy de proposito: RLS ligado + nenhum grant = negado a anon/authenticated.
revoke all on table public.credenciais_webauthn from public, anon, authenticated, service_role;

create or replace function public.credenciais_webauthn_proteger()
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
    raise exception 'Operação DELETE proibida em credenciais_webauthn: use a revogação.'
      using errcode = 'AG003';
  end if;

  if tg_op = 'UPDATE' then
    if new.id is distinct from old.id
       or new.usuario_id is distinct from old.usuario_id
       or new.credential_id is distinct from old.credential_id
       or new.chave_publica is distinct from old.chave_publica
       or new.transportes is distinct from old.transportes
       or new.criada_em is distinct from old.criada_em then
      raise exception 'Identidade e chave da passkey são imutáveis.' using errcode = 'AG054';
    end if;
    if new.contador < old.contador then
      raise exception 'O contador da passkey não pode regredir.' using errcode = 'AG054';
    end if;
    if old.revogada_em is not null and new.revogada_em is distinct from old.revogada_em then
      raise exception 'Passkey revogada não pode ser reativada.' using errcode = 'AG054';
    end if;
    return new;
  end if;

  -- INSERT: so ativa, de perfil ativo, respeitando o limite (serializado por usuario).
  if new.revogada_em is not null then
    raise exception 'Passkey nova deve nascer ativa.' using errcode = 'AG054';
  end if;
  select p.ativo into v_ativa from public.perfis p where p.id = new.usuario_id;
  if not coalesce(v_ativa, false) then
    raise exception 'Somente usuário ativo cadastra passkey.' using errcode = 'AG051';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.credenciais_webauthn:' || new.usuario_id::text, 0));
  select count(*) into v_n from public.credenciais_webauthn x
   where x.usuario_id = new.usuario_id and x.revogada_em is null;
  if v_n >= 10 then
    raise exception 'Limite de 10 passkeys ativas por usuário.' using errcode = 'AG052';
  end if;
  return new;
end;
$$;

revoke all on function public.credenciais_webauthn_proteger() from public, anon, authenticated, service_role;

drop trigger if exists trg_credenciais_webauthn_proteger on public.credenciais_webauthn;
create trigger trg_credenciais_webauthn_proteger
  before insert or update or delete on public.credenciais_webauthn
  for each row execute function public.credenciais_webauthn_proteger();
alter table public.credenciais_webauthn enable always trigger trg_credenciais_webauthn_proteger;

drop trigger if exists trg_credenciais_webauthn_sem_truncate on public.credenciais_webauthn;
create trigger trg_credenciais_webauthn_sem_truncate
  before truncate on public.credenciais_webauthn
  for each statement execute function public.bloquear_alteracao();
alter table public.credenciais_webauthn enable always trigger trg_credenciais_webauthn_sem_truncate;

-- ---------------------------------------------------------------------------
-- 3. desafios_webauthn
-- ---------------------------------------------------------------------------
create table if not exists public.desafios_webauthn (
  id         uuid primary key default gen_random_uuid(),
  usuario_id uuid not null references public.perfis (id) on delete restrict,
  tipo       text not null,
  desafio    text not null,
  criado_em  timestamptz not null default now(),
  expira_em  timestamptz not null default now() + interval '5 minutes',
  usado_em   timestamptz,

  constraint ck_desafios_webauthn_tipo check (tipo in ('registro', 'autenticacao')),
  constraint ck_desafios_webauthn_desafio check (desafio ~ '^[A-Za-z0-9_-]{43}$')
);

comment on table public.desafios_webauthn is
  'Desafios WebAuthn (32 bytes base64url gerados no banco), validade 5 min, uso unico. Acesso so por RPC service_role.';

create index if not exists ix_desafios_webauthn_usuario on public.desafios_webauthn (usuario_id);
create index if not exists ix_desafios_webauthn_expira_em on public.desafios_webauthn (expira_em);

alter table public.desafios_webauthn enable row level security;
revoke all on table public.desafios_webauthn from public, anon, authenticated, service_role;

-- Imutavel, exceto usado_em (null -> valor, uma vez). DELETE so no expurgo (vencido ha > 1 dia).
create or replace function public.desafios_webauthn_proteger()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'DELETE' then
    if coalesce(current_setting('sisveiculos.purgar_webauthn', true), '') = '1'
       and old.expira_em < now() - interval '1 day' then
      return old;
    end if;
    raise exception 'Operação DELETE proibida em desafios_webauthn: só o expurgo remove.'
      using errcode = 'AG003';
  end if;

  if new.id is distinct from old.id
     or new.usuario_id is distinct from old.usuario_id
     or new.tipo is distinct from old.tipo
     or new.desafio is distinct from old.desafio
     or new.criado_em is distinct from old.criado_em
     or new.expira_em is distinct from old.expira_em
     or (old.usado_em is not null and new.usado_em is distinct from old.usado_em)
     or new.usado_em is null then
    raise exception 'Desafio WebAuthn é imutável (só o uso único pode ser marcado).' using errcode = 'AG054';
  end if;
  return new;
end;
$$;

revoke all on function public.desafios_webauthn_proteger() from public, anon, authenticated, service_role;

drop trigger if exists trg_desafios_webauthn_proteger on public.desafios_webauthn;
create trigger trg_desafios_webauthn_proteger
  before update or delete on public.desafios_webauthn
  for each row execute function public.desafios_webauthn_proteger();
alter table public.desafios_webauthn enable always trigger trg_desafios_webauthn_proteger;

drop trigger if exists trg_desafios_webauthn_sem_truncate on public.desafios_webauthn;
create trigger trg_desafios_webauthn_sem_truncate
  before truncate on public.desafios_webauthn
  for each statement execute function public.bloquear_alteracao();
alter table public.desafios_webauthn enable always trigger trg_desafios_webauthn_sem_truncate;

-- ---------------------------------------------------------------------------
-- 4. reautenticacoes_webauthn
-- ---------------------------------------------------------------------------
create table if not exists public.reautenticacoes_webauthn (
  id         uuid primary key default gen_random_uuid(),
  usuario_id uuid not null references public.perfis (id) on delete restrict,
  sessao     text not null,
  criada_em  timestamptz not null default now(),
  valido_ate timestamptz not null default now() + interval '5 minutes',

  constraint ck_reautenticacoes_webauthn_sessao check (char_length(sessao) between 1 and 128)
);

comment on table public.reautenticacoes_webauthn is
  'Reautenticacao por passkey: vale 5 min para (usuario, session_id do JWT). Acesso so por RPC service_role.';

create index if not exists ix_reautenticacoes_webauthn_validade
  on public.reautenticacoes_webauthn (usuario_id, sessao, valido_ate);
-- Expurgo e FK.
create index if not exists ix_reautenticacoes_webauthn_valido_ate
  on public.reautenticacoes_webauthn (valido_ate);

alter table public.reautenticacoes_webauthn enable row level security;
revoke all on table public.reautenticacoes_webauthn from public, anon, authenticated, service_role;

create or replace function public.reautenticacoes_webauthn_proteger()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'DELETE'
     and coalesce(current_setting('sisveiculos.purgar_webauthn', true), '') = '1'
     and old.valido_ate < now() - interval '1 day' then
    return old;
  end if;
  if tg_op = 'UPDATE' then
    raise exception 'Operação UPDATE proibida em reautenticacoes_webauthn.' using errcode = 'AG003';
  end if;
  raise exception 'Operação % proibida em reautenticacoes_webauthn: só o expurgo remove.', tg_op
    using errcode = 'AG003';
end;
$$;

revoke all on function public.reautenticacoes_webauthn_proteger() from public, anon, authenticated, service_role;

drop trigger if exists trg_reautenticacoes_webauthn_proteger on public.reautenticacoes_webauthn;
create trigger trg_reautenticacoes_webauthn_proteger
  before update or delete on public.reautenticacoes_webauthn
  for each row execute function public.reautenticacoes_webauthn_proteger();
alter table public.reautenticacoes_webauthn enable always trigger trg_reautenticacoes_webauthn_proteger;

drop trigger if exists trg_reautenticacoes_webauthn_sem_truncate on public.reautenticacoes_webauthn;
create trigger trg_reautenticacoes_webauthn_sem_truncate
  before truncate on public.reautenticacoes_webauthn
  for each statement execute function public.bloquear_alteracao();
alter table public.reautenticacoes_webauthn enable always trigger trg_reautenticacoes_webauthn_sem_truncate;

-- ---------------------------------------------------------------------------
-- 5. RPCs (SECURITY DEFINER, search_path vazio, EXECUTE so service_role)
-- ---------------------------------------------------------------------------
-- criar_desafio_webauthn: perfil ativo; invalida (marca como usados) os pendentes do mesmo usuario/tipo;
-- no maximo 30 desafios por usuario por hora (AG053). Desafio = 32 bytes do CSPRNG do Postgres
-- (2 x gen_random_uuid(), 244 bits aleatorios; core, sem extensao) em base64url sem padding (43 chars).
create or replace function public.criar_desafio_webauthn(p_usuario uuid, p_tipo text)
returns table (id uuid, desafio text)
language plpgsql
volatile
security definer
set search_path = ''
as $$
#variable_conflict use_column
declare
  v_ativa boolean;
  v_n     integer;
  v_id    uuid;
  v_des   text;
begin
  if p_usuario is null or p_tipo is null or p_tipo not in ('registro', 'autenticacao') then
    raise exception 'Dados do desafio inválidos.' using errcode = 'AG050';
  end if;
  select p.ativo into v_ativa from public.perfis p where p.id = p_usuario;
  if not coalesce(v_ativa, false) then
    raise exception 'Perfil inexistente ou inativo.' using errcode = 'AG051';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.desafios_webauthn:' || p_usuario::text, 0));
  select count(*) into v_n from public.desafios_webauthn d
   where d.usuario_id = p_usuario and d.criado_em > now() - interval '1 hour';
  if v_n >= 30 then
    raise exception 'Muitos desafios solicitados; tente mais tarde.' using errcode = 'AG053';
  end if;

  update public.desafios_webauthn d
     set usado_em = clock_timestamp()
   where d.usuario_id = p_usuario and d.tipo = p_tipo and d.usado_em is null;

  v_des := translate(
    encode(decode(replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''), 'hex'), 'base64'),
    '+/=', '-_');
  insert into public.desafios_webauthn (usuario_id, tipo, desafio)
  values (p_usuario, p_tipo, v_des)
  returning id into v_id;

  return query select v_id, v_des;
end;
$$;

-- consumir_desafio_webauthn: ATOMICO e de USO UNICO. null = inexistente, expirado, ja usado, de outro usuario
-- ou de outro tipo (mesma resposta para todos os casos).
create or replace function public.consumir_desafio_webauthn(p_id uuid, p_usuario uuid, p_tipo text)
returns text
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_des text;
begin
  if p_id is null or p_usuario is null or p_tipo is null then
    return null;
  end if;
  update public.desafios_webauthn d
     set usado_em = clock_timestamp()
   where d.id = p_id and d.usuario_id = p_usuario and d.tipo = p_tipo
     and d.usado_em is null and d.expira_em > clock_timestamp()
  returning d.desafio into v_des;
  return v_des;
end;
$$;

-- registrar_credencial_webauthn: perfil ativo (AG051), limite de 10 (AG052, no gatilho), duplicada (AG055).
create or replace function public.registrar_credencial_webauthn(
  p_usuario       uuid,
  p_credential_id text,
  p_chave_publica text,
  p_contador      bigint,
  p_transportes   text[],
  p_apelido       text
)
returns uuid
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_id uuid;
begin
  if p_usuario is null or p_credential_id is null or p_chave_publica is null or p_contador is null
     or p_contador < 0
     or p_credential_id !~ '^[A-Za-z0-9_-]{16,1024}$' or p_chave_publica !~ '^[A-Za-z0-9_-]{16,4096}$'
     or (p_transportes is not null
         and (cardinality(p_transportes) > 5
              or not (p_transportes <@ array['usb', 'nfc', 'ble', 'internal', 'hybrid']::text[])))
     or (p_apelido is not null and (char_length(p_apelido) not between 1 and 40 or p_apelido <> btrim(p_apelido)))
  then
    raise exception 'Dados da passkey inválidos.' using errcode = 'AG050';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.webauthn_credential:' || p_credential_id, 0));
  if exists (select 1 from public.credenciais_webauthn c where c.credential_id = p_credential_id) then
    raise exception 'Passkey já cadastrada.' using errcode = 'AG055';
  end if;

  insert into public.credenciais_webauthn (usuario_id, credential_id, chave_publica, contador, transportes, apelido)
  values (p_usuario, p_credential_id, p_chave_publica, p_contador, p_transportes, p_apelido)
  returning id into v_id;

  insert into public.auditoria (tabela, registro_id, acao, ator_id, detalhes)
  values ('perfis', p_usuario, 'passkey_cadastrada', p_usuario,
          jsonb_build_object('recurso', 'passkey', 'credencial', v_id));
  return v_id;
end;
$$;

-- credenciais_ativas_webauthn: so ativas do usuario (perfil ativo). NUNCA devolve a chave publica.
create or replace function public.credenciais_ativas_webauthn(p_usuario uuid)
returns table (
  id            uuid,
  credential_id text,
  transportes   text[],
  apelido       text,
  criada_em     timestamptz,
  ultimo_uso_em timestamptz
)
language plpgsql
stable
security definer
set search_path = ''
as $$
#variable_conflict use_column
begin
  return query
    select c.id, c.credential_id, c.transportes, c.apelido, c.criada_em, c.ultimo_uso_em
      from public.credenciais_webauthn c
      join public.perfis p on p.id = c.usuario_id and p.ativo
     where c.usuario_id = p_usuario and c.revogada_em is null
     order by c.criada_em, c.id;
end;
$$;

-- buscar_credencial_webauthn: so ativa, do usuario (perfil ativo). Unico lugar que devolve a chave publica.
create or replace function public.buscar_credencial_webauthn(p_usuario uuid, p_credential_id text)
returns table (id uuid, chave_publica text, contador bigint)
language plpgsql
stable
security definer
set search_path = ''
as $$
#variable_conflict use_column
begin
  return query
    select c.id, c.chave_publica, c.contador
      from public.credenciais_webauthn c
      join public.perfis p on p.id = c.usuario_id and p.ativo
     where c.usuario_id = p_usuario and c.credential_id = p_credential_id and c.revogada_em is null;
end;
$$;

-- atualizar_contador_webauthn: aceita so p_novo > contador (ou ambos 0: autenticador sem contador). A
-- comparacao e o UPDATE sao um unico comando (atomico). false = regressao (possivel clone) ou credencial
-- inexistente/revogada; nada e gravado.
create or replace function public.atualizar_contador_webauthn(p_id uuid, p_novo bigint)
returns boolean
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_id uuid;
begin
  if p_id is null or p_novo is null or p_novo < 0 then
    return false;
  end if;
  update public.credenciais_webauthn c
     set contador = p_novo, ultimo_uso_em = clock_timestamp()
   where c.id = p_id and c.revogada_em is null
     and (p_novo > c.contador or (p_novo = 0 and c.contador = 0))
  returning c.id into v_id;
  return v_id is not null;
end;
$$;

-- revogar_credencial_webauthn: so do proprio usuario (IDOR: de outro = inexistente => false). Idempotente.
create or replace function public.revogar_credencial_webauthn(p_usuario uuid, p_id uuid)
returns boolean
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_id uuid;
begin
  if p_usuario is null or p_id is null then
    return false;
  end if;
  update public.credenciais_webauthn c
     set revogada_em = clock_timestamp()
   where c.id = p_id and c.usuario_id = p_usuario and c.revogada_em is null
  returning c.id into v_id;
  if v_id is null then
    return false;
  end if;
  insert into public.auditoria (tabela, registro_id, acao, ator_id, detalhes)
  values ('perfis', p_usuario, 'passkey_revogada', p_usuario,
          jsonb_build_object('recurso', 'passkey', 'credencial', v_id));
  return true;
end;
$$;

-- registrar_reautenticacao_webauthn: grava e devolve valido_ate (5 min). Perfil ativo (AG051).
create or replace function public.registrar_reautenticacao_webauthn(p_usuario uuid, p_sessao text)
returns timestamptz
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_ativa boolean;
  v_ate   timestamptz;
begin
  if p_usuario is null or p_sessao is null or char_length(p_sessao) not between 1 and 128 then
    raise exception 'Dados da reautenticação inválidos.' using errcode = 'AG050';
  end if;
  select p.ativo into v_ativa from public.perfis p where p.id = p_usuario;
  if not coalesce(v_ativa, false) then
    raise exception 'Perfil inexistente ou inativo.' using errcode = 'AG051';
  end if;
  insert into public.reautenticacoes_webauthn (usuario_id, sessao)
  values (p_usuario, p_sessao)
  returning valido_ate into v_ate;
  insert into public.auditoria (tabela, registro_id, acao, ator_id, detalhes)
  values ('perfis', p_usuario, 'passkey_autenticada', p_usuario, jsonb_build_object('recurso', 'passkey'));
  return v_ate;
end;
$$;

create or replace function public.reautenticacao_valida_webauthn(p_usuario uuid, p_sessao text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
      from public.reautenticacoes_webauthn r
      join public.perfis p on p.id = r.usuario_id and p.ativo
     where r.usuario_id = p_usuario and r.sessao = p_sessao and r.valido_ate > clock_timestamp()
  );
$$;

-- registrar_evento_webauthn: so eventos da allowlist; codigo curto [a-z_]{1,40}. Sem material criptografico.
create or replace function public.registrar_evento_webauthn(p_usuario uuid, p_evento text, p_codigo text)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
begin
  if p_usuario is null
     or p_evento is null
     or p_evento not in ('passkey_falha', 'passkey_regressao_contador', 'passkey_desafio_invalido')
     or p_codigo is null or p_codigo !~ '^[a-z_]{1,40}$' then
    raise exception 'Evento de passkey inválido.' using errcode = 'AG050';
  end if;
  if not exists (select 1 from public.perfis p where p.id = p_usuario) then
    raise exception 'Perfil inexistente ou inativo.' using errcode = 'AG051';
  end if;
  insert into public.auditoria (tabela, registro_id, acao, ator_id, detalhes)
  values ('perfis', p_usuario, p_evento, p_usuario,
          jsonb_build_object('recurso', 'passkey', 'codigo', p_codigo));
end;
$$;

-- purgar_webauthn: remove desafios e reautenticacoes vencidos ha > 1 dia. Retorna o total removido.
create or replace function public.purgar_webauthn()
returns integer
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_a integer;
  v_b integer;
begin
  perform set_config('sisveiculos.purgar_webauthn', '1', true);
  delete from public.desafios_webauthn d where d.expira_em < now() - interval '1 day';
  get diagnostics v_a = row_count;
  delete from public.reautenticacoes_webauthn r where r.valido_ate < now() - interval '1 day';
  get diagnostics v_b = row_count;
  perform set_config('sisveiculos.purgar_webauthn', '', true);
  return v_a + v_b;
end;
$$;

-- ---------------------------------------------------------------------------
-- 6. Privilegios: EXECUTE so service_role
-- ---------------------------------------------------------------------------
revoke all on function public.criar_desafio_webauthn(uuid, text) from public, anon, authenticated;
revoke all on function public.consumir_desafio_webauthn(uuid, uuid, text) from public, anon, authenticated;
revoke all on function public.registrar_credencial_webauthn(uuid, text, text, bigint, text[], text) from public, anon, authenticated;
revoke all on function public.credenciais_ativas_webauthn(uuid) from public, anon, authenticated;
revoke all on function public.buscar_credencial_webauthn(uuid, text) from public, anon, authenticated;
revoke all on function public.atualizar_contador_webauthn(uuid, bigint) from public, anon, authenticated;
revoke all on function public.revogar_credencial_webauthn(uuid, uuid) from public, anon, authenticated;
revoke all on function public.registrar_reautenticacao_webauthn(uuid, text) from public, anon, authenticated;
revoke all on function public.reautenticacao_valida_webauthn(uuid, text) from public, anon, authenticated;
revoke all on function public.registrar_evento_webauthn(uuid, text, text) from public, anon, authenticated;
revoke all on function public.purgar_webauthn() from public, anon, authenticated;

grant execute on function public.criar_desafio_webauthn(uuid, text) to service_role;
grant execute on function public.consumir_desafio_webauthn(uuid, uuid, text) to service_role;
grant execute on function public.registrar_credencial_webauthn(uuid, text, text, bigint, text[], text) to service_role;
grant execute on function public.credenciais_ativas_webauthn(uuid) to service_role;
grant execute on function public.buscar_credencial_webauthn(uuid, text) to service_role;
grant execute on function public.atualizar_contador_webauthn(uuid, bigint) to service_role;
grant execute on function public.revogar_credencial_webauthn(uuid, uuid) to service_role;
grant execute on function public.registrar_reautenticacao_webauthn(uuid, text) to service_role;
grant execute on function public.reautenticacao_valida_webauthn(uuid, text) to service_role;
grant execute on function public.registrar_evento_webauthn(uuid, text, text) to service_role;
grant execute on function public.purgar_webauthn() to service_role;

-- ---------------------------------------------------------------------------
-- 7. Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  t text;
  v text;
  d text;
begin
  select pg_get_constraintdef(oid) into d from pg_constraint
   where conrelid = 'public.auditoria'::regclass and conname = 'ck_auditoria_acao';
  foreach v in array array['saudacao_excluida', 'passkey_cadastrada', 'passkey_revogada', 'passkey_autenticada',
                           'passkey_falha', 'passkey_regressao_contador', 'passkey_desafio_invalido'] loop
    if d is null or position('''' || v || '''' in d) = 0 then
      raise exception 'ck_auditoria_acao sem o valor %.', v;
    end if;
  end loop;

  foreach t in array array['credenciais_webauthn', 'desafios_webauthn', 'reautenticacoes_webauthn'] loop
    if not (select relrowsecurity from pg_class where oid = ('public.' || t)::regclass) then
      raise exception 'RLS desligado em public.%.', t;
    end if;
    if exists (select 1 from pg_policies where schemaname = 'public' and tablename = t) then
      raise exception 'public.% nao deve ter policy (deny-by-default).', t;
    end if;
    if has_table_privilege('anon', 'public.' || t, 'select')
       or has_table_privilege('authenticated', 'public.' || t, 'select')
       or has_table_privilege('service_role', 'public.' || t, 'select') then
      raise exception 'Privilegios indevidos em public.%.', t;
    end if;
  end loop;

  foreach t in array array[
    'public.criar_desafio_webauthn(uuid,text)', 'public.consumir_desafio_webauthn(uuid,uuid,text)',
    'public.registrar_credencial_webauthn(uuid,text,text,bigint,text[],text)',
    'public.credenciais_ativas_webauthn(uuid)', 'public.buscar_credencial_webauthn(uuid,text)',
    'public.atualizar_contador_webauthn(uuid,bigint)', 'public.revogar_credencial_webauthn(uuid,uuid)',
    'public.registrar_reautenticacao_webauthn(uuid,text)', 'public.reautenticacao_valida_webauthn(uuid,text)',
    'public.registrar_evento_webauthn(uuid,text,text)', 'public.purgar_webauthn()'] loop
    if has_function_privilege('anon', t, 'execute') or has_function_privilege('authenticated', t, 'execute')
       or not has_function_privilege('service_role', t, 'execute') then
      raise exception 'EXECUTE incorreto em %.', t;
    end if;
  end loop;
end
$$;
