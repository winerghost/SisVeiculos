-- Migration: recurso opcional PERFIL MOTORISTA (parte 2 de 2). Nova migration; nao edita as anteriores.
-- Idempotente. Depende de 20261111120000_motorista_role.sql (valor 'motorista' do enum role_usuario).
--
-- Motivo: CLAUDE.md ("Recursos opcionais"): desligado por padrao; so funciona se o admin habilitar em
-- Configuracoes; cada alteracao vai para auditoria; desligar nunca apaga dados ja gravados.
--
-- 1. configuracoes ganha DUAS flags (boolean not null default false):
--      motorista ............ liga o recurso (existe o role 'motorista' e a designacao em requisicoes)
--      motorista_registra ... o motorista designado pode registrar saida/chegada (regra aplicada pelo
--                             BACKEND; ver "Fronteira" abaixo). Exige motorista ligado:
--                             CHECK ck_configuracoes_motorista_registra_exige_motorista + AG010 amigavel.
--    configuracoes_validar (versao da migration de notificacoes, 7 chaves) passa a auditar as 9 chaves
--    com o mesmo padrao: ator zerado, AG002 sem admin ativo, uma linha 'configuracao_alterada' por chave
--    alterada (de/para 'true'/'false', detalhes {"recurso": "<chave>"}). Nada mais muda.
--    Desligar `motorista` com `motorista_registra` ligado e recusado (CHECK/AG010): desligue as duas na
--    mesma alteracao ou a dependente primeiro, como em abastecimento_manutencao/quilometragem.
-- 2. requisicoes.motorista_id (uuid, nula) -> perfis(id) ON DELETE RESTRICT, com indice parcial.
--    * CHECK ck_requisicoes_motorista_status: motorista_id so existe a partir de 'autorizada'
--      (autorizada, em_uso, concluida, cancelada); nunca em 'solicitada' nem em 'negada'.
--    * Coluna de transporte requisicoes.motorista_alterado_por (uuid, nula): quem designa/troca/remove o
--      motorista, no padrao de configuracoes.alterado_por. Validada e ZERADA pelo trigger; nunca persiste.
--      O ator vem do backend (ver migration 10: o banco valida existencia, atividade e papel, mas nao
--      prova autoria; o pytest de permissoes do backend e a barreira).
--    * Trigger trg_requisicoes_validar_motorista (BEFORE INSERT OR UPDATE, ENABLE ALWAYS). Roda DEPOIS de
--      trg_requisicoes_validar (ordem alfabetica), que ja congela requisicoes encerradas (AG001).
--      Regras quando motorista_id muda:
--        - so com a requisicao 'autorizada' ou 'em_uso' (inclui a propria transicao solicitada->autorizada,
--          pois o status NOVO e que vale); caso contrario AG032;
--        - ator informado, ativo, com role autorizador ou admin; caso contrario AG002;
--        - ao DEFINIR ou TROCAR (valor nao nulo): configuracoes.motorista ligada (AG033) e o referenciado
--          existe, esta ativo e tem role = 'motorista' (AG031; FOR SHARE: nao pode ser desativado ou
--          rebaixado por outra transacao ate o fim desta);
--        - REMOVER (nulo) e permitido nos mesmos status, mesmo com o recurso desligado (so desfaz);
--        - motorista_alterado_por sem mudar motorista_id => AG004.
--      Troca em em_uso e permitida (ex.: motorista indisponivel); cada mudanca e auditada com de/para.
-- 3. Auditoria: ck_auditoria_acao ganha 'motorista_designado' (as 19 acoes existentes preservadas;
--    ck_auditoria_tabela nao muda: usa 'requisicoes'). Linha gravada pelo proprio trigger, no mesmo
--    statement da mudanca (se o UPDATE falhar depois, a linha some junto): registro_id = requisicao,
--    ator_id = quem designou, de/para = uuid antigo/novo do motorista (ids, sem nome nem e-mail; LGPD),
--    detalhes {"recurso":"motorista","status":"<status>"}. Append-only intacto (so INSERT).
--
-- FRONTEIRA BANCO x BACKEND (decisao):
--  * O banco garante: role e atividade do motorista, status permitido, flag ligado para NOVA designacao,
--    ator valido e auditoria. Valores ja gravados nunca sao apagados ao desligar o recurso.
--  * NAO alterado nesta migration (de proposito): requisicoes_validar continua exigindo autorizador/admin
--    como responsavel por saida/chegada. Portanto `motorista_registra` hoje so e GUARDADA e auditada; o
--    banco ainda NAO deixa o motorista registrar saida/chegada. Isso exige reescrever requisicoes_validar
--    (funcao central de transicoes) e fica como decisao pendente do usuario, em migration propria.
--  * Quem e o motorista de uma requisicao nao concede leitura: RLS segue deny-by-default; o backend
--    filtra por dono/designacao (anti-IDOR).
--
-- Efeitos conhecidos (nao tratados aqui): desativar ou rebaixar um motorista que ja esta designado em
-- requisicao aberta nao remove a designacao (a validacao e no momento de designar); cabe ao backend avisar.
--
-- Erros novos: AG031 motorista inexistente, inativo ou sem role 'motorista'; AG032 motorista so a partir de
-- 'autorizada'; AG033 recurso Perfil Motorista desligado. Reaproveitados: AG002 ator ausente/inativo/sem
-- papel; AG004 coluna de transporte sem mudanca; AG010 dependencia entre flags (CHECK 23514 equivalente).
-- RLS/grants: nao mudam (colunas herdam o deny-by-default da tabela). Funcoes novas: EXECUTE revogado de
-- public, anon, authenticated e service_role (so o Postgres as chama, como trigger).
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo). Sem colunas de data/hora novas.

-- ---------------------------------------------------------------------------
-- 1. configuracoes: motorista e motorista_registra + configuracoes_validar (9 chaves)
-- ---------------------------------------------------------------------------
alter table public.configuracoes
  add column if not exists motorista          boolean not null default false,
  add column if not exists motorista_registra boolean not null default false;

comment on column public.configuracoes.motorista is
  'Recurso opcional: Perfil Motorista (role motorista e designacao em requisicoes). Desligado por padrao.';
comment on column public.configuracoes.motorista_registra is
  'Recurso opcional: o motorista designado registra saida/chegada. Exige motorista ligado. Desligado por padrao.';

alter table public.configuracoes
  drop constraint if exists ck_configuracoes_motorista_registra_exige_motorista;
alter table public.configuracoes
  add constraint ck_configuracoes_motorista_registra_exige_motorista
  check (not motorista_registra or motorista);

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
     and new.motorista_registra is not distinct from old.motorista_registra then
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

  for v_chave in
    select chave
      from jsonb_object_keys(to_jsonb(new)) as chave
     where chave = any (array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa',
                              'relatorio_email', 'relatorio_whatsapp', 'notificacao_email_criticos',
                              'motorista', 'motorista_registra'])
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
-- 2. Auditoria: nova acao 'motorista_designado' (preserva as 19 existentes)
-- ---------------------------------------------------------------------------
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
    'motorista_designado'
  ));

-- ---------------------------------------------------------------------------
-- 3. requisicoes: motorista_id, coluna de transporte, indice e CHECK
-- ---------------------------------------------------------------------------
alter table public.requisicoes
  add column if not exists motorista_id          uuid,
  add column if not exists motorista_alterado_por uuid;

alter table public.requisicoes drop constraint if exists fk_requisicoes_motorista;
alter table public.requisicoes
  add constraint fk_requisicoes_motorista
  foreign key (motorista_id) references public.perfis (id) on delete restrict;

comment on column public.requisicoes.motorista_id is
  'Motorista designado (perfil com role motorista, ativo na designacao). Nulo ate ser definido; so a partir de autorizada. Recurso opcional.';
comment on column public.requisicoes.motorista_alterado_por is
  'Transporte: autorizador/admin que designa, troca ou remove o motorista. Validado e zerado pelo trigger; nunca persiste.';

create index if not exists ix_requisicoes_motorista_id
  on public.requisicoes (motorista_id)
  where motorista_id is not null;

alter table public.requisicoes drop constraint if exists ck_requisicoes_motorista_status;
alter table public.requisicoes
  add constraint ck_requisicoes_motorista_status
  check (motorista_id is null or status in ('autorizada', 'em_uso', 'concluida', 'cancelada'));

-- ---------------------------------------------------------------------------
-- 4. Trigger de validacao + auditoria da designacao (BEFORE INSERT OR UPDATE)
-- Auditoria gravada aqui (e nao em AFTER) porque motorista_alterado_por precisa ser zerado na mesma
-- linha; se o UPDATE falhar depois, a linha de auditoria e desfeita junto (padrao de configuracoes).
-- Leitura do flag SEM FOR SHARE de proposito (mesma razao da migration 18): sem ordem de lock nova.
-- ---------------------------------------------------------------------------
create or replace function public.requisicoes_validar_motorista()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator        uuid := new.motorista_alterado_por;
  v_ator_role   public.role_usuario;
  v_ator_ativo  boolean;
  v_ligado      boolean;
  v_mot_role    public.role_usuario;
  v_mot_ativo   boolean;
begin
  new.motorista_alterado_por := null;  -- nunca persiste

  if tg_op = 'INSERT' then
    if new.motorista_id is not null or v_ator is not null then
      raise exception 'Motorista só pode ser definido após a autorização.' using errcode = 'AG032';
    end if;
    return new;
  end if;

  if new.motorista_id is not distinct from old.motorista_id then
    if v_ator is not null then
      raise exception 'Responsável pelo motorista informado sem mudança de motorista.'
        using errcode = 'AG004';
    end if;
    return new;
  end if;

  -- Mudanca de motorista (definir, trocar ou remover).
  if new.status not in ('autorizada', 'em_uso') then
    raise exception 'O motorista só pode ser definido ou alterado com a requisição autorizada ou em uso.'
      using errcode = 'AG032';
  end if;

  if v_ator is null then
    raise exception 'Designação de motorista exige o responsável registrado.' using errcode = 'AG002';
  end if;
  select role, ativo into v_ator_role, v_ator_ativo from public.perfis where id = v_ator for share;
  if not found or not v_ator_ativo or v_ator_role not in ('autorizador', 'admin') then
    raise exception 'Apenas autorizador ou admin ativo pode definir o motorista.' using errcode = 'AG002';
  end if;

  if new.motorista_id is not null then
    select motorista into v_ligado from public.configuracoes where id;
    if not coalesce(v_ligado, false) then
      raise exception 'O Perfil Motorista está desligado em Configurações.' using errcode = 'AG033';
    end if;

    select role, ativo into v_mot_role, v_mot_ativo
      from public.perfis where id = new.motorista_id for share;
    if not found or not v_mot_ativo or v_mot_role is distinct from 'motorista'::public.role_usuario then
      raise exception 'Motorista inexistente, inativo ou sem o perfil de motorista.' using errcode = 'AG031';
    end if;
  end if;

  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
  values ('requisicoes', new.id, 'motorista_designado', v_ator,
          old.motorista_id::text, new.motorista_id::text,
          jsonb_build_object('recurso', 'motorista', 'status', new.status::text));

  return new;
end;
$$;

revoke all on function public.requisicoes_validar_motorista()
  from public, anon, authenticated, service_role;

drop trigger if exists trg_requisicoes_validar_motorista on public.requisicoes;
create trigger trg_requisicoes_validar_motorista
  before insert or update on public.requisicoes
  for each row execute function public.requisicoes_validar_motorista();
alter table public.requisicoes enable always trigger trg_requisicoes_validar_motorista;

-- ---------------------------------------------------------------------------
-- Autoverificacao (falha a migration inteira se algo estiver fora do esperado)
-- ---------------------------------------------------------------------------
do $$
declare
  v        text;
  d_acao   text;
  v_oid    oid;
begin
  -- Role presente no enum.
  if not exists (select 1 from pg_enum e join pg_type t on t.oid = e.enumtypid
                  where t.typname = 'role_usuario' and e.enumlabel = 'motorista') then
    raise exception 'role_usuario sem o valor motorista (rode a migration 20261111120000 antes).';
  end if;

  -- Flags: boolean, not null, default false.
  for v in select unnest(array['motorista', 'motorista_registra']) loop
    if not exists (select 1 from information_schema.columns
                    where table_schema = 'public' and table_name = 'configuracoes'
                      and column_name = v and data_type = 'boolean'
                      and is_nullable = 'NO' and column_default = 'false') then
      raise exception 'configuracoes.% deve ser boolean not null default false.', v;
    end if;
  end loop;

  -- A linha unica nao pode ter nascido com o recurso ligado.
  if exists (select 1 from public.configuracoes where motorista_registra and not motorista) then
    raise exception 'configuracoes com motorista_registra sem motorista.';
  end if;

  -- CHECKs.
  for v in select unnest(array['ck_configuracoes_motorista_registra_exige_motorista']) loop
    if not exists (select 1 from pg_constraint where conrelid = 'public.configuracoes'::regclass
                      and conname = v and contype = 'c' and convalidated) then
      raise exception 'Constraint % ausente em configuracoes.', v;
    end if;
  end loop;
  for v in select unnest(array['ck_requisicoes_motorista_status']) loop
    if not exists (select 1 from pg_constraint where conrelid = 'public.requisicoes'::regclass
                      and conname = v and contype = 'c' and convalidated) then
      raise exception 'Constraint % ausente em requisicoes.', v;
    end if;
  end loop;

  -- FK sem exclusao em cascata (RESTRICT = 'r').
  if not exists (select 1 from pg_constraint where conrelid = 'public.requisicoes'::regclass
                    and conname = 'fk_requisicoes_motorista' and contype = 'f'
                    and confdeltype = 'r' and confrelid = 'public.perfis'::regclass) then
    raise exception 'fk_requisicoes_motorista ausente ou sem ON DELETE RESTRICT.';
  end if;

  -- Indice.
  if to_regclass('public.ix_requisicoes_motorista_id') is null then
    raise exception 'ix_requisicoes_motorista_id ausente.';
  end if;

  -- Acoes de auditoria: nenhuma perdida, a nova presente.
  select pg_get_constraintdef(oid) into d_acao from pg_constraint
   where conrelid = 'public.auditoria'::regclass and conname = 'ck_auditoria_acao';
  foreach v in array array[
    'criada', 'transicao', 'convite_criado', 'convite_usado', 'convite_revogado',
    'perfil_criado', 'perfil_alterado', 'perfil_unidade_alterada', 'admin_semeado',
    'configuracao_alterada', 'veiculo_criado', 'veiculo_alterado', 'acao_administrativa',
    'acesso_negado', 'checklist_registrado', 'foto_perfil_alterada', 'foto_perfil_removida',
    'relatorio_gerado', 'relatorio_enviado', 'motorista_designado'] loop
    if d_acao is null or position('''' || v || '''' in d_acao) = 0 then
      raise exception 'ck_auditoria_acao sem o valor %.', v;
    end if;
  end loop;

  -- Triggers: existem e em ENABLE ALWAYS ('A').
  if not exists (select 1 from pg_trigger
                  where tgrelid = 'public.requisicoes'::regclass and tgname = 'trg_requisicoes_validar_motorista'
                    and tgenabled = 'A' and not tgisinternal) then
    raise exception 'trg_requisicoes_validar_motorista ausente ou nao esta em ENABLE ALWAYS.';
  end if;
  if not exists (select 1 from pg_trigger
                  where tgrelid = 'public.configuracoes'::regclass and tgname = 'trg_configuracoes_validar'
                    and tgenabled <> 'D' and not tgisinternal) then
    raise exception 'trg_configuracoes_validar ausente ou desabilitado.';
  end if;

  -- Funcoes novas/alteradas: ninguem da API executa (so o Postgres, como trigger).
  foreach v in array array['public.requisicoes_validar_motorista()', 'public.configuracoes_validar()'] loop
    v_oid := to_regprocedure(v);
    if v_oid is null then
      raise exception 'Funcao % ausente.', v;
    end if;
    if has_function_privilege('anon', v_oid, 'execute')
       or has_function_privilege('authenticated', v_oid, 'execute')
       or has_function_privilege('service_role', v_oid, 'execute') then
      raise exception 'Funcao % executavel por anon/authenticated/service_role.', v;
    end if;
  end loop;

  -- RLS deny-by-default intacto e sem acesso direto da API as tabelas tocadas.
  for v in select unnest(array['requisicoes', 'configuracoes', 'auditoria', 'perfis']) loop
    if not (select relrowsecurity from pg_class where oid = ('public.' || v)::regclass) then
      raise exception 'RLS desligado em public.%.', v;
    end if;
    if has_table_privilege('anon', 'public.' || v, 'select, insert, update, delete')
       or has_table_privilege('authenticated', 'public.' || v, 'select, insert, update, delete') then
      raise exception 'anon/authenticated com privilegio direto em public.%.', v;
    end if;
  end loop;
  if has_table_privilege('service_role', 'public.requisicoes', 'delete')
     or has_table_privilege('service_role', 'public.requisicoes', 'truncate') then
    raise exception 'service_role com DELETE/TRUNCATE em requisicoes (sem DELETE fisico).';
  end if;
end
$$;
