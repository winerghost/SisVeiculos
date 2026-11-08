// Gera public/og/og-agu-v1.png (1200x630) UMA vez. Uso: node scripts/gerar-og.mjs
// Renderiza um HTML local com o Playwright do projeto (sem rede) e otimiza o PNG sem dependências.
import { chromium } from '@playwright/test';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { deflateSync, inflateSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const raiz = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fonte = readFileSync(
  path.join(raiz, 'node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2'),
).toString('base64');
const logo = readFileSync(path.join(raiz, 'public/logo-agu.webp')).toString('base64');

const html = `<!doctype html><html><head><meta charset="utf-8"><style>
@font-face{font-family:'Inter';font-weight:100 900;src:url(data:font/woff2;base64,${fonte}) format('woff2')}
*{box-sizing:border-box;margin:0}
body{width:1200px;height:630px;background:#0F3D75;font-family:'Inter',sans-serif;color:#fff;display:flex;align-items:center;padding:0 90px;gap:70px}
.cartao{background:#F4F6F9;border-radius:16px;padding:60px 70px;flex:none;display:flex}
.cartao img{width:256px;height:152px;display:block}
h1{font-size:84px;font-weight:800;line-height:1.05;letter-spacing:-1px}
p{font-size:42px;font-weight:500;margin-top:28px;line-height:1.25;color:#DCE6F5}
</style></head><body>
<div class="cartao"><img alt="" src="data:image/webp;base64,${logo}"></div>
<div><h1>Controle de Veículos</h1><p>Saída e chegada de veículos da AGU</p></div>
</body></html>`;

const navegador = await chromium.launch();
const pagina = await navegador.newPage({ viewport: { width: 1200, height: 630 } });
await pagina.setContent(html);
await pagina.evaluate(() => document.fonts.ready);
const bruto = await pagina.screenshot({ type: 'png' });
await navegador.close();

const saida = otimizarPng(bruto);
mkdirSync(path.join(raiz, 'public/og'), { recursive: true });
writeFileSync(path.join(raiz, 'public/og/og-agu-v1.png'), saida);
console.log(`og-agu-v1.png: ${bruto.length} -> ${saida.length} bytes`);

// Recomprime o IDAT com deflate nível 9 (sem perda de pixels).
function otimizarPng(buf) {
  let o = 8;
  const antes = [];
  const idat = [];
  const depois = [];
  let viuIdat = false;
  while (o < buf.length) {
    const len = buf.readUInt32BE(o);
    const tipo = buf.toString('ascii', o + 4, o + 8);
    const dados = buf.subarray(o + 8, o + 8 + len);
    if (tipo === 'IDAT') {
      idat.push(dados);
      viuIdat = true;
    } else if (tipo === 'IHDR' || !viuIdat) antes.push([tipo, dados]);
    else depois.push([tipo, dados]);
    o += 12 + len;
  }
  const comprimido = deflateSync(inflateSync(Buffer.concat(idat)), { level: 9 });
  const fim = [...antes, ['IDAT', comprimido], ...depois];
  const partes = [buf.subarray(0, 8), ...fim.map(([t, d]) => chunk(t, d))];
  const novo = Buffer.concat(partes);
  return novo.length < buf.length ? novo : buf;
}
function chunk(tipo, dados) {
  const t = Buffer.from(tipo, 'ascii');
  const c = Buffer.alloc(4);
  c.writeUInt32BE(crc32(Buffer.concat([t, dados])));
  const l = Buffer.alloc(4);
  l.writeUInt32BE(dados.length);
  return Buffer.concat([l, t, dados, c]);
}
function crc32(b) {
  let c = ~0;
  for (const x of b) {
    c ^= x;
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}
