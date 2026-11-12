-- Migration: flag do recurso opcional KM DO VIGILANTE (parte 2 de 3 do Perfil Vigilante).
-- Nova migration; nao edita as anteriores. Idempotente. Depende de 20261112120000_motorista_localizacao_flag.sql
-- (versao anterior de configuracoes_validar, 10 chaves). Nao usa o valor 'vigilante' do enum (ver 20261117120000).
--
-- Motivo (decisao do usuario, docs/plano-motorista-vigilante.md secao 2, item 4): o vigilante so informa o KM de
-- saida/chegada se o admin habilitar. CLAUDE.md ("Recursos opcionais"): desligado por padrao, so funciona se o
-- admin habilitar em Configuracoes, cada alteracao vai para auditoria, desligar nunca apaga dados.
--
-- 1. configuracoes.vigilante_km boolean not null default false.
--    CHECK ck_configuracoes_vigilante_km_exige_quilometragem: vigilante_km exige quilometragem. AG010 amigavel no
--    trigger (antes do CHECK 23514). Para desligar `quilometragem` com vigilante_km ligada: desligue vigilante_km
--    antes (ou na mesma alteracao), como nas demais dependencias.
-- 2. configuracoes_validar (versao de 20261112120000, 10 chaves) passa a auditar 11 chaves, mesmo padrao: ator
--    zerado, AG002 sem admin ativo, uma linha 'configuracao_alterada' por chave alterada (de/para 'true'/'false',
--    detalhes {"recurso": "<chave>"}). NAO ha acao nova de auditoria. TODAS as chaves anteriores sao preservadas.
-- Fuso: sem colunas de data/hora. RLS/grants: nao mudam (tabela sem politicas para anon/authenticated).

alter table public.configuracoes
  add column if not exists vigilante_km boolean not null default false;

comment on column public.configuracoes.vigilante_km is
  'Recurso opcional: o vigilante informa o KM de saida e de chegada ao registrar. Exige quilometragem. Desligado por padrao.';

alter table public.configuracoes
  drop constraint if exists ck_configuracoes_vigilante_km_exige_quilometragem;
alter table public.configuracoes
  add constraint ck_configuracoes_vigilante_km_exige_quilometragem
  check (not vigilante_km or quilometragem);

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
     and new.vigilante_km is not distinct from old.vigilante_km then
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
    raise exception 'O KM do vigilante exige a Quilometragem ligada.'
      using errcode = 'AG010';
  end if;

  for v_chave in
    select chave
      from jsonb_object_keys(to_jsonb(new)) as chave
     where chave = any (array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa',
                              'relatorio_email', 'relatorio_whatsapp', 'notificacao_email_criticos',
                              'motorista', 'motorista_registra', 'motorista_localizacao',
                              'vigilante_km'])
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
-- Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v_oid oid;
  v_def text;
  v_chave text;
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'configuracoes'
                    and column_name = 'vigilante_km' and data_type = 'boolean'
                    and is_nullable = 'NO' and column_default = 'false') then
    raise exception 'configuracoes.vigilante_km deve ser boolean not null default false.';
  end if;
  if exists (select 1 from public.configuracoes where vigilante_km and not quilometragem) then
    raise exception 'configuracoes com vigilante_km sem quilometragem.';
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'public.configuracoes'::regclass
                    and conname = 'ck_configuracoes_vigilante_km_exige_quilometragem'
                    and contype = 'c' and convalidated) then
    raise exception 'ck_configuracoes_vigilante_km_exige_quilometragem ausente.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.configuracoes'::regclass
                    and tgname = 'trg_configuracoes_validar' and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'trg_configuracoes_validar ausente ou desabilitado.';
  end if;
  -- A funcao deve conhecer TODAS as chaves (evita regressao para uma versao anterior).
  v_oid := to_regprocedure('public.configuracoes_validar()');
  v_def := pg_get_functiondef(v_oid);
  foreach v_chave in array array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa',
                                 'relatorio_email', 'relatorio_whatsapp', 'notificacao_email_criticos',
                                 'motorista', 'motorista_registra', 'motorista_localizacao', 'vigilante_km']
  loop
    if position('''' || v_chave || '''' in v_def) = 0 then
      raise exception 'configuracoes_validar nao audita %.', v_chave;
    end if;
  end loop;
  if has_function_privilege('anon', v_oid, 'execute')
     or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'configuracoes_validar executavel pela API.';
  end if;
end
$$;
