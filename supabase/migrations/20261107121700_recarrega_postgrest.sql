-- Migration 17: pede ao PostgREST que releia a configuracao por role e o schema.
-- Nova migration; nao edita a 16. Idempotente (NOTIFY pode ser repetido sem efeito colateral).
--
-- Motivo: a migration 16 aplicou lock_timeout por role e enviou 'reload config', mas o NOTIFY so
-- chega a quem esta escutando no momento. Aqui o reload e reenviado de forma isolada (config de
-- roles + cache de schema) para o PostgREST refletir o estado final das migrations 1-16.
--
-- Escopo: somente NOTIFY. Nao altera RLS, policies, grants, roles nem dados.

notify pgrst, 'reload config';
notify pgrst, 'reload schema';
