-- Migration: tabela localizacoes_viagem (parte 2 de 4). Nova migration; nao edita as anteriores. Idempotente.
-- Depende de 20261112120000_motorista_localizacao_flag.sql.
--
-- Motivo: guardar latitude/longitude da saida e da chegada registradas pelo MOTORISTA. Visibilidade: so o
-- admin pode ver coordenadas; por isso elas NAO ficam em `requisicoes` (cujo payload vai a varios perfis)
-- e sim nesta tabela separada, que so o backend (service_role) le, e so para o admin.
--
-- Estrutura: id, requisicao_id (FK RESTRICT), momento ('saida'|'chegada'), latitude numeric(9,6)
-- [-90,90], longitude numeric(9,6) [-180,180], precisao_m numeric(8,2) >= 0 (opcional), registrado_por
-- (FK perfis RESTRICT), registrado_em timestamptz (carimbo do servidor). UNIQUE(requisicao_id, momento).
-- NaN e recusado pelos CHECKs (NaN > qualquer numero no Postgres).
--
-- Append-only: UPDATE, DELETE e TRUNCATE recusados (AG003, funcao public.bloquear_alteracao, ENABLE ALWAYS).
-- Sem DELETE fisico.
--
-- Escrita SO pela RPC registrar_transicao_motorista (migration 20261112120200). O gatilho de INSERT exige o
-- marcador transacional `sisveiculos.registro_motorista` = '<requisicao_id>:<momento>', posto apenas pela
-- RPC com set_config(..., true); INSERT direto (mesmo do service_role, que nem tem INSERT) => AG037.
-- Regras do gatilho (todas com mensagem fixa, sem coordenadas):
--   AG034 recurso desligado (motorista, motorista_registra e motorista_localizacao precisam estar ligados);
--   AG002 registrante inexistente, inativo ou sem role motorista;
--   AG035 registrante nao e o motorista designado (requisicoes.motorista_id);
--   AG001 momento incompativel com o status (saida exige 'autorizada'; chegada exige 'em_uso');
--   AG037 sem o marcador da RPC, momento invalido ou requisicao inexistente.
--
-- Auditoria: cada insercao gera UMA linha (tabela 'requisicoes', registro_id = requisicao, acao NOVA
-- 'localizacao_registrada', ator_id = motorista, detalhes {"recurso":"motorista_localizacao","momento":"..."})
-- SEM latitude, longitude nem precisao. ck_auditoria_acao ganha o valor (as 20 existentes preservadas);
-- ck_auditoria_tabela nao muda.
--
-- RLS deny-by-default: RLS ligado + politica explicita using(false) para anon/authenticated; REVOKE de
-- public/anon/authenticated/service_role e GRANT SELECT apenas a service_role (leitura pelo backend, que
-- so a repassa ao admin). Sem INSERT/UPDATE/DELETE para ninguem da API.
-- LGPD: dado pessoal sensivel (deslocamento do servidor). Coletar so o necessario; nao logar; so admin ve.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. Tabela
-- ---------------------------------------------------------------------------
create table if not exists public.localizacoes_viagem (
  id             uuid primary key default gen_random_uuid(),
  requisicao_id  uuid not null references public.requisicoes (id) on delete restrict,
  momento        text not null,
  latitude       numeric(9, 6) not null,
  longitude      numeric(9, 6) not null,
  precisao_m     numeric(8, 2),
  registrado_por uuid not null references public.perfis (id) on delete restrict,
  registrado_em  timestamptz not null default now(),

  constraint uq_localizacoes_viagem_requisicao_momento unique (requisicao_id, momento),
  constraint ck_localizacoes_viagem_momento check (momento in ('saida', 'chegada')),
  constraint ck_localizacoes_viagem_latitude check (latitude between -90 and 90),
  constraint ck_localizacoes_viagem_longitude check (longitude between -180 and 180),
  constraint ck_localizacoes_viagem_precisao check (precisao_m is null or (precisao_m >= 0 and precisao_m <> 'NaN'))
);

comment on table public.localizacoes_viagem is
  'Coordenadas da saida/chegada registradas pelo motorista (recurso opcional). Dado sensivel: so o admin ve (via backend). Append-only; escrita so pela RPC registrar_transicao_motorista.';

create index if not exists ix_localizacoes_viagem_registrado_por
  on public.localizacoes_viagem (registrado_por);

-- ---------------------------------------------------------------------------
-- 2. Append-only
-- ---------------------------------------------------------------------------
drop trigger if exists trg_localizacoes_viagem_imutavel on public.localizacoes_viagem;
create trigger trg_localizacoes_viagem_imutavel
  before update or delete on public.localizacoes_viagem
  for each row execute function public.bloquear_alteracao();
alter table public.localizacoes_viagem enable always trigger trg_localizacoes_viagem_imutavel;

drop trigger if exists trg_localizacoes_viagem_sem_truncate on public.localizacoes_viagem;
create trigger trg_localizacoes_viagem_sem_truncate
  before truncate on public.localizacoes_viagem
  for each statement execute function public.bloquear_alteracao();
alter table public.localizacoes_viagem enable always trigger trg_localizacoes_viagem_sem_truncate;

-- ---------------------------------------------------------------------------
-- 3. Auditoria: nova acao 'localizacao_registrada' (preserva as 20 existentes)
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
    'localizacao_registrada'
  ));

-- ---------------------------------------------------------------------------
-- 4. Gatilho de validacao (BEFORE INSERT)
-- Leitura das flags SEM FOR SHARE (mesma convencao das migrations 18/22: sem ordem de lock nova).
-- Ordem de locks: requisicoes -> perfis (igual a requisicoes_validar).
-- ---------------------------------------------------------------------------
create or replace function public.localizacoes_viagem_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_status       public.status_requisicao;
  v_motorista_id uuid;
  v_role         public.role_usuario;
  v_ativo        boolean;
  v_mot          boolean;
  v_reg          boolean;
  v_loc          boolean;
begin
  new.registrado_em := clock_timestamp();  -- carimbo do servidor

  if new.requisicao_id is null or new.momento is null or new.registrado_por is null then
    raise exception 'Campo obrigatório da localização ausente.' using errcode = '23502';
  end if;

  if coalesce(current_setting('sisveiculos.registro_motorista', true), '')
       is distinct from new.requisicao_id::text || ':' || new.momento then
    raise exception 'A localização só é gravada pelo registro de saída ou chegada do motorista.'
      using errcode = 'AG037';
  end if;

  if new.momento not in ('saida', 'chegada') then
    raise exception 'Momento da localização inválido.' using errcode = 'AG037';
  end if;

  select motorista, motorista_registra, motorista_localizacao into v_mot, v_reg, v_loc
    from public.configuracoes where id;
  if not (coalesce(v_mot, false) and coalesce(v_reg, false) and coalesce(v_loc, false)) then
    raise exception 'A localização do motorista está desligada em Configurações.' using errcode = 'AG034';
  end if;

  select status, motorista_id into v_status, v_motorista_id
    from public.requisicoes where id = new.requisicao_id for share;
  if not found then
    raise exception 'Requisição inexistente.' using errcode = 'AG037';
  end if;

  if not ((new.momento = 'saida' and v_status = 'autorizada')
          or (new.momento = 'chegada' and v_status = 'em_uso')) then
    raise exception 'Localização de % não pode ser registrada com a requisição %.', new.momento, v_status
      using errcode = 'AG001';
  end if;

  select role, ativo into v_role, v_ativo from public.perfis where id = new.registrado_por for share;
  if not found or not v_ativo or v_role is distinct from 'motorista'::public.role_usuario then
    raise exception 'Apenas motorista ativo registra localização.' using errcode = 'AG002';
  end if;

  if v_motorista_id is distinct from new.registrado_por then
    raise exception 'Somente o motorista designado nesta requisição registra a localização.'
      using errcode = 'AG035';
  end if;

  return new;
end;
$$;

revoke all on function public.localizacoes_viagem_validar() from public, anon, authenticated, service_role;

drop trigger if exists trg_localizacoes_viagem_validar on public.localizacoes_viagem;
create trigger trg_localizacoes_viagem_validar
  before insert on public.localizacoes_viagem
  for each row execute function public.localizacoes_viagem_validar();
alter table public.localizacoes_viagem enable always trigger trg_localizacoes_viagem_validar;

-- ---------------------------------------------------------------------------
-- 5. Auditoria da insercao (AFTER INSERT), SEM coordenadas
-- ---------------------------------------------------------------------------
create or replace function public.localizacoes_viagem_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
  values ('requisicoes', new.requisicao_id, 'localizacao_registrada', new.registrado_por, null, null,
          jsonb_build_object('recurso', 'motorista_localizacao', 'momento', new.momento));
  return null;
end;
$$;

revoke all on function public.localizacoes_viagem_auditar() from public, anon, authenticated, service_role;

drop trigger if exists trg_localizacoes_viagem_auditoria on public.localizacoes_viagem;
create trigger trg_localizacoes_viagem_auditoria
  after insert on public.localizacoes_viagem
  for each row execute function public.localizacoes_viagem_auditar();
alter table public.localizacoes_viagem enable always trigger trg_localizacoes_viagem_auditoria;

-- ---------------------------------------------------------------------------
-- 6. RLS deny-by-default, negacao explicita e grants minimos
-- ---------------------------------------------------------------------------
alter table public.localizacoes_viagem enable row level security;

drop policy if exists localizacoes_viagem_sem_acesso_api on public.localizacoes_viagem;
create policy localizacoes_viagem_sem_acesso_api on public.localizacoes_viagem
  for all to anon, authenticated using (false) with check (false);

revoke all on table public.localizacoes_viagem from public, anon, authenticated, service_role;
grant select on table public.localizacoes_viagem to service_role;

-- ---------------------------------------------------------------------------
-- Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v     text;
  d     text;
  v_oid oid;
begin
  if to_regclass('public.localizacoes_viagem') is null then
    raise exception 'public.localizacoes_viagem ausente.';
  end if;
  if not (select relrowsecurity from pg_class where oid = 'public.localizacoes_viagem'::regclass) then
    raise exception 'RLS desligado em localizacoes_viagem.';
  end if;
  if has_table_privilege('anon', 'public.localizacoes_viagem', 'select, insert, update, delete')
     or has_table_privilege('authenticated', 'public.localizacoes_viagem', 'select, insert, update, delete') then
    raise exception 'anon/authenticated com privilegio em localizacoes_viagem.';
  end if;
  if not has_table_privilege('service_role', 'public.localizacoes_viagem', 'select')
     or has_table_privilege('service_role', 'public.localizacoes_viagem', 'insert, update, delete, truncate') then
    raise exception 'service_role deve ter somente SELECT em localizacoes_viagem.';
  end if;

  foreach v in array array['uq_localizacoes_viagem_requisicao_momento', 'ck_localizacoes_viagem_momento',
                           'ck_localizacoes_viagem_latitude', 'ck_localizacoes_viagem_longitude',
                           'ck_localizacoes_viagem_precisao'] loop
    if not exists (select 1 from pg_constraint where conrelid = 'public.localizacoes_viagem'::regclass
                      and conname = v and convalidated) then
      raise exception 'Constraint % ausente em localizacoes_viagem.', v;
    end if;
  end loop;

  foreach v in array array['trg_localizacoes_viagem_imutavel', 'trg_localizacoes_viagem_sem_truncate',
                           'trg_localizacoes_viagem_validar', 'trg_localizacoes_viagem_auditoria'] loop
    if not exists (select 1 from pg_trigger where tgrelid = 'public.localizacoes_viagem'::regclass
                      and tgname = v and tgenabled = 'A' and not tgisinternal) then
      raise exception 'Gatilho % ausente ou nao esta em ENABLE ALWAYS.', v;
    end if;
  end loop;

  foreach v in array array['public.localizacoes_viagem_validar()', 'public.localizacoes_viagem_auditar()'] loop
    v_oid := to_regprocedure(v);
    if v_oid is null
       or has_function_privilege('anon', v_oid, 'execute')
       or has_function_privilege('authenticated', v_oid, 'execute')
       or has_function_privilege('service_role', v_oid, 'execute') then
      raise exception 'Funcao % ausente ou executavel pela API.', v;
    end if;
  end loop;

  select pg_get_constraintdef(oid) into d from pg_constraint
   where conrelid = 'public.auditoria'::regclass and conname = 'ck_auditoria_acao';
  foreach v in array array[
    'criada', 'transicao', 'convite_criado', 'convite_usado', 'convite_revogado',
    'perfil_criado', 'perfil_alterado', 'perfil_unidade_alterada', 'admin_semeado',
    'configuracao_alterada', 'veiculo_criado', 'veiculo_alterado', 'acao_administrativa',
    'acesso_negado', 'checklist_registrado', 'foto_perfil_alterada', 'foto_perfil_removida',
    'relatorio_gerado', 'relatorio_enviado', 'motorista_designado', 'localizacao_registrada'] loop
    if d is null or position('''' || v || '''' in d) = 0 then
      raise exception 'ck_auditoria_acao sem o valor %.', v;
    end if;
  end loop;
end
$$;
