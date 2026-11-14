# Plano de ação — Recursos web nativos (sem Capacitor)

Cinco recursos opcionais, **todos desligados por padrão** e habilitados pelo admin em Configurações
(o backend é a fonte da verdade: `GET /api/eu` devolve `recursos`; o front só exibe o que a API
informar como ligado). Ordem obrigatória, cada fase implementada e testada antes da próxima.

| Flag (Configurações) | Recurso | Fase |
|---|---|---|
| `indicador_conectividade` | Indicador de conexão com a internet e com o Flask | 1 |
| `feedback_tatil` | Vibração curta (Vibration API) | 2 |
| `leitor_qr_veiculos` | Leitor de QR Code dos veículos | 3 |
| `voz_campos` | Ditado por voz (Web Speech API) | 4 |
| `passkeys` | Passkeys (WebAuthn) como reautenticação/fator adicional | 5 |

## Fase 0 — Fundação (flags)
Uma migration (`20261121120000_recursos_web_nativos.sql`) com as 5 colunas e `configuracoes_validar`
(cópia fiel da versão atual + chaves novas, auditoria preservada); backend: `Configuracoes`,
`CATALOGO`, validação, testes. O front lê `eu.recursos.<flag>`.

## Fase 1 — Conectividade
`/api/saude` já existe (público, sem rate limit, `{"status":"ok"}`): só garantir resposta mínima e
rápida e testar. Front: `lib/conectividade.ts` (online/offline + sonda com timeout, uma por vez,
intervalo controlado, `navigator.onLine` nunca prova o Flask) e `components/indicador-conexao.tsx`
no `AppShell`. Estados: conectado, sem internet, servidor indisponível, conexão restabelecida.
Só informativo; sem escrita offline e sem fila.

## Fase 2 — Feedback tátil
`lib/tatil.ts` (`navigator.vibrate` com checagem de suporte, padrões curtos, respeita
`prefers-reduced-motion`, nunca na navegação, só com a flag ligada). Usos: leitura de QR, confirmação,
saída, chegada, checklist concluído, erro importante. Sempre complemento do visual; regra alguma depende dele.

## Fase 3 — Leitor de QR Code
- **Conteúdo do QR**: apenas um **código público opaco** do veículo (`veiculos.codigo_publico`, aleatório,
  único; migration nova). Nunca JWT, dados pessoais, permissões. Ler o QR não autoriza nada.
- **API**: `GET /api/veiculos/por-codigo/<codigo>` (JWT; devolve só o que o perfil pode ver +
  `acoes_permitidas`; 404 uniforme; rate limit). Admin vê/gera o QR do veículo para impressão.
- **Biblioteca**: decidir por análise (BarcodeDetector nativo quando existir + fallback de leitura em
  JS; critérios: manutenção, tamanho, compatibilidade iOS, `npm audit`). Sem upload de imagem.
- **UI**: câmera traseira, moldura de posicionamento, anti-duplicidade, encerrar câmera, digitação
  manual, erros (câmera indisponível, permissão negada, QR inválido, veículo inexistente).

## Fase 4 — Voz
`lib/voz.ts` + `components/campo-voz.tsx` (botão de microfone, indicador, parar/cancelar, revisar,
nunca substitui texto sem confirmação, nunca envia sozinho). Campos: observação da requisição,
descrição de avarias e observações do checklist. **Privacidade**: no Chrome/Safari o reconhecimento
pode usar servidor do fornecedor do navegador; usar processamento local quando o navegador
oferecer e, sem ele, aviso claro e consentimento antes do uso; documentar para aprovação da AGU.

## Fase 5 — Passkeys (primeira etapa)
Supabase Auth continua como provedor de identidade e emissor do JWT; **nenhum JWT é emitido pelo
Flask**. A Passkey é validada no backend (`webauthn`/py_webauthn: desafio, assinatura, origem, RP ID,
expiração, uso único, contador) e serve como **reautenticação**: cadastro e revogação de novas Passkeys
exigem login recente (claim `iat`) ou reautenticação por Passkey; login continua por e-mail/senha.
Tabelas `credenciais_webauthn` e `desafios_webauthn` (RLS deny-by-default, RPCs service_role, auditoria).
Substituir o login por Passkey fica **fora desta etapa**, dependendo de análise e aprovação.

## Times por fase (arquivos exclusivos)
Cada fase abre painéis herdr nomeados com a tarefa, roda agentes de banco, backend, front e testes
em paralelo no que for independente, verifica (ruff/black/pytest, tsc/eslint, e2e) e fecha os painéis.

## Entrega de cada fase
Arquivos, dependências, testes e resultado, migration (sim/não), rebuild Docker (sim, `--build`) e limitações.
