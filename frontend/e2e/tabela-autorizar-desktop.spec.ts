import { expect, test, type Page } from '@playwright/test';
import { mockarApi, type Opcoes } from './mock-api';

// Desktop (>= md): ações direto na célula e modal "Autorizar requisição" (API mockada).
test.use({ viewport: { width: 1280, height: 800 } });

async function entrar(page: Page, opcoes: Opcoes = {}) {
  const mock = await mockarApi(page, opcoes);
  await page.goto('/login');
  await page.getByLabel('E-mail').fill('autorizador@exemplo.com');
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.getByRole('link', { name: 'Requisições' }).click();
  await expect(page.getByRole('heading', { name: 'Requisições', level: 1 })).toBeVisible();
  return mock;
}

test('Autorizar só abre o modal; Voltar não autoriza; Confirmar envia o motorista', async ({
  page,
}) => {
  const mock = await entrar(page, {
    sementes: [{ status: 'solicitada' }],
    motoristas: [
      { id: 'm1', nome: 'Douglas' },
      { id: 'm2', nome: 'Paulo' },
    ],
  });
  await expect(page.getByRole('columnheader', { name: 'Ações' })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Negar requisição/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Mais ações/ })).toBeVisible();

  await page.getByRole('button', { name: /^Autorizar requisição de/ }).click();
  const modal = page.getByRole('dialog', { name: 'Autorizar requisição' });
  await expect(modal).toBeVisible();
  await expect(modal.getByText('Solicitante:')).toBeVisible();
  await expect(modal.getByRole('button', { name: 'Fechar' })).toBeVisible();
  expect(mock.corposAcao).toHaveLength(0);

  await modal.getByRole('button', { name: 'Voltar' }).click();
  await expect(modal).toBeHidden();
  expect(mock.corposAcao).toHaveLength(0);

  await page.getByRole('button', { name: /^Autorizar requisição de/ }).click();
  await modal.getByLabel(/Motorista/).selectOption('m2');
  await modal.getByRole('button', { name: 'Confirmar autorização' }).click();
  await expect(modal).toBeHidden();
  expect(mock.corposAcao).toEqual([{ acao: 'autorizar', corpo: { motorista_id: 'm2' } }]);
});

for (const [status, mensagem] of [
  [409, 'Outro autorizador já decidiu esta requisição.'],
  [500, 'Falha interna ao autorizar.'],
] as const) {
  test(`erro ${status} ao autorizar: modal fica aberto com a mensagem e nova tentativa funciona`, async ({
    page,
  }) => {
    const mock = await entrar(page, {
      sementes: [{ status: 'solicitada' }],
      motoristas: [
        { id: 'm1', nome: 'Douglas' },
        { id: 'm2', nome: 'Paulo' },
      ],
    });
    // Registrada depois do mock: tem precedência; a 1ª chamada falha e as demais seguem ao mock.
    let chamadas = 0;
    await page.route(/\/api\/requisicoes\/[^/?]+\/autorizar(\?.*)?$/, (route) => {
      chamadas += 1;
      if (chamadas === 1) {
        return route.fulfill({
          status,
          json: { erro: { codigo: 'falha_teste', mensagem } },
        });
      }
      return route.fallback();
    });

    await page.getByRole('button', { name: /^Autorizar requisição de/ }).click();
    const modal = page.getByRole('dialog', { name: 'Autorizar requisição' });
    await modal.getByLabel(/Motorista/).selectOption('m2');
    await modal.getByRole('button', { name: 'Confirmar autorização' }).click();

    await expect(modal.getByText(mensagem)).toBeVisible();
    await expect(modal).toBeVisible();
    await expect(modal.getByLabel(/Motorista/)).toHaveValue('m2');
    await expect(modal.getByRole('button', { name: 'Confirmar autorização' })).toBeEnabled();
    await expect(modal.getByRole('button', { name: 'Voltar' })).toBeEnabled();

    await modal.getByRole('button', { name: 'Confirmar autorização' }).click();
    await expect(modal).toBeHidden();
    expect(chamadas).toBe(2);
    expect(mock.corposAcao).toEqual([{ acao: 'autorizar', corpo: { motorista_id: 'm2' } }]);
  });
}

test('Negar abre a confirmação existente', async ({ page }) => {
  await entrar(page, { sementes: [{ status: 'solicitada' }] });
  await page.getByRole('button', { name: /^Negar requisição de/ }).click();
  await expect(page.getByRole('alertdialog', { name: 'Negar esta requisição?' })).toBeVisible();
  await page.getByRole('button', { name: 'Voltar' }).click();
  await expect(page.getByRole('alertdialog')).toBeHidden();
});

test('Ver detalhes em requisição concluída leva ao detalhe', async ({ page }) => {
  await entrar(page, { sementes: [{ status: 'concluida' }] });
  await expect(page.getByRole('button', { name: /^Autorizar requisição de/ })).toHaveCount(0);
  await page.getByRole('link', { name: /^Ver detalhes da requisição de/ }).click();
  await expect(page).toHaveURL(/\/requisicoes\/detalhe\?id=/);
});

test('Arquivar fica acessível pelo Mais, dentro do diálogo', async ({ page }) => {
  const mock = await entrar(page, { sementes: [{ status: 'concluida' }] });
  // Fora do diálogo não há botão Arquivar na linha.
  await expect(page.getByRole('button', { name: 'Arquivar' })).toHaveCount(0);
  await page.getByRole('button', { name: /^Mais ações da requisição de/ }).click();
  const dialogo = page.getByRole('dialog', { name: 'Mais ações' });
  await expect(dialogo).toBeVisible();
  await dialogo.getByRole('button', { name: 'Arquivar' }).click();
  await expect(page.getByRole('status').getByText('Requisição arquivada.')).toBeVisible();
  expect(mock.organizacoes.map((o) => o.acao)).toEqual(['arquivar']);
});

test('não há linhas extras de ações (nenhum td colspan) nem aria-expanded nas ações', async ({
  page,
}) => {
  await entrar(page, {
    sementes: [{ status: 'solicitada' }, { status: 'concluida' }, { status: 'negada' }],
  });
  await expect(page.locator('tbody tr')).toHaveCount(3);
  await expect(page.locator('tbody td[colspan]')).toHaveCount(0);
  await expect(page.locator('tbody [aria-expanded]')).toHaveCount(0);
});

test.describe('mobile (390px)', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('continua em cartões, sem tabela nem botões "Mais"', async ({ page }) => {
    await entrar(page, { sementes: [{ status: 'solicitada' }, { status: 'concluida' }] });
    await expect(page.locator('ul.lista-animada > li')).toHaveCount(2);
    await expect(page.getByRole('table')).toHaveCount(0);
    await expect(page.getByRole('columnheader', { name: 'Ações' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^Mais ações/ })).toHaveCount(0);
  });
});
