-- Migration: recurso "Dados" (Configuracoes do admin): exportar e importar.
-- Nova migration; NAO edita nenhuma anterior. Depende de 20261122120000 (lista vigente de 30 valores de
-- ck_auditoria_acao), 20261107120500/11 (privado.exigir_admin_ativo) e das tabelas veiculos, requisicoes,
-- checklists, localizacoes_viagem e configuracoes. NAO aplicar pelo servidor: supabase db push e do pipeline.
--
-- O que entra (decisoes fechadas): veiculos, requisicoes (km e motorista_id sao colunas dela), localizacoes_viagem,
-- checklists SEM fotos e configuracoes. NUNCA perfis/usuarios/senhas, auditoria, notificacoes nem fotos.
-- Formato do arquivo (montado pelo backend; o banco so trata `tabelas`):
--   {"versao": 1, "gerado_em": ..., "sha256": ..., "tabelas": {
--      "veiculos": [...], "requisicoes": [...], "checklists": [...], "localizacoes_viagem": [...],
--      "configuracoes": {...} (objeto ou lista de 1 objeto)}}
-- A lista de colunas de cada tabela vive em privado.dados_colunas() (fonte unica de exportar e importar).
--
-- 1. ck_auditoria_acao ganha 'dados_exportados' e 'dados_importados' (30 vigentes preservados -> 32).
--    ck_auditoria_tabela NAO muda: tabela = 'configuracoes', registro_id nulo, como configuracao_alterada.
--    detalhes SO com contagens por tabela (inteiros) e versao: nunca dado pessoal.
-- 2. public.exportar_dados(p_admin) -> jsonb {"tabelas": {...}, "contagens": {...}}: um unico SELECT (um unico
--    snapshot consistente, sem requisicao orfa de checklist) e a auditoria 'dados_exportados' na MESMA transacao.
--    O backend so embrulha com versao/gerado_em/sha256 e entrega o arquivo.
-- 3. public.importar_dados(p_payload, p_admin, p_confirmar) -> jsonb. MESCLAR, nunca apaga, transacao unica:
--      p_confirmar = false (previa): executa TUDO e desfaz (subtransacao com rollback): previa == confirmar.
--      p_confirmar = true: grava SO se nao houver nenhum erro de linha; com erro, nada e gravado.
--    Retorno: {"resumo": {<tabela>: {novos, existentes, conflitos}}, "erros": [{tabela, indice, mensagem}] (ate
--    200), "erros_total", "aplicado": bool, "politica_conflito": {...}}. `acoes_permitidas` e do backend
--    (confirmar = previa sem erros).
--    Idempotencia/chaves: veiculos por id, senao por placa (veiculo do arquivo com placa ja existente vira o
--    existente e as requisicoes do arquivo sao REMAPEADAS para ele); requisicoes por id; checklists e
--    localizacoes por (requisicao_id, momento) ou id; configuracoes e linha unica.
--    "existente" = igual; "conflito" = existe e difere. Politica: veiculos e configuracoes (mutaveis) -> o
--    arquivo ATUALIZA (descricao/placa/ativo e as flags); requisicoes, checklists e localizacoes (historico
--    imutavel) -> o conflito e IGNORADO (a linha atual prevalece, nada e alterado). O admin ve a contagem na previa.
--    Erro de linha (nada grava): FK inexistente (usuario/veiculo/requisicao), CHECKs, unicidade e veiculo
--    ocupado (ux_requisicoes_veiculo_aberta: outra requisicao aberta no mesmo veiculo, no arquivo ou no banco).
--    Mensagens de erro nunca carregam valores (sem dado pessoal): so texto fixo e nome de constraint/coluna.
--    Flags NAO exportadas/importadas (seguranca/infra externa): mfa, passkeys, notificacao_push, relatorio_email,
--    relatorio_whatsapp. Importar nunca liga MFA, Passkeys, Push nem envios externos.
--    Importar requisicoes e restauracao de historico: os gatilhos de maquina de estados (validar, km, checklist,
--    motorista, localizacao) e de auditoria/notificacao por linha sao DESLIGADOS so dentro da transacao (ALTER TABLE
--    ... DISABLE TRIGGER exige SHARE ROW EXCLUSIVE: escritores concorrentes esperam; o DDL e transacional, entao
--    rollback/erro restaura sozinho e o fim normal restaura explicitamente o estado original: O/A/R). Gatilhos que
--    chamam bloquear_alteracao (sem DELETE/UPDATE) ficam ligados. CHECKs, FKs e indices unicos continuam valendo.
--    Veiculos e configuracoes usam os gatilhos normais (ator = p_admin => auditoria veiculo_criado/alterado e
--    configuracao_alterada com o responsavel).
--    Limites por chamada: veiculos 500, requisicoes 20000, checklists 40000, localizacoes_viagem 40000.
--    Serializa importacoes por advisory xact lock. lock_timeout do service_role (5s) -> 55P03 se a tabela
--    estiver ocupada (o backend traduz em 503/tente novamente).
--
-- Erros: AG002 (nao admin ativo), AG060 (arquivo/estrutura invalida ou limite excedido), AG061 (linha invalida).
-- Seguranca: SECURITY DEFINER, search_path vazio, EXECUTE so service_role; RLS e grants das tabelas intactos.
-- Fuso: timestamptz (UTC; exibicao em America/Sao_Paulo).

set local lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 1. Auditoria: novas acoes (preserva as 30 existentes)
-- ---------------------------------------------------------------------------
do $$
declare
  v_def text;
  v     text;
begin
  select pg_get_constraintdef(oid) into v_def from pg_constraint
   where conrelid = 'public.auditoria'::regclass and conname = 'ck_auditoria_acao';
  foreach v in array array[
    'criada', 'transicao', 'convite_criado', 'convite_usado', 'convite_revogado',
    'perfil_criado', 'perfil_alterado', 'perfil_unidade_alterada', 'admin_semeado',
    'configuracao_alterada', 'veiculo_criado', 'veiculo_alterado', 'acao_administrativa', 'acesso_negado',
    'checklist_registrado', 'foto_perfil_alterada', 'foto_perfil_removida', 'relatorio_gerado',
    'relatorio_enviado', 'motorista_designado', 'localizacao_registrada', 'localizacao_lida',
    'localizacao_expurgada', 'saudacao_excluida', 'passkey_cadastrada', 'passkey_revogada',
    'passkey_autenticada', 'passkey_falha', 'passkey_regressao_contador', 'passkey_desafio_invalido'
  ] loop
    if v_def is null or position('''' || v || '''' in v_def) = 0 then
      raise exception 'ck_auditoria_acao vigente sem o valor % (lista inesperada; revisar antes de recriar).', v;
    end if;
  end loop;
end
$$;

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
    'localizacao_expurgada',
    'saudacao_excluida',
    'passkey_cadastrada', 'passkey_revogada', 'passkey_autenticada',
    'passkey_falha', 'passkey_regressao_contador', 'passkey_desafio_invalido',
    'dados_exportados', 'dados_importados'
  ));

-- ---------------------------------------------------------------------------
-- 2. Helpers privados (sem EXECUTE para ninguem da API)
-- ---------------------------------------------------------------------------
-- Fonte unica das colunas que trafegam no arquivo. Fora da lista (de proposito): transporte (alterado_por,
-- motorista_alterado_por), mfa/passkeys/notificacao_push/relatorio_* e id/atualizado_em de configuracoes.
create or replace function privado.dados_colunas(p_tabela text)
returns text[]
language sql
immutable
set search_path = ''
as $$
  select case p_tabela
    when 'veiculos' then array['id', 'descricao', 'placa', 'ativo', 'codigo_publico', 'criado_em', 'atualizado_em']
    when 'requisicoes' then array[
      'id', 'usuario_id', 'nome_solicitante', 'unidade', 'hora_saida', 'trajeto', 'observacao', 'veiculo_id',
      'status', 'autorizado_por', 'autorizado_em', 'saida_real_em', 'chegada_real_em', 'saida_registrada_por',
      'chegada_registrada_por', 'cancelada_por', 'cancelada_em', 'km_saida', 'km_chegada', 'motorista_id',
      'criado_em', 'atualizado_em']
    when 'checklists' then array[
      'id', 'requisicao_id', 'momento', 'combustivel', 'avarias', 'descricao_avarias', 'registrado_por',
      'registrado_em']
    when 'localizacoes_viagem' then array[
      'id', 'requisicao_id', 'momento', 'latitude', 'longitude', 'precisao_m', 'registrado_por', 'registrado_em']
    when 'configuracoes' then array[
      'quilometragem', 'abastecimento_manutencao', 'checklist', 'notificacao_email_criticos', 'motorista',
      'motorista_registra', 'motorista_localizacao', 'vigilante_km', 'autorizador_decide_propria',
      'autorizador_km', 'indicador_conectividade', 'feedback_tatil', 'leitor_qr_veiculos', 'voz_campos']
  end;
$$;

revoke all on function privado.dados_colunas(text) from public, anon, authenticated, service_role;

create or replace function privado.dados_filtrar(p_obj jsonb, p_chaves text[])
returns jsonb
language sql
immutable
set search_path = ''
as $$
  select coalesce(jsonb_object_agg(e.key, e.value), '{}'::jsonb)
    from jsonb_each(p_obj) as e
   where e.key = any (p_chaves);
$$;

revoke all on function privado.dados_filtrar(jsonb, text[]) from public, anon, authenticated, service_role;

create or replace function privado.dados_inc(p_resumo jsonb, p_tabela text, p_campo text)
returns jsonb
language sql
immutable
set search_path = ''
as $$
  select jsonb_set(p_resumo, array[p_tabela, p_campo],
                   to_jsonb(coalesce((p_resumo #>> array[p_tabela, p_campo])::integer, 0) + 1));
$$;

revoke all on function privado.dados_inc(jsonb, text, text) from public, anon, authenticated, service_role;

-- Mensagem de erro de linha SEM valores: texto fixo + nome de constraint/coluna (schema, nao dado).
create or replace function privado.dados_mensagem_erro(p_estado text, p_constraint text, p_coluna text, p_msg text)
returns text
language sql
immutable
set search_path = ''
as $$
  select left(case
    when p_constraint = 'ux_requisicoes_veiculo_aberta' then
      'Veículo já possui outra requisição aberta (solicitada, autorizada ou em uso).'
    when p_constraint = 'ux_veiculos_placa' then 'Placa já pertence a outro veículo.'
    when p_constraint = 'ux_veiculos_codigo_publico' then 'Código público já pertence a outro veículo.'
    when p_estado = '23505' then 'Registro duplicado (' || coalesce(p_constraint, 'unicidade') || ').'
    when p_estado = '23503' then
      'Referência inexistente (' || coalesce(p_constraint, 'chave estrangeira') || '): usuário, veículo ou requisição não encontrado.'
    when p_estado = '23502' then 'Campo obrigatório ausente: ' || coalesce(p_coluna, '?') || '.'
    when p_estado = '23514' then 'Valor viola a regra ' || coalesce(p_constraint, '?') || '.'
    when p_estado like 'AG%' then coalesce(p_msg, 'Linha inválida.')
    when p_estado like '22%' then 'Valor com formato inválido.'
    else 'Linha inválida.'
  end, 300);
$$;

revoke all on function privado.dados_mensagem_erro(text, text, text, text)
  from public, anon, authenticated, service_role;

-- Desliga os gatilhos (exceto os que chamam bloquear_alteracao) de requisicoes/checklists/localizacoes_viagem e
-- devolve o estado original para restaurar. DDL transacional: erro/rollback desfaz sozinho.
create or replace function privado.dados_desligar_gatilhos()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  r       record;
  v_saida jsonb := '[]'::jsonb;
begin
  for r in
    select c.relname as tabela, t.tgname as gatilho, t.tgenabled::text as estado
      from pg_trigger t
      join pg_class c on c.oid = t.tgrelid
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public'
       and c.relname in ('requisicoes', 'checklists', 'localizacoes_viagem')
       and not t.tgisinternal
       and t.tgenabled <> 'D'
       and t.tgfoid <> 'public.bloquear_alteracao()'::regprocedure
     order by c.relname, t.tgname
  loop
    execute format('alter table public.%I disable trigger %I', r.tabela, r.gatilho);
    v_saida := v_saida || jsonb_build_object('tabela', r.tabela, 'gatilho', r.gatilho, 'estado', r.estado);
  end loop;
  return v_saida;
end;
$$;

revoke all on function privado.dados_desligar_gatilhos() from public, anon, authenticated, service_role;

create or replace function privado.dados_religar_gatilhos(p_estado jsonb)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  r record;
begin
  for r in select e ->> 'tabela' as tabela, e ->> 'gatilho' as gatilho, e ->> 'estado' as estado
             from jsonb_array_elements(p_estado) as e
  loop
    execute format('alter table public.%I %s trigger %I', r.tabela,
                   case r.estado when 'A' then 'enable always' when 'R' then 'enable replica' else 'enable' end,
                   r.gatilho);
  end loop;
end;
$$;

revoke all on function privado.dados_religar_gatilhos(jsonb) from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 3. exportar_dados
-- ---------------------------------------------------------------------------
create or replace function public.exportar_dados(p_admin uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_tabelas   jsonb;
  v_contagens jsonb;
begin
  perform privado.exigir_admin_ativo(p_admin);

  -- Um unico SELECT = um unico snapshot (consistente entre as tabelas).
  select jsonb_build_object(
           'veiculos', coalesce((select jsonb_agg(privado.dados_filtrar(to_jsonb(v), privado.dados_colunas('veiculos'))
                                                  order by v.criado_em, v.id) from public.veiculos v), '[]'::jsonb),
           'requisicoes', coalesce((select jsonb_agg(privado.dados_filtrar(to_jsonb(r), privado.dados_colunas('requisicoes'))
                                                     order by r.criado_em, r.id) from public.requisicoes r), '[]'::jsonb),
           'checklists', coalesce((select jsonb_agg(privado.dados_filtrar(to_jsonb(c), privado.dados_colunas('checklists'))
                                                    order by c.registrado_em, c.id) from public.checklists c), '[]'::jsonb),
           'localizacoes_viagem', coalesce((select jsonb_agg(privado.dados_filtrar(to_jsonb(l), privado.dados_colunas('localizacoes_viagem'))
                                                             order by l.registrado_em, l.id) from public.localizacoes_viagem l), '[]'::jsonb),
           'configuracoes', coalesce((select privado.dados_filtrar(to_jsonb(g), privado.dados_colunas('configuracoes'))
                                        from public.configuracoes g where g.id), '{}'::jsonb)
         )
    into v_tabelas;

  v_contagens := jsonb_build_object(
    'veiculos', jsonb_array_length(v_tabelas -> 'veiculos'),
    'requisicoes', jsonb_array_length(v_tabelas -> 'requisicoes'),
    'checklists', jsonb_array_length(v_tabelas -> 'checklists'),
    'localizacoes_viagem', jsonb_array_length(v_tabelas -> 'localizacoes_viagem'),
    'configuracoes', 1);

  insert into public.auditoria (tabela, registro_id, acao, ator_id, detalhes)
  values ('configuracoes', null, 'dados_exportados', p_admin,
          jsonb_build_object('versao', 1, 'contagens', v_contagens));

  return jsonb_build_object('tabelas', v_tabelas, 'contagens', v_contagens);
end;
$$;

revoke all on function public.exportar_dados(uuid) from public, anon, authenticated;
grant execute on function public.exportar_dados(uuid) to service_role;

comment on function public.exportar_dados(uuid) is
  'Exporta veiculos, requisicoes, checklists (sem fotos), localizacoes e flags de configuracoes num unico snapshot e audita dados_exportados (so contagens). So admin ativo; EXECUTE so service_role.';

-- ---------------------------------------------------------------------------
-- 4. importar_dados
-- ---------------------------------------------------------------------------
create or replace function public.importar_dados(p_payload jsonb, p_admin uuid, p_confirmar boolean)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  c_lim_veiculos  constant integer := 500;
  c_lim_requisic  constant integer := 20000;
  c_lim_checklist constant integer := 40000;
  c_lim_locais    constant integer := 40000;
  c_max_erros     constant integer := 200;
  c_ign_req       constant text[] := array['criado_em', 'atualizado_em'];

  v_tabelas    jsonb;
  v_chave      text;
  v_veic       jsonb;
  v_req        jsonb;
  v_chk        jsonb;
  v_loc        jsonb;
  v_cfg        jsonb;
  v_resumo     jsonb := jsonb_build_object(
                  'veiculos', '{"novos":0,"existentes":0,"conflitos":0}'::jsonb,
                  'requisicoes', '{"novos":0,"existentes":0,"conflitos":0}'::jsonb,
                  'checklists', '{"novos":0,"existentes":0,"conflitos":0}'::jsonb,
                  'localizacoes_viagem', '{"novos":0,"existentes":0,"conflitos":0}'::jsonb,
                  'configuracoes', '{"novos":0,"existentes":0,"conflitos":0}'::jsonb);
  v_erros      jsonb := '[]'::jsonb;
  v_erros_n    integer := 0;
  v_aplicado   boolean := false;
  v_gatilhos   jsonb;
  v_mapa_veic  jsonb := '{}'::jsonb;   -- id do arquivo -> id local (veiculo casado por placa)

  v_i          integer;
  v_row        jsonb;
  v_f          jsonb;
  v_id_txt     text;
  v_estado     text;
  v_constr     text;
  v_coluna     text;
  v_msg        text;
  v_tab_atual  text;

  v_vrec       public.veiculos;
  v_vatual     public.veiculos;
  v_vachou     boolean;
  v_rrec       public.requisicoes;
  v_atual      jsonb;
  v_crec       public.checklists;
  v_lrec       public.localizacoes_viagem;
  v_cfg_atual  public.configuracoes;
  v_cfg_novo   public.configuracoes;
  v_chv        text;
begin
  perform privado.exigir_admin_ativo(p_admin);

  if p_confirmar is null then
    raise exception 'Informe se é prévia ou confirmação.' using errcode = 'AG060';
  end if;
  if p_payload is null or jsonb_typeof(p_payload) <> 'object'
     or jsonb_typeof(p_payload -> 'tabelas') is distinct from 'object' then
    raise exception 'Arquivo de dados inválido: objeto "tabelas" ausente.' using errcode = 'AG060';
  end if;
  if (p_payload -> 'versao') is distinct from to_jsonb(1) then
    raise exception 'Versão do arquivo de dados não suportada.' using errcode = 'AG060';
  end if;

  v_tabelas := p_payload -> 'tabelas';
  for v_chave in select k from jsonb_object_keys(v_tabelas) as k loop
    if v_chave <> all (array['veiculos', 'requisicoes', 'checklists', 'localizacoes_viagem', 'configuracoes']) then
      raise exception 'Arquivo de dados inválido: tabela não reconhecida.' using errcode = 'AG060';
    end if;
  end loop;

  v_veic := coalesce(v_tabelas -> 'veiculos', '[]'::jsonb);
  v_req  := coalesce(v_tabelas -> 'requisicoes', '[]'::jsonb);
  v_chk  := coalesce(v_tabelas -> 'checklists', '[]'::jsonb);
  v_loc  := coalesce(v_tabelas -> 'localizacoes_viagem', '[]'::jsonb);
  v_cfg  := v_tabelas -> 'configuracoes';

  if jsonb_typeof(v_veic) <> 'array' or jsonb_typeof(v_req) <> 'array'
     or jsonb_typeof(v_chk) <> 'array' or jsonb_typeof(v_loc) <> 'array' then
    raise exception 'Arquivo de dados inválido: cada tabela deve ser uma lista.' using errcode = 'AG060';
  end if;
  if v_cfg is not null then
    if jsonb_typeof(v_cfg) = 'array' then
      if jsonb_array_length(v_cfg) > 1 then
        raise exception 'Arquivo de dados inválido: configuracoes aceita uma única linha.' using errcode = 'AG060';
      end if;
      v_cfg := v_cfg -> 0;
    end if;
    if v_cfg is not null and jsonb_typeof(v_cfg) not in ('object', 'null') then
      raise exception 'Arquivo de dados inválido: configuracoes deve ser um objeto.' using errcode = 'AG060';
    end if;
    if jsonb_typeof(v_cfg) = 'null' then
      v_cfg := null;
    end if;
  end if;

  if jsonb_array_length(v_veic) > c_lim_veiculos or jsonb_array_length(v_req) > c_lim_requisic
     or jsonb_array_length(v_chk) > c_lim_checklist or jsonb_array_length(v_loc) > c_lim_locais then
    raise exception 'Arquivo de dados excede o limite de linhas por importação.' using errcode = 'AG060';
  end if;

  -- Uma importacao por vez (e coerente com as travas de admin ja tomadas por exigir_admin_ativo).
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.importar_dados', 0));

  begin  -- subtransacao: previa e importacao com erro terminam em rollback (AG099)
    lock table public.requisicoes, public.checklists, public.localizacoes_viagem in share row exclusive mode;
    v_gatilhos := privado.dados_desligar_gatilhos();

    -- ===================== veiculos =====================
    v_tab_atual := 'veiculos';
    for v_i, v_row in
      select (o.ord - 1)::integer, o.val from jsonb_array_elements(v_veic) with ordinality as o(val, ord)
    loop
      begin
        if jsonb_typeof(v_row) <> 'object' then
          raise exception 'A linha deve ser um objeto.' using errcode = 'AG061';
        end if;
        v_vrec := jsonb_populate_record(null::public.veiculos,
                    privado.dados_filtrar(v_row, privado.dados_colunas('veiculos')));
        if v_vrec.id is null then
          raise exception 'Campo obrigatório ausente: id.' using errcode = 'AG061';
        end if;

        select * into v_vatual from public.veiculos where id = v_vrec.id;
        v_vachou := found;
        if not v_vachou and v_vrec.placa is not null then
          select * into v_vatual from public.veiculos where placa = v_vrec.placa;
          v_vachou := found;
          if v_vachou then
            v_mapa_veic := v_mapa_veic || jsonb_build_object(v_vrec.id::text, v_vatual.id::text);
          end if;
        end if;

        if v_vachou then
          if v_vatual.descricao is not distinct from coalesce(v_vrec.descricao, v_vatual.descricao)
             and v_vatual.placa is not distinct from coalesce(v_vrec.placa, v_vatual.placa)
             and v_vatual.ativo is not distinct from coalesce(v_vrec.ativo, v_vatual.ativo) then
            v_resumo := privado.dados_inc(v_resumo, 'veiculos', 'existentes');
          else
            update public.veiculos
               set descricao = coalesce(v_vrec.descricao, descricao),
                   placa = coalesce(v_vrec.placa, placa),
                   ativo = coalesce(v_vrec.ativo, ativo),
                   alterado_por = p_admin
             where id = v_vatual.id;
            v_resumo := privado.dados_inc(v_resumo, 'veiculos', 'conflitos');
          end if;
        else
          insert into public.veiculos (id, descricao, placa, ativo, codigo_publico, criado_em, alterado_por)
          values (v_vrec.id, v_vrec.descricao, v_vrec.placa, coalesce(v_vrec.ativo, true),
                  coalesce(v_vrec.codigo_publico, replace(gen_random_uuid()::text, '-', '')),
                  coalesce(v_vrec.criado_em, now()), p_admin);
          v_resumo := privado.dados_inc(v_resumo, 'veiculos', 'novos');
        end if;
      exception when others then
        get stacked diagnostics v_estado = returned_sqlstate, v_constr = constraint_name,
                                v_coluna = column_name, v_msg = message_text;
        if v_estado like '40%' or v_estado like '53%' or v_estado like '55%' or v_estado like '57%' then
          raise;
        end if;
        v_erros_n := v_erros_n + 1;
        if v_erros_n <= c_max_erros then
          v_erros := v_erros || jsonb_build_object('tabela', v_tab_atual, 'indice', v_i,
                       'mensagem', privado.dados_mensagem_erro(v_estado, v_constr, v_coluna, v_msg));
        end if;
      end;
    end loop;

    -- ===================== requisicoes =====================
    v_tab_atual := 'requisicoes';
    for v_i, v_row in
      select (o.ord - 1)::integer, o.val from jsonb_array_elements(v_req) with ordinality as o(val, ord)
    loop
      begin
        if jsonb_typeof(v_row) <> 'object' then
          raise exception 'A linha deve ser um objeto.' using errcode = 'AG061';
        end if;
        v_f := privado.dados_filtrar(v_row, privado.dados_colunas('requisicoes'));
        v_id_txt := v_f ->> 'veiculo_id';
        if v_id_txt is not null and v_mapa_veic ? v_id_txt then
          v_f := jsonb_set(v_f, array['veiculo_id'], v_mapa_veic -> v_id_txt);
        end if;
        v_rrec := jsonb_populate_record(null::public.requisicoes, v_f);
        if v_rrec.id is null then
          raise exception 'Campo obrigatório ausente: id.' using errcode = 'AG061';
        end if;

        select to_jsonb(r) into v_atual from public.requisicoes r where r.id = v_rrec.id;
        if found then
          if (to_jsonb(v_rrec) - c_ign_req - 'motorista_alterado_por')
             = (v_atual - c_ign_req - 'motorista_alterado_por') then
            v_resumo := privado.dados_inc(v_resumo, 'requisicoes', 'existentes');
          else
            v_resumo := privado.dados_inc(v_resumo, 'requisicoes', 'conflitos');  -- ignorado: historico imutavel
          end if;
        else
          v_rrec.criado_em := coalesce(v_rrec.criado_em, now());
          v_rrec.atualizado_em := coalesce(v_rrec.atualizado_em, v_rrec.criado_em);
          v_rrec.motorista_alterado_por := null;
          insert into public.requisicoes select (v_rrec).*;
          v_resumo := privado.dados_inc(v_resumo, 'requisicoes', 'novos');
        end if;
      exception when others then
        get stacked diagnostics v_estado = returned_sqlstate, v_constr = constraint_name,
                                v_coluna = column_name, v_msg = message_text;
        if v_estado like '40%' or v_estado like '53%' or v_estado like '55%' or v_estado like '57%' then
          raise;
        end if;
        v_erros_n := v_erros_n + 1;
        if v_erros_n <= c_max_erros then
          v_erros := v_erros || jsonb_build_object('tabela', v_tab_atual, 'indice', v_i,
                       'mensagem', privado.dados_mensagem_erro(v_estado, v_constr, v_coluna, v_msg));
        end if;
      end;
    end loop;

    -- ===================== checklists =====================
    v_tab_atual := 'checklists';
    for v_i, v_row in
      select (o.ord - 1)::integer, o.val from jsonb_array_elements(v_chk) with ordinality as o(val, ord)
    loop
      begin
        if jsonb_typeof(v_row) <> 'object' then
          raise exception 'A linha deve ser um objeto.' using errcode = 'AG061';
        end if;
        v_crec := jsonb_populate_record(null::public.checklists,
                    privado.dados_filtrar(v_row, privado.dados_colunas('checklists')));

        select to_jsonb(c) into v_atual from public.checklists c
         where (v_crec.id is not null and c.id = v_crec.id)
            or (c.requisicao_id = v_crec.requisicao_id and c.momento = v_crec.momento)
         limit 1;
        if found then
          if (to_jsonb(v_crec) - 'id' - 'registrado_em') = (v_atual - 'id' - 'registrado_em') then
            v_resumo := privado.dados_inc(v_resumo, 'checklists', 'existentes');
          else
            v_resumo := privado.dados_inc(v_resumo, 'checklists', 'conflitos');  -- ignorado: imutavel
          end if;
        else
          v_crec.id := coalesce(v_crec.id, gen_random_uuid());
          v_crec.registrado_em := coalesce(v_crec.registrado_em, now());
          insert into public.checklists select (v_crec).*;
          v_resumo := privado.dados_inc(v_resumo, 'checklists', 'novos');
        end if;
      exception when others then
        get stacked diagnostics v_estado = returned_sqlstate, v_constr = constraint_name,
                                v_coluna = column_name, v_msg = message_text;
        if v_estado like '40%' or v_estado like '53%' or v_estado like '55%' or v_estado like '57%' then
          raise;
        end if;
        v_erros_n := v_erros_n + 1;
        if v_erros_n <= c_max_erros then
          v_erros := v_erros || jsonb_build_object('tabela', v_tab_atual, 'indice', v_i,
                       'mensagem', privado.dados_mensagem_erro(v_estado, v_constr, v_coluna, v_msg));
        end if;
      end;
    end loop;

    -- ===================== localizacoes_viagem =====================
    v_tab_atual := 'localizacoes_viagem';
    for v_i, v_row in
      select (o.ord - 1)::integer, o.val from jsonb_array_elements(v_loc) with ordinality as o(val, ord)
    loop
      begin
        if jsonb_typeof(v_row) <> 'object' then
          raise exception 'A linha deve ser um objeto.' using errcode = 'AG061';
        end if;
        v_lrec := jsonb_populate_record(null::public.localizacoes_viagem,
                    privado.dados_filtrar(v_row, privado.dados_colunas('localizacoes_viagem')));

        select to_jsonb(l) into v_atual from public.localizacoes_viagem l
         where (v_lrec.id is not null and l.id = v_lrec.id)
            or (l.requisicao_id = v_lrec.requisicao_id and l.momento = v_lrec.momento)
         limit 1;
        if found then
          if (to_jsonb(v_lrec) - 'id' - 'registrado_em') = (v_atual - 'id' - 'registrado_em') then
            v_resumo := privado.dados_inc(v_resumo, 'localizacoes_viagem', 'existentes');
          else
            v_resumo := privado.dados_inc(v_resumo, 'localizacoes_viagem', 'conflitos');  -- ignorado: imutavel
          end if;
        else
          v_lrec.id := coalesce(v_lrec.id, gen_random_uuid());
          v_lrec.registrado_em := coalesce(v_lrec.registrado_em, now());
          insert into public.localizacoes_viagem select (v_lrec).*;
          v_resumo := privado.dados_inc(v_resumo, 'localizacoes_viagem', 'novos');
        end if;
      exception when others then
        get stacked diagnostics v_estado = returned_sqlstate, v_constr = constraint_name,
                                v_coluna = column_name, v_msg = message_text;
        if v_estado like '40%' or v_estado like '53%' or v_estado like '55%' or v_estado like '57%' then
          raise;
        end if;
        v_erros_n := v_erros_n + 1;
        if v_erros_n <= c_max_erros then
          v_erros := v_erros || jsonb_build_object('tabela', v_tab_atual, 'indice', v_i,
                       'mensagem', privado.dados_mensagem_erro(v_estado, v_constr, v_coluna, v_msg));
        end if;
      end;
    end loop;

    -- Gatilhos de volta ao estado original (O/A/R) antes de mexer em configuracoes.
    perform privado.dados_religar_gatilhos(v_gatilhos);

    -- ===================== configuracoes (so flags da lista; mfa/passkeys/push nunca) =====================
    v_tab_atual := 'configuracoes';
    if v_cfg is not null then
      begin
        v_f := privado.dados_filtrar(v_cfg, privado.dados_colunas('configuracoes'));
        for v_chv in select k from jsonb_object_keys(v_f) as k loop
          if jsonb_typeof(v_f -> v_chv) <> 'boolean' then
            raise exception 'O valor de % deve ser verdadeiro ou falso.', v_chv using errcode = 'AG061';
          end if;
        end loop;

        select * into v_cfg_atual from public.configuracoes where id;
        v_cfg_novo := jsonb_populate_record(v_cfg_atual, v_f);

        if privado.dados_filtrar(to_jsonb(v_cfg_novo), privado.dados_colunas('configuracoes'))
           = privado.dados_filtrar(to_jsonb(v_cfg_atual), privado.dados_colunas('configuracoes')) then
          v_resumo := privado.dados_inc(v_resumo, 'configuracoes', 'existentes');
        else
          update public.configuracoes
             set quilometragem = v_cfg_novo.quilometragem,
                 abastecimento_manutencao = v_cfg_novo.abastecimento_manutencao,
                 checklist = v_cfg_novo.checklist,
                 notificacao_email_criticos = v_cfg_novo.notificacao_email_criticos,
                 motorista = v_cfg_novo.motorista,
                 motorista_registra = v_cfg_novo.motorista_registra,
                 motorista_localizacao = v_cfg_novo.motorista_localizacao,
                 vigilante_km = v_cfg_novo.vigilante_km,
                 autorizador_decide_propria = v_cfg_novo.autorizador_decide_propria,
                 autorizador_km = v_cfg_novo.autorizador_km,
                 indicador_conectividade = v_cfg_novo.indicador_conectividade,
                 feedback_tatil = v_cfg_novo.feedback_tatil,
                 leitor_qr_veiculos = v_cfg_novo.leitor_qr_veiculos,
                 voz_campos = v_cfg_novo.voz_campos,
                 alterado_por = p_admin
           where id;
          v_resumo := privado.dados_inc(v_resumo, 'configuracoes', 'conflitos');  -- aplicado (arquivo prevalece)
        end if;
      exception when others then
        get stacked diagnostics v_estado = returned_sqlstate, v_constr = constraint_name,
                                v_coluna = column_name, v_msg = message_text;
        if v_estado like '40%' or v_estado like '53%' or v_estado like '55%' or v_estado like '57%' then
          raise;
        end if;
        v_erros_n := v_erros_n + 1;
        if v_erros_n <= c_max_erros then
          v_erros := v_erros || jsonb_build_object('tabela', v_tab_atual, 'indice', 0,
                       'mensagem', privado.dados_mensagem_erro(v_estado, v_constr, v_coluna, v_msg));
        end if;
      end;
    end if;

    if p_confirmar and v_erros_n = 0 then
      insert into public.auditoria (tabela, registro_id, acao, ator_id, detalhes)
      values ('configuracoes', null, 'dados_importados', p_admin,
              jsonb_build_object('versao', 1, 'resumo', v_resumo));
      v_aplicado := true;
    else
      raise exception 'desfazer' using errcode = 'AG099';  -- previa ou erro: nada fica gravado
    end if;
  exception when sqlstate 'AG099' then
    null;  -- variaveis locais (resumo/erros) sobrevivem ao rollback da subtransacao
  end;

  return jsonb_build_object(
    'resumo', v_resumo,
    'erros', v_erros,
    'erros_total', v_erros_n,
    'aplicado', v_aplicado,
    'politica_conflito', jsonb_build_object(
      'veiculos', 'atualiza', 'configuracoes', 'atualiza',
      'requisicoes', 'ignora', 'checklists', 'ignora', 'localizacoes_viagem', 'ignora'));
end;
$$;

revoke all on function public.importar_dados(jsonb, uuid, boolean) from public, anon, authenticated;
grant execute on function public.importar_dados(jsonb, uuid, boolean) to service_role;

comment on function public.importar_dados(jsonb, uuid, boolean) is
  'Importa (mescla, nunca apaga) veiculos, requisicoes, checklists, localizacoes e flags de configuracoes numa transacao unica. p_confirmar=false => previa (nada grava). Com erro de linha nada grava. Audita dados_importados so com contagens. So admin ativo; EXECUTE so service_role.';

-- ---------------------------------------------------------------------------
-- 5. Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v_def  text;
  v      text;
  v_tab  text;
  v_cols text[];
  v_oid  oid;
  v_fn   text;
begin
  -- ck_auditoria_acao: as 32 acoes.
  select pg_get_constraintdef(oid) into v_def from pg_constraint
   where conrelid = 'public.auditoria'::regclass and conname = 'ck_auditoria_acao';
  foreach v in array array['dados_exportados', 'dados_importados', 'passkey_desafio_invalido', 'criada'] loop
    if position('''' || v || '''' in v_def) = 0 then
      raise exception 'ck_auditoria_acao sem o valor %.', v;
    end if;
  end loop;

  -- Toda coluna das tabelas esta na lista (trafega) ou e excecao conhecida; toda coluna da lista existe.
  foreach v_tab in array array['veiculos', 'requisicoes', 'checklists', 'localizacoes_viagem', 'configuracoes'] loop
    v_cols := privado.dados_colunas(v_tab);
    foreach v in array v_cols loop
      if not exists (select 1 from information_schema.columns
                      where table_schema = 'public' and table_name = v_tab and column_name = v) then
        raise exception 'Coluna %.% da lista de dados nao existe.', v_tab, v;
      end if;
    end loop;
    for v in select column_name from information_schema.columns
              where table_schema = 'public' and table_name = v_tab loop
      if v <> all (v_cols)
         and v <> all (array['alterado_por', 'motorista_alterado_por', 'id', 'atualizado_em', 'mfa', 'passkeys',
                             'notificacao_push', 'relatorio_email', 'relatorio_whatsapp']) then
        raise exception 'Coluna %.% fora da lista de exportacao/importacao: revisar privado.dados_colunas.', v_tab, v;
      end if;
    end loop;
  end loop;

  -- Funcoes publicas: SECURITY DEFINER, search_path vazio, EXECUTE so service_role.
  foreach v_fn in array array['public.exportar_dados(uuid)', 'public.importar_dados(jsonb, uuid, boolean)'] loop
    v_oid := to_regprocedure(v_fn);
    if v_oid is null then
      raise exception '% ausente.', v_fn;
    end if;
    if not (select prosecdef and proconfig is not null and 'search_path=""' = any (proconfig)
              from pg_proc where oid = v_oid) then
      raise exception '% deve ser SECURITY DEFINER com search_path vazio.', v_fn;
    end if;
    if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
       or has_function_privilege('public', v_oid, 'execute')
       or not has_function_privilege('service_role', v_oid, 'execute') then
      raise exception 'Grants de % divergentes (so service_role executa).', v_fn;
    end if;
  end loop;

  -- Helpers privados: nenhum papel da API executa.
  foreach v_fn in array array['privado.dados_colunas(text)', 'privado.dados_filtrar(jsonb, text[])',
                              'privado.dados_inc(jsonb, text, text)',
                              'privado.dados_mensagem_erro(text, text, text, text)',
                              'privado.dados_desligar_gatilhos()', 'privado.dados_religar_gatilhos(jsonb)'] loop
    v_oid := to_regprocedure(v_fn);
    if v_oid is null then
      raise exception '% ausente.', v_fn;
    end if;
    if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
       or has_function_privilege('service_role', v_oid, 'execute') then
      raise exception '% nao deve ser executavel pela API.', v_fn;
    end if;
  end loop;
end
$$;

notify pgrst, 'reload schema';
