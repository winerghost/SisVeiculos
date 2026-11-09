import { expect, test, type Page } from '@playwright/test';
import { mockarAdmin, type OpcoesAdmin } from './mock-admin';

async function entrarAdmin(page: Page, opcoes: OpcoesAdmin = {}) {
  const mock = await mockarAdmin(page, opcoes);
  await page.goto('/login');
  await page.getByLabel('E-mail').fill('admin@agu.gov.br');
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  return mock;
}

test('saudação: banner mostra o texto da API como texto puro', async ({ page }) => {
  await entrarAdmin(page, { saudacao: 'Acordado, coruja noturna, <b>Marcos</b>?' });
  const banner = page.getByRole('status').filter({ hasText: 'Acordado, coruja noturna' });
  await expect(banner).toBeVisible();
  await expect(banner).toContainText('<b>Marcos</b>');
});

test('saudação: texto null não renderiza banner', async ({ page }) => {
  await entrarAdmin(page, { saudacao: null });
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: 'coruja' })).toHaveCount(0);
});

test('saudação: bloco só aparece com a função habilitada', async ({ page }) => {
  await entrarAdmin(page);
  await page.goto('/admin/configuracoes');
  await expect(page.getByLabel('Provedor', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Saudações' })).toHaveCount(0);
});

test('saudação: admin aprova, rejeita, adiciona e gera frases', async ({ page }) => {
  const mock = await entrarAdmin(page, { saudacaoLigada: true });
  await page.goto('/admin/configuracoes');
  await expect(page.getByRole('heading', { name: 'Saudações' })).toBeVisible();
  await expect(page.getByText('Acordado, coruja noturna, {nome}?')).toBeVisible();
  await page.getByRole('button', { name: 'Aprovar' }).click();
  await expect(page.getByText('Nenhuma frase neste status.')).toBeVisible();
  await page.getByRole('button', { name: 'Aprovadas' }).click();
  await expect(page.getByText('Acordado, coruja noturna, {nome}?')).toBeVisible();
  // Aprovada: a API libera só Rejeitar (nenhum Aprovar nesta aba).
  await expect(page.getByRole('button', { name: 'Aprovar' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Rejeitar' })).toHaveCount(2);

  await page.getByLabel('Texto da frase').fill('Bom dia, {nome}!');
  await page.getByRole('button', { name: 'Adicionar frase' }).click();
  await expect(page.getByText('Frase adicionada.')).toBeVisible();
  await expect(page.getByText('Bom dia, {nome}!')).toBeVisible();

  await page.getByRole('button', { name: 'Gerar com IA' }).click();
  await expect(page.getByText('Feliz Natal, {nome}!')).toBeVisible();
  await page.getByRole('button', { name: 'Pendentes' }).click();
  await page.getByRole('button', { name: 'Rejeitar' }).click();
  await page.getByRole('button', { name: 'Rejeitadas' }).click();
  await expect(page.getByText('Feliz Natal, {nome}!')).toBeVisible();
  // Rejeitada: a API libera só Aprovar.
  await expect(page.getByRole('button', { name: 'Aprovar' })).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'Rejeitar' })).toHaveCount(0);

  expect(mock.corpos.saudacoes).toEqual([
    { id: 'f1', acao: 'aprovar' },
    { ocasiao: 'Madrugada', texto: 'Bom dia, {nome}!' },
    { ocasiao: 'Madrugada', quantidade: 3 },
    { id: 'f4', acao: 'rejeitar' },
  ]);
});

test('saudação: erro da geração por IA aparece como veio', async ({ page }) => {
  await entrarAdmin(page, {
    saudacaoLigada: true,
    erroGerarSaudacao: 'Provedor de IA indisponível.',
  });
  await page.goto('/admin/configuracoes');
  await page.getByRole('button', { name: 'Gerar com IA' }).click();
  await expect(
    page.getByRole('alert').filter({ hasText: 'Provedor de IA indisponível.' }),
  ).toBeVisible();
});
