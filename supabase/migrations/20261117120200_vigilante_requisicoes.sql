-- Migration: o vigilante registra saida/chegada + KM conforme vigilante_km (parte 3 de 3 do Perfil Vigilante).
-- Nova migration; nao edita as anteriores. Idempotente. Depende de 20261117120000 (enum) e 20261117120100 (flag).
-- Substitui requisicoes_validar (versao de 20261112120200) e requisicoes_validar_km (versao de 20261107121800),
-- preservando TODAS as regras delas (imutabilidade, carimbos do servidor, congelamento de encerradas, AG001..AG011,
-- ramo do motorista AG034..AG036, km imutavel AG004). Gatilhos de checklist (AG013) e motorista (AG031..AG033)
-- seguem como estao.
--
-- 1. requisicoes_validar: unica mudanca = ramo novo para v_role = 'vigilante':
--      - so autorizada->em_uso (saida) e em_uso->concluida (chegada), de QUALQUER requisicao;
--      - autorizar, negar e cancelar => AG002 (inclusive cancelar a propria solicitada: contrato do plano, secao 3);
--      - SEM flag em configuracoes (sempre ativo) e SEM exigir designacao de motorista (nao ha AG034/AG035/AG036).
--    autorizador/admin/motorista: comportamento identico ao anterior.
--
-- 2. KM x quilometragem x vigilante_km (regra escolhida, a mais simples e coerente):
--      quilometragem desligada ........ KM nao e exigido de ninguem (igual a hoje); vigilante_km nao pode estar
--                                       ligada (CHECK da migration 20261117120100).
--      quilometragem ligada, ator != vigilante ........ KM de saida/chegada obrigatorio (AG012), igual a hoje.
--      quilometragem ligada, ator = vigilante, vigilante_km LIGADA ...... KM obrigatorio (AG012), como os demais.
--      quilometragem ligada, ator = vigilante, vigilante_km DESLIGADA ... o AG012 NAO vale: o registro passa e o
--          KM fica nulo (o vigilante nao lanca KM). Evita bloquear saida/chegada de forma incoerente: o KM e
--          imutavel e so entra junto da transicao, entao ninguem poderia completa-lo depois. Se o vigilante
--          enviar KM mesmo assim, o banco aceita (os CHECKs de coerencia seguem valendo); o backend ignora o
--          campo com a flag desligada.
--    O ator vem de requisicao_ator(new) (quem registra a saida/chegada), nunca do cliente. Desligar vigilante_km
--    nunca apaga KM ja gravado (imutavel). Mistura permitida: saida pelo autorizador (com KM) e chegada pelo
--    vigilante sem KM, com vigilante_km desligada.
-- Fuso: timestamptz (UTC; exibicao America/Sao_Paulo). RLS/grants de tabelas: nao mudam.

-- ---------------------------------------------------------------------------
-- 1. requisicoes_validar (substitui a de 20261112120200; unico ramo novo: vigilante)
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
-- 2. requisicoes_validar_km (substitui a de 20261107121800; unica mudanca: dispensa do vigilante sem vigilante_km)
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
  v_role     public.role_usuario;
  v_dispensa boolean := false;
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

  select quilometragem, vigilante_km into v_ligado, v_vig_km from public.configuracoes where id;
  if coalesce(v_ligado, false) then
    -- NOVO: vigilante com vigilante_km desligada nao lanca KM; o AG012 nao o bloqueia.
    -- O ator (saida_registrada_por em em_uso, chegada_registrada_por em concluida) ja foi validado por
    -- trg_requisicoes_validar, que dispara antes deste gatilho (ordem alfabetica dos gatilhos BEFORE).
    select p.role into v_role
      from public.perfis p
     where p.id = public.requisicao_ator(new);
    v_dispensa := v_role = 'vigilante' and not coalesce(v_vig_km, false);

    if not v_dispensa then
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
-- Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v_oid oid;
begin
  v_oid := to_regprocedure('public.requisicoes_validar()');
  if position('vigilante' in pg_get_functiondef(v_oid)) = 0
     or position('AG034' in pg_get_functiondef(v_oid)) = 0 then
    raise exception 'requisicoes_validar sem o ramo do vigilante ou sem o do motorista (regressao).';
  end if;
  if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'requisicoes_validar executavel pela API.';
  end if;

  v_oid := to_regprocedure('public.requisicoes_validar_km()');
  if position('vigilante_km' in pg_get_functiondef(v_oid)) = 0
     or position('AG012' in pg_get_functiondef(v_oid)) = 0 then
    raise exception 'requisicoes_validar_km sem a regra do vigilante ou sem AG012 (regressao).';
  end if;
  if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'requisicoes_validar_km executavel pela API.';
  end if;

  if not exists (select 1 from pg_trigger where tgrelid = 'public.requisicoes'::regclass
                  and tgname = 'trg_requisicoes_validar' and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'trg_requisicoes_validar ausente ou desabilitado.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.requisicoes'::regclass
                  and tgname = 'trg_requisicoes_validar_km' and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'trg_requisicoes_validar_km ausente ou desabilitado.';
  end if;
end
$$;
