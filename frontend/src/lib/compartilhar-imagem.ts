/**
 * Geração local do PNG da requisição e saída (Web Share API, download ou cópia do link).
 *
 * Tudo acontece no aparelho: nada é enviado a terceiros. A única leitura de rede é a da fonte
 * Inter, auto-hospedada na mesma origem (`/_next/static/media`), para embuti-la no PNG.
 *
 * Por que `toBlob` e não `toPng` + `fetch(dataUrl)`: o CSP (`connect-src`) não libera `data:`.
 * O `toBlob` usa `canvas.toBlob`, sem fetch. A biblioteca é carregada por import dinâmico para não
 * pesar a carga inicial da tela.
 */

/** Teto de área do canvas (o iOS recusa acima de ~16,7 MP); com 540 px lógicos, sobra altura. */
const AREA_MAXIMA_PX = 16_000_000;
/** 540 px lógicos × 2 = 1080 px de largura: boa resolução para o WhatsApp. */
const PIXEL_RATIO_MAXIMO = 2;
const FAMILIA_FONTE = 'Inter Variable';
const TITULO_COMPARTILHAMENTO = 'Requisição de veículo';

let cssFontesPromessa: Promise<string> | null = null;
let aquecido = false;

const normalizarFamilia = (f: string) => f.trim().replace(/["']/g, '');

/** Verdadeiro se o `unicode-range` (ex.: "U+0-FF, U+131, U+4??") cobre o ponto de código. */
function cobre(intervalos: string, ponto: number): boolean {
  if (!intervalos.trim()) return true;
  return intervalos.split(',').some((parte) => {
    const t = parte.trim().replace(/^U\+/i, '');
    if (!t) return false;
    const [ini, fim] = t.includes('-')
      ? (t.split('-') as [string, string])
      : [t.replace(/\?/g, '0'), t.replace(/\?/g, 'F')];
    const a = parseInt(ini, 16);
    const b = parseInt(fim, 16);
    return Number.isFinite(a) && Number.isFinite(b) && ponto >= a && ponto <= b;
  });
}

function regrasFontFace(lista: CSSRuleList, saida: CSSFontFaceRule[]) {
  for (const regra of Array.from(lista)) {
    if (regra instanceof CSSFontFaceRule) saida.push(regra);
    else if ('cssRules' in regra) regrasFontFace((regra as CSSGroupingRule).cssRules, saida);
  }
}

async function paraDataUrl(url: string): Promise<string> {
  const resp = await fetch(url, { credentials: 'same-origin' });
  if (!resp.ok) throw new Error(`fonte ${resp.status}`);
  const blob = await resp.blob();
  return new Promise((resolve, reject) => {
    const leitor = new FileReader();
    leitor.onload = () => resolve(String(leitor.result));
    leitor.onerror = () => reject(leitor.error);
    leitor.readAsDataURL(blob);
  });
}

/**
 * CSS das faces da Inter só com os subsets `latin` e `latin-ext` (pt-BR), com o woff2 embutido.
 * O `getFontEmbedCSS` da biblioteca embutiria todos os subsets (cirílico, grego, vietnamita),
 * centenas de KB a mais por geração. Só lê folhas e fontes da mesma origem.
 */
function cssFontes(): Promise<string> {
  if (cssFontesPromessa) return cssFontesPromessa;
  cssFontesPromessa = (async () => {
    const faces: CSSFontFaceRule[] = [];
    for (const folha of Array.from(document.styleSheets)) {
      let regras: CSSRuleList;
      try {
        regras = folha.cssRules;
      } catch {
        continue; // folha de outra origem: ignorada
      }
      regrasFontFace(regras, faces);
    }
    const textos = await Promise.all(
      faces
        .filter((f) => normalizarFamilia(f.style.getPropertyValue('font-family')) === FAMILIA_FONTE)
        .filter((f) => {
          const r = f.style.getPropertyValue('unicode-range');
          // 'A' está em latin; U+0100 abre o latin-ext (vietnamita e cirílico não o cobrem).
          return cobre(r, 0x41) || cobre(r, 0x100);
        })
        .map(async (f) => {
          const base = f.parentStyleSheet?.href ?? window.location.href;
          let css = f.cssText;
          const urls = Array.from(css.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/g));
          for (const [trecho, bruto] of urls) {
            if (!bruto || bruto.startsWith('data:')) continue;
            const absoluta = new URL(bruto, base);
            if (absoluta.origin !== window.location.origin) return '';
            css = css.replace(trecho, `url("${await paraDataUrl(absoluta.href)}")`);
          }
          return css;
        }),
    );
    return textos.filter(Boolean).join('\n');
  })();
  cssFontesPromessa.catch(() => {
    cssFontesPromessa = null; // nova tentativa na próxima geração
  });
  return cssFontesPromessa;
}

/** PNG do nó (montado fora da tela, nunca `display:none`). Largura 1080 px com 540 px lógicos. */
export async function gerarBlob(no: HTMLElement): Promise<Blob> {
  const { toBlob } = await import('html-to-image');
  await document.fonts.ready;
  const fontEmbedCSS = await cssFontes();
  const largura = no.offsetWidth;
  const altura = no.offsetHeight;
  if (!largura || !altura) throw new Error('cartão sem dimensões');
  const pixelRatio = Math.min(PIXEL_RATIO_MAXIMO, Math.sqrt(AREA_MAXIMA_PX / (largura * altura)));
  const opcoes = {
    width: largura,
    height: altura,
    pixelRatio,
    fontEmbedCSS,
    cacheBust: false,
    backgroundColor: getComputedStyle(no).backgroundColor,
  };
  if (!aquecido) {
    // O Safari desenha a 1ª imagem antes de decodificar a fonte embutida: geração descartada,
    // pequena, uma vez por carga da página.
    await toBlob(no, { ...opcoes, pixelRatio: 0.25 }).catch(() => null);
    aquecido = true;
  }
  const blob = await toBlob(no, opcoes);
  if (!blob) throw new Error('falha ao gerar o PNG');
  return blob;
}

/** `requisicao-<8 primeiros do id>-<aaaammdd>.png`, com a data no fuso do sistema. */
export function nomeArquivo(id: string, agora: Date = new Date()): string {
  const dia = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Sao_Paulo',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
    .format(agora)
    .replace(/-/g, '');
  return `requisicao-${id.slice(0, 8)}-${dia}.png`;
}

export function baixar(blob: Blob, nome: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = nome;
  a.rel = 'noopener';
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Com atraso: o iOS abre a pré-visualização e ainda lê o blob depois do clique.
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/**
 * - `compartilhado`: a folha de compartilhamento foi concluída;
 * - `cancelado`: o usuário fechou a folha (tratado em silêncio);
 * - `bloqueado`: o navegador exigiu um gesto novo (`NotAllowedError`), nada foi baixado;
 * - `baixado_sem_suporte`: sem compartilhamento de arquivos, a imagem foi baixada.
 */
export type ResultadoCompartilhar =
  'compartilhado' | 'cancelado' | 'bloqueado' | 'baixado_sem_suporte';

export async function compartilharOuBaixar(
  blob: Blob,
  nome: string,
): Promise<ResultadoCompartilhar> {
  const arquivo = new File([blob], nome, { type: 'image/png' });
  const nav = navigator as Navigator & {
    canShare?: (dados: ShareData) => boolean;
    share?: (dados: ShareData) => Promise<void>;
  };
  const dados: ShareData = { files: [arquivo], title: TITULO_COMPARTILHAMENTO };
  let pode = false;
  try {
    pode =
      typeof nav.share === 'function' &&
      typeof nav.canShare === 'function' &&
      nav.canShare({ files: [arquivo] });
  } catch {
    pode = false;
  }
  if (pode) {
    try {
      await nav.share!(dados);
      return 'compartilhado';
    } catch (e) {
      const nomeErro = e instanceof DOMException || e instanceof Error ? e.name : '';
      if (nomeErro === 'AbortError') return 'cancelado';
      if (nomeErro === 'NotAllowedError') return 'bloqueado';
      // Outros erros do share (tipo de arquivo, falha interna): cai no download.
    }
  }
  baixar(blob, nome);
  return 'baixado_sem_suporte';
}

/** Copia o texto; usa a Clipboard API e, sem ela, o `execCommand` como reserva. */
export async function copiarLink(url: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(url);
      return true;
    }
  } catch {
    // segue para a reserva
  }
  try {
    const campo = document.createElement('textarea');
    campo.value = url;
    campo.setAttribute('readonly', '');
    campo.style.position = 'fixed';
    campo.style.left = '-10000px';
    document.body.appendChild(campo);
    campo.select();
    const ok = document.execCommand('copy');
    campo.remove();
    return ok;
  } catch {
    return false;
  }
}
