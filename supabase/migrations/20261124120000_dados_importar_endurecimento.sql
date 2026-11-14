-- Migration: endurecimento da importacao de Dados (revisao de seguranca). NOVA migration: NAO edita a
-- 20261123120000 (exportar_dados e os helpers dela seguem iguais). Redefine so public.importar_dados e acrescenta
-- helpers privados. NAO aplicar pelo servidor: supabase db push e do pipeline.
--
-- Mudancas em importar_dados(p_payload, p_admin, p_confirmar):
--  (a) AUDITORIA por requisicao nova: a importacao desliga os gatilhos de auditoria, entao cada requisicao
--      INSERIDA ganha uma linha ('requisicoes', id, 'criada', ator = p_admin, de nulo, para = status,
--      detalhes {"origem": "importacao"}). So uuid/status/texto fixo, nenhum dado pessoal. Requisicao ja
--      existente (igual ou em conflito ignorado) nao gera linha. Acao 'criada' ja existe em ck_auditoria_acao
--      (nao ha 'importada'); a origem fica em detalhes. Previa e erro desfazem tudo (subtransacao).
--  (b) VALIDACAO como regra da RPC, ja que os gatilhos de estado ficam desligados (erro AG061, texto fixo, sem
--      valores). Em requisicao NOVA:
--        autorizado_por .............. perfil ATIVO com role autorizador ou admin;
--        motorista_id ................ perfil ATIVO com role motorista;
--        saida/chegada_registrada_por  perfil ATIVO com role autorizador ou vigilante;
--        estado/km (espelho dos CHECKs, para dar mensagem clara e nao depender so deles): status x autorizacao/
--        cancelamento/saida/chegada reais; pares autorizado, saida, chegada e cancelamento; chegada > saida;
--        km >= 0; km_chegada >= km_saida; km_chegada exige km_saida; km so com a saida/chegada real.
--      Os CHECKs, FKs e indices unicos da tabela continuam valendo como segunda barreira.
--  (c) configuracoes: notificacao_email_criticos FICA FORA da importacao (envio externo; o arquivo de exportacao
--      continua trazendo-a, a importacao a ignora). A resposta ganha "configuracoes_alteradas":
--      [{"chave", "de", "para"}] (so flags que mudariam; booleanos), igual na previa e na confirmacao.
--  (d) Limites e estrutura (AG060) com mensagem por tabela: "<tabela>" + limite + quantidade recebida (so
--      numeros e nome de tabela). Limites inalterados: veiculos 500, requisicoes 20000, checklists 40000,
--      localizacoes_viagem 40000, configuracoes 1 linha. Erros de linha (AG061) seguem agrupados por tabela e
--      indice, ate 200 detalhados, com "erros_total" completo.
--
-- Erros: AG002 (nao admin ativo), AG060 (arquivo/estrutura invalida ou limite excedido), AG061 (linha invalida).
-- Seguranca: SECURITY DEFINER, search_path vazio, EXECUTE so service_role; helpers sem EXECUTE para a API.

set local lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 1. Helpers privados
-- ---------------------------------------------------------------------------
-- Flags de configuracoes que a IMPORTACAO aceita (= privado.dados_colunas('configuracoes') sem
-- notificacao_email_criticos).
create or replace function privado.dados_colunas_cfg_importar()
returns text[]
language sql
immutable
set search_path = ''
as $$
  select array_remove(privado.dados_colunas('configuracoes'), 'notificacao_email_criticos');
$$;

revoke all on function privado.dados_colunas_cfg_importar() from public, anon, authenticated, service_role;

-- Perfil existe, esta ativo e tem um dos papeis (comparacao por texto: independe do enum).
create or replace function privado.dados_perfil_ok(p_id uuid, p_papeis text[])
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from public.perfis p
                  where p.id = p_id and p.ativo and p.role::text = any (p_papeis));
$$;

revoke all on function privado.dados_perfil_ok(uuid, text[]) from public, anon, authenticated, service_role;

-- Regras de estado/km/responsaveis de uma requisicao NOVA. Texto fixo (sem valores): AG061.
create or replace function privado.dados_validar_requisicao(r public.requisicoes)
returns void
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if r.status is null then
    raise exception 'Campo obrigatório ausente: status.' using errcode = 'AG061';
  end if;

  -- Responsaveis (papel e situacao vigentes no banco de destino).
  if r.autorizado_por is not null
     and not privado.dados_perfil_ok(r.autorizado_por, array['autorizador', 'admin']) then
    raise exception 'autorizado_por deve ser um autorizador ou administrador ativo.' using errcode = 'AG061';
  end if;
  if r.motorista_id is not null and not privado.dados_perfil_ok(r.motorista_id, array['motorista']) then
    raise exception 'motorista_id deve ser um motorista ativo.' using errcode = 'AG061';
  end if;
  if r.saida_registrada_por is not null
     and not privado.dados_perfil_ok(r.saida_registrada_por, array['autorizador', 'vigilante']) then
    raise exception 'saida_registrada_por deve ser um autorizador ou vigilante ativo.' using errcode = 'AG061';
  end if;
  if r.chegada_registrada_por is not null
     and not privado.dados_perfil_ok(r.chegada_registrada_por, array['autorizador', 'vigilante']) then
    raise exception 'chegada_registrada_por deve ser um autorizador ou vigilante ativo.' using errcode = 'AG061';
  end if;

  -- Pares (um sem o outro e incoerente).
  if (r.autorizado_por is null) is distinct from (r.autorizado_em is null) then
    raise exception 'autorizado_por e autorizado_em devem vir juntos.' using errcode = 'AG061';
  end if;
  if (r.saida_real_em is null) is distinct from (r.saida_registrada_por is null) then
    raise exception 'saida_real_em e saida_registrada_por devem vir juntos.' using errcode = 'AG061';
  end if;
  if (r.chegada_real_em is null) is distinct from (r.chegada_registrada_por is null) then
    raise exception 'chegada_real_em e chegada_registrada_por devem vir juntos.' using errcode = 'AG061';
  end if;
  if (r.cancelada_por is null) is distinct from (r.cancelada_em is null) then
    raise exception 'cancelada_por e cancelada_em devem vir juntos.' using errcode = 'AG061';
  end if;

  -- Estado x colunas (espelho de ck_requisicoes_status_coerente).
  if not (case r.status::text
            when 'solicitada' then r.autorizado_por is null and r.cancelada_por is null
                                   and r.saida_real_em is null and r.chegada_real_em is null
            when 'autorizada' then r.autorizado_por is not null and r.cancelada_por is null
                                   and r.saida_real_em is null and r.chegada_real_em is null
            when 'negada'     then r.autorizado_por is not null and r.cancelada_por is null
                                   and r.saida_real_em is null and r.chegada_real_em is null
            when 'cancelada'  then r.cancelada_por is not null
                                   and r.saida_real_em is null and r.chegada_real_em is null
            when 'em_uso'     then r.autorizado_por is not null and r.cancelada_por is null
                                   and r.saida_real_em is not null and r.chegada_real_em is null
            when 'concluida'  then r.autorizado_por is not null and r.cancelada_por is null
                                   and r.saida_real_em is not null and r.chegada_real_em is not null
            else false
          end) then
    raise exception 'Estado da requisição incoerente com autorização, cancelamento, saída e chegada reais.'
      using errcode = 'AG061';
  end if;
  if r.saida_real_em is not null and r.chegada_real_em is not null and r.chegada_real_em <= r.saida_real_em then
    raise exception 'chegada_real_em deve ser posterior a saida_real_em.' using errcode = 'AG061';
  end if;

  -- Quilometragem (espelho de ck_requisicoes_km_*).
  if r.km_saida is not null and r.km_saida < 0 then
    raise exception 'km_saida não pode ser negativo.' using errcode = 'AG061';
  end if;
  if r.km_chegada is not null and r.km_chegada < 0 then
    raise exception 'km_chegada não pode ser negativo.' using errcode = 'AG061';
  end if;
  if r.km_chegada is not null and r.km_saida is null then
    raise exception 'km_chegada exige km_saida.' using errcode = 'AG061';
  end if;
  if r.km_saida is not null and r.km_chegada is not null and r.km_chegada < r.km_saida then
    raise exception 'km_chegada não pode ser menor que km_saida.' using errcode = 'AG061';
  end if;
  if r.km_saida is not null and r.saida_real_em is null then
    raise exception 'km_saida exige saída real registrada.' using errcode = 'AG061';
  end if;
  if r.km_chegada is not null and r.chegada_real_em is null then
    raise exception 'km_chegada exige chegada real registrada.' using errcode = 'AG061';
  end if;
end;
$$;

revoke all on function privado.dados_validar_requisicao(public.requisicoes)
  from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 2. importar_dados (redefinicao completa)
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
  'Importa (mescla, nunca apaga) veiculos, requisicoes, checklists, localizacoes e flags de configuracoes (sem notificacao_email_criticos) numa transacao unica. p_confirmar=false => previa (nada grava). Com erro de linha nada grava. Valida responsaveis/estado/km das requisicoes novas e audita cada uma (criada, origem importacao). Devolve configuracoes_alteradas. So admin ativo; EXECUTE so service_role.';

-- ---------------------------------------------------------------------------
-- 3. Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v_oid oid;
  v_fn  text;
  v_def text;
begin
  v_oid := to_regprocedure('public.importar_dados(jsonb, uuid, boolean)');
  if v_oid is null then
    raise exception 'public.importar_dados ausente.';
  end if;
  if not (select prosecdef and proconfig is not null and 'search_path=""' = any (proconfig)
            from pg_proc where oid = v_oid) then
    raise exception 'importar_dados deve ser SECURITY DEFINER com search_path vazio.';
  end if;
  if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('public', v_oid, 'execute')
     or not has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'Grants de importar_dados divergentes (so service_role executa).';
  end if;
  v_def := pg_get_functiondef(v_oid);
  if position('configuracoes_alteradas' in v_def) = 0 or position('importacao' in v_def) = 0
     or position('dados_validar_requisicao' in v_def) = 0
     or position('dados_colunas_cfg_importar' in v_def) = 0 then
    raise exception 'importar_dados sem as regras desta migration.';
  end if;
  if 'notificacao_email_criticos' = any (privado.dados_colunas_cfg_importar()) then
    raise exception 'notificacao_email_criticos nao pode ser importada.';
  end if;

  foreach v_fn in array array['privado.dados_colunas_cfg_importar()', 'privado.dados_perfil_ok(uuid, text[])',
                              'privado.dados_validar_requisicao(public.requisicoes)'] loop
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
