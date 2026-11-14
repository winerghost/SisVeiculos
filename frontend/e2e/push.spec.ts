import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { expect, test, type Page } from '@playwright/test';
import { mockarApi } from './mock-api';
import { mockarNotificacoes } from './mock-notificacoes';
import {
  CHAVE_PUBLICA,
  ENDPOINT,
  mockarApiPush,
  mockarNavegadorPush,
  type ApiPush,
  type NavegadorPush,
} from './mock-push';

const IPHONE_UA =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1';
const DESKTOP_UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';

async function abrir(page: Page, navegador: NavegadorPush = {}, api: ApiPush = {}) {
  await mockarApi(page);
  await mockarNotificacoes(page, { solicitante: [] });
  const nav = await mockarNavegadorPush(page, navegador);
  const push = await mockarApiPush(page, api);
  await page.goto('/login');
  await page.getByLabel('E-mail').fill('solicitante@exemplo.com');
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await expect(page.getByRole('link', { name: /^Notificações/ })).toBeVisible();
  // Carga real da página de notificações (o init script roda de novo e zera os contadores).
  await page.goto('/notificacoes');
  await expect(page.getByRole('heading', { name: 'Notificações', level: 1 })).toBeVisible();
  return { nav, push };
}

const cartao = (page: Page) => page.getByRole('heading', { name: 'Avisos no aparelho' });
const botaoAtivar = (page: Page) =>
  page.getByRole('button', { name: /Ativar avisos neste aparelho|Ativando/ });
const botaoDesativar = (page: Page) =>
  page.getByRole('button', { name: /Desativar avisos neste aparelho|Desativando/ });
const dialogo = (page: Page) => page.getByRole('dialog', { name: 'Ativar avisos neste aparelho?' });
// O anunciador de rotas do Next também é role=alert (vazio): só contam alertas com texto.
const alertas = (page: Page) => page.getByRole('alert').filter({ hasText: /\S/ });
const ativo = (page: Page) => page.getByText('Avisos ativos neste aparelho.');

async function ativarAteOFim(page: Page) {
  await botaoAtivar(page).click();
  await dialogo(page).getByRole('button', { name: 'Continuar' }).click();
}

for (const [nome, ajuste] of [
  ['mobile', {}],
  [
    'desktop',
    {
      viewport: { width: 1280, height: 800 },
      isMobile: false,
      hasTouch: false,
      userAgent: DESKTOP_UA,
    },
  ],
] as const) {
  test.describe(`ativação de push (${nome})`, () => {
    test.use(ajuste);

    test('recurso desligado (habilitado=false): nenhum componente e nenhum pedido', async ({
      page,
    }) => {
      const { nav, push } = await abrir(page, {}, { habilitado: false });
      await expect.poll(() => push.state.configs).toBeGreaterThan(0);
      await expect(cartao(page)).toHaveCount(0);
      await expect(botaoAtivar(page)).toHaveCount(0);
      expect((await nav.estado()).requestPermission).toBe(0);
      expect(push.posts).toHaveLength(0);
    });

    test('habilitado sem chave pública: nada é exibido', async ({ page }) => {
      const { push } = await abrir(page, {}, { chave_publica: null });
      await expect.poll(() => push.state.configs).toBeGreaterThan(0);
      await expect(cartao(page)).toHaveCount(0);
    });

    test('API recusa /push/config (recurso desligado no servidor): some sem ruído', async ({
      page,
    }) => {
      const { push } = await abrir(page, {}, { erroConfig: 404 });
      await expect.poll(() => push.state.configs).toBeGreaterThan(0);
      await expect(cartao(page)).toHaveCount(0);
      await expect(alertas(page)).toHaveCount(0);
    });

    test('fluxo feliz: permissão só após o clique, explicação antes, POST sem usuário e estado ativo', async ({
      page,
    }) => {
      const { nav, push } = await abrir(page);
      await expect(cartao(page)).toBeVisible();
      await expect(botaoAtivar(page)).toBeVisible();
      // Carregamento da página: nenhum pedido de permissão.
      expect((await nav.estado()).requestPermission).toBe(0);

      // Clique em "Ativar": só a explicação; ainda sem permissão.
      await botaoAtivar(page).click();
      await expect(dialogo(page)).toBeVisible();
      await expect(dialogo(page)).toContainText('Nenhum dado da viagem');
      await expect(dialogo(page)).toContainText('o navegador vai pedir sua permissão');
      expect((await nav.estado()).requestPermission).toBe(0);
      expect(push.posts).toHaveLength(0);

      // Confirmação na explicação: agora sim, 1 pedido.
      await dialogo(page).getByRole('button', { name: 'Continuar' }).click();
      await expect(ativo(page)).toBeVisible();
      const e = await nav.estado();
      expect(e.requestPermission).toBe(1);
      expect(e.subscribe).toBe(1);
      expect(e.subscribeOpcoes[0]).toEqual({ userVisibleOnly: true, chaveBytes: 65 });

      expect(push.posts).toHaveLength(1);
      // Contrato: só endpoint e keys; o usuário vem do JWT (cabeçalho), nunca do body.
      expect(push.posts[0]!.corpo).toEqual({
        endpoint: ENDPOINT,
        keys: { p256dh: 'p256dh-de-teste', auth: 'auth-de-teste' },
      });
      expect(push.posts[0]!.autorizacao).toBe('Bearer tok-solicitante');
      expect(JSON.stringify(push.posts[0]!.corpo)).not.toMatch(/usuario|user|email|perfil/i);
      await expect(botaoDesativar(page)).toBeVisible();
      await expect(botaoAtivar(page)).toHaveCount(0);

      // Layout: dentro da tela e com alvos de 44px.
      const largura = page.viewportSize()!.width;
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
        largura,
      );
      const caixa = await botaoDesativar(page).boundingBox();
      expect(caixa!.height).toBeGreaterThanOrEqual(44);
      expect(caixa!.x + caixa!.width).toBeLessThanOrEqual(largura);
    });

    test('"Voltar" na explicação não pede permissão nem chama a API', async ({ page }) => {
      const { nav, push } = await abrir(page);
      await botaoAtivar(page).click();
      await dialogo(page).getByRole('button', { name: 'Voltar' }).click();
      await expect(dialogo(page)).toBeHidden();
      await expect(botaoAtivar(page)).toBeVisible();
      expect((await nav.estado()).requestPermission).toBe(0);
      expect(push.posts).toHaveLength(0);
    });

    test('permissão negada no pedido: mensagem, sem insistir e sem POST', async ({ page }) => {
      const { nav, push } = await abrir(page, { resposta: 'denied' });
      await ativarAteOFim(page);
      await expect(page.getByText(/avisos estão bloqueados neste navegador/)).toBeVisible();
      await expect(botaoAtivar(page)).toHaveCount(0);
      await expect(alertas(page)).toHaveCount(0);
      const e = await nav.estado();
      expect(e.requestPermission).toBe(1);
      expect(e.subscribe).toBe(0);
      expect(push.posts).toHaveLength(0);
    });

    test('permissão já bloqueada ao carregar: orientação, sem pedir e sem POST', async ({
      page,
    }) => {
      const { nav, push } = await abrir(page, { permissao: 'denied' });
      await expect(page.getByText(/avisos estão bloqueados neste navegador/)).toBeVisible();
      await expect(botaoAtivar(page)).toHaveCount(0);
      expect((await nav.estado()).requestPermission).toBe(0);
      expect(push.posts).toHaveLength(0);
    });

    test('pedido dispensado (default): nada muda, sem POST e sem insistência', async ({ page }) => {
      const { nav, push } = await abrir(page, { resposta: 'default' });
      await ativarAteOFim(page);
      await expect.poll(async () => (await nav.estado()).requestPermission).toBe(1);
      await expect(botaoAtivar(page)).toBeVisible();
      await expect(botaoAtivar(page)).toBeEnabled();
      await expect(alertas(page)).toHaveCount(0);
      expect((await nav.estado()).subscribe).toBe(0);
      expect(push.posts).toHaveLength(0);
    });

    test('navegador sem suporte: mensagem e alternativa (sino), sem botão', async ({ page }) => {
      const { push } = await abrir(page, { suporte: false });
      await expect(cartao(page)).toBeVisible();
      await expect(page.getByText(/Este navegador não permite avisos no aparelho/)).toBeVisible();
      await expect(page.getByText(/no sino do sistema/)).toBeVisible();
      await expect(botaoAtivar(page)).toHaveCount(0);
      expect(push.posts).toHaveLength(0);
    });

    test('iOS fora da tela inicial: orientação de instalar e NENHUM pedido de permissão', async ({
      page,
    }) => {
      const { nav, push } = await abrir(page, { userAgent: IPHONE_UA, standalone: false });
      await expect(cartao(page)).toBeVisible();
      await expect(
        page.getByText(/só funcionam com o sistema instalado na Tela de Início/),
      ).toBeVisible();
      await expect(page.getByText('Adicionar à Tela de Início')).toBeVisible();
      await expect(botaoAtivar(page)).toHaveCount(0);
      const e = await nav.estado();
      expect(e.requestPermission).toBe(0);
      expect(e.subscribe).toBe(0);
      expect(push.posts).toHaveLength(0);
    });

    test('iOS com o PWA na tela inicial (standalone): oferece ativar', async ({ page }) => {
      const { nav } = await abrir(page, { userAgent: IPHONE_UA, standalone: true });
      await expect(botaoAtivar(page)).toBeVisible();
      await expect(page.getByText(/Tela de Início:/)).toHaveCount(0);
      expect((await nav.estado()).requestPermission).toBe(0);
    });

    test('desativar: DELETE com o endpoint e unsubscribe, volta ao estado inativo', async ({
      page,
    }) => {
      const { nav, push } = await abrir(page, { permissao: 'granted', jaInscrito: true });
      await expect(ativo(page)).toBeVisible();
      expect((await nav.estado()).requestPermission).toBe(0);

      await botaoDesativar(page).click();
      await expect(botaoAtivar(page)).toBeVisible();
      await expect(ativo(page)).toHaveCount(0);
      expect(push.deletes).toEqual([{ endpoint: ENDPOINT }]);
      expect((await nav.estado()).unsubscribe).toBe(1);
      // O único POST é o reenvio idempotente feito ao carregar a tela (aparelho já inscrito).
      expect(push.posts).toHaveLength(1);
    });

    test('aparelho já inscrito: ao carregar reenvia a inscrição (idempotente) sem pedir permissão', async ({
      page,
    }) => {
      const { nav, push } = await abrir(page, { permissao: 'granted', jaInscrito: true });
      await expect(ativo(page)).toBeVisible();
      expect(push.posts).toHaveLength(1);
      expect(push.posts[0]!.corpo).toEqual({
        endpoint: ENDPOINT,
        keys: { p256dh: 'p256dh-de-teste', auth: 'auth-de-teste' },
      });
      const e = await nav.estado();
      expect(e.requestPermission).toBe(0);
      expect(e.subscribe).toBe(0);
    });

    test('aparelho já inscrito e API recusa o reenvio: erro exibido, não afirma "ativo"', async ({
      page,
    }) => {
      await abrir(
        page,
        { permissao: 'granted', jaInscrito: true },
        {
          errosInscrever: [
            { status: 403, codigo: 'recurso_desligado', mensagem: 'Push desligado.' },
          ],
        },
      );
      await expect(alertas(page).filter({ hasText: 'Push desligado.' })).toBeVisible();
      await expect(ativo(page)).toHaveCount(0);
      await expect(botaoAtivar(page)).toBeVisible();
    });

    test('erro na desativação é exibido e permite tentar de novo', async ({ page }) => {
      const { nav, push } = await abrir(
        page,
        { permissao: 'granted', jaInscrito: true },
        { errosRevogar: [{ status: 500, codigo: 'erro_interno', mensagem: 'Falha ao revogar.' }] },
      );
      await botaoDesativar(page).click();
      await expect(page.getByRole('alert').filter({ hasText: 'Falha ao revogar.' })).toBeVisible();
      await expect(ativo(page)).toBeVisible();
      await expect(botaoDesativar(page)).toBeEnabled();
      // Falhou antes do unsubscribe local: a inscrição do aparelho segue intacta.
      expect((await nav.estado()).unsubscribe).toBe(0);

      await botaoDesativar(page).click();
      await expect(botaoAtivar(page)).toBeVisible();
      expect(push.deletes).toHaveLength(2);
      expect((await nav.estado()).unsubscribe).toBe(1);
    });

    for (const status of [400, 403, 500]) {
      test(`erro ${status} da API ao inscrever: mensagem exibida, spinner some e dá para tentar de novo`, async ({
        page,
      }) => {
        const mensagem = `Mensagem do servidor ${status}.`;
        const { nav, push } = await abrir(
          page,
          {},
          { errosInscrever: [{ status, codigo: 'erro_teste', mensagem }] },
        );
        await ativarAteOFim(page);
        await expect(page.getByRole('alert').filter({ hasText: mensagem })).toBeVisible();
        // Spinner some: botão habilitado e sem aria-busy; ainda inativo.
        await expect(botaoAtivar(page)).toBeEnabled();
        await expect(botaoAtivar(page)).not.toHaveAttribute('aria-busy', 'true');
        await expect(ativo(page)).toHaveCount(0);
        // A inscrição local é desfeita para não ficar órfã no navegador.
        expect((await nav.estado()).unsubscribe).toBe(1);
        expect(push.posts).toHaveLength(1);

        // Nova tentativa funciona e limpa o erro.
        await ativarAteOFim(page);
        await expect(ativo(page)).toBeVisible();
        await expect(alertas(page)).toHaveCount(0);
        expect(push.posts).toHaveLength(2);
      });
    }

    test('navegador sem keys na inscrição: erro claro, sem POST', async ({ page }) => {
      const { nav, push } = await abrir(page, { semKeys: true });
      await ativarAteOFim(page);
      await expect(
        page.getByRole('alert').filter({ hasText: /não devolveu os dados necessários/ }),
      ).toBeVisible();
      expect(push.posts).toHaveLength(0);
      expect((await nav.estado()).unsubscribe).toBe(1);
      await expect(botaoAtivar(page)).toBeEnabled();
    });

    test('clique duplo em "Continuar" gera um único POST e um único pedido de permissão', async ({
      page,
    }) => {
      const { nav, push } = await abrir(page, { atrasoPermissao: 150 }, { atrasoPost: 300 });
      await botaoAtivar(page).click();
      await dialogo(page).getByRole('button', { name: 'Continuar' }).dblclick();
      await expect(ativo(page)).toBeVisible();
      expect(push.posts).toHaveLength(1);
      expect((await nav.estado()).requestPermission).toBe(1);
      expect((await nav.estado()).subscribe).toBe(1);
    });

    test('dois cliques na mesma tarefa em "Continuar" também geram um único POST', async ({
      page,
    }) => {
      const { nav, push } = await abrir(page, { atrasoPermissao: 150 }, { atrasoPost: 300 });
      await botaoAtivar(page).click();
      await expect(dialogo(page)).toBeVisible();
      await dialogo(page)
        .getByRole('button', { name: 'Continuar' })
        .evaluate((el) => {
          (el as HTMLButtonElement).click();
          (el as HTMLButtonElement).click();
        });
      await expect(ativo(page)).toBeVisible();
      expect(push.posts).toHaveLength(1);
      expect((await nav.estado()).requestPermission).toBe(1);
    });

    test('durante a ativação o botão mostra "Ativando..." e fica desabilitado', async ({
      page,
    }) => {
      await abrir(page, {}, { atrasoPost: 600 });
      await ativarAteOFim(page);
      const b = page.getByRole('button', { name: 'Ativando...' });
      await expect(b).toBeDisabled();
      await expect(b).toHaveAttribute('aria-busy', 'true');
      await expect(ativo(page)).toBeVisible();
    });
  });
}

test('a chave pública da API é usada no subscribe (65 bytes) e não é gravada em storage', async ({
  page,
}) => {
  const { nav } = await abrir(page);
  await ativarAteOFim(page);
  await expect(ativo(page)).toBeVisible();
  expect((await nav.estado()).subscribeOpcoes[0]!.chaveBytes).toBe(65);
  const guardado = await page.evaluate(() =>
    JSON.stringify({ ...localStorage, ...sessionStorage }),
  );
  expect(guardado).not.toContain(CHAVE_PUBLICA);
  expect(guardado).not.toContain(ENDPOINT);
});

// ---------------------------------------------------------------------------------------------
// Service worker (public/sw.js): carregado num contexto simulado (sem navegador).
// ---------------------------------------------------------------------------------------------

const ORIGEM = 'https://sisv.exemplo.test';

type Manipulador = (evento: unknown) => void;

function carregarSw(janelas: { url: string }[] = []) {
  const manipuladores: Record<string, Manipulador> = {};
  const mostradas: { titulo: string; opcoes: Record<string, unknown> }[] = [];
  const abertas: string[] = [];
  const navegadas: string[] = [];
  const janelasFake = janelas.map((j) => ({
    url: j.url,
    focus: async () => ({
      navigate: async (alvo: string) => {
        navegadas.push(alvo);
      },
    }),
    navigate: async (alvo: string) => {
      navegadas.push(alvo);
    },
  }));
  const self = {
    location: { origin: ORIGEM },
    addEventListener: (tipo: string, fn: Manipulador) => {
      manipuladores[tipo] = fn;
    },
    skipWaiting: () => undefined,
    registration: {
      showNotification: async (titulo: string, opcoes: Record<string, unknown>) => {
        mostradas.push({ titulo, opcoes });
      },
    },
    clients: {
      claim: async () => undefined,
      matchAll: async () => janelasFake,
      openWindow: async (alvo: string) => {
        abertas.push(alvo);
        return null;
      },
    },
  };
  const codigo = readFileSync(resolve(process.cwd(), 'public/sw.js'), 'utf8');
  runInNewContext(codigo, { self, caches: {}, URL, Response, fetch: () => undefined });
  return { manipuladores, mostradas, abertas, navegadas };
}

async function dispararPush(
  sw: ReturnType<typeof carregarSw>,
  data: { json: () => unknown } | null,
) {
  const esperas: Promise<unknown>[] = [];
  sw.manipuladores['push']!({ data, waitUntil: (p: Promise<unknown>) => esperas.push(p) });
  await Promise.all(esperas);
}

async function dispararClique(sw: ReturnType<typeof carregarSw>, data: unknown) {
  const esperas: Promise<unknown>[] = [];
  let fechada = 0;
  sw.manipuladores['notificationclick']!({
    notification: { close: () => (fechada += 1), data },
    waitUntil: (p: Promise<unknown>) => esperas.push(p),
  });
  await Promise.all(esperas);
  return fechada;
}

test.describe('service worker: evento push', () => {
  test('registra os listeners de push e notificationclick', () => {
    const sw = carregarSw();
    expect(typeof sw.manipuladores['push']).toBe('function');
    expect(typeof sw.manipuladores['notificationclick']).toBe('function');
  });

  test('payload JSON gera showNotification com título, texto, tag e data.destino', async () => {
    const sw = carregarSw();
    await dispararPush(sw, {
      json: () => ({
        titulo: 'Sua requisição foi autorizada',
        texto: 'Abra o sistema para ver.',
        destino: '/requisicoes/detalhe?id=1',
        tag: 'notif-123',
      }),
    });
    expect(sw.mostradas).toHaveLength(1);
    const { titulo, opcoes } = sw.mostradas[0]!;
    expect(titulo).toBe('Sua requisição foi autorizada');
    expect(opcoes['body']).toBe('Abra o sistema para ver.');
    expect(opcoes['tag']).toBe('notif-123');
    expect(opcoes['data']).toEqual({ destino: '/requisicoes/detalhe?id=1' });
    expect(opcoes['icon']).toBe('/icons/icon-192.png');
  });

  test('sem tag no payload, a notificação não define tag', async () => {
    const sw = carregarSw();
    await dispararPush(sw, { json: () => ({ titulo: 'A', texto: 'B' }) });
    expect(sw.mostradas[0]!.opcoes).not.toHaveProperty('tag');
    expect(sw.mostradas[0]!.opcoes['data']).toEqual({ destino: null });
  });

  test('payload vazio (sem data) usa texto genérico sem lançar', async () => {
    const sw = carregarSw();
    await dispararPush(sw, null);
    expect(sw.mostradas).toHaveLength(1);
    expect(sw.mostradas[0]!.titulo).toBe('Controle de veículos');
    expect(sw.mostradas[0]!.opcoes['body']).toBe('Você tem uma nova notificação no sistema.');
    expect(sw.mostradas[0]!.opcoes['data']).toEqual({ destino: null });
  });

  test('payload inválido (json() lança) usa texto genérico sem lançar', async () => {
    const sw = carregarSw();
    await dispararPush(sw, {
      json: () => {
        throw new SyntaxError('Unexpected token');
      },
    });
    expect(sw.mostradas).toHaveLength(1);
    expect(sw.mostradas[0]!.opcoes['body']).toBe('Você tem uma nova notificação no sistema.');
  });

  for (const [nome, valor] of [
    ['null', null],
    ['string', 'texto solto'],
    ['número', 42],
    ['array', []],
  ] as const) {
    test(`JSON válido porém não-objeto (${nome}) usa texto genérico sem lançar`, async () => {
      const sw = carregarSw();
      await dispararPush(sw, { json: () => valor });
      expect(sw.mostradas).toHaveLength(1);
      expect(sw.mostradas[0]!.titulo).toBe('Controle de veículos');
      expect(sw.mostradas[0]!.opcoes['body']).toBe('Você tem uma nova notificação no sistema.');
    });
  }

  test('campos com tipo errado são ignorados (sem lançar)', async () => {
    const sw = carregarSw();
    await dispararPush(sw, {
      json: () => ({ titulo: 123, texto: { x: 1 }, destino: 5, tag: ['a'] }),
    });
    const { titulo, opcoes } = sw.mostradas[0]!;
    expect(titulo).toBe('Controle de veículos');
    expect(opcoes['body']).toBe('Você tem uma nova notificação no sistema.');
    expect(opcoes['data']).toEqual({ destino: null });
    expect(opcoes).not.toHaveProperty('tag');
  });
});

test.describe('service worker: notificationclick', () => {
  test('fecha a notificação e abre o destino interno na mesma origem', async () => {
    const sw = carregarSw();
    const fechadas = await dispararClique(sw, { destino: '/requisicoes/detalhe?id=7#linha' });
    expect(fechadas).toBe(1);
    expect(sw.abertas).toEqual([`${ORIGEM}/requisicoes/detalhe?id=7#linha`]);
  });

  for (const destino of [
    'https://outro.com',
    'https://outro.com/x',
    'http://outro.com',
    '//outro.com',
    '//outro.com/x',
    '/\\outro.com',
    'javascript:alert(1)',
    'requisicoes/detalhe',
    '',
    null,
    undefined,
    42,
  ]) {
    test(`rejeita destino ${JSON.stringify(destino) ?? 'undefined'} e cai na raiz`, async () => {
      const sw = carregarSw();
      await dispararClique(sw, { destino });
      expect(sw.abertas).toHaveLength(1);
      expect(sw.abertas[0]).toBe(`${ORIGEM}/`);
      expect(new URL(sw.abertas[0]!).origin).toBe(ORIGEM);
    });
  }

  test('sem data na notificação abre a raiz', async () => {
    const sw = carregarSw();
    await dispararClique(sw, null);
    expect(sw.abertas).toEqual([`${ORIGEM}/`]);
  });

  test('com janela do app aberta, foca e navega nela (sem abrir outra)', async () => {
    const sw = carregarSw([{ url: `${ORIGEM}/notificacoes` }]);
    await dispararClique(sw, { destino: '/requisicoes/detalhe?id=9' });
    expect(sw.navegadas).toEqual([`${ORIGEM}/requisicoes/detalhe?id=9`]);
    expect(sw.abertas).toHaveLength(0);
  });

  test('janela de outra origem não é reaproveitada', async () => {
    const sw = carregarSw([{ url: 'https://outro.com/x' }]);
    await dispararClique(sw, { destino: '/viagem' });
    expect(sw.navegadas).toHaveLength(0);
    expect(sw.abertas).toEqual([`${ORIGEM}/viagem`]);
  });

  test('destino externo com janela aberta navega só para a raiz da própria origem', async () => {
    const sw = carregarSw([{ url: `${ORIGEM}/notificacoes` }]);
    await dispararClique(sw, { destino: 'https://outro.com' });
    expect(sw.navegadas).toEqual([`${ORIGEM}/`]);
  });
});
