import { expect, test, type Page } from '@playwright/test';
import { mockarApi } from './mock-api';
import { mockarAdmin } from './mock-admin';
import { mockarMotorista } from './mock-motorista';
import { mockarRelatorios } from './mock-relatorios';

// Contrato assumido do plano (docs/plano-motorista-vigilante.md §3). Os mocks existem só para o
// teste; o front real nunca calcula permissão: tudo vem de permissoes/acoes_permitidas/flags.

async function entrar(page: Page, email: string) {
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
}

// ---- Admin: perfis, configurações ----

test('admin: convite e edição oferecem Motorista e Vigilante em pt-BR', async ({ page }) => {
  const mock = await mockarAdmin(page);
  await entrar(page, 'admin@agu.gov.br');
  await page.goto('/admin/usuarios/novo');
  await expect(page.getByLabel('Perfil').locator('option')).toHaveText([
    'Solicitante',
    'Autorizador',
    'Administrador',
    'Motorista',
    'Vigilante',
  ]);
  await page.getByLabel('E-mail').fill('paulo@gmail.com');
  await page.getByLabel('Nome completo').fill('Paulo Reis');
  await page.getByLabel('Perfil').selectOption('vigilante');
  await page.getByRole('button', { name: 'Enviar convite' }).click();
  await expect(page.getByText('Convite enviado para')).toBeVisible();
  expect(mock.corpos.convidar[0]).toMatchObject({ email: 'paulo@gmail.com', role: 'vigilante' });

  // Edição: o perfil escolhido vai como role e o chip mostra o rótulo em pt-BR.
  await page.goto('/admin/usuarios');
  const href = await page.getByRole('link', { name: /Ana Souza/ }).getAttribute('href');
  await page.goto(href ?? '/');
  await page.getByLabel('Perfil').selectOption('motorista');
  await page.getByRole('button', { name: 'Salvar alterações' }).click();
  await expect(page.getByText('Alterações salvas.')).toBeVisible();
  await expect(page.getByText('Motorista', { exact: true }).first()).toBeVisible();
});

test('configurações: vigilante_km mostra a dependência da API e motorista registra segue desligado', async ({
  page,
}) => {
  const mock = await mockarAdmin(page);
  await entrar(page, 'admin@agu.gov.br');
  await page.goto('/admin/configuracoes');

  const vig = page.getByRole('switch', { name: 'KM lançado pelo vigilante' });
  await expect(vig).toBeVisible();
  await expect(vig).toBeDisabled();
  await expect(page.getByText('Ligue antes a Quilometragem.')).toBeVisible();
  // `exige` vem da API; o rótulo da dependência é o do próprio item.
  const item = page.getByRole('listitem').filter({ hasText: 'KM lançado pelo vigilante' }).first();
  await expect(item).toContainText('Exige: Quilometragem');
  await expect(
    page.getByRole('switch', { name: 'Motorista registra saída e chegada' }),
  ).toHaveAttribute('aria-checked', 'false');

  await page.getByRole('switch', { name: 'Quilometragem' }).click();
  await expect(vig).toBeEnabled();
  await vig.click();
  await expect(vig).toHaveAttribute('aria-checked', 'true');
  expect(mock.corpos.config).toEqual([{ quilometragem: true }, { vigilante_km: true }]);
});

// ---- Vigilante ----

test('vigilante: lista autorizadas/em uso, KM só se a API pedir e botões vêm de acoes_permitidas', async ({
  page,
}) => {
  const mock = await mockarApi(page, { km: true, sementes: [{ status: 'autorizada' }] });
  await entrar(page, 'vigilante@exemplo.com');

  // Mesma visualização do solicitante + menu Viagem; sem Autorizar.
  await expect(page.getByRole('link', { name: 'Requisições' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Autorizar', exact: true })).toHaveCount(0);
  await page.getByRole('link', { name: 'Viagem' }).click();

  await expect(page.getByRole('tab', { name: /Em uso · 0/ })).toBeVisible();
  await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
  await expect(page.getByRole('button', { name: 'Autorizar' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Cancelar/ })).toHaveCount(0);
  const saida = page.getByRole('button', { name: 'Registrar saída' });
  expect((await saida.boundingBox())?.height).toBeGreaterThanOrEqual(44);
  await page.getByLabel('Km de saída').fill('1200');
  await saida.click();

  await expect(page.getByRole('tab', { name: /Em uso · 1/ })).toBeVisible();
  await page.getByRole('tab', { name: /Em uso · 1/ }).click();
  await expect(page.getByLabel('Km de saída')).toHaveCount(0);
  await page.getByLabel('Km de chegada').fill('1245');
  await page.getByRole('button', { name: 'Registrar chegada agora' }).click();
  await expect(page.getByText('Nenhuma viagem em uso.')).toBeVisible();

  expect(mock.corposAcao).toEqual([
    { acao: 'saida', corpo: { km_saida: 1200 } },
    { acao: 'chegada', corpo: { km_chegada: 1245 } },
  ]);
});

test('vigilante: sem KM exigido pela API não há campo de km', async ({ page }) => {
  const mock = await mockarApi(page, { sementes: [{ status: 'autorizada' }] });
  await entrar(page, 'vigilante@exemplo.com');
  await page.getByRole('link', { name: 'Viagem' }).click();
  await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
  await expect(page.getByLabel('Km de saída')).toHaveCount(0);
  await page.getByRole('button', { name: 'Registrar saída' }).click();
  await expect(page.getByRole('tab', { name: /Em uso · 1/ })).toBeVisible();
  expect(mock.corposAcao[0]?.acao).toBe('saida');
});

test('autorizador sem registrar_saida em permissoes não ganha abas (só quem a API libera)', async ({
  page,
}) => {
  await mockarApi(page);
  await entrar(page, 'solicitante@exemplo.com');
  await expect(page.getByRole('link', { name: 'Viagem' })).toHaveCount(0);
});

// ---- Motorista ----

test('autorização: o motorista padrão da API vem pré-selecionado e vai no corpo', async ({
  page,
}) => {
  const mock = await mockarApi(page, { sementes: [{ status: 'solicitada' }] });
  await page.route('**/api/motoristas', (route) =>
    route.fulfill({
      json: {
        itens: [{ id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', nome: 'Carlos Lima' }],
        padrao: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
      },
    }),
  );
  await entrar(page, 'autorizador@exemplo.com');
  await page.getByRole('link', { name: 'Autorizar' }).click();

  const seletor = page.getByLabel('Motorista', { exact: true });
  await expect(seletor).toHaveValue('eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee');
  await page.getByRole('button', { name: 'Autorizar' }).click();
  await expect(page.getByText('Nada por aqui.')).toBeVisible();
  expect(mock.corposAcao[0]).toEqual({
    acao: 'autorizar',
    corpo: { motorista_id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee' },
  });
});

test('autorização: padrao nulo mantém o seletor opcional e vazio', async ({ page }) => {
  await mockarApi(page, { sementes: [{ status: 'solicitada' }] });
  await page.route('**/api/motoristas', (route) =>
    route.fulfill({
      json: {
        itens: [
          { id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', nome: 'Carlos Lima' },
          { id: 'ffffffff-ffff-4fff-8fff-ffffffffffff', nome: 'Rita Alves' },
        ],
        padrao: null,
      },
    }),
  );
  await entrar(page, 'autorizador@exemplo.com');
  await page.getByRole('link', { name: 'Autorizar' }).click();
  await expect(page.getByLabel('Motorista (opcional)')).toHaveValue('');
});

test('motorista: o detalhe mostra a linha do tempo completa da viagem', async ({ page }) => {
  await mockarMotorista(page, { motoristaPadrao: true });
  await entrar(page, 'motorista@exemplo.com');
  await page.getByRole('link', { name: 'Minhas viagens' }).click();
  await page
    .getByRole('link', { name: /Sede -> Procuradoria Regional/ })
    .first()
    .click();
  const linha = page.getByRole('list', { name: 'Linha do tempo da requisição' });
  await expect(linha.getByRole('listitem')).toHaveCount(4);
  await expect(linha).toContainText('Solicitada');
  await expect(linha).toContainText('Chegada registrada');
});

// ---- Relatórios ----

test('relatórios: filtro "lançado por vigilante" e quem lançou o KM vêm do catálogo da API', async ({
  page,
}) => {
  await mockarApi(page);
  const rel = await mockarRelatorios(page, { kmVigilante: true });
  await entrar(page, 'autorizador@exemplo.com');
  await page.getByRole('link', { name: 'Relatórios' }).click();

  await page.getByRole('button', { name: 'Filtros' }).click();
  await page.getByRole('dialog').getByRole('switch', { name: 'Lançado por vigilante' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Aplicar' }).click();
  await expect(page.getByLabel('Filtros aplicados')).toHaveText(
    'Incluir canceladas: Não · Lançado por vigilante: Sim',
  );

  await page.getByRole('button', { name: 'Visualizar' }).click();
  const tabela = page.getByRole('table', { name: /Movimentação de veículos/ });
  await expect(tabela.getByRole('columnheader')).toHaveText([
    'Data',
    'Solicitante',
    'Situação',
    'Km de saída',
    'Km de chegada',
    'KM lançado por',
  ]);
  await expect(tabela.getByText('Paulo Reis (vigilante)').first()).toBeVisible();
  expect(rel.consultas.at(-1)).toEqual({
    rota: 'movimentacao/previa',
    query: { incluir_canceladas: 'false', lancado_por_vigilante: 'true' },
  });
});
