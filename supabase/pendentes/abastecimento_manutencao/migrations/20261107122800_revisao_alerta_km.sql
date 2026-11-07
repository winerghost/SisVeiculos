-- Migration 28: recurso opcional ABASTECIMENTO E MANUTENCAO, parte 4 (alerta de revisao por km).
-- Nova migration; nao edita as anteriores. Idempotente.
--
-- Regra de negocio no BANCO, frontend so exibe (CLAUDE.md). Onde fica o limite e como se calcula:
--  * LIMITE: tabela public.revisao_regras (1 linha por veiculo): intervalo_km (distancia entre
--    revisoes, 1..1000000) e aviso_km (antecedencia do alerta, 0..intervalo_km; padrao 1000).
--    Definida SO por admin ativo, pela RPC definir_regra_revisao (service_role), e auditada
--    ('revisao_regra_definida', de/para = intervalo_km antigo/novo, detalhes = ids e valores
--    da regra). Sem DELETE/TRUNCATE; o UPDATE existe apenas dentro da RPC (service_role so SELECT).
--  * BASE (ultimo km de revisao) DERIVADA, nao duplicada: maior km entre as manutencoes VIGENTES
--    (nao anuladas) do veiculo com tipo = 'revisao'. Anular a revisao muda a base sozinha. Como
--    o odometro nao retrocede (migration 26), o maior km e o da revisao mais recente.
--  * KM ATUAL = maior km entre abastecimentos e manutencoes vigentes (odometro_registros_vigentes).
--  * SITUACAO (view public.revisao_situacao, security_invoker, SELECT so para service_role):
--      sem_referencia : nao ha revisao registrada (o admin lanca a revisao inicial com o km);
--      vencida        : km_atual >= proxima_revisao_km;
--      proxima        : km_atual >= proxima_revisao_km - aviso_km;
--      em_dia         : caso contrario.
--    proxima_revisao_km = ultima_revisao_km + intervalo_km; km_restante = proxima - atual
--    (negativo quando vencida). Veiculo sem regra nao aparece na view (sem alerta configurado).
--  * O backend consulta a view; nao recalcula. Com o recurso DESLIGADO o backend recusa os
--    endpoints e nao consulta (o banco nao le a flag: a view e so dado derivado).
--  * Alternativa nao adotada: coluna em veiculos. Exigiria mexer no gatilho de veiculos (ator,
--    advisory perfis_admin, auditoria da migration 13) para um dado de outro recurso.
--
-- Ator e locks: o gatilho BEFORE INSERT OR UPDATE chama privado.exigir_admin_ativo (advisory
-- EXCLUSIVO 'sisveiculos.perfis_admin' ANTES de ler o perfil, como configuracoes_validar): ordem
-- advisory -> linhas do contrato da migration 15. Esta escrita e administrativa e rara; nao
-- combinar com registro de odometro na mesma transacao (advisory do odometro, migration 26).
--
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. Tabela
-- ---------------------------------------------------------------------------
create table if not exists public.revisao_regras (
  veiculo_id     uuid primary key references public.veiculos (id) on delete restrict,
  intervalo_km   integer not null,
  aviso_km       integer not null default 1000,
  definido_por   uuid not null references public.perfis (id) on delete restrict,
  atualizado_em  timestamptz not null default now(),

  constraint ck_revisao_regras_intervalo check (intervalo_km between 1 and 1000000),
  constraint ck_revisao_regras_aviso check (aviso_km >= 0 and aviso_km <= intervalo_km)
);

comment on table public.revisao_regras is
  'Regra de alerta de revisao por km, por veiculo (recurso Abastecimento e manutencao). Escrita so via definir_regra_revisao (admin).';

create index if not exists ix_revisao_regras_definido_por on public.revisao_regras (definido_por);

drop trigger if exists trg_revisao_regras_atualizado_em on public.revisao_regras;
create trigger trg_revisao_regras_atualizado_em
  before update on public.revisao_regras
  for each row execute function public.set_atualizado_em();

drop trigger if exists trg_revisao_regras_sem_delete on public.revisao_regras;
create trigger trg_revisao_regras_sem_delete
  before delete on public.revisao_regras
  for each row execute function public.bloquear_alteracao();
alter table public.revisao_regras enable always trigger trg_revisao_regras_sem_delete;

drop trigger if exists trg_revisao_regras_sem_truncate on public.revisao_regras;
create trigger trg_revisao_regras_sem_truncate
  before truncate on public.revisao_regras
  for each statement execute function public.bloquear_alteracao();
alter table public.revisao_regras enable always trigger trg_revisao_regras_sem_truncate;

alter table public.revisao_regras enable row level security;
drop policy if exists revisao_regras_sem_acesso_api on public.revisao_regras;
create policy revisao_regras_sem_acesso_api on public.revisao_regras
  for all to anon, authenticated using (false) with check (false);

revoke all on table public.revisao_regras from public, anon, authenticated, service_role;
grant select on table public.revisao_regras to service_role;

-- ---------------------------------------------------------------------------
-- 2. Validacao (admin ativo) e auditoria
-- ---------------------------------------------------------------------------
create or replace function public.revisao_regras_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'UPDATE' and new.veiculo_id is distinct from old.veiculo_id then
    raise exception 'O veículo da regra de revisão não pode mudar.' using errcode = 'AG004';
  end if;
  perform privado.exigir_admin_ativo(new.definido_por);
  return new;
end;
$$;

create or replace function public.revisao_regras_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'UPDATE' and new.intervalo_km = old.intervalo_km and new.aviso_km = old.aviso_km then
    return null;  -- reenvio da mesma regra: nada mudou
  end if;
  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
  values ('revisao_regras', new.veiculo_id, 'revisao_regra_definida', new.definido_por,
          case when tg_op = 'UPDATE' then old.intervalo_km::text end, new.intervalo_km::text,
          jsonb_build_object('veiculo_id', new.veiculo_id, 'intervalo_km', new.intervalo_km,
                             'aviso_km', new.aviso_km));
  return null;
end;
$$;

revoke all on function public.revisao_regras_validar() from public, anon, authenticated, service_role;
revoke all on function public.revisao_regras_auditar() from public, anon, authenticated, service_role;

drop trigger if exists trg_revisao_regras_validar on public.revisao_regras;
create trigger trg_revisao_regras_validar
  before insert or update on public.revisao_regras
  for each row execute function public.revisao_regras_validar();
alter table public.revisao_regras enable always trigger trg_revisao_regras_validar;

drop trigger if exists trg_revisao_regras_auditoria on public.revisao_regras;
create trigger trg_revisao_regras_auditoria
  after insert or update on public.revisao_regras
  for each row execute function public.revisao_regras_auditar();
alter table public.revisao_regras enable always trigger trg_revisao_regras_auditoria;

-- ---------------------------------------------------------------------------
-- 3. RPC (upsert pelo admin)
-- ---------------------------------------------------------------------------
create or replace function public.definir_regra_revisao(
  p_veiculo_id   uuid,
  p_intervalo_km integer,
  p_aviso_km     integer,
  p_definido_por uuid
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.revisao_regras (veiculo_id, intervalo_km, aviso_km, definido_por)
  values (p_veiculo_id, p_intervalo_km, coalesce(p_aviso_km, 1000), p_definido_por)
  on conflict (veiculo_id) do update
    set intervalo_km = excluded.intervalo_km,
        aviso_km     = excluded.aviso_km,
        definido_por = excluded.definido_por;
end;
$$;

revoke all on function public.definir_regra_revisao(uuid, integer, integer, uuid)
  from public, anon, authenticated;
grant execute on function public.definir_regra_revisao(uuid, integer, integer, uuid) to service_role;

-- ---------------------------------------------------------------------------
-- 4. View do alerta (calculo no banco)
-- ---------------------------------------------------------------------------
create or replace view public.revisao_situacao
with (security_invoker = true) as
select r.veiculo_id,
       r.intervalo_km,
       r.aviso_km,
       b.ultima_revisao_km,
       o.km_atual,
       b.ultima_revisao_km + r.intervalo_km                    as proxima_revisao_km,
       b.ultima_revisao_km + r.intervalo_km - o.km_atual        as km_restante,
       case
         when b.ultima_revisao_km is null or o.km_atual is null then 'sem_referencia'
         when o.km_atual >= b.ultima_revisao_km + r.intervalo_km then 'vencida'
         when o.km_atual >= b.ultima_revisao_km + r.intervalo_km - r.aviso_km then 'proxima'
         else 'em_dia'
       end                                                      as situacao
  from public.revisao_regras r
  left join lateral (
    select max(m.km) as ultima_revisao_km
      from public.manutencoes_vigentes m
     where m.veiculo_id = r.veiculo_id and m.tipo = 'revisao'
  ) b on true
  left join lateral (
    select max(o2.km) as km_atual
      from public.odometro_registros_vigentes o2
     where o2.veiculo_id = r.veiculo_id
  ) o on true;

comment on view public.revisao_situacao is
  'Alerta de revisao por km calculado no banco. situacao: sem_referencia | em_dia | proxima | vencida.';

revoke all on public.revisao_situacao from public, anon, authenticated, service_role;
grant select on public.revisao_situacao to service_role;

-- ---------------------------------------------------------------------------
-- 5. Trava + recarga do PostgREST (tabelas, views e RPCs novas das migrations 25 a 28)
-- ---------------------------------------------------------------------------
do $$
declare
  f text;
  r text;
  p text;
begin
  if not (select relrowsecurity from pg_class where oid = 'public.revisao_regras'::regclass) then
    raise exception 'public.revisao_regras sem RLS.';
  end if;
  if has_table_privilege('anon', 'public.revisao_regras', 'select, insert, update, delete')
     or has_table_privilege('authenticated', 'public.revisao_regras', 'select, insert, update, delete') then
    raise exception 'public.revisao_regras permite acesso direto a anon/authenticated.';
  end if;
  foreach p in array array['insert', 'update', 'delete', 'truncate', 'trigger', 'references'] loop
    if has_table_privilege('service_role', 'public.revisao_regras', p) then
      raise exception 'service_role tem % em public.revisao_regras.', p;
    end if;
  end loop;

  foreach f in array array['public.abastecimentos_vigentes', 'public.manutencoes_vigentes',
                           'public.odometro_registros_vigentes', 'public.revisao_situacao'] loop
    if has_table_privilege('anon', f::regclass, 'select')
       or has_table_privilege('authenticated', f::regclass, 'select') then
      raise exception 'view % legivel por anon/authenticated.', f;
    end if;
    if not has_table_privilege('service_role', f::regclass, 'select') then
      raise exception 'service_role sem SELECT em %.', f;
    end if;
    if not exists (select 1 from pg_class where oid = f::regclass
                    and reloptions is not null and 'security_invoker=true' = any (reloptions)) then
      raise exception 'view % sem security_invoker.', f;
    end if;
  end loop;

  foreach f in array array['public.revisao_regras_validar()', 'public.revisao_regras_auditar()'] loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if has_function_privilege(r, f::regprocedure, 'execute') then
        raise exception '% ainda executa %.', r, f;
      end if;
    end loop;
  end loop;

  f := 'public.definir_regra_revisao(uuid, integer, integer, uuid)';
  if has_function_privilege('anon', f::regprocedure, 'execute')
     or has_function_privilege('authenticated', f::regprocedure, 'execute')
     or not has_function_privilege('service_role', f::regprocedure, 'execute') then
    raise exception 'EXECUTE incorreto em %.', f;
  end if;
end
$$;

notify pgrst, 'reload schema';
