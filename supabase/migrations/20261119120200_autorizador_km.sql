-- Migration: flag autorizador_km + KM obrigatorio POR PAPEL do ator (AG012).
-- Nova migration; nao edita as anteriores. Idempotente. Depende de 20261117120200 (requisicoes_validar_km),
-- 20261117120100 (vigilante_km) e 20261119120100 (configuracoes_validar, 12 chaves).
--
-- Motivo (decisao do usuario, 2026-11-12, item 2): motorista NUNCA lanca KM; vigilante lanca (quilometragem +
-- vigilante_km); autorizador/admin so lancam se a nova flag autorizador_km (exige quilometragem, default false)
-- estiver ligada; solicitante nunca. Antes, com a Quilometragem ligada, todo ator que nao fosse vigilante era
-- obrigado a lancar KM.
--
-- 1. configuracoes.autorizador_km boolean not null default false + CHECK
--    ck_configuracoes_autorizador_km_exige_quilometragem (autorizador_km => quilometragem).
-- 2. configuracoes_validar: copia fiel da versao de 20261119120100 (12 chaves) + 13a chave, AG010 amigavel
--    antes do CHECK ("O KM do autorizador exige a Quilometragem ligada." / caminho inverso "Desligue o KM do
--    autorizador antes de desligar a Quilometragem."). Admin ativo (AG002), ator zerado e auditoria preservados.
-- 3. requisicoes_validar_km: copia fiel da versao de 20261117120200 (km imutavel AG004, so junto da transicao
--    AG004); UNICA mudanca = quem e obrigado a lancar, pelo papel do ator da transicao (requisicao_ator(new)):
--      quilometragem desligada ............................ ninguem e obrigado
--      ligada, ator vigilante ............................. obrigado se vigilante_km
--      ligada, ator autorizador ou admin .................. obrigado se autorizador_km
--      ligada, ator motorista (ou qualquer outro papel) ... NUNCA obrigado
--    Quem e obrigado lanca km_saida na saida e km_saida + km_chegada na chegada (AG012), como antes. Quem nao
--    e obrigado passa sem KM (o backend ignora o campo; o banco aceita se vier, e os CHECKs de coerencia
--    seguem valendo: chegada exige saida, chegada >= saida). Consequencia conhecida: saida sem KM (ator
--    dispensado) seguida de chegada por ator obrigado exige os DOIS km na chegada (o CHECK
--    ck_requisicoes_km_chegada_exige_saida impede km_chegada sem km_saida).
--    Desligar autorizador_km nunca apaga KM ja gravado (imutavel).
-- Fuso: sem colunas de data/hora. RLS/grants: nao mudam.

-- ---------------------------------------------------------------------------
-- 1. Coluna e CHECK
-- ---------------------------------------------------------------------------
alter table public.configuracoes
  add column if not exists autorizador_km boolean not null default false;

comment on column public.configuracoes.autorizador_km is
  'Recurso opcional: autorizador e admin informam o KM de saida e de chegada ao registrar. Exige quilometragem. Desligado por padrao.';

alter table public.configuracoes
  drop constraint if exists ck_configuracoes_autorizador_km_exige_quilometragem;
alter table public.configuracoes
  add constraint ck_configuracoes_autorizador_km_exige_quilometragem
  check (not autorizador_km or quilometragem);

-- ---------------------------------------------------------------------------
-- 2. configuracoes_validar (13 chaves)
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
     and new.autorizador_km is not distinct from old.autorizador_km then
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

  for v_chave in
    select chave
      from jsonb_object_keys(to_jsonb(new)) as chave
     where chave = any (array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa',
                              'relatorio_email', 'relatorio_whatsapp', 'notificacao_email_criticos',
                              'motorista', 'motorista_registra', 'motorista_localizacao',
                              'vigilante_km', 'autorizador_decide_propria', 'autorizador_km'])
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
-- 3. requisicoes_validar_km (KM obrigatorio por papel do ator)
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

  select quilometragem, vigilante_km, autorizador_km into v_ligado, v_vig_km, v_aut_km
    from public.configuracoes where id;
  if coalesce(v_ligado, false) then
    -- NOVO: a obrigatoriedade depende do PAPEL do ator da transicao. O ator (saida_registrada_por em em_uso,
    -- chegada_registrada_por em concluida) ja foi validado por trg_requisicoes_validar, que dispara antes
    -- deste gatilho (ordem alfabetica dos gatilhos BEFORE). Motorista e demais papeis: nunca obrigados.
    select p.role into v_role
      from public.perfis p
     where p.id = public.requisicao_ator(new);
    v_obriga := case
                  when v_role = 'vigilante' then coalesce(v_vig_km, false)
                  when v_role in ('autorizador', 'admin') then coalesce(v_aut_km, false)
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
                    and column_name = 'autorizador_km' and data_type = 'boolean'
                    and is_nullable = 'NO' and column_default = 'false') then
    raise exception 'configuracoes.autorizador_km deve ser boolean not null default false.';
  end if;
  if exists (select 1 from public.configuracoes where autorizador_km and not quilometragem) then
    raise exception 'configuracoes com autorizador_km sem quilometragem.';
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'public.configuracoes'::regclass
                    and conname = 'ck_configuracoes_autorizador_km_exige_quilometragem'
                    and contype = 'c' and convalidated) then
    raise exception 'ck_configuracoes_autorizador_km_exige_quilometragem ausente.';
  end if;

  v_oid := to_regprocedure('public.configuracoes_validar()');
  v_def := pg_get_functiondef(v_oid);
  foreach v_chave in array array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa', 'relatorio_email', 'relatorio_whatsapp', 'notificacao_email_criticos', 'motorista', 'motorista_registra', 'motorista_localizacao', 'vigilante_km', 'autorizador_decide_propria', 'autorizador_km']
  loop
    if position('''' || v_chave || '''' in v_def) = 0 then
      raise exception 'configuracoes_validar nao audita %.', v_chave;
    end if;
  end loop;
  if position('Desligue o KM do vigilante' in v_def) = 0
     or position('Desligue o KM do autorizador' in v_def) = 0 then
    raise exception 'configuracoes_validar sem as mensagens do caminho inverso (regressao).';
  end if;
  if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'configuracoes_validar executavel pela API.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.configuracoes'::regclass
                  and tgname = 'trg_configuracoes_validar' and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'trg_configuracoes_validar ausente ou desabilitado.';
  end if;

  v_oid := to_regprocedure('public.requisicoes_validar_km()');
  v_def := pg_get_functiondef(v_oid);
  if position('autorizador_km' in v_def) = 0 or position('vigilante_km' in v_def) = 0
     or position('AG012' in v_def) = 0 or position('AG004' in v_def) = 0 then
    raise exception 'requisicoes_validar_km sem a regra por papel, AG012 ou AG004 (regressao).';
  end if;
  if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'requisicoes_validar_km executavel pela API.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.requisicoes'::regclass
                  and tgname = 'trg_requisicoes_validar_km' and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'trg_requisicoes_validar_km ausente ou desabilitado.';
  end if;
end
$$;
