# Análise do webhook n8n de teste (SisVeiculo)

Data: 2026-11-13. Análise somente leitura, feita no editor do n8n (`https://n8n.revizzicar.com.br`, workflow **SisVeiculo**, id `FCnNXHYgDm67A9kC`). Nada foi alterado, salvo ou ativado, e o webhook não foi chamado. Nenhum segredo consta deste documento.

## Achados

**Estrutura do workflow.** Só existe **um nó**, o `Webhook`, dentro de uma nota "Registro de controle". Não há nós seguintes: nenhuma validação, deduplicação, gravação ou notificação. O título do workflow tem o ícone ▶️ (modo teste/escuta).

**Configuração do nó Webhook:**

| Item | Valor encontrado |
|---|---|
| Método HTTP | `POST` |
| Caminho | `SisVeiculo` (URL de teste: `/webhook-test/SisVeiculo`; produção: `/webhook/SisVeiculo`) |
| Autenticação | **None** |
| Respond | **Immediately** (responde 200 `{"message":"Workflow was started"}` sem esperar nada) |
| Options | nenhuma (sem Raw Body, sem IP allowlist, sem Response Headers) |
| Settings do nó | padrão (Retry on Fail desligado, On Error = stop workflow) |

**Compatibilidade com o backend** (`backend/app/notificacoes/n8n.py`):
- Método, `Content-Type: application/json` e Respond Immediately batem com o cliente. O backend só olha `status < 400`, então a resposta imediata funciona.
- O cliente envia `Authorization: Bearer`, `X-Timestamp`, `X-Assinatura` (`sha256=HMAC(chave, "<ts>.<corpo>")`), `X-Evento-Id` e `X-Entrega-Id`. **O n8n não valida nenhum deles**: com Authentication = None, qualquer pessoa que souber a URL pode enviar eventos falsos. O único "segredo" é o caminho `SisVeiculo`, que é curto e previsível.
- Corpo: sem nós seguintes, **nenhum nome de campo é lido**. Portanto o campo aninhado `detalhes` **não quebra nada hoje**. Em n8n o JSON aninhado chega em `$json.body.detalhes` sem problema; só quebraria expressões futuras mal escritas.
- Observação: o `montar_payload` em disco ainda envia o payload mínimo (`evento`, `evento_id`, `requisicao_id`, `status`, `veiculo_id`, `unidade`, `ocorrido_em`, `versao`). O campo `detalhes` ainda não está no código lido. A análise vale para o payload novo descrito no briefing.

## Riscos

1. **Sem autenticação** (alto, aumenta com o payload completo de saída/chegada, que o CLAUDE.md autoriza a conter dados pessoais): endpoint aberto recebe eventos forjados e dados pessoais ficam expostos a quem conseguir ler as execuções.
2. **Sem verificação de HMAC nem de timestamp**: sem proteção contra adulteração ou replay.
3. **Sem deduplicação por `X-Evento-Id`**: o backend pode reenviar o mesmo evento (timeout com resposta perdida); hoje seria processado duas vezes.
4. **Respond Immediately** esconde falhas: o backend sempre vê sucesso, mesmo que o fluxo posterior falhe. É o modo certo para "notifica e agrega", mas exige alerta de erro no n8n (Error Workflow).
5. **Retenção de execuções**: o n8n guarda o corpo recebido nas execuções. Com dados de saída/chegada completos (LGPD), ajustar retenção e acesso (itens abaixo).
6. Detalhe de teste: no modo teste o webhook só aceita **uma** chamada por escuta; um segundo evento recebe 404 e, após as 2 tentativas, o backend registra "entrega desistida". Isso é esperado, não é bug.

## O que mudar no n8n ao criar a URL de produção

1. **Publicar/ativar** o workflow (produção só responde com o workflow publicado). Atualizar `N8N_WEBHOOK_URL` para `https://n8n.revizzicar.com.br/webhook/SisVeiculo` e fazer `docker compose up -d` (só `.env`, sem build).
2. **Trocar o caminho** por um valor longo e aleatório (por ex. UUID), pois a URL de produção fica permanente. Atualizar `.env` junto.
3. **Authentication = Header Auth**: header `Authorization`, valor `Bearer <N8N_WEBHOOK_TOKEN>` (criar a credencial no n8n; o valor vem do `.env`, nunca do repositório). Não há nome de header customizado: o backend usa `Authorization`.
4. **Validar HMAC** logo após o Webhook (nó Code/If): ativar **Options → Raw Body** para ter os bytes exatos; calcular `HMAC-SHA256(N8N_HMAC_SECRET, X-Timestamp + "." + corpo_bruto)`, comparar com `X-Assinatura` em tempo constante e rejeitar se `|agora - X-Timestamp| > 300 s`. O corpo é serializado com `separators=(",", ":")` e `ensure_ascii=False`, então o HMAC deve ser sobre os bytes recebidos, não sobre JSON reserializado.
5. **Deduplicar por `X-Evento-Id`** (Data Table ou static data), descartando repetidos.
6. **Respond Immediately mantido**, mas com um **Error Workflow** configurado para alertar falhas.
7. **Ler os campos** em `$json.body.*`; tratar `detalhes` como objeto opcional (`$json.body.detalhes?.campo`) e não assumir ordem nem presença das chaves. Versionar por `$json.body.versao`.
8. **Execuções**: em Settings do workflow, desativar "Save successful production executions" ou reduzir a retenção, e restringir quem acessa o n8n, já que o corpo traz dados pessoais.
9. Opcional: limitar por IP de origem (Options → IP(s) Whitelist) ao IP da VPS.
10. Antes da produção, confirmar a política de segurança da AGU (já prevista no CLAUDE.md).

## Resumo

- Um único nó Webhook: POST, caminho `SisVeiculo`, Authentication None, Respond Immediately, sem options.
- Sem nós seguintes: nada lê o corpo, então o `detalhes` aninhado não quebra nada.
- Nenhum cabeçalho (`Authorization`, `X-Assinatura`, `X-Timestamp`, `X-Evento-Id`) é validado.
- Risco principal: endpoint sem autenticação receberá dados pessoais completos em saída/chegada.
- Em produção: URL `/webhook/`, caminho aleatório, Header Auth Bearer, HMAC sobre Raw Body com janela de 5 min.
- Deduplicar por `X-Evento-Id` e configurar Error Workflow.
- Ler `$json.body.*` e tratar `detalhes` como opcional.
- Reduzir a retenção de execuções (LGPD).
- Em deploy, só `.env` muda: `docker compose up -d` sem `--build`.
- O código em disco ainda envia o payload mínimo; `detalhes` não aparece em `montar_payload`.
