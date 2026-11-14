# Leitor de QR Code dos veículos (Fase 3)

Recurso opcional `leitor_qr_veiculos` (desligado por padrão; a API decide via `eu.recursos`).

## Decisão de biblioteca

Leitura: **`BarcodeDetector` nativo quando existir e aceitar `qr_code`; senão `jsQR` (fallback em JS puro).**
Geração (admin): **`qrcode-generator`** (zero dependências, só gera os módulos; o SVG é montado no cliente).

| Opção | Manutenção | Tamanho (npm) | iOS Safari | Worker/CSP | Licença | Veredito |
|---|---|---|---|---|---|---|
| `BarcodeDetector` nativo | Parte do navegador | 0 | **Não existe** | Nenhum | n/a | Usar quando houver (Chrome/Android; rápido, sem custo de JS) |
| `jsQR` 1.4.0 | Estável, sem mudanças funcionais (publicação de 11/2025) | ~280 kB (unpacked; ~130 kB min) | Sim | Roda na thread principal, **sem worker, sem WASM** | Apache-2.0 | **Fallback escolhido**: menor custo e não exige mudar o CSP |
| `qr-scanner` (nimiq) 1.4.2 | Última versão em 11/2022 | ~520 kB | Sim | Usa Web Worker (arquivo/Blob); `worker-src 'self'` exigiria cuidado com bundler | MIT | Descartado: parado desde 2022 e worker |
| `@zxing/browser` 0.2.1 | Baixa atividade (porte de ZXing) | ~5,8 MB | Sim | Sem worker, mas pesado | MIT | Descartado: tamanho |
| `barcode-detector` 3.x (polyfill) | Ativo | ~260 kB + `zxing-wasm` | Sim | **WASM** (precisa `wasm-unsafe-eval` e do `.wasm` servido de fora ou empacotado) | MIT | Descartado: exigiria afrouxar o CSP |

`qrcode` (geração) traz `pngjs` e `yargs` (voltados a Node); `qrcode-generator` é menor e basta.

Custos do `jsQR`: roda na thread principal; mitigado reduzindo o quadro a no máximo 480 px de lado e
decodificando ~5 vezes por segundo (intervalo de 200 ms). O `BarcodeDetector` e o `jsQR` são
carregados no mesmo pacote (o `jsQR` é pequeno); se o tamanho do bundle virar problema, dá para
importá-lo sob demanda (`import()`) só quando não houver `BarcodeDetector`.

**CSP:** nenhuma mudança necessária (sem worker, sem WASM, sem `blob:` em `worker-src`). A câmera já
está liberada por `Permissions-Policy: camera=(self)` em `security-headers.conf`.

**`npm audit`:** `npm audit --omit=dev` = 0 vulnerabilidades (produção). Os achados do `npm audit`
completo (`serve`/`compression`, `minimatch` via eslint) são de devDependencies anteriores e não vêm
das novas dependências.

## Conteúdo do QR

Somente `AGU-VEICULO:<codigo_publico>`, com `codigo_publico` no formato do backend `^[a-z0-9]{16,32}$`.
O leitor extrai o código com regex estrita (`lib/qr.ts`); qualquer outro conteúdo (URL, JWT, JSON, texto
livre) mostra "QR Code inválido" e **nada é interpretado nem enviado**. Ler o QR não autoriza nada.

## Comportamento

- Aparece só com `eu.recursos.leitor_qr_veiculos === true` (item "Ler QR" no menu e a página `/veiculos/ler`).
- A câmera só abre após o clique em "Ler QR Code". `getUserMedia` com `facingMode: environment` (ideal) e fallback
  para qualquer câmera. "Encerrar câmera", desmontagem da tela e aba oculta param todas as trilhas.
- Nenhum quadro é salvo nem enviado: só o código lido vai ao Flask (`GET /api/veiculos/por-codigo/<codigo>`).
- Antiduplicidade: o mesmo texto é ignorado por ~3 s e há uma consulta por vez.
- Digitação manual do código (mesma rota da API).
- Resultado: descrição, placa, disponibilidade e motivo como a API devolver; "Solicitar este veículo"
  (`/requisicoes/nova?veiculo=<id>`) só se `acoes_permitidas` incluir `solicitar`. `?veiculo=` apenas
  pré-seleciona; a API revalida ao enviar.
- Estados: abrindo, câmera indisponível (ou sem HTTPS), permissão negada (orientação), QR inválido,
  veículo inexistente (mensagem da API) e consulta em andamento (Spinner). Toque leve (`tatil.leitura()`) quando a flag ligar.

## Admin

No cartão do veículo, se a API devolver `codigo_publico`: QR em SVG (gerado no cliente), **Imprimir**
(janela só com o QR) e **Rotacionar código** (botão só se `acoes_permitidas` incluir `rotacionar_codigo`,
com diálogo de confirmação e Spinner).

## Contrato assumido com o backend (a confirmar com o time Back)

- `GET /api/veiculos/por-codigo/<codigo>` → mesmo formato de `Veiculo` (`id`, `descricao`, `placa`,
  `disponivel`, `motivo_indisponibilidade`, `acoes_permitidas`).
- `GET /api/admin/veiculos` → cada item com `codigo_publico` (só admin, só com o recurso ligado).
- `POST /api/admin/veiculos/<id>/rotacionar-codigo` → `VeiculoAdmin` atualizado; ação `rotacionar_codigo`.
