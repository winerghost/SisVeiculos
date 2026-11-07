-- Migration 16: limita a espera por lock no banco para as roles da API (PostgREST).
-- Nova migration; nao edita as anteriores. Idempotente (ALTER ROLE ... SET sobrescreve o mesmo valor).
--
-- Motivo: o contrato da migration 15 pede lock_timeout curto + retry em 55P03/40P01, mas o backend
-- fala com o PostgREST por comando unico (cada requisicao e uma transacao) e nao consegue emitir
-- SET LOCAL lock_timeout. Sem limite, uma espera por lock (ex.: advisory 'sisveiculos.perfis_admin'
-- seguro por outra transacao) prenderia a requisicao ate o statement_timeout da role.
--
-- Como funciona: o PostgREST (v9+) le as configuracoes por role (pg_db_role_setting) e as aplica
-- a cada requisicao ao assumir a role do JWT (SET LOCAL ROLE), o mesmo mecanismo que faz valer o
-- statement_timeout por role documentado pela Supabase (anon 3s, authenticated 8s). Um simples
-- SET ROLE em outra ferramenta NAO aplica o ALTER ROLE; ele vale em login e no PostgREST.
-- O PostgREST guarda esse cache: por isso o NOTIFY 'reload config' ao final.
--
-- Roles:
--  * service_role  : usada pelo backend Flask. Principal alvo.
--  * authenticated, anon : sem uso de escrita pelo front (dados passam pelo Flask), mas entram
--    por defesa em profundidade: se alguma policy/rota as usar, a espera tambem e limitada.
--  * authenticator : NAO alterada. E apenas a role de login do pool do PostgREST; como as
--    configuracoes das roles acima prevalecem por requisicao, nao agrega protecao e afetaria
--    tambem conexoes administrativas que usem essa role.
--  * postgres / supabase_admin / supabase_auth_admin : NAO alteradas. O pipeline (db push) e o
--    seed rodam como postgres; DDL e migrations podem legitimamente esperar mais (ex.: ALTER TABLE
--    aguardando uma transacao longa) e nao devem falhar por 5s.
--
-- Efeito para o backend: lock_timeout expira com SQLSTATE 55P03 (lock_not_available); o contrato
-- de retry da migration 15 (55P03 e 40P01) passa a valer na pratica.
-- Funcoes SECURITY DEFINER rodam na mesma sessao: herdam o lock_timeout do chamador (nao ha
-- mudanca de privilegio, RLS ou grant nesta migration).
--
-- Fuso: nao se aplica (sem timestamptz aqui).

alter role service_role  set lock_timeout = '5s';
alter role authenticated set lock_timeout = '5s';
alter role anon          set lock_timeout = '5s';

-- Recarrega a configuracao do PostgREST para ele reler as configuracoes por role.
notify pgrst, 'reload config';
