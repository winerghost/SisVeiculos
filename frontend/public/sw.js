/* Service worker do Controle de veículos (AGU).
 *
 * - /_next/static (nome com hash): cache-first.
 * - Ativos sem hash (/icons, logo, manifest): stale-while-revalidate (servem do cache e atualizam).
 * - API (/api): network-first e NUNCA gravada em cache. Dados autenticados não são
 *   persistidos; sem rede, a resposta é um erro no formato da API. Sem escrita offline.
 * - Navegação (HTML estático, sem dados de usuário): network-first com cópia para uso offline.
 * - Requisições para outras origens (Supabase) não são interceptadas.
 * - Web Push: o payload é genérico (sem dados pessoais); o clique só abre destino interno.
 */
const VERSAO = 'v4';
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
  url.pathname === '/favicon.ico' ||
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

// ---- Web Push -------------------------------------------------------------------------------

const TEXTO_PADRAO = 'Você tem uma nova notificação no sistema.';

/** Só aceita caminho interno da mesma origem (nunca URL externa). */
function destinoSeguro(destino) {
  if (typeof destino !== 'string' || !destino.startsWith('/')) return '/';
  if (destino.startsWith('//') || destino.startsWith('/\\')) return '/';
  try {
    const u = new URL(destino, self.location.origin);
    return u.origin === self.location.origin ? u.pathname + u.search + u.hash : '/';
  } catch {
    return '/';
  }
}

self.addEventListener('push', (event) => {
  let dados = {};
  try {
    dados = event.data ? event.data.json() : {};
  } catch {
    dados = {};
  }
  if (dados === null || typeof dados !== 'object') dados = {};
  const titulo =
    typeof dados.titulo === 'string' && dados.titulo ? dados.titulo : 'Controle de veículos';
  const texto = typeof dados.texto === 'string' && dados.texto ? dados.texto : TEXTO_PADRAO;
  const opcoes = {
    body: texto,
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
    data: { destino: typeof dados.destino === 'string' ? dados.destino : null },
  };
  if (typeof dados.tag === 'string' && dados.tag) opcoes.tag = dados.tag;
  event.waitUntil(self.registration.showNotification(titulo, opcoes));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const destino = destinoSeguro(event.notification.data && event.notification.data.destino);
  const alvo = new URL(destino, self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((janelas) => {
      const existente = janelas.find((j) => new URL(j.url).origin === self.location.origin);
      if (existente) {
        return existente
          .focus()
          .then((f) => (f || existente).navigate(alvo))
          .catch(() => self.clients.openWindow(alvo));
      }
      return self.clients.openWindow(alvo);
    }),
  );
});
