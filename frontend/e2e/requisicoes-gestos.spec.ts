import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockarApi, type Opcoes } from './mock-api';

const CARTAO = 'ul.lista-animada > li';
const MOTORISTA_A = { id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', nome: 'Carlos Lima' };
const MOTORISTA_B = { id: 'ffffffff-ffff-4fff-8fff-ffffffffffff', nome: 'Rita Alves' };

async function entrar(page: Page, email: string, opcoes: Opcoes = {}) {
  const mock = await mockarApi(page, opcoes);
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.getByRole('link', { name: 'Requisições' }).click();
  await expect(page.getByRole('heading', { name: 'Requisições', level: 1 })).toBeVisible();
  return mock;
}

/** Arrasta o cartão começando SOBRE o link (o gesto precisa funcionar aí). fracao < 0 = esquerda. */
async function arrastar(page: Page, cartao: Locator, fracao: number) {
  await cartao.scrollIntoViewIfNeeded();
  const li = (await cartao.boundingBox())!;
  const link = (await cartao.getByRole('link').first().boundingBox())!;
  const y = link.y + Math.min(link.height / 2, 24);
  const x0 = link.x + link.width / 2;
  await page.mouse.move(x0, y);
  await page.mouse.down();
  await page.mouse.move(x0 + (li.width * fracao) / 2, y, { steps: 5 });
  await page.mouse.move(x0 + li.width * fracao, y, { steps: 5 });
  await page.mouse.up();
}

test('direita em Solicitada autoriza; abaixo do limiar volta sem chamar a API nem navegar', async ({
  page,
}) => {
  const mock = await entrar(page, 'autorizador@exemplo.com', {
    sementes: [{ status: 'solicitada' }],
  });
  const cartao = page.locator(CARTAO).first();

  await arrastar(page, cartao, 0.3);
  await expect(page).toHaveURL(/\/requisicoes$/);
  expect(mock.corposAcao).toEqual([]);

  // Arrastar sobre o link passou do limiar: executa e NÃO navega para o detalhe.
  await arrastar(page, cartao, 0.7);
  await expect(cartao.getByText('Autorizada', { exact: true }).first()).toBeVisible();
  await expect(page).toHaveURL(/\/requisicoes$/);
  expect(mock.corposAcao.map((c) => c.acao)).toEqual(['autorizar']);
  expect(mock.reqs[0]?.status).toBe('autorizada');
});

test('esquerda em Solicitada abre o diálogo com Negar, Cancelar e Voltar; Voltar não chama a API', async ({
  page,
}) => {
  const mock = await entrar(page, 'autorizador@exemplo.com', {
    sementes: [{ status: 'solicitada' }],
  });
  const cartao = page.locator(CARTAO).first();
  await arrastar(page, cartao, -0.6);

  const dialogo = page.getByRole('alertdialog', { name: 'Negar ou cancelar esta requisição?' });
  await expect(dialogo).toBeVisible();
  await expect(dialogo.getByRole('button', { name: 'Negar', exact: true })).toBeVisible();
  await expect(dialogo.getByRole('button', { name: 'Cancelar requisição' })).toBeVisible();
  await expect(dialogo.getByRole('button', { name: 'Voltar' })).toBeFocused();
  await dialogo.getByRole('button', { name: 'Voltar' }).click();
  await expect(dialogo).toBeHidden();
  expect(mock.corposAcao).toEqual([]);

  // Esc também fecha.
  await arrastar(page, cartao, -0.6);
  await expect(dialogo).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialogo).toBeHidden();

  await arrastar(page, cartao, -0.6);
  await dialogo.getByRole('button', { name: 'Negar', exact: true }).click();
  await expect(cartao.getByText('Negada', { exact: true }).first()).toBeVisible();
  expect(mock.corposAcao.map((c) => c.acao)).toEqual(['negar']);
});

test('botões Negar e Cancelar também passam pelo diálogo de confirmação', async ({ page }) => {
  const mock = await entrar(page, 'autorizador@exemplo.com', {
    sementes: [{ status: 'solicitada' }],
  });
  const cartao = page.locator(CARTAO).first();
  await cartao.getByRole('button', { name: 'Cancelar requisição' }).click();

  const dialogo = page.getByRole('alertdialog', { name: 'Cancelar esta requisição?' });
  await expect(dialogo).toBeVisible();
  // Um único botão de ação: o diálogo só traz a ação que foi clicada.
  await expect(dialogo.getByRole('button', { name: 'Negar' })).toHaveCount(0);
  expect(mock.corposAcao).toEqual([]);
  await dialogo.getByRole('button', { name: 'Cancelar requisição' }).click();
  await expect(cartao.getByText('Cancelada', { exact: true }).first()).toBeVisible();
  expect(mock.corposAcao.map((c) => c.acao)).toEqual(['cancelar']);
});

test('solicitante: o diálogo da esquerda só oferece Cancelar (conforme acoes_permitidas)', async ({
  page,
}) => {
  const mock = await entrar(page, 'solicitante@exemplo.com', {
    sementes: [{ status: 'solicitada' }],
  });
  const cartao = page.locator(CARTAO).first();

  // Sem `autorizar` na lista, a direita não faz nada.
  await arrastar(page, cartao, 0.7);
  expect(mock.corposAcao).toEqual([]);
  await expect(cartao.getByText('Solicitada', { exact: true }).first()).toBeVisible();

  await arrastar(page, cartao, -0.6);
  const dialogo = page.getByRole('alertdialog', { name: 'Cancelar esta requisição?' });
  await expect(dialogo).toBeVisible();
  await expect(dialogo.getByRole('button', { name: 'Negar' })).toHaveCount(0);
  await dialogo.getByRole('button', { name: 'Cancelar requisição' }).click();
  await expect(cartao.getByText('Cancelada', { exact: true }).first()).toBeVisible();
});

test('Concluída: direita arquiva com aviso e Desfazer; botão equivalente sempre visível', async ({
  page,
}) => {
  const mock = await entrar(page, 'autorizador@exemplo.com', {
    sementes: [{ status: 'concluida' }],
  });
  const cartao = page.locator(CARTAO).first();
  await expect(cartao.getByRole('button', { name: 'Arquivar' })).toBeVisible();

  await arrastar(page, cartao, 0.6);
  await expect(page.getByRole('status').getByText('Requisição arquivada.')).toBeVisible();
  await expect(page.getByText('Nenhuma requisição ainda.')).toBeVisible();

  await page.getByRole('button', { name: 'Desfazer' }).click();
  await expect(page.locator(CARTAO)).toHaveCount(1);
  await expect(page.getByRole('status').getByText('Requisição desarquivada.')).toBeVisible();
  expect(mock.organizacoes.map((o) => o.acao)).toEqual(['arquivar', 'desarquivar']);
});

test('Arquivadas: lista só as arquivadas; esquerda e botão desarquivam', async ({ page }) => {
  const mock = await entrar(page, 'autorizador@exemplo.com', {
    sementes: [{ status: 'negada' }, { status: 'cancelada' }],
    arquivadas: [
      { papel: 'autorizador', indice: 0 },
      { papel: 'autorizador', indice: 1 },
    ],
  });
  await expect(page.getByText('Nenhuma requisição ainda.')).toBeVisible();

  await page.getByRole('button', { name: 'Arquivadas' }).click();
  await expect(page.getByRole('button', { name: 'Arquivadas · 2' })).toBeVisible();
  await expect(page.locator(CARTAO)).toHaveCount(2);
  // Sem `arquivar` na lista, a direita não faz nada; a esquerda desarquiva.
  await arrastar(page, page.locator(CARTAO).first(), 0.6);
  expect(mock.organizacoes).toEqual([]);
  await arrastar(page, page.locator(CARTAO).first(), -0.6);
  await expect(page.locator(CARTAO)).toHaveCount(1);
  await expect(page.getByText('Requisição desarquivada.')).toBeVisible();

  await page.getByRole('button', { name: 'Desarquivar' }).click();
  await expect(page.getByText('Nenhuma requisição arquivada.')).toBeVisible();
  expect(mock.organizacoes.map((o) => o.acao)).toEqual(['desarquivar', 'desarquivar']);

  await page.getByRole('button', { name: 'Ativas' }).click();
  await expect(page.locator(CARTAO)).toHaveCount(2);
});

test('arquivar só aparece (gesto e botão) quando a API libera; solicitada nunca', async ({
  page,
}) => {
  const mock = await entrar(page, 'autorizador@exemplo.com', {
    sementes: [{ status: 'solicitada' }, { status: 'autorizada' }],
  });
  await expect(page.getByRole('button', { name: 'Arquivar' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Desarquivar' })).toHaveCount(0);
  expect(mock.organizacoes).toEqual([]);
});

test('sem parada de foco extra nos cartões e alvos de toque com 44 px ou mais', async ({
  page,
}) => {
  await entrar(page, 'autorizador@exemplo.com', { sementes: [{ status: 'concluida' }] });
  await expect(page.locator(`${CARTAO} [tabindex="0"]`)).toHaveCount(0);
  const caixa = (await page.getByRole('button', { name: 'Arquivar' }).boundingBox())!;
  expect(caixa.height).toBeGreaterThanOrEqual(44);
  const aba = (await page.getByRole('button', { name: 'Arquivadas' }).boundingBox())!;
  expect(aba.height).toBeGreaterThanOrEqual(44);
});

test('grupos_acao: botões na ordem fixa, independente da ordem das chaves do JSON', async ({
  page,
}) => {
  await entrar(page, 'autorizador@exemplo.com', {
    checklist: true,
    sementes: [{ status: 'autorizada' }],
  });
  // O mock manda as chaves em ordem alfabética (consultar, decidir, operar). `consultar` só tem
  // ações de leitura (sem botão); decidir vem antes de operar.
  const grupos = await page
    .locator(`${CARTAO} [data-grupo]`)
    .evaluateAll((els) => els.map((e) => e.getAttribute('data-grupo')));
  expect(grupos).toEqual(['decidir', 'operar']);
});

test('autorizar por gesto com mais de um motorista abre a seleção com o padrão pré-selecionado', async ({
  page,
}) => {
  const mock = await entrar(page, 'autorizador@exemplo.com', {
    sementes: [{ status: 'solicitada' }],
    motoristas: [MOTORISTA_A, MOTORISTA_B],
    motoristaPadrao: MOTORISTA_B.id,
  });
  const cartao = page.locator(CARTAO).first();
  await arrastar(page, cartao, 0.7);

  const dialogo = page.getByRole('dialog', { name: 'Autorizar requisição' });
  await expect(dialogo).toBeVisible();
  await expect(dialogo.getByLabel('Motorista')).toHaveValue(MOTORISTA_B.id);
  expect(mock.corposAcao).toEqual([]);

  await dialogo.getByLabel('Motorista').selectOption({ label: 'Carlos Lima' });
  await dialogo.getByRole('button', { name: 'Autorizar' }).click();
  await expect(cartao.getByText('Autorizada', { exact: true }).first()).toBeVisible();
  expect(mock.corposAcao).toEqual([{ acao: 'autorizar', corpo: { motorista_id: MOTORISTA_A.id } }]);
});

test('autorizar por gesto com um só motorista: nome pré-selecionado e autoriza direto', async ({
  page,
}) => {
  const mock = await entrar(page, 'autorizador@exemplo.com', {
    sementes: [{ status: 'solicitada' }],
    motoristas: [MOTORISTA_A],
    motoristaPadrao: MOTORISTA_A.id,
  });
  const cartao = page.locator(CARTAO).first();
  await expect(cartao.getByLabel('Motorista')).toHaveValue(MOTORISTA_A.id);
  await arrastar(page, cartao, 0.7);
  await expect(cartao.getByText('Autorizada', { exact: true }).first()).toBeVisible();
  expect(mock.corposAcao).toEqual([{ acao: 'autorizar', corpo: { motorista_id: MOTORISTA_A.id } }]);
});

test('ver_checklist: sem a ação na lista a tela nem consulta o checklist', async ({ page }) => {
  const consultas: string[] = [];
  page.on('request', (r) => {
    if (/\/api\/requisicoes\/[^/]+\/checklist/.test(r.url())) consultas.push(r.url());
  });
  await entrar(page, 'solicitante@exemplo.com', {
    checklist: true,
    sementes: [{ status: 'autorizada' }],
  });
  await page.locator(CARTAO).first().getByRole('link').first().click();
  await expect(page.getByRole('heading', { name: 'Linha do tempo' })).toBeVisible();
  expect(consultas).toEqual([]);
});

test('teclado: Tab alcança o link e os botões do cartão, sem parada extra', async ({ page }) => {
  await entrar(page, 'autorizador@exemplo.com', { sementes: [{ status: 'solicitada' }] });
  const cartao = page.locator(CARTAO).first();
  await cartao.getByRole('link').first().focus();
  await page.keyboard.press('Tab');
  await expect(cartao.getByRole('button', { name: 'Negar' })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(cartao.getByRole('button', { name: 'Autorizar' })).toBeFocused();
});

test('vigilante: concluída arquiva por gesto e botão quando a API libera, com Desfazer', async ({
  page,
}) => {
  const mock = await entrar(page, 'vigilante@exemplo.com', {
    sementes: [{ status: 'concluida' }],
  });
  const cartao = page.locator(CARTAO).first();
  await expect(cartao.getByRole('button', { name: 'Arquivar' })).toBeVisible();
  await arrastar(page, cartao, 0.6);
  await expect(page.getByRole('status').getByText('Requisição arquivada.')).toBeVisible();
  await page.getByRole('button', { name: 'Desfazer' }).click();
  await expect(page.locator(CARTAO)).toHaveCount(1);
  expect(mock.organizacoes.map((o) => [o.papel, o.acao])).toEqual([
    ['vigilante', 'arquivar'],
    ['vigilante', 'desarquivar'],
  ]);
});
