-- Migration: public.ops_banco_lacrado(), leitura do LACRE do banco para o backend.
-- Nova migration; NAO edita nenhuma anterior. Idempotente (create or replace + revoke/grant + checagem).
-- NAO aplicar pelo servidor: supabase db push e do pipeline.
--
-- Motivo (decisao do dono do projeto, 2026-11-15): a limpeza de Storage (backend app.ops.limpar_storage)
-- precisa recusar quando o banco estiver lacrado, como ja faz o SQL de supabase/ops. O backend fala com o
-- banco pela API (service_role) e nao le comentario de banco: esta funcao devolve so um booleano.
--   * true se o comentario do banco atual CONTEM 'sisveiculos:lacrado'; false caso contrario
--     (sem comentario, comentario padrao, marcador de teste 'sisveiculos:descartavel', outro texto).
--     "Contem" (e nao "comeca com", como no SQL de ops) e mais restritivo: na duvida, recusa.
--   * Nao devolve o texto do comentario (nada alem do booleano sai do banco).
--   * SECURITY DEFINER com search_path = '' (nomes qualificados com pg_catalog). STABLE, sem efeito colateral.
--   * EXECUTE so para service_role; revogado de public, anon e authenticated (deny-by-default).
-- Nenhuma tabela nova: RLS nao muda. Fuso: nao se aplica (sem timestamp).

set local lock_timeout = '5s';

create or replace function public.ops_banco_lacrado()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    (select pg_catalog.strpos(
              coalesce(pg_catalog.shobj_description(d.oid, 'pg_database'), ''),
              'sisveiculos:lacrado') > 0
       from pg_catalog.pg_database d
      where d.datname = pg_catalog.current_database()),
    false);
$$;

comment on function public.ops_banco_lacrado() is
  'true se o comentario do banco atual contem sisveiculos:lacrado (limpeza pre-lancamento proibida). So service_role.';

revoke all on function public.ops_banco_lacrado() from public, anon, authenticated;
grant execute on function public.ops_banco_lacrado() to service_role;

-- Checagem: privilegios e configuracao como o esperado (falha => a migration inteira e desfeita).
do $$
declare
  v_fn constant regprocedure := 'public.ops_banco_lacrado()'::regprocedure;
begin
  if has_function_privilege('anon', v_fn, 'execute')
     or has_function_privilege('authenticated', v_fn, 'execute') then
    raise exception 'ops_banco_lacrado nao pode ser executavel por anon/authenticated.';
  end if;
  if not has_function_privilege('service_role', v_fn, 'execute') then
    raise exception 'ops_banco_lacrado precisa ser executavel por service_role.';
  end if;
  if not exists (select 1 from pg_proc p
                  where p.oid = v_fn and p.prosecdef
                    and p.proconfig @> array['search_path=""']) then
    raise exception 'ops_banco_lacrado deve ser SECURITY DEFINER com search_path vazio.';
  end if;
end
$$;
