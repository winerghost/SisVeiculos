-- Migration 2: tabela veiculos.
-- Motivo: requisicoes referenciam veiculo_id. Hoje ha um so veiculo, mas a tabela
-- existe para o admin cadastrar mais no futuro. Veiculo nunca e apagado;
-- usa ativo = false para tirar de uso e manter o historico.
-- RLS ligado e deny-by-default: sem politicas; o Flask usa service_role (ignora RLS).
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).
-- Reutiliza public.set_atualizado_em() da migration 1.

-- ---------------------------------------------------------------------------
-- veiculos
-- placa: guardada normalizada (maiusculas, sem hifen/espaco). Aceita o padrao
-- antigo (ABC1234) e o Mercosul (ABC1D23). A API normaliza antes de gravar;
-- o CHECK garante o formato mesmo se algo escapar.
-- ---------------------------------------------------------------------------
create table if not exists public.veiculos (
  id            uuid primary key default gen_random_uuid(),
  descricao     text not null check (char_length(btrim(descricao)) between 1 and 120),
  placa         text check (placa ~ '^[A-Z]{3}[0-9][A-Z0-9][0-9]{2}$'),
  ativo         boolean not null default true,
  criado_em     timestamptz not null default now(),
  atualizado_em timestamptz not null default now()
);

comment on table public.veiculos is
  'Veiculos da frota. Sem DELETE fisico: use ativo = false. Placa normalizada (maiusculas, sem hifen).';

-- Unicidade da placa (parcial: veiculos sem placa nao colidem entre si).
-- Como o CHECK forca a forma normalizada, o indice sobre a coluna ja e normalizado.
create unique index if not exists ux_veiculos_placa
  on public.veiculos (placa)
  where placa is not null;

drop trigger if exists trg_veiculos_atualizado_em on public.veiculos;
create trigger trg_veiculos_atualizado_em
  before update on public.veiculos
  for each row execute function public.set_atualizado_em();

-- ---------------------------------------------------------------------------
-- RLS deny-by-default: habilitado, sem politicas e sem grants aos papeis da API.
-- ---------------------------------------------------------------------------
alter table public.veiculos enable row level security;

revoke all on table public.veiculos from anon, authenticated;

-- ---------------------------------------------------------------------------
-- Seed idempotente com dados FICTICIOS.
-- ATENCAO: o admin deve substituir este registro pelo veiculo real (descricao e
-- placa) pela area administrativa. A placa abaixo e apenas um exemplo.
-- ---------------------------------------------------------------------------
insert into public.veiculos (descricao, placa)
values ('Veículo de exemplo', 'AAA0A00')
on conflict do nothing;
