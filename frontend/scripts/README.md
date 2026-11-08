# Scripts de ativos (rodar só quando a arte mudar)

Usam o Playwright já instalado no projeto (Chromium local, sem rede) e nenhuma dependência nova.
Rode a partir de `frontend/` e commite os arquivos gerados.

## Imagem de compartilhamento (Open Graph)

    node scripts/gerar-og.mjs

Gera `public/og/og-agu-v1.png` (1200x630, bem abaixo de 300 KB): fundo #0F3D75, título e subtítulo em
Inter (woff2 local de `@fontsource-variable/inter`) e `public/logo-agu.webp` em tamanho nativo sobre
um cartão claro. O PNG é recomprimido (deflate nível 9, sem perda).

Ao mudar a imagem, **crie um nome novo** (`og-agu-v2.png`) e atualize `metadata.openGraph/twitter` em
`src/app/layout.tsx` e o teste `e2e/meta.spec.ts`; assim os caches de pré-visualização (WhatsApp,
Teams etc.) são quebrados.

## Ícones

    node scripts/gerar-icones.mjs

A partir de `public/icons/icon-512.png` (ícone **provisório**: "AGU" em branco sobre #0F3D75), gera:

- `public/icons/apple-touch-icon.png` (180x180, fundo opaco);
- `public/icons/icon-maskable-192.png` e `icon-maskable-512.png` (fundo azul cheio, conteúdo a 80% do
  lado, dentro da zona segura);
- `public/favicon.ico` (16, 32 e 48 px, PNGs embutidos).

`icon-192.png` e `icon-512.png` seguem como `any` e não são tocados. Para usar a arte oficial,
substitua `icon-512.png` (e `icon-192.png`), rode o script e suba `VERSAO` em `public/sw.js`.
