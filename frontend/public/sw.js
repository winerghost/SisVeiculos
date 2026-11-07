/* Service worker do Controle de veículos (AGU).
 *
 * - /_next/static (nome com hash): cache-first.
 * - Ativos sem hash (/icons, logo, manifest): stale-while-revalidate (servem do cache e atualizam).
 * - API (/api): network-first e NUNCA gravada em cache. Dados autenticados não são
 *   persistidos; sem rede, a resposta é um erro no formato da API. Sem escrita offline.
 * - Navegação (HTML estático, sem dados de usuário): network-first com cópia para uso offline.
 * - Requisições para outras origens (Supabase) não são interceptadas.
 */
const VERSAO = 'v2';
const CACHE_ASSETS = `assets-${VERSAO}`;
const CACHE_PAGINAS = `paginas-${VERSAO}`;

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((nomes) =>
        Promise.all(
          nomes
            .filter((n) => n !== CACHE_ASSETS && n !== CACHE_PAGINAS)
            .map((n) => caches.delete(n)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

const ehAssetVersionado = (url) => url.pathname.startsWith('/_next/static/');
const ehAssetRevalidavel = (url) =>
  url.pathname.startsWith('/icons/') ||
  url.pathname === '/logo-agu.webp' ||
  url.pathname === '/manifest.webmanifest';

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(req, { cache: 'no-store' }).catch(
        () =>
          new Response(
            JSON.stringify({
              erro: {
                codigo: 'sem_conexao',
                mensagem: 'Sem conexão com o servidor. Tente novamente.',
              },
            }),
            { status: 503, headers: { 'Content-Type': 'application/json' } },
          ),
      ),
    );
    return;
  }

  // Ativos com query string nunca vão para o cache.
  if (url.search === '' && ehAssetVersionado(url)) {
    event.respondWith(
      caches.match(req).then(
        (hit) =>
          hit ||
          fetch(req).then((resp) => {
            if (resp.ok) {
              const copia = resp.clone();
              caches.open(CACHE_ASSETS).then((c) => c.put(req, copia));
            }
            return resp;
          }),
      ),
    );
    return;
  }

  if (url.search === '' && ehAssetRevalidavel(url)) {
    event.respondWith(
      caches.open(CACHE_ASSETS).then((c) =>
        c.match(req).then((hit) => {
          const rede = fetch(req)
            .then((resp) => {
              if (resp.ok) c.put(req, resp.clone());
              return resp;
            })
            .catch(() => hit);
          return hit || rede;
        }),
      ),
    );
    return;
  }

  if (req.mode === 'navigate') {
    // Chave só com o caminho: a query (ex.: ?id=) nunca vai para o cache.
    const chave = url.origin + url.pathname;
    event.respondWith(
      fetch(req)
        .then((resp) => {
          if (resp.ok) {
            const copia = resp.clone();
            caches.open(CACHE_PAGINAS).then((c) => c.put(chave, copia));
          }
          return resp;
        })
        .catch(() => caches.match(chave).then((hit) => hit || caches.match(url.origin + '/'))),
    );
  }
});
