-- Migration: flag do recurso opcional LOCALIZACAO DO MOTORISTA (parte 1 de 4). Nova migration; nao edita as
-- anteriores. Idempotente. Depende de 20261111120100_motorista_recurso.sql.
--
-- Motivo (regra de negocio decidida pelo usuario): quando o ROLE motorista registra saida ou chegada, a
-- latitude e a longitude sao obrigatorias; sem coordenadas o registro e BLOQUEADO (sem excecao). Vale so
-- para registros feitos pelo motorista. CLAUDE.md ("Recursos opcionais"): desligado por padrao, so
-- funciona se o admin habilitar em Configuracoes, cada alteracao vai para auditoria.
--
-- 1. configuracoes.motorista_localizacao boolean not null default false.
--    CHECK ck_configuracoes_localizacao_exige_registra: exige motorista_registra (que ja exige motorista,
--    ck_configuracoes_motorista_registra_exige_motorista). AG010 amigavel no trigger (CHECK 23514 equivalente).
--    Para desligar `motorista_registra` com a localizacao ligada: desligue a localizacao antes (ou na mesma
--    alteracao), como nas demais dependencias.
-- 2. configuracoes_validar (versao de 20261111120100, 9 chaves) passa a auditar 10 chaves, mesmo padrao:
--    ator zerado, AG002 sem admin ativo, uma linha 'configuracao_alterada' por chave alterada
--    (de/para 'true'/'false', detalhes {"recurso": "<chave>"}). NAO ha acao nova de auditoria: reaproveita
--    'configuracao_alterada'. Desligar o recurso nunca apaga as localizacoes ja gravadas.
-- Fuso: sem colunas de data/hora. RLS/grants: nao mudam.

alter table public.configuracoes
  add column if not exists motorista_localizacao boolean not null default false;

comment on column public.configuracoes.motorista_localizacao is
  'Recurso opcional: o motorista so registra saida/chegada com latitude e longitude. Exige motorista_registra. Desligado por padrao.';

alter table public.configuracoes
  drop constraint if exists ck_configuracoes_localizacao_exige_registra;
alter table public.configuracoes
  add constraint ck_configuracoes_localizacao_exige_registra
  check (not motorista_localizacao or motorista_registra);

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
     and new.motorista_localizacao is not distinct from old.motorista_localizacao then
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

  for v_chave in
    select chave
      from jsonb_object_keys(to_jsonb(new)) as chave
     where chave = any (array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa',
                              'relatorio_email', 'relatorio_whatsapp', 'notificacao_email_criticos',
                              'motorista', 'motorista_registra', 'motorista_localizacao'])
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
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'configuracoes'
                    and column_name = 'motorista_localizacao' and data_type = 'boolean'
                    and is_nullable = 'NO' and column_default = 'false') then
    raise exception 'configuracoes.motorista_localizacao deve ser boolean not null default false.';
  end if;
  if exists (select 1 from public.configuracoes where motorista_localizacao and not motorista_registra) then
    raise exception 'configuracoes com motorista_localizacao sem motorista_registra.';
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'public.configuracoes'::regclass
                    and conname = 'ck_configuracoes_localizacao_exige_registra'
                    and contype = 'c' and convalidated) then
    raise exception 'ck_configuracoes_localizacao_exige_registra ausente.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.configuracoes'::regclass
                    and tgname = 'trg_configuracoes_validar' and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'trg_configuracoes_validar ausente ou desabilitado.';
  end if;
  -- A funcao deve conhecer a chave nova (evita regressao para a versao de 9 chaves).
  if position('motorista_localizacao' in pg_get_functiondef('public.configuracoes_validar()'::regprocedure)) = 0 then
    raise exception 'configuracoes_validar nao audita motorista_localizacao.';
  end if;
  v_oid := to_regprocedure('public.configuracoes_validar()');
  if has_function_privilege('anon', v_oid, 'execute')
     or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'configuracoes_validar executavel pela API.';
  end if;
end
$$;
