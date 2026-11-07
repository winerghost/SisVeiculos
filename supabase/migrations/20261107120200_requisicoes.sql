-- Migration 3: tabela requisicoes.
-- Motivo: registra o pedido, a autorizacao e a saida/chegada reais de um veiculo.
-- hora_saida e a saida PREVISTA; saida_real_em/chegada_real_em sao os registros reais.
-- Sem prazo maximo de uso nem antecedencia minima: nenhum CHECK depende de now().
-- usuario_id vem do JWT no backend, nunca do body.
-- NAO inclui aqui: indice unico parcial por veiculo_id (migration 4) e triggers de
-- transicao de estado (migration 5). Os CHECKs abaixo garantem so a coerencia
-- entre status e colunas.
-- Sem DELETE fisico: FKs com ON DELETE RESTRICT; cancelamento/negativa via status.
-- RLS ligado e deny-by-default: sem politicas; o Flask usa service_role (ignora RLS).
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).
-- Reutiliza public.set_atualizado_em() da migration 1.

create table if not exists public.requisicoes (
  id                      uuid primary key default gen_random_uuid(),
  usuario_id              uuid not null references public.perfis (id) on delete restrict,
  nome_solicitante        text not null check (char_length(btrim(nome_solicitante)) between 1 and 120),
  unidade                 public.unidade not null,
  hora_saida              timestamptz not null,
  trajeto                 text not null check (char_length(btrim(trajeto)) between 1 and 500),
  observacao              text check (observacao is null or char_length(observacao) <= 500),
  veiculo_id              uuid not null references public.veiculos (id) on delete restrict,
  status                  public.status_requisicao not null default 'solicitada',
  autorizado_por          uuid references public.perfis (id) on delete restrict,
  autorizado_em           timestamptz,
  saida_real_em           timestamptz,
  chegada_real_em         timestamptz,
  saida_registrada_por    uuid references public.perfis (id) on delete restrict,
  chegada_registrada_por  uuid references public.perfis (id) on delete restrict,
  criado_em               timestamptz not null default now(),
  atualizado_em           timestamptz not null default now(),

  -- chegada real depois da saida real (so quando ambas existem).
  constraint ck_requisicoes_chegada_apos_saida
    check (saida_real_em is null or chegada_real_em is null or chegada_real_em > saida_real_em),

  -- autorizador e data andam juntos; quem registrou e quando tambem.
  constraint ck_requisicoes_autorizacao_par
    check ((autorizado_por is null) = (autorizado_em is null)),
  constraint ck_requisicoes_saida_par
    check ((saida_real_em is null) = (saida_registrada_por is null)),
  constraint ck_requisicoes_chegada_par
    check ((chegada_real_em is null) = (chegada_registrada_por is null)),

  -- coerencia entre status e colunas de saida/chegada/autorizacao.
  constraint ck_requisicoes_status_coerente
    check (
      case status
        when 'solicitada' then saida_real_em is null and chegada_real_em is null
        when 'cancelada'  then saida_real_em is null and chegada_real_em is null
        when 'negada'     then saida_real_em is null and chegada_real_em is null
                               and autorizado_por is not null
        when 'autorizada' then saida_real_em is null and chegada_real_em is null
                               and autorizado_por is not null
        when 'em_uso'     then saida_real_em is not null and chegada_real_em is null
        when 'concluida'  then saida_real_em is not null and chegada_real_em is not null
      end
    )
);

comment on table public.requisicoes is
  'Requisicoes de uso de veiculo. hora_saida e prevista; saida/chegada reais em *_real_em. Sem DELETE fisico.';

-- ---------------------------------------------------------------------------
-- Indices de apoio (FKs nao ganham indice automatico no Postgres)
-- ---------------------------------------------------------------------------
-- usuario_id: "minhas requisicoes" (anti-IDOR) e checagem da FK.
create index if not exists ix_requisicoes_usuario_id on public.requisicoes (usuario_id);
-- status: fila do autorizador (listar solicitadas/autorizadas/em_uso).
create index if not exists ix_requisicoes_status on public.requisicoes (status);
-- veiculo_id: historico por veiculo e checagem da FK.
create index if not exists ix_requisicoes_veiculo_id on public.requisicoes (veiculo_id);

drop trigger if exists trg_requisicoes_atualizado_em on public.requisicoes;
create trigger trg_requisicoes_atualizado_em
  before update on public.requisicoes
  for each row execute function public.set_atualizado_em();

-- ---------------------------------------------------------------------------
-- RLS deny-by-default: habilitado, sem politicas e sem grants aos papeis da API.
-- ---------------------------------------------------------------------------
alter table public.requisicoes enable row level security;

revoke all on table public.requisicoes from anon, authenticated;
