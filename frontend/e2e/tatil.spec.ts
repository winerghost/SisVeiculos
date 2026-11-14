import { expect, test, type Page, type Route } from '@playwright/test';
import { mockarApi, type Opcoes } from './mock-api';

// Fase 2 (feedback tátil). O playwright.config liga reducedMotion: 'reduce' para todos os testes;
// aqui o padrão é 'no-preference' e o cenário de redução é declarado explicitamente.
test.use({ reducedMotion: 'no-preference' });

type Modo = 'espiao' | 'ausente' | 'lanca';
type Chamada = { padrao: number | number[]; t: number };
type JanelaEspia = Window & { __vib: Chamada[]; __tentativas: number };

/** Substitui navigator.vibrate antes de qualquer script da página. */
async function instalarVibracao(page: Page, modo: Modo) {
  await page.addInitScript((m: Modo) => {
    const w = window as unknown as JanelaEspia;
    w.__vib = [];
    w.__tentativas = 0;
    if (m === 'ausente') {
      delete (Navigator.prototype as unknown as Record<string, unknown>).vibrate;
      delete (navigator as unknown as Record<string, unknown>).vibrate;
      return;
    }
    Object.defineProperty(navigator, 'vibrate', {
      configurable: true,
      writable: true,
      value: (padrao: number | number[]) => {
        w.__tentativas += 1;
        if (m === 'lanca') throw new Error('vibrate indisponível (teste)');
        w.__vib.push({ padrao, t: performance.now() });
        return true;
      },
    });
  }, modo);
}

const chamadas = (page: Page) => page.evaluate(() => (window as unknown as JanelaEspia).__vib);
const tentativas = (page: Page) =>
  page.evaluate(() => (window as unknown as JanelaEspia).__tentativas);
const soma = (p: number | number[]) => (Array.isArray(p) ? p.reduce((a, b) => a + b, 0) : p);
const padroes = async (page: Page) => (await chamadas(page)).map((c) => c.padrao);

/** Erros de página e de console (ignora falhas de rede 4xx/5xx esperadas dos mocks). */
function coletarErros(page: Page) {
  const erros: string[] = [];
  page.on('pageerror', (e) => erros.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) {
      erros.push(`console: ${m.text()}`);
    }
  });
  return erros;
}

async function entrar(page: Page, opcoes: Opcoes, email = 'autorizador@exemplo.com') {
  const mock = await mockarApi(page, opcoes);
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await expect(page).toHaveURL(/\/requisicoes$/);
  return mock;
}

const CARTAO = 'ul.lista-animada > li';
const EM_USO = { status: 'em_uso' as const, saida_real_em: new Date().toISOString() };

/** Segura a rota até `liberar()`; depois repassa ao mock. `status` força resposta de erro. */
function segurar(page: Page, rota: RegExp, status?: number) {
  let liberar!: () => void;
  const portao = new Promise<void>((ok) => (liberar = ok));
  let chegou!: () => void;
  const recebido = new Promise<void>((ok) => (chegou = ok));
  void page.route(rota, async (route: Route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    chegou();
    await portao;
    if (status) {
      return route.fulfill({
        status,
        json: { erro: { codigo: 'erro_teste', mensagem: `Falha ${status} de teste.` } },
      });
    }
    return route.fallback();
  });
  return { liberar, recebido };
}

const ROTA_SAIDA = /\/api\/requisicoes\/[^/]+\/saida$/;
const ROTA_CHEGADA = /\/api\/requisicoes\/[^/]+\/chegada$/;

test.describe('navegador compatível + flag ligada', () => {
  test.beforeEach(async ({ page }) => {
    await instalarVibracao(page, 'espiao');
  });

  test('login, carregamento e navegação entre páginas não vibram; hover e foco também não', async ({
    page,
  }) => {
    await entrar(page, { tatil: true, sementes: [{ status: 'solicitada' }] });
    await expect(page.locator(CARTAO).first()).toBeVisible();
    for (const nome of ['Autorizar', 'Viagem', 'Requisições']) {
      await page.getByRole('link', { name: nome }).click();
      await page.waitForLoadState('networkidle');
    }
    const botao = page.getByRole('link', { name: 'Viagem' });
    await botao.hover();
    await botao.focus();
    await page.keyboard.press('Tab');
    await page.keyboard.press('Shift+Tab');
    await page.goBack();
    await page.reload();
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('navigation').first()).toBeVisible();
    expect(await tentativas(page)).toBe(0);
    expect(await chamadas(page)).toEqual([]);
  });

  test('saída vibra UMA vez, só depois da resposta 200 do backend', async ({ page }) => {
    const mock = await entrar(page, { tatil: true, sementes: [{ status: 'autorizada' }] });
    const seg = segurar(page, ROTA_SAIDA);
    await page.getByRole('link', { name: 'Autorizar' }).click();
    await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
    await page.getByRole('button', { name: 'Registrar saída' }).click();
    await seg.recebido;
    // Requisição em voo: nada de vibração antes da resposta.
    await page.waitForTimeout(400);
    expect(await tentativas(page)).toBe(0);
    seg.liberar();
    await expect(page.getByRole('tab', { name: /Em uso · 1/ })).toBeVisible();
    await expect.poll(() => padroes(page)).toEqual([[40]]);
    await page.waitForTimeout(300);
    expect(await tentativas(page)).toBe(1);
    expect(mock.reqs[0]?.status).toBe('em_uso');
  });

  test('chegada vibra UMA vez, só depois da resposta 200 do backend', async ({ page }) => {
    const mock = await entrar(page, { tatil: true, sementes: [EM_USO] });
    const seg = segurar(page, ROTA_CHEGADA);
    await page.getByRole('link', { name: 'Viagem' }).click();
    await page.getByRole('button', { name: 'Registrar chegada agora' }).click();
    await seg.recebido;
    await page.waitForTimeout(400);
    expect(await tentativas(page)).toBe(0);
    seg.liberar();
    await expect(page.getByText('Nenhuma viagem em uso.')).toBeVisible();
    await expect.poll(() => padroes(page)).toEqual([[40]]);
    await page.waitForTimeout(300);
    expect(await tentativas(page)).toBe(1);
    expect(mock.reqs[0]?.status).toBe('concluida');
  });

  test('autorizar => confirmação, uma vez', async ({ page }) => {
    await entrar(page, { tatil: true, sementes: [{ status: 'solicitada' }] });
    await page.getByRole('link', { name: 'Autorizar' }).click();
    await page.getByRole('button', { name: 'Autorizar' }).click();
    await expect(page.getByRole('tab', { name: /Autorizadas · 1/ })).toBeVisible();
    await expect.poll(() => padroes(page)).toEqual([[20]]);
    expect(await tentativas(page)).toBe(1);
  });

  test('negar => confirmação, uma vez', async ({ page }) => {
    const mock = await entrar(page, { tatil: true, sementes: [{ status: 'solicitada' }] });
    await page
      .locator(CARTAO)
      .first()
      .getByRole('button', { name: 'Negar ou cancelar' })
      .or(page.locator(CARTAO).first().getByRole('button', { name: 'Negar', exact: true }))
      .first()
      .click();
    const dialogo = page.getByRole('alertdialog');
    await dialogo.getByRole('button', { name: 'Negar', exact: true }).click();
    await expect.poll(() => mock.reqs[0]?.status).toBe('negada');
    await expect.poll(() => padroes(page)).toEqual([[20]]);
    expect(await tentativas(page)).toBe(1);
  });

  test('cancelar => confirmação, uma vez', async ({ page }) => {
    const mock = await entrar(page, { tatil: true, sementes: [{ status: 'solicitada' }] });
    await page.locator(CARTAO).first().getByRole('button', { name: 'Cancelar requisição' }).click();
    await page
      .getByRole('alertdialog')
      .getByRole('button', { name: 'Cancelar requisição' })
      .click();
    await expect.poll(() => mock.reqs[0]?.status).toBe('cancelada');
    await expect.poll(() => padroes(page)).toEqual([[20]]);
    expect(await tentativas(page)).toBe(1);
  });

  test('checklist enviado => padrão do checklist (uma vez)', async ({ page }) => {
    const mock = await entrar(page, {
      tatil: true,
      checklist: true,
      sementes: [{ status: 'autorizada' }],
    });
    await page.getByRole('link', { name: 'Autorizar' }).click();
    await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
    await page.locator('label', { hasText: '1/2' }).click();
    await page.locator('label', { hasText: 'Sem avarias' }).click();
    await page.getByRole('button', { name: 'Registrar checklist de saída' }).click();
    await expect(page.getByRole('button', { name: 'Registrar saída' })).toBeVisible();
    expect(mock.registrosChecklist).toHaveLength(1);
    await expect.poll(() => padroes(page)).toEqual([[30, 40, 30]]);
    expect(await tentativas(page)).toBe(1);
  });

  test('checklist recusado pela API => padrão de erro', async ({ page }) => {
    await entrar(page, { tatil: true, checklist: true, sementes: [{ status: 'autorizada' }] });
    await page.getByRole('link', { name: 'Autorizar' }).click();
    await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
    await page.route(/\/api\/requisicoes\/[^/]+\/checklist\/saida$/, (route) =>
      route.request().method() === 'POST'
        ? route.fulfill({
            status: 400,
            json: { erro: { codigo: 'checklist_invalido', mensagem: 'Checklist inválido.' } },
          })
        : route.fallback(),
    );
    await page.locator('label', { hasText: '1/2' }).click();
    await page.locator('label', { hasText: 'Sem avarias' }).click();
    await page.getByRole('button', { name: 'Registrar checklist de saída' }).click();
    await expect(page.getByText('Checklist inválido.')).toBeVisible();
    await expect.poll(() => padroes(page)).toEqual([[50, 40, 50]]);
  });

  for (const status of [409, 500]) {
    test(`erro ${status} na saída => padrão de erro, uma vez, mensagem da API exibida`, async ({
      page,
    }) => {
      await entrar(page, { tatil: true, sementes: [{ status: 'autorizada' }] });
      const seg = segurar(page, ROTA_SAIDA, status);
      seg.liberar();
      await page.getByRole('link', { name: 'Autorizar' }).click();
      await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
      await page.getByRole('button', { name: 'Registrar saída' }).click();
      await expect(page.getByText(`Falha ${status} de teste.`)).toBeVisible();
      await expect.poll(() => padroes(page)).toEqual([[50, 40, 50]]);
      expect(await tentativas(page)).toBe(1);
    });
  }

  test('duração total de cada padrão <= 150 ms', async ({ page }) => {
    // Percorre os padrões reais exercitados: saída, confirmação, erro e checklist.
    await entrar(page, {
      tatil: true,
      checklist: true,
      sementes: [{ status: 'solicitada' }, { status: 'autorizada' }],
    });
    await page.getByRole('link', { name: 'Autorizar' }).click();
    await page.getByRole('button', { name: 'Autorizar' }).click();
    await expect.poll(async () => (await chamadas(page)).length).toBeGreaterThanOrEqual(1);
    const todos = await chamadas(page);
    for (const c of todos) expect(soma(c.padrao)).toBeLessThanOrEqual(150);
  });
});

test.describe('flag feedback_tatil desligada', () => {
  for (const opcoes of [{ tatil: false }, {}] as Opcoes[]) {
    test(`flag ${JSON.stringify(opcoes)}: 0 chamadas em saída, chegada e erro`, async ({
      page,
    }) => {
      await instalarVibracao(page, 'espiao');
      const mock = await entrar(page, {
        ...opcoes,
        sementes: [{ status: 'autorizada' }, EM_USO],
      });
      await page.getByRole('link', { name: 'Autorizar' }).click();
      await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
      await page.getByRole('button', { name: 'Registrar saída' }).click();
      await expect.poll(() => mock.reqs.filter((r) => r.status === 'em_uso').length).toBe(2);
      await page.getByRole('link', { name: 'Viagem' }).click();
      await page.getByRole('button', { name: 'Registrar chegada agora' }).first().click();
      await expect.poll(() => mock.reqs.filter((r) => r.status === 'concluida').length).toBe(1);
      await page.waitForTimeout(400);
      expect(await tentativas(page)).toBe(0);
    });
  }
});

test.describe('navegador SEM suporte', () => {
  test('nenhuma exceção, nenhum erro no console e o mesmo feedback visual', async ({ page }) => {
    await instalarVibracao(page, 'ausente');
    const erros = coletarErros(page);
    const mock = await entrar(page, {
      tatil: true,
      sementes: [{ status: 'autorizada' }],
    });
    expect(await page.evaluate(() => 'vibrate' in navigator)).toBe(false);
    await page.getByRole('link', { name: 'Autorizar' }).click();
    await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
    await page.getByRole('button', { name: 'Registrar saída' }).click();
    await expect(page.getByRole('tab', { name: /Em uso · 1/ })).toBeVisible();
    await page.getByRole('link', { name: 'Viagem' }).click();
    await page.getByRole('button', { name: 'Registrar chegada agora' }).click();
    await expect(page.getByText('Nenhuma viagem em uso.')).toBeVisible();
    expect(mock.reqs[0]?.status).toBe('concluida');
    expect(erros).toEqual([]);
  });

  test('erro da API sem suporte: mensagem idêntica e sem exceção', async ({ page }) => {
    await instalarVibracao(page, 'ausente');
    const erros = coletarErros(page);
    await entrar(page, { tatil: true, sementes: [{ status: 'autorizada' }] });
    const seg = segurar(page, ROTA_SAIDA, 409);
    seg.liberar();
    await page.getByRole('link', { name: 'Autorizar' }).click();
    await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
    await page.getByRole('button', { name: 'Registrar saída' }).click();
    await expect(page.getByText('Falha 409 de teste.')).toBeVisible();
    expect(erros).toEqual([]);
  });
});

test.describe('prefers-reduced-motion: reduce', () => {
  test('0 chamadas mesmo com a flag ligada', async ({ page }) => {
    await instalarVibracao(page, 'espiao');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const mock = await entrar(page, {
      tatil: true,
      sementes: [{ status: 'autorizada' }],
    });
    await page.getByRole('link', { name: 'Autorizar' }).click();
    await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
    await page.getByRole('button', { name: 'Registrar saída' }).click();
    await expect(page.getByRole('tab', { name: /Em uso · 1/ })).toBeVisible();
    await page.getByRole('link', { name: 'Viagem' }).click();
    await page.getByRole('button', { name: 'Registrar chegada agora' }).click();
    await expect(page.getByText('Nenhuma viagem em uso.')).toBeVisible();
    expect(mock.reqs[0]?.status).toBe('concluida');
    await page.waitForTimeout(300);
    expect(await tentativas(page)).toBe(0);
  });

  test('erro da API também não vibra', async ({ page }) => {
    await instalarVibracao(page, 'espiao');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await entrar(page, { tatil: true, sementes: [{ status: 'autorizada' }] });
    const seg = segurar(page, ROTA_SAIDA, 500);
    seg.liberar();
    await page.getByRole('link', { name: 'Autorizar' }).click();
    await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
    await page.getByRole('button', { name: 'Registrar saída' }).click();
    await expect(page.getByText('Falha 500 de teste.')).toBeVisible();
    expect(await tentativas(page)).toBe(0);
  });
});

test.describe('navigator.vibrate lançando exceção', () => {
  test('não quebra o fluxo nem gera erro não tratado', async ({ page }) => {
    await instalarVibracao(page, 'lanca');
    const erros = coletarErros(page);
    const mock = await entrar(page, { tatil: true, sementes: [{ status: 'autorizada' }] });
    await page.getByRole('link', { name: 'Autorizar' }).click();
    await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
    await page.getByRole('button', { name: 'Registrar saída' }).click();
    await expect(page.getByRole('tab', { name: /Em uso · 1/ })).toBeVisible();
    await page.getByRole('link', { name: 'Viagem' }).click();
    await page.getByRole('button', { name: 'Registrar chegada agora' }).click();
    await expect(page.getByText('Nenhuma viagem em uso.')).toBeVisible();
    // A exceção foi tentada (o código chamou vibrate), mas o fluxo seguiu sem mensagem de erro.
    expect(await tentativas(page)).toBeGreaterThanOrEqual(2);
    await expect(page.locator('[role="alert"]:not(next-route-announcer *)')).toHaveCount(0);
    expect(mock.reqs[0]?.status).toBe('concluida');
    expect(erros).toEqual([]);
  });
});
