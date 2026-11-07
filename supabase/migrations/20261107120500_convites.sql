-- Migration 6: convites de conta (sem autocadastro).
-- Motivo: so o admin cria contas, por convite de uso unico com prazo de validade.
-- O token em claro NUNCA e gravado: o backend gera um token aleatorio, envia por
-- e-mail e grava apenas o SHA-256 em hex minusculo (token_hash). Convite expirado,
-- usado ou revogado nao vale; nada e apagado (historico preservado).
-- E-mail aceito: Gmail (gmail.com) ou institucional (agu.gov.br e subdominios).
-- ASSUNCAO a confirmar: lista de dominios institucionais. Para mudar, recrie o
-- CHECK ck_convites_email_dominio em nova migration.
-- Prazo: padrao 7 dias, maximo 30 (ajustavel por migration).
-- Erros com SQLSTATE proprio (continuam a serie da migration 5):
--   AG005 convite ja usado ou revogado (encerrado)
--   AG006 convite expirado
--   AG007 convite inexistente ou e-mail da conta diferente do convidado
-- O responsavel vem das colunas da linha (criado_por, usado_por, revogado_por),
-- pois o Flask usa service_role e auth.uid() seria nulo (coerente com a migration 5).
-- Auditoria: criacao, uso e revogacao geram linha, sem e-mail nem hash (LGPD).
-- RLS ligado e deny-by-default; sem grants nem politicas para anon/authenticated
-- (somente o backend com service_role acessa convites).
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- Schema privado: funcoes internas fora da API (PostgREST expoe so public).
-- ---------------------------------------------------------------------------
create schema if not exists privado;
revoke all on schema privado from public, anon, authenticated;

-- Exige que o responsavel seja um admin ativo (reutilizada nas migrations 7 e 8).
-- Volatil de proposito: precisa enxergar dados recem-comitados (ver migration 7).
create or replace function privado.exigir_admin_ativo(p_ator uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role  public.role_usuario;
  v_ativo boolean;
begin
  if p_ator is null then
    raise exception 'Ação administrativa exige o responsável registrado.' using errcode = 'AG002';
  end if;
  select role, ativo into v_role, v_ativo from public.perfis where id = p_ator;
  if not found or not v_ativo or v_role <> 'admin' then
    raise exception 'Apenas administrador ativo pode executar esta ação.' using errcode = 'AG002';
  end if;
end;
$$;

revoke all on function privado.exigir_admin_ativo(uuid) from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- convites
-- ---------------------------------------------------------------------------
create table if not exists public.convites (
  id            uuid primary key default gen_random_uuid(),
  email         text not null,
  role          public.role_usuario not null default 'solicitante',
  unidade       public.unidade not null,
  token_hash    text not null,
  criado_por    uuid not null references public.perfis (id) on delete restrict,
  criado_em     timestamptz not null default now(),
  expira_em     timestamptz not null default (now() + interval '7 days'),
  usado_em      timestamptz,
  usado_por     uuid references public.perfis (id) on delete restrict,
  revogado_em   timestamptz,
  revogado_por  uuid references public.perfis (id) on delete restrict,

  constraint ck_convites_token_hash check (token_hash ~ '^[0-9a-f]{64}$'),
  constraint ck_convites_email_dominio
    check (char_length(email) <= 254
           and email ~ '^[a-z0-9._%+-]+@(gmail\.com|([a-z0-9-]+\.)*agu\.gov\.br)$'),
  constraint ck_convites_prazo
    check (expira_em > criado_em and expira_em <= criado_em + interval '30 days'),
  constraint ck_convites_uso_par check ((usado_em is null) = (usado_por is null)),
  constraint ck_convites_revogacao_par check ((revogado_em is null) = (revogado_por is null)),
  constraint ck_convites_um_desfecho check (usado_em is null or revogado_em is null)
);

comment on table public.convites is
  'Convites de conta. Guarda so o hash do token. Uso unico, com prazo. Sem DELETE fisico. Contem e-mail (dado pessoal): nao logar.';

-- Busca pelo token (unica por construcao).
create unique index if not exists ux_convites_token_hash on public.convites (token_hash);
-- FKs (checagem e consultas "convites criados por").
create index if not exists ix_convites_criado_por on public.convites (criado_por);
create index if not exists ix_convites_usado_por on public.convites (usado_por) where usado_por is not null;
create index if not exists ix_convites_revogado_por on public.convites (revogado_por) where revogado_por is not null;
-- Listagem de pendentes por e-mail (nao unico: ver nota na revisao da migration).
create index if not exists ix_convites_email on public.convites (email);

-- Sem DELETE/TRUNCATE fisico (funcao da migration 5).
drop trigger if exists trg_convites_sem_delete on public.convites;
create trigger trg_convites_sem_delete
  before delete on public.convites
  for each row execute function public.bloquear_alteracao();
drop trigger if exists trg_convites_sem_truncate on public.convites;
create trigger trg_convites_sem_truncate
  before truncate on public.convites
  for each statement execute function public.bloquear_alteracao();

-- ---------------------------------------------------------------------------
-- Validacao (BEFORE INSERT OR UPDATE): so admin ativo cria/revoga; uso unico e prazo.
-- ---------------------------------------------------------------------------
create or replace function public.convites_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    perform privado.exigir_admin_ativo(new.criado_por);
    if new.usado_em is not null or new.revogado_em is not null then
      raise exception 'Convite deve nascer aberto.' using errcode = 'AG001';
    end if;
    return new;
  end if;

  -- UPDATE: identidade e conteudo do convite sao imutaveis.
  if new.id is distinct from old.id
     or new.email is distinct from old.email
     or new.role is distinct from old.role
     or new.unidade is distinct from old.unidade
     or new.token_hash is distinct from old.token_hash
     or new.criado_por is distinct from old.criado_por
     or new.criado_em is distinct from old.criado_em
     or new.expira_em is distinct from old.expira_em then
    raise exception 'Dados do convite são imutáveis.' using errcode = 'AG004';
  end if;

  -- Convite encerrado (usado ou revogado): congelado.
  if old.usado_em is not null or old.revogado_em is not null then
    if new.usado_em is distinct from old.usado_em
       or new.usado_por is distinct from old.usado_por
       or new.revogado_em is distinct from old.revogado_em
       or new.revogado_por is distinct from old.revogado_por then
      raise exception 'Convite já usado ou revogado.' using errcode = 'AG005';
    end if;
    return new;
  end if;

  -- Uso: so antes de expirar. O carimbo e sempre do servidor.
  if new.usado_em is not null then
    if old.expira_em <= clock_timestamp() then
      raise exception 'Convite expirado.' using errcode = 'AG006';
    end if;
    new.usado_em := now();
  end if;

  -- Revogacao: so admin ativo.
  if new.revogado_em is not null then
    perform privado.exigir_admin_ativo(new.revogado_por);
    new.revogado_em := now();
  end if;

  return new;
end;
$$;

revoke all on function public.convites_validar() from public, anon, authenticated;

drop trigger if exists trg_convites_validar on public.convites;
create trigger trg_convites_validar
  before insert or update on public.convites
  for each row execute function public.convites_validar();

-- ---------------------------------------------------------------------------
-- Auditoria (AFTER): criado, usado, revogado. Sem e-mail nem hash.
-- ---------------------------------------------------------------------------
create or replace function public.convites_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    insert into public.auditoria (tabela, registro_id, acao, ator_id, detalhes)
    values ('convites', new.id, 'convite_criado', new.criado_por,
            jsonb_build_object('role', new.role, 'unidade', new.unidade, 'expira_em', new.expira_em));
  elsif old.usado_em is null and new.usado_em is not null then
    insert into public.auditoria (tabela, registro_id, acao, ator_id)
    values ('convites', new.id, 'convite_usado', new.usado_por);
  elsif old.revogado_em is null and new.revogado_em is not null then
    insert into public.auditoria (tabela, registro_id, acao, ator_id)
    values ('convites', new.id, 'convite_revogado', new.revogado_por);
  end if;
  return null;
end;
$$;

revoke all on function public.convites_auditar() from public, anon, authenticated;

drop trigger if exists trg_convites_auditoria on public.convites;
create trigger trg_convites_auditoria
  after insert or update on public.convites
  for each row execute function public.convites_auditar();

-- ---------------------------------------------------------------------------
-- Consumo atomico do convite: valida, cria o perfil e marca como usado na mesma
-- transacao. FOR UPDATE serializa tentativas simultaneas do mesmo token: a segunda
-- espera e encontra o convite ja usado (AG005). A conta no Supabase Auth ja deve
-- existir (criada pelo fluxo de convite/definicao de senha) com o MESMO e-mail.
-- Perfil ja existente para a conta => unique_violation (23505).
-- Chamavel apenas pelo backend (service_role).
-- ---------------------------------------------------------------------------
create or replace function public.consumir_convite(
  p_token_hash text,
  p_usuario_id uuid,
  p_nome       text
)
returns public.perfis
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_conv   public.convites;
  v_email  text;
  v_perfil public.perfis;
begin
  select * into v_conv from public.convites where token_hash = p_token_hash for update;
  if not found then
    raise exception 'Convite inválido.' using errcode = 'AG007';
  end if;
  if v_conv.usado_em is not null or v_conv.revogado_em is not null then
    raise exception 'Convite já usado ou revogado.' using errcode = 'AG005';
  end if;
  if v_conv.expira_em <= clock_timestamp() then
    raise exception 'Convite expirado.' using errcode = 'AG006';
  end if;

  select lower(email) into v_email from auth.users where id = p_usuario_id;
  if v_email is distinct from v_conv.email then
    raise exception 'Convite inválido.' using errcode = 'AG007';
  end if;

  insert into public.perfis (id, nome, unidade, role)
  values (p_usuario_id, p_nome, v_conv.unidade, v_conv.role)
  returning * into v_perfil;

  update public.convites set usado_em = now(), usado_por = p_usuario_id where id = v_conv.id;

  return v_perfil;
end;
$$;

revoke all on function public.consumir_convite(text, uuid, text) from public, anon, authenticated;
grant execute on function public.consumir_convite(text, uuid, text) to service_role;

-- ---------------------------------------------------------------------------
-- RLS deny-by-default: habilitado, sem politicas e sem grants aos papeis da API.
-- ---------------------------------------------------------------------------
alter table public.convites enable row level security;

revoke all on table public.convites from anon, authenticated;
