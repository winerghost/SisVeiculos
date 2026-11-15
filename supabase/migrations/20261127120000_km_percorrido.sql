-- Migration: km_percorrido. Nova migration; nao edita as anteriores. Idempotente.
--
-- Motivo: requisicoes ganha km_percorrido (km_chegada - km_saida), calculado SO no banco.
--   * Gatilho BEFORE INSERT OR UPDATE (nao coluna gerada: a importacao faz insert ... select (v_rrec).*,
--     que recusaria valor explicito em coluna gerada). Nulo se algum km for nulo. O valor do cliente nunca
--     vale: o gatilho sobrescreve. Nome trg_requisicoes_zz_* para rodar por ULTIMO entre os BEFORE (ordem
--     alfabetica): calcula sobre o km ja validado e, em linha encerrada, trg_requisicoes_validar ainda ve
--     NEW = OLD e recusa (AG001) quem tentar gravar km_percorrido direto.
--   * Backfill com os gatilhos desligados (dados_desligar_gatilhos/dados_religar_gatilhos, estado original
--     restaurado): linhas encerradas sao congeladas (AG001) e atualizado_em do historico nao pode mudar.
--   * privado.dados_desligar_gatilhos passa a NAO desligar o gatilho de calculo (a importacao recalcula).
--   * public.importar_dados: redefinicao identica a 20261125120000, so com km_percorrido em c_ign_req
--     (fora dos dois lados da comparacao de requisicao existente; o arquivo nao traz a chave e, se trouxer,
--     dados_filtrar a descarta). Assinatura, grants e comment mantidos.
--   * Relatorios (mesmas assinaturas de 20261119120000): rel_movimentacao ganha km_percorrido junto de
--     km_saida/km_chegada (so com Quilometragem ligada); rel_opcionais_km (ja so com o recurso ligado) ganha
--     km_percorrido e mantem km_rodado por compatibilidade. rel_pendencias nao lista km: nao muda.
-- RLS de requisicoes intacto (coluna nova herda as politicas da tabela).
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

set local lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 1. Coluna
-- ---------------------------------------------------------------------------
alter table public.requisicoes add column if not exists km_percorrido integer;

comment on column public.requisicoes.km_percorrido is
  'Km percorrido na viagem (km_chegada - km_saida). Calculado so pelo banco (gatilho trg_requisicoes_zz_km_percorrido); nulo se algum km for nulo. Exibido apenas com o recurso Quilometragem ligado.';

-- ---------------------------------------------------------------------------
-- 2. Funcao de calculo (sem acesso a tabelas; SECURITY INVOKER, search_path vazio)
-- ---------------------------------------------------------------------------
create or replace function public.requisicoes_calcular_km_percorrido()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.km_percorrido := case when new.km_saida is not null and new.km_chegada is not null
                            then new.km_chegada - new.km_saida end;
  return new;
end;
$$;

revoke all on function public.requisicoes_calcular_km_percorrido() from public, anon, authenticated;

comment on function public.requisicoes_calcular_km_percorrido() is
  'Gatilho: calcula requisicoes.km_percorrido = km_chegada - km_saida (nulo se algum km for nulo); ignora valor vindo do cliente.';

-- ---------------------------------------------------------------------------
-- 3. Backfill (gatilhos desligados e restaurados no estado original; DDL transacional)
-- ---------------------------------------------------------------------------
do $$
declare
  v_estado jsonb;
begin
  v_estado := privado.dados_desligar_gatilhos();
  update public.requisicoes r
     set km_percorrido = case when r.km_saida is not null and r.km_chegada is not null
                              then r.km_chegada - r.km_saida end
   where r.km_percorrido is distinct from
         (case when r.km_saida is not null and r.km_chegada is not null then r.km_chegada - r.km_saida end);
  perform privado.dados_religar_gatilhos(v_estado);
end
$$;

-- ---------------------------------------------------------------------------
-- 4. Gatilho
-- ---------------------------------------------------------------------------
drop trigger if exists trg_requisicoes_zz_km_percorrido on public.requisicoes;
create trigger trg_requisicoes_zz_km_percorrido
  before insert or update on public.requisicoes
  for each row execute function public.requisicoes_calcular_km_percorrido();
-- Padrao dos gatilhos de requisicoes: ENABLE ALWAYS (dispara tambem com session_replication_role = replica).
alter table public.requisicoes enable always trigger trg_requisicoes_zz_km_percorrido;


-- ---------------------------------------------------------------------------
-- 5. Importacao: nao desligar o gatilho de calculo (corpo de 20261123120000 + uma exclusao)
-- ---------------------------------------------------------------------------
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
       and t.tgfoid <> 'public.requisicoes_calcular_km_percorrido()'::regprocedure
     order by c.relname, t.tgname
  loop
    execute format('alter table public.%I disable trigger %I', r.tabela, r.gatilho);
    v_saida := v_saida || jsonb_build_object('tabela', r.tabela, 'gatilho', r.gatilho, 'estado', r.estado);
  end loop;
  return v_saida;
end;
$$;

revoke all on function privado.dados_desligar_gatilhos() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 6. importar_dados (redefinicao completa de 20261125120000; so muda c_ign_req)
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
  c_ign_req       constant text[] := array['criado_em', 'atualizado_em', 'km_percorrido'];  -- derivado: recalculado pelo gatilho

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
  v_alteradas  jsonb := '[]'::jsonb;   -- configuracoes_alteradas: [{chave, de, para}]

  v_lim        record;
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

  -- Tipo e limite por tabela, com mensagem que diz qual tabela e qual o limite.
  for v_lim in
    select * from (values
      ('veiculos', v_veic, c_lim_veiculos),
      ('requisicoes', v_req, c_lim_requisic),
      ('checklists', v_chk, c_lim_checklist),
      ('localizacoes_viagem', v_loc, c_lim_locais)
    ) as t(nome, val, lim)
  loop
    if jsonb_typeof(v_lim.val) <> 'array' then
      raise exception 'Arquivo de dados inválido: a tabela % deve ser uma lista.', v_lim.nome
        using errcode = 'AG060';
    end if;
    if jsonb_array_length(v_lim.val) > v_lim.lim then
      raise exception 'Arquivo de dados excede o limite da tabela %: máximo de % linhas por importação, recebidas %.',
        v_lim.nome, v_lim.lim, jsonb_array_length(v_lim.val)
        using errcode = 'AG060';
    end if;
  end loop;

  if v_cfg is not null then
    if jsonb_typeof(v_cfg) = 'array' then
      if jsonb_array_length(v_cfg) > 1 then
        raise exception 'Arquivo de dados excede o limite da tabela configuracoes: máximo de 1 linha, recebidas %.',
          jsonb_array_length(v_cfg) using errcode = 'AG060';
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
          perform privado.dados_validar_requisicao(v_rrec);  -- responsaveis, estado e km (AG061)
          insert into public.requisicoes select (v_rrec).*;
          -- Gatilhos de auditoria desligados na importacao: a criacao e auditada aqui (so uuid/status).
          insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
          values ('requisicoes', v_rrec.id, 'criada', p_admin, null, v_rrec.status::text,
                  jsonb_build_object('origem', 'importacao'));
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
          perform privado.dados_validar_checklist(v_crec);  -- requisicao, autor, valores (AG061)
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
          perform privado.dados_validar_localizacao(v_lrec);  -- requisicao, autor, faixas (AG061)
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

    -- ===================== configuracoes =====================
    -- So flags de privado.dados_colunas_cfg_importar(): mfa/passkeys/push/relatorios e
    -- notificacao_email_criticos nunca sao importados.
    v_tab_atual := 'configuracoes';
    if v_cfg is not null then
      begin
        v_f := privado.dados_filtrar(v_cfg, privado.dados_colunas_cfg_importar());
        for v_chv in select k from jsonb_object_keys(v_f) as k loop
          if jsonb_typeof(v_f -> v_chv) <> 'boolean' then
            raise exception 'O valor de % deve ser verdadeiro ou falso.', v_chv using errcode = 'AG061';
          end if;
        end loop;

        select * into v_cfg_atual from public.configuracoes where id;
        v_cfg_novo := jsonb_populate_record(v_cfg_atual, v_f);

        select coalesce(jsonb_agg(jsonb_build_object('chave', k, 'de', to_jsonb(v_cfg_atual) -> k,
                                                     'para', to_jsonb(v_cfg_novo) -> k) order by k), '[]'::jsonb)
          into v_alteradas
          from unnest(privado.dados_colunas_cfg_importar()) as k
         where (to_jsonb(v_cfg_atual) -> k) is distinct from (to_jsonb(v_cfg_novo) -> k);

        if jsonb_array_length(v_alteradas) = 0 then
          v_resumo := privado.dados_inc(v_resumo, 'configuracoes', 'existentes');
        else
          update public.configuracoes
             set quilometragem = v_cfg_novo.quilometragem,
                 abastecimento_manutencao = v_cfg_novo.abastecimento_manutencao,
                 checklist = v_cfg_novo.checklist,
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
    'configuracoes_alteradas', v_alteradas,
    'politica_conflito', jsonb_build_object(
      'veiculos', 'atualiza', 'configuracoes', 'atualiza',
      'requisicoes', 'ignora', 'checklists', 'ignora', 'localizacoes_viagem', 'ignora'));
end;
$$;

revoke all on function public.importar_dados(jsonb, uuid, boolean) from public, anon, authenticated;
grant execute on function public.importar_dados(jsonb, uuid, boolean) to service_role;

comment on function public.importar_dados(jsonb, uuid, boolean) is
  'Importa (mescla, nunca apaga) veiculos, requisicoes, checklists, localizacoes e flags de configuracoes (sem notificacao_email_criticos) numa transacao unica. p_confirmar=false => previa (nada grava). Com erro de linha nada grava. Valida usuario/cancelada_por (existencia), responsaveis ativos, estado/km das requisicoes novas e checklists/localizacoes novos (requisicao, autor, faixas); audita cada requisicao nova (criada, origem importacao). km_percorrido do arquivo e ignorado (recalculado pelo gatilho). Devolve configuracoes_alteradas. So admin ativo; EXECUTE so service_role.';

-- ---------------------------------------------------------------------------
-- 7. rel_movimentacao (+ km_percorrido, so com Quilometragem ligada)
-- ---------------------------------------------------------------------------
create or replace function public.rel_movimentacao(
  p_inicio             timestamptz,
  p_fim                timestamptz,
  p_campo_data         text default 'criacao',
  p_unidade            public.unidade default null,
  p_veiculo            uuid default null,
  p_usuario            uuid default null,
  p_incluir_nomes      boolean default false,
  p_incluir_trajeto    boolean default false,
  p_limite             integer default 5001,
  p_incluir_observacao boolean default false
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_km     boolean;
  v_nomes  boolean := coalesce(p_incluir_nomes, false);
  v_traj   boolean := coalesce(p_incluir_trajeto, false);
  v_obs    boolean := coalesce(p_incluir_observacao, false);
  v_cols   text[] := array['codigo', 'criada_em', 'unidade', 'veiculo_id', 'veiculo', 'status',
                           'hora_saida_prevista', 'autorizada_em', 'saida_real_em', 'chegada_real_em',
                           'espera_autorizacao_s', 'duracao_uso_s'];
  v_linhas jsonb;
  v_cont   integer;
begin
  perform privado.dash_validar_janela(p_inicio, p_fim);
  perform privado.rel_validar_limite(p_limite);
  if p_campo_data is null or p_campo_data not in ('criacao', 'saida_prevista', 'saida_real') then
    raise exception 'Campo de data inválido: use criacao, saida_prevista ou saida_real.'
      using errcode = 'AG023';
  end if;

  select coalesce(c.quilometragem, false) into v_km from public.configuracoes c where c.id;
  v_km := coalesce(v_km, false);
  if v_km then v_cols := v_cols || array['km_saida', 'km_chegada', 'km_percorrido']; end if;
  if v_nomes then v_cols := v_cols || array['solicitante', 'autorizado_por', 'saida_por', 'chegada_por']; end if;
  if v_traj then v_cols := v_cols || array['trajeto']; end if;
  if v_obs then v_cols := v_cols || array['observacao']; end if;

  with ids as (
    select r.id, r.criado_em as k from public.requisicoes r
     where p_campo_data = 'criacao' and r.criado_em >= p_inicio and r.criado_em < p_fim
       and (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
       and (p_usuario is null or r.usuario_id = p_usuario)
    union all
    select r.id, r.hora_saida from public.requisicoes r
     where p_campo_data = 'saida_prevista' and r.hora_saida >= p_inicio and r.hora_saida < p_fim
       and (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
       and (p_usuario is null or r.usuario_id = p_usuario)
    union all
    select r.id, r.saida_real_em from public.requisicoes r
     where p_campo_data = 'saida_real' and r.saida_real_em >= p_inicio and r.saida_real_em < p_fim
       and (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
       and (p_usuario is null or r.usuario_id = p_usuario)
  ),
  sel as (
    select i.id, i.k, row_number() over (order by i.k, i.id) as n
      from (select * from ids order by k, id limit p_limite + 1) i
  )
  select coalesce(jsonb_agg(x.j order by x.n) filter (where x.n <= p_limite), '[]'::jsonb),
         count(*)
    into v_linhas, v_cont
    from (
      select s.n,
             jsonb_build_object(
               'codigo', left(r.id::text, 8),
               'criada_em', r.criado_em,
               'unidade', r.unidade,
               'veiculo_id', r.veiculo_id,
               'veiculo', case when v.placa is null then v.descricao else v.placa || ' - ' || v.descricao end,
               'status', r.status,
               'hora_saida_prevista', r.hora_saida,
               'autorizada_em', r.autorizado_em,
               'saida_real_em', r.saida_real_em,
               'chegada_real_em', r.chegada_real_em,
               'espera_autorizacao_s',
                 case when r.autorizado_em is not null and r.status <> 'negada'
                      then floor(extract(epoch from r.autorizado_em - r.criado_em))::bigint end,
               'duracao_uso_s',
                 case when r.saida_real_em is not null and r.chegada_real_em is not null
                      then floor(extract(epoch from r.chegada_real_em - r.saida_real_em))::bigint end)
             || case when v_km then jsonb_build_object('km_saida', r.km_saida, 'km_chegada', r.km_chegada,
                                             'km_percorrido', r.km_percorrido)
                     else '{}'::jsonb end
             || case when v_nomes then jsonb_build_object(
                  'solicitante', r.nome_solicitante,
                  'autorizado_por', pa.nome,
                  'saida_por', ps.nome,
                  'chegada_por', pc.nome) else '{}'::jsonb end
             || case when v_traj then jsonb_build_object('trajeto', r.trajeto) else '{}'::jsonb end
             || case when v_obs then jsonb_build_object('observacao', r.observacao) else '{}'::jsonb end as j
        from sel s
        join public.requisicoes r on r.id = s.id
        join public.veiculos v on v.id = r.veiculo_id
        left join public.perfis pa on v_nomes and pa.id = r.autorizado_por
        left join public.perfis ps on v_nomes and ps.id = r.saida_registrada_por
        left join public.perfis pc on v_nomes and pc.id = r.chegada_registrada_por
    ) x;

  return jsonb_build_object(
    'campo_data', p_campo_data,
    'colunas', to_jsonb(v_cols),
    'colunas_opcionais', jsonb_build_object('km', v_km, 'nomes', v_nomes, 'trajeto', v_traj,
                                            'observacao', v_obs),
    'total_linhas', least(v_cont, p_limite),
    'truncado', v_cont > p_limite,
    'linhas', v_linhas);
end;
$$;

-- ---------------------------------------------------------------------------
-- 8. rel_opcionais_km (+ km_percorrido; km_rodado mantido por compatibilidade)
-- ---------------------------------------------------------------------------
-- Recurso Quilometragem desligado continua devolvendo apenas {"habilitado": false}.
create or replace function public.rel_opcionais_km(
  p_inicio             timestamptz,
  p_fim                timestamptz,
  p_unidade            public.unidade default null,
  p_veiculo            uuid default null,
  p_usuario            uuid default null,
  p_limite             integer default 5001,
  p_incluir_observacao boolean default false
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_km     boolean;
  v_obs    boolean := coalesce(p_incluir_observacao, false);
  v_cols   text[] := array['codigo', 'unidade', 'veiculo_id', 'veiculo', 'saida_real_em',
                           'chegada_real_em', 'km_saida', 'km_chegada', 'km_rodado', 'km_percorrido'];
  v_linhas jsonb;
  v_cont   integer;
  v_viag   bigint;
  v_rodado bigint;
begin
  perform privado.dash_validar_janela(p_inicio, p_fim);
  perform privado.rel_validar_limite(p_limite);
  select coalesce(c.quilometragem, false) into v_km from public.configuracoes c where c.id;
  if not coalesce(v_km, false) then
    return jsonb_build_object('habilitado', false);
  end if;
  if v_obs then v_cols := v_cols || array['observacao']; end if;

  select count(*), coalesce(sum(r.km_chegada - r.km_saida), 0)::bigint into v_viag, v_rodado
    from public.requisicoes r
   where r.status = 'concluida' and r.saida_real_em >= p_inicio and r.saida_real_em < p_fim
     and r.km_saida is not null and r.km_chegada is not null
     and (p_unidade is null or r.unidade = p_unidade)
     and (p_veiculo is null or r.veiculo_id = p_veiculo)
     and (p_usuario is null or r.usuario_id = p_usuario);

  with sel as (
    select s.id, row_number() over (order by s.saida_real_em, s.id) as n
      from (select r.id, r.saida_real_em from public.requisicoes r
             where r.status = 'concluida' and r.saida_real_em >= p_inicio and r.saida_real_em < p_fim
               and r.km_saida is not null and r.km_chegada is not null
               and (p_unidade is null or r.unidade = p_unidade)
               and (p_veiculo is null or r.veiculo_id = p_veiculo)
               and (p_usuario is null or r.usuario_id = p_usuario)
             order by r.saida_real_em, r.id limit p_limite + 1) s
  )
  select coalesce(jsonb_agg(x.j order by x.n) filter (where x.n <= p_limite), '[]'::jsonb), count(*)
    into v_linhas, v_cont
    from (
      select s.n,
             jsonb_build_object(
               'codigo', left(r.id::text, 8), 'unidade', r.unidade, 'veiculo_id', r.veiculo_id,
               'veiculo', case when v.placa is null then v.descricao else v.placa || ' - ' || v.descricao end,
               'saida_real_em', r.saida_real_em, 'chegada_real_em', r.chegada_real_em,
               'km_saida', r.km_saida, 'km_chegada', r.km_chegada,
               'km_rodado', r.km_chegada - r.km_saida, 'km_percorrido', r.km_percorrido)
             || case when v_obs then jsonb_build_object('observacao', r.observacao)
                     else '{}'::jsonb end as j
        from sel s
        join public.requisicoes r on r.id = s.id
        join public.veiculos v on v.id = r.veiculo_id
    ) x;

  return jsonb_build_object(
    'habilitado', true,
    'colunas', to_jsonb(v_cols),
    'totais', jsonb_build_object('viagens', v_viag, 'km_rodado', v_rodado),
    'total_linhas', least(v_cont, p_limite), 'truncado', v_cont > p_limite, 'linhas', v_linhas);
end;
$$;

comment on function public.rel_movimentacao(timestamptz, timestamptz, text, public.unidade, uuid, uuid, boolean, boolean, integer, boolean) is
  'Relatorio: uma linha por requisicao (nomes, trajeto e observacao so por flag; km_saida, km_chegada e km_percorrido so se recurso ligado). Somente service_role.';
comment on function public.rel_opcionais_km(timestamptz, timestamptz, public.unidade, uuid, uuid, integer, boolean) is
  'Relatorio: km por viagem concluida (km_rodado e km_percorrido), so se o recurso Quilometragem estiver ligado; observacao so por flag. Somente service_role.';

revoke all on function public.rel_movimentacao(timestamptz, timestamptz, text, public.unidade, uuid, uuid, boolean, boolean, integer, boolean) from public, anon, authenticated;
revoke all on function public.rel_opcionais_km(timestamptz, timestamptz, public.unidade, uuid, uuid, integer, boolean) from public, anon, authenticated;
grant execute on function public.rel_movimentacao(timestamptz, timestamptz, text, public.unidade, uuid, uuid, boolean, boolean, integer, boolean) to service_role;
grant execute on function public.rel_opcionais_km(timestamptz, timestamptz, public.unidade, uuid, uuid, integer, boolean) to service_role;

-- ---------------------------------------------------------------------------
-- 9. Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  f     text;
  r     text;
  v_oid oid;
  v_def text;
begin
  if not exists (select 1 from pg_attribute
                  where attrelid = 'public.requisicoes'::regclass and attname = 'km_percorrido'
                    and not attisdropped and atttypid = 'integer'::regtype and attgenerated = '') then
    raise exception 'requisicoes.km_percorrido ausente ou com tipo divergente.';
  end if;
  if not exists (select 1 from pg_trigger
                  where tgrelid = 'public.requisicoes'::regclass and tgname = 'trg_requisicoes_zz_km_percorrido'
                    and tgenabled = 'A'
                    and tgfoid = 'public.requisicoes_calcular_km_percorrido()'::regprocedure) then
    raise exception 'Gatilho trg_requisicoes_zz_km_percorrido ausente ou fora de ENABLE ALWAYS.';
  end if;
  if exists (select 1 from public.requisicoes x
              where x.km_percorrido is distinct from (x.km_chegada - x.km_saida)) then
    raise exception 'Backfill de km_percorrido incompleto.';
  end if;
  if not exists (select 1 from pg_class where relname = 'requisicoes' and relrowsecurity
                    and relnamespace = 'public'::regnamespace) then
    raise exception 'RLS desligado em public.requisicoes.';
  end if;
  foreach r in array array['anon', 'authenticated'] loop
    if has_function_privilege(r, 'public.requisicoes_calcular_km_percorrido()', 'execute') then
      raise exception '% executa requisicoes_calcular_km_percorrido.', r;
    end if;
  end loop;

  if position('requisicoes_calcular_km_percorrido' in
              pg_get_functiondef('privado.dados_desligar_gatilhos()'::regprocedure)) = 0 then
    raise exception 'dados_desligar_gatilhos ainda desliga o gatilho de km_percorrido.';
  end if;

  v_oid := to_regprocedure('public.importar_dados(jsonb, uuid, boolean)');
  v_def := pg_get_functiondef(v_oid);
  if position('''km_percorrido''' in v_def) = 0
     or position('dados_validar_checklist' in v_def) = 0 or position('dados_validar_localizacao' in v_def) = 0
     or position('dados_validar_requisicao' in v_def) = 0 or position('configuracoes_alteradas' in v_def) = 0
     or position('dados_colunas_cfg_importar' in v_def) = 0 then
    raise exception 'importar_dados sem as regras vigentes ou sem km_percorrido em c_ign_req.';
  end if;
  if not (select prosecdef and 'search_path=""' = any (proconfig) from pg_proc where oid = v_oid)
     or has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('public', v_oid, 'execute')
     or not has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'importar_dados com SECURITY DEFINER/search_path/grants divergentes.';
  end if;

  if exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
              where n.nspname = 'public' and p.proname in ('rel_movimentacao', 'rel_opcionais_km', 'importar_dados')
              group by p.proname having count(*) > 1) then
    raise exception 'Sobrecarga duplicada em rel_movimentacao/rel_opcionais_km/importar_dados.';
  end if;
  foreach f in array array[
    'public.rel_movimentacao(timestamptz, timestamptz, text, public.unidade, uuid, uuid, boolean, boolean, integer, boolean)',
    'public.rel_opcionais_km(timestamptz, timestamptz, public.unidade, uuid, uuid, integer, boolean)'
  ] loop
    foreach r in array array['anon', 'authenticated', 'public'] loop
      if has_function_privilege(r, f::regprocedure, 'execute') then
        raise exception '% executa %.', r, f;
      end if;
    end loop;
    if not has_function_privilege('service_role', f::regprocedure, 'execute') then
      raise exception 'service_role sem EXECUTE em %.', f;
    end if;
    if not exists (select 1 from pg_proc p2
                    where p2.oid = f::regprocedure and p2.prosecdef
                      and array_to_string(p2.proconfig, ',') like '%search_path=%') then
      raise exception '% sem SECURITY DEFINER ou search_path fixo.', f;
    end if;
    if position('km_percorrido' in pg_get_functiondef(f::regprocedure)) = 0 then
      raise exception '% sem km_percorrido.', f;
    end if;
  end loop;
end
$$;

notify pgrst, 'reload schema';
