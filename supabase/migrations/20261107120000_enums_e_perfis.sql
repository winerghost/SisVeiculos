-- Migration 1: enums e tabela perfis.
-- Motivo: o papel (role) do usuario vem do banco, nunca do cliente. Esta migration
-- cria os tipos de dominio e a tabela de perfis, 1:1 com auth.users.
-- Colunas de saida/chegada reais ficam em requisicoes (migration propria).
-- RLS ligado e deny-by-default: sem politicas aqui; as politicas por papel
-- vem em migration propria. O Flask usa service_role (que ignora RLS).
-- Fuso: todos os timestamps sao timestamptz (armazenados em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- Enums (idempotentes: CREATE TYPE nao tem IF NOT EXISTS)
-- ---------------------------------------------------------------------------
do $$
begin
  if not exists (select 1 from pg_type t join pg_namespace n on n.oid = t.typnamespace
                 where n.nspname = 'public' and t.typname = 'role_usuario') then
    create type public.role_usuario as enum ('solicitante', 'autorizador', 'admin');
  end if;

  if not exists (select 1 from pg_type t join pg_namespace n on n.oid = t.typnamespace
                 where n.nspname = 'public' and t.typname = 'unidade') then
    create type public.unidade as enum ('PU', 'PF', 'CJU', 'UEA');
  end if;

  if not exists (select 1 from pg_type t join pg_namespace n on n.oid = t.typnamespace
                 where n.nspname = 'public' and t.typname = 'status_requisicao') then
    create type public.status_requisicao as enum
      ('solicitada', 'autorizada', 'em_uso', 'concluida', 'negada', 'cancelada');
  end if;
end
$$;

-- ---------------------------------------------------------------------------
-- perfis: um por conta do Supabase Auth. Nunca apagar (ON DELETE RESTRICT);
-- conta desativada usa ativo = false e mantem o historico.
-- ---------------------------------------------------------------------------
create table if not exists public.perfis (
  id            uuid primary key references auth.users (id) on delete restrict,
  nome          text not null check (char_length(btrim(nome)) between 1 and 120),
  unidade       public.unidade not null,
  role          public.role_usuario not null default 'solicitante',
  ativo         boolean not null default true,
  criado_em     timestamptz not null default now(),
  atualizado_em timestamptz not null default now()
);

comment on table public.perfis is
  'Perfil 1:1 com auth.users. Fonte da verdade do papel (role). Sem DELETE fisico.';

-- Mantem atualizado_em coerente (search_path fixo por seguranca).
create or replace function public.set_atualizado_em()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.atualizado_em := now();
  return new;
end;
$$;

drop trigger if exists trg_perfis_atualizado_em on public.perfis;
create trigger trg_perfis_atualizado_em
  before update on public.perfis
  for each row execute function public.set_atualizado_em();

-- ---------------------------------------------------------------------------
-- RLS deny-by-default: habilitado, sem politicas e sem grants aos papeis da API.
-- ---------------------------------------------------------------------------
alter table public.perfis enable row level security;

revoke all on table public.perfis from anon, authenticated;
revoke all on function public.set_atualizado_em() from public, anon, authenticated;
