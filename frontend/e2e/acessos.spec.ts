import { expect, test, type Page } from '@playwright/test';
import { mockarAdmin, type OpcoesAdmin } from './mock-admin';

const ANA_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';

async function entrarAdmin(page: Page, opcoes: OpcoesAdmin = {}) {
  const mock = await mockarAdmin(page, opcoes);
  await page.goto('/login');
  await page.getByLabel('E-mail').fill('admin@agu.gov.br');
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await expect(page).toHaveURL(/\/$|\/requisicoes|\/admin/);
  return mock;
}

const params = (consulta: string | undefined) => new URLSearchParams(consulta ?? '');

test('acessos: aba visível com ver_acessos; lista, aviso, IP mascarado e paginação da API', async ({
  page,
}) => {
  const mock = await entrarAdmin(page);
  await page.goto('/admin/usuarios');
  await expect(page.getByRole('link', { name: 'Acessos', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Acessos', exact: true }).click();

  await expect(
    page.getByText(
      'Registros de acesso são dados pessoais. Use apenas para segurança e auditoria.',
    ),
  ).toBeVisible();
  await expect(page.getByRole('list', { name: 'Acessos' }).getByRole('listitem')).toHaveCount(20);
  await expect(page.getByText('1–20 de 45')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Anterior' })).toBeDisabled();

  // Primeiro registro: data/hora no fuso de São Paulo, método e nível em texto amigável.
  const primeiro = page.getByRole('listitem').filter({ hasText: 'sess-0000' });
  await expect(primeiro).toContainText('Ana Souza');
  await expect(primeiro).toContainText('05/11/2026 12:00');
  await expect(primeiro).toContainText('Senha');
  await expect(primeiro).toContainText('Verificação simples (aal1)');
  await expect(page.getByRole('listitem').filter({ hasText: 'sess-0001' })).toContainText(
    'Segundo fator',
  );
  await expect(page.getByRole('listitem').filter({ hasText: 'sess-0001' })).toContainText(
    'Duas etapas (aal2)',
  );

  // O IP aparece exatamente como a API devolveu (já mascarado), sem completar nem reformatar.
  const ips = await page.locator('dd', { hasText: '203.0.113' }).allTextContents();
  expect(ips).toHaveLength(20);
  expect(new Set(ips)).toEqual(new Set(['203.0.113.0/24']));

  const primeira = params(mock.corpos.acessos[0]);
  expect(primeira.get('limite')).toBe('20');
  expect(primeira.get('offset')).toBe('0');
  expect(primeira.has('usuario_id')).toBe(false);

  await page.getByRole('button', { name: 'Próxima' }).click();
  await expect(page.getByText('21–40 de 45')).toBeVisible();
  await page.getByRole('button', { name: 'Próxima' }).click();
  await expect(page.getByText('41–45 de 45')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Próxima' })).toBeDisabled();
  expect(params(mock.corpos.acessos.at(-1)).get('offset')).toBe('40');
  await page.getByRole('button', { name: 'Anterior' }).click();
  await expect(page.getByText('21–40 de 45')).toBeVisible();
});

test('acessos: filtros por usuário e período (ate exclusivo) vão à API', async ({ page }) => {
  const mock = await entrarAdmin(page);
  await page.goto('/admin/acessos');
  await expect(page.getByText('1–20 de 45')).toBeVisible();

  await page.getByLabel('Usuário').selectOption({ label: 'Ana Souza' });
  await expect(page.getByText('1–15 de 15')).toBeVisible();
  const comUsuario = params(mock.corpos.acessos.at(-1));
  expect(comUsuario.get('usuario_id')).toBe(ANA_ID);
  expect(comUsuario.get('offset')).toBe('0');

  await page.getByLabel('Usuário').selectOption('');
  await page.getByLabel('Data inicial').fill('2026-11-04');
  await page.getByLabel('Data final').fill('2026-11-04');
  // Dia 04/11 inteiro: o registro de 05/11 00:00 (hora local) fica de fora (limite exclusivo).
  await expect(page.getByText('1–2 de 2')).toBeVisible();
  const consulta = params(mock.corpos.acessos.at(-1));
  expect(consulta.get('de')).toBe('2026-11-04T00:00:00-03:00');
  expect(consulta.get('ate')).toBe('2026-11-05T00:00:00-03:00');
  expect(consulta.get('limite')).toBe('20');
  expect(consulta.get('offset')).toBe('0');
  expect(consulta.has('usuario_id')).toBe(false);
});

test('acessos: erro 400 da API é exibido como veio', async ({ page }) => {
  await entrarAdmin(page);
  await page.goto('/admin/acessos');
  await expect(page.getByText('1–20 de 45')).toBeVisible();
  await page.getByLabel('Data inicial').fill('2026-11-10');
  await page.getByLabel('Data final').fill('2026-11-01');
  await expect(page.getByText("O parâmetro 'de' não pode ser posterior a 'ate'.")).toBeVisible();
  await expect(page.getByRole('listitem')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Próxima' })).toBeDisabled();
});

test('acessos: estado vazio', async ({ page }) => {
  await entrarAdmin(page, { totalAcessos: 0 });
  await page.goto('/admin/acessos');
  await expect(page.getByText('Nenhum acesso encontrado.')).toBeVisible();
  await expect(page.getByText('0 acessos')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Próxima' })).toBeDisabled();
});

test('acessos: sem ver_acessos a aba não existe e a rota mostra o 403 da API', async ({ page }) => {
  await entrarAdmin(page, { semVerAcessos: true });
  await page.goto('/admin/usuarios');
  await expect(page.getByRole('link', { name: /Ana Souza/ })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Acessos', exact: true })).toHaveCount(0);

  await page.goto('/admin/acessos');
  await expect(page.getByText('Você não tem permissão para esta ação.')).toBeVisible();
  await expect(page.getByRole('listitem')).toHaveCount(0);
});

test('usuários: mostra último acesso ou "Nunca acessou" na lista e no detalhe', async ({
  page,
}) => {
  await entrarAdmin(page);
  await page.goto('/admin/usuarios');
  await expect(
    page.getByRole('link', { name: /^Marcos Silva/ }).getByText('Último acesso: 05/11/2026 12:30'),
  ).toBeVisible();
  const ana = page.getByRole('link', { name: /Ana Souza/ });
  await expect(ana.getByText('Nunca acessou')).toBeVisible();
  // O e-mail longo continua truncado com reticências.
  const estilo = await ana
    .getByText(/ana\.souza\.com\.um/)
    .evaluate((el) => getComputedStyle(el).textOverflow);
  expect(estilo).toBe('ellipsis');

  await page.goto((await ana.getAttribute('href')) ?? '/');
  await expect(page.getByText('Nunca acessou')).toBeVisible();
  await page.goto('/admin/usuarios');
  await page.goto(
    (await page.getByRole('link', { name: /^Marcos Silva/ }).getAttribute('href')) ?? '/',
  );
  await expect(page.getByText('Último acesso: 05/11/2026 12:30')).toBeVisible();
});
