# Payload do webhook n8n

Aprovação LGPD registrada pelo dono do projeto: **só** `requisicao.saida` e `requisicao.chegada`
enviam dados completos (bloco `detalhes`, `versao` 2). Os demais eventos seguem mínimos (`versao` 1).
O envio é best-effort: se montar `detalhes` falhar, o evento sai sem ele (`versao` 1).

A exceção aprovada inclui ids de pessoas, texto livre (`descricao_avarias`, `observacao`, `trajeto`) e coordenadas.
Nunca enviado: tokens, URLs assinadas, caminhos de Storage, CPF, segredos; e-mail só pelo bloco
`contatos` e pessoas de `detalhes` (exceção abaixo).

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

Exceção aprovada do e-mail (dono do projeto, 2026-11-15): o e-mail de login (`auth.users`, lido pelo
backend via Admin API) segue **exatamente** a regra do telefone: vai em todos os eventos `requisicao.*`
no bloco `contatos` e nas pessoas de `detalhes` em saída/chegada, montado à parte. Conta inativa também
envia. `email`/`e_mail` **seguem na denylist** de qualquer outra coluna. Nunca vai a log nem a rota que
não seja admin.

Exceção aprovada do resumo (dono do projeto, 2026-11-15): todo evento `requisicao.*` leva o bloco
`resumo` (nome do solicitante, saída prevista e trajeto) para o n8n montar a mensagem de WhatsApp,
sem IA. Montado à parte por allowlist fixa desses campos; `observacao` **nunca** entra. Não muda a
`versao`. Nunca vai a log nem à resposta da API.

## Chaves comuns (todos os eventos)

| Chave | Descrição |
|---|---|
| `evento` | `requisicao.criada/autorizada/negada/saida/chegada/cancelada/motorista_alterado`, `checklist.registrado` |
| `evento_id` | uuid4 estável (igual em reentregas; deduplicar por ele ou por `X-Evento-Id`) |
| `requisicao_id` | id da requisição |
| `status`, `veiculo_id`, `unidade` | allowlist mínima |
| `ocorrido_em` | ISO 8601 UTC |
| `versao` | `1` sem `detalhes`; `2` com `detalhes` |
| `contatos` | só eventos `requisicao.*` (ver abaixo); ausente em `checklist.registrado` ou se a leitura falhar (best-effort) |
| `resumo` | só eventos `requisicao.*` (ver abaixo); ausente em `checklist.registrado` ou se a leitura falhar (best-effort) |
| `cancelamento` | só em `requisicao.cancelada` (ver abaixo) |
| `motorista_alteracao` | só em `requisicao.motorista_alterado` (ver abaixo) |

## `contatos` (todos os eventos de requisição)

| Chave | Conteúdo |
|---|---|
| `solicitante` | `{id, nome, telefone, email}` do dono da requisição |
| `autorizador` | `{id, nome, telefone, email}` de `autorizado_por` (quem autorizou ou negou); `null` antes da decisão |
| `motorista` | `{id, nome, telefone, email}` ou `null` se não designado; **chave ausente** com o recurso Motorista desligado |

`telefone` é `null` quando a pessoa não tem número cadastrado.

`email` (login do `auth.users`, lido pela Admin API) é `null` em dois casos, que o n8n **não** deve tratar como
"pessoa sem e-mail":
- a conta não existe no Auth (404): `null` só para essa pessoa;
- a Admin API falhou ou passou do prazo (2 s no total, best-effort): `null` para **todas** as pessoas do evento
  (em `contatos` e em `detalhes`). A ação segue normalmente e o backend registra só
  `n8n: emails indisponiveis evento=... (<tipo da exceção>)`, sem PII.

Com o n8n desligado (sem `N8N_WEBHOOK_URL`), o backend não monta `contatos` nem `detalhes` e não lê
telefone nem e-mail.

## `resumo` (todos os eventos de requisição)

| Chave | Conteúdo |
|---|---|
| `nome_solicitante` | `nome_solicitante` da requisição |
| `hora_saida` | saída **prevista** (coluna `hora_saida`), ISO 8601 UTC, ex.: `2026-11-16T17:30:00+00:00` |
| `hora_saida_fmt` | a mesma saída prevista em America/Sao_Paulo, `dd/mm/aaaa HH:MM`, ex.: `16/11/2026 14:30` |
| `trajeto` | trajeto da requisição |

Vem da requisição em memória quando ela tem os campos; senão o backend relê a linha completa. Se
não der para ler, a chave **fica ausente** e o evento segue; o log traz só
`n8n: resumo indisponivel evento=... (<tipo da exceção>)`, sem PII.

## Blocos por evento (aprovados em 2026-11-16, sem dado pessoal)

Montados no serviço e filtrados por uma allowlist fixa por evento (`BLOCOS_POR_EVENTO`, em
`backend/app/notificacoes/n8n.py`): bloco ou campo fora dela é descartado. Não mudam a `versao`.

### `cancelamento` (`requisicao.cancelada`)

| Chave | Conteúdo |
|---|---|
| `origem` | `solicitante` quando quem cancelou é o dono da requisição (inclusive autorizador/admin cancelando a própria); `equipe` nos demais casos |
| `status_anterior` | `solicitada` ou `autorizada` |

Quem cancelou (id, nome, contato) **não** é enviado. Uso no n8n: avisar o solicitante quando
`origem = equipe` (pendência B1).

### `motorista_alteracao` (`requisicao.motorista_alterado`)

Emitido depois do commit de `POST /api/requisicoes/<id>/motorista` (designação, troca ou remoção do
motorista em `autorizada`/`em_uso`) com o recurso Motorista ligado. Sem mudança (mesmo motorista),
recusa (400/403/404/409) ou recurso desligado: nenhum evento. A designação feita **na autorização**
segue saindo só em `requisicao.autorizada`.

| Chave | Conteúdo |
|---|---|
| `tipo` | `designado` (não havia motorista), `trocado` ou `removido` (passou a `null`) |
| `status` | `autorizada` ou `em_uso` |

`contatos.motorista` já traz o **novo** motorista (`null` na remoção). O motorista anterior não sai
em lugar nenhum do evento (opção A1). O evento leva `contatos` e `resumo` como os demais
`requisicao.*`, sem `detalhes` (`versao` 1).

## `detalhes` (só saída e chegada)

| Chave | Presença | Conteúdo |
|---|---|---|
| `momento` | sempre | `saida` ou `chegada` |
| `requisicao` | sempre | **todas** as colunas da linha, menos: (a) chaves cujo **nome contenha** (sem diferenciar maiúsculas, em qualquer nível de dict/lista) `email`, `e_mail`, `token`, `senha`, `password`, `secret`, `key`, `caminho`, `path`, `url`, `cpf`, `telefone`, `celular` (`DENYLIST_WEBHOOK_REQ`; telefone e e-mail das pessoas saem só em `contatos` e nos blocos de pessoa); (b) `km_saida`, `km_chegada` e `km_percorrido` com Quilometragem desligada; (c) `motorista_id` e `motorista_alterado_por` com Motorista desligado, mesmo com valor gravado. A linha é lida com `select *`: coluna nova entra sozinha |
| `veiculo` | sempre | linha **inteira** do veículo (`select *`: id, descricao, placa, modelo, ativo e colunas futuras), com a mesma denylist; `codigo_publico` só com Leitor de QR ligado |
| `solicitante`, `autorizado_por`, `saida_registrada_por`, `chegada_registrada_por` | sempre | `{id, nome, telefone, email}` (campos `null` se ainda não ocorreu) |
| `saida_real_em`, `chegada_real_em` | sempre | timestamps (`chegada_real_em` nulo na saída) |
| `motorista` | recurso Motorista ligado | `{id, nome, telefone, email}` |
| `km_saida`, `km_chegada`, `km_percorrido` | recurso Quilometragem ligado | inteiros ou `null`; `km_percorrido` = `km_chegada - km_saida`, calculado pelo banco (trigger), preenchido na chegada |
| `localizacao` | recurso Localização do motorista ligado e registrada no momento | linha de `localizacoes_viagem` do momento (coordenadas, precisão, quando, quem), com a mesma denylist por substring |
| `checklist` | recurso Checklist ligado e registrado no momento | `{momento, combustivel, avarias, descricao_avarias, registrado_por {id,nome,telefone,email}, registrado_em, quantidade_fotos}` (sem caminhos nem URLs de fotos) |

Recurso desligado => chave ausente. Regra: campo novo (coluna ou recurso) deve ser repassado aqui e
coberto pelo teste de regressão `test_toda_chave_da_requisicao_vai_em_detalhes_menos_denylist`.

## Arquivos alterados

`backend/app/notificacoes/n8n.py`, `backend/app/services/requisicoes.py`,
`backend/tests/test_notificacoes.py`, `backend/tests/test_motorista_localizacao.py`,
`backend/tests/test_telefone.py` (telefone e `contatos`), `backend/tests/test_webhook_resumo.py`
(`resumo`), `backend/tests/test_webhook_motorista_cancelamento.py` (`cancelamento` e
`requisicao.motorista_alterado`).
