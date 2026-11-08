-- Migration 25a: FOTO DE PERFIL, parte 1 (tabela, imutabilidade, RLS, gatilhos, RPCs e auditoria).
-- Nova migration; nao edita as anteriores. Idempotente. Fica entre a 24 (122400) e as migrations
-- pendentes do Abastecimento (122500-122800, fora de supabase/migrations/).
--
-- Decisao (aprovada): recurso SEMPRE LIGADO, foto OPCIONAL por usuario, SEM flag em
-- `configuracoes`. A foto fica em tabela SEPARADA (`perfil_fotos`), nao em colunas de `perfis`.
-- Motivo: `perfis` tem gatilho de comando (advisory global `sisveiculos.perfis_admin`, migration 14)
-- e o contrato da migration 15 (locks). Esta migration NAO escreve em `perfis`, portanto nao
-- toma `perfis_admin`, nao disputa o 55P03 e nao conflita com o FOR SHARE do contrato da 15.
--
-- Tabela public.perfil_fotos:
--  * 1 linha por foto enviada; `removida_em` (NULL = ativa) faz a remocao LOGICA. Uma foto ATIVA
--    por usuario: indice unico PARCIAL (usuario_id) WHERE removida_em IS NULL.
--  * `caminho` GERADA e armazenada: <usuario_id>/<id da foto>.<ext> (ext deriva do mime). Nao e
--    informavel (INSERT com caminho => 428C9); uma foto nao aponta para objeto de outro usuario.
--  * IMUTAVEL: UPDATE so e aceito quando `removida_em` passa de NULL para um valor e nada mais
--    muda (AG003 caso contrario); DELETE e TRUNCATE bloqueados (AG003), ENABLE ALWAYS.
--  * Sem dado pessoal na tabela: so ids, mime e tamanho. O arquivo (a imagem, dado pessoal) fica
--    no bucket privado `avatares` (migration 25b); a linha nao guarda nome de arquivo original.
--
-- RLS/grants (deny-by-default): RLS ligado com politica explicita de negacao; anon/authenticated
-- sem nenhum privilegio; service_role SOMENTE SELECT (a migration falha se sobrar privilegio).
-- Escrita so pelas RPCs abaixo.
--
-- Validacao no gatilho BEFORE INSERT (vale para a RPC e para INSERT direto do dono): mensagens
-- FIXAS, sem DETAIL e sem conteudo da linha (campos de diagnostico schema/table/column/constraint
-- preenchidos), como nas migrations 23/24. Codigos: 23502 (campo obrigatorio), 23514
-- (ck_perfil_fotos_mime / ck_perfil_fotos_tamanho), AG017 (usuario inativo). Usuario inexistente
-- no INSERT direto => FK 23503; nas RPCs => AG017.
--
-- NOVO CODIGO DE ERRO: AG017 = usuario inexistente ou inativo para a foto de perfil.
--   Em uso hoje: AG001-AG014 (ativas) e AG015/AG016 (reservados pelas pendentes do Abastecimento).
--   AG018 permanece livre.
--
-- RPCs (SECURITY DEFINER, search_path vazio, EXECUTE so para service_role), uma transacao cada:
--   trocar_foto_perfil(p_usuario, p_id, p_mime, p_tamanho) -> (id_anterior, caminho_anterior)
--   remover_foto_perfil(p_usuario)                         -> (id_anterior, caminho_anterior)
-- O backend deve apagar o objeto anterior do Storage DEPOIS do commit, usando o caminho devolvido
-- (sempre do PROPRIO usuario; nunca de outro). Sem foto anterior, as colunas vem NULL.
-- O `p_usuario` vem do JWT validado no backend, nunca do cliente: o banco confia no ator
-- informado pelo service_role, como nas demais RPCs (registrar_checklist etc.).
-- `p_id` e gerado pelo backend (uuid) para ele calcular o caminho `<usuario>/<p_id>.<ext>` e
-- pedir a URL assinada de upload; o banco recalcula o MESMO caminho (coluna gerada).
-- remover_foto_perfil NAO exige usuario ativo (remover dado pessoal de conta desativada e
-- legitimo, LGPD); exige que exista (AG017). Idempotente: sem foto ativa devolve NULLs.
--
-- LOCKS (contrato da migration 15 preservado): as RPCs tomam
--   pg_advisory_xact_lock(hashtextextended('sisveiculos.avatar:' || p_usuario, 0))
-- chave PROPRIA POR USUARIO, distinta de 'sisveiculos.perfis_admin'. Serializa duas trocas
-- simultaneas do mesmo usuario (a segunda enxerga a foto ativa da primeira, ja confirmada, e a
-- marca como removida; o indice parcial e a rede de seguranca). ORDEM: esta transacao so toma o
-- lock do avatar; NUNCA tomar 'sisveiculos.perfis_admin' (nem escrever em `perfis`) na mesma
-- transacao, para nao haver ciclo. `perfis` e lido sem lock; a FK so toma FOR KEY SHARE na linha
-- do perfil, que nao conflita com UPDATE de colunas nao-chave. lock_timeout de 5 s (migration 16)
-- vale: 55P03 => retry limitado no backend.
--
-- CONSULTA em lote (backend, service_role tem SELECT):
--   select usuario_id, id, caminho from public.perfil_fotos
--    where usuario_id in (...) and removida_em is null;
-- O indice parcial uq_perfil_fotos_ativa (usuario_id) WHERE removida_em IS NULL cobre exatamente
-- esse predicado. NAO ha view publica. Gerar URL assinada de LEITURA curta no backend, so depois
-- de checar permissao; nunca logar URLs assinadas.
--
-- Auditoria (constraint trigger DEFERRED, sem dado pessoal): 'foto_perfil_alterada' (INSERT) e
-- 'foto_perfil_removida' (UPDATE de removida_em), tabela 'perfil_fotos', ator_id = usuario_id,
-- registro_id = id da foto, detalhes = {mime, tamanho_bytes}. Sem caminho, URL ou nome. Uma troca
-- gera duas linhas (removida da antiga + alterada da nova). ck_auditoria_acao/ck_auditoria_tabela
-- sao recriados repetindo TODOS os valores atuais (15 acoes e 6 tabelas) + 2 acoes e 1 tabela
-- = 17 acoes e 7 tabelas. A recriacao toma ACCESS EXCLUSIVE brevemente em `auditoria` e valida
-- as linhas existentes.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. Tabela
-- ---------------------------------------------------------------------------
create table if not exists public.perfil_fotos (
  id             uuid primary key default gen_random_uuid(),
  usuario_id     uuid not null references public.perfis (id) on delete restrict,
  mime           text not null,
  tamanho_bytes  integer not null,
  -- Caminho no bucket privado 'avatares': <usuario_id>/<id>.<ext>. Calculado.
  caminho        text generated always as (
                   usuario_id::text || '/' || id::text || '.' ||
                   case mime
                     when 'image/jpeg' then 'jpg'
                     when 'image/png'  then 'png'
                     when 'image/webp' then 'webp'
                   end
                 ) stored,
  criado_em      timestamptz not null default now(),
  removida_em    timestamptz,

  constraint ck_perfil_fotos_mime check (mime in ('image/jpeg', 'image/png', 'image/webp')),
  -- 1 MB = 1048576 bytes; tambem imposto pelo bucket (migration 25b).
  constraint ck_perfil_fotos_tamanho check (tamanho_bytes between 1 and 1048576)
);

comment on table public.perfil_fotos is
  'Foto de perfil (opcional, uma ATIVA por usuario). Remocao logica por removida_em; UPDATE de outras colunas, DELETE e TRUNCATE bloqueados. Escrita so via trocar_foto_perfil/remover_foto_perfil.';
comment on column public.perfil_fotos.caminho is
  'GERADO: <usuario_id>/<id>.<ext> no bucket privado avatares. Nao informavel.';
comment on column public.perfil_fotos.removida_em is
  'NULL = foto ativa. Passa uma unica vez de NULL para um valor (remocao logica).';

-- Uma foto ativa por usuario; cobre tambem a consulta em lote por usuario_id (ativas).
create unique index if not exists uq_perfil_fotos_ativa
  on public.perfil_fotos (usuario_id) where removida_em is null;

-- ---------------------------------------------------------------------------
-- 2. Gatilhos de dados (validacao sem DETAIL, imutabilidade)
-- ---------------------------------------------------------------------------
create or replace function public.perfil_fotos_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ativo boolean;
begin
  new.criado_em   := clock_timestamp();  -- carimbo do servidor
  new.removida_em := null;               -- nasce ativa

  -- Campos obrigatorios (NOT NULL). Mensagem fixa: so o nome do campo, nunca valores.
  if new.id is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: id.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'id';
  end if;
  if new.usuario_id is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: usuario_id.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'usuario_id';
  end if;
  if new.mime is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: mime.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'mime';
  end if;
  if new.tamanho_bytes is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: tamanho_bytes.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'tamanho_bytes';
  end if;

  if new.mime not in ('image/jpeg', 'image/png', 'image/webp') then
    raise exception 'Violação da regra ck_perfil_fotos_mime: tipo de imagem não permitido.'
      using errcode = '23514', schema = 'public', table = 'perfil_fotos',
            column = 'mime', constraint = 'ck_perfil_fotos_mime';
  end if;
  if new.tamanho_bytes < 1 or new.tamanho_bytes > 1048576 then
    raise exception 'Violação da regra ck_perfil_fotos_tamanho: tamanho deve ficar entre 1 byte e 1 MB.'
      using errcode = '23514', schema = 'public', table = 'perfil_fotos',
            column = 'tamanho_bytes', constraint = 'ck_perfil_fotos_tamanho';
  end if;

  -- Usuario sem lock (contrato da migration 15). Inexistente: a FK responde (23503).
  select ativo into v_ativo from public.perfis where id = new.usuario_id;
  if not found then
    return new;
  end if;
  if not v_ativo then
    raise exception 'Usuário inativo não pode ter foto de perfil.' using errcode = 'AG017';
  end if;
  return new;
end;
$$;

create or replace function public.perfil_fotos_so_remocao()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if old.removida_em is null
     and new.removida_em is not null
     -- `caminho` fica de fora: coluna gerada, ainda nao recalculada em gatilho BEFORE (NULL em
     -- `new`); ela deriva de usuario_id, id e mime, que estao na comparacao.
     and (new.id, new.usuario_id, new.mime, new.tamanho_bytes, new.criado_em)
         is not distinct from
         (old.id, old.usuario_id, old.mime, old.tamanho_bytes, old.criado_em) then
    return new;
  end if;
  raise exception 'Operação UPDATE proibida em perfil_fotos: só a remoção lógica (removida_em) é permitida, uma única vez.'
    using errcode = 'AG003';
end;
$$;

revoke all on function public.perfil_fotos_validar()    from public, anon, authenticated, service_role;
revoke all on function public.perfil_fotos_so_remocao() from public, anon, authenticated, service_role;

drop trigger if exists trg_perfil_fotos_validar on public.perfil_fotos;
create trigger trg_perfil_fotos_validar
  before insert on public.perfil_fotos
  for each row execute function public.perfil_fotos_validar();
alter table public.perfil_fotos enable always trigger trg_perfil_fotos_validar;

drop trigger if exists trg_perfil_fotos_so_remocao on public.perfil_fotos;
create trigger trg_perfil_fotos_so_remocao
  before update on public.perfil_fotos
  for each row execute function public.perfil_fotos_so_remocao();
alter table public.perfil_fotos enable always trigger trg_perfil_fotos_so_remocao;

drop trigger if exists trg_perfil_fotos_sem_delete on public.perfil_fotos;
create trigger trg_perfil_fotos_sem_delete
  before delete on public.perfil_fotos
  for each row execute function public.bloquear_alteracao();
alter table public.perfil_fotos enable always trigger trg_perfil_fotos_sem_delete;

drop trigger if exists trg_perfil_fotos_sem_truncate on public.perfil_fotos;
create trigger trg_perfil_fotos_sem_truncate
  before truncate on public.perfil_fotos
  for each statement execute function public.bloquear_alteracao();
alter table public.perfil_fotos enable always trigger trg_perfil_fotos_sem_truncate;

-- ---------------------------------------------------------------------------
-- 3. RLS deny-by-default, negacao explicita e grants minimos
-- ---------------------------------------------------------------------------
alter table public.perfil_fotos enable row level security;

drop policy if exists perfil_fotos_sem_acesso_api on public.perfil_fotos;
create policy perfil_fotos_sem_acesso_api on public.perfil_fotos
  for all to anon, authenticated using (false) with check (false);

revoke all on table public.perfil_fotos from public, anon, authenticated;
revoke all on table public.perfil_fotos from service_role;
grant select on table public.perfil_fotos to service_role;

-- ---------------------------------------------------------------------------
-- 4. Auditoria (valores novos nos CHECKs + constraint trigger deferred sem dado pessoal)
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
    'foto_perfil_alterada', 'foto_perfil_removida'
  ));

alter table public.auditoria drop constraint if exists ck_auditoria_tabela;
alter table public.auditoria
  add constraint ck_auditoria_tabela
  check (tabela in ('requisicoes', 'convites', 'perfis', 'veiculos', 'configuracoes', 'checklists',
                    'perfil_fotos'));

do $$
declare
  v text;
  d_acao   text;
  d_tabela text;
begin
  select pg_get_constraintdef(oid) into d_acao from pg_constraint
   where conrelid = 'public.auditoria'::regclass and conname = 'ck_auditoria_acao';
  select pg_get_constraintdef(oid) into d_tabela from pg_constraint
   where conrelid = 'public.auditoria'::regclass and conname = 'ck_auditoria_tabela';

  foreach v in array array[
    'criada', 'transicao', 'convite_criado', 'convite_usado', 'convite_revogado',
    'perfil_criado', 'perfil_alterado', 'perfil_unidade_alterada', 'admin_semeado',
    'configuracao_alterada', 'veiculo_criado', 'veiculo_alterado', 'acao_administrativa',
    'acesso_negado', 'checklist_registrado', 'foto_perfil_alterada', 'foto_perfil_removida'] loop
    if d_acao is null or position('''' || v || '''' in d_acao) = 0 then
      raise exception 'ck_auditoria_acao sem o valor %.', v;
    end if;
  end loop;
  foreach v in array array['requisicoes', 'convites', 'perfis', 'veiculos', 'configuracoes',
                           'checklists', 'perfil_fotos'] loop
    if d_tabela is null or position('''' || v || '''' in d_tabela) = 0 then
      raise exception 'ck_auditoria_tabela sem o valor %.', v;
    end if;
  end loop;
end
$$;

create or replace function public.perfil_fotos_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  -- Sem caminho, URL ou nome de arquivo; ator = o proprio usuario.
  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
  values ('perfil_fotos', new.id,
          case tg_op when 'INSERT' then 'foto_perfil_alterada' else 'foto_perfil_removida' end,
          new.usuario_id, null, null,
          jsonb_build_object('mime', new.mime, 'tamanho_bytes', new.tamanho_bytes));
  return null;
end;
$$;

revoke all on function public.perfil_fotos_auditar() from public, anon, authenticated, service_role;

drop trigger if exists trg_perfil_fotos_auditoria on public.perfil_fotos;
create constraint trigger trg_perfil_fotos_auditoria
  after insert on public.perfil_fotos
  deferrable initially deferred
  for each row execute function public.perfil_fotos_auditar();
alter table public.perfil_fotos enable always trigger trg_perfil_fotos_auditoria;

drop trigger if exists trg_perfil_fotos_auditoria_remocao on public.perfil_fotos;
create constraint trigger trg_perfil_fotos_auditoria_remocao
  after update on public.perfil_fotos
  deferrable initially deferred
  for each row when (old.removida_em is null and new.removida_em is not null)
  execute function public.perfil_fotos_auditar();
alter table public.perfil_fotos enable always trigger trg_perfil_fotos_auditoria_remocao;

-- ---------------------------------------------------------------------------
-- 5. RPCs (uma transacao; EXECUTE so para service_role)
-- ---------------------------------------------------------------------------
create or replace function public.trocar_foto_perfil(
  p_usuario uuid,
  p_id      uuid,
  p_mime    text,
  p_tamanho integer
)
returns table (id_anterior uuid, caminho_anterior text)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ativo boolean;
begin
  if p_usuario is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: usuario_id.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'usuario_id';
  end if;

  select ativo into v_ativo from public.perfis where id = p_usuario;  -- sem lock (contrato 15)
  if not found or not v_ativo then
    raise exception 'Usuário inexistente ou inativo.' using errcode = 'AG017';
  end if;

  -- Serializa trocas do MESMO usuario. Nunca tomar sisveiculos.perfis_admin nesta transacao.
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.avatar:' || p_usuario::text, 0));

  update public.perfil_fotos
     set removida_em = clock_timestamp()
   where usuario_id = p_usuario and removida_em is null
  returning id, caminho into id_anterior, caminho_anterior;

  -- Validacao de conteudo e novo carimbo no gatilho BEFORE INSERT (mensagens fixas).
  insert into public.perfil_fotos (id, usuario_id, mime, tamanho_bytes)
  values (p_id, p_usuario, p_mime, p_tamanho);

  return next;
end;
$$;

create or replace function public.remover_foto_perfil(p_usuario uuid)
returns table (id_anterior uuid, caminho_anterior text)
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_usuario is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: usuario_id.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'usuario_id';
  end if;

  if not exists (select 1 from public.perfis where id = p_usuario) then  -- sem lock (contrato 15)
    raise exception 'Usuário inexistente ou inativo.' using errcode = 'AG017';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.avatar:' || p_usuario::text, 0));

  update public.perfil_fotos
     set removida_em = clock_timestamp()
   where usuario_id = p_usuario and removida_em is null
  returning id, caminho into id_anterior, caminho_anterior;

  return next;  -- idempotente: sem foto ativa devolve NULLs
end;
$$;

revoke all on function public.trocar_foto_perfil(uuid, uuid, text, integer) from public, anon, authenticated;
grant execute on function public.trocar_foto_perfil(uuid, uuid, text, integer) to service_role;
revoke all on function public.remover_foto_perfil(uuid) from public, anon, authenticated;
grant execute on function public.remover_foto_perfil(uuid) to service_role;

-- ---------------------------------------------------------------------------
-- 6. Trava: a migration falha se sobrar acesso indevido ou faltar protecao
-- ---------------------------------------------------------------------------
do $$
declare
  p text;
  f text;
  r text;
begin
  if not (select relrowsecurity from pg_class where oid = 'public.perfil_fotos'::regclass) then
    raise exception 'public.perfil_fotos sem RLS.';
  end if;
  if has_table_privilege('anon', 'public.perfil_fotos', 'select, insert, update, delete')
     or has_table_privilege('authenticated', 'public.perfil_fotos', 'select, insert, update, delete') then
    raise exception 'public.perfil_fotos permite acesso direto a anon/authenticated.';
  end if;
  foreach p in array array['insert', 'update', 'delete', 'truncate', 'trigger', 'references'] loop
    if has_table_privilege('service_role', 'public.perfil_fotos', p) then
      raise exception 'service_role tem % em public.perfil_fotos.', p;
    end if;
  end loop;
  if not has_table_privilege('service_role', 'public.perfil_fotos', 'select') then
    raise exception 'service_role sem SELECT em public.perfil_fotos.';
  end if;

  foreach f in array array['public.perfil_fotos_validar()', 'public.perfil_fotos_so_remocao()',
                           'public.perfil_fotos_auditar()'] loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if has_function_privilege(r, f::regprocedure, 'execute') then
        raise exception '% ainda executa %.', r, f;
      end if;
    end loop;
    if exists (select 1 from pg_proc x, aclexplode(coalesce(x.proacl, acldefault('f', x.proowner))) a
                where x.oid = f::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE') then
      raise exception 'PUBLIC ainda executa %.', f;
    end if;
  end loop;

  foreach f in array array['public.trocar_foto_perfil(uuid, uuid, text, integer)',
                           'public.remover_foto_perfil(uuid)'] loop
    foreach r in array array['anon', 'authenticated'] loop
      if has_function_privilege(r, f::regprocedure, 'execute') then
        raise exception '% executa %.', r, f;
      end if;
    end loop;
    if not has_function_privilege('service_role', f::regprocedure, 'execute') then
      raise exception 'service_role nao executa %.', f;
    end if;
    if exists (select 1 from pg_proc x, aclexplode(coalesce(x.proacl, acldefault('f', x.proowner))) a
                where x.oid = f::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE') then
      raise exception 'PUBLIC executa %.', f;
    end if;
    if not exists (select 1 from pg_proc x
                    where x.oid = f::regprocedure and x.prosecdef
                      and array_to_string(x.proconfig, ',') like '%search_path=%') then
      raise exception '% sem SECURITY DEFINER ou search_path fixo.', f;
    end if;
  end loop;
end
$$;
