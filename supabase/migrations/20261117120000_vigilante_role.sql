-- Migration: role 'vigilante' (parte 1 de 3 do Perfil Vigilante / seguranca patrimonial).
-- Nova migration; nao edita as anteriores. Idempotente.
--
-- Motivo: o vigilante registra saida e chegada de qualquer requisicao autorizada/em uso, sem autorizar,
-- negar nem cancelar (docs/plano-motorista-vigilante.md, secoes 2 e 3). O papel do usuario e o enum
-- public.role_usuario (perfis.role e convites.role usam o tipo); hoje:
-- ('solicitante', 'autorizador', 'admin', 'motorista'). Esta migration acrescenta 'vigilante'.
--
-- POR QUE UM ARQUIVO SEPARADO: no Postgres um valor de enum recem-adicionado so pode ser USADO depois do
-- COMMIT da transacao que o criou (erro 55P04), e o `supabase db push` aplica cada arquivo como uma
-- transacao. As migrations seguintes (20261117120100 e 20261117120200) usam o valor em funcoes; por isso
-- precisam vir em outros arquivos, aplicados depois.
--
-- Efeito de ter o role: NENHUM acesso novo. RLS segue deny-by-default (sem politicas e sem grants para
-- anon/authenticated); o role so tem significado na regra de negocio do backend e nos triggers da
-- migration 20261117120200. Antes dela, 'vigilante' nao e autorizador nem admin: as checagens existentes
-- ('autorizador'/'admin') o recusam (AG002).
-- Fuso: nao se aplica (sem colunas de data/hora).

alter type public.role_usuario add value if not exists 'vigilante';
