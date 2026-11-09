import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockarAdmin } from './mock-admin';

async function abrirRejeitadas(page: Page) {
  const mock = await mockarAdmin(page, { saudacaoLigada: true });
  await page.goto('/login');
  await page.getByLabel('E-mail').fill('admin@agu.gov.br');
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.goto('/admin/configuracoes');
  await page.getByRole('button', { name: 'Rejeitar' }).first().click();
  await page.getByRole('button', { name: 'Rejeitadas' }).click();
  return mock;
}

async function arrastar(page: Page, alvo: Locator, fracao: number) {
  await alvo.scrollIntoViewIfNeeded();
  const caixa = (await alvo.boundingBox())!;
  const y = caixa.y + caixa.height / 2;
  const x0 = caixa.x + caixa.width / 2;
  await page.mouse.move(x0, y);
  await page.mouse.down();
  await page.mouse.move(x0 + (caixa.width * fracao) / 2, y, { steps: 5 });
  await page.mouse.move(x0 + caixa.width * fracao, y, { steps: 5 });
  await page.mouse.up();
}

const TEXTO = 'Acordado, coruja noturna, {nome}?';

test('arrastar: abaixo do limiar volta ao lugar sem chamar a API', async ({ page }) => {
  const mock = await abrirRejeitadas(page);
  const item = page.getByRole('listitem').filter({ hasText: TEXTO }).locator('[tabindex="0"]');
  await arrastar(page, item, -0.2);
  await expect(page.getByText(TEXTO)).toBeVisible();
  expect(mock.corpos.saudacoes).toEqual([{ id: 'f1', acao: 'rejeitar' }]);
});

test('arrastar para a esquerda aprova', async ({ page }) => {
  const mock = await abrirRejeitadas(page);
  const item = page.getByRole('listitem').filter({ hasText: TEXTO }).locator('[tabindex="0"]');
  await arrastar(page, item, -0.6);
  await expect(page.getByText('Nenhuma frase neste status.')).toBeVisible();
  expect(mock.corpos.saudacoes).toContainEqual({ id: 'f1', acao: 'aprovar' });
});

test('arrastar para a direita pede confirmação; cancelar não exclui, confirmar exclui', async ({
  page,
}) => {
  const mock = await abrirRejeitadas(page);
  const item = page.getByRole('listitem').filter({ hasText: TEXTO }).locator('[tabindex="0"]');
  await arrastar(page, item, 0.6);
  await expect(page.getByText('Excluir definitivamente?')).toBeVisible();
  await page.getByRole('button', { name: 'Cancelar' }).click();
  await expect(page.getByText(TEXTO)).toBeVisible();
  expect(mock.corpos.saudacoes).not.toContainEqual({ id: 'f1', acao: 'excluir' });

  await arrastar(page, item, 0.6);
  await page.getByRole('button', { name: 'Confirmar' }).click();
  await expect(page.getByText('Nenhuma frase neste status.')).toBeVisible();
  expect(mock.corpos.saudacoes).toContainEqual({ id: 'f1', acao: 'excluir' });
});

test('teclado: ArrowRight inicia a exclusão e ArrowLeft aprova', async ({ page }) => {
  const mock = await abrirRejeitadas(page);
  const item = page.getByRole('listitem').filter({ hasText: TEXTO }).locator('[tabindex="0"]');
  await item.focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByText('Excluir definitivamente?')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByText('Excluir definitivamente?')).toHaveCount(0);
  await item.focus();
  await page.keyboard.press('ArrowLeft');
  await expect(page.getByText('Nenhuma frase neste status.')).toBeVisible();
  expect(mock.corpos.saudacoes).toContainEqual({ id: 'f1', acao: 'aprovar' });
});

test('abas Pendentes e Aprovadas não têm o gesto', async ({ page }) => {
  await abrirRejeitadas(page);
  await page.getByRole('button', { name: 'Aprovadas' }).click();
  await expect(page.getByText('Arraste ou use as setas')).toHaveCount(0);
});
