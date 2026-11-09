-- Migration: IA opcional, Fase 2 (resumo narrativo em Dashboards/Relatorios). Nova migration; NAO edita as
-- anteriores. Idempotente.
--
-- Motivo: o resumo narrativo e um novo recurso de IA, ligado/desligado pelo admin como `saudacao`.
--
-- 1. public.ia_configuracoes.resumo boolean not null default false (desligado por padrao). Nenhuma
--    mudanca de trigger/grants e necessaria, e foi verificado:
--      * ia_configuracoes_validar() (20261108130000) e generica: compara to_jsonb(new) x to_jsonb(old)
--        sem as colunas de controle, entao a coluna nova ja gera uma linha em `auditoria`
--        (acao='configuracao_alterada', tabela='configuracoes', detalhes {"recurso":"ia.resumo"}).
--      * Os grants da tabela sao por tabela (sem lista de colunas): anon/authenticated revogados,
--        service_role herda select/insert/update; a coluna nova entra automaticamente.
-- 2. ck_ia_uso_funcao passa a aceitar 'saudacao', 'teste' e 'resumo' (valores atuais: 'saudacao','teste',
--    migration 20261111120200). Drop + add toma ACCESS EXCLUSIVE em ia_uso por instantes e valida as
--    linhas existentes (todas dentro da lista antiga, que e subconjunto da nova). Nenhum dado e alterado.

alter table public.ia_configuracoes
  add column if not exists resumo boolean not null default false;

alter table public.ia_uso drop constraint if exists ck_ia_uso_funcao;
alter table public.ia_uso
  add constraint ck_ia_uso_funcao check (funcao in ('saudacao', 'teste', 'resumo'));
