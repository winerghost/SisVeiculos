-- Migration: o vigilante registra o checklist de saida/chegada (achado M1 da revisao de seguranca).
-- Nova migration; nao edita as anteriores. Idempotente. Depende de 20261117120000 (enum) e 20261112120300.
--
-- Motivo: com o recurso Checklist ligado, a saida/chegada exige checklist (AG013). checklists_validar
-- (versao de 20261112120300) recusava o role 'vigilante' (AG002), o que o impedia de registrar saida e
-- chegada, contrariando o "sempre ativo" do plano (docs/plano-motorista-vigilante.md, secao 2, item 3).
-- Mudanca UNICA: ramo novo `elsif v_role = 'vigilante'` no bloco do ator, espelhando o da saida/chegada
-- (20261117120200): vale para QUALQUER requisicao, sem flag em configuracoes e sem exigir designacao
-- (nao ha AG034/AG035). O restante e copia fiel: campos obrigatorios, CHECKs espelhados, janela AG014
-- (saida so com 'autorizada', chegada so com 'em_uso'), ator ativo, autorizador/admin e motorista
-- (AG034/AG035) iguais, e outros roles (solicitante) e inativos seguem em AG002. A mensagem de AG002
-- passa a citar o vigilante. Fotos: checklist_fotos_validar so exige o marcador do checklist aberto
-- (sem checagem de role), logo as fotos do vigilante passam pela mesma RPC registrar_checklist.
-- Fuso: nao se aplica. RLS/grants: nao mudam (funcao sem EXECUTE para a API).

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
    raise exception 'Apenas autorizador, admin, vigilante ou o motorista designado ativo registra checklist.'
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
  elsif v_role = 'vigilante' then
    null;  -- NOVO: o vigilante registra o checklist de QUALQUER requisicao na janela; sem flag, sem designacao.
  elsif v_role not in ('autorizador', 'admin') then
    raise exception 'Apenas autorizador, admin, vigilante ou o motorista designado ativo registra checklist.'
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
  v_def text;
begin
  v_def := pg_get_functiondef(v_oid);
  if position('''vigilante''' in v_def) = 0 or position('AG035' in v_def) = 0 or position('AG014' in v_def) = 0 then
    raise exception 'checklists_validar sem o ramo do vigilante ou com regressao (motorista/janela).';
  end if;
  if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'checklists_validar executavel pela API.';
  end if;
  if not exists (select 1 from pg_proc where oid = v_oid and prosecdef
                  and proconfig is not null and position('search_path=' in array_to_string(proconfig, ',')) > 0) then
    raise exception 'checklists_validar sem SECURITY DEFINER ou search_path fixo.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.checklists'::regclass
                  and tgfoid = v_oid and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'gatilho de checklists_validar ausente ou desabilitado.';
  end if;
end
$$;
