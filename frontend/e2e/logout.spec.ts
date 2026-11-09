import { expect, test, type Page } from '@playwright/test';
import { mockarApi } from './mock-api';

async function entrar(page: Page) {
  const mock = await mockarApi(page);
  // Registrada depois do mock (prevalece): captura os logouts do SDK.
  const logouts: string[] = [];
  return { mock, logouts };
}

async function logar(page: Page) {
  await page.goto('/login');
  await page.getByLabel('E-mail').fill('solicitante@agu.gov.br');
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

const sessaoLocal = (page: Page) =>
  page.evaluate(() => Object.keys(localStorage).filter((k) => k.includes('auth-token')).length);

test('logout manual chama /auth/v1/logout com scope=global e vai ao login', async ({ page }) => {
  const { logouts } = await entrar(page);
  await page.route('**/auth/v1/logout*', (route) => {
    logouts.push(route.request().url());
    return route.fulfill({ status: 204 });
  });
  await logar(page);
  await page.getByRole('button', { name: 'Sair' }).first().click();
  await expect(page).toHaveURL(/\/login/);
  await expect.poll(() => logouts.length).toBe(1);
  expect(new URL(logouts[0] ?? '').searchParams.get('scope')).toBe('global');
  expect(await sessaoLocal(page)).toBe(0);
});

test('falha de rede no logout global ainda leva ao login e limpa a sessão local', async ({
  page,
}) => {
  const { logouts } = await entrar(page);
  await page.route('**/auth/v1/logout*', (route) => {
    logouts.push(route.request().url());
    return route.abort('failed');
  });
  await logar(page);
  await page.getByRole('button', { name: 'Sair' }).first().click();
  await expect(page).toHaveURL(/\/login/);
  await expect.poll(() => sessaoLocal(page)).toBe(0);
  expect(logouts.length).toBeGreaterThanOrEqual(1);
  expect(new URL(logouts[0] ?? '').searchParams.get('scope')).toBe('global');
});

test('logout global lento não trava: vai ao login antes do prazo da rede', async ({ page }) => {
  await entrar(page);
  await page.route('**/auth/v1/logout*', async (route) => {
    await new Promise((r) => setTimeout(r, 8000));
    await route.fulfill({ status: 204 }).catch(() => undefined);
  });
  await logar(page);
  await page.getByRole('button', { name: 'Sair' }).first().click();
  await expect(page).toHaveURL(/\/login/, { timeout: 2000 });
  // Estoura o prazo de 3 s e cai para o escopo local.
  await expect.poll(() => sessaoLocal(page), { timeout: 8000 }).toBe(0);
});

test('expiração por 401 usa scope=local', async ({ page }) => {
  const { logouts } = await entrar(page);
  await page.route('**/auth/v1/logout*', (route) => {
    logouts.push(route.request().url());
    return route.fulfill({ status: 204 });
  });
  await logar(page);
  await page.route('**/api/eu', (route) =>
    route.fulfill({
      status: 401,
      json: { erro: { codigo: 'nao_autenticado', mensagem: 'Sessão expirada.' } },
    }),
  );
  await page.reload();
  await expect(page).toHaveURL(/\/login/);
  await expect.poll(() => logouts.length).toBe(1);
  expect(new URL(logouts[0] ?? '').searchParams.get('scope')).toBe('local');
});

test('logout apaga caches paginas-* e preserva assets-*', async ({ page }) => {
  await entrar(page);
  await logar(page);
  await page.evaluate(async () => {
    await (await caches.open('paginas-v3')).put('/x', new Response('x'));
    await (await caches.open('assets-v3')).put('/y', new Response('y'));
  });
  await page.getByRole('button', { name: 'Sair' }).first().click();
  await expect(page).toHaveURL(/\/login/);
  await expect
    .poll(() => page.evaluate(async () => (await caches.keys()).sort()))
    .toEqual(['assets-v3']);
});

test('dois cliques e dois 401 simultâneos geram um único logout', async ({ page }) => {
  const { logouts } = await entrar(page);
  await page.route('**/auth/v1/logout*', (route) => {
    logouts.push(route.request().url());
    return route.fulfill({ status: 204 });
  });
  await logar(page);
  await page
    .getByRole('button', { name: 'Sair' })
    .first()
    .dblclick({ force: true })
    .catch(() => undefined);
  await expect(page).toHaveURL(/\/login/);
  await page.waitForTimeout(500);
  expect(logouts).toHaveLength(1);

  // Dois 401 ao mesmo tempo (contagem de notificações disparada duas vezes).
  logouts.length = 0;
  await logar(page);
  await page.route('**/api/notificacoes/contagem', (route) =>
    route.fulfill({
      status: 401,
      json: { erro: { codigo: 'nao_autenticado', mensagem: 'Sessão expirada.' } },
    }),
  );
  await page.evaluate(() => {
    document.dispatchEvent(new Event('visibilitychange'));
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(page).toHaveURL(/\/login/);
  await page.waitForTimeout(500);
  expect(logouts).toHaveLength(1);
  expect(new URL(logouts[0] ?? '').searchParams.get('scope')).toBe('local');
});
