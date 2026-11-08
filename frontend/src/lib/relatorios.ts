import type { FiltroRelatorio, FormatoRelatorio } from './tipos';

// Só apresentação e montagem de parâmetros: o servidor valida e aplica qualquer regra.

export const FORMATOS: { valor: FormatoRelatorio; rotulo: string; mime: string }[] = [
  { valor: 'csv', rotulo: 'CSV', mime: 'text/csv' },
  {
    valor: 'xlsx',
    rotulo: 'XLSX',
    mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  },
];

/** Valor inicial de um filtro, vindo do padrão do catálogo (bool sempre "true"/"false"). */
export function padraoDe(f: FiltroRelatorio): string {
  if (f.tipo === 'bool') return f.padrao === true || f.padrao === 'true' ? 'true' : 'false';
  return f.padrao === null || f.padrao === undefined ? '' : String(f.padrao);
}

/** Valores dos filtros do catálogo: o que o usuário escolheu ou, na falta, o padrão. */
export function valoresEfetivos(
  filtros: FiltroRelatorio[],
  escolhidos: Record<string, string>,
): Record<string, string> {
  return Object.fromEntries(filtros.map((f) => [f.nome, escolhidos[f.nome] ?? padraoDe(f)]));
}

/** Query enviada à API: só os preenchidos. */
export function paraQuery(valores: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(valores).filter(([, v]) => v !== ''));
}

/** Corpo JSON do envio por e-mail: bool vai como booleano; os demais como texto. */
export function paraCorpo(
  filtros: FiltroRelatorio[],
  valores: Record<string, string>,
): Record<string, string | boolean> {
  const corpo: Record<string, string | boolean> = {};
  for (const f of filtros) {
    const v = valores[f.nome];
    if (v === undefined || v === '') continue;
    corpo[f.nome] = f.tipo === 'bool' ? v === 'true' : v;
  }
  return corpo;
}

const dataBr = (v: string) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : v;
};

/** Texto "Rótulo: valor" de cada filtro preenchido, usando os rótulos do catálogo. */
export function descreverFiltros(
  filtros: FiltroRelatorio[],
  valores: Record<string, unknown>,
): string[] {
  const partes: string[] = [];
  for (const f of filtros) {
    const bruto = valores[f.nome];
    if (bruto === undefined || bruto === null || bruto === '') continue;
    const v = String(bruto);
    let texto = v;
    if (f.tipo === 'bool') texto = v === 'true' ? 'Sim' : 'Não';
    else if (f.tipo === 'data') texto = dataBr(v);
    else texto = f.opcoes?.find((o) => o.valor === v)?.rotulo ?? v;
    partes.push(`${f.rotulo}: ${texto}`);
  }
  return partes;
}

/** Entrega o Blob ao usuário pelo navegador. Nada é guardado em cache ou storage. */
export function salvarArquivo(blob: Blob, nome: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = nome;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
