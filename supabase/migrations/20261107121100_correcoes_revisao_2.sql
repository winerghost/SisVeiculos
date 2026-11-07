-- Migration 11: correcoes da segunda revisao de seguranca (migrations 6-10).
-- Nao edita migrations anteriores: reescreve funcoes com create or replace.
--
-- DECISAO (v1) mantida: o ator vem de colunas da linha (alterado_por, criado_por...);
-- o Flask (service_role) e confiavel para informar o ator. O pytest de IDOR/permissoes
-- do backend continua obrigatorio.
--
-- Corrige:
--  A1 (alto)  Inserir perfil admin exige admin ativo como responsavel (AG002), sob o
--             mesmo advisory lock da migration 7. Sem ator SOMENTE se nao existir
--             nenhum admin ativo (bootstrap do seed). consumir_convite passa
--             alterado_por = criado_por do convite quando o convite e de admin.
--  A2 (alto)  consumir_convite e o seed exigem conta do Auth confirmada
--             (email_confirmed_at), nao apagada (deleted_at) e nao banida
--             (banned_until nulo ou vencido). Colunas de auth.users usadas:
--             email_confirmed_at, banned_until, deleted_at (existem no schema auth
--             do Supabase/GoTrue; a funcao e plpgsql, entao um nome errado so
--             apareceria na execucao: os testes cobrem os tres casos).
--  M1         convites: criado_em forcado a clock_timestamp() no INSERT; com isso o
--             CHECK ck_convites_prazo (expira_em <= criado_em + 30 dias) passa a ser
--             efetivo (antes o chamador podia mandar criado_em futuro).
--  M3         default privileges: funcoes futuras criadas por postgres nascem sem
--             EXECUTE para PUBLIC em qualquer schema (grants explicitos por migration).
--  M6         registrar_auditoria valida tamanho de de/para (<= 64) e existencia do
--             ator; CHECKs equivalentes em auditoria (linhas existentes sao curtas).
--  B1         exigir_admin_ativo le o ator com FOR SHARE (nao pode ser desativado ou
--             rebaixado por outra transacao ate o fim desta). Risco residual: um
--             deadlock raro entre revogar convite de admin e consumi-lo enquanto o
--             criador e alterado; o Postgres detecta e aborta uma das transacoes.
--  B2         Indice unico parcial: um convite aberto por e-mail
--             (usado_em e revogado_em nulos). NAO inclui expira_em (nao e imutavel
--             no predicado): convite expirado e nao usado continua bloqueando novo
--             convite ate ser revogado; o backend deve revogar antes de reconvidar.
--             Se houver duplicados abertos em dados antigos, esta migration falha de
--             proposito (revogar os duplicados antes).
--  B4         Uso do convite (usado_em/usado_por) so e aceito durante consumir_convite,
--             por variavel de sessao local (sisveiculos.convite_em_consumo =
--             '<convite>:<usuario>') e usado_por precisa ser o usuario do perfil criado.
--             LIMITE: protege contra UPDATE direto acidental/indevido do backend; quem
--             tem SQL livre como service_role ainda poderia forjar a variavel (nao ha
--             como provar o chamador dentro de um trigger). Defesa em profundidade.
--  Testes     supabase/tests/test_correcoes_revisao_2.py.
--
-- PENDENTES (decisao do usuario ou pipeline; NAO tratados aqui):
--  M2 (SELECT de authenticated), M4 (seed no pipeline), M5, M8,
--  autoautorizacao (autorizador autorizar a propria requisicao), B6.
--
-- CHECKLIST DE DEPLOY (fora do banco): desligar o signup publico do Supabase Auth
-- (Authentication > Sign In / Providers > "Allow new users to sign up" = off; local:
-- [auth] enable_signup = false). Sem isso, qualquer pessoa cria conta no Auth e o
-- e-mail confirmado passa a ser a unica barreira. Tambem desabilitar login anonimo.
--
-- Novo codigo: 22023 (invalid_parameter_value) para de/para acima de 64 caracteres.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- B1. exigir_admin_ativo com FOR SHARE no ator
-- ---------------------------------------------------------------------------
create or replace function privado.exigir_admin_ativo(p_ator uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role  public.role_usuario;
  v_ativo boolean;
begin
  if p_ator is null then
    raise exception 'Ação administrativa exige o responsável registrado.' using errcode = 'AG002';
  end if;
  select role, ativo into v_role, v_ativo from public.perfis where id = p_ator for share;
  if not found or not v_ativo or v_role <> 'admin' then
    raise exception 'Apenas administrador ativo pode executar esta ação.' using errcode = 'AG002';
  end if;
end;
$$;

revoke all on function privado.exigir_admin_ativo(uuid) from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- A1. perfis_guarda_admin: INSERT de admin exige admin ativo (exceto bootstrap)
-- ---------------------------------------------------------------------------
create or replace function public.perfis_guarda_admin()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator   uuid := new.alterado_por;
  v_outros integer;
begin
  new.alterado_por := null;  -- nunca persiste

  if tg_op = 'INSERT' then
    if new.role = 'admin' then
      -- Mesma chave da migration 7 e do seed: serializa criacao e alteracao de admins.
      perform pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0));
      -- Bootstrap: sem nenhum admin ativo, o primeiro nasce sem ator (seed).
      -- Havendo admin ativo, o responsavel precisa ser um deles.
      if exists (select 1 from public.perfis where role = 'admin' and ativo) then
        perform privado.exigir_admin_ativo(v_ator);
      end if;
    end if;
    return new;
  end if;

  if new.id is distinct from old.id then
    raise exception 'O id do perfil é imutável.' using errcode = 'AG004';
  end if;

  -- Nada de role/ativo mudou: nao e acao administrativa sobre papel.
  if new.role is not distinct from old.role and new.ativo is not distinct from old.ativo then
    return new;
  end if;

  -- Serializa alteracoes de papel/ativo (mesma chave usada no seed).
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0));

  perform privado.exigir_admin_ativo(v_ator);

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

  return new;
end;
$$;

revoke all on function public.perfis_guarda_admin() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- M1 + B4. convites_validar: criado_em do servidor; uso so via consumir_convite
-- ---------------------------------------------------------------------------
create or replace function public.convites_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    perform privado.exigir_admin_ativo(new.criado_por);
    if new.usado_em is not null or new.revogado_em is not null then
      raise exception 'Convite deve nascer aberto.' using errcode = 'AG001';
    end if;
    -- M1: o chamador nao escolhe criado_em. Como o CHECK ck_convites_prazo roda depois
    -- deste trigger, expira_em <= criado_em + 30 dias passa a valer de fato.
    new.criado_em := clock_timestamp();
    return new;
  end if;

  -- UPDATE: identidade e conteudo do convite sao imutaveis.
  if new.id is distinct from old.id
     or new.email is distinct from old.email
     or new.role is distinct from old.role
     or new.unidade is distinct from old.unidade
     or new.token_hash is distinct from old.token_hash
     or new.criado_por is distinct from old.criado_por
     or new.criado_em is distinct from old.criado_em
     or new.expira_em is distinct from old.expira_em then
    raise exception 'Dados do convite são imutáveis.' using errcode = 'AG004';
  end if;

  -- Convite encerrado (usado ou revogado): congelado.
  if old.usado_em is not null or old.revogado_em is not null then
    if new.usado_em is distinct from old.usado_em
       or new.usado_por is distinct from old.usado_por
       or new.revogado_em is distinct from old.revogado_em
       or new.revogado_por is distinct from old.revogado_por then
      raise exception 'Convite já usado ou revogado.' using errcode = 'AG005';
    end if;
    return new;
  end if;

  -- Uso: so antes de expirar. O carimbo e sempre do servidor.
  if new.usado_em is not null then
    if old.expira_em <= clock_timestamp() then
      raise exception 'Convite expirado.' using errcode = 'AG006';
    end if;
    -- B4: so durante consumir_convite e para o usuario cujo perfil acabou de ser criado.
    if coalesce(current_setting('sisveiculos.convite_em_consumo', true), '')
       is distinct from (new.id::text || ':' || coalesce(new.usado_por::text, '')) then
      raise exception 'O convite só pode ser usado por consumir_convite.' using errcode = 'AG004';
    end if;
    new.usado_em := now();
  end if;

  -- Revogacao: so admin ativo.
  if new.revogado_em is not null then
    perform privado.exigir_admin_ativo(new.revogado_por);
    new.revogado_em := now();
  end if;

  return new;
end;
$$;

revoke all on function public.convites_validar() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- B2. Um convite aberto por e-mail
-- ---------------------------------------------------------------------------
create unique index if not exists ux_convites_email_aberto
  on public.convites (email)
  where usado_em is null and revogado_em is null;

-- ---------------------------------------------------------------------------
-- A1 + A2 + B4. consumir_convite
-- ---------------------------------------------------------------------------
create or replace function public.consumir_convite(
  p_token_hash text,
  p_usuario_id uuid,
  p_nome       text
)
returns public.perfis
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_conv      public.convites;
  v_email     text;
  v_confirmado timestamptz;
  v_banido    timestamptz;
  v_apagado   timestamptz;
  v_perfil    public.perfis;
begin
  select * into v_conv from public.convites where token_hash = p_token_hash for update;
  if not found then
    raise exception 'Convite inválido.' using errcode = 'AG007';
  end if;
  if v_conv.usado_em is not null or v_conv.revogado_em is not null then
    raise exception 'Convite já usado ou revogado.' using errcode = 'AG005';
  end if;
  if v_conv.expira_em <= clock_timestamp() then
    raise exception 'Convite expirado.' using errcode = 'AG006';
  end if;

  select lower(u.email), u.email_confirmed_at, u.banned_until, u.deleted_at
    into v_email, v_confirmado, v_banido, v_apagado
    from auth.users u
   where u.id = p_usuario_id;

  -- A2: e-mail igual ao convidado E conta confirmada, ativa e nao apagada.
  -- Mesma mensagem/codigo para tudo (nao revela qual condicao falhou).
  if v_email is distinct from v_conv.email
     or v_confirmado is null
     or v_apagado is not null
     or (v_banido is not null and v_banido > clock_timestamp()) then
    raise exception 'Convite inválido.' using errcode = 'AG007';
  end if;

  -- A1: convite de admin cria perfil admin tendo como responsavel quem o emitiu
  -- (que precisa continuar admin ativo: o trigger de perfis revalida sob o lock).
  insert into public.perfis (id, nome, unidade, role, alterado_por)
  values (p_usuario_id, p_nome, v_conv.unidade, v_conv.role,
          case when v_conv.role = 'admin' then v_conv.criado_por end)
  returning * into v_perfil;

  -- B4: autoriza o UPDATE de uso apenas para este convite e este usuario (local a transacao).
  perform set_config('sisveiculos.convite_em_consumo', v_conv.id::text || ':' || p_usuario_id::text, true);
  update public.convites set usado_em = now(), usado_por = p_usuario_id where id = v_conv.id;
  perform set_config('sisveiculos.convite_em_consumo', '', true);

  return v_perfil;
end;
$$;

revoke all on function public.consumir_convite(text, uuid, text) from public, anon, authenticated;
grant execute on function public.consumir_convite(text, uuid, text) to service_role;

-- ---------------------------------------------------------------------------
-- A2. Seed do primeiro admin: conta confirmada, nao apagada, nao banida
-- ---------------------------------------------------------------------------
create or replace function privado.semear_primeiro_admin(
  p_email   text,
  p_nome    text default 'Administrador inicial',
  p_unidade public.unidade default 'UEA'
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_email      text := lower(btrim(coalesce(p_email, '')));
  v_id         uuid;
  v_confirmado timestamptz;
  v_banido     timestamptz;
  v_apagado    timestamptz;
begin
  if v_email = '' then
    return 'sem_email';
  end if;

  -- Mesma chave do trigger de perfis: sem corrida com criacao/alteracao de admins.
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0));

  if exists (select 1 from public.perfis where role = 'admin' and ativo) then
    return 'ja_existe_admin';
  end if;

  select u.id, u.email_confirmed_at, u.banned_until, u.deleted_at
    into v_id, v_confirmado, v_banido, v_apagado
    from auth.users u
   where lower(u.email) = v_email;
  if v_id is null then
    return 'conta_auth_inexistente';
  end if;

  if v_confirmado is null
     or v_apagado is not null
     or (v_banido is not null and v_banido > clock_timestamp()) then
    return 'conta_auth_invalida';
  end if;

  if exists (select 1 from public.perfis where id = v_id) then
    return 'perfil_ja_existe';
  end if;

  insert into public.perfis (id, nome, unidade, role)
  values (v_id, p_nome, p_unidade, 'admin');

  insert into public.auditoria (tabela, registro_id, acao, ator_id, para)
  values ('perfis', v_id, 'admin_semeado', null, 'admin/ativo');

  return 'criado';
end;
$$;

revoke all on function privado.semear_primeiro_admin(text, text, public.unidade)
  from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- M6. registrar_auditoria + CHECKs de tamanho em auditoria
-- ---------------------------------------------------------------------------
create or replace function public.registrar_auditoria(
  p_tabela      text,
  p_registro_id uuid,
  p_acao        text,
  p_ator_id     uuid,
  p_de          text default null,
  p_para        text default null,
  p_detalhes    jsonb default '{}'::jsonb
)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_id bigint;
begin
  if p_acao not in ('acao_administrativa', 'acesso_negado') then
    raise exception 'Ação de auditoria não permitida por esta função.' using errcode = 'AG003';
  end if;
  if char_length(p_de) > 64 or char_length(p_para) > 64 then
    raise exception 'Campos de/para da auditoria aceitam no máximo 64 caracteres.' using errcode = '22023';
  end if;
  if p_ator_id is not null and not exists (select 1 from public.perfis where id = p_ator_id) then
    raise exception 'Responsável informado na auditoria não existe.' using errcode = 'AG002';
  end if;
  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
  values (p_tabela, p_registro_id, p_acao, p_ator_id, p_de, p_para, coalesce(p_detalhes, '{}'::jsonb))
  returning id into v_id;
  return v_id;
end;
$$;

revoke all on function public.registrar_auditoria(text, uuid, text, uuid, text, text, jsonb)
  from public, anon, authenticated;
grant execute on function public.registrar_auditoria(text, uuid, text, uuid, text, text, jsonb)
  to service_role;

-- Linhas existentes tem de/para curtos ('role/ativo', status, 'true'/'false').
alter table public.auditoria drop constraint if exists ck_auditoria_de_tamanho;
alter table public.auditoria
  add constraint ck_auditoria_de_tamanho check (char_length(de) <= 64) not valid;
alter table public.auditoria validate constraint ck_auditoria_de_tamanho;

alter table public.auditoria drop constraint if exists ck_auditoria_para_tamanho;
alter table public.auditoria
  add constraint ck_auditoria_para_tamanho check (char_length(para) <= 64) not valid;
alter table public.auditoria validate constraint ck_auditoria_para_tamanho;

-- ---------------------------------------------------------------------------
-- M3. Funcoes futuras criadas por postgres: sem EXECUTE para PUBLIC (global)
-- ---------------------------------------------------------------------------
alter default privileges for role postgres revoke execute on functions from public;
