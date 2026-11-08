import { expect, test, type Page } from '@playwright/test';
import { mockarApi } from './mock-api';

async function entrar(page: Page, email: string) {
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
}

const ID0 = '33333333-3333-4333-8333-333333333330';

test('menu Dashboards: só aparece quando a API libera (solicitante recebe 403)', async ({
  page,
}) => {
  await mockarApi(page);
  await entrar(page, 'solicitante@exemplo.com');
  await expect(page).toHaveURL(/\/requisicoes$/);
  await expect(page.getByRole('link', { name: 'Requisições' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Dashboards' })).toHaveCount(0);
});

test('menu Dashboards aparece para o autorizador', async ({ page }) => {
  await mockarApi(page);
  await entrar(page, 'autorizador@exemplo.com');
  await expect(page.getByRole('link', { name: 'Dashboards' })).toBeVisible();
});

test('operação agora: contadores, cartões com linha do tempo compacta e ações da API', async ({
  page,
}) => {
  await mockarApi(page, { sementes: [{ status: 'solicitada' }] });
  await entrar(page, 'autorizador@exemplo.com');
  await page.getByRole('link', { name: 'Dashboards' }).click();

  await expect(page.getByRole('tab', { name: 'Operação agora' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByText('Aguardando autorização', { exact: true })).toBeVisible();
  await expect(page.getByText('Mais antiga há 15 min')).toBeVisible();

  const linha = page.locator('ol[data-variante="compacta"]');
  await expect(linha).toHaveCount(1);
  await expect(linha.locator('li')).toHaveCount(4);
  await expect(linha.locator('li[aria-current="step"]')).toHaveCount(1);

  // O botão vem de acoes_permitidas; ao autorizar, a API passa a mandar outro.
  await page.getByRole('button', { name: 'Autorizar' }).click();
  await expect(page.getByRole('button', { name: 'Registrar saída' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Autorizar' })).toHaveCount(0);
});

test('detalhe: linha do tempo completa com texto equivalente e etapa atual', async ({ page }) => {
  await mockarApi(page, { sementes: [{ status: 'autorizada' }] });
  await entrar(page, 'autorizador@exemplo.com');
  await page.goto(`/requisicoes/detalhe?id=${ID0}`);
  const linha = page.locator('ol[data-variante="completa"]');
  await expect(linha.locator('li')).toHaveCount(4);
  await expect(linha.locator('li[aria-current="step"]')).toContainText('Autorizada');
  await expect(page.getByText(/Requisição autorizada\. Solicitada: concluida/)).toBeAttached();
  await expect(linha.getByText('por Marcos Silva')).toBeVisible();
});

test('detalhe: requisição negada mostra o rótulo em texto', async ({ page }) => {
  await mockarApi(page, {
    sementes: [{ status: 'negada', autorizado_em: new Date().toISOString() }],
  });
  await entrar(page, 'autorizador@exemplo.com');
  await page.goto(`/requisicoes/detalhe?id=${ID0}`);
  const linha = page.locator('ol[data-variante="completa"]');
  await expect(linha.locator('li')).toHaveCount(2);
  await expect(linha.getByText('Negada', { exact: false }).first()).toBeVisible();
});

test('volume: filtros em Drawer só enviam valores; tabela alternativa disponível', async ({
  page,
}) => {
  const mock = await mockarApi(page);
  await entrar(page, 'autorizador@exemplo.com');
  await page.getByRole('link', { name: 'Dashboards' }).click();
  await page.getByRole('tab', { name: 'Volume' }).click();
  await expect(page.getByText('Requisições por período')).toBeVisible();
  // Filtros efetivos devolvidos pela API (o front não calcula datas padrão).
  await expect(page.getByLabel('Filtros aplicados')).toContainText('10/10/2026 a 08/11/2026');

  await page.getByRole('button', { name: 'Filtros' }).click();
  const gaveta = page.getByRole('dialog');
  await gaveta.getByLabel('Início').fill('2026-11-01');
  await gaveta.getByLabel('Unidade').selectOption('PU');
  await gaveta.getByRole('button', { name: 'Aplicar' }).click();
  await expect
    .poll(() => mock.consultasPainel.at(-1)?.query)
    .toEqual({ inicio: '2026-11-01', unidade: 'PU' });

  await page.getByRole('button', { name: 'Ver como tabela' }).click();
  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.getByRole('cell', { name: '3' }).first()).toBeVisible();
});

test('tempos: diferença em relação ao previsto com texto neutro', async ({ page }) => {
  await mockarApi(page);
  await entrar(page, 'autorizador@exemplo.com');
  await page.getByRole('link', { name: 'Dashboards' }).click();
  await page.getByRole('tab', { name: 'Tempos' }).click();
  await expect(page.getByText('Diferença em relação ao previsto')).toBeVisible();
  await expect(page.getByText('2 min antes do previsto')).toBeVisible();
  await expect(page.getByText(/atrasad/i)).toHaveCount(0);
});

test('opcionais: avarias listadas com momento, data e link para o detalhe', async ({ page }) => {
  await mockarApi(page);
  await entrar(page, 'autorizador@exemplo.com');
  await page.getByRole('link', { name: 'Dashboards' }).click();
  await page.getByRole('tab', { name: 'Opcionais' }).click();
  const link = page.getByRole('link', { name: /Chegada · 06\/11\/2026/ });
  await expect(link).toHaveAttribute('href', /\/requisicoes\/detalhe\?id=/);
});
