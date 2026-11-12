-- Migration: mensagem correta ao desligar a Quilometragem com o KM do vigilante ligado (achado B4).
-- Nova migration; nao edita as anteriores. Idempotente. Depende de 20261117120100_vigilante_km_flag.sql.
--
-- Motivo: o gatilho ja barrava a combinacao (AG010 antes do CHECK 23514), mas usava a mesma mensagem
-- "exige a Quilometragem ligada" nos dois caminhos; ao DESLIGAR a Quilometragem com vigilante_km ligada ela
-- descrevia o caminho inverso. Agora: ligar vigilante_km sem Quilometragem => "O KM do vigilante exige a
-- Quilometragem ligada."; desligar a Quilometragem com vigilante_km ligada (estado antigo com ambas
-- ligadas) => "Desligue o KM do vigilante antes de desligar a Quilometragem.". Ambos com AG010.
-- Mudanca UNICA no corpo: o bloco acima. As 11 chaves auditadas, o ator zerado, AG002 sem admin ativo e as
-- demais dependencias (AG010) sao copia fiel da versao de 20261117120100. Desligar ambas na MESMA alteracao
-- continua valido. CHECK ck_configuracoes_vigilante_km_exige_quilometragem inalterado.
-- Fuso: sem colunas de data/hora. RLS/grants: nao mudam.

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

do $$
declare
  v_oid oid := to_regprocedure('public.configuracoes_validar()');
  v_def text := pg_get_functiondef(to_regprocedure('public.configuracoes_validar()'));
  v_chave text;
begin
  if position('Desligue o KM do vigilante' in v_def) = 0 then
    raise exception 'configuracoes_validar sem a mensagem do caminho inverso.';
  end if;
  foreach v_chave in array array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa',
                                 'relatorio_email', 'relatorio_whatsapp', 'notificacao_email_criticos',
                                 'motorista', 'motorista_registra', 'motorista_localizacao', 'vigilante_km']
  loop
    if position('''' || v_chave || '''' in v_def) = 0 then
      raise exception 'configuracoes_validar nao audita %.', v_chave;
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
end
$$;
