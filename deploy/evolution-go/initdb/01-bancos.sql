-- Roda só quando o volume do Postgres é criado pela primeira vez.
-- evogo_auth já nasce pelo POSTGRES_DB; aqui cria o segundo banco usado pela Evolution GO.
CREATE DATABASE evogo_users;
