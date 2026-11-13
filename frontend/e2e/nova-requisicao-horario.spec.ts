import { expect, test, type Page } from '@playwright/test';
import { mockarApi } from './mock-api';

async function entrar(page: Page, email: string, senha = 'senha-correta') {
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill(senha);
  await page.getByRole('button', { name: 'Entrar' }).click();
}

async function abrirSeletor(page: Page) {
  await page.getByRole('link', { name: 'Nova requisição' }).click();
  await page.getByRole('button', { name: /Escolha o dia e o horário/ }).click();
  return page.getByRole('dialog');
}

test('o seletor abre na sugestão da API, não em 08:00', async ({ page }) => {
  const mock = await mockarApi(page, {
    horarioReferencia: { data: '2026-11-13', hora: 14, minuto: 35 },
  });
  await entrar(page, 'solicitante@exemplo.com');
  const gaveta = await abrirSeletor(page);

  await expect(gaveta.getByLabel('Hora')).toHaveValue('14');
  await expect(gaveta.getByLabel('Minuto')).toHaveValue('35');
  await expect(gaveta.getByText('novembro 2026')).toBeVisible();
  await expect(gaveta.getByRole('gridcell', { name: /13 de novembro/ })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  expect(mock.consultasReferencia()).toBe(1);

  await gaveta.getByRole('button', { name: 'Confirmar' }).click();
  await expect(page.getByRole('button', { name: /13\/11\/2026 às 14:35/ })).toBeVisible();
});

test('valor já confirmado é preservado ao reabrir o seletor', async ({ page }) => {
  const mock = await mockarApi(page, {
    horarioReferencia: { data: '2026-11-13', hora: 14, minuto: 35 },
  });
  await entrar(page, 'solicitante@exemplo.com');
  let gaveta = await abrirSeletor(page);
  await gaveta.getByLabel('Hora').selectOption('16');
  await gaveta.getByLabel('Minuto').selectOption('10');
  await gaveta.getByRole('button', { name: 'Confirmar' }).click();

  await page.getByRole('button', { name: /13\/11\/2026 às 16:10/ }).click();
  gaveta = page.getByRole('dialog');
  await expect(gaveta.getByLabel('Hora')).toHaveValue('16');
  await expect(gaveta.getByLabel('Minuto')).toHaveValue('10');
  expect(mock.consultasReferencia()).toBe(1); // reabrir não consulta de novo
});

test('nenhum dia do calendário fica desabilitado (quem valida é o servidor)', async ({ page }) => {
  await mockarApi(page, { horarioReferencia: { data: '2026-11-13', hora: 14, minuto: 35 } });
  await entrar(page, 'solicitante@exemplo.com');
  const gaveta = await abrirSeletor(page);
  await expect(gaveta.locator('[role="gridcell"][data-day="2026-11-01"]')).toBeVisible();
  await expect(gaveta.locator('[role="gridcell"][aria-disabled="true"]')).toHaveCount(0);
  await expect(gaveta.locator('button[disabled]').filter({ hasText: /^\d+$/ })).toHaveCount(0);
});

test('falha na consulta de referência: abre com o relógio do aparelho', async ({ page }) => {
  await mockarApi(page, { erroHorarioReferencia: true });
  await entrar(page, 'solicitante@exemplo.com');
  const gaveta = await abrirSeletor(page);
  const minuto = Number(await gaveta.getByLabel('Minuto').inputValue());
  expect(minuto % 5).toBe(0); // sugestão local arredondada de 5 em 5
  await expect(gaveta.getByRole('button', { name: 'Confirmar' })).toBeEnabled();
});

test('horário no passado: a mensagem vem do servidor', async ({ page }) => {
  const mensagem =
    'A hora de saída não pode ser anterior ao horário atual. Agora são 13/11/2026 às 14:32 (horário de Brasília). Escolha um horário a partir das 14:22.';
  await mockarApi(page, {
    erroCriacao: { status: 400, codigo: 'hora_saida_passada', mensagem },
  });
  await entrar(page, 'solicitante@exemplo.com');
  await page.getByRole('link', { name: 'Nova requisição' }).click();
  await page.getByLabel('Veículo', { exact: true }).selectOption({ index: 1 });
  await page.getByRole('button', { name: /Escolha o dia e o horário/ }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Confirmar' }).click();
  await page.getByLabel('Trajeto').fill('Sede -> Fórum');
  await page.getByRole('button', { name: 'Enviar requisição' }).click();
  await expect(page.getByText('Não foi possível enviar')).toBeVisible();
  await expect(page.getByText(mensagem)).toBeVisible();
});

// ---- cor da data e hora (situação calculada pela API) -----------------------------------
const CORES = {
  no_prazo: { fundo: 'rgb(217, 242, 230)', texto: 'rgb(10, 90, 58)' },
  proximo: { fundo: 'rgb(255, 241, 214)', texto: 'rgb(122, 74, 0)' },
  atrasado: { fundo: 'rgb(253, 226, 224)', texto: 'rgb(138, 31, 23)' },
} as const;

for (const situacao of ['no_prazo', 'proximo', 'atrasado'] as const) {
  test(`data e hora em ${situacao}: cor da situação informada pela API`, async ({ page }) => {
    await mockarApi(page, { sementes: [{ status: 'solicitada', situacao_horario: situacao }] });
    await entrar(page, 'solicitante@exemplo.com');
    const marca = page.locator(`[data-situacao-horario="${situacao}"]`).first();
    await expect(marca).toBeVisible();
    await expect(marca).toHaveCSS('background-color', CORES[situacao].fundo);
    await expect(marca).toHaveCSS('color', CORES[situacao].texto);
    // A cor não é o único sinal: o estado também aparece em texto (visível ou para leitor de tela).
    const texto = (await marca.textContent()) ?? '';
    expect(texto).toMatch(
      situacao === 'proximo' ? /em breve/ : situacao === 'atrasado' ? /vencido/ : /\d/,
    );
  });
}

test('sem situação (em uso, concluída etc.) a data fica sem destaque', async ({ page }) => {
  await mockarApi(page, { sementes: [{ status: 'em_uso', situacao_horario: null }] });
  await entrar(page, 'solicitante@exemplo.com');
  await expect(page.getByText('Em uso', { exact: true }).first()).toBeVisible();
  await expect(page.locator('[data-situacao-horario]')).toHaveCount(0);
});
