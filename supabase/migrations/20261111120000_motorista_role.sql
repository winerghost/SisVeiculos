-- Migration: role 'motorista' (parte 1 de 2 do recurso opcional Perfil Motorista).
-- Nova migration; nao edita as anteriores. Idempotente.
--
-- Motivo: o papel do usuario e o enum public.role_usuario (perfis.role e convites.role usam o tipo).
-- Hoje: ('solicitante', 'autorizador', 'admin'). Esta migration acrescenta 'motorista'.
--
-- POR QUE UM ARQUIVO SEPARADO: no Postgres um valor de enum recem-adicionado so pode ser USADO depois
-- do COMMIT da transacao que o criou (erro 55P04 caso contrario), e o `supabase db push` aplica cada
-- arquivo como uma transacao. A migration seguinte (20261111120100_motorista_recurso.sql) usa o valor
-- em CHECK, funcoes e auditoria; por isso ela precisa vir em outro arquivo, aplicado depois.
--
-- Efeito de ter o role: NENHUM acesso novo. RLS segue deny-by-default (sem politicas e sem grants para
-- anon/authenticated); o role so tem significado na regra de negocio do backend e nos triggers da
-- migration seguinte. Quem so tinha 'autorizador'/'admin' nas checagens existentes continua igual:
-- 'motorista' nao e autorizador nem admin, logo nao autoriza, nao nega, nao registra saida/chegada
-- e nao administra nada ate que a migration seguinte (e o backend) o permitam, e so com o recurso ligado.
-- Fuso: nao se aplica (sem colunas de data/hora).

alter type public.role_usuario add value if not exists 'motorista';
