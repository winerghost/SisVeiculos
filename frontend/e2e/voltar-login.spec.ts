import { expect, test, type Page } from '@playwright/test';
import { mockarApi } from './mock-api';
import { caminhoInterno, destinoAposLogin, urlDeLogin } from '../src/lib/voltar';

// Retorno pós-login (`/login?voltar=`): volta à tela do link e nunca sai do site.

const ID = '33333333-3333-4333-8333-333333333330';
const DETALHE = `/requisicoes/detalhe?id=${ID}`;

async function preencherLogin(page: Page, email: string) {
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
}

const caminhoAtual = (page: Page) => {
  const u = new URL(page.url());
  return `${u.pathname}${u.search}`;
};

test.describe('validação do caminho (lib/voltar)', () => {
  const maliciosos = [
    '//evil.com',
    '//evil.com/requisicoes',
    'https://evil.com',
    'http:/evil.com',
    'javascript:alert(1)',
    'JaVaScRiPt:alert(1)',
    ' /requisicoes',
    '/\\evil.com',
    '\\\\evil.com',
    '/%2F/evil.com',
    '/%5Cevil.com',
    '%2F%2Fevil.com',
    '/req\tuisicoes',
    '/req\nuisicoes',
    '/req\u0000uisicoes',
    '/req uisicoes',
    '/%E0%A4%A',
    'requisicoes',
    '',
    '/.//evil.com',
    '/..//evil.com',
    '/%2e//evil.com',
    '/a/..//evil.com',
    '/%09/evil.com',
    '/%0A/evil.com',
    '/ x',
    '/﻿x',
    '/requisicoes/../login',
    '/login',
    '/login?voltar=%2Frequisicoes',
    `/${'a'.repeat(600)}`,
  ];

  for (const v of maliciosos) {
    test(`recusa ${JSON.stringify(v).slice(0, 40)}`, () => {
      expect(caminhoInterno(v)).toBeNull();
    });
  }

  test('aceita caminhos internos e preserva a query', () => {
    expect(caminhoInterno(DETALHE)).toBe(DETALHE);
    expect(caminhoInterno('/requisicoes')).toBe('/requisicoes');
    expect(caminhoInterno('/admin/usuarios/detalhe?id=abc#topo')).toBe(
      '/admin/usuarios/detalhe?id=abc#topo',
    );
    expect(caminhoInterno(null)).toBeNull();
    expect(caminhoInterno(42)).toBeNull();
  });

  test('destino padrão quando ausente ou inválido', () => {
    expect(destinoAposLogin('')).toBe('/requisicoes');
    expect(destinoAposLogin('?voltar=%2F%2Fevil.com')).toBe('/requisicoes');
    expect(destinoAposLogin('?voltar=javascript%3Aalert(1)')).toBe('/requisicoes');
    expect(destinoAposLogin(`?voltar=${encodeURIComponent(DETALHE)}`)).toBe(DETALHE);
  });

  test('url de login só leva voltar para caminho interno', () => {
    expect(urlDeLogin('/requisicoes/detalhe', `?id=${ID}`)).toBe(
      `/login?voltar=${encodeURIComponent(DETALHE)}`,
    );
    expect(urlDeLogin('/', '')).toBe('/login');
    expect(urlDeLogin('/requisicoes', '')).toBe('/login');
    expect(urlDeLogin('//evil.com', '')).toBe('/login');
  });

  test('url de login leva só o id das telas de detalhe (minimização)', () => {
    // Parâmetros extras no detalhe são descartados; só `id` segue.
    expect(urlDeLogin('/requisicoes/detalhe', `?id=${ID}&x=segredo`)).toBe(
      `/login?voltar=${encodeURIComponent(DETALHE)}`,
    );
    expect(urlDeLogin('/motorista/detalhe', `?id=${ID}`)).toBe(
      `/login?voltar=${encodeURIComponent(`/motorista/detalhe?id=${ID}`)}`,
    );
    // Filtros de outras telas (ex.: relatórios) não vão para a URL do login.
    expect(urlDeLogin('/relatorios', '?de=2026-11-01&ate=2026-11-17&unidade=PU')).toBe(
      `/login?voltar=${encodeURIComponent('/relatorios')}`,
    );
    expect(urlDeLogin('/requisicoes/detalhe', '')).toBe(
      `/login?voltar=${encodeURIComponent('/requisicoes/detalhe')}`,
    );
  });
});

test('link aberto sem sessão volta à requisição depois do login', async ({ page }) => {
  await mockarApi(page, { sementes: [{ status: 'solicitada' }] });
  await page.goto(DETALHE);
  await expect(page).toHaveURL(/\/login\?voltar=/);
  expect(new URL(page.url()).searchParams.get('voltar')).toBe(DETALHE);

  await preencherLogin(page, 'solicitante@exemplo.com');
  await expect.poll(() => caminhoAtual(page)).toBe(DETALHE);
});

test('login sem voltar segue para /requisicoes', async ({ page }) => {
  await mockarApi(page);
  await page.goto('/login');
  await preencherLogin(page, 'solicitante@exemplo.com');
  await expect.poll(() => caminhoAtual(page)).toBe('/requisicoes');
});

for (const malicioso of [
  '//evil.com',
  'https://evil.com',
  '/\\evil.com',
  'javascript:alert(1)',
  '/%2F/evil.com',
]) {
  test(`open redirect bloqueado: ${malicioso}`, async ({ page }) => {
    await mockarApi(page);
    let saiu = false;
    // Pelo host (a query do próprio login contém "evil.com").
    await page.route(
      (u) => u.hostname.endsWith('evil.com'),
      (route) => {
        saiu = true;
        return route.abort();
      },
    );
    const dialogos: string[] = [];
    page.on('dialog', (d) => {
      dialogos.push(d.message());
      void d.dismiss();
    });
    await page.goto(`/login?voltar=${encodeURIComponent(malicioso)}`);
    await preencherLogin(page, 'solicitante@exemplo.com');
    await expect.poll(() => caminhoAtual(page)).toBe('/requisicoes');
    expect(new URL(page.url()).origin).toBe('http://127.0.0.1:3100');
    expect(saiu).toBe(false);
    expect(dialogos).toEqual([]);
  });
}

test('após o segundo fator também volta à requisição', async ({ page }) => {
  await mockarApi(page, { sementes: [{ status: 'solicitada' }] });
  // /api/eu exige MFA até a verificação; depois segue para o mock padrão.
  let verificado = false;
  await page.route('**/api/eu', (route) =>
    verificado
      ? route.fallback()
      : route.fulfill({
          status: 403,
          json: {
            erro: {
              codigo: 'mfa_requerido',
              mensagem: 'Este perfil exige verificação em duas etapas.',
            },
          },
        }),
  );
  await page.route('**/auth/v1/factors/fator-1/challenge', (route) =>
    route.fulfill({
      json: {
        id: 'desafio-1',
        type: 'totp',
        expires_at: Math.floor(Date.now() / 1000) + 300,
      },
    }),
  );
  await page.route('**/auth/v1/factors/fator-1/verify', (route) => {
    verificado = true;
    return route.fulfill({
      json: {
        access_token: 'tok-autorizador',
        token_type: 'bearer',
        expires_in: 3600,
        expires_at: Math.floor(Date.now() / 1000) + 3600,
        refresh_token: 'refresh-autorizador-2',
        user: {
          id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
          aud: 'authenticated',
          email: 'autorizador@exemplo.com',
          app_metadata: {},
          user_metadata: {},
          created_at: '2026-01-01T00:00:00Z',
        },
      },
    });
  });

  await page.goto(DETALHE);
  await expect(page).toHaveURL(/\/login\?voltar=/);
  await preencherLogin(page, 'autorizador@exemplo.com');
  await expect(page.getByLabel('Código do autenticador')).toBeVisible();
  await page.getByLabel('Código do autenticador').fill('123456');
  await page.getByLabel('Código do autenticador').press('Enter');
  await expect.poll(() => caminhoAtual(page)).toBe(DETALHE);
});

test('logout manual vai ao login sem voltar', async ({ page }) => {
  await mockarApi(page);
  await page.goto('/login');
  await preencherLogin(page, 'solicitante@exemplo.com');
  await expect.poll(() => caminhoAtual(page)).toBe('/requisicoes');
  await page.getByRole('button', { name: 'Sair' }).first().click();
  await expect.poll(() => caminhoAtual(page)).toBe('/login');
});
