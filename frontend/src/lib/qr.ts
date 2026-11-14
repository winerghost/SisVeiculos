import qrcode from 'qrcode-generator';

/** Prefixo do conteúdo do QR dos veículos; o backend define o formato do código. */
const PREFIXO = 'AGU-VEICULO:';
const CODIGO = /^[a-z0-9]{16,32}$/;
const CONTEUDO = /^AGU-VEICULO:([a-z0-9]{16,32})$/;

/**
 * Extrai o código público do texto lido. Regex estrita: qualquer outro conteúdo (URL, JWT, JSON,
 * texto livre) devolve null. Só formato; quem decide se o veículo existe é a API.
 */
export function extrairCodigo(texto: string): string | null {
  return CONTEUDO.exec(texto.trim())?.[1] ?? null;
}

/** Valida o código digitado à mão (mesmo formato do backend); aceita o conteúdo completo do QR. */
export function normalizarCodigoManual(texto: string): string | null {
  const t = texto.trim().toLowerCase();
  if (CODIGO.test(t)) return t;
  return extrairCodigo(texto.trim());
}

/** Texto que vai dentro do QR impresso. */
export function conteudoQr(codigoPublico: string): string {
  return `${PREFIXO}${codigoPublico}`;
}

/** Gera o QR como caminho SVG (módulos escuros) e a dimensão da grade, sem serviço externo. */
export function gerarModulosQr(texto: string): { caminho: string; tamanho: number } {
  const qr = qrcode(0, 'M');
  qr.addData(texto);
  qr.make();
  const n = qr.getModuleCount();
  let caminho = '';
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      if (qr.isDark(y, x)) caminho += `M${x} ${y}h1v1h-1z`;
    }
  }
  return { caminho, tamanho: n };
}
