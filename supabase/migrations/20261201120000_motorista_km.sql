-- Migration: recurso opcional "KM pelo motorista" (flag configuracoes.motorista_km).
-- Nova migration; nao edita as anteriores. Idempotente. Depende de 20261119120200 (requisicoes_validar_km por
-- papel), 20261121120000 (configuracoes_validar, 19 chaves; nenhuma migration posterior a redefine),
-- 20261123120000 (privado.dados_colunas; nao redefinida depois) e 20261127120000 (importar_dados vigente).
--
-- Motivo (contrato do recurso): ligado, o motorista, ao registrar a saida da propria viagem, e obrigado a
-- lancar km_saida e, na chegada, km_chegada (e km_saida junto se a saida ficou sem km), igual ao vigilante com
-- vigilante_km. Desligado, o motorista registra sem km (comportamento atual; o backend ignora o campo).
-- Exige quilometragem E motorista_registra ligados. Desligar nunca apaga km gravado (km e imutavel, AG004).
--
-- 1. configuracoes.motorista_km boolean not null default false + CHECK
--    ck_configuracoes_motorista_km_exige_dependencias (motorista_km => quilometragem and motorista_registra).
-- 2. configuracoes_validar: copia fiel da versao de 20261121120000 (19 chaves) + 20a chave (early-return,
--    auditoria) e regras AG010 amigaveis antes do CHECK, com caminho inverso:
--      "O KM do motorista exige a Quilometragem ligada." / "Desligue o KM do motorista antes de desligar a
--      Quilometragem."
--      "O KM do motorista exige o registro de saída e chegada pelo motorista ligado." / "Desligue o KM do
--      motorista antes de desligar o registro de saída e chegada pelo motorista."
--    Admin ativo (AG002), ator zerado e uma linha de auditoria 'configuracao_alterada' por chave preservados.
-- 3. requisicoes_validar_km: copia fiel da versao de 20261119120200; UNICA mudanca = ramo do papel motorista:
--      ligada, ator motorista ... obrigado se motorista_km (antes: nunca)
--    Demais ramos iguais (vigilante => vigilante_km; autorizador/admin => autorizador_km; outros => nunca).
-- 4. Exportacao/importacao: privado.dados_colunas (copia fiel de 20261123120000) ganha motorista_km na lista de
--    configuracoes (exportar_dados e dados_colunas_cfg_importar derivam dela); public.importar_dados (copia
--    fiel de 20261127120000) ganha motorista_km no UPDATE explicito de configuracoes. Assinaturas, grants e
--    comments mantidos.
-- Fuso: sem colunas de data/hora novas (timestamptz, UTC; exibicao em America/Sao_Paulo). RLS/grants: nao mudam.

set local lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 1. Coluna e CHECK
-- ---------------------------------------------------------------------------
alter table public.configuracoes
  add column if not exists motorista_km boolean not null default false;

comment on column public.configuracoes.motorista_km is
  'Recurso opcional: o motorista informa o KM de saida ao registrar a saida e o KM de chegada ao registrar a chegada. Exige quilometragem e motorista_registra. Desligado por padrao.';

alter table public.configuracoes
  drop constraint if exists ck_configuracoes_motorista_km_exige_dependencias;
alter table public.configuracoes
  add constraint ck_configuracoes_motorista_km_exige_dependencias
  check (not motorista_km or (quilometragem and motorista_registra));

-- ---------------------------------------------------------------------------
-- 2. configuracoes_validar (20 chaves)
-- ---------------------------------------------------------------------------
create or replace function public.configuracoes_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator  uuid := new.alterado_por;
  v_chave text;
begin
  new.alterado_por := null;  -- nunca persiste

  if new.quilometragem is not distinct from old.quilometragem
     and new.abastecimento_manutencao is not distinct from old.abastecimento_manutencao
     and new.checklist is not distinct from old.checklist
     and new.mfa is not distinct from old.mfa
     and new.relatorio_email is not distinct from old.relatorio_email
     and new.relatorio_whatsapp is not distinct from old.relatorio_whatsapp
     and new.notificacao_email_criticos is not distinct from old.notificacao_email_criticos
     and new.motorista is not distinct from old.motorista
     and new.motorista_registra is not distinct from old.motorista_registra
     and new.motorista_localizacao is not distinct from old.motorista_localizacao
     and new.vigilante_km is not distinct from old.vigilante_km
     and new.autorizador_decide_propria is not distinct from old.autorizador_decide_propria
     and new.autorizador_km is not distinct from old.autorizador_km
     and new.notificacao_push is not distinct from old.notificacao_push
     and new.indicador_conectividade is not distinct from old.indicador_conectividade
     and new.feedback_tatil is not distinct from old.feedback_tatil
     and new.leitor_qr_veiculos is not distinct from old.leitor_qr_veiculos
     and new.voz_campos is not distinct from old.voz_campos
     and new.passkeys is not distinct from old.passkeys
     and new.motorista_km is not distinct from old.motorista_km then
    return new;
  end if;

  perform privado.exigir_admin_ativo(v_ator);

  if new.abastecimento_manutencao and not new.quilometragem then
    raise exception 'Abastecimento e manutenção exigem a Quilometragem ligada.'
      using errcode = 'AG010';
  end if;

  if new.motorista_registra and not new.motorista then
    raise exception 'O registro de saída e chegada pelo motorista exige o Perfil Motorista ligado.'
      using errcode = 'AG010';
  end if;

  if new.motorista_localizacao and not new.motorista_registra then
    raise exception 'A localização do motorista exige o registro de saída e chegada pelo motorista ligado.'
      using errcode = 'AG010';
  end if;

  if new.vigilante_km and not new.quilometragem then
    if old.quilometragem and old.vigilante_km then
      -- Caminho inverso (achado B4): desligar a Quilometragem com o KM do vigilante ligado.
      raise exception 'Desligue o KM do vigilante antes de desligar a Quilometragem.'
        using errcode = 'AG010';
    end if;
    raise exception 'O KM do vigilante exige a Quilometragem ligada.'
      using errcode = 'AG010';
  end if;

  if new.autorizador_km and not new.quilometragem then
    if old.quilometragem and old.autorizador_km then
      -- Caminho inverso: desligar a Quilometragem com o KM do autorizador ligado.
      raise exception 'Desligue o KM do autorizador antes de desligar a Quilometragem.'
        using errcode = 'AG010';
    end if;
    raise exception 'O KM do autorizador exige a Quilometragem ligada.'
      using errcode = 'AG010';
  end if;

  -- NOVO: KM pelo motorista exige Quilometragem E registro de saida e chegada pelo motorista.
  if new.motorista_km and not new.quilometragem then
    if old.quilometragem and old.motorista_km then
      -- Caminho inverso: desligar a Quilometragem com o KM do motorista ligado.
      raise exception 'Desligue o KM do motorista antes de desligar a Quilometragem.'
        using errcode = 'AG010';
    end if;
    raise exception 'O KM do motorista exige a Quilometragem ligada.'
      using errcode = 'AG010';
  end if;

  if new.motorista_km and not new.motorista_registra then
    if old.motorista_registra and old.motorista_km then
      -- Caminho inverso: desligar o registro pelo motorista com o KM do motorista ligado.
      raise exception 'Desligue o KM do motorista antes de desligar o registro de saída e chegada pelo motorista.'
        using errcode = 'AG010';
    end if;
    raise exception 'O KM do motorista exige o registro de saída e chegada pelo motorista ligado.'
      using errcode = 'AG010';
  end if;

  for v_chave in
    select chave
      from jsonb_object_keys(to_jsonb(new)) as chave
     where chave = any (array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa',
                              'relatorio_email', 'relatorio_whatsapp', 'notificacao_email_criticos',
                              'motorista', 'motorista_registra', 'motorista_localizacao',
                              'vigilante_km', 'autorizador_decide_propria', 'autorizador_km',
                              'notificacao_push',
                              'indicador_conectividade', 'feedback_tatil', 'leitor_qr_veiculos',
                              'voz_campos', 'passkeys',
                              'motorista_km'])
       and (to_jsonb(new) -> chave) is distinct from (to_jsonb(old) -> chave)
  loop
    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
    values ('configuracoes', null, 'configuracao_alterada', v_ator,
            to_jsonb(old) ->> v_chave, to_jsonb(new) ->> v_chave,
            jsonb_build_object('recurso', v_chave));
  end loop;

  return new;
end;
$$;

revoke all on function public.configuracoes_validar() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 3. requisicoes_validar_km (KM obrigatorio por papel do ator; + ramo motorista)
-- ---------------------------------------------------------------------------
create or replace function public.requisicoes_validar_km()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ligado   boolean;
  v_vig_km   boolean;
  v_aut_km   boolean;
  v_mot_km   boolean;
  v_role     public.role_usuario;
  v_obriga   boolean := false;
begin
  if tg_op = 'INSERT' then
    return new;  -- nasce 'solicitada': os CHECKs recusam qualquer km.
  end if;

  -- Gravado uma vez, imutavel.
  if (old.km_saida is not null and new.km_saida is distinct from old.km_saida)
     or (old.km_chegada is not null and new.km_chegada is distinct from old.km_chegada) then
    raise exception 'Quilometragem já gravada não pode mudar.' using errcode = 'AG004';
  end if;

  -- Sem mudar de status, km nao muda (so entra junto da transicao, que e auditada).
  if new.status = old.status then
    if new.km_saida is distinct from old.km_saida
       or new.km_chegada is distinct from old.km_chegada then
      raise exception 'Quilometragem só é registrada junto da saída ou da chegada.'
        using errcode = 'AG004';
    end if;
    return new;
  end if;

  select quilometragem, vigilante_km, autorizador_km, motorista_km
    into v_ligado, v_vig_km, v_aut_km, v_mot_km
    from public.configuracoes where id;
  if coalesce(v_ligado, false) then
    -- A obrigatoriedade depende do PAPEL do ator da transicao. O ator (saida_registrada_por em em_uso,
    -- chegada_registrada_por em concluida) ja foi validado por trg_requisicoes_validar, que dispara antes
    -- deste gatilho (ordem alfabetica dos gatilhos BEFORE). Motorista: obrigado so com motorista_km
    -- (o CHECK de configuracoes garante motorista_km => quilometragem e motorista_registra).
    -- Demais papeis: nunca obrigados.
    select p.role into v_role
      from public.perfis p
     where p.id = public.requisicao_ator(new);
    v_obriga := case
                  when v_role = 'vigilante' then coalesce(v_vig_km, false)
                  when v_role in ('autorizador', 'admin') then coalesce(v_aut_km, false)
                  when v_role = 'motorista' then coalesce(v_mot_km, false)
                  else false
                end;

    if v_obriga then
      if new.status = 'em_uso' and new.km_saida is null then
        raise exception 'Quilometragem de saída é obrigatória.' using errcode = 'AG012';
      end if;
      if new.status = 'concluida' and (new.km_saida is null or new.km_chegada is null) then
        raise exception 'Quilometragem de saída e de chegada é obrigatória.' using errcode = 'AG012';
      end if;
    end if;
  end if;

  return new;
end;
$$;

revoke all on function public.requisicoes_validar_km() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 4a. privado.dados_colunas (copia fiel de 20261123120000 + motorista_km em configuracoes)
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
      'autorizador_km', 'indicador_conectividade', 'feedback_tatil', 'leitor_qr_veiculos', 'voz_campos',
      'motorista_km']
  end;
$$;

revoke all on function privado.dados_colunas(text) from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 4b. importar_dados (redefinicao completa de 20261127120000; so ganha motorista_km no UPDATE de configuracoes)
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
                 motorista_km = v_cfg_novo.motorista_km,
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
-- 5. Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v_oid   oid;
  v_def   text;
  v_chave text;
  v_msg   text;
begin
  -- Coluna e CHECK
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'configuracoes'
                    and column_name = 'motorista_km' and data_type = 'boolean'
                    and is_nullable = 'NO' and column_default = 'false') then
    raise exception 'configuracoes.motorista_km deve ser boolean not null default false.';
  end if;
  if exists (select 1 from public.configuracoes
              where motorista_km and not (quilometragem and motorista_registra)) then
    raise exception 'configuracoes com motorista_km sem quilometragem ou sem motorista_registra.';
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'public.configuracoes'::regclass
                    and conname = 'ck_configuracoes_motorista_km_exige_dependencias'
                    and contype = 'c' and convalidated) then
    raise exception 'ck_configuracoes_motorista_km_exige_dependencias ausente.';
  end if;
  if not exists (select 1 from pg_class where relname = 'configuracoes' and relrowsecurity
                    and relnamespace = 'public'::regnamespace) then
    raise exception 'RLS desligado em public.configuracoes.';
  end if;

  -- configuracoes_validar: 20 chaves, mensagens AG010 (incl. caminho inverso), grants e gatilho
  v_oid := to_regprocedure('public.configuracoes_validar()');
  v_def := pg_get_functiondef(v_oid);
  foreach v_chave in array array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa', 'relatorio_email', 'relatorio_whatsapp', 'notificacao_email_criticos', 'motorista', 'motorista_registra', 'motorista_localizacao', 'vigilante_km', 'autorizador_decide_propria', 'autorizador_km', 'notificacao_push', 'indicador_conectividade', 'feedback_tatil', 'leitor_qr_veiculos', 'voz_campos', 'passkeys', 'motorista_km']
  loop
    if position('''' || v_chave || '''' in v_def) = 0 then
      raise exception 'configuracoes_validar nao audita %.', v_chave;
    end if;
  end loop;
  if position('new.motorista_km is not distinct from old.motorista_km' in v_def) = 0 then
    raise exception 'configuracoes_validar sem motorista_km no early-return.';
  end if;
  foreach v_msg in array array[
    'Desligue o KM do vigilante antes de desligar a Quilometragem.',
    'Desligue o KM do autorizador antes de desligar a Quilometragem.',
    'O KM do motorista exige a Quilometragem ligada.',
    'O KM do motorista exige o registro de saída e chegada pelo motorista ligado.',
    'Desligue o KM do motorista antes de desligar a Quilometragem.',
    'Desligue o KM do motorista antes de desligar o registro de saída e chegada pelo motorista.']
  loop
    if position(v_msg in v_def) = 0 then
      raise exception 'configuracoes_validar sem a mensagem AG010: %', v_msg;
    end if;
  end loop;
  if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'configuracoes_validar executavel pela API.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.configuracoes'::regclass
                  and tgname = 'trg_configuracoes_validar' and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'trg_configuracoes_validar ausente ou desabilitado.';
  end if;

  -- requisicoes_validar_km: ramo motorista + regras anteriores
  v_oid := to_regprocedure('public.requisicoes_validar_km()');
  v_def := pg_get_functiondef(v_oid);
  if position('motorista_km' in v_def) = 0 or position('autorizador_km' in v_def) = 0
     or position('vigilante_km' in v_def) = 0
     or position('AG012' in v_def) = 0 or position('AG004' in v_def) = 0 then
    raise exception 'requisicoes_validar_km sem a regra por papel (motorista/vigilante/autorizador), AG012 ou AG004.';
  end if;
  if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'requisicoes_validar_km executavel pela API.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.requisicoes'::regclass
                  and tgname = 'trg_requisicoes_validar_km' and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'trg_requisicoes_validar_km ausente ou desabilitado.';
  end if;

  -- Exportacao/importacao
  if not ('motorista_km' = any (privado.dados_colunas('configuracoes'))) then
    raise exception 'privado.dados_colunas(configuracoes) sem motorista_km.';
  end if;
  if not ('motorista_km' = any (privado.dados_colunas_cfg_importar())) then
    raise exception 'privado.dados_colunas_cfg_importar sem motorista_km.';
  end if;
  if 'mfa' = any (privado.dados_colunas('configuracoes')) or 'passkeys' = any (privado.dados_colunas('configuracoes'))
     or 'notificacao_push' = any (privado.dados_colunas('configuracoes')) then
    raise exception 'privado.dados_colunas(configuracoes) exporta flag de seguranca (regressao).';
  end if;
  if has_function_privilege('anon', 'privado.dados_colunas(text)'::regprocedure, 'execute')
     or has_function_privilege('authenticated', 'privado.dados_colunas(text)'::regprocedure, 'execute')
     or has_function_privilege('service_role', 'privado.dados_colunas(text)'::regprocedure, 'execute') then
    raise exception 'privado.dados_colunas executavel pela API.';
  end if;

  v_oid := to_regprocedure('public.importar_dados(jsonb, uuid, boolean)');
  v_def := pg_get_functiondef(v_oid);
  if position('motorista_km = v_cfg_novo.motorista_km' in v_def) = 0
     or position('''km_percorrido''' in v_def) = 0
     or position('dados_validar_checklist' in v_def) = 0 or position('dados_validar_localizacao' in v_def) = 0
     or position('dados_validar_requisicao' in v_def) = 0 or position('configuracoes_alteradas' in v_def) = 0
     or position('dados_colunas_cfg_importar' in v_def) = 0 then
    raise exception 'importar_dados sem motorista_km no UPDATE de configuracoes ou sem as regras vigentes.';
  end if;
  if not (select prosecdef and 'search_path=""' = any (proconfig) from pg_proc where oid = v_oid)
     or has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('public', v_oid, 'execute')
     or not has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'importar_dados com SECURITY DEFINER/search_path/grants divergentes.';
  end if;
  if exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
              where n.nspname = 'public' and p.proname = 'importar_dados'
              group by p.proname having count(*) > 1) then
    raise exception 'Sobrecarga duplicada em importar_dados.';
  end if;
end
$$;

notify pgrst, 'reload schema';
