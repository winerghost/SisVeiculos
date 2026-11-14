'use client';

import * as React from 'react';
import { conteudoQr, gerarModulosQr } from '@/lib/qr';

const MARGEM = 4; // zona silenciosa exigida pelo padrão QR

/** QR do veículo em SVG, gerado no cliente a partir do código público (sem serviço externo). */
export function QrVeiculo({
  codigoPublico,
  descricao,
  className,
}: {
  codigoPublico: string;
  descricao: string;
  className?: string;
}) {
  const { caminho, tamanho } = React.useMemo(
    () => gerarModulosQr(conteudoQr(codigoPublico)),
    [codigoPublico],
  );
  const lado = tamanho + MARGEM * 2;
  return (
    <svg
      role="img"
      aria-label={`QR Code do veículo ${descricao}`}
      viewBox={`0 0 ${lado} ${lado}`}
      shapeRendering="crispEdges"
      className={className}
    >
      <rect width={lado} height={lado} fill="#fff" />
      <path transform={`translate(${MARGEM} ${MARGEM})`} d={caminho} fill="#000" />
    </svg>
  );
}

const escapar = (t: string) => t.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

/** Abre uma janela só com o QR (preto no branco) e aciona a impressão. Devolve false se bloqueada. */
export function imprimirQr(codigoPublico: string, descricao: string, placa: string | null) {
  const { caminho, tamanho } = gerarModulosQr(conteudoQr(codigoPublico));
  const lado = tamanho + MARGEM * 2;
  const html = `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>QR ${escapar(descricao)}</title>
<style>body{font-family:Inter,Arial,sans-serif;text-align:center;margin:2rem}svg{width:10cm;height:10cm}h1{font-size:1.4rem;margin:.5rem 0 0}p{margin:.25rem 0;color:#333}</style></head>
<body><svg viewBox="0 0 ${lado} ${lado}" shape-rendering="crispEdges" role="img" aria-label="QR Code"><rect width="${lado}" height="${lado}" fill="#fff"/><path transform="translate(${MARGEM} ${MARGEM})" d="${caminho}"/></svg>
<h1>${escapar(descricao)}</h1>${placa ? `<p>${escapar(placa)}</p>` : ''}<p>Use a opção Ler QR Code do app.</p>
<script>window.onload=function(){window.print()}</script></body></html>`;
  const w = window.open('', '_blank');
  if (!w) return false;
  w.document.open();
  w.document.write(html);
  w.document.close();
  return true;
}
