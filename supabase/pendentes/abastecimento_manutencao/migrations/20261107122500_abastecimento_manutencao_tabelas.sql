-- Migration 25: recurso opcional ABASTECIMENTO E MANUTENCAO, parte 1 (tabelas, imutabilidade, RLS e grants).
-- Nova migration; nao edita as anteriores. Idempotente.
--
-- Motivo: CLAUDE.md ("Recursos opcionais"): com configuracoes.abastecimento_manutencao ligado, o
-- sistema registra abastecimentos e manutencoes por veiculo, com alerta de revisao por km (exige
-- Quilometragem ligada). Desligado por padrao; desligar nunca apaga dados. Esta migration cria
-- so a ESTRUTURA e as invariantes dos dados; as regras (odometro, ator, janela) vem na 26, a
-- auditoria e a escrita (RPC) na 27 e o alerta de revisao na 28.
--
-- DEPENDENCIA DE FLAG (confirmada, nao recriada): ck_configuracoes_abastecimento_exige_km
-- (migration 8) + AG010 em configuracoes_validar impedem ligar abastecimento_manutencao com a
-- quilometragem desligada e desligar a quilometragem com o abastecimento ligado.
--
-- Desenho (conjunto minimo):
--  * public.abastecimentos: veiculo, requisicao opcional, quando (abastecido_em), litros
--    numeric(6,2) > 0, valor_total numeric(10,2) >= 0 opcional, km do odometro (>= 0),
--    combustivel (enum curto), registrado_por e registrado_em. SEM texto livre: nenhum risco de
--    dado pessoal em erro, log ou auditoria.
--  * public.manutencoes: veiculo, tipo (enum fechado), quando (realizada_em), km (>= 0), valor
--    opcional, descricao opcional (texto livre, max 200), registrado_por e registrado_em. A
--    descricao e o unico texto livre: pode receber nome/telefone digitado. Nunca vai para
--    auditoria, logs ou n8n, e os erros do gatilho (migration 26) saem SEM o conteudo da linha
--    (licao M1 do checklist: o `DETAIL: Failing row contains (...)` do Postgres vazaria o texto).
--  * IMUTAVEIS: UPDATE, DELETE e TRUNCATE bloqueados por gatilho (AG003, bloquear_alteracao),
--    ENABLE ALWAYS, e sem o privilegio correspondente ao service_role. Sem DELETE fisico.
--  * CORRECAO = ANULACAO AUDITADA (decisao): tabelas filhas append-only
--    abastecimentos_anulacoes / manutencoes_anulacoes (1:1 com o registro, PK = FK) com motivo
--    em enum fechado (sem texto livre), anulado_por e anulado_em. O registro original NUNCA muda;
--    "vigente" = sem anulacao (views na migration 26). Corrige-se anulando e registrando de novo.
--    Por que anulacao e nao retificacao encadeada: o odometro nao retrocede por ordem de
--    registro; anular so REMOVE um ponto da sequencia, o que nunca a quebra, logo nao ha regra de
--    limite inferior/superior por posicao nem coluna de ordem. Retificacao exigiria isso.
--  * Sem DELETE fisico, timestamptz em tudo (armazenado em UTC; exibicao em America/Sao_Paulo).
--
-- RLS/grants (deny-by-default): RLS ligado nas quatro tabelas, politica explicita de negacao a
-- anon/authenticated, nenhum privilegio a anon/authenticated. service_role: SOMENTE SELECT; a
-- escrita passa por RPC SECURITY DEFINER (migration 27).
--
-- Erros previstos: 23514 (ck_abastecimentos_* / ck_manutencoes_*), 23503 (FK), 23505 (anulacao
-- repetida: pk_*_anulacoes), AG003 (UPDATE/DELETE/TRUNCATE).

-- ---------------------------------------------------------------------------
-- 1. Enums fechados (idempotente)
-- ---------------------------------------------------------------------------
do $$
begin
  if not exists (select 1 from pg_type t join pg_namespace n on n.oid = t.typnamespace
                 where n.nspname = 'public' and t.typname = 'tipo_combustivel') then
    create type public.tipo_combustivel as enum ('gasolina', 'etanol', 'diesel', 'gnv', 'outro');
  end if;
  if not exists (select 1 from pg_type t join pg_namespace n on n.oid = t.typnamespace
                 where n.nspname = 'public' and t.typname = 'tipo_manutencao') then
    create type public.tipo_manutencao as enum ('revisao', 'troca_oleo', 'pneus', 'freios', 'outro');
  end if;
  if not exists (select 1 from pg_type t join pg_namespace n on n.oid = t.typnamespace
                 where n.nspname = 'public' and t.typname = 'motivo_anulacao') then
    create type public.motivo_anulacao as enum ('erro_de_digitacao', 'duplicado', 'registro_indevido');
  end if;
end
$$;

-- ---------------------------------------------------------------------------
-- 2. abastecimentos
-- ---------------------------------------------------------------------------
create table if not exists public.abastecimentos (
  id              uuid primary key default gen_random_uuid(),
  veiculo_id      uuid not null references public.veiculos (id) on delete restrict,
  requisicao_id   uuid references public.requisicoes (id) on delete restrict,
  abastecido_em   timestamptz not null,
  litros          numeric(6, 2) not null,
  valor_total     numeric(10, 2),
  km              integer not null,
  combustivel     public.tipo_combustivel not null,
  registrado_por  uuid not null references public.perfis (id) on delete restrict,
  registrado_em   timestamptz not null default now(),

  constraint ck_abastecimentos_litros_positivo check (litros > 0),
  constraint ck_abastecimentos_valor_nao_negativo check (valor_total is null or valor_total >= 0),
  constraint ck_abastecimentos_km_nao_negativo check (km >= 0)
);

comment on table public.abastecimentos is
  'Abastecimentos (recurso Abastecimento e manutencao). Imutavel; correcao por anulacao auditada. Escrita so via registrar_abastecimento.';
comment on column public.abastecimentos.km is
  'Odometro no abastecimento. Nao retrocede por veiculo entre registros vigentes (migration 26).';

create index if not exists ix_abastecimentos_veiculo_km on public.abastecimentos (veiculo_id, km);
create index if not exists ix_abastecimentos_veiculo_data on public.abastecimentos (veiculo_id, abastecido_em desc);
create index if not exists ix_abastecimentos_requisicao on public.abastecimentos (requisicao_id)
  where requisicao_id is not null;
create index if not exists ix_abastecimentos_registrado_por on public.abastecimentos (registrado_por);

-- ---------------------------------------------------------------------------
-- 3. manutencoes
-- ---------------------------------------------------------------------------
create table if not exists public.manutencoes (
  id              uuid primary key default gen_random_uuid(),
  veiculo_id      uuid not null references public.veiculos (id) on delete restrict,
  tipo            public.tipo_manutencao not null,
  realizada_em    timestamptz not null,
  km              integer not null,
  valor           numeric(10, 2),
  descricao       text,
  registrado_por  uuid not null references public.perfis (id) on delete restrict,
  registrado_em   timestamptz not null default now(),

  constraint ck_manutencoes_km_nao_negativo check (km >= 0),
  constraint ck_manutencoes_valor_nao_negativo check (valor is null or valor >= 0),
  constraint ck_manutencoes_descricao check (
    descricao is null or (char_length(descricao) <= 200 and descricao ~ '\S')
  )
);

comment on table public.manutencoes is
  'Manutencoes (recurso Abastecimento e manutencao). Imutavel; correcao por anulacao auditada. Escrita so via registrar_manutencao.';
comment on column public.manutencoes.descricao is
  'Texto livre (max 200). Pode conter dado pessoal digitado: nunca vai para auditoria, logs ou n8n.';

create index if not exists ix_manutencoes_veiculo_km on public.manutencoes (veiculo_id, km);
create index if not exists ix_manutencoes_veiculo_data on public.manutencoes (veiculo_id, realizada_em desc);
create index if not exists ix_manutencoes_registrado_por on public.manutencoes (registrado_por);

-- ---------------------------------------------------------------------------
-- 4. Anulacoes (append-only; 1 por registro)
-- ---------------------------------------------------------------------------
create table if not exists public.abastecimentos_anulacoes (
  abastecimento_id  uuid not null references public.abastecimentos (id) on delete restrict,
  motivo            public.motivo_anulacao not null,
  anulado_por       uuid not null references public.perfis (id) on delete restrict,
  anulado_em        timestamptz not null default now(),

  constraint pk_abastecimentos_anulacoes primary key (abastecimento_id)
);

create table if not exists public.manutencoes_anulacoes (
  manutencao_id  uuid not null references public.manutencoes (id) on delete restrict,
  motivo         public.motivo_anulacao not null,
  anulado_por    uuid not null references public.perfis (id) on delete restrict,
  anulado_em     timestamptz not null default now(),

  constraint pk_manutencoes_anulacoes primary key (manutencao_id)
);

comment on table public.abastecimentos_anulacoes is
  'Anulacao auditada de um abastecimento (o registro original permanece). Imutavel. Escrita so via anular_abastecimento.';
comment on table public.manutencoes_anulacoes is
  'Anulacao auditada de uma manutencao (o registro original permanece). Imutavel. Escrita so via anular_manutencao.';

create index if not exists ix_abastecimentos_anulacoes_por on public.abastecimentos_anulacoes (anulado_por);
create index if not exists ix_manutencoes_anulacoes_por on public.manutencoes_anulacoes (anulado_por);

-- ---------------------------------------------------------------------------
-- 5. Imutabilidade: sem UPDATE, DELETE nem TRUNCATE, mesmo para service_role/dono
-- ---------------------------------------------------------------------------
do $$
declare
  t text;
begin
  foreach t in array array['abastecimentos', 'manutencoes',
                           'abastecimentos_anulacoes', 'manutencoes_anulacoes']
  loop
    execute format('drop trigger if exists trg_%s_imutavel on public.%I', t, t);
    execute format(
      'create trigger trg_%s_imutavel before update or delete on public.%I '
      'for each row execute function public.bloquear_alteracao()', t, t);
    execute format('alter table public.%I enable always trigger trg_%s_imutavel', t, t);

    execute format('drop trigger if exists trg_%s_sem_truncate on public.%I', t, t);
    execute format(
      'create trigger trg_%s_sem_truncate before truncate on public.%I '
      'for each statement execute function public.bloquear_alteracao()', t, t);
    execute format('alter table public.%I enable always trigger trg_%s_sem_truncate', t, t);
  end loop;
end
$$;

-- ---------------------------------------------------------------------------
-- 6. RLS deny-by-default, negacao explicita e grants minimos
-- ---------------------------------------------------------------------------
alter table public.abastecimentos          enable row level security;
alter table public.manutencoes             enable row level security;
alter table public.abastecimentos_anulacoes enable row level security;
alter table public.manutencoes_anulacoes    enable row level security;

drop policy if exists abastecimentos_sem_acesso_api on public.abastecimentos;
create policy abastecimentos_sem_acesso_api on public.abastecimentos
  for all to anon, authenticated using (false) with check (false);

drop policy if exists manutencoes_sem_acesso_api on public.manutencoes;
create policy manutencoes_sem_acesso_api on public.manutencoes
  for all to anon, authenticated using (false) with check (false);

drop policy if exists abastecimentos_anulacoes_sem_acesso_api on public.abastecimentos_anulacoes;
create policy abastecimentos_anulacoes_sem_acesso_api on public.abastecimentos_anulacoes
  for all to anon, authenticated using (false) with check (false);

drop policy if exists manutencoes_anulacoes_sem_acesso_api on public.manutencoes_anulacoes;
create policy manutencoes_anulacoes_sem_acesso_api on public.manutencoes_anulacoes
  for all to anon, authenticated using (false) with check (false);

revoke all on table public.abastecimentos, public.manutencoes,
  public.abastecimentos_anulacoes, public.manutencoes_anulacoes
  from public, anon, authenticated, service_role;
grant select on table public.abastecimentos, public.manutencoes,
  public.abastecimentos_anulacoes, public.manutencoes_anulacoes to service_role;

-- Trava: a migration falha se sobrar acesso direto indevido.
do $$
declare
  t text;
  p text;
begin
  foreach t in array array['abastecimentos', 'manutencoes',
                           'abastecimentos_anulacoes', 'manutencoes_anulacoes']
  loop
    if not (select relrowsecurity from pg_class where oid = ('public.' || t)::regclass) then
      raise exception 'public.% sem RLS.', t;
    end if;
    if has_table_privilege('anon', 'public.' || t, 'select, insert, update, delete')
       or has_table_privilege('authenticated', 'public.' || t, 'select, insert, update, delete') then
      raise exception 'public.% permite acesso direto a anon/authenticated.', t;
    end if;
    foreach p in array array['insert', 'update', 'delete', 'truncate', 'trigger', 'references'] loop
      if has_table_privilege('service_role', 'public.' || t, p) then
        raise exception 'service_role tem % em public.%.', p, t;
      end if;
    end loop;
  end loop;
end
$$;
