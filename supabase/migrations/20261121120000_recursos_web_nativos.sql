-- Migration: Recursos web nativos (5 flags novas em configuracoes + configuracoes_validar com 19 chaves).
-- Nova migration; nao edita as anteriores. Idempotente. Depende de 20261120120000 (configuracoes_validar com 14
-- chaves, incluindo notificacao_push; 20261120120100 nao a altera). Contrato: docs/plano-recursos-web-nativos.md.
--
-- Motivo: recursos opcionais de experiencia nativa no navegador (Fase 0 = so as flags; as fases seguintes
-- implementam cada recurso). Todos desligados por padrao, SEM dependencias entre si nem de outros recursos:
--   indicador_conectividade  indicador de conexao com a internet e com o Flask
--   feedback_tatil           vibracao curta (Vibration API)
--   leitor_qr_veiculos       leitor de QR Code dos veiculos
--   voz_campos               ditado por voz (Web Speech API)
--   passkeys                 passkeys (WebAuthn) como reautenticacao/fator adicional
-- Desligar um recurso nunca apaga dados ja gravados (nada aqui cria dados).
--
-- configuracoes_validar: copia fiel da versao de 20261120120000 (14 chaves) + as 5 novas. Preserva: so admin
-- ativo altera (AG002), ator (alterado_por) zerado, regras de dependencia AG010 e uma linha de auditoria
-- 'configuracao_alterada' por chave alterada. Sem erros novos.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. Colunas (boolean not null default false)
-- ---------------------------------------------------------------------------
alter table public.configuracoes
  add column if not exists indicador_conectividade boolean not null default false,
  add column if not exists feedback_tatil boolean not null default false,
  add column if not exists leitor_qr_veiculos boolean not null default false,
  add column if not exists voz_campos boolean not null default false,
  add column if not exists passkeys boolean not null default false;

comment on column public.configuracoes.indicador_conectividade is
  'Recurso opcional: indicador de conexao com a internet e com o servidor (desligado por padrao). Sem dependencias.';
comment on column public.configuracoes.feedback_tatil is
  'Recurso opcional: vibracao curta como feedback (Vibration API) (desligado por padrao). Sem dependencias.';
comment on column public.configuracoes.leitor_qr_veiculos is
  'Recurso opcional: leitor de QR Code dos veiculos (desligado por padrao). Sem dependencias.';
comment on column public.configuracoes.voz_campos is
  'Recurso opcional: ditado por voz nos campos de texto (Web Speech API) (desligado por padrao). Sem dependencias.';
comment on column public.configuracoes.passkeys is
  'Recurso opcional: passkeys (WebAuthn) como reautenticacao/fator adicional (desligado por padrao). Sem dependencias.';

-- ---------------------------------------------------------------------------
-- 2. configuracoes_validar (19 chaves)
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
     and new.passkeys is not distinct from old.passkeys then
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
                              'vigilante_km', 'autorizador_decide_propria', 'autorizador_km',
                              'notificacao_push',
                              'indicador_conectividade', 'feedback_tatil', 'leitor_qr_veiculos',
                              'voz_campos', 'passkeys'])
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
  v_def   text;
  v_chave text;
begin
  foreach v_chave in array array['indicador_conectividade', 'feedback_tatil', 'leitor_qr_veiculos', 'voz_campos', 'passkeys']
  loop
    if not exists (select 1 from information_schema.columns
                    where table_schema = 'public' and table_name = 'configuracoes'
                      and column_name = v_chave and data_type = 'boolean'
                      and is_nullable = 'NO' and column_default = 'false') then
      raise exception 'configuracoes.% deve ser boolean not null default false.', v_chave;
    end if;
  end loop;

  v_def := pg_get_functiondef(to_regprocedure('public.configuracoes_validar()'));
  foreach v_chave in array array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa', 'relatorio_email', 'relatorio_whatsapp', 'notificacao_email_criticos', 'motorista', 'motorista_registra', 'motorista_localizacao', 'vigilante_km', 'autorizador_decide_propria', 'autorizador_km', 'notificacao_push', 'indicador_conectividade', 'feedback_tatil', 'leitor_qr_veiculos', 'voz_campos', 'passkeys']
  loop
    if position('''' || v_chave || '''' in v_def) = 0 then
      raise exception 'configuracoes_validar nao audita %.', v_chave;
    end if;
  end loop;
  if position('Desligue o KM do vigilante' in v_def) = 0
     or position('Desligue o KM do autorizador' in v_def) = 0 then
    raise exception 'configuracoes_validar sem as mensagens do caminho inverso (regressao).';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.configuracoes'::regclass
                  and tgname = 'trg_configuracoes_validar' and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'trg_configuracoes_validar ausente ou desabilitado.';
  end if;
end
$$;
