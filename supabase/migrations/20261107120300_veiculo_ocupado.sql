-- Migration 4: regra central "veiculo ocupado".
-- Motivo: so pode existir UMA requisicao aberta por veiculo (sem reserva futura;
-- funciona como fila). Aberta = status solicitada, autorizada ou em_uso.
-- negada, cancelada e concluida NAO entram no indice e, portanto, liberam o veiculo.
-- Fica no banco (indice unico parcial) para ser seguro contra concorrencia: duas
-- insercoes simultaneas no mesmo veiculo resultam em uma so persistida.
-- O backend deve traduzir o erro 23505 (unique_violation) deste indice
-- (ux_requisicoes_veiculo_aberta) em mensagem clara ao usuario.
-- Nao usa btree_gist nem exclusion constraint (nao ha intervalo de horario).
-- O indice tambem serve a buscas por veiculo_id + status aberto.

create unique index if not exists ux_requisicoes_veiculo_aberta
  on public.requisicoes (veiculo_id)
  where status in ('solicitada', 'autorizada', 'em_uso');

comment on index public.ux_requisicoes_veiculo_aberta is
  'Um veiculo so tem uma requisicao aberta (solicitada/autorizada/em_uso). Violacao = 23505; a API traduz em "veiculo ocupado".';
