-- Migration: arquivamento PESSOAL de requisicoes (tabela requisicoes_arquivadas).
-- Nova migration; nao edita as anteriores. Idempotente. Depende de perfis e requisicoes.
--
-- Motivo (decisao do usuario, 2026-11-12, itens 4, 5, 7 e 8): arquivar e uma preferencia de organizacao
-- POR USUARIO, sem tocar em requisicoes (a linha encerrada e congelada pelo gatilho, AG001). Cada pessoa tem a
-- sua lista de arquivadas; arquivar nao afeta os outros usuarios, relatorios nem dashboards.
--   - Podem ser arquivadas: concluida, negada, cancelada. NUNCA solicitada, autorizada ou em_uso.
--   - Quem pode (solicitante da propria, autorizador/admin de qualquer, motorista das atribuidas a ele, nunca o
--     vigilante) e regra do BACKEND (usa obter() com escopo/anti-IDOR). O banco garante so o invariante de
--     estado (AG041) e a integridade referencial.
--   - NAO vai para auditoria (item 7): nao e transicao de estado nem acao administrativa; so ids e data.
--   - Desarquivar = apagar a linha do vinculo. E o UNICO DELETE desta tabela e vale so para a preferencia
--     (nunca para requisicoes, perfis ou auditoria, que seguem sem DELETE); nenhum dado oficial se perde.
--
-- Colunas: usuario_id (quem arquivou), requisicao_id, arquivada_em (timestamptz, UTC; exibicao em
-- America/Sao_Paulo; carimbada pelo servidor). PK composta (usuario_id, requisicao_id) = idempotencia e
-- protecao contra corrida (duas chamadas simultaneas: uma persiste, a outra viola a PK).
-- FKs ON DELETE RESTRICT (nada some em cascata). Indices: PK; (usuario_id, arquivada_em desc) para a listagem;
-- (requisicao_id) para a FK.
-- Erro novo: AG041 "So requisicoes concluidas, negadas ou canceladas podem ser arquivadas." (insercao com a
-- requisicao em outro estado). Mensagem fixa, sem dado pessoal.
-- RLS deny-by-default: habilitada, politica que nega anon/authenticated; revoke de tudo; service_role (backend)
-- recebe somente SELECT, INSERT e DELETE (sem UPDATE/TRUNCATE).

-- ---------------------------------------------------------------------------
-- 1. Tabela
-- ---------------------------------------------------------------------------
create table if not exists public.requisicoes_arquivadas (
  usuario_id    uuid not null references public.perfis (id) on delete restrict,
  requisicao_id uuid not null references public.requisicoes (id) on delete restrict,
  arquivada_em  timestamptz not null default now(),
  primary key (usuario_id, requisicao_id)
);

comment on table public.requisicoes_arquivadas is
  'Arquivamento pessoal: o usuario esconde de SUA lista uma requisicao encerrada (concluida, negada ou cancelada). Preferencia, nao dado oficial; nao e auditada. Desarquivar apaga a linha.';
comment on column public.requisicoes_arquivadas.arquivada_em is
  'Quando o usuario arquivou (timestamptz, UTC; exibicao em America/Sao_Paulo). Carimbada pelo servidor.';

create index if not exists ix_requisicoes_arquivadas_usuario_data
  on public.requisicoes_arquivadas (usuario_id, arquivada_em desc);
create index if not exists ix_requisicoes_arquivadas_requisicao
  on public.requisicoes_arquivadas (requisicao_id);

-- ---------------------------------------------------------------------------
-- 2. Defesa no banco: so requisicao encerrada; carimbo do servidor
-- ---------------------------------------------------------------------------
create or replace function public.requisicoes_arquivadas_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_status public.status_requisicao;
begin
  select status into v_status from public.requisicoes where id = new.requisicao_id;
  if not found or v_status not in ('concluida', 'negada', 'cancelada') then
    raise exception 'Só requisições concluídas, negadas ou canceladas podem ser arquivadas.'
      using errcode = 'AG041';
  end if;
  new.arquivada_em := clock_timestamp();
  return new;
end;
$$;

revoke all on function public.requisicoes_arquivadas_validar() from public, anon, authenticated, service_role;

drop trigger if exists trg_requisicoes_arquivadas_validar on public.requisicoes_arquivadas;
create trigger trg_requisicoes_arquivadas_validar
  before insert on public.requisicoes_arquivadas
  for each row execute function public.requisicoes_arquivadas_validar();

-- ---------------------------------------------------------------------------
-- 3. RLS deny-by-default e grants minimos
-- ---------------------------------------------------------------------------
alter table public.requisicoes_arquivadas enable row level security;

drop policy if exists requisicoes_arquivadas_sem_acesso_api on public.requisicoes_arquivadas;
create policy requisicoes_arquivadas_sem_acesso_api on public.requisicoes_arquivadas
  for all to anon, authenticated using (false) with check (false);

revoke all on table public.requisicoes_arquivadas from public, anon, authenticated, service_role;
grant select, insert, delete on table public.requisicoes_arquivadas to service_role;

-- ---------------------------------------------------------------------------
-- Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v_oid oid := to_regprocedure('public.requisicoes_arquivadas_validar()');
  v_tab regclass := 'public.requisicoes_arquivadas'::regclass;
  v_tipo text;
begin
  if not exists (select 1 from pg_class where oid = v_tab and relrowsecurity) then
    raise exception 'RLS desligado em requisicoes_arquivadas.';
  end if;
  if exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'requisicoes_arquivadas'
              and not (roles::text[] <@ array['anon', 'authenticated'])) then
    raise exception 'requisicoes_arquivadas com politica fora do deny-by-default.';
  end if;
  if has_table_privilege('anon', v_tab, 'select, insert, update, delete')
     or has_table_privilege('authenticated', v_tab, 'select, insert, update, delete') then
    raise exception 'requisicoes_arquivadas acessivel por anon/authenticated.';
  end if;
  if not has_table_privilege('service_role', v_tab, 'select')
     or not has_table_privilege('service_role', v_tab, 'insert')
     or not has_table_privilege('service_role', v_tab, 'delete')
     or has_table_privilege('service_role', v_tab, 'update')
     or has_table_privilege('service_role', v_tab, 'truncate') then
    raise exception 'grants de service_role em requisicoes_arquivadas fora do esperado (select, insert, delete).';
  end if;
  -- FKs sempre RESTRICT (confdeltype 'r').
  if (select count(*) from pg_constraint where conrelid = v_tab and contype = 'f' and confdeltype = 'r') <> 2
     or exists (select 1 from pg_constraint where conrelid = v_tab and contype = 'f' and confdeltype <> 'r') then
    raise exception 'FKs de requisicoes_arquivadas devem ser 2 e ON DELETE RESTRICT.';
  end if;
  select format_type(atttypid, atttypmod) into v_tipo
    from pg_attribute where attrelid = v_tab and attname = 'arquivada_em' and not attisdropped;
  if v_tipo is distinct from 'timestamp with time zone' then
    raise exception 'arquivada_em deve ser timestamptz.';
  end if;
  if not exists (select 1 from pg_indexes where schemaname = 'public' and tablename = 'requisicoes_arquivadas'
                  and indexname = 'ix_requisicoes_arquivadas_usuario_data') then
    raise exception 'indice ix_requisicoes_arquivadas_usuario_data ausente.';
  end if;
  if position('AG041' in pg_get_functiondef(v_oid)) = 0
     or has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'requisicoes_arquivadas_validar sem AG041 ou executavel pela API.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = v_tab
                  and tgname = 'trg_requisicoes_arquivadas_validar' and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'trg_requisicoes_arquivadas_validar ausente ou desabilitado.';
  end if;
end
$$;
