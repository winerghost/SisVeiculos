-- Migration 27: recurso opcional ABASTECIMENTO E MANUTENCAO, parte 3 (auditoria e escrita por RPC).
-- Nova migration; nao edita as anteriores. Idempotente.
--
-- Auditoria (AFTER INSERT, ENABLE ALWAYS, SECURITY DEFINER, search_path vazio), SEM dado pessoal:
--  * abastecimento_registrado (tabela 'abastecimentos'): ator = registrado_por, detalhes =
--    {veiculo_id, requisicao_id?, km, combustivel}.
--  * manutencao_registrada (tabela 'manutencoes'): ator = registrado_por, para = tipo, detalhes =
--    {veiculo_id, tipo, km}. A descricao (texto livre) NUNCA entra; litros e valores tambem nao.
--  * abastecimento_anulado / manutencao_anulada: registro_id = id do registro anulado, ator =
--    anulado_por, para = motivo (enum fechado), detalhes vazios.
--  A auditoria roda na mesma transacao do INSERT: se ela falhar, o registro tambem e desfeito.
--  As acoes novas NAO entram em registrar_auditoria (lista fechada: so acao_administrativa e
--  acesso_negado); saem apenas dos gatilhos. A acao 'revisao_regra_definida' e a tabela
--  'revisao_regras' (migration 28) ja entram nos CHECKs aqui.
--
-- CHECKs ck_auditoria_acao / ck_auditoria_tabela RECRIADOS repetindo TODOS os valores atuais
-- (migration 20: 15 acoes e 6 tabelas) mais os novos (5 acoes e 3 tabelas) = 20 acoes e 9
-- tabelas. Uma DO ao final falha a migration se algum valor antigo ou novo faltar. Toda
-- migration futura que os recriar deve repetir os 20 e os 9.
--
-- Escrita pelo backend (service_role, EXECUTE so para ele), cada RPC em UMA transacao do PostgREST:
--   registrar_abastecimento(p_veiculo_id, p_requisicao_id, p_abastecido_em, p_litros,
--                           p_valor_total, p_km, p_combustivel, p_registrado_por) -> uuid
--   registrar_manutencao(p_veiculo_id, p_tipo, p_realizada_em, p_km, p_valor, p_descricao,
--                        p_registrado_por) -> uuid
--   anular_abastecimento(p_id, p_motivo, p_anulado_por) -> void
--   anular_manutencao(p_id, p_motivo, p_anulado_por) -> void
-- Parametros opcionais sao passados como null (sem DEFAULT: assinatura unica e explicita).
-- O ator vem do JWT validado no backend, nunca do body. Com o recurso DESLIGADO o banco nao
-- recusa (o backend recusa os endpoints), como na quilometragem e no checklist.
--
-- Erros para o backend traduzir: AG002 (ator), AG015 (odometro retrocede), AG016 (requisicao de
-- outro veiculo), 23514 (ck_abastecimentos_*, ck_manutencoes_*), 23502 (campo obrigatorio de
-- manutencao, com diag.column_name), 23503 (veiculo/requisicao/registro inexistente), 23505 com
-- pk_*_anulacoes (registro ja anulado), 22P02 (enum fora da lista), 55P03/40P01 (retry).
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. CHECKs de auditoria (todos os valores antigos + novos)
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
    'abastecimento_registrado', 'abastecimento_anulado',
    'manutencao_registrada', 'manutencao_anulada',
    'revisao_regra_definida'
  ));

alter table public.auditoria drop constraint if exists ck_auditoria_tabela;
alter table public.auditoria
  add constraint ck_auditoria_tabela
  check (tabela in ('requisicoes', 'convites', 'perfis', 'veiculos', 'configuracoes', 'checklists',
                    'abastecimentos', 'manutencoes', 'revisao_regras'));

do $$
declare
  v text;
  d_acao   text;
  d_tabela text;
begin
  select pg_get_constraintdef(oid) into d_acao from pg_constraint
   where conrelid = 'public.auditoria'::regclass and conname = 'ck_auditoria_acao';
  select pg_get_constraintdef(oid) into d_tabela from pg_constraint
   where conrelid = 'public.auditoria'::regclass and conname = 'ck_auditoria_tabela';

  foreach v in array array[
    'criada', 'transicao', 'convite_criado', 'convite_usado', 'convite_revogado',
    'perfil_criado', 'perfil_alterado', 'perfil_unidade_alterada', 'admin_semeado',
    'configuracao_alterada', 'veiculo_criado', 'veiculo_alterado', 'acao_administrativa',
    'acesso_negado', 'checklist_registrado', 'abastecimento_registrado', 'abastecimento_anulado',
    'manutencao_registrada', 'manutencao_anulada', 'revisao_regra_definida'] loop
    if d_acao is null or position('''' || v || '''' in d_acao) = 0 then
      raise exception 'ck_auditoria_acao sem o valor %.', v;
    end if;
  end loop;
  foreach v in array array['requisicoes', 'convites', 'perfis', 'veiculos', 'configuracoes',
                           'checklists', 'abastecimentos', 'manutencoes', 'revisao_regras'] loop
    if d_tabela is null or position('''' || v || '''' in d_tabela) = 0 then
      raise exception 'ck_auditoria_tabela sem o valor %.', v;
    end if;
  end loop;
end
$$;

-- ---------------------------------------------------------------------------
-- 2. Funcoes e gatilhos de auditoria
-- ---------------------------------------------------------------------------
create or replace function public.abastecimentos_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
  values ('abastecimentos', new.id, 'abastecimento_registrado', new.registrado_por, null, null,
          jsonb_strip_nulls(jsonb_build_object(
            'veiculo_id', new.veiculo_id, 'requisicao_id', new.requisicao_id,
            'km', new.km, 'combustivel', new.combustivel)));
  return null;
end;
$$;

create or replace function public.manutencoes_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
  values ('manutencoes', new.id, 'manutencao_registrada', new.registrado_por, null, new.tipo::text,
          jsonb_build_object('veiculo_id', new.veiculo_id, 'tipo', new.tipo, 'km', new.km));
  return null;
end;
$$;

create or replace function public.abastecimentos_anulacoes_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para)
  values ('abastecimentos', new.abastecimento_id, 'abastecimento_anulado', new.anulado_por,
          null, new.motivo::text);
  return null;
end;
$$;

create or replace function public.manutencoes_anulacoes_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para)
  values ('manutencoes', new.manutencao_id, 'manutencao_anulada', new.anulado_por,
          null, new.motivo::text);
  return null;
end;
$$;

revoke all on function public.abastecimentos_auditar()          from public, anon, authenticated, service_role;
revoke all on function public.manutencoes_auditar()             from public, anon, authenticated, service_role;
revoke all on function public.abastecimentos_anulacoes_auditar() from public, anon, authenticated, service_role;
revoke all on function public.manutencoes_anulacoes_auditar()    from public, anon, authenticated, service_role;

drop trigger if exists trg_abastecimentos_auditoria on public.abastecimentos;
create trigger trg_abastecimentos_auditoria
  after insert on public.abastecimentos
  for each row execute function public.abastecimentos_auditar();
alter table public.abastecimentos enable always trigger trg_abastecimentos_auditoria;

drop trigger if exists trg_manutencoes_auditoria on public.manutencoes;
create trigger trg_manutencoes_auditoria
  after insert on public.manutencoes
  for each row execute function public.manutencoes_auditar();
alter table public.manutencoes enable always trigger trg_manutencoes_auditoria;

drop trigger if exists trg_abastecimentos_anulacoes_auditoria on public.abastecimentos_anulacoes;
create trigger trg_abastecimentos_anulacoes_auditoria
  after insert on public.abastecimentos_anulacoes
  for each row execute function public.abastecimentos_anulacoes_auditar();
alter table public.abastecimentos_anulacoes enable always trigger trg_abastecimentos_anulacoes_auditoria;

drop trigger if exists trg_manutencoes_anulacoes_auditoria on public.manutencoes_anulacoes;
create trigger trg_manutencoes_anulacoes_auditoria
  after insert on public.manutencoes_anulacoes
  for each row execute function public.manutencoes_anulacoes_auditar();
alter table public.manutencoes_anulacoes enable always trigger trg_manutencoes_anulacoes_auditoria;

-- ---------------------------------------------------------------------------
-- 3. Escrita pelo backend (RPC)
-- ---------------------------------------------------------------------------
create or replace function public.registrar_abastecimento(
  p_veiculo_id     uuid,
  p_requisicao_id  uuid,
  p_abastecido_em  timestamptz,
  p_litros         numeric,
  p_valor_total    numeric,
  p_km             integer,
  p_combustivel    public.tipo_combustivel,
  p_registrado_por uuid
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_id uuid;
begin
  insert into public.abastecimentos
    (veiculo_id, requisicao_id, abastecido_em, litros, valor_total, km, combustivel, registrado_por)
  values
    (p_veiculo_id, p_requisicao_id, p_abastecido_em, p_litros, p_valor_total, p_km, p_combustivel,
     p_registrado_por)
  returning id into v_id;
  return v_id;
end;
$$;

create or replace function public.registrar_manutencao(
  p_veiculo_id     uuid,
  p_tipo           public.tipo_manutencao,
  p_realizada_em   timestamptz,
  p_km             integer,
  p_valor          numeric,
  p_descricao      text,
  p_registrado_por uuid
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_id uuid;
begin
  insert into public.manutencoes
    (veiculo_id, tipo, realizada_em, km, valor, descricao, registrado_por)
  values
    (p_veiculo_id, p_tipo, p_realizada_em, p_km, p_valor, p_descricao, p_registrado_por)
  returning id into v_id;
  return v_id;
end;
$$;

create or replace function public.anular_abastecimento(
  p_id          uuid,
  p_motivo      public.motivo_anulacao,
  p_anulado_por uuid
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.abastecimentos_anulacoes (abastecimento_id, motivo, anulado_por)
  values (p_id, p_motivo, p_anulado_por);
end;
$$;

create or replace function public.anular_manutencao(
  p_id          uuid,
  p_motivo      public.motivo_anulacao,
  p_anulado_por uuid
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.manutencoes_anulacoes (manutencao_id, motivo, anulado_por)
  values (p_id, p_motivo, p_anulado_por);
end;
$$;

revoke all on function public.registrar_abastecimento(uuid, uuid, timestamptz, numeric, numeric, integer, public.tipo_combustivel, uuid)
  from public, anon, authenticated;
revoke all on function public.registrar_manutencao(uuid, public.tipo_manutencao, timestamptz, integer, numeric, text, uuid)
  from public, anon, authenticated;
revoke all on function public.anular_abastecimento(uuid, public.motivo_anulacao, uuid)
  from public, anon, authenticated;
revoke all on function public.anular_manutencao(uuid, public.motivo_anulacao, uuid)
  from public, anon, authenticated;

grant execute on function public.registrar_abastecimento(uuid, uuid, timestamptz, numeric, numeric, integer, public.tipo_combustivel, uuid)
  to service_role;
grant execute on function public.registrar_manutencao(uuid, public.tipo_manutencao, timestamptz, integer, numeric, text, uuid)
  to service_role;
grant execute on function public.anular_abastecimento(uuid, public.motivo_anulacao, uuid)
  to service_role;
grant execute on function public.anular_manutencao(uuid, public.motivo_anulacao, uuid)
  to service_role;

-- ---------------------------------------------------------------------------
-- 4. Trava: gatilhos de auditoria sem EXECUTE da API; RPCs so para service_role.
-- ---------------------------------------------------------------------------
do $$
declare
  f text;
  r text;
begin
  foreach f in array array['public.abastecimentos_auditar()', 'public.manutencoes_auditar()',
                           'public.abastecimentos_anulacoes_auditar()', 'public.manutencoes_anulacoes_auditar()']
  loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if has_function_privilege(r, f::regprocedure, 'execute') then
        raise exception '% ainda executa %.', r, f;
      end if;
    end loop;
  end loop;

  foreach f in array array[
    'public.registrar_abastecimento(uuid, uuid, timestamptz, numeric, numeric, integer, public.tipo_combustivel, uuid)',
    'public.registrar_manutencao(uuid, public.tipo_manutencao, timestamptz, integer, numeric, text, uuid)',
    'public.anular_abastecimento(uuid, public.motivo_anulacao, uuid)',
    'public.anular_manutencao(uuid, public.motivo_anulacao, uuid)']
  loop
    if has_function_privilege('anon', f::regprocedure, 'execute')
       or has_function_privilege('authenticated', f::regprocedure, 'execute')
       or not has_function_privilege('service_role', f::regprocedure, 'execute') then
      raise exception 'EXECUTE incorreto em %.', f;
    end if;
    if exists (select 1 from pg_proc p,
                      aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
                where p.oid = f::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE') then
      raise exception 'PUBLIC ainda executa %.', f;
    end if;
  end loop;
end
$$;
