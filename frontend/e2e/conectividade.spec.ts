import { expect, test, type Page } from '@playwright/test';
import { mockarApi } from './mock-api';
import { mockarNotificacoes } from './mock-notificacoes';

// Indicador de conectividade (src/lib/conectividade.ts + components/indicador-conexao.tsx).
// O tempo da página é controlado por page.clock; a sonda GET /api/saude é uma API fake.

type Modo = 'ok' | '503' | '500' | 'abort' | 'pendente';

const DESKTOP_UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';

const T = {
  conectado: 'Conectado',
  semInternet: 'Sem conexão com a internet',
  servidor: 'Servidor indisponível',
  restabelecida: 'Conexão restabelecida',
};

async function abrir(page: Page, opcoes: { flag?: boolean | null; modo?: Modo } = {}) {
  const sonda = {
    modo: opcoes.modo ?? 'ok',
    chamadas: 0,
    emVoo: 0,
    maxEmVoo: 0,
    metodos: [] as string[],
  };
  const presos: (() => void)[] = [];
  const liberar = () => presos.splice(0).forEach((f) => f());
  // Pedido cancelado pela página (timeout): libera o handler preso e zera o "em voo".
  page.on('requestfailed', (r) => {
    if (r.url().includes('/api/saude')) liberar();
  });

  // Concorrência medida DENTRO da página (o relógio falso e a rede real têm ritmos diferentes).
  await page.addInitScript(() => {
    const w = window as unknown as { __saude: { emVoo: number; max: number } };
    w.__saude = { emVoo: 0, max: 0 };
    const original = window.fetch.bind(window);
    window.fetch = (entrada, init) => {
      const url =
        typeof entrada === 'string' ? entrada : entrada instanceof URL ? entrada.href : entrada.url;
      if (!url.includes('/api/saude')) return original(entrada, init);
      w.__saude.emVoo += 1;
      w.__saude.max = Math.max(w.__saude.max, w.__saude.emVoo);
      return original(entrada, init).finally(() => {
        w.__saude.emVoo -= 1;
      });
    };
  });
  await page.clock.install();
  await mockarApi(page, {
    conectividade: opcoes.flag === null ? undefined : (opcoes.flag ?? true),
  });
  await mockarNotificacoes(page, { solicitante: [] });
  await page.route('**/api/saude', async (route) => {
    sonda.chamadas += 1;
    sonda.metodos.push(route.request().method());
    sonda.emVoo += 1;
    sonda.maxEmVoo = Math.max(sonda.maxEmVoo, sonda.emVoo);
    try {
      switch (sonda.modo) {
        case 'ok':
          await route.fulfill({ json: { status: 'ok' } });
          break;
        case '503':
          await route.fulfill({ status: 503, json: { erro: { codigo: 'x', mensagem: 'x' } } });
          break;
        case '500':
          await route.fulfill({ status: 500, body: 'erro' });
          break;
        case 'abort':
          await route.abort('failed');
          break;
        case 'pendente':
          await new Promise<void>((ok) => presos.push(ok));
          await route.abort().catch(() => undefined);
          break;
      }
    } finally {
      sonda.emVoo -= 1;
    }
  });

  // Escritas feitas depois do login (o login do Supabase é um POST legítimo).
  const escritas: string[] = [];
  let contar = false;
  page.on('request', (r) => {
    if (contar && !['GET', 'HEAD', 'OPTIONS'].includes(r.method())) {
      escritas.push(`${r.method()} ${new URL(r.url()).pathname}`);
    }
  });

  await page.goto('/login');
  await page.getByLabel('E-mail').fill('solicitante@exemplo.com');
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await expect(page.getByRole('link', { name: /^Notificações/ })).toBeVisible();
  contar = true;
  return { sonda, escritas, liberar };
}

const indicador = (page: Page) =>
  page
    .getByRole('status')
    .filter({ hasText: /Conectado|Sem conexão|Servidor indisponível|restabelecida/ });
const mostra = (page: Page, texto: string) => expect(indicador(page)).toHaveText(texto);

const maxEmVooNaPagina = (page: Page) =>
  page.evaluate(() => (window as unknown as { __saude: { max: number } }).__saude.max);

/** Deixa a página terminar de processar a resposta da sonda (e agendar a próxima). */
async function assentar(page: Page) {
  await page.waitForTimeout(150);
  await page.evaluate(async () => {
    for (let i = 0; i < 10; i++) await Promise.resolve();
  });
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
  test.describe(`indicador de conectividade (${nome})`, () => {
    test.use(ajuste);

    test('flag desligada: indicador ausente e nenhuma sonda', async ({ page }) => {
      const { sonda } = await abrir(page, { flag: false });
      await assentar(page);
      await page.clock.runFor(120_000);
      await assentar(page);
      await expect(indicador(page)).toHaveCount(0);
      expect(sonda.chamadas).toBe(0);
    });

    test('flag ausente em eu.recursos: também não aparece nem sonda', async ({ page }) => {
      const { sonda } = await abrir(page, { flag: null });
      await assentar(page);
      await page.clock.runFor(60_000);
      await expect(indicador(page)).toHaveCount(0);
      expect(sonda.chamadas).toBe(0);
    });

    test('internet e /api/saude 200: "Conectado" com role=status, sem escrita', async ({
      page,
    }) => {
      const { sonda, escritas } = await abrir(page);
      await expect.poll(() => sonda.chamadas).toBe(1);
      await mostra(page, T.conectado);
      await expect(indicador(page)).toBeVisible();
      await expect(indicador(page)).toHaveAttribute('aria-live', 'polite');
      expect(sonda.metodos).toEqual(['GET']);
      expect(escritas).toEqual([]);
      // Texto legível: tamanho de fonte >= 14px e cor com contraste (não transparente).
      const estilo = await indicador(page).evaluate((el) => {
        const s = getComputedStyle(el);
        return { fonte: parseFloat(s.fontSize), cor: s.color, opacidade: s.opacity };
      });
      expect(estilo.fonte).toBeGreaterThanOrEqual(14);
      expect(estilo.opacidade).toBe('1');
      // Dentro da tela.
      const largura = page.viewportSize()!.width;
      const caixa = await indicador(page).boundingBox();
      expect(caixa!.x + caixa!.width).toBeLessThanOrEqual(largura);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
        largura,
      );
    });

    test('offline: "Sem conexão com a internet" sem sondar', async ({ page, context }) => {
      const { sonda, escritas } = await abrir(page);
      await expect.poll(() => sonda.chamadas).toBe(1);
      await assentar(page);
      await context.setOffline(true);
      await mostra(page, T.semInternet);
      await page.clock.runFor(120_000);
      await assentar(page);
      await mostra(page, T.semInternet);
      expect(sonda.chamadas).toBe(1);
      expect(escritas).toEqual([]);
      // Alvo de toque do "Tentar agora" >= 44px; clicar offline não sonda.
      const botao = page.getByRole('button', { name: 'Tentar agora' });
      expect((await botao.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      await botao.click();
      await assentar(page);
      expect(sonda.chamadas).toBe(1);
      await context.setOffline(false);
    });

    test('evento offline mostra o aviso sem sondar', async ({ page }) => {
      const { sonda } = await abrir(page);
      await expect.poll(() => sonda.chamadas).toBe(1);
      await assentar(page);
      await page.evaluate(() => window.dispatchEvent(new Event('offline')));
      await mostra(page, T.semInternet);
      await assentar(page);
      expect(sonda.chamadas).toBe(1);
    });

    for (const modo of ['503', '500', 'abort'] as const) {
      test(`Flask indisponível (${modo}) com navigator.onLine true: "Servidor indisponível"`, async ({
        page,
      }) => {
        const { sonda, escritas } = await abrir(page, { modo });
        await mostra(page, T.servidor);
        expect(await page.evaluate(() => navigator.onLine)).toBe(true);
        expect(sonda.chamadas).toBe(1);
        await expect(page.getByRole('button', { name: 'Tentar agora' })).toBeVisible();
        expect(escritas).toEqual([]);
      });
    }

    test('timeout: resposta que passa de 4 s vira "Servidor indisponível"', async ({ page }) => {
      const { sonda } = await abrir(page, { modo: 'pendente' });
      await expect.poll(() => sonda.chamadas).toBe(1);
      await assentar(page);
      await page.clock.runFor(3_000);
      await assentar(page);
      await mostra(page, T.conectado); // ainda dentro do prazo: nada mudou
      await page.clock.runFor(1_500);
      await mostra(page, T.servidor);
      // O pedido foi cancelado pela página: nada fica em voo.
      await expect.poll(() => sonda.emVoo).toBe(0);
    });

    test('recuperação: "Conexão restabelecida" e depois "Conectado" sem recarregar', async ({
      page,
    }) => {
      const { sonda, escritas } = await abrir(page, { modo: '503' });
      await mostra(page, T.servidor);
      await page.evaluate(() => ((window as unknown as { __marca: number }).__marca = 7));

      sonda.modo = 'ok';
      await page.getByRole('button', { name: 'Tentar agora' }).click();
      await mostra(page, T.restabelecida);
      await expect(page.getByRole('button', { name: 'Tentar agora' })).toHaveCount(0);
      await assentar(page);
      await page.clock.runFor(4_500);
      await mostra(page, T.conectado);
      expect(await page.evaluate(() => (window as unknown as { __marca: number }).__marca)).toBe(7);
      expect(escritas).toEqual([]);
    });

    test('recuperação de offline (evento online) volta a sondar e restabelece', async ({
      page,
      context,
    }) => {
      const { sonda } = await abrir(page);
      await expect.poll(() => sonda.chamadas).toBe(1);
      await assentar(page);
      await context.setOffline(true);
      await mostra(page, T.semInternet);
      await context.setOffline(false);
      await mostra(page, T.restabelecida);
      expect(sonda.chamadas).toBe(2);
      await assentar(page);
      await page.clock.runFor(4_500);
      await mostra(page, T.conectado);
    });

    test('nunca há duas sondas simultâneas (online/visibilidade repetidos com uma em voo)', async ({
      page,
    }) => {
      const { sonda, liberar } = await abrir(page, { modo: 'pendente' });
      await expect.poll(() => sonda.chamadas).toBe(1);
      await page.evaluate(() => {
        for (let i = 0; i < 5; i++) {
          window.dispatchEvent(new Event('online'));
          document.dispatchEvent(new Event('visibilitychange'));
        }
      });
      await assentar(page);
      expect(sonda.chamadas).toBe(1);
      expect(await maxEmVooNaPagina(page)).toBe(1);
      liberar();
      await assentar(page);
      // Timeout e novas rodadas ao longo de 2 minutos: jamais mais de uma em voo.
      sonda.modo = '503';
      await page.clock.runFor(120_000);
      await assentar(page);
      expect(await maxEmVooNaPagina(page)).toBe(1);
    });

    test('intervalo respeitado: 30 s quando ok, sem sondar a cada instante', async ({ page }) => {
      const { sonda } = await abrir(page);
      await expect.poll(() => sonda.chamadas).toBe(1);
      await assentar(page);
      await page.clock.runFor(28_000);
      await assentar(page);
      expect(sonda.chamadas).toBe(1);
      await page.clock.runFor(3_000);
      await expect.poll(() => sonda.chamadas).toBe(2);
      await assentar(page);
      await page.clock.runFor(10_000);
      await assentar(page);
      expect(sonda.chamadas).toBe(2);
    });

    test('intervalo respeitado: 10 s em falha', async ({ page }) => {
      const { sonda } = await abrir(page, { modo: '503' });
      await mostra(page, T.servidor);
      await assentar(page);
      await page.clock.runFor(8_500);
      await assentar(page);
      expect(sonda.chamadas).toBe(1);
      await page.clock.runFor(2_500);
      await expect.poll(() => sonda.chamadas).toBe(2);
    });

    test('aba oculta pausa a sonda; ao voltar a ficar visível sonda de imediato', async ({
      page,
    }) => {
      const { sonda } = await abrir(page);
      await expect.poll(() => sonda.chamadas).toBe(1);
      await assentar(page);
      const definirOculta = (oculta: boolean) =>
        page.evaluate((o) => {
          Object.defineProperty(document, 'hidden', { configurable: true, get: () => o });
          document.dispatchEvent(new Event('visibilitychange'));
        }, oculta);
      await definirOculta(true);
      await page.clock.runFor(180_000);
      await assentar(page);
      expect(sonda.chamadas).toBe(1);
      await definirOculta(false);
      await expect.poll(() => sonda.chamadas).toBe(2);
    });

    test('durante as quedas nenhuma escrita (POST/PUT/PATCH/DELETE) é disparada ou repetida', async ({
      page,
      context,
    }) => {
      const { sonda, escritas } = await abrir(page, { modo: '503' });
      await mostra(page, T.servidor);
      await page.clock.runFor(60_000);
      await context.setOffline(true);
      await mostra(page, T.semInternet);
      await page.clock.runFor(60_000);
      await context.setOffline(false);
      sonda.modo = 'ok';
      await mostra(page, T.restabelecida);
      await page.clock.runFor(90_000);
      await assentar(page);
      expect(escritas).toEqual([]);
      expect(new Set(sonda.metodos)).toEqual(new Set(['GET']));
    });
  });
}
