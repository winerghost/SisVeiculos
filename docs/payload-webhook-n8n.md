# Payload do webhook n8n

Aprovação LGPD registrada pelo dono do projeto: **só** `requisicao.saida` e `requisicao.chegada`
enviam dados completos (bloco `detalhes`, `versao` 2). Os demais eventos seguem mínimos (`versao` 1).
O envio é best-effort: se montar `detalhes` falhar, o evento sai sem ele (`versao` 1).

A exceção aprovada inclui ids de pessoas, texto livre (`descricao_avarias`, `observacao`, `trajeto`) e coordenadas.
Nunca enviado: tokens, URLs assinadas, caminhos de Storage, e-mail, CPF, telefone, segredos.
Log de falha ao montar `detalhes`: só `evento` e o tipo da exceção, sem traceback.

## Chaves comuns (todos os eventos)

| Chave | Descrição |
|---|---|
| `evento` | `requisicao.criada/autorizada/negada/saida/chegada/cancelada`, `checklist.registrado` |
| `evento_id` | uuid4 estável (igual em reentregas; deduplicar por ele ou por `X-Evento-Id`) |
| `requisicao_id` | id da requisição |
| `status`, `veiculo_id`, `unidade` | allowlist mínima |
| `ocorrido_em` | ISO 8601 UTC |
| `versao` | `1` sem `detalhes`; `2` com `detalhes` |

## `detalhes` (só saída e chegada)

| Chave | Presença | Conteúdo |
|---|---|---|
| `momento` | sempre | `saida` ou `chegada` |
| `requisicao` | sempre | **todas** as colunas da linha, menos: (a) chaves cujo **nome contenha** (sem diferenciar maiúsculas, em qualquer nível de dict/lista) `email`, `e_mail`, `token`, `senha`, `password`, `secret`, `key`, `caminho`, `path`, `url`, `cpf`, `telefone`, `celular` (`DENYLIST_WEBHOOK_REQ`); (b) `km_saida` e `km_chegada` com Quilometragem desligada; (c) `motorista_id` com Motorista desligado, mesmo com valor gravado. Coluna nova e inofensiva entra sozinha |
| `veiculo` | sempre | `{id, descricao}` (placa/modelo) |
| `solicitante`, `autorizado_por`, `saida_registrada_por`, `chegada_registrada_por` | sempre | `{id, nome}` (`null` se ainda não ocorreu) |
| `saida_real_em`, `chegada_real_em` | sempre | timestamps (`chegada_real_em` nulo na saída) |
| `motorista` | recurso Motorista ligado | `{id, nome}` |
| `km_saida`, `km_chegada` | recurso Quilometragem ligado | inteiros ou `null` |
| `localizacao` | recurso Localização do motorista ligado e registrada no momento | linha de `localizacoes_viagem` do momento (coordenadas, precisão, quando, quem), com a mesma denylist por substring |
| `checklist` | recurso Checklist ligado e registrado no momento | `{momento, combustivel, avarias, descricao_avarias, registrado_por {id,nome}, registrado_em, quantidade_fotos}` (sem caminhos nem URLs de fotos) |

Recurso desligado => chave ausente. Regra: campo novo (coluna ou recurso) deve ser repassado aqui e
coberto pelo teste de regressão `test_toda_chave_da_requisicao_vai_em_detalhes_menos_denylist`.

## Arquivos alterados

`backend/app/notificacoes/n8n.py`, `backend/app/services/requisicoes.py`,
`backend/tests/test_notificacoes.py`, `backend/tests/test_motorista_localizacao.py`.
