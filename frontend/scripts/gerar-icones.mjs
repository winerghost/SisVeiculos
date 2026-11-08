// Gera apple-touch-icon, ícones maskable e favicon.ico a partir do ícone provisório
// public/icons/icon-512.png (fundo #0F3D75 + "AGU"). Uso: node scripts/gerar-icones.mjs
// Substitua icon-512.png pela arte oficial e rode de novo para regerar tudo.
import { chromium } from '@playwright/test';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const raiz = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const origem = readFileSync(path.join(raiz, 'public/icons/icon-512.png')).toString('base64');
const FUNDO = '#0F3D75';

const navegador = await chromium.launch();

// Renderiza o ícone na escala (fração do lado) dada, centralizado sobre fundo azul cheio e opaco.
async function render(lado, escala) {
  const p = await navegador.newPage({ viewport: { width: lado, height: lado } });
  const img = Math.round(lado * escala);
  await p.setContent(
    `<body style="margin:0;width:${lado}px;height:${lado}px;background:${FUNDO};display:grid;place-items:center">` +
      `<img alt="" width="${img}" height="${img}" style="display:block" src="data:image/png;base64,${origem}"></body>`,
  );
  await p.waitForFunction(() => document.images[0].complete);
  const png = await p.screenshot({ type: 'png', omitBackground: false });
  await p.close();
  return png;
}

const salvar = (rel, buf) => {
  writeFileSync(path.join(raiz, 'public', rel), buf);
  console.log(`${rel}: ${buf.length} bytes`);
};

// Maskable: conteúdo a 80% (zona segura) do lado; o "AGU" do ícone ocupa ~74% do próprio lado.
// Escala 0.8 mantém o texto bem dentro do círculo seguro.
salvar('icons/icon-maskable-192.png', await render(192, 0.8));
salvar('icons/icon-maskable-512.png', await render(512, 0.8));
salvar('icons/apple-touch-icon.png', await render(180, 1));

// favicon.ico: contêiner ICO com PNGs embutidos (16, 32, 48).
const tamanhos = [16, 32, 48];
const pngs = [];
for (const t of tamanhos) pngs.push(await render(t, 1));
await navegador.close();

const cab = Buffer.alloc(6);
cab.writeUInt16LE(1, 2);
cab.writeUInt16LE(tamanhos.length, 4);
let deslocamento = 6 + 16 * tamanhos.length;
const entradas = tamanhos.map((t, i) => {
  const e = Buffer.alloc(16);
  e[0] = t;
  e[1] = t;
  e.writeUInt16LE(1, 4); // planos
  e.writeUInt16LE(32, 6); // bits por pixel
  e.writeUInt32LE(pngs[i].length, 8);
  e.writeUInt32LE(deslocamento, 12);
  deslocamento += pngs[i].length;
  return e;
});
salvar('favicon.ico', Buffer.concat([cab, ...entradas, ...pngs]));
