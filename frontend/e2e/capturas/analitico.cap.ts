import { expect, test, type Page } from '@playwright/test';
import { mockarApi, type Opcoes } from '../mock-api';
import { mockarAdmin } from '../mock-admin';
import { mockarNotificacoes, nova } from '../mock-notificacoes';
import { mockarRelatorios, type OpcoesRelatorios } from '../mock-relatorios';

const DESTINO = '../docs/mockups';

async function entrar(page: Page, email: string) {
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
}

async function foto(page: Page, nome: string) {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${DESTINO}/${nome}.png` });
}

async function abrirDashboards(page: Page, o: Opcoes = {}) {
  await mockarApi(page, o);
  await entrar(page, 'autorizador@exemplo.com');
  await page.getByRole('link', { name: 'Dashboards' }).click();
}

async function abrirRelatorios(page: Page, o: OpcoesRelatorios = {}) {
  await mockarApi(page);
  await mockarRelatorios(page, o);
  await entrar(page, 'autorizador@exemplo.com');
  await page.getByRole('link', { name: 'Relatórios' }).click();
  await expect(page.getByRole('heading', { name: 'Relatórios', level: 1 })).toBeVisible();
}

test('50 dashboard operação', async ({ page }) => {
  await abrirDashboards(page, {
    sementes: [{ status: 'solicitada' }, { status: 'autorizada' }],
  });
  await expect(page.getByText('Aguardando autorização', { exact: true })).toBeVisible();
  await foto(page, '50-dashboard-operacao');
});

test('51 dashboard painel', async ({ page }) => {
  await abrirDashboards(page);
  await page.getByRole('tab', { name: 'Volume' }).click();
  const titulo = page.getByText('Requisições por período');
  await expect(titulo).toBeVisible();
  await titulo.evaluate((el) => el.scrollIntoView({ block: 'start' }));
  await page.mouse.wheel(0, -60);
  await foto(page, '51-dashboard-painel');
});

test('52 dashboard resumo IA', async ({ page }) => {
  await abrirDashboards(page, { resumoIA: true });
  await page.getByRole('tab', { name: 'Volume' }).click();
  await page.getByRole('button', { name: 'Resumir com IA' }).click();
  await expect(page.getByText('Resumo gerado por IA; confira os números do painel')).toBeVisible();
  await foto(page, '52-dashboard-resumo-ia');
});

test('53 relatórios filtros', async ({ page }) => {
  await abrirRelatorios(page);
  await page.getByRole('button', { name: 'Filtros' }).click();
  const gaveta = page.getByRole('dialog');
  await gaveta.getByLabel('Início').fill('2026-11-01');
  await gaveta.getByLabel('Unidade').selectOption('UEA');
  await expect(gaveta).toBeVisible();
  await foto(page, '53-relatorios-filtros');
});

test('54 relatórios prévia com resumo IA', async ({ page }) => {
  await abrirRelatorios(page, { resumoIA: true });
  await page.getByRole('button', { name: 'Visualizar' }).click();
  await expect(page.getByRole('heading', { name: /Prévia/ })).toBeVisible();
  await page.getByRole('button', { name: 'Resumir com IA' }).click();
  await expect(page.getByText(/Foram 2 movimentações/)).toBeVisible();
  await page
    .getByRole('button', { name: 'Resumir com IA' })
    .evaluate((el) => el.scrollIntoView({ block: 'start' }));
  await foto(page, '54-relatorios-previa-resumo-ia');
});

test('55 home com saudação sazonal', async ({ page }) => {
  await mockarApi(page, { sementes: [{ status: 'solicitada' }] });
  await page.route('**/api/saudacao', (route) =>
    route.fulfill({
      json: {
        texto: 'Boa tarde, Marcos! Que a semana renda.',
        chave: '2026-11-09|tarde',
        valido_ate: null,
      },
    }),
  );
  await entrar(page, 'autorizador@exemplo.com');
  await expect(page.getByRole('status').filter({ hasText: 'Boa tarde, Marcos' })).toBeVisible();
  await foto(page, '55-home-saudacao-sazonal');
});

test('56 sino de notificações', async ({ page }) => {
  await mockarApi(page, { sementes: [{ status: 'solicitada' }] });
  await mockarNotificacoes(page, {
    autorizador: [
      nova({ titulo: 'Nova requisição aguardando autorização', critica: true }, 2),
      nova({ titulo: 'Requisição de Ana Souza foi cancelada' }, 35),
      nova({ titulo: 'Viagem concluída', lida_em: new Date().toISOString() }, 300),
    ],
  });
  await entrar(page, 'autorizador@exemplo.com');
  await expect(page.getByRole('link', { name: 'Notificações, 2 não lidas' })).toBeVisible();
  await page.getByRole('link', { name: 'Notificações, 2 não lidas' }).click();
  await expect(page.getByRole('heading', { name: 'Notificações', level: 1 })).toBeVisible();
  await expect(page.getByRole('list', { name: 'Notificações' }).getByRole('listitem')).toHaveCount(
    3,
  );
  await foto(page, '56-sino-notificacoes');
});

test('57 menu inferior do autorizador', async ({ page }) => {
  await mockarApi(page, { comAdmin: true, sementes: [{ status: 'solicitada' }] });
  await mockarRelatorios(page);
  await entrar(page, 'autorizador@exemplo.com');
  await expect(page.getByRole('link', { name: 'Relatórios' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Admin' })).toBeVisible();
  await foto(page, '57-menu-inferior-autorizador');
});

test('58 menu admin', async ({ page }) => {
  await mockarAdmin(page);
  await entrar(page, 'admin@agu.gov.br');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await page.goto('/admin/usuarios');
  await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
  await foto(page, '58-menu-admin');
});
