-- Migration 20261107122430: historico de ACESSOS (logins bem-sucedidos) com minimizacao de dados (LGPD).
-- Nova migration; nao edita as anteriores. Idempotente. NAO usa nem altera `auditoria`
-- (seus CHECKs prometem "sem dados pessoais") e NAO toca em `perfis` (nem toma o advisory
-- 'sisveiculos.perfis_admin').
--
-- Finalidade: o admin consulta quem entrou e quando (seguranca/transparencia). Base legal a
-- confirmar com o encarregado (ver docs/acessos-login.md).
--
-- Decisoes (aprovadas):
--  * Somente logins BEM-SUCEDIDOS, gravados pelo BACKEND depois de validar o JWT. Falhas de login
--    e renovacao de token NAO entram aqui (ficam nos logs do Auth do Supabase).
--  * Minimizacao no proprio banco, nao so no backend:
--     - IP so MASCARADO, coluna cidr: IPv4 ate /24 e IPv6 ate /48 (CHECK por masklen). Um IP
--       completo (ex.: 203.0.113.5 = /32) e RECUSADO, tanto pela RPC quanto pelo CHECK.
--     - user-agent reduzido a `ua_familia` no formato "Familia/Sistema" (so letras, espacos, '.',
--       '+', '-', sem digitos: nao cabe versao nem texto bruto), max. 49 caracteres.
--     - session_id (claim do JWT) guardado so para idempotencia (UNIQUE); NUNCA devolvido por
--       completo: a consulta devolve apenas `sessao_ref` (8 primeiros hex do sha256).
--  * Retencao 12 meses com expurgo (purgar_acessos_login; piso de 6 meses). O usuario comum NAO
--    consulta; a consulta do admin e auditada PELO BACKEND (registrar_auditoria, acao
--    'acao_administrativa', ja existente).
--  * Imutavel: UPDATE e TRUNCATE sempre bloqueados; DELETE so dentro do expurgo (abaixo).
--
-- Imutabilidade e expurgo (mecanismo):
--  * Gatilho BEFORE UPDATE/DELETE (linha) + BEFORE TRUNCATE (comando), ENABLE ALWAYS, errcode
--    AG003. DELETE e permitido SOMENTE se (a) a variavel de sessao LOCAL
--    sisveiculos.expurgo_acessos = '1' E (b) a linha tem mais de 6 meses (piso fixo no gatilho:
--    mesmo com a variavel forjada nao se apaga historico recente).
--  * A variavel e ligada via set_config(..., true) SO dentro de purgar_acessos_login e
--    desligada ao fim; ao ser LOCAL, some no COMMIT/ROLLBACK.
--  * Por que um papel sem EXECUTE nao forja: para o DELETE chegar ao gatilho o papel precisa do
--    privilegio DELETE na tabela, e ninguem da API (anon, authenticated, service_role) o tem
--    (a trava no fim desta migration falha se tiverem). Setar a variavel sem poder deletar nao
--    adianta, e a unica rota de DELETE da API e a funcao, que fixa o piso e valida p_meses.
--    Limite herdado (igual as migrations 12 a 14): SQL livre como dono/superusuario (postgres)
--    consegue forjar a variavel, mas esse papel tambem poderia desabilitar o gatilho; vale o
--    piso de 6 meses do gatilho.
--  * service_role: SOMENTE SELECT na tabela; escreve/expurga so pelas RPCs.
--
-- RPCs (SECURITY DEFINER, search_path vazio, EXECUTE so service_role):
--  1. registrar_acesso_login(...)  -> boolean (true = inseriu; false = a sessao ja estava
--     registrada). Idempotente (ON CONFLICT (session_id) DO NOTHING).
--  2. listar_acessos_login(...)    -> jsonb {total, limite, offset, itens[]}; limite maximo 100.
--  3. ultimo_acesso_por_usuario(uuid[]) -> (usuario_id, ultimo_acesso_em); max. 100 ids.
--  4. purgar_acessos_login(p_meses default 12) -> integer (linhas removidas); min. 6 meses.
--     Tambem executavel pelo `postgres` (dono) para o pg_cron.
--
-- Usuario inexistente/inativo na gravacao: ERRO AG019 (e nao ignorar em silencio). Motivo: um
-- login bem-sucedido so ocorre para conta ativa; chegar aqui com outra coisa e bug do backend
-- ou corrida com desativacao, e o erro torna isso visivel sem custo. O backend trata o registro
-- como best-effort: captura o erro, nao derruba o login e nao loga o conteudo (so o codigo).
--
-- Erros: AG003 (imutavel; ja existe), AG019 (usuario inexistente ou inativo), AG020 (dado de
-- acesso invalido: campo obrigatorio ausente, metodo/aal fora da lista, IP nao mascarado ou
-- invalido, ua_familia fora do formato, ocorrido_em fora da janela, p_meses < 6, lote > 100).
-- Todas as mensagens sao FIXAS: nao repetem IP, user-agent nem qualquer valor recebido
-- (a RPC valida ANTES do INSERT para o Postgres nunca montar `DETAIL: Failing row contains`).
-- AG021 em diante, livres. AG015-AG018 estao reservados por outras migrations.
--
-- Lock/concorrencia: nao ha FOR SHARE/FOR UPDATE em perfis, veiculos ou configuracoes. A FK
-- para perfis toma apenas FOR KEY SHARE (nao conflita com UPDATE de colunas nao-chave; perfis
-- nunca e apagado). Leituras de perfis sao SELECT simples. Nao toma 'sisveiculos.perfis_admin'.
-- Duas chamadas simultaneas com a mesma sessao: a segunda espera a primeira confirmar e nao
-- insere (ON CONFLICT DO NOTHING), sem erro.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. Tabela
-- ---------------------------------------------------------------------------
create table if not exists public.acessos_login (
  id             uuid primary key default gen_random_uuid(),
  usuario_id     uuid not null references public.perfis (id) on delete restrict,
  session_id     uuid not null,
  ocorrido_em    timestamptz not null,
  metodo         text,
  aal            text,
  ip_mascarado   cidr,
  ua_familia     text,
  registrado_em  timestamptz not null default now(),

  constraint uq_acessos_login_session unique (session_id),
  constraint ck_acessos_login_metodo check (metodo in ('password', 'totp', 'outro')),
  constraint ck_acessos_login_aal check (aal in ('aal1', 'aal2')),
  -- IP so mascarado: prefixo de no maximo /24 (IPv4) ou /48 (IPv6). Host completo e recusado.
  constraint ck_acessos_login_ip_mascarado
    check (ip_mascarado is null
           or masklen(ip_mascarado) <= case family(ip_mascarado) when 4 then 24 else 48 end),
  -- "Familia/Sistema", sem digitos (sem versao), max. 49 caracteres: nunca o texto bruto.
  constraint ck_acessos_login_ua_familia
    check (ua_familia is null
           or (char_length(ua_familia) <= 49
               and ua_familia ~ '^[A-Za-z][A-Za-z .+-]{0,23}/[A-Za-z][A-Za-z .+-]{0,23}$'))
);

comment on table public.acessos_login is
  'Logins bem-sucedidos (LGPD: IP mascarado, UA reduzido, sessao so por UNIQUE). Imutavel; DELETE so no expurgo (>12 meses). Escrita/leitura so via RPCs.';
comment on column public.acessos_login.ip_mascarado is
  'cidr com prefixo maximo /24 (v4) ou /48 (v6). IP completo e recusado.';
comment on column public.acessos_login.ua_familia is
  'Familia/Sistema sem versao (ex.: Chrome/Windows). Nunca o user-agent bruto.';
comment on column public.acessos_login.session_id is
  'Claim session_id do JWT; so para idempotencia. Nunca devolvido por completo (sessao_ref).';

create index if not exists ix_acessos_login_usuario_ocorrido
  on public.acessos_login (usuario_id, ocorrido_em desc);
create index if not exists ix_acessos_login_ocorrido
  on public.acessos_login (ocorrido_em desc);

-- ---------------------------------------------------------------------------
-- 2. Imutabilidade (DELETE so no expurgo)
-- ---------------------------------------------------------------------------
create or replace function public.acessos_login_proteger()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'DELETE'
     and coalesce(current_setting('sisveiculos.expurgo_acessos', true), '') = '1'
     and old.ocorrido_em < now() - interval '6 months' then
    return old;
  end if;
  raise exception 'Operação % proibida em acessos_login: o histórico só é removido pelo expurgo.', tg_op
    using errcode = 'AG003';
end;
$$;

revoke all on function public.acessos_login_proteger() from public, anon, authenticated, service_role;

drop trigger if exists trg_acessos_login_imutavel on public.acessos_login;
create trigger trg_acessos_login_imutavel
  before update or delete on public.acessos_login
  for each row execute function public.acessos_login_proteger();
alter table public.acessos_login enable always trigger trg_acessos_login_imutavel;

drop trigger if exists trg_acessos_login_sem_truncate on public.acessos_login;
create trigger trg_acessos_login_sem_truncate
  before truncate on public.acessos_login
  for each statement execute function public.bloquear_alteracao();
alter table public.acessos_login enable always trigger trg_acessos_login_sem_truncate;

-- ---------------------------------------------------------------------------
-- 3. RLS deny-by-default, negacao explicita e grants minimos
-- ---------------------------------------------------------------------------
alter table public.acessos_login enable row level security;

drop policy if exists acessos_login_sem_acesso_api on public.acessos_login;
create policy acessos_login_sem_acesso_api on public.acessos_login
  for all to anon, authenticated using (false) with check (false);

revoke all on table public.acessos_login from public, anon, authenticated, service_role;
grant select on table public.acessos_login to service_role;

-- ---------------------------------------------------------------------------
-- 4. RPC: registrar (idempotente)
-- ---------------------------------------------------------------------------
create or replace function public.registrar_acesso_login(
  p_usuario      uuid,
  p_session      uuid,
  p_ocorrido_em  timestamptz,
  p_metodo       text,
  p_aal          text,
  p_ip           text,
  p_ua           text
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ip    cidr;
  v_ativo boolean;
  v_n     integer;
begin
  -- Validacao ANTES do INSERT, com mensagens fixas (sem valores recebidos, sem DETAIL).
  if p_usuario is null or p_session is null or p_ocorrido_em is null then
    raise exception 'Dados obrigatórios do acesso ausentes.' using errcode = 'AG020';
  end if;
  if p_metodo is not null and p_metodo not in ('password', 'totp', 'outro') then
    raise exception 'Método de acesso inválido.' using errcode = 'AG020';
  end if;
  if p_aal is not null and p_aal not in ('aal1', 'aal2') then
    raise exception 'Nível de autenticação inválido.' using errcode = 'AG020';
  end if;
  if p_ua is not null
     and (char_length(p_ua) > 49
          or p_ua !~ '^[A-Za-z][A-Za-z .+-]{0,23}/[A-Za-z][A-Za-z .+-]{0,23}$') then
    raise exception 'Família do navegador fora do formato permitido.' using errcode = 'AG020';
  end if;
  if p_ip is not null then
    if char_length(p_ip) > 43 then
      raise exception 'IP deve ser enviado mascarado.' using errcode = 'AG020';
    end if;
    begin
      v_ip := p_ip::cidr;
    exception when invalid_text_representation then
      -- Captura aqui para a mensagem padrao do Postgres (que repetiria o IP) nao ir ao log.
      raise exception 'IP deve ser enviado mascarado.' using errcode = 'AG020';
    end;
    if masklen(v_ip) > (select max_prefixo from (values (4, 24), (6, 48)) as m (familia, max_prefixo)
                         where m.familia = family(v_ip)) then
      raise exception 'IP deve ser enviado mascarado.' using errcode = 'AG020';
    end if;
  end if;

  select ativo into v_ativo from public.perfis where id = p_usuario;
  if not found or not v_ativo then
    raise exception 'Usuário inexistente ou inativo.' using errcode = 'AG019';
  end if;

  -- Sessao ja registrada: idempotente, antes da janela (reenvio tardio nao falha).
  if exists (select 1 from public.acessos_login where session_id = p_session) then
    return false;
  end if;

  if p_ocorrido_em > now() + interval '5 minutes' or p_ocorrido_em < now() - interval '7 days' then
    raise exception 'Momento do acesso fora da janela permitida.' using errcode = 'AG020';
  end if;

  insert into public.acessos_login (usuario_id, session_id, ocorrido_em, metodo, aal, ip_mascarado, ua_familia)
  values (p_usuario, p_session, p_ocorrido_em, p_metodo, p_aal, v_ip, p_ua)
  on conflict (session_id) do nothing;
  get diagnostics v_n = row_count;
  return v_n = 1;
end;
$$;

revoke all on function public.registrar_acesso_login(uuid, uuid, timestamptz, text, text, text, text)
  from public, anon, authenticated;
grant execute on function public.registrar_acesso_login(uuid, uuid, timestamptz, text, text, text, text)
  to service_role;

-- ---------------------------------------------------------------------------
-- 5. RPC: listar (paginada; nunca devolve session_id completo)
-- ---------------------------------------------------------------------------
-- Filtro de periodo: p_de INCLUSIVO, p_ate EXCLUSIVO. Ordem: ocorrido_em desc, id desc.
-- limite padrao 50, minimo 1, maximo 100 (valores fora sao ajustados, nao erro); offset >= 0.
-- Uma unica instrucao: total e pagina vem do mesmo snapshot.
create or replace function public.listar_acessos_login(
  p_usuario  uuid default null,
  p_de       timestamptz default null,
  p_ate      timestamptz default null,
  p_limite   integer default 50,
  p_offset   integer default 0
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_limite integer := least(greatest(coalesce(p_limite, 50), 1), 100);
  v_offset integer := greatest(coalesce(p_offset, 0), 0);
  v_res    jsonb;
begin
  with f as (
    select a.id, a.usuario_id, a.session_id, a.ocorrido_em, a.metodo, a.aal,
           a.ip_mascarado, a.ua_familia
      from public.acessos_login a
     where (p_usuario is null or a.usuario_id = p_usuario)
       and (p_de is null or a.ocorrido_em >= p_de)
       and (p_ate is null or a.ocorrido_em < p_ate)
  ),
  pag as (
    select f.id, f.usuario_id, p.nome, f.ocorrido_em, f.metodo, f.aal,
           f.ip_mascarado::text as ip_mascarado, f.ua_familia,
           left(encode(sha256(convert_to(f.session_id::text, 'UTF8')), 'hex'), 8) as sessao_ref
      from f
      join public.perfis p on p.id = f.usuario_id
     order by f.ocorrido_em desc, f.id desc
     limit v_limite offset v_offset
  )
  select jsonb_build_object(
           'total', (select count(*) from f),
           'limite', v_limite,
           'offset', v_offset,
           'itens', coalesce((select jsonb_agg(to_jsonb(pag) order by pag.ocorrido_em desc, pag.id desc)
                                from pag), '[]'::jsonb))
    into v_res;
  return v_res;
end;
$$;

revoke all on function public.listar_acessos_login(uuid, timestamptz, timestamptz, integer, integer)
  from public, anon, authenticated;
grant execute on function public.listar_acessos_login(uuid, timestamptz, timestamptz, integer, integer)
  to service_role;

-- ---------------------------------------------------------------------------
-- 6. RPC: ultimo acesso por usuario (lote, max. 100)
-- ---------------------------------------------------------------------------
create or replace function public.ultimo_acesso_por_usuario(p_usuarios uuid[])
returns table (usuario_id uuid, ultimo_acesso_em timestamptz)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if p_usuarios is null then
    return;
  end if;
  if cardinality(p_usuarios) > 100 then
    raise exception 'Máximo de 100 usuários por consulta.' using errcode = 'AG020';
  end if;
  return query
    select a.usuario_id, max(a.ocorrido_em)
      from public.acessos_login a
     where a.usuario_id = any (p_usuarios)
     group by a.usuario_id;
end;
$$;

revoke all on function public.ultimo_acesso_por_usuario(uuid[]) from public, anon, authenticated;
grant execute on function public.ultimo_acesso_por_usuario(uuid[]) to service_role;

-- ---------------------------------------------------------------------------
-- 7. RPC: expurgo
-- ---------------------------------------------------------------------------
create or replace function public.purgar_acessos_login(p_meses integer default 12)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_n integer;
begin
  if p_meses is null or p_meses < 6 or p_meses > 1200 then
    raise exception 'Retenção mínima de 6 meses.' using errcode = 'AG020';
  end if;

  -- Variavel LOCAL: vale so nesta transacao e e desligada logo apos o DELETE.
  perform set_config('sisveiculos.expurgo_acessos', '1', true);
  delete from public.acessos_login
   where ocorrido_em < now() - make_interval(months => p_meses);
  get diagnostics v_n = row_count;
  perform set_config('sisveiculos.expurgo_acessos', '', true);
  return v_n;
end;
$$;

-- EXECUTE: service_role (backend/pipeline) e o dono `postgres` (pg_cron roda como quem agenda).
revoke all on function public.purgar_acessos_login(integer) from public, anon, authenticated;
grant execute on function public.purgar_acessos_login(integer) to service_role;

-- ---------------------------------------------------------------------------
-- 8. Agendamento CONDICIONAL com pg_cron (nunca falha a migration)
-- ---------------------------------------------------------------------------
-- So agenda se a extensao ja estiver criada. Nao cria a extensao. Se nao estiver, ver
-- docs/acessos-login.md (habilitar no painel e agendar, ou chamar a RPC pelo backend/pipeline).
do $$
begin
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    begin
      perform cron.schedule('purgar-acessos-login', '17 3 * * *',
                            'select public.purgar_acessos_login(12)');
    exception when others then
      raise notice 'pg_cron presente, mas o agendamento do expurgo falhou; agende manualmente (docs/acessos-login.md).';
    end;
  else
    raise notice 'pg_cron ausente: expurgo de acessos_login nao agendado (docs/acessos-login.md).';
  end if;
end
$$;

-- ---------------------------------------------------------------------------
-- 9. Trava: a migration falha se sobrar acesso indevido
-- ---------------------------------------------------------------------------
do $$
declare
  p text;
  r text;
  f text;
begin
  if not (select relrowsecurity from pg_class where oid = 'public.acessos_login'::regclass) then
    raise exception 'public.acessos_login sem RLS.';
  end if;
  if has_table_privilege('anon', 'public.acessos_login', 'select, insert, update, delete')
     or has_table_privilege('authenticated', 'public.acessos_login', 'select, insert, update, delete') then
    raise exception 'public.acessos_login permite acesso direto a anon/authenticated.';
  end if;
  foreach p in array array['insert', 'update', 'delete', 'truncate', 'trigger', 'references'] loop
    if has_table_privilege('service_role', 'public.acessos_login', p) then
      raise exception 'service_role tem % em public.acessos_login.', p;
    end if;
  end loop;

  -- RPCs: so service_role (anon/authenticated/PUBLIC sem EXECUTE).
  foreach f in array array[
    'public.registrar_acesso_login(uuid, uuid, timestamptz, text, text, text, text)',
    'public.listar_acessos_login(uuid, timestamptz, timestamptz, integer, integer)',
    'public.ultimo_acesso_por_usuario(uuid[])',
    'public.purgar_acessos_login(integer)'
  ] loop
    foreach r in array array['anon', 'authenticated'] loop
      if has_function_privilege(r, f::regprocedure, 'execute') then
        raise exception '% executa %.', r, f;
      end if;
    end loop;
    if not has_function_privilege('service_role', f::regprocedure, 'execute') then
      raise exception 'service_role sem EXECUTE em %.', f;
    end if;
    if exists (select 1 from pg_proc p2, aclexplode(coalesce(p2.proacl, acldefault('f', p2.proowner))) a
                where p2.oid = f::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE') then
      raise exception 'PUBLIC executa %.', f;
    end if;
    if not exists (select 1 from pg_proc p2
                    where p2.oid = f::regprocedure and p2.prosecdef
                      and array_to_string(p2.proconfig, ',') like '%search_path=%') then
      raise exception '% sem SECURITY DEFINER ou search_path fixo.', f;
    end if;
  end loop;

  -- Funcao-gatilho: ninguem da API executa.
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if has_function_privilege(r, 'public.acessos_login_proteger()'::regprocedure, 'execute') then
      raise exception '% executa acessos_login_proteger.', r;
    end if;
  end loop;
end
$$;

-- Faz o PostgREST enxergar as novas funcoes.
notify pgrst, 'reload schema';
