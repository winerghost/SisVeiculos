// Gera docs/limpeza-geral-producao.pdf a partir de docs/limpeza-geral-producao.html.
// Uso (em frontend/): node scripts/gerar-pdf-limpeza.mjs
// Usa o Playwright do projeto (Chromium local, sem rede) e a Inter local de @fontsource-variable/inter
// (o HTML a referencia por caminho relativo). Nenhuma dependência nova.
import { chromium } from '@playwright/test';
import { existsSync, statSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const raiz = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const origem = path.join(raiz, 'docs', 'limpeza-geral-producao.html');
const destino = path.join(raiz, 'docs', 'limpeza-geral-producao.pdf');
const fonte = path.join(
  raiz,
  'frontend/node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2',
);
if (!existsSync(fonte)) {
  console.error('Fonte Inter não encontrada: rode npm ci em frontend/.');
  process.exit(1);
}

const rodape = `<div style="width:100%;font-family:'Segoe UI',Arial,sans-serif;font-size:8pt;color:#3A4757;
  padding:0 18mm;display:flex;justify-content:space-between">
  <span>Limpeza geral antes da produção · Controle de Veículos (AGU)</span>
  <span>Página <span class="pageNumber"></span> de <span class="totalPages"></span></span></div>`;

const navegador = await chromium.launch();
try {
  const pagina = await navegador.newPage();
  // Bloqueia qualquer acesso fora de file:// (o guia é autocontido).
  await pagina.route(/^(?!file:)/, (r) => r.abort());
  await pagina.goto(pathToFileURL(origem).href, { waitUntil: 'load' });
  await pagina.evaluate(() => document.fonts.ready);
  const interOk = await pagina.evaluate(() => document.fonts.check("12px 'Inter'"));
  if (!interOk) throw new Error('A fonte Inter não carregou.');
  await pagina.pdf({
    path: destino,
    format: 'A4',
    printBackground: true,
    preferCSSPageSize: true,
    displayHeaderFooter: true,
    headerTemplate: '<span></span>',
    footerTemplate: rodape,
    tagged: true,
    outline: true,
  });
} finally {
  await navegador.close();
}
console.log(`limpeza-geral-producao.pdf: ${statSync(destino).size} bytes`);
