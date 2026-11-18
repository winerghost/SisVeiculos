-- Migration: Web Push sobrevive ao logout/login do MESMO usuario (alternativa B, decisao do dono 2026-11-17).
-- Nova migration; NAO edita as anteriores. Idempotente. NAO aplicar pelo servidor: supabase db push e do pipeline.
--
-- Problema: o logout revogava a inscricao no servidor e cancelava a inscricao local; no login seguinte o usuario
-- precisava clicar de novo em "Ativar avisos neste aparelho".
-- Desenho (revisao LGPD): o logout passa a revogar com a marca `revogada_por_logout`; o front so mantem a inscricao
-- local quando o servidor confirmou. No login, `reativar_inscricao_push` religa SOMENTE se:
--   * o endpoint pertence ao proprio usuario (o do JWT); endpoint alheio ou inexistente => false, sem revelar dono;
--   * a revogacao foi por logout (revogacao manual, 404/410 do servico push e 5 falhas nunca religam);
--   * a revogacao tem no maximo 30 dias (mesmo prazo do expurgo);
--   * o perfil esta ativo e ha vaga no limite de 10 (senao false, sem erro; o botao continua o caminho).
-- Passar o aparelho a OUTRO usuario continua exigindo o opt-in explicito (registrar_inscricao_push pelo botao).
--
-- 1. push_inscricoes.revogada_por_logout boolean not null default false + CHECK (so em linha revogada).
-- 2. Gatilho trg_push_inscricoes_logout (BEFORE INSERT/UPDATE, sempre ativo): linha ativa sempre fica com a marca
--    false (qualquer reativacao/reatribuicao a limpa) e a marca so pode nascer na transicao ativa -> revogada.
--    SECURITY INVOKER (so altera NEW). Nao substitui push_inscricoes_proteger (identidade, AG043/AG044 e DELETE seguem la).
-- 3. revogar_inscricao_push_logout(uuid, text): como revogar_inscricao_push (so a do proprio usuario,
--    idempotente), marcando a revogacao como logout. Retorno {"revogadas": int}; o backend devolve ao aparelho
--    so `revogada` (bool) e, se false, o aparelho cancela a inscricao local.
-- 4. reativar_inscricao_push(uuid, text): retorno {"reativada": bool}. Serializada pelo mesmo advisory lock por
--    endpoint de registrar_inscricao_push.
-- 5. Autoverificacao (coluna, CHECK, definer/search_path, grants, gatilho sempre ativo) e reload do PostgREST.
--
-- Mantidos: RLS sem policy e sem grant na tabela, RPCs SECURITY DEFINER com search_path vazio, EXECUTE so
-- service_role. Endpoint e chaves nunca saem destas RPCs. Fuso: timestamptz (UTC; exibicao America/Sao_Paulo).
-- Sem codigos de erro novos (AG042 para parametros invalidos).

set local lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 1. Coluna e CHECK
-- ---------------------------------------------------------------------------
alter table public.push_inscricoes
  add column if not exists revogada_por_logout boolean not null default false;

comment on column public.push_inscricoes.revogada_por_logout is
  'true = revogada pelo logout do proprio dono; so assim reativar_inscricao_push religa (mesmo usuario, ate 30 dias). Sempre false em linha ativa.';

alter table public.push_inscricoes
  drop constraint if exists ck_push_inscricoes_logout;
alter table public.push_inscricoes
  add constraint ck_push_inscricoes_logout
  check (not revogada_por_logout or revogada_em is not null);

-- ---------------------------------------------------------------------------
-- 2. Gatilho da marca de logout
-- ---------------------------------------------------------------------------
-- SECURITY INVOKER (privilegio minimo): so mexe em NEW, nao le nem grava outra tabela.
create or replace function public.push_inscricoes_marca_logout()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if new.revogada_em is null then
    -- Ativa (insercao, reativacao ou reatribuicao): nunca carrega marca de logout.
    new.revogada_por_logout := false;
  elsif tg_op = 'INSERT' then
    new.revogada_por_logout := false;
  elsif new.revogada_por_logout and not old.revogada_por_logout and old.revogada_em is not null then
    -- A marca so nasce na transicao ativa -> revogada (nunca em linha ja revogada por outro motivo).
    new.revogada_por_logout := false;
  end if;
  return new;
end;
$$;

revoke all on function public.push_inscricoes_marca_logout() from public, anon, authenticated, service_role;

drop trigger if exists trg_push_inscricoes_logout on public.push_inscricoes;
create trigger trg_push_inscricoes_logout
  before insert or update on public.push_inscricoes
  for each row execute function public.push_inscricoes_marca_logout();
alter table public.push_inscricoes enable always trigger trg_push_inscricoes_logout;

-- ---------------------------------------------------------------------------
-- 3. revogar_inscricao_push_logout
-- ---------------------------------------------------------------------------
create or replace function public.revogar_inscricao_push_logout(p_usuario uuid, p_endpoint text)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_n integer;
begin
  if p_usuario is null or p_endpoint is null or length(p_endpoint) > 2048 then
    raise exception 'Dados da inscrição de push inválidos.' using errcode = 'AG042';
  end if;
  update public.push_inscricoes i
     set revogada_em = clock_timestamp(), revogada_por_logout = true
   where i.usuario_id = p_usuario
     and i.endpoint_hash = encode(sha256(convert_to(p_endpoint, 'UTF8')), 'hex')
     and i.revogada_em is null;
  get diagnostics v_n = row_count;
  return jsonb_build_object('revogadas', v_n);
end;
$$;

-- ---------------------------------------------------------------------------
-- 4. reativar_inscricao_push
-- ---------------------------------------------------------------------------
create or replace function public.reativar_inscricao_push(p_usuario uuid, p_endpoint text)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_hash   text;
  v_linha  public.push_inscricoes%rowtype;
  v_ativo  boolean;
  v_n      integer;
begin
  if p_usuario is null or p_endpoint is null or length(p_endpoint) > 2048 then
    raise exception 'Dados da inscrição de push inválidos.' using errcode = 'AG042';
  end if;

  v_hash := encode(sha256(convert_to(p_endpoint, 'UTF8')), 'hex');
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.push_endpoint:' || v_hash, 0));

  select * into v_linha from public.push_inscricoes i where i.endpoint_hash = v_hash for update;
  -- Inexistente ou de outro usuario: mesma resposta (nao revela o dono).
  if not found or v_linha.usuario_id <> p_usuario then
    return jsonb_build_object('reativada', false);
  end if;

  select p.ativo into v_ativo from public.perfis p where p.id = p_usuario;
  if not coalesce(v_ativo, false) then
    return jsonb_build_object('reativada', false);
  end if;

  -- Ja ativa do proprio usuario: idempotente.
  if v_linha.revogada_em is null then
    return jsonb_build_object('reativada', true);
  end if;

  if not v_linha.revogada_por_logout or v_linha.revogada_em < now() - interval '30 days' then
    return jsonb_build_object('reativada', false);
  end if;

  select count(*) into v_n from public.push_inscricoes x
   where x.usuario_id = p_usuario and x.revogada_em is null;
  if v_n >= 10 then
    return jsonb_build_object('reativada', false);
  end if;

  begin
    update public.push_inscricoes i
       set revogada_em = null, falhas = 0
     where i.id = v_linha.id;
  exception
    -- Corrida com outra inscricao do mesmo usuario (limite) ou perfil desativado no meio: so nao religa.
    when sqlstate 'AG043' or sqlstate 'AG044' then
      return jsonb_build_object('reativada', false);
  end;
  return jsonb_build_object('reativada', true);
end;
$$;

revoke all on function public.revogar_inscricao_push_logout(uuid, text) from public, anon, authenticated;
revoke all on function public.reativar_inscricao_push(uuid, text) from public, anon, authenticated;
grant execute on function public.revogar_inscricao_push_logout(uuid, text) to service_role;
grant execute on function public.reativar_inscricao_push(uuid, text) to service_role;

-- ---------------------------------------------------------------------------
-- 5. Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v_rpc text;
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'push_inscricoes'
                    and column_name = 'revogada_por_logout' and is_nullable = 'NO') then
    raise exception 'push_inscricoes.revogada_por_logout ausente ou anulavel.';
  end if;
  if not exists (select 1 from pg_constraint where conname = 'ck_push_inscricoes_logout'
                  and conrelid = 'public.push_inscricoes'::regclass) then
    raise exception 'ck_push_inscricoes_logout ausente.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.push_inscricoes'::regclass
                  and tgname = 'trg_push_inscricoes_logout' and tgenabled = 'A' and not tgisinternal) then
    raise exception 'trg_push_inscricoes_logout ausente ou nao sempre ativo.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.push_inscricoes'::regclass
                  and tgname = 'trg_push_inscricoes_proteger' and tgenabled = 'A' and not tgisinternal) then
    raise exception 'trg_push_inscricoes_proteger ausente ou nao sempre ativo.';
  end if;
  if not exists (select 1 from pg_proc p
                  where p.oid = 'public.push_inscricoes_marca_logout()'::regprocedure and not p.prosecdef
                    and p.proconfig is not null
                    and exists (select 1 from unnest(p.proconfig) c
                                 where replace(c, ' ', '') in ('search_path=', 'search_path=""'))) then
    raise exception 'push_inscricoes_marca_logout deve ser SECURITY INVOKER com search_path vazio.';
  end if;
  if has_function_privilege('anon', 'public.push_inscricoes_marca_logout()'::regprocedure, 'execute')
     or has_function_privilege('authenticated', 'public.push_inscricoes_marca_logout()'::regprocedure, 'execute') then
    raise exception 'push_inscricoes_marca_logout executavel por anon/authenticated.';
  end if;
  foreach v_rpc in array array['public.revogar_inscricao_push_logout(uuid, text)',
                               'public.reativar_inscricao_push(uuid, text)'] loop
    if not exists (select 1 from pg_proc p where p.oid = v_rpc::regprocedure and p.prosecdef
                    and p.proconfig is not null
                    and exists (select 1 from unnest(p.proconfig) c
                                 where replace(c, ' ', '') in ('search_path=', 'search_path=""'))) then
      raise exception '% sem SECURITY DEFINER ou search_path vazio.', v_rpc;
    end if;
    if has_function_privilege('anon', v_rpc::regprocedure, 'execute')
       or has_function_privilege('authenticated', v_rpc::regprocedure, 'execute') then
      raise exception '% executavel por anon/authenticated.', v_rpc;
    end if;
  end loop;
  if not has_function_privilege('service_role', 'public.revogar_inscricao_push_logout(uuid, text)'::regprocedure, 'execute')
     or not has_function_privilege('service_role', 'public.reativar_inscricao_push(uuid, text)'::regprocedure, 'execute') then
    raise exception 'service_role sem EXECUTE nas RPCs novas de push.';
  end if;
  if has_function_privilege('service_role', 'public.push_inscricoes_marca_logout()'::regprocedure, 'execute') then
    raise exception 'push_inscricoes_marca_logout executavel por service_role.';
  end if;
end
$$;

notify pgrst, 'reload schema';
