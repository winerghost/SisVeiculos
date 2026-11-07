-- Migration 9: RLS por papel (leitura) + seed do primeiro admin.
--
-- RLS: o Flask usa service_role (ignora RLS); estas politicas sao defesa em
-- profundidade caso alguem use a anon key ou um JWT de usuario direto no banco.
-- Deny-by-default: anon nao tem nada. authenticated recebe SOMENTE SELECT, filtrado
-- por papel; nenhuma politica nem grant de INSERT/UPDATE/DELETE (toda escrita passa
-- pelo backend, que valida e usa as funcoes/triggers das migrations anteriores).
-- Papel vem de perfis (nunca do JWT) via privado.papel_atual(), que devolve NULL
-- para conta inexistente ou inativa => conta inativa nao le nada.
--   perfis        : o proprio perfil; admin le todos.
--   veiculos      : qualquer papel ativo (solicitante precisa escolher o veiculo).
--   requisicoes   : solicitante so as proprias (anti-IDOR); autorizador e admin todas.
--   auditoria     : somente admin.
--   configuracoes : somente admin (o front so exibe o que a API informar).
--   convites      : sem politica e sem grant (somente backend; contem e-mail e hash).
-- ASSUNCAO a confirmar: autorizador NAO le outros perfis (LGPD: minimo necessario);
-- ele ve o nome do solicitante em requisicoes.nome_solicitante.
-- Politicas usam (select ...) para o planner avaliar a funcao uma unica vez.
--
-- Seed do primeiro admin: nunca por endpoint publico. O e-mail NAO esta neste
-- arquivo; vem do operador (variavel SEED_ADMIN_EMAIL do pipeline), por duas vias:
--   a) este arquivo le a configuracao de sessao app.seed_admin_email (se o pipeline
--      a definir, ex.: PGOPTIONS="-c app.seed_admin_email=$SEED_ADMIN_EMAIL");
--   b) chamada manual pelo pipeline com o papel postgres:
--      psql "$DATABASE_URL" -v e="$SEED_ADMIN_EMAIL" -c "select privado.semear_primeiro_admin(:'e')"
-- Sem a configuracao, nada acontece. A conta ja deve existir no Supabase Auth
-- (criada pelo painel/convite do projeto, sem perfil). A funcao e idempotente e se
-- recusa a agir se ja existir admin ativo ou se o perfil ja existir.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- Papel do usuario autenticado (SECURITY DEFINER evita recursao de RLS em perfis).
-- ---------------------------------------------------------------------------
grant usage on schema privado to authenticated;

create or replace function privado.papel_atual()
returns public.role_usuario
language sql
stable
security definer
set search_path = ''
as $$
  select p.role from public.perfis p where p.id = (select auth.uid()) and p.ativo;
$$;

revoke all on function privado.papel_atual() from public, anon;
grant execute on function privado.papel_atual() to authenticated;

-- ---------------------------------------------------------------------------
-- Grants minimos: somente SELECT para authenticated (as politicas filtram).
-- ---------------------------------------------------------------------------
grant select on table public.perfis, public.veiculos, public.requisicoes,
                      public.auditoria, public.configuracoes to authenticated;

-- ---------------------------------------------------------------------------
-- Politicas (uma por tabela/acao, para nao multiplicar politicas permissivas)
-- ---------------------------------------------------------------------------
drop policy if exists perfis_leitura on public.perfis;
create policy perfis_leitura on public.perfis
  for select to authenticated
  using (
    (select privado.papel_atual()) = 'admin'
    or (id = (select auth.uid()) and (select privado.papel_atual()) is not null)
  );

drop policy if exists veiculos_leitura on public.veiculos;
create policy veiculos_leitura on public.veiculos
  for select to authenticated
  using ((select privado.papel_atual()) is not null);

drop policy if exists requisicoes_leitura on public.requisicoes;
create policy requisicoes_leitura on public.requisicoes
  for select to authenticated
  using (
    (select privado.papel_atual()) in ('autorizador', 'admin')
    or (usuario_id = (select auth.uid()) and (select privado.papel_atual()) is not null)
  );

drop policy if exists auditoria_leitura on public.auditoria;
create policy auditoria_leitura on public.auditoria
  for select to authenticated
  using ((select privado.papel_atual()) = 'admin');

drop policy if exists configuracoes_leitura on public.configuracoes;
create policy configuracoes_leitura on public.configuracoes
  for select to authenticated
  using ((select privado.papel_atual()) = 'admin');

-- Garante RLS ligado em todas (idempotente; nunca desabilitar).
alter table public.perfis        enable row level security;
alter table public.veiculos      enable row level security;
alter table public.requisicoes   enable row level security;
alter table public.auditoria     enable row level security;
alter table public.configuracoes enable row level security;
alter table public.convites      enable row level security;

-- ---------------------------------------------------------------------------
-- Seed do primeiro admin (promove conta do Auth que ainda nao tem perfil).
-- Retorna o resultado como texto; nunca devolve nem registra o e-mail.
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
  v_email text := lower(btrim(coalesce(p_email, '')));
  v_id    uuid;
begin
  if v_email = '' then
    return 'sem_email';
  end if;

  -- Mesma chave do trigger de perfis (migration 7): sem corrida com alteracoes de papel.
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0));

  if exists (select 1 from public.perfis where role = 'admin' and ativo) then
    return 'ja_existe_admin';
  end if;

  select id into v_id from auth.users where lower(email) = v_email;
  if v_id is null then
    return 'conta_auth_inexistente';
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

-- Somente o dono (postgres, no pipeline). Nem service_role, nem a API.
revoke all on function privado.semear_primeiro_admin(text, text, public.unidade)
  from public, anon, authenticated, service_role;

-- Via (a): executa no db push se o pipeline definiu app.seed_admin_email.
do $$
declare
  v_resultado text;
begin
  v_resultado := privado.semear_primeiro_admin(nullif(current_setting('app.seed_admin_email', true), ''));
  raise notice 'seed do primeiro admin: %', v_resultado;
end
$$;
