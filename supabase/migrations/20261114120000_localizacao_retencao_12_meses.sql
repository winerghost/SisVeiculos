-- Migration: retencao de 12 meses para localizacoes_viagem (expurgo controlado). Nova migration; NAO edita as
-- anteriores. Idempotente. Depende de 20261112120100 (tabela e gatilho de imutabilidade) e 20261113120200
-- (lista vigente de 22 valores de ck_auditoria_acao).
--
-- Motivo (LGPD, minimizacao/necessidade): coordenadas de saida/chegada sao dado pessoal sensivel e ficam
-- guardadas por 12 meses; depois sao apagadas. Base legal a confirmar com a AGU (docs/lgpd-localizacao.md).
--
-- O que muda:
--   1. trg_localizacoes_viagem_imutavel passa a usar public.localizacoes_viagem_proteger() em vez de
--      bloquear_alteracao(): UPDATE continua sempre bloqueado (AG003); DELETE so passa se (a) o marcador
--      transacional `sisveiculos.expurgo_localizacoes` = '1' (posto SO por expurgar_localizacoes_vencidas()
--      com set_config(..., true) e desligado logo apos o DELETE) E (b) a linha tem mais de 12 meses (piso
--      fixo no proprio gatilho, mesmo padrao de acessos_login). TRUNCATE segue bloqueado.
--   2. ck_auditoria_acao ganha 'localizacao_expurgada' (23 valores; os 22 vigentes preservados).
--      ck_auditoria_tabela nao muda (usa 'requisicoes', registro_id nulo).
--   3. public.expurgar_localizacoes_vencidas() -> integer: apaga registrado_em < now() - 12 meses, grava UMA
--      linha em auditoria por execucao (inclusive com 0 apagadas) com detalhes {recurso, apagadas, corte},
--      NUNCA coordenadas, e devolve a contagem. SECURITY DEFINER, search_path vazio, EXECUTE so service_role.
--      registrar_auditoria NAO muda (a funcao insere em auditoria como dono; a acao nao e exposta a API).
-- NAO agenda nada (pg_cron nao e habilitado nas migrations): ver docs/lgpd-localizacao.md.
-- Fuso: timestamptz (UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. Auditoria: nova acao 'localizacao_expurgada' (preserva as 22 existentes)
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
    'localizacao_lida',
    'localizacao_expurgada'
  ));

-- ---------------------------------------------------------------------------
-- 2. Imutabilidade com excecao controlada (DELETE so no expurgo e so > 12 meses)
-- ---------------------------------------------------------------------------
create or replace function public.localizacoes_viagem_proteger()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'DELETE'
     and coalesce(current_setting('sisveiculos.expurgo_localizacoes', true), '') = '1'
     and old.registrado_em < now() - interval '12 months' then
    return old;
  end if;
  raise exception 'Operação % proibida em %: localizações só são removidas pelo expurgo de 12 meses.',
    tg_op, tg_table_name
    using errcode = 'AG003';
end;
$$;

revoke all on function public.localizacoes_viagem_proteger() from public, anon, authenticated, service_role;

drop trigger if exists trg_localizacoes_viagem_imutavel on public.localizacoes_viagem;
create trigger trg_localizacoes_viagem_imutavel
  before update or delete on public.localizacoes_viagem
  for each row execute function public.localizacoes_viagem_proteger();
alter table public.localizacoes_viagem enable always trigger trg_localizacoes_viagem_imutavel;

-- ---------------------------------------------------------------------------
-- 3. Expurgo
-- ---------------------------------------------------------------------------
create or replace function public.expurgar_localizacoes_vencidas()
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_corte timestamptz := now() - interval '12 months';
  v_n     integer;
begin
  -- Variavel LOCAL: vale so nesta transacao e e desligada logo apos o DELETE.
  perform set_config('sisveiculos.expurgo_localizacoes', '1', true);
  delete from public.localizacoes_viagem where registrado_em < v_corte;
  get diagnostics v_n = row_count;
  perform set_config('sisveiculos.expurgo_localizacoes', '', true);

  -- Uma linha por execucao; apenas contagem e corte (nunca coordenadas nem ids de requisicao).
  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
  values ('requisicoes', null, 'localizacao_expurgada', null, null, null,
          jsonb_build_object('recurso', 'motorista_localizacao', 'apagadas', v_n,
                             'corte', to_char(v_corte at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')));
  return v_n;
end;
$$;

revoke all on function public.expurgar_localizacoes_vencidas() from public, anon, authenticated;
grant execute on function public.expurgar_localizacoes_vencidas() to service_role;

comment on function public.expurgar_localizacoes_vencidas() is
  'Apaga localizacoes_viagem com mais de 12 meses (LGPD), audita contagem e corte e devolve a contagem. Nao agendada: chamar via service_role/pipeline.';

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
    'localizacao_lida', 'localizacao_expurgada'] loop
    if d is null or position('''' || v || '''' in d) = 0 then
      raise exception 'ck_auditoria_acao sem o valor %.', v;
    end if;
  end loop;

  -- Gatilho de imutabilidade: ENABLE ALWAYS e apontando para a funcao nova.
  if not exists (select 1 from pg_trigger t
                  where t.tgrelid = 'public.localizacoes_viagem'::regclass
                    and t.tgname = 'trg_localizacoes_viagem_imutavel'
                    and t.tgenabled = 'A' and not t.tgisinternal
                    and t.tgfoid = to_regprocedure('public.localizacoes_viagem_proteger()')) then
    raise exception 'trg_localizacoes_viagem_imutavel ausente, nao ALWAYS ou com funcao errada.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.localizacoes_viagem'::regclass
                    and tgname = 'trg_localizacoes_viagem_sem_truncate' and tgenabled = 'A') then
    raise exception 'trg_localizacoes_viagem_sem_truncate ausente ou nao ALWAYS.';
  end if;

  v_oid := to_regprocedure('public.localizacoes_viagem_proteger()');
  if v_oid is null
     or has_function_privilege('anon', v_oid, 'execute')
     or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'localizacoes_viagem_proteger() ausente ou executavel pela API.';
  end if;

  v_oid := to_regprocedure('public.expurgar_localizacoes_vencidas()');
  if v_oid is null
     or not (select prosecdef from pg_proc where oid = v_oid)
     or not coalesce((select position('search_path=' in array_to_string(proconfig, ',')) > 0
                        from pg_proc where oid = v_oid), false)
     or has_function_privilege('anon', v_oid, 'execute')
     or has_function_privilege('authenticated', v_oid, 'execute')
     or not has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'expurgar_localizacoes_vencidas() ausente, sem SECURITY DEFINER/search_path vazio ou com grants errados.';
  end if;
  if exists (select 1 from information_schema.routine_privileges
              where routine_schema = 'public' and routine_name = 'expurgar_localizacoes_vencidas'
                and grantee = 'PUBLIC') then
    raise exception 'expurgar_localizacoes_vencidas() executavel por PUBLIC.';
  end if;

  -- service_role continua sem DELETE direto na tabela.
  if has_table_privilege('service_role', 'public.localizacoes_viagem', 'insert, update, delete, truncate') then
    raise exception 'service_role nao pode ter escrita direta em localizacoes_viagem.';
  end if;
end
$$;

-- Recarrega o cache do PostgREST para expor a nova RPC.
notify pgrst, 'reload schema';
