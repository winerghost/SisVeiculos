-- Migration: flag autorizador_decide_propria + defesa no banco (AG040) contra autoautorizacao.
-- Nova migration; nao edita as anteriores. Idempotente. Depende de 20261117120200 (requisicoes_validar) e
-- 20261118120100 (configuracoes_validar, 11 chaves).
--
-- Motivo (decisao do usuario, 2026-11-12, item 1): hoje a regra "quem pede nao aprova" vive so no backend e
-- na env PERMITIR_AUTOAUTORIZACAO (sem auditoria, exige reinicio). Passa a ser o recurso opcional
-- configuracoes.autorizador_decide_propria (default false, CLAUDE.md "Recursos opcionais"): autorizador e admin
-- so autorizam/negam a PROPRIA requisicao se estiver ligada; o banco tambem barra (item 8: defesa no banco).
--
-- 1. configuracoes.autorizador_decide_propria boolean not null default false. Flag independente (sem CHECK de
--    dependencia, sem AG010).
-- 2. configuracoes_validar: copia fiel da versao de 20261118120100 (AG002 sem admin ativo, ator zerado, AG010 e
--    mensagens, uma linha 'configuracao_alterada' por chave) com a 12a chave na condicao "nada mudou" e na
--    lista auditada. As 11 anteriores sao preservadas.
-- 3. requisicoes_validar: copia fiel da versao de 20261117120200 (imutabilidade, carimbos, congelamento,
--    AG001..AG011, motorista AG034..AG036, vigilante). UNICA mudanca: autorizada/negada com
--    autorizado_por = usuario_id da requisicao, sem a flag ligada => AG040. Cancelar a propria nao muda.
--
-- Erro novo: AG040 "Você não pode decidir sobre a sua própria requisição." (AG037 NAO serve: ja e usado por
-- localizacoes_viagem; AG040 era o proximo livre). Mensagem fixa, sem dado pessoal.
-- Ordem de deploy: esta migration ANTES do backend novo (o backend seleciona a coluna nova).
-- Fuso: sem colunas de data/hora. RLS/grants: nao mudam (tabela sem politicas para anon/authenticated).

-- ---------------------------------------------------------------------------
-- 1. Coluna
-- ---------------------------------------------------------------------------
alter table public.configuracoes
  add column if not exists autorizador_decide_propria boolean not null default false;

comment on column public.configuracoes.autorizador_decide_propria is
  'Recurso opcional: autorizador e admin podem autorizar ou negar a propria requisicao. Desligado por padrao.';

-- ---------------------------------------------------------------------------
-- 2. configuracoes_validar (12 chaves)
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
     and new.autorizador_decide_propria is not distinct from old.autorizador_decide_propria then
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

  for v_chave in
    select chave
      from jsonb_object_keys(to_jsonb(new)) as chave
     where chave = any (array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa',
                              'relatorio_email', 'relatorio_whatsapp', 'notificacao_email_criticos',
                              'motorista', 'motorista_registra', 'motorista_localizacao',
                              'vigilante_km', 'autorizador_decide_propria'])
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
-- 3. requisicoes_validar (unica mudanca: AG040)
-- ---------------------------------------------------------------------------
create or replace function public.requisicoes_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator          uuid;
  v_role          public.role_usuario;
  v_ativo         boolean;
  v_veiculo_ativo boolean;
  v_mot           boolean;
  v_reg           boolean;
  v_loc           boolean;
  v_propria       boolean;
begin
  if tg_op = 'INSERT' then
    if new.status <> 'solicitada' then
      raise exception 'Requisição nova deve nascer como solicitada.' using errcode = 'AG001';
    end if;
    if new.autorizado_em is not null or new.saida_real_em is not null
       or new.chegada_real_em is not null or new.cancelada_em is not null then
      raise exception 'Carimbos de data/hora são definidos pelo servidor.' using errcode = 'AG004';
    end if;
    new.criado_em := clock_timestamp();

    select ativo into v_ativo from public.perfis where id = new.usuario_id for share;
    if not found or not v_ativo then
      raise exception 'Usuário solicitante inexistente ou inativo.' using errcode = 'AG002';
    end if;

    select ativo into v_veiculo_ativo from public.veiculos where id = new.veiculo_id for share;
    if not found or not v_veiculo_ativo then
      raise exception 'Veículo inexistente ou inativo.' using errcode = 'AG011';
    end if;
    return new;
  end if;

  -- UPDATE ------------------------------------------------------------------
  if new.id is distinct from old.id
     or new.usuario_id is distinct from old.usuario_id
     or new.criado_em is distinct from old.criado_em then
    raise exception 'Campos id, usuario_id e criado_em são imutáveis.' using errcode = 'AG004';
  end if;

  -- Estados terminais: linha congelada (atualizado_em e ajustado pelo outro trigger).
  if old.status in ('negada', 'cancelada', 'concluida') then
    if (to_jsonb(new) - 'atualizado_em') is distinct from (to_jsonb(old) - 'atualizado_em') then
      raise exception 'Requisição encerrada (%) não pode ser alterada.', old.status
        using errcode = 'AG001';
    end if;
    return new;
  end if;

  -- Carimbos nunca vem do chamador: qualquer diferenca em relacao a linha atual e recusada
  -- (o servidor os preenche abaixo, apos esta checagem).
  if new.autorizado_em   is distinct from old.autorizado_em
     or new.saida_real_em   is distinct from old.saida_real_em
     or new.chegada_real_em is distinct from old.chegada_real_em
     or new.cancelada_em    is distinct from old.cancelada_em then
    raise exception 'Carimbos de data/hora são definidos pelo servidor.' using errcode = 'AG004';
  end if;

  -- Campos descritivos so mudam enquanto 'solicitada'.
  if old.status <> 'solicitada' and (
        new.hora_saida       is distinct from old.hora_saida
     or new.trajeto          is distinct from old.trajeto
     or new.unidade          is distinct from old.unidade
     or new.nome_solicitante is distinct from old.nome_solicitante
     or new.observacao       is distinct from old.observacao) then
    raise exception 'Dados da requisição não podem mudar após a decisão do autorizador.'
      using errcode = 'AG004';
  end if;

  -- Veiculo so muda enquanto 'solicitada' e o novo precisa estar ativo.
  if new.veiculo_id is distinct from old.veiculo_id then
    if old.status <> 'solicitada' then
      raise exception 'O veículo não pode mudar após a decisão do autorizador.' using errcode = 'AG004';
    end if;
    select ativo into v_veiculo_ativo from public.veiculos where id = new.veiculo_id for share;
    if not found or not v_veiculo_ativo then
      raise exception 'Veículo inexistente ou inativo.' using errcode = 'AG011';
    end if;
  end if;

  -- Colunas de autorizacao, saida, chegada e cancelamento: gravadas uma vez, imutaveis.
  if (old.autorizado_por         is not null and new.autorizado_por         is distinct from old.autorizado_por)
  or (old.saida_registrada_por   is not null and new.saida_registrada_por   is distinct from old.saida_registrada_por)
  or (old.chegada_registrada_por is not null and new.chegada_registrada_por is distinct from old.chegada_registrada_por)
  or (old.cancelada_por          is not null and new.cancelada_por          is distinct from old.cancelada_por)
  then
    raise exception 'Dados de autorização, saída, chegada e cancelamento já gravados não podem mudar.'
      using errcode = 'AG004';
  end if;

  if new.status = old.status then
    -- Sem mudar de status, os responsaveis nao podem ser preenchidos (so na transicao).
    if new.autorizado_por         is distinct from old.autorizado_por
       or new.saida_registrada_por   is distinct from old.saida_registrada_por
       or new.chegada_registrada_por is distinct from old.chegada_registrada_por
       or new.cancelada_por          is distinct from old.cancelada_por then
      raise exception 'Responsáveis só são registrados junto da transição de status.'
        using errcode = 'AG004';
    end if;
    return new;  -- edicao de campos descritivos; CHECKs cuidam da coerencia.
  end if;

  -- Transicao permitida?
  if not (case old.status
            when 'solicitada' then new.status in ('autorizada', 'negada', 'cancelada')
            when 'autorizada' then new.status in ('em_uso', 'cancelada')
            when 'em_uso'     then new.status = 'concluida'
            else false
          end) then
    raise exception 'Transição inválida: % -> %.', old.status, new.status using errcode = 'AG001';
  end if;

  -- Ator da transicao: existe, esta ativo e tem papel adequado (FOR SHARE: nao pode
  -- ser desativado/rebaixado por outra transacao enquanto esta nao terminar).
  v_ator := public.requisicao_ator(new);
  if v_ator is null then
    raise exception 'Transição para % exige o responsável registrado.', new.status using errcode = 'AG002';
  end if;

  select role, ativo into v_role, v_ativo from public.perfis where id = v_ator for share;
  if not found or not v_ativo then
    raise exception 'Responsável pela transição inexistente ou inativo.' using errcode = 'AG002';
  end if;

  if new.status = 'cancelada' then
    -- NOVO: vigilante nunca cancela (nem a propria requisicao), contrato do plano vigilante (secao 3).
    if v_role = 'vigilante' then
      raise exception 'O vigilante não autoriza, nega nem cancela; só registra saída ou chegada.'
        using errcode = 'AG002';
    end if;
    -- admin/autorizador cancelam solicitada ou autorizada; solicitante so a propria e so 'solicitada'.
    if not (v_role in ('admin', 'autorizador')
            or (v_ator = old.usuario_id and old.status = 'solicitada')) then
      raise exception 'Sem permissão para cancelar esta requisição.' using errcode = 'AG002';
    end if;
  elsif v_role = 'motorista' then
    -- NOVO: o motorista so registra saida (em_uso) ou chegada (concluida), e so da PROPRIA viagem.
    if new.status not in ('em_uso', 'concluida') then
      raise exception 'Apenas autorizador ou admin pode autorizar ou negar; o motorista só registra saída ou chegada.'
        using errcode = 'AG002';
    end if;

    select motorista, motorista_registra, motorista_localizacao into v_mot, v_reg, v_loc
      from public.configuracoes where id;  -- sem FOR SHARE (convencao das migrations 18/22)
    if not (coalesce(v_mot, false) and coalesce(v_reg, false)) then
      raise exception 'O registro de saída e chegada pelo motorista está desligado em Configurações.'
        using errcode = 'AG034';
    end if;

    if old.motorista_id is distinct from v_ator or new.motorista_id is distinct from old.motorista_id then
      raise exception 'Somente o motorista designado nesta requisição registra a saída ou a chegada.'
        using errcode = 'AG035';
    end if;

    if coalesce(v_loc, false)
       and not exists (select 1 from public.localizacoes_viagem l
                        where l.requisicao_id = new.id
                          and l.momento = case new.status when 'em_uso' then 'saida' else 'chegada' end
                          and l.registrado_por = v_ator) then
      raise exception 'Latitude e longitude são obrigatórias para o motorista registrar a saída ou a chegada.'
        using errcode = 'AG036';
    end if;
  elsif v_role = 'vigilante' then
    -- NOVO: o vigilante registra saida (em_uso) ou chegada (concluida) de QUALQUER requisicao; sem flag e sem
    -- exigir designacao (diferente do motorista). Autorizar/negar => AG002. Fonte unica: perfis.role.
    if new.status not in ('em_uso', 'concluida') then
      raise exception 'O vigilante não autoriza, nega nem cancela; só registra saída ou chegada.'
        using errcode = 'AG002';
    end if;
  elsif v_role not in ('autorizador', 'admin') then
    raise exception 'Apenas autorizador ou admin pode autorizar, negar ou registrar saída/chegada.'
      using errcode = 'AG002';
  end if;

  -- NOVO (AG040): autorizar ou negar a PROPRIA requisicao so vale com autorizador_decide_propria ligada.
  -- Aqui so chegam autorizador/admin para autorizada/negada (motorista e vigilante ficam restritos a
  -- em_uso/concluida acima). v_ator e quem decide (autorizado_por); old.usuario_id e o dono (imutavel).
  if new.status in ('autorizada', 'negada') and v_ator = old.usuario_id then
    select autorizador_decide_propria into v_propria
      from public.configuracoes where id;  -- sem FOR SHARE (convencao das migrations 18/22)
    if not coalesce(v_propria, false) then
      raise exception 'Você não pode decidir sobre a sua própria requisição.' using errcode = 'AG040';
    end if;
  end if;

  -- Carimbos do servidor (sobrescrevem sempre).
  case new.status
    when 'autorizada', 'negada' then new.autorizado_em := clock_timestamp();
    when 'em_uso'               then new.saida_real_em := clock_timestamp();
    when 'concluida'            then new.chegada_real_em := clock_timestamp();
    when 'cancelada'            then new.cancelada_em := clock_timestamp();
    else null;
  end case;

  return new;
end;
$$;

revoke all on function public.requisicoes_validar() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v_oid   oid;
  v_def   text;
  v_chave text;
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'configuracoes'
                    and column_name = 'autorizador_decide_propria' and data_type = 'boolean'
                    and is_nullable = 'NO' and column_default = 'false') then
    raise exception 'configuracoes.autorizador_decide_propria deve ser boolean not null default false.';
  end if;

  v_oid := to_regprocedure('public.configuracoes_validar()');
  v_def := pg_get_functiondef(v_oid);
  foreach v_chave in array array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa', 'relatorio_email', 'relatorio_whatsapp', 'notificacao_email_criticos', 'motorista', 'motorista_registra', 'motorista_localizacao', 'vigilante_km', 'autorizador_decide_propria']
  loop
    if position('''' || v_chave || '''' in v_def) = 0 then
      raise exception 'configuracoes_validar nao audita %.', v_chave;
    end if;
  end loop;
  if position('Desligue o KM do vigilante' in v_def) = 0 then
    raise exception 'configuracoes_validar sem a mensagem do caminho inverso (regressao).';
  end if;
  if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'configuracoes_validar executavel pela API.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.configuracoes'::regclass
                  and tgname = 'trg_configuracoes_validar' and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'trg_configuracoes_validar ausente ou desabilitado.';
  end if;

  v_oid := to_regprocedure('public.requisicoes_validar()');
  v_def := pg_get_functiondef(v_oid);
  if position('AG040' in v_def) = 0 or position('autorizador_decide_propria' in v_def) = 0
     or position('vigilante' in v_def) = 0 or position('AG034' in v_def) = 0
     or position('AG035' in v_def) = 0 or position('AG036' in v_def) = 0 then
    raise exception 'requisicoes_validar sem AG040 ou com regra anterior perdida (regressao).';
  end if;
  if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'requisicoes_validar executavel pela API.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.requisicoes'::regclass
                  and tgname = 'trg_requisicoes_validar' and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'trg_requisicoes_validar ausente ou desabilitado.';
  end if;
end
$$;
