-- Migration: policy explicita de negacao (using false) em ia_configuracoes, ia_uso e saudacoes (DB-03).
-- Nova migration; NAO edita as anteriores (20261108130000 e 20261115120100). Idempotente.
--
-- Motivo: as 3 tabelas ja tinham RLS ligado e nenhum grant para anon/authenticated (deny-by-default), mas
-- sem policy. Isso e seguro, porem implicito: um GRANT futuro por engano abriria a tabela e o advisor do
-- Supabase aponta "RLS sem policy". Seguimos o padrao de perfil_fotos/checklists: policy PERMISSIVE
-- `<tabela>_sem_acesso_api` para anon e authenticated com using (false) / with check (false).
-- service_role segue com bypass de RLS e com os grants minimos ja existentes (nada muda para o backend).

alter table public.ia_configuracoes enable row level security;
drop policy if exists ia_configuracoes_sem_acesso_api on public.ia_configuracoes;
create policy ia_configuracoes_sem_acesso_api on public.ia_configuracoes
  for all to anon, authenticated using (false) with check (false);

alter table public.ia_uso enable row level security;
drop policy if exists ia_uso_sem_acesso_api on public.ia_uso;
create policy ia_uso_sem_acesso_api on public.ia_uso
  for all to anon, authenticated using (false) with check (false);

alter table public.saudacoes enable row level security;
drop policy if exists saudacoes_sem_acesso_api on public.saudacoes;
create policy saudacoes_sem_acesso_api on public.saudacoes
  for all to anon, authenticated using (false) with check (false);

-- Autoverificacao
do $$
declare
  t text;
begin
  foreach t in array array['ia_configuracoes', 'ia_uso', 'saudacoes'] loop
    if not (select relrowsecurity from pg_class where oid = ('public.' || t)::regclass) then
      raise exception 'RLS desligado em public.%.', t;
    end if;
    if not exists (select 1 from pg_policies
                    where schemaname = 'public' and tablename = t and policyname = t || '_sem_acesso_api'
                      and permissive = 'PERMISSIVE' and cmd = 'ALL'
                      and roles::text[] @> array['anon', 'authenticated']
                      and qual = 'false' and with_check = 'false') then
      raise exception 'Policy %_sem_acesso_api ausente ou diferente do esperado.', t;
    end if;
    if has_table_privilege('anon', 'public.' || t, 'select, insert, update, delete')
       or has_table_privilege('authenticated', 'public.' || t, 'select, insert, update, delete') then
      raise exception 'anon/authenticated nao podem ter privilegio em public.%.', t;
    end if;
  end loop;
end
$$;
