-- Migration: desativar o perfil apaga a marca de logout das inscricoes push (revisao de seguranca B2, 2026-11-17).
-- Nova migration; NAO edita as anteriores. Idempotente. NAO aplicar pelo servidor: supabase db push e do pipeline.
-- Depende de 20261206120000_push_reativar_apos_logout.sql (coluna revogada_por_logout).
--
-- Problema: usuario sai (inscricao revogada com a marca de logout), e desativado e um admin o reativa em ate
-- 30 dias; no login seguinte reativar_inscricao_push religava o aparelho sem novo opt-in.
-- Desenho: quando perfis.ativo passa de true para false, as inscricoes revogadas por logout desse usuario perdem
-- a marca. Depois de uma desativacao, voltar a receber avisos exige o botao (opt-in explicito).
-- Inscricoes ativas nao mudam aqui (o envio ja filtra perfil ativo); desligar nao apaga dados.
--
-- 1. Funcao de gatilho public.perfis_limpa_marca_push_logout(), SECURITY DEFINER (push_inscricoes nao
--    tem grant para ninguem), search_path vazio, sem EXECUTE para public/anon/authenticated/service_role.
-- 2. Gatilho trg_perfis_limpa_marca_push_logout: AFTER UPDATE OF ativo, so na transicao ativo -> inativo, sempre ativo.
-- 3. Autoverificacao e reload do PostgREST.

set local lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 1. Funcao do gatilho
-- ---------------------------------------------------------------------------
create or replace function public.perfis_limpa_marca_push_logout()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if old.ativo and not new.ativo then
    update public.push_inscricoes i
       set revogada_por_logout = false
     where i.usuario_id = new.id
       and i.revogada_por_logout;
  end if;
  return null;
end;
$$;

revoke all on function public.perfis_limpa_marca_push_logout() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 2. Gatilho
-- ---------------------------------------------------------------------------
drop trigger if exists trg_perfis_limpa_marca_push_logout on public.perfis;
create trigger trg_perfis_limpa_marca_push_logout
  after update of ativo on public.perfis
  for each row
  when (old.ativo and not new.ativo)
  execute function public.perfis_limpa_marca_push_logout();
alter table public.perfis enable always trigger trg_perfis_limpa_marca_push_logout;

-- ---------------------------------------------------------------------------
-- 3. Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v_papel text;
begin
  if not exists (select 1 from pg_trigger where tgrelid = 'public.perfis'::regclass
                  and tgname = 'trg_perfis_limpa_marca_push_logout' and tgenabled = 'A' and not tgisinternal) then
    raise exception 'trg_perfis_limpa_marca_push_logout ausente ou nao sempre ativo.';
  end if;
  if not exists (select 1 from pg_proc p
                  where p.oid = 'public.perfis_limpa_marca_push_logout()'::regprocedure and p.prosecdef
                    and p.proconfig is not null
                    and exists (select 1 from unnest(p.proconfig) c
                                 where replace(c, ' ', '') in ('search_path=', 'search_path=""'))) then
    raise exception 'perfis_limpa_marca_push_logout deve ser SECURITY DEFINER com search_path vazio.';
  end if;
  foreach v_papel in array array['anon', 'authenticated', 'service_role'] loop
    if has_function_privilege(v_papel, 'public.perfis_limpa_marca_push_logout()'::regprocedure, 'execute') then
      raise exception 'perfis_limpa_marca_push_logout executavel por %.', v_papel;
    end if;
  end loop;
end
$$;

notify pgrst, 'reload schema';
