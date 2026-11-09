import { expect, test, type Page } from '@playwright/test';
import { mockarApi, type Opcoes } from './mock-api';
import { mockarRelatorios, type OpcoesRelatorios } from './mock-relatorios';

async function entrar(page: Page) {
  await page.goto('/login');
  await page.getByLabel('E-mail').fill('autorizador@exemplo.com');
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
}

async function abrirVolume(page: Page, o: Opcoes = {}) {
  const mock = await mockarApi(page, o);
  await entrar(page);
  await page.getByRole('link', { name: 'Dashboards' }).click();
  await page.getByRole('tab', { name: 'Volume' }).click();
  await expect(page.getByText('Requisições por período')).toBeVisible();
  return mock;
}

async function abrirPrevia(page: Page, o: OpcoesRelatorios = {}) {
  await mockarApi(page);
  const rel = await mockarRelatorios(page, o);
  await entrar(page);
  await page.getByRole('link', { name: 'Relatórios' }).click();
  await page.getByRole('button', { name: 'Visualizar' }).click();
  await expect(page.getByRole('heading', { name: /Prévia/ })).toBeVisible();
  return rel;
}

const BOTAO = { name: 'Resumir com IA' };

test('dashboard: sem resumo_ia não há botão', async ({ page }) => {
  await abrirVolume(page);
  await expect(page.getByRole('button', BOTAO)).toHaveCount(0);
});

test('dashboard: gera só no clique, mostra selo e texto puro, e limpa ao mudar o filtro', async ({
  page,
}) => {
  const mock = await abrirVolume(page, { resumoIA: true });
  expect(mock.resumosPainel).toHaveLength(0);
  const altura = (await page.getByRole('button', BOTAO).boundingBox())!.height;
  expect(altura).toBeGreaterThanOrEqual(44);

  await page.getByRole('button', BOTAO).click();
  await expect(page.getByText('Resumo gerado por IA; confira os números do painel')).toBeVisible();
  await expect(page.getByText('<b>não é HTML</b>', { exact: false })).toBeVisible();
  expect(mock.resumosPainel).toHaveLength(1);
  expect(mock.resumosPainel[0]?.painel).toBe('volume');

  await page.getByRole('button', { name: 'Filtros' }).click();
  const gaveta = page.getByRole('dialog');
  await gaveta.getByLabel('Unidade').selectOption('PU');
  await gaveta.getByRole('button', { name: 'Aplicar' }).click();
  await expect(page.getByText('Resumo gerado por IA; confira os números do painel')).toHaveCount(0);
  await page.getByRole('button', BOTAO).click();
  await expect.poll(() => mock.resumosPainel.at(-1)?.query).toMatchObject({ unidade: 'PU' });
});

test('dashboard: erro da API aparece como veio', async ({ page }) => {
  await abrirVolume(page, {
    resumoIA: true,
    erroResumo: { status: 409, codigo: 'ia_indisponivel', mensagem: 'A IA está desligada.' },
  });
  await page.getByRole('button', BOTAO).click();
  await expect(page.getByRole('alert').filter({ hasText: 'A IA está desligada.' })).toBeVisible();
});

test('relatório: sem resumo_ia não há botão', async ({ page }) => {
  await abrirPrevia(page);
  await expect(page.getByRole('button', BOTAO)).toHaveCount(0);
});

test('relatório: gera, mostra selo, erro 429 e limpa ao mudar filtro', async ({ page }) => {
  const rel = await abrirPrevia(page, { resumoIA: true });
  await page.getByRole('button', BOTAO).click();
  await expect(page.getByText(/Foram 2 movimentações/)).toBeVisible();
  await expect(
    page.getByText(
      'Resumo gerado por IA do relatório completo; confira com o arquivo do relatório',
    ),
  ).toBeVisible();
  await expect(page.getByText('Resumo do relatório completo (1200 registros)')).toBeVisible();
  expect(rel.resumos).toHaveLength(1);

  await page.getByRole('button', { name: 'Filtros' }).click();
  const gaveta = page.getByRole('dialog');
  await gaveta.getByLabel('Unidade').selectOption('PU');
  await gaveta.getByRole('button', { name: 'Aplicar' }).click();
  await expect(page.getByText(/Foram 2 movimentações/)).toHaveCount(0);
});

test('relatório: erro 429 aparece como veio', async ({ page }) => {
  await abrirPrevia(page, {
    resumoIA: true,
    erroResumo: { status: 429, codigo: 'limite', mensagem: 'Muitas solicitações de resumo.' },
  });
  await page.getByRole('button', BOTAO).click();
  await expect(
    page.getByRole('alert').filter({ hasText: 'Muitas solicitações de resumo.' }),
  ).toBeVisible();
});

test('dashboard: botão fica desabilitado até os números refletirem o filtro e usa os filtros efetivos', async ({
  page,
}) => {
  const mock = await abrirVolume(page, { resumoIA: true, atrasoPainelMs: 1500 });
  await page.getByRole('button', { name: 'Filtros' }).click();
  const gaveta = page.getByRole('dialog');
  await gaveta.getByLabel('Unidade').selectOption('PU');
  await gaveta.getByRole('button', { name: 'Aplicar' }).click();
  // Durante a recarga, os números ainda são os do filtro anterior.
  await expect(page.getByRole('button', BOTAO)).toBeDisabled();
  await expect(page.getByRole('button', BOTAO)).toBeEnabled({ timeout: 10_000 });
  await page.getByRole('button', BOTAO).click();
  await expect(page.getByText('Resumo gerado por IA; confira os números do painel')).toBeVisible();
  expect(mock.resumosPainel.at(-1)?.query).toEqual({
    inicio: '2026-10-10',
    fim: '2026-11-08',
    unidade: 'PU',
    granularidade: 'dia',
  });
});

test('relatório: sem escopo na resposta não exibe nada extra', async ({ page }) => {
  await abrirPrevia(page, { resumoIA: true, semEscopo: true });
  await page.getByRole('button', BOTAO).click();
  await expect(page.getByText(/Foram 2 movimentações/)).toBeVisible();
  await expect(page.getByText(/1200 registros/)).toHaveCount(0);
});
