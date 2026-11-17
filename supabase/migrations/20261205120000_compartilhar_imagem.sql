-- Migration: recurso opcional "Compartilhar requisição como imagem" (flag configuracoes.compartilhar_imagem).
-- Nova migration; nao edita as anteriores. Idempotente. Depende de 20261201120000 (configuracoes_validar,
-- 20 chaves; nenhuma migration posterior a redefine).
--
-- Decisao do dono (2026-11-17, alternativa D de LGPD): ligado, o backend inclui a acao `compartilhar_imagem`
-- em `acoes_permitidas` para quem ja pode ver a requisicao (nao muda quem ve). A imagem (PNG) e gerada no
-- proprio aparelho a partir dos dados ja devolvidos pela API; sem fotos, e-mail, telefone, checklist nem
-- localizacao; observacao so por escolha do usuario. Nenhum dado novo sai do banco por causa desta flag.
--
-- 1. configuracoes.compartilhar_imagem boolean not null default false (sem dependencias de outros recursos).
-- 2. configuracoes_validar: copia fiel da versao de 20261201120000 (20 chaves) + 21a chave (early-return e
--    auditoria). Admin ativo (AG002), ator zerado, regras AG010 e uma linha de auditoria
--    'configuracao_alterada' por chave preservados.
-- Exportacao/importacao de dados: a flag fica DE FORA (como mfa, passkeys, notificacao_push e relatorio_*),
-- para que importar um arquivo nunca ligue sozinho um recurso que depende de decisao de LGPD;
-- privado.dados_colunas e importar_dados nao mudam.
-- 3. Autoverificacao (coluna, 21 chaves, early-return, definer/search_path, grants, gatilho, RLS, fora da
--    exportacao) e reload do schema do PostgREST.
-- Fuso: sem colunas de data/hora novas. RLS/grants: nao mudam.

set local lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 1. Coluna
-- ---------------------------------------------------------------------------
alter table public.configuracoes
  add column if not exists compartilhar_imagem boolean not null default false;

comment on column public.configuracoes.compartilhar_imagem is
  'Recurso opcional: permite gerar no aparelho uma imagem (PNG) da requisicao para compartilhar ou baixar, sem fotos nem contatos; observacao so por escolha do usuario. Desligado por padrao.';

-- ---------------------------------------------------------------------------
-- 2. configuracoes_validar (21 chaves)
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
     and new.passkeys is not distinct from old.passkeys
     and new.motorista_km is not distinct from old.motorista_km
     and new.compartilhar_imagem is not distinct from old.compartilhar_imagem then
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

  -- KM pelo motorista exige Quilometragem E registro de saida e chegada pelo motorista.
  if new.motorista_km and not new.quilometragem then
    if old.quilometragem and old.motorista_km then
      -- Caminho inverso: desligar a Quilometragem com o KM do motorista ligado.
      raise exception 'Desligue o KM do motorista antes de desligar a Quilometragem.'
        using errcode = 'AG010';
    end if;
    raise exception 'O KM do motorista exige a Quilometragem ligada.'
      using errcode = 'AG010';
  end if;

  if new.motorista_km and not new.motorista_registra then
    if old.motorista_registra and old.motorista_km then
      -- Caminho inverso: desligar o registro pelo motorista com o KM do motorista ligado.
      raise exception 'Desligue o KM do motorista antes de desligar o registro de saída e chegada pelo motorista.'
        using errcode = 'AG010';
    end if;
    raise exception 'O KM do motorista exige o registro de saída e chegada pelo motorista ligado.'
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
                              'voz_campos', 'passkeys',
                              'motorista_km',
                              'compartilhar_imagem'])
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
-- 3. Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v_oid   oid;
  v_def   text;
  v_chave text;
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'configuracoes'
                    and column_name = 'compartilhar_imagem' and data_type = 'boolean'
                    and is_nullable = 'NO' and column_default = 'false') then
    raise exception 'configuracoes.compartilhar_imagem deve ser boolean not null default false.';
  end if;
  if not exists (select 1 from pg_class where relname = 'configuracoes' and relrowsecurity
                    and relnamespace = 'public'::regnamespace) then
    raise exception 'RLS desligado em public.configuracoes.';
  end if;

  -- configuracoes_validar: 21 chaves, early-return, SECURITY DEFINER/search_path, grants e gatilho
  v_oid := to_regprocedure('public.configuracoes_validar()');
  v_def := pg_get_functiondef(v_oid);
  foreach v_chave in array array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa', 'relatorio_email', 'relatorio_whatsapp', 'notificacao_email_criticos', 'motorista', 'motorista_registra', 'motorista_localizacao', 'vigilante_km', 'autorizador_decide_propria', 'autorizador_km', 'notificacao_push', 'indicador_conectividade', 'feedback_tatil', 'leitor_qr_veiculos', 'voz_campos', 'passkeys', 'motorista_km', 'compartilhar_imagem']
  loop
    if position('''' || v_chave || '''' in v_def) = 0 then
      raise exception 'configuracoes_validar nao audita %.', v_chave;
    end if;
  end loop;
  if position('new.compartilhar_imagem is not distinct from old.compartilhar_imagem' in v_def) = 0 then
    raise exception 'configuracoes_validar sem compartilhar_imagem no early-return.';
  end if;
  if position('privado.exigir_admin_ativo' in v_def) = 0 then
    raise exception 'configuracoes_validar sem exigir_admin_ativo.';
  end if;
  if not (select prosecdef and 'search_path=""' = any (proconfig) from pg_proc where oid = v_oid) then
    raise exception 'configuracoes_validar com SECURITY DEFINER/search_path divergentes.';
  end if;
  if has_function_privilege('anon', v_oid, 'execute') or has_function_privilege('authenticated', v_oid, 'execute')
     or has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'configuracoes_validar executavel pela API.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.configuracoes'::regclass
                  and tgname = 'trg_configuracoes_validar' and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'trg_configuracoes_validar ausente ou desabilitado.';
  end if;

  -- Fora da exportacao/importacao (decisao LGPD de 2026-11-17)
  if 'compartilhar_imagem' = any (privado.dados_colunas('configuracoes')) then
    raise exception 'privado.dados_colunas(configuracoes) exporta compartilhar_imagem (regressao).';
  end if;
end
$$;

notify pgrst, 'reload schema';
