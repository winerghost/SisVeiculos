/**
 * Retorno depois do login (`/login?voltar=<caminho>`).
 *
 * O valor vem da URL e pode ser forjado por terceiros: só é usado depois de passar por
 * `caminhoInterno`, que aceita apenas caminhos do próprio site (evita open redirect).
 * Isto é navegação, não regra de negócio: quem pode ver a tela de destino continua sendo
 * decidido pela API.
 */

/** Destino padrão após o login quando não há `voltar` válido. */
export const DESTINO_PADRAO = '/requisicoes';

const TAMANHO_MAXIMO = 512;
// Controle C0/C1, DEL e qualquer espaço em branco (inclui \t, \n, \r, U+2028/2029, NBSP).
const PROIBIDOS = /[\s\u0000-\u001F\u007F-\u009F]/;
const BASE_FICTICIA = 'https://interno.invalid';

function inseguro(v: string): boolean {
  return (
    !v.startsWith('/') ||
    v.startsWith('//') ||
    v.includes('\\') ||
    PROIBIDOS.test(v) ||
    // Esquema explícito (ex.: "javascript:") antes de qualquer barra: nunca é caminho interno.
    /^[a-z][a-z0-9+.-]*:/i.test(v)
  );
}

/**
 * Devolve o próprio valor se for um caminho interno seguro (com query e hash preservados,
 * ex.: `/requisicoes/detalhe?id=...`); caso contrário, `null`.
 */
export function caminhoInterno(valor: unknown): string | null {
  if (typeof valor !== 'string' || valor.length === 0 || valor.length > TAMANHO_MAXIMO) {
    return null;
  }
  if (inseguro(valor)) return null;

  // Barras ou contrabarras codificadas (%2F, %5C) também não podem formar "//" ou "\".
  let decodificado: string;
  try {
    decodificado = decodeURIComponent(valor);
  } catch {
    return null;
  }
  if (inseguro(decodificado)) return null;

  // Conferência final pelo parser do navegador: tem de continuar na mesma origem.
  let url: URL;
  try {
    url = new URL(valor, BASE_FICTICIA);
  } catch {
    return null;
  }
  if (url.origin !== BASE_FICTICIA) return null;
  // Segmentos de ponto ('/.//x', '/a/..//x', '/%2e//x') resolvem para '//x': recusa.
  if (url.pathname.startsWith('//') || url.pathname.includes('\\')) return null;

  // Voltar para o próprio login criaria um laço.
  const caminho = url.pathname.replace(/\/+$/, '').toLowerCase();
  if (caminho === '/login' || caminho.startsWith('/login.')) return null;

  // Devolve a forma normalizada pelo parser, nunca o texto cru recebido.
  const normalizado = `${url.pathname}${url.search}${url.hash}`;
  return inseguro(normalizado) ? null : normalizado;
}

/** Destino após login (inclusive após o segundo fator): `voltar` validado ou o padrão. */
export function destinoAposLogin(busca: string): string {
  let voltar: string | null = null;
  try {
    voltar = new URLSearchParams(busca).get('voltar');
  } catch {
    voltar = null;
  }
  return caminhoInterno(voltar) ?? DESTINO_PADRAO;
}

/**
 * Telas de detalhe cujo retorno precisa da query: só o parâmetro `id` é levado ao login.
 * Nas demais (ex.: filtros de /relatorios), só o caminho, para não duplicar a query em
 * `/login?voltar=...` (logs de acesso, Cloudflare, histórico): minimização (LGPD).
 */
const ROTAS_COM_ID = new Set([
  '/requisicoes/detalhe',
  '/motorista/detalhe',
  '/admin/usuarios/detalhe',
]);

function buscaMinima(caminho: string, busca: string): string {
  const rota = caminho.replace(/\/+$/, '');
  if (!ROTAS_COM_ID.has(rota)) return '';
  let id: string | null = null;
  try {
    id = new URLSearchParams(busca).get('id');
  } catch {
    id = null;
  }
  return id ? `?${new URLSearchParams({ id }).toString()}` : '';
}

/** Endereço do login levando a tela atual como retorno, se ela for um caminho interno válido. */
export function urlDeLogin(caminho: string, busca: string): string {
  const destino = caminhoInterno(`${caminho}${buscaMinima(caminho, busca)}`);
  if (!destino || destino === '/' || destino === DESTINO_PADRAO) return '/login';
  return `/login?voltar=${encodeURIComponent(destino)}`;
}
