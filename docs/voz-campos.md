# Ditado por voz nos campos de texto (`voz_campos`)

Recurso opcional, desligado por padrão. O admin liga em Configurações; o front só mostra o
microfone se `GET /api/eu` devolver `recursos.voz_campos === true` **e** o navegador tiver a Web
Speech API. Sem isso, o campo segue só com digitação (a alternativa está sempre presente).

## Onde aparece

Observação da requisição (`/requisicoes/nova`) e descrição das avarias do checklist. O checklist
atual não tem campo separado de "observações"; se a API ganhar um, basta envolvê-lo em `CampoVoz`.
Nunca em identificação, e-mail, senha, km ou localização.

## Como funciona

- `lib/voz.ts`: detecção, criação do reconhecedor (`pt-BR`, `continuous=false`,
  `interimResults=true`, `maxAlternatives=1`) e consentimento em memória.
- `lib/use-voz.ts`: estados `indisponivel | ocioso | pedindo_permissao | ouvindo | processando | erro`,
  `iniciar`, `parar`, `cancelar`.
- `components/campo-voz.tsx`: botão "Falar", indicador "Ouvindo...", Parar/Cancelar e área
  "Texto reconhecido" (editável) com **Inserir no fim do campo** (preserva o digitado),
  **Substituir** (só após confirmação) e **Descartar**. Respeita `maxLength` com aviso de corte.
- O microfone só liga no clique. Para sozinho em 60 s, ao desmontar, ao sair do campo e com a aba
  oculta. O texto nunca vai ao Flask por conta própria: só entra no campo e o usuário envia o
  formulário como sempre (a API valida como qualquer outro texto).

## Privacidade (para aprovação da política de segurança da AGU)

- **Nenhuma gravação**: o código não usa `MediaRecorder` nem cria Blob de áudio; nada é guardado.
- **Nada vai ao n8n** a partir do áudio; o texto só segue no fluxo normal da requisição/checklist.
- **Processamento local**: se o navegador oferecer (`SpeechRecognition.available/install` e
  `processLocally`), o áudio fica no aparelho.
- **Sem processamento local**: o áudio é tratado pelo serviço do fornecedor do navegador (Chrome =
  Google, Safari = Apple), fora do controle do SisVeiculos. Antes do primeiro uso na sessão há um
  diálogo de consentimento ("Concordar e usar" / "Cancelar"). O consentimento fica só em memória
  (some ao recarregar; nunca em `localStorage`).
- Pontos a decidir pela AGU: se o uso do serviço do fornecedor é aceitável para dados de
  deslocamentos oficiais (LGPD); se convém exigir sempre processamento local (recusar o fallback).
- Sem dados pessoais em logs; o código não registra o texto reconhecido.

## Compatibilidade

| Ambiente                      | Situação                                                                                |
| ----------------------------- | --------------------------------------------------------------------------------------- |
| Chrome/Edge desktop e Android | Suportado; serviço do fornecedor, local se o modelo pt-BR estiver disponível            |
| Safari iOS/iPadOS e macOS     | Suportado (`webkitSpeechRecognition`), serviço da Apple; comportamento varia por versão |
| Firefox                       | Sem suporte: o botão não aparece                                                        |
| PWA instalado                 | Depende do navegador/SO; exige HTTPS e permissão de microfone                           |

## Limitações

- Qualidade e idioma dependem do navegador; ruído e sotaques afetam o resultado.
- Uma frase por captura (`continuous=false`); o navegador pode encerrar antes dos 60 s.
- Não testado com microfone real nesta entrega (verificação por tipos e lint).
