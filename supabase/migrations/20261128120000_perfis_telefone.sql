-- Migration: telefone (WhatsApp) opcional em perfis, editado so pelo admin.
-- Nova migration; NAO edita nenhuma anterior. Idempotente (add column if not exists, DO com checagem,
-- create or replace). Depende de 20261107121400 (perfis_guarda_admin vigente, reescrito aqui com o
-- mesmo corpo + telefone) e de 20261123120000 (lista vigente de 32 valores de ck_auditoria_acao).
-- NAO aplicar pelo servidor: supabase db push e do pipeline.
--
-- Motivo (decisao do dono do projeto, 2026-11-15): o n8n (Evolution Go) avisa o motorista na
-- autorizacao e o solicitante/autorizador na chegada pelo WhatsApp; o numero e o campo `number`.
--
-- 1. perfis.telefone text NULL. So digitos: DDI 55 + DDD (sem zero) + 9 opcional + 8 digitos
--    (12 ou 13 digitos), ex.: 5561999998888. NULL = sem telefone. O backend normaliza (tira espacos,
--    parenteses, hifen e '+') e traduz a violacao de ck_perfis_telefone; o banco so aceita o formato final.
--    String vazia NAO e aceita (o backend converte "" em NULL).
-- 2. Quem altera: so admin ativo. A troca de telefone passa a ser acao administrativa no guarda
--    (BEFORE): exige alterado_por = admin ativo (AG002 senao), sob o mesmo advisory exclusivo. O admin
--    PODE alterar o proprio telefone (a trava AG008 continua so para papel/ativo).
--    INSERT (consumir_convite, seed) nao muda: o telefone do convite entra junto com o perfil.
-- 3. Auditoria append-only: nova acao 'perfil_telefone_alterado' (32 vigentes preservadas -> 33).
--    tabela='perfis', registro_id = perfil, ator_id = admin, de/para NULOS e
--    detalhes = {"telefone_alterado": true}. NUNCA o valor (antigo ou novo) do telefone (LGPD).
--    Acao propria (e nao 'perfil_alterado') para nao disparar o aviso perfil_alterado_admin nem
--    misturar com o par role/ativo em de/para. ck_auditoria_tabela nao muda.
--
-- Leitura: RLS e grants inalterados (anon/authenticated sem privilegio em perfis; deny-by-default).
-- Nenhuma RPC devolve perfis a nao-admin (so consumir_convite, ao backend). Exportar/importar dados
-- nao inclui perfis (20261123120000), entao nada muda la.
-- Fuso: timestamptz (UTC; exibicao em America/Sao_Paulo).

set local lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 1. Coluna e CHECK
-- ---------------------------------------------------------------------------
alter table public.perfis add column if not exists telefone text;

do $$
begin
  if not exists (
    select 1 from pg_constraint
     where conrelid = 'public.perfis'::regclass and conname = 'ck_perfis_telefone'
  ) then
    alter table public.perfis
      add constraint ck_perfis_telefone
      check (telefone is null or telefone ~ '^55[1-9]{2}9?[0-9]{8}$');
  end if;
end
$$;

comment on column public.perfis.telefone is
  'Telefone/WhatsApp opcional (so digitos, 55+DDD+numero). Dado pessoal: so admin edita e le; vai ao webhook n8n (excecao aprovada). Nunca logar nem auditar o valor.';

-- ---------------------------------------------------------------------------
-- 2. Auditoria: nova acao (preserva as 32 vigentes)
-- ---------------------------------------------------------------------------
do $$
declare
  v_def text;
  v     text;
begin
  select pg_get_constraintdef(oid) into v_def from pg_constraint
   where conrelid = 'public.auditoria'::regclass and conname = 'ck_auditoria_acao';
  foreach v in array array[
    'criada', 'transicao', 'convite_criado', 'convite_usado', 'convite_revogado',
    'perfil_criado', 'perfil_alterado', 'perfil_unidade_alterada', 'admin_semeado',
    'configuracao_alterada', 'veiculo_criado', 'veiculo_alterado', 'acao_administrativa', 'acesso_negado',
    'checklist_registrado', 'foto_perfil_alterada', 'foto_perfil_removida', 'relatorio_gerado',
    'relatorio_enviado', 'motorista_designado', 'localizacao_registrada', 'localizacao_lida',
    'localizacao_expurgada', 'saudacao_excluida', 'passkey_cadastrada', 'passkey_revogada',
    'passkey_autenticada', 'passkey_falha', 'passkey_regressao_contador', 'passkey_desafio_invalido',
    'dados_exportados', 'dados_importados'
  ] loop
    if v_def is null or position('''' || v || '''' in v_def) = 0 then
      raise exception 'ck_auditoria_acao vigente sem o valor % (lista inesperada; revisar antes de recriar).', v;
    end if;
  end loop;
end
$$;

alter table public.auditoria drop constraint if exists ck_auditoria_acao;
alter table public.auditoria
  add constraint ck_auditoria_acao check (acao in (
    'criada', 'transicao',
    'convite_criado', 'convite_usado', 'convite_revogado',
    'perfil_criado', 'perfil_alterado', 'perfil_unidade_alterada', 'admin_semeado',
    'configuracao_alterada',
    'veiculo_criado', 'veiculo_alterado',
    'acao_administrativa', 'acesso_negado',
    'checklist_registrado',
    'foto_perfil_alterada', 'foto_perfil_removida',
    'relatorio_gerado', 'relatorio_enviado',
    'motorista_designado',
    'localizacao_registrada',
    'localizacao_lida',
    'localizacao_expurgada',
    'saudacao_excluida',
    'passkey_cadastrada', 'passkey_revogada', 'passkey_autenticada',
    'passkey_falha', 'passkey_regressao_contador', 'passkey_desafio_invalido',
    'dados_exportados', 'dados_importados',
    'perfil_telefone_alterado'
  ));

-- ---------------------------------------------------------------------------
-- 3. Guarda: troca de telefone exige admin ativo e gera auditoria sem o valor.
--    Corpo identico ao de 20261107121400 (Baixo 1), acrescido de v_muda_telefone.
-- ---------------------------------------------------------------------------
create or replace function public.perfis_guarda_admin()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator          uuid := new.alterado_por;
  v_outros        integer;
  v_muda_papel    boolean;
  v_muda_unidade  boolean;
  v_muda_telefone boolean;
  v_chave         text;
begin
  new.alterado_por := null;  -- nunca persiste

  if tg_op = 'INSERT' then
    v_chave := 'sisveiculos.ator_criacao_' || replace(new.id::text, '-', '');
    if new.role = 'admin' then
      -- Mesma chave da migration 7 e do seed: serializa criacao e alteracao de admins.
      perform pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0));
      if exists (select 1 from public.perfis where role = 'admin' and ativo) then
        -- Havendo admin ativo, o responsavel precisa ser um deles.
        perform privado.exigir_admin_ativo(v_ator);
        -- Informa o ator a perfis_auditar (AFTER), por perfil.
        perform set_config(v_chave, v_ator::text, true);
      elsif coalesce(current_setting('sisveiculos.seed_em_curso', true), '') <> '1' then
        -- Bootstrap (sem admin ativo) so durante privado.semear_primeiro_admin.
        raise exception 'O primeiro administrador só pode ser criado pelo seed.' using errcode = 'AG002';
      else
        -- Seed: sem ator; descarta qualquer valor residual.
        perform set_config(v_chave, '', true);
      end if;
    elsif coalesce(current_setting(v_chave, true), '') <> '' then
      -- Perfil nao admin nao tem ator de criacao; limpa variavel forjada/residual.
      perform set_config(v_chave, '', true);
    end if;
    return new;
  end if;

  if new.id is distinct from old.id then
    raise exception 'O id do perfil é imutável.' using errcode = 'AG004';
  end if;

  v_muda_papel    := new.role is distinct from old.role or new.ativo is distinct from old.ativo;
  v_muda_unidade  := new.unidade is distinct from old.unidade;
  v_muda_telefone := new.telefone is distinct from old.telefone;

  -- Nada administrativo mudou (ex.: so o nome).
  if not v_muda_papel and not v_muda_unidade and not v_muda_telefone then
    return new;
  end if;

  -- Exclusivo ANTES de exigir_admin_ativo (tambem exclusivo): sem promocao de lock.
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0));

  perform privado.exigir_admin_ativo(v_ator);

  if v_muda_papel then
    if v_ator = old.id then
      raise exception 'O administrador não pode alterar o próprio papel nem desativar a si mesmo.'
        using errcode = 'AG008';
    end if;

    -- Deixaria de existir admin ativo alem deste?
    if old.role = 'admin' and old.ativo and not (new.role = 'admin' and new.ativo) then
      select count(*) into v_outros
        from public.perfis
       where role = 'admin' and ativo and id <> old.id;
      if v_outros = 0 then
        raise exception 'Deve existir ao menos um administrador ativo.' using errcode = 'AG009';
      end if;
    end if;

    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para)
    values ('perfis', old.id, 'perfil_alterado', v_ator,
            old.role::text || '/' || case when old.ativo then 'ativo' else 'inativo' end,
            new.role::text || '/' || case when new.ativo then 'ativo' else 'inativo' end);
  end if;

  if v_muda_unidade then
    -- Informa o ator a perfis_auditar (AFTER), por perfil.
    perform set_config('sisveiculos.ator_unidade_' || replace(new.id::text, '-', ''),
                       v_ator::text, true);
  end if;

  if v_muda_telefone then
    -- LGPD: so o fato (sim), nunca o valor antigo ou novo.
    insert into public.auditoria (tabela, registro_id, acao, ator_id, detalhes)
    values ('perfis', old.id, 'perfil_telefone_alterado', v_ator,
            jsonb_build_object('telefone_alterado', true));
  end if;

  return new;
end;
$$;

revoke all on function public.perfis_guarda_admin() from public, anon, authenticated;
