import { expect, test, type Page } from '@playwright/test';
import { mockarApi } from './mock-api';
import { mockarAdmin } from './mock-admin';
import { mockarMotorista, type OpcoesMotorista } from './mock-motorista';

async function entrar(page: Page, email: string) {
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
}

async function entrarMotorista(page: Page, opcoes: OpcoesMotorista = {}) {
  const mock = await mockarMotorista(page, opcoes);
  await entrar(page, 'motorista@exemplo.com');
  await page.getByRole('link', { name: 'Minhas viagens' }).click();
  await expect(page.getByRole('heading', { name: 'Minhas viagens' })).toBeVisible();
  return mock;
}

test('motorista: viagens agrupadas, registra saída e chegada pelos botões da API', async ({
  page,
}) => {
  const mock = await entrarMotorista(page);

  await expect(page.getByRole('heading', { name: /Em andamento · 0/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: /Próximas · 1/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: /Concluídas · 1/ })).toBeVisible();
  await expect(page.getByText('Veículo 01 · ABC-1D23').first()).toBeVisible();
  // A viagem concluída não traz ações: só há o botão da autorizada.
  await expect(page.getByRole('button', { name: /Registrar/ })).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'Registrar chegada agora' })).toHaveCount(0);

  await page.getByRole('button', { name: 'Registrar saída' }).click();
  await expect(page.getByRole('heading', { name: /Em andamento · 1/ })).toBeVisible();
  await page.getByRole('button', { name: 'Registrar chegada agora' }).click();
  await expect(page.getByRole('heading', { name: /Em andamento · 0/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: /Concluídas · 2/ })).toBeVisible();
  expect(mock.acoesRecebidas).toEqual(['saida', 'chegada']);
});

test('motorista: sem acoes_permitidas da API nenhum botão de saída/chegada aparece', async ({
  page,
}) => {
  await entrarMotorista(page, { semAcoes: true });
  await expect(page.getByRole('heading', { name: /Próximas · 1/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Registrar/ })).toHaveCount(0);
});

test('motorista: detalhe da viagem mostra horários, veículo e acompanhamento', async ({ page }) => {
  await entrarMotorista(page);
  await page
    .getByRole('link', { name: /Sede -> Procuradoria Regional/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/motorista\/detalhe\?id=/);
  await expect(page.getByText('Chegada real')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Acompanhamento da viagem' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Registrar/ })).toHaveCount(0);
});

test('menu Minhas viagens só aparece quando a API libera o perfil', async ({ page }) => {
  await mockarApi(page);
  await entrar(page, 'solicitante@exemplo.com');
  await expect(page.getByRole('link', { name: 'Requisições' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Minhas viagens' })).toHaveCount(0);
});

test('autorização: seletor de motorista vem da API e o cartão exibe o motorista', async ({
  page,
}) => {
  const mock = await mockarApi(page, { sementes: [{ status: 'solicitada' }] });
  await page.route('**/api/motoristas', (route) =>
    route.fulfill({
      json: {
        itens: [
          { id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', nome: 'Carlos Lima' },
          { id: 'ffffffff-ffff-4fff-8fff-ffffffffffff', nome: 'Rita Alves' },
        ],
      },
    }),
  );
  await entrar(page, 'autorizador@exemplo.com');
  await page.getByRole('link', { name: 'Autorizar' }).click();

  await page.getByLabel('Motorista (opcional)').selectOption({ label: 'Carlos Lima' });
  await page.getByRole('button', { name: 'Autorizar' }).click();
  await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
  await expect(page.getByText('Motorista: Carlos Lima')).toBeVisible();
  expect(mock.corposAcao[0]).toEqual({
    acao: 'autorizar',
    corpo: { motorista_id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee' },
  });
});

test('autorização: sem /api/motoristas (recurso desligado) não há seletor', async ({ page }) => {
  await mockarApi(page, { sementes: [{ status: 'solicitada' }] });
  await entrar(page, 'autorizador@exemplo.com');
  await page.getByRole('link', { name: 'Autorizar' }).click();
  await expect(page.getByRole('button', { name: 'Autorizar' })).toBeVisible();
  await expect(page.getByLabel('Motorista (opcional)')).toHaveCount(0);
});

test('configurações: interruptores do Perfil Motorista seguem acoes_permitidas da API', async ({
  page,
}) => {
  const mock = await mockarAdmin(page);
  await page.goto('/login');
  await page.getByLabel('E-mail').fill('admin@agu.gov.br');
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.goto('/admin/configuracoes');

  const perfil = page.getByRole('switch', { name: 'Perfil Motorista' });
  const registra = page.getByRole('switch', { name: 'Motorista registra saída e chegada' });
  await expect(page.getByText('Ligue antes o Perfil Motorista.')).toBeVisible();
  await expect(registra).toBeDisabled();

  await perfil.click();
  await expect(perfil).toHaveAttribute('aria-checked', 'true');
  await expect(registra).toBeEnabled();
  await registra.click();
  await expect(registra).toHaveAttribute('aria-checked', 'true');
  expect(mock.corpos.config).toEqual([{ motorista: true }, { motorista_registra: true }]);
});

const LOCAL = { latitude: -15.7939, longitude: -47.8828 };
const ID_AUTORIZADA = '44444444-4444-4444-8444-444444444441';
const ID_CONCLUIDA = '44444444-4444-4444-8444-444444444442';

test('localização: com a permissão, saída e chegada enviam as coordenadas pedidas pela API', async ({
  page,
  context,
}) => {
  await context.grantPermissions(['geolocation']);
  await context.setGeolocation({ ...LOCAL, accuracy: 12 });
  const mock = await entrarMotorista(page, { localizacao: true });

  await expect(
    page.getByText(
      'Sua localização será registrada neste momento e visível apenas ao administrador.',
    ),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Registrar saída' }).click();
  await expect(page.getByRole('heading', { name: /Em andamento · 1/ })).toBeVisible();
  await page.getByRole('button', { name: 'Registrar chegada agora' }).click();
  await expect(page.getByRole('heading', { name: /Concluídas · 2/ })).toBeVisible();

  expect(mock.corposAcao.map((c) => c.acao)).toEqual(['saida', 'chegada']);
  for (const { corpo } of mock.corposAcao) {
    expect(corpo.latitude).toBeCloseTo(LOCAL.latitude, 4);
    expect(corpo.longitude).toBeCloseTo(LOCAL.longitude, 4);
    expect(typeof corpo.precisao_m).toBe('number');
  }
});

test('localização: sem permissão nada é enviado, a mensagem aparece e dá para tentar de novo', async ({
  page,
  context,
}) => {
  const mock = await entrarMotorista(page, { localizacao: true });

  await page.getByRole('button', { name: 'Registrar saída' }).click();
  await expect(
    page.getByRole('alert').filter({ hasText: 'Não foi possível obter sua localização.' }),
  ).toContainText('Permita o acesso à localização do aparelho e tente novamente.');
  expect(mock.acoesRecebidas).toEqual([]);
  expect(mock.corposAcao).toEqual([]);

  await context.grantPermissions(['geolocation']);
  await context.setGeolocation(LOCAL);
  await page.getByRole('button', { name: 'Tentar novamente' }).click();
  await expect(page.getByRole('heading', { name: /Em andamento · 1/ })).toBeVisible();
  expect(mock.acoesRecebidas).toEqual(['saida']);
});

test('localização: a API não pede coordenadas, o aparelho não é consultado', async ({
  page,
  context,
}) => {
  // Sem permissão concedida: se o front consultasse a posição, a ação falharia.
  void context;
  const mock = await entrarMotorista(page);
  await expect(page.getByText(/visível apenas ao administrador/)).toHaveCount(0);
  await page.getByRole('button', { name: 'Registrar saída' }).click();
  await expect(page.getByRole('heading', { name: /Em andamento · 1/ })).toBeVisible();
  expect(mock.corposAcao[0]?.corpo).toEqual({});
});

test('localização: seção do detalhe só aparece se a API devolver os dados', async ({ page }) => {
  await entrarMotorista(page, {
    localizacoesAdmin: [
      {
        momento: 'saida',
        latitude: -15.7939,
        longitude: -47.8828,
        precisao_m: 12.4,
        registrado_em: '2026-11-01T12:10:00Z',
        registrado_por_nome: 'Carlos Lima',
      },
      {
        momento: 'chegada',
        latitude: -15.8,
        longitude: -47.9,
        precisao_m: null,
        registrado_em: '2026-11-01T14:00:00Z',
        registrado_por_nome: 'Carlos Lima',
      },
    ],
  });
  await page.goto(`/requisicoes/detalhe?id=${ID_CONCLUIDA}`);
  const secao = page.getByRole('region', { name: 'Localização do registro' });
  await expect(secao).toBeVisible();
  await expect(secao.getByText('Registro de saída')).toBeVisible();
  await expect(secao.getByText('Precisão: ±12 m')).toBeVisible();
  const link = secao.getByRole('link', { name: 'Abrir no mapa' }).first();
  await expect(link).toHaveAttribute(
    'href',
    'https://www.openstreetmap.org/?mlat=-15.7939&mlon=-47.8828#map=17/-15.7939/-47.8828',
  );
  await expect(link).toHaveAttribute('target', '_blank');
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  await expect(page.locator('iframe')).toHaveCount(0);
});

test('localização: 403 da API (não admin) deixa o detalhe sem a seção', async ({ page }) => {
  await entrarMotorista(page);
  await page.goto(`/requisicoes/detalhe?id=${ID_CONCLUIDA}`);
  await expect(page.getByRole('heading', { name: 'Linha do tempo' })).toBeVisible();
  await expect(page.getByText('Localização do registro')).toHaveCount(0);
});

test('atribuir_motorista: seletor aparece só com a ação da API e troca via POST', async ({
  page,
}) => {
  const mock = await entrarMotorista(page, { trocarMotorista: true });
  await page.goto(`/requisicoes/detalhe?id=${ID_AUTORIZADA}`);
  await expect(page.getByText('Motorista: Carlos Lima')).toBeVisible();
  await page.getByLabel('Trocar motorista').selectOption({ label: 'Rita Alves' });
  await page.getByRole('button', { name: 'Salvar motorista' }).click();
  await expect(page.getByText('Motorista: Rita Alves')).toBeVisible();
  expect(mock.motoristasAtribuidos).toEqual([
    { motorista_id: 'ffffffff-ffff-4fff-8fff-ffffffffffff' },
  ]);
});

test('atribuir_motorista: sem a ação em acoes_permitidas não há seletor', async ({ page }) => {
  await entrarMotorista(page);
  await page.goto(`/requisicoes/detalhe?id=${ID_AUTORIZADA}`);
  await expect(page.getByRole('button', { name: 'Registrar saída' })).toBeVisible();
  await expect(page.getByLabel('Trocar motorista')).toHaveCount(0);
});

test('configurações: interruptor de localização depende de "Motorista registra" (itens da API)', async ({
  page,
}) => {
  const mock = await mockarAdmin(page);
  await page.goto('/login');
  await page.getByLabel('E-mail').fill('admin@agu.gov.br');
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.goto('/admin/configuracoes');

  const loc = page.getByRole('switch', { name: 'Localização no registro do motorista' });
  await expect(loc).toBeDisabled();
  await page.getByRole('switch', { name: 'Perfil Motorista' }).click();
  await page.getByRole('switch', { name: 'Motorista registra saída e chegada' }).click();
  await expect(loc).toBeEnabled();
  await loc.click();
  await expect(loc).toHaveAttribute('aria-checked', 'true');
  expect(mock.corpos.config.at(-1)).toEqual({ motorista_localizacao: true });
});
