# Payload do webhook n8n

Aprovação LGPD registrada pelo dono do projeto: **só** `requisicao.saida` e `requisicao.chegada`
enviam dados completos (bloco `detalhes`, `versao` 2). Os demais eventos seguem mínimos (`versao` 1).
O envio é best-effort: se montar `detalhes` falhar, o evento sai sem ele (`versao` 1).

A exceção aprovada inclui ids de pessoas, texto livre (`descricao_avarias`, `observacao`, `trajeto`) e coordenadas.
Nunca enviado: tokens, URLs assinadas, caminhos de Storage, e-mail, CPF, segredos.

Exceção aprovada do telefone (dono do projeto, 2026-11-15): o telefone (WhatsApp, `perfis.telefone`,
só dígitos `55`+DDD+número, ex.: `5561999998888`, o campo `number` do Evolution Go) vai em **todos** os
eventos `requisicao.*` no bloco `contatos`, e nas pessoas de `detalhes` em saída/chegada. Só o admin o
cadastra (`/admin/usuarios`). Uso no n8n: na autorização avisa o motorista; na chegada, o solicitante e
quem autorizou. A exceção cobre **só** `perfis.telefone`, montado à parte (`contatos` e pessoas de
`detalhes`): `telefone`/`celular` **seguem na denylist** das linhas `requisicao`, `veiculo` e
`localizacao`, então uma coluna futura com telefone de terceiro não sai sem revisão de LGPD.
Log de falha ao montar `detalhes` ou `contatos`: só `evento` e o tipo da exceção, sem traceback e
nunca com telefone (`n8n: detalhes indisponiveis evento=...` / `n8n: contatos indisponiveis evento=...`).
Falhou a leitura, a chave sai `null` e a ação segue.

## Chaves comuns (todos os eventos)

| Chave | Descrição |
|---|---|
| `evento` | `requisicao.criada/autorizada/negada/saida/chegada/cancelada`, `checklist.registrado` |
| `evento_id` | uuid4 estável (igual em reentregas; deduplicar por ele ou por `X-Evento-Id`) |
| `requisicao_id` | id da requisição |
| `status`, `veiculo_id`, `unidade` | allowlist mínima |
| `ocorrido_em` | ISO 8601 UTC |
| `versao` | `1` sem `detalhes`; `2` com `detalhes` |
| `contatos` | só eventos `requisicao.*` (ver abaixo); ausente em `checklist.registrado` ou se a leitura falhar (best-effort) |

## `contatos` (todos os eventos de requisição)

| Chave | Conteúdo |
|---|---|
| `solicitante` | `{id, nome, telefone}` do dono da requisição |
| `autorizador` | `{id, nome, telefone}` de `autorizado_por` (quem autorizou ou negou); `null` antes da decisão |
| `motorista` | `{id, nome, telefone}` ou `null` se não designado; **chave ausente** com o recurso Motorista desligado |

`telefone` é `null` quando a pessoa não tem número cadastrado.

## `detalhes` (só saída e chegada)

| Chave | Presença | Conteúdo |
|---|---|---|
| `momento` | sempre | `saida` ou `chegada` |
| `requisicao` | sempre | **todas** as colunas da linha, menos: (a) chaves cujo **nome contenha** (sem diferenciar maiúsculas, em qualquer nível de dict/lista) `email`, `e_mail`, `token`, `senha`, `password`, `secret`, `key`, `caminho`, `path`, `url`, `cpf`, `telefone`, `celular` (`DENYLIST_WEBHOOK_REQ`; o telefone das pessoas sai só em `contatos` e nos blocos de pessoa); (b) `km_saida`, `km_chegada` e `km_percorrido` com Quilometragem desligada; (c) `motorista_id` e `motorista_alterado_por` com Motorista desligado, mesmo com valor gravado. A linha é lida com `select *`: coluna nova entra sozinha |
| `veiculo` | sempre | linha **inteira** do veículo (`select *`: id, descricao, placa, modelo, ativo e colunas futuras), com a mesma denylist; `codigo_publico` só com Leitor de QR ligado |
| `solicitante`, `autorizado_por`, `saida_registrada_por`, `chegada_registrada_por` | sempre | `{id, nome, telefone}` (campos `null` se ainda não ocorreu) |
| `saida_real_em`, `chegada_real_em` | sempre | timestamps (`chegada_real_em` nulo na saída) |
| `motorista` | recurso Motorista ligado | `{id, nome, telefone}` |
| `km_saida`, `km_chegada`, `km_percorrido` | recurso Quilometragem ligado | inteiros ou `null`; `km_percorrido` = `km_chegada - km_saida`, calculado pelo banco (trigger), preenchido na chegada |
| `localizacao` | recurso Localização do motorista ligado e registrada no momento | linha de `localizacoes_viagem` do momento (coordenadas, precisão, quando, quem), com a mesma denylist por substring |
| `checklist` | recurso Checklist ligado e registrado no momento | `{momento, combustivel, avarias, descricao_avarias, registrado_por {id,nome,telefone}, registrado_em, quantidade_fotos}` (sem caminhos nem URLs de fotos) |

Recurso desligado => chave ausente. Regra: campo novo (coluna ou recurso) deve ser repassado aqui e
coberto pelo teste de regressão `test_toda_chave_da_requisicao_vai_em_detalhes_menos_denylist`.

## Arquivos alterados

`backend/app/notificacoes/n8n.py`, `backend/app/services/requisicoes.py`,
`backend/tests/test_notificacoes.py`, `backend/tests/test_motorista_localizacao.py`,
`backend/tests/test_telefone.py` (telefone e `contatos`).
