-- Migration: correcoes da revisao do recurso Motorista. Nova migration; nao edita as anteriores. Idempotente.
-- Depende de 20261112120100_localizacoes_viagem.sql e 20261113120000_notificacoes_motorista.sql.
--
-- Motivo (1): o admin passa a consultar as coordenadas de uma viagem (via backend). Essa leitura de dado
-- sensivel (LGPD) precisa deixar rastro: nova acao de auditoria 'localizacao_lida'. O CHECK
-- ck_auditoria_acao e recriado partindo da lista vigente mais recente (21 valores, definida em
-- 20261112120100; 20261112120200, 20261112120300, 20261113120000 e 20261113120100 nao o alteraram)
-- mais 'localizacao_lida' (22 valores). ck_auditoria_tabela nao muda. O detalhes dessa acao NUNCA deve
-- conter latitude, longitude nem precisao (so ids/momento); esta migration nao cria gatilho: a linha e
-- gravada pelo backend/RPC ja existente de auditoria.
--
-- Motivo (2): notificacoes_gerar() (gatilho SECURITY DEFINER; migrations 20261110120000 e 20261113120000)
-- revogava EXECUTE so de public/anon/authenticated, deixando service_role com o privilegio. As demais
-- funcoes de gatilho revogam tambem de service_role. Gatilho nao precisa de EXECUTE do chamador.
-- Fuso: timestamptz (UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. Auditoria: nova acao 'localizacao_lida' (preserva as 21 existentes)
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
    'localizacao_lida'
  ));

-- ---------------------------------------------------------------------------
-- 2. notificacoes_gerar(): revoke tambem de service_role
-- ---------------------------------------------------------------------------
revoke all on function public.notificacoes_gerar() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v     text;
  d     text;
  v_oid oid;
begin
  select pg_get_constraintdef(oid) into d from pg_constraint
   where conrelid = 'public.auditoria'::regclass and conname = 'ck_auditoria_acao';
  foreach v in array array[
    'criada', 'transicao', 'convite_criado', 'convite_usado', 'convite_revogado',
    'perfil_criado', 'perfil_alterado', 'perfil_unidade_alterada', 'admin_semeado',
    'configuracao_alterada', 'veiculo_criado', 'veiculo_alterado', 'acao_administrativa',
    'acesso_negado', 'checklist_registrado', 'foto_perfil_alterada', 'foto_perfil_removida',
    'relatorio_gerado', 'relatorio_enviado', 'motorista_designado', 'localizacao_registrada',
    'localizacao_lida'] loop
    if d is null or position('''' || v || '''' in d) = 0 then
      raise exception 'ck_auditoria_acao sem o valor %.', v;
    end if;
  end loop;

  v_oid := to_regprocedure('public.notificacoes_gerar()');
  if v_oid is null
     or has_function_privilege('anon', v_oid, 'execute')
     or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'notificacoes_gerar() ausente ou executavel pela API.';
  end if;
end
$$;
