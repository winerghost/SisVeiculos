import type { Page, Route } from '@playwright/test';

// Mocks do Web Push: (1) navegador (Notification, PushManager, serviceWorker.ready) injetado antes
// da página; (2) API /api/push/*. Usado junto com mockarApi e mockarNotificacoes.

// Chave VAPID pública de exemplo (65 bytes em base64url); só precisa decodificar.
export const CHAVE_PUBLICA =
  'BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U';
export const ENDPOINT = 'https://fcm.googleapis.com/fcm/send/endpoint-de-teste';

export interface NavegadorPush {
  /** false remove Notification, PushManager e serviceWorker. */
  suporte?: boolean;
  permissao?: 'default' | 'granted' | 'denied';
  /** Resposta de Notification.requestPermission(). */
  resposta?: 'granted' | 'denied' | 'default';
  /** Atraso (ms) na resposta do requestPermission. */
  atrasoPermissao?: number;
  /** Já existe inscrição no aparelho (getSubscription devolve algo). */
  jaInscrito?: boolean;
  /** toJSON() sem keys. */
  semKeys?: boolean;
  userAgent?: string;
  standalone?: boolean;
}

export interface EstadoNavegadorPush {
  requestPermission: number;
  /** Havia <dialog open> no momento de cada requestPermission. */
  dialogoAbertoNoPedido: boolean[];
  subscribe: number;
  subscribeOpcoes: { userVisibleOnly: boolean; chaveBytes: number }[];
  getSubscription: number;
  unsubscribe: number;
}

export async function mockarNavegadorPush(page: Page, cfg: NavegadorPush = {}) {
  await page.addInitScript((c) => {
    const estado = {
      requestPermission: 0,
      dialogoAbertoNoPedido: [] as boolean[],
      subscribe: 0,
      subscribeOpcoes: [] as { userVisibleOnly: boolean; chaveBytes: number }[],
      getSubscription: 0,
      unsubscribe: 0,
    };
    (window as unknown as { __push: typeof estado }).__push = estado;

    if (c.userAgent) {
      Object.defineProperty(navigator, 'userAgent', { configurable: true, get: () => c.userAgent });
    }
    if (c.standalone !== undefined) {
      const original = window.matchMedia.bind(window);
      window.matchMedia = (q: string) =>
        q.includes('display-mode: standalone')
          ? ({
              matches: c.standalone,
              media: q,
              addEventListener() {},
              removeEventListener() {},
              addListener() {},
              removeListener() {},
              onchange: null,
              dispatchEvent: () => false,
            } as unknown as MediaQueryList)
          : original(q);
    }

    if (c.suporte === false) {
      // Remove as três APIs que detectarSuporte() consulta.
      delete (window as unknown as Record<string, unknown>)['Notification'];
      delete (window as unknown as Record<string, unknown>)['PushManager'];
      delete (Navigator.prototype as unknown as Record<string, unknown>)['serviceWorker'];
      return;
    }

    let permissao = c.permissao ?? 'default';
    let inscrito = c.jaInscrito ?? false;

    const inscricao = {
      endpoint: 'https://fcm.googleapis.com/fcm/send/endpoint-de-teste',
      toJSON() {
        return c.semKeys
          ? { endpoint: this.endpoint }
          : { endpoint: this.endpoint, keys: { p256dh: 'p256dh-de-teste', auth: 'auth-de-teste' } };
      },
      async unsubscribe() {
        estado.unsubscribe += 1;
        inscrito = false;
        return true;
      },
    };

    class FakeNotification {
      static get permission() {
        return permissao;
      }
      static async requestPermission() {
        estado.requestPermission += 1;
        estado.dialogoAbertoNoPedido.push(!!document.querySelector('dialog[open]'));
        if (c.atrasoPermissao) await new Promise((r) => setTimeout(r, c.atrasoPermissao));
        permissao = (c.resposta ?? 'granted') as typeof permissao;
        return permissao;
      }
    }
    Object.defineProperty(window, 'Notification', { configurable: true, value: FakeNotification });
    Object.defineProperty(window, 'PushManager', { configurable: true, value: function () {} });

    const pushManager = {
      async getSubscription() {
        estado.getSubscription += 1;
        return inscrito ? inscricao : null;
      },
      async subscribe(op: { userVisibleOnly: boolean; applicationServerKey: Uint8Array }) {
        estado.subscribe += 1;
        estado.subscribeOpcoes.push({
          userVisibleOnly: op.userVisibleOnly,
          chaveBytes: op.applicationServerKey.byteLength,
        });
        inscrito = true;
        return inscricao;
      },
    };
    const registro = { pushManager };
    const sw = {
      ready: Promise.resolve(registro),
      register: async () => registro,
      getRegistration: async () => registro,
      addEventListener() {},
      removeEventListener() {},
      controller: null,
    };
    Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: sw });
  }, cfg);

  return {
    estado: () =>
      page.evaluate(() => (window as unknown as { __push: EstadoNavegadorPush }).__push),
  };
}

export interface ApiPush {
  habilitado?: boolean;
  chave_publica?: string | null;
  /** GET /push/config falha com este status. */
  erroConfig?: number;
  /** POST /push/inscricoes responde com erro (uma vez por item; depois sucesso). */
  errosInscrever?: { status: number; codigo: string; mensagem: string }[];
  errosRevogar?: { status: number; codigo: string; mensagem: string }[];
  /** Atraso (ms) do POST, para provocar clique duplo. */
  atrasoPost?: number;
}

export async function mockarApiPush(page: Page, cfg: ApiPush = {}) {
  const posts: { corpo: Record<string, unknown>; autorizacao: string }[] = [];
  const deletes: Record<string, unknown>[] = [];
  const state = { configs: 0 };
  const errosIns = [...(cfg.errosInscrever ?? [])];
  const errosRev = [...(cfg.errosRevogar ?? [])];

  const erro = (route: Route, e: { status: number; codigo: string; mensagem: string }) =>
    route.fulfill({ status: e.status, json: { erro: { codigo: e.codigo, mensagem: e.mensagem } } });

  await page.route('**/api/push/config', async (route) => {
    state.configs += 1;
    if (cfg.erroConfig) {
      return erro(route, {
        status: cfg.erroConfig,
        codigo: 'recurso_desligado',
        mensagem: 'Recurso desligado.',
      });
    }
    await route.fulfill({
      json: {
        habilitado: cfg.habilitado ?? true,
        chave_publica: cfg.chave_publica === undefined ? CHAVE_PUBLICA : cfg.chave_publica,
      },
    });
  });

  await page.route('**/api/push/inscricoes', async (route) => {
    const req = route.request();
    if (req.method() === 'POST') {
      posts.push({
        corpo: req.postDataJSON() as Record<string, unknown>,
        autorizacao: req.headers()['authorization'] ?? '',
      });
      if (cfg.atrasoPost) await new Promise((r) => setTimeout(r, cfg.atrasoPost));
      const e = errosIns.shift();
      if (e) return erro(route, e);
      return route.fulfill({ status: 201, json: { ok: true } });
    }
    if (req.method() === 'DELETE') {
      deletes.push(req.postDataJSON() as Record<string, unknown>);
      const e = errosRev.shift();
      if (e) return erro(route, e);
      return route.fulfill({ status: 204 });
    }
    return route.fallback();
  });

  return { posts, deletes, state };
}
