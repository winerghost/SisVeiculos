-- Migration: o motorista designado grava o checklist da PROPRIA viagem (parte 4 de 4).
-- Nova migration; nao edita as anteriores. Idempotente. Depende de 20261112120200.
--
-- Motivo: com o recurso Checklist ligado, a saida/chegada exige checklist (AG013). Hoje
-- checklists_validar (versao de 20261107122400) so aceita autorizador/admin como registrado_por (AG002),
-- o que impediria o motorista de concluir o proprio registro. A RPC registrar_checklist insere em
-- checklists (e as fotos em checklist_fotos pelo marcador transacional que este gatilho liga); portanto
-- basta ajustar o gatilho. NADA mais muda: a funcao e copia fiel da versao 22400, exceto o bloco do ator.
--
-- Regra nova (apenas para registrado_por com role 'motorista'):
--   * configuracoes.motorista E motorista_registra ligados; senao AG034;
--   * registrado_por = requisicoes.motorista_id da requisicao (so a PROPRIA viagem); senao AG035.
-- autorizador/admin: igual a antes. Qualquer outro role, inativo ou inexistente: AG002.
-- Fotos: checklist_fotos_validar so exige o marcador do checklist aberto na transacao (nenhuma checagem de
-- role), logo as fotos do motorista passam pela mesma RPC, sem mudanca. Storage: o upload e feito pelo
-- backend (service_role), sem politica por role.
--
-- (2) saida_registrada_por / chegada_registrada_por: as FKs e CHECKs de requisicoes so exigem perfil
-- existente e a paridade com os carimbos; a unica restricao de papel estava em requisicoes_validar
-- (migration 20261112120200). (3) motorista_id aceita NULL de volta (ck_requisicoes_motorista_status e
-- requisicoes_validar_motorista ja permitem remover em autorizada/em_uso, com auditoria
-- 'motorista_designado' de/para em qualquer UPDATE, inclusive vindo do backend).
-- Fuso: nao se aplica.

create or replace function public.checklists_validar()
returns trigger
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
begin
  new.registrado_em := clock_timestamp();  -- carimbo do servidor

  -- Campos obrigatorios (NOT NULL). Mensagem fixa: so o nome do campo, nunca valores.
  if new.id is null then
    raise exception 'Campo obrigatório do checklist ausente: id.'
      using errcode = '23502', schema = 'public', table = 'checklists', column = 'id';
  end if;
  if new.requisicao_id is null then
    raise exception 'Campo obrigatório do checklist ausente: requisicao_id.'
      using errcode = '23502', schema = 'public', table = 'checklists', column = 'requisicao_id';
  end if;
  if new.momento is null then
    raise exception 'Campo obrigatório do checklist ausente: momento.'
      using errcode = '23502', schema = 'public', table = 'checklists', column = 'momento';
  end if;
  if new.combustivel is null then
    raise exception 'Campo obrigatório do checklist ausente: combustivel.'
      using errcode = '23502', schema = 'public', table = 'checklists', column = 'combustivel';
  end if;
  if new.avarias is null then
    raise exception 'Campo obrigatório do checklist ausente: avarias.'
      using errcode = '23502', schema = 'public', table = 'checklists', column = 'avarias';
  end if;
  if new.registrado_por is null then
    raise exception 'Campo obrigatório do checklist ausente: registrado_por.'
      using errcode = '23502', schema = 'public', table = 'checklists', column = 'registrado_por';
  end if;

  -- Mesmas regras dos CHECKs (ordem alfabetica dos nomes, como o Postgres avalia), sem DETAIL.
  if new.avarias then
    if new.descricao_avarias is null or new.descricao_avarias !~ '\S' then
      raise exception 'Violação da regra ck_checklists_avarias_descricao: informe a descrição das avarias.'
        using errcode = '23514', schema = 'public', table = 'checklists',
              column = 'descricao_avarias', constraint = 'ck_checklists_avarias_descricao';
    end if;
  elsif new.descricao_avarias is not null then
    raise exception 'Violação da regra ck_checklists_avarias_descricao: sem avarias não há descrição.'
      using errcode = '23514', schema = 'public', table = 'checklists',
            column = 'descricao_avarias', constraint = 'ck_checklists_avarias_descricao';
  end if;

  if new.descricao_avarias is not null and char_length(new.descricao_avarias) > 500 then
    raise exception 'Violação da regra ck_checklists_descricao_tamanho: máximo de 500 caracteres.'
      using errcode = '23514', schema = 'public', table = 'checklists',
            column = 'descricao_avarias', constraint = 'ck_checklists_descricao_tamanho';
  end if;

  if new.momento not in ('saida', 'chegada') then
    raise exception 'Violação da regra ck_checklists_momento: momento inválido.'
      using errcode = '23514', schema = 'public', table = 'checklists',
            column = 'momento', constraint = 'ck_checklists_momento';
  end if;

  select status, motorista_id into v_status, v_motorista_id
    from public.requisicoes where id = new.requisicao_id for share;
  if not found then
    return new;  -- a FK responde (23503)
  end if;

  if not ((new.momento = 'saida' and v_status = 'autorizada')
          or (new.momento = 'chegada' and v_status = 'em_uso')) then
    raise exception 'Checklist de % não pode ser registrado com a requisição %.', new.momento, v_status
      using errcode = 'AG014';
  end if;

  select role, ativo into v_role, v_ativo from public.perfis where id = new.registrado_por for share;
  if not found or not v_ativo then
    raise exception 'Apenas autorizador, admin ou o motorista designado ativo registra checklist.'
      using errcode = 'AG002';
  end if;

  if v_role = 'motorista' then
    select motorista, motorista_registra into v_mot, v_reg from public.configuracoes where id;
    if not (coalesce(v_mot, false) and coalesce(v_reg, false)) then
      raise exception 'O registro de saída e chegada pelo motorista está desligado em Configurações.'
        using errcode = 'AG034';
    end if;
    if v_motorista_id is distinct from new.registrado_por then
      raise exception 'Somente o motorista designado nesta requisição registra o checklist.'
        using errcode = 'AG035';
    end if;
  elsif v_role not in ('autorizador', 'admin') then
    raise exception 'Apenas autorizador, admin ou o motorista designado ativo registra checklist.'
      using errcode = 'AG002';
  end if;

  -- Libera as fotos deste checklist apenas nesta transacao.
  perform set_config('sisveiculos.checklist_aberto_' || replace(new.id::text, '-', ''), '1', true);
  return new;
end;
$$;

revoke all on function public.checklists_validar() from public, anon, authenticated, service_role;

do $$
declare
  v_oid oid := to_regprocedure('public.checklists_validar()');
begin
  if position('AG035' in pg_get_functiondef(v_oid)) = 0 then
    raise exception 'checklists_validar sem o ramo do motorista.';
  end if;
  if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'checklists_validar executavel pela API.';
  end if;
  if not exists (select 1 from pg_proc where oid = v_oid and prosecdef
                  and proconfig is not null and position('search_path=' in array_to_string(proconfig, ',')) > 0) then
    raise exception 'checklists_validar sem SECURITY DEFINER ou search_path fixo.';
  end if;
end
$$;
