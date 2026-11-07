-- Migration 12: correcoes da terceira revisao (migration 11).
-- Nao edita migrations anteriores: reescreve funcoes com create or replace
-- (create or replace preserva a ACL; revoke/grant repetidos por seguranca).
--
-- Corrige:
--  H1 (alto)  privado.exigir_admin_ativo trocava o FOR SHARE da linha do ator (migration 11,
--             item B1) por um lock consultivo COMPARTILHADO
--             pg_advisory_xact_lock_shared(hashtextextended('sisveiculos.perfis_admin', 0)),
--             a mesma chave do lock EXCLUSIVO de perfis_guarda_admin e do seed. Motivo: o
--             FOR SHARE na linha podia dar deadlock (40P01) entre UPDATE de perfis (que
--             trava a linha e depois pede o lock exclusivo) e acoes de convite (que travam
--             a linha do ator e depois pedem o lock). Agora ha UM unico recurso de lock e a
--             ordem de aquisicao e sempre a mesma: nao ha ciclo entre eles. Efeito mantido:
--             enquanto a transacao do convite/revogacao nao termina, ninguem consegue mudar
--             role/ativo de admins (o exclusivo espera os compartilhados).
--             Limite: ate dois compartilhados simultaneos NAO bloqueiam entre si; uma
--             transacao que ja segura o compartilhado e depois altera role/ativo de perfil
--             (pede o exclusivo) pode entrar em deadlock com outra igual. O backend nao
--             deve misturar as duas coisas na mesma transacao.
--  M1         O ramo de bootstrap de perfis_guarda_admin (INSERT de admin sem nenhum admin
--             ativo) so vale se privado.semear_primeiro_admin tiver feito
--             set_config('sisveiculos.seed_em_curso', '1', true) na mesma transacao. Sem a
--             marca, o INSERT e recusado (AG002). Isto fecha o caminho ACIDENTAL (ex.: um
--             insert do backend quando a base esta sem admin ativo), mas NAO SQL livre: quem
--             executa SQL arbitrario como service_role/postgres ainda pode forjar a variavel
--             (trigger nao prova o chamador). Defesa em profundidade.
--  B2         INSERT de admin com ator conhecido gera a linha 'perfil_criado' de auditoria
--             com ator_id = o ator. O guarda (BEFORE) grava o ator em uma variavel de sessao
--             local POR PERFIL (sisveiculos.ator_criacao_<id sem hifens>); perfis_auditar
--             (AFTER) a consome e grava UMA unica linha. Variavel por id: correta em INSERT
--             multi-linha e em ON CONFLICT DO NOTHING (nada orfao na auditoria); a FK de
--             ator e satisfeita porque o ator ja existe. Mesmo limite do M1 quanto a SQL livre
--             (a FK ainda exige que o ator exista em perfis).
--  B1         O comentario do B4 da migration 11 dizia que usado_por "precisa ser o usuario
--             do perfil criado", mas o trigger so comparava a variavel de sessao com
--             usado_por; nao verificava o perfil. Agora convites_validar exige que exista
--             perfis.id = new.usado_por criado NA MESMA transacao
--             (criado_em = transaction_timestamp()). Limite documentado: quem tem SQL livre
--             ainda pode inserir o perfil e forjar a variavel na mesma transacao; o teste
--             test_correcoes_revisao_3.py registra esse limite.
--  Testes     supabase/tests/test_correcoes_revisao_3.py (novo); ajustes em test_convites.py
--             (M2: nao deixar convites abertos para e-mails fixos) e em
--             test_correcoes_revisao_2.py (">= 1" em vez de "!= (0,)").
--
-- ANOTACOES (sem alteracao de codigo):
--  B3  Extensoes instaladas no futuro precisam de grant explicito (as default privileges
--      da migration 11 removem EXECUTE de PUBLIC em funcoes novas criadas por postgres).
--  B4  O seed (privado.semear_primeiro_admin) deve rodar no pipeline DEPOIS de todas as
--      migrations; nao e executado por migration.
--
-- PENDENTES (decisao do usuario; NAO tratados aqui):
--  M2/SELECT de authenticated, M4, M5, M8, autoautorizacao (autorizador autorizar a propria
--  requisicao), B6.
--
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- H1. exigir_admin_ativo com lock consultivo compartilhado (sem FOR SHARE)
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
  -- Compartilhado: varias acoes de convite convivem; o exclusivo de perfis_guarda_admin
  -- (mudanca de role/ativo) espera todas terminarem e vice-versa. Retomado na mesma sessao
  -- sem conflito quando o chamador ja segura o exclusivo.
  perform pg_advisory_xact_lock_shared(hashtextextended('sisveiculos.perfis_admin', 0));
  -- Leitura apos o lock (READ COMMITTED: enxerga o que ja foi comitado).
  select role, ativo into v_role, v_ativo from public.perfis where id = p_ator;
  if not found or not v_ativo or v_role <> 'admin' then
    raise exception 'Apenas administrador ativo pode executar esta ação.' using errcode = 'AG002';
  end if;
end;
$$;

revoke all on function privado.exigir_admin_ativo(uuid) from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- M1 + B2. perfis_guarda_admin: bootstrap so com seed em curso; ator da criacao
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
      if exists (select 1 from public.perfis where role = 'admin' and ativo) then
        -- Havendo admin ativo, o responsavel precisa ser um deles.
        perform privado.exigir_admin_ativo(v_ator);
        -- B2: informa o ator a perfis_auditar (AFTER), por perfil.
        perform set_config('sisveiculos.ator_criacao_' || replace(new.id::text, '-', ''),
                           v_ator::text, true);
      elsif coalesce(current_setting('sisveiculos.seed_em_curso', true), '') <> '1' then
        -- M1: bootstrap (sem admin ativo) so durante privado.semear_primeiro_admin.
        raise exception 'O primeiro administrador só pode ser criado pelo seed.' using errcode = 'AG002';
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
-- B2. perfis_auditar: 'perfil_criado' com o ator informado pelo guarda (se houver)
-- ---------------------------------------------------------------------------
create or replace function public.perfis_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_chave text;
  v_ator  uuid;
begin
  if tg_op = 'INSERT' then
    v_chave := 'sisveiculos.ator_criacao_' || replace(new.id::text, '-', '');
    v_ator := nullif(current_setting(v_chave, true), '')::uuid;
    if v_ator is not null then
      perform set_config(v_chave, '', true);  -- consumido
    end if;
    insert into public.auditoria (tabela, registro_id, acao, ator_id, para)
    values ('perfis', new.id, 'perfil_criado', v_ator,
            new.role::text || '/' || case when new.ativo then 'ativo' else 'inativo' end);
  elsif new.unidade is distinct from old.unidade then
    -- role/ativo sao auditados (com ator) pelo trigger da migration 7.
    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para)
    values ('perfis', new.id, 'perfil_unidade_alterada', null, old.unidade::text, new.unidade::text);
  end if;
  return null;
end;
$$;

revoke all on function public.perfis_auditar() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- M1. Seed marca a transacao como seed em curso
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

  -- M1: unica marca que libera o bootstrap no trigger; local a transacao e desfeita logo apos.
  perform set_config('sisveiculos.seed_em_curso', '1', true);
  insert into public.perfis (id, nome, unidade, role)
  values (v_id, p_nome, p_unidade, 'admin');
  perform set_config('sisveiculos.seed_em_curso', '', true);

  insert into public.auditoria (tabela, registro_id, acao, ator_id, para)
  values ('perfis', v_id, 'admin_semeado', null, 'admin/ativo');

  return 'criado';
end;
$$;

revoke all on function privado.semear_primeiro_admin(text, text, public.unidade)
  from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- B1. convites_validar: usado_por precisa ter perfil criado nesta transacao
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
    -- O chamador nao escolhe criado_em (ck_convites_prazo roda depois deste trigger).
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
    -- So durante consumir_convite e para o usuario cujo perfil acabou de ser criado.
    if coalesce(current_setting('sisveiculos.convite_em_consumo', true), '')
       is distinct from (new.id::text || ':' || coalesce(new.usado_por::text, '')) then
      raise exception 'O convite só pode ser usado por consumir_convite.' using errcode = 'AG004';
    end if;
    -- B1: o perfil de usado_por tem de ter sido criado NESTA transacao (como consumir_convite faz).
    if not exists (select 1 from public.perfis
                    where id = new.usado_por and criado_em = transaction_timestamp()) then
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
