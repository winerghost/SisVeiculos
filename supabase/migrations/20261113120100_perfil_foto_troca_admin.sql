-- Migration: FOTO DE PERFIL, troca pelo dono OU por admin ativo (RPC com ator) e auditoria com o
-- ator correto. Nova migration; NAO edita as 122410/122420/122450 (ja aplicadas). Idempotente.
--
-- Motivo: hoje o admin so REMOVE a foto de outro usuario (122450). Passa a poder TROCAR.
--
-- 1. MODELO DO ATOR: coluna `criada_por` (uuid, FK perfis), espelho de `removida_por` (122450), e
--    NAO variavel de sessao (a auditoria e um constraint trigger DEFERRED que roda no COMMIT; ver
--    o cabecalho da 122450). A coluna viaja na propria linha ate o gatilho: ator por EVENTO.
--      * NULL = foto enviada pelo proprio dono (comportamento atual preservado);
--      * preenchida = admin que trocou a foto do usuario.
--    Sem CHECK (violacao de CHECK exporia "Failing row" no DETAIL): a validacao fica no gatilho
--    BEFORE INSERT, com mensagem fixa. O gatilho de imutabilidade (perfil_fotos_so_remocao) NAO
--    muda; `service_role` continua SEM INSERT/UPDATE na tabela: so as RPCs escrevem.
--    A FK criada_por -> perfis(id) ON DELETE RESTRICT so toma FOR KEY SHARE na linha do admin (nao
--    conflita com UPDATE de role/ativo). Sem indice (coluna quase nunca consultada).
--
-- 2. perfil_fotos_validar (BEFORE INSERT) recriada com TODAS as validacoes, mensagens e codigos da
--    versao da 122450, mais: criada_por = usuario_id e normalizado para NULL (dono); criada_por
--    nao nulo precisa ser um ADMIN ATIVO, senao AG022 (mensagem fixa, sem valores). Isso barra
--    INSERT direto com criada_por forjado (id de nao-admin, de admin inativo ou inexistente).
--
-- 3. AUDITORIA: perfil_fotos_auditar (SECURITY DEFINER, search_path vazio) grava
--      INSERT (foto_perfil_alterada): ator_id = coalesce(criada_por, usuario_id);
--      UPDATE (foto_perfil_removida): ator_id = coalesce(removida_por, usuario_id)  (como a 122450);
--    e `detalhes` = {mime, tamanho_bytes, por_admin} (por_admin boolean = ator <> dono) em ambas.
--    Linhas antigas de 'foto_perfil_alterada' nao tem `por_admin`: ausente = false. Sem caminho,
--    URL, nome nem outro dado pessoal. ck_auditoria_acao/ck_auditoria_tabela NAO mudam.
--
-- 4. NOVA RPC trocar_foto_perfil_por(p_usuario, p_ator, p_foto, p_mime, p_tamanho) -> caminho da
--    foto ANTERIOR (ou NULL). SECURITY DEFINER, search_path vazio, EXECUTE so para service_role.
--      * p_ator = p_usuario: identica a trocar_foto_perfil (ator = dono, criada_por e
--        removida_por ficam NULL);
--      * p_ator <> p_usuario: exige ADMIN ATIVO (AG022, mensagem fixa); a foto nova grava
--        criada_por = p_ator e a anterior e marcada com removida_por = p_ator (a auditoria mostra
--        o admin como autor das duas linhas, por_admin = true);
--      * alvo inexistente ou INATIVO => AG017; argumento nulo => 23502.
--    O p_ator vem do JWT validado no backend (nunca do cliente); a checagem de admin ativo e uma
--    segunda barreira. Validacoes de conteudo (mime/tamanho) ficam no gatilho BEFORE INSERT.
--    LOCKS (contrato da migration 15 preservado): mesmo advisory por usuario das demais RPCs de
--    foto, pg_advisory_xact_lock(hashtextextended('sisveiculos.avatar:' || p_usuario, 0)); NAO toma
--    `sisveiculos.perfis_admin` e NAO escreve em `perfis`. O papel do ator e lido SEM lock (limite
--    aceito B4); com p_ator <> p_usuario a checagem de admin ativo roda antes E depois do advisory
--    (snapshot novo apos a espera, READ COMMITTED), como a remover_foto_perfil_por. A janela
--    residual (admin rebaixado entre a 2a checagem e o commit) afeta uma unica troca, auditada com
--    o ator; tomar perfis_admin criaria o ciclo que o contrato da 15 proibe. lock_timeout de 5 s
--    (migration 16) vale: 55P03 => retry limitado no backend.
--    O backend apaga o objeto anterior do Storage DEPOIS do commit, com o caminho devolvido.
--    A RPC trocar_foto_perfil(...) NAO muda.
--
-- 5. ERRO: AG022 passa a cobrir tambem a TROCA nao autorizada (ator diferente do dono e que nao
--    e admin ativo). Nenhum codigo novo.
--
-- Locks desta migration: ADD COLUMN com REFERENCES toma SHARE ROW EXCLUSIVE em `perfis` e
-- ACCESS EXCLUSIVE breve em perfil_fotos (coluna nullable, sem rewrite); lock_timeout de 5 s abaixo
-- faz falhar em vez de enfileirar escritas. CREATE OR REPLACE FUNCTION nao bloqueia leituras.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. Coluna criada_por
-- ---------------------------------------------------------------------------
set local lock_timeout = '5s';

alter table public.perfil_fotos
  add column if not exists criada_por uuid references public.perfis (id) on delete restrict;

comment on column public.perfil_fotos.criada_por is
  'Admin que trocou a foto de outro usuario (gravado por trocar_foto_perfil_por). NULL = enviada pelo proprio dono. Validada no gatilho: so admin ativo.';

-- ---------------------------------------------------------------------------
-- 2. perfil_fotos_validar: a da 122450 + validacao/normalizacao de criada_por
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
  new.criado_em    := clock_timestamp();  -- carimbo do servidor
  new.removida_em  := null;               -- nasce ativa
  new.removida_por := null;               -- e sem remocao

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

  -- Autor da troca: o dono vira NULL; qualquer outro precisa ser admin ativo (sem lock em perfis).
  if new.criada_por is not distinct from new.usuario_id then
    new.criada_por := null;
  end if;
  if new.criada_por is not null
     and not exists (select 1 from public.perfis
                      where id = new.criada_por and role = 'admin' and ativo) then
    raise exception 'Troca da foto de perfil não autorizada.'
      using errcode = 'AG022', schema = 'public', table = 'perfil_fotos', column = 'criada_por';
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

revoke all on function public.perfil_fotos_validar() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 3. perfil_fotos_auditar: ator por evento (criada_por / removida_por) e por_admin
-- ---------------------------------------------------------------------------
create or replace function public.perfil_fotos_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator    uuid := case tg_op
                      when 'INSERT' then coalesce(new.criada_por, new.usuario_id)
                      else coalesce(new.removida_por, new.usuario_id)
                    end;
  v_detalhe jsonb;
begin
  v_detalhe := jsonb_build_object('mime', new.mime, 'tamanho_bytes', new.tamanho_bytes,
                                  'por_admin', v_ator <> new.usuario_id);
  -- Sem caminho, URL ou nome de arquivo. registro_id = id da foto (o dono: join por esse id).
  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
  values ('perfil_fotos', new.id,
          case tg_op when 'INSERT' then 'foto_perfil_alterada' else 'foto_perfil_removida' end,
          v_ator, null, null, v_detalhe);
  return null;
end;
$$;

revoke all on function public.perfil_fotos_auditar() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 4. RPC trocar_foto_perfil_por
-- ---------------------------------------------------------------------------
create or replace function public.trocar_foto_perfil_por(
  p_usuario uuid,
  p_ator    uuid,
  p_foto    uuid,
  p_mime    text,
  p_tamanho integer
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ativo    boolean;
  v_anterior text;
  v_admin    uuid;
begin
  if p_usuario is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: usuario_id.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'usuario_id';
  end if;
  if p_ator is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: criada_por.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'criada_por';
  end if;
  if p_foto is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: id.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'id';
  end if;
  if p_mime is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: mime.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'mime';
  end if;
  if p_tamanho is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: tamanho_bytes.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'tamanho_bytes';
  end if;

  -- Autorizacao (sem lock em perfis; limite B4): o dono, ou admin ativo.
  if p_ator <> p_usuario
     and not exists (select 1 from public.perfis
                      where id = p_ator and role = 'admin' and ativo) then
    raise exception 'Troca da foto de perfil não autorizada.' using errcode = 'AG022';
  end if;

  select ativo into v_ativo from public.perfis where id = p_usuario;  -- sem lock (contrato 15)
  if not found or not v_ativo then
    raise exception 'Usuário inexistente ou inativo.' using errcode = 'AG017';
  end if;

  -- Serializa trocas do MESMO usuario. Nunca tomar sisveiculos.perfis_admin nesta transacao.
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.avatar:' || p_usuario::text, 0));

  -- Repete a checagem de admin ativo apos a espera pelo lock (snapshot novo).
  if p_ator <> p_usuario
     and not exists (select 1 from public.perfis
                      where id = p_ator and role = 'admin' and ativo) then
    raise exception 'Troca da foto de perfil não autorizada.' using errcode = 'AG022';
  end if;

  v_admin := case when p_ator <> p_usuario then p_ator else null end;

  update public.perfil_fotos
     set removida_em  = clock_timestamp(),
         removida_por = v_admin
   where usuario_id = p_usuario and removida_em is null
  returning caminho into v_anterior;

  -- Validacao de conteudo e carimbo no gatilho BEFORE INSERT (mensagens fixas).
  insert into public.perfil_fotos (id, usuario_id, mime, tamanho_bytes, criada_por)
  values (p_foto, p_usuario, p_mime, p_tamanho, v_admin);

  return v_anterior;
end;
$$;

revoke all on function public.trocar_foto_perfil_por(uuid, uuid, uuid, text, integer)
  from public, anon, authenticated;
grant execute on function public.trocar_foto_perfil_por(uuid, uuid, uuid, text, integer)
  to service_role;

-- ---------------------------------------------------------------------------
-- 5. Trava: a migration falha se sobrar acesso indevido
-- ---------------------------------------------------------------------------
do $$
declare
  f text := 'public.trocar_foto_perfil_por(uuid, uuid, uuid, text, integer)';
  r text;
begin
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

  foreach f in array array['public.perfil_fotos_validar()', 'public.perfil_fotos_auditar()'] loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if has_function_privilege(r, f::regprocedure, 'execute') then
        raise exception '% ainda executa %.', r, f;
      end if;
    end loop;
  end loop;

  foreach r in array array['insert', 'update', 'delete', 'truncate'] loop
    if has_table_privilege('service_role', 'public.perfil_fotos', r) then
      raise exception 'service_role tem % em public.perfil_fotos.', r;
    end if;
  end loop;
end
$$;
