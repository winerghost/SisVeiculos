-- Migration: o motorista registra saida/chegada + RPC atomica com localizacao (parte 3 de 4).
-- Nova migration; nao edita as anteriores. Idempotente. Depende de 20261112120100_localizacoes_viagem.sql.
-- Resolve a pendencia registrada em 20261111120100 ("requisicoes_validar continua exigindo
-- autorizador/admin"). Substitui requisicoes_validar (versao de 20261107121000), preservando TODAS as
-- regras dela (imutabilidade, carimbos do servidor, congelamento de encerradas, AG001..AG011) e
-- dos gatilhos posteriores (km AG012, checklist AG013, motorista AG031..AG033), que seguem como estao.
--
-- 1. requisicoes_validar: unica mudanca = ramo novo para v_role = 'motorista' na transicao:
--      - so autorizada->em_uso (saida) e em_uso->concluida (chegada); demais transicoes => AG002;
--      - configuracoes.motorista E motorista_registra ligados; senao AG034;
--      - ator = requisicoes.motorista_id ANTIGO (OLD) e a designacao nao muda na mesma gravacao;
--        senao AG035;
--      - com motorista_localizacao ligada, exige linha em localizacoes_viagem do mesmo momento gravada
--        por este motorista; senao AG036 (coordenadas obrigatorias; sem excecao). Como essa linha so
--        nasce dentro da RPC abaixo, uma gravacao direta do motorista nao passa.
--    autorizador/admin: comportamento identico ao anterior (nao coletam localizacao, mesmo com motorista
--    designado e recurso ligado). Cancelamento por motorista continua recusado.
--
-- 2. RPC public.registrar_transicao_motorista(p_requisicao_id, p_momento, p_ator, p_latitude, p_longitude,
--      p_precisao_m, p_km_saida, p_km_chegada) returns jsonb {"status", "localizacao_gravada"}.
--    DECISAO DE ATOMICIDADE (RPC em vez de gatilho deferido):
--      * o backend fala com o banco por PostgREST (supabase-py): cada chamada e uma transacao propria;
--        um gatilho deferido nunca veria a localizacao gravada "depois" em outra chamada e quebraria
--        toda saida/chegada do motorista. Uma RPC = uma transacao: tudo ou nada.
--      * e a opcao mais simples: um unico ponto de entrada, mesmo padrao de registrar_checklist.
--      * seguranca em camadas: (a) a RPC trava a requisicao (FOR UPDATE; duas chamadas simultaneas
--        serializam e a segunda falha por status); (b) grava a localizacao ANTES do UPDATE; (c) o gatilho
--        requisicoes_validar exige a existencia dessa linha (dado, nao flag); (d) o gatilho de
--        localizacoes_viagem so aceita INSERT com o marcador transacional posto aqui. Falhou o UPDATE
--        (km AG012, checklist AG013 etc.), a localizacao some junto (rollback).
--    Com motorista_localizacao DESLIGADA a RPC ignora latitude/longitude/precisao (nao grava nada) e faz so
--    a transicao. O backend pode usar a RPC sempre para o motorista.
--    SECURITY DEFINER, search_path vazio, EXECUTE so para service_role (revogado de public/anon/authenticated).
--    Checklist: com o recurso ligado o checklist do momento precisa existir antes (AG013), gravado pelo
--    motorista via registrar_checklist (migration 20261112120300).
--    Erros novos: AG034 recurso desligado; AG035 nao e o motorista designado; AG036 coordenadas ausentes ou
--    invalidas; AG037 momento invalido, requisicao inexistente ou gravacao fora do fluxo. Mensagens fixas,
--    sem coordenadas. Reaproveitados: AG001 status incompativel; AG002 ator invalido; AG004 km imutavel.
-- Fuso: timestamptz (UTC; exibicao America/Sao_Paulo). RLS/grants de tabelas: nao mudam.

-- ---------------------------------------------------------------------------
-- 1. requisicoes_validar (substitui a de 20261107121000; unico ramo novo: motorista)
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
-- 2. RPC atomica: transicao do motorista + localizacao na mesma transacao
-- ---------------------------------------------------------------------------
create or replace function public.registrar_transicao_motorista(
  p_requisicao_id uuid,
  p_momento       text,
  p_ator          uuid,
  p_latitude      numeric default null,
  p_longitude     numeric default null,
  p_precisao_m    numeric default null,
  p_km_saida      integer default null,
  p_km_chegada    integer default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_status       public.status_requisicao;
  v_motorista_id uuid;
  v_role         public.role_usuario;
  v_ativo        boolean;
  v_mot          boolean;
  v_reg          boolean;
  v_loc          boolean;
  v_novo         public.status_requisicao;
begin
  if p_momento is null or p_momento not in ('saida', 'chegada') or p_requisicao_id is null or p_ator is null then
    raise exception 'Momento inválido: use saida ou chegada.' using errcode = 'AG037';
  end if;

  select status, motorista_id into v_status, v_motorista_id
    from public.requisicoes where id = p_requisicao_id for update;
  if not found then
    raise exception 'Requisição inexistente.' using errcode = 'AG037';
  end if;

  select role, ativo into v_role, v_ativo from public.perfis where id = p_ator for share;
  if not found or not v_ativo or v_role is distinct from 'motorista'::public.role_usuario then
    raise exception 'Apenas motorista ativo usa este registro.' using errcode = 'AG002';
  end if;

  select motorista, motorista_registra, motorista_localizacao into v_mot, v_reg, v_loc
    from public.configuracoes where id;
  if not (coalesce(v_mot, false) and coalesce(v_reg, false)) then
    raise exception 'O registro de saída e chegada pelo motorista está desligado em Configurações.'
      using errcode = 'AG034';
  end if;

  if v_motorista_id is distinct from p_ator then
    raise exception 'Somente o motorista designado nesta requisição registra a saída ou a chegada.'
      using errcode = 'AG035';
  end if;

  if not ((p_momento = 'saida' and v_status = 'autorizada')
          or (p_momento = 'chegada' and v_status = 'em_uso')) then
    raise exception 'Registro de % não é possível com a requisição %.', p_momento, v_status
      using errcode = 'AG001';
  end if;

  if coalesce(v_loc, false) then
    if p_latitude is null or p_longitude is null
       or p_latitude = 'NaN'::numeric or p_longitude = 'NaN'::numeric
       or p_latitude not between -90 and 90 or p_longitude not between -180 and 180 then
      raise exception 'Latitude e longitude válidas são obrigatórias para o motorista registrar a saída ou a chegada.'
        using errcode = 'AG036';
    end if;
    if p_precisao_m is not null
       and (p_precisao_m = 'NaN'::numeric or p_precisao_m < 0 or p_precisao_m > 999999) then
      raise exception 'Precisão da localização inválida.' using errcode = 'AG036';
    end if;

    perform set_config('sisveiculos.registro_motorista', p_requisicao_id::text || ':' || p_momento, true);
    insert into public.localizacoes_viagem
      (requisicao_id, momento, latitude, longitude, precisao_m, registrado_por)
    values (p_requisicao_id, p_momento, p_latitude, p_longitude, p_precisao_m, p_ator);
    perform set_config('sisveiculos.registro_motorista', '', true);
  end if;

  if p_momento = 'saida' then
    update public.requisicoes
       set status = 'em_uso',
           saida_registrada_por = p_ator,
           km_saida = coalesce(p_km_saida, km_saida)
     where id = p_requisicao_id
    returning status into v_novo;
  else
    update public.requisicoes
       set status = 'concluida',
           chegada_registrada_por = p_ator,
           km_saida = coalesce(p_km_saida, km_saida),
           km_chegada = coalesce(p_km_chegada, km_chegada)
     where id = p_requisicao_id
    returning status into v_novo;
  end if;

  return jsonb_build_object('status', v_novo::text, 'localizacao_gravada', coalesce(v_loc, false));
end;
$$;

revoke all on function public.registrar_transicao_motorista(uuid, text, uuid, numeric, numeric, numeric, integer, integer)
  from public, anon, authenticated;
grant execute on function public.registrar_transicao_motorista(uuid, text, uuid, numeric, numeric, numeric, integer, integer)
  to service_role;

-- ---------------------------------------------------------------------------
-- Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v_oid oid;
  v_def text;
begin
  v_oid := to_regprocedure('public.requisicoes_validar()');
  v_def := pg_get_functiondef(v_oid);
  if position('AG034' in v_def) = 0 or position('AG035' in v_def) = 0 or position('AG036' in v_def) = 0 then
    raise exception 'requisicoes_validar sem o ramo do motorista.';
  end if;
  if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'requisicoes_validar executavel pela API.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.requisicoes'::regclass
                  and tgname = 'trg_requisicoes_validar' and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'trg_requisicoes_validar ausente ou desabilitado.';
  end if;

  v_oid := to_regprocedure('public.registrar_transicao_motorista(uuid, text, uuid, numeric, numeric, numeric, integer, integer)');
  if v_oid is null then
    raise exception 'registrar_transicao_motorista ausente.';
  end if;
  if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or not has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'registrar_transicao_motorista: EXECUTE so para service_role.';
  end if;
  if exists (select 1 from pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
              where p.oid = v_oid and a.grantee = 0 and a.privilege_type = 'EXECUTE') then
    raise exception 'PUBLIC executa registrar_transicao_motorista.';
  end if;
  if not exists (select 1 from pg_proc where oid = v_oid and prosecdef
                  and proconfig is not null and position('search_path=' in array_to_string(proconfig, ',')) > 0) then
    raise exception 'registrar_transicao_motorista sem SECURITY DEFINER ou search_path fixo.';
  end if;
end
$$;
