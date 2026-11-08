-- IA: amplia o CHECK ck_ia_uso_funcao para aceitar a funcao 'teste'.
--
-- Motivo: o botao "Testar conexao" (Configuracoes > IA) registra o consumo em public.ia_uso com
-- funcao = 'teste', mas a migration 20261108130000 so aceitava 'saudacao'.
--
-- Efeitos: drop + add constraint toma ACCESS EXCLUSIVE em public.ia_uso por instantes e valida as
-- linhas existentes (todas 'saudacao', ou nenhuma). Nenhum dado e alterado. RLS, grants e triggers
-- (append-only) permanecem como estao. Idempotente: pode ser reaplicada sem erro.

alter table public.ia_uso drop constraint if exists ck_ia_uso_funcao;
alter table public.ia_uso
  add constraint ck_ia_uso_funcao check (funcao in ('saudacao', 'teste'));
