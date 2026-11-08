import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockarApi } from './mock-api';
import { mockarAdmin, type OpcoesAdmin } from './mock-admin';

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);
const arquivo = { name: 'eu.png', mimeType: 'image/png', buffer: PNG };

async function entrar(page: Page, email: string, opcoes: Parameters<typeof mockarApi>[1] = {}) {
  const mock = await mockarApi(page, opcoes);
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  return mock;
}

const conta = (page: Page) => page.getByRole('link', { name: /^Conta:/ });
const carregada = (img: Locator) =>
  expect.poll(() => img.evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0));

test('avatar: foto no cabeçalho e nos cartões de requisição, com nome visível (alt vazio)', async ({
  page,
}) => {
  await entrar(page, 'autorizador@exemplo.com', {
    comFoto: true,
    sementes: [{ status: 'solicitada' }],
  });
  const foto = conta(page).locator('img');
  await expect(foto).toBeVisible();
  await carregada(foto).toBe(true);
  await expect(foto).toHaveAttribute('alt', '');

  await page.getByRole('link', { name: 'Autorizar' }).click();
  const cartao = page.getByRole('listitem').filter({ hasText: 'Marcos Silva' });
  await expect(cartao.locator('img').first()).toBeVisible();
  await expect(cartao.getByText('Marcos Silva')).toBeVisible();

  // Detalhe: solicitante com avatar.
  await cartao.getByRole('link').first().click();
  await expect(page.getByText(/Solicitante: Marcos Silva/)).toBeVisible();
  await expect(
    page
      .getByText(/Solicitante: Marcos Silva/)
      .locator('xpath=..')
      .locator('img'),
  ).toBeVisible();
});

test('avatar: sem foto mostra a inicial; imagem que falha cai para a inicial', async ({ page }) => {
  await entrar(page, 'solicitante@exemplo.com', { comFoto: true, fotoQuebrada: true });
  await expect(conta(page)).toBeVisible();
  await expect(conta(page).locator('img')).toHaveCount(0);
  await expect(conta(page)).toContainText('M');
});

test('/seguranca: consentimento, pré-visualização, envio reprocessado e cabeçalho atualizado', async ({
  page,
}) => {
  const mock = await entrar(page, 'solicitante@exemplo.com');
  await expect(conta(page).locator('img')).toHaveCount(0);
  await conta(page).click();

  await expect(
    page.getByText(
      'A foto é opcional e serve apenas para identificar você no sistema. Ela não é usada para reconhecimento facial. Você pode remover a qualquer momento.',
    ),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Remover foto' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Enviar foto' })).toBeDisabled();
  await expect(page.locator('#foto-arquivo')).toHaveAttribute(
    'accept',
    'image/jpeg,image/png,image/webp',
  );

  await page.locator('#foto-arquivo').setInputFiles(arquivo);
  await expect(page.getByAltText('Pré-visualização da nova foto')).toBeVisible();
  await expect(page.getByText('Pronta para enviar.')).toBeVisible();

  await page.getByRole('button', { name: 'Enviar foto' }).click();
  await expect(page.getByText('Foto atualizada.')).toBeVisible();

  // upload-url com o mime/tamanho do arquivo reprocessado (JPEG, bem menor que 1 MB).
  expect(mock.pedidosFotoPerfil).toHaveLength(1);
  const pedido = mock.pedidosFotoPerfil[0] as { mime: string; tamanho: number };
  expect(pedido.mime).toBe('image/jpeg');
  expect(pedido.tamanho).toBeGreaterThan(0);
  expect(pedido.tamanho).toBeLessThan(1_048_576);
  // Upload ao staging do próprio usuário e confirmação com o id devolvido.
  expect(mock.uploadsAvatar).toEqual([
    'tmp/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/abababab-abab-4bab-8bab-abababababab',
  ]);
  expect(mock.confirmacoesFoto).toEqual([{ id: 'abababab-abab-4bab-8bab-abababababab' }]);

  // Cabeçalho (/api/eu recarregado) e botão Remover aparecem.
  await expect(page.getByAltText('Foto de Marcos Silva')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Remover foto' })).toBeVisible();
  await page.getByRole('link', { name: 'Voltar' }).click();
  await expect(conta(page).locator('img')).toBeVisible();
  // A URL assinada não vai para a URL da página nem para o título.
  expect(page.url()).not.toContain('token');
  expect(await page.title()).not.toContain('token');
});

test('/seguranca: erro da API é exibido como veio e a pré-visualização é mantida', async ({
  page,
}) => {
  await entrar(page, 'solicitante@exemplo.com', {
    erroConfirmacao: {
      status: 400,
      codigo: 'foto_invalida',
      mensagem: 'O arquivo enviado não é uma imagem válida (use JPEG, PNG ou WebP).',
    },
  });
  await conta(page).click();
  await page.locator('#foto-arquivo').setInputFiles(arquivo);
  await page.getByRole('button', { name: 'Enviar foto' }).click();
  await expect(
    page
      .getByRole('alert')
      .filter({ hasText: 'O arquivo enviado não é uma imagem válida (use JPEG, PNG ou WebP).' }),
  ).toBeVisible();
  await expect(page.getByAltText('Pré-visualização da nova foto')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Enviar foto' })).toBeEnabled();
});

test('/seguranca: Remover só aparece com foto e limpa o avatar', async ({ page }) => {
  await entrar(page, 'solicitante@exemplo.com', { comFoto: true });
  await expect(conta(page).locator('img')).toBeVisible();
  await conta(page).click();
  await page.getByRole('button', { name: 'Remover foto' }).click();
  await expect(page.getByText('Foto removida.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Remover foto' })).toHaveCount(0);
  await expect(conta(page).locator('img')).toHaveCount(0);
});

// ---- Administração ----

async function entrarAdmin(page: Page, opcoes: OpcoesAdmin = {}) {
  const mock = await mockarAdmin(page, opcoes);
  await page.goto('/login');
  await page.getByLabel('E-mail').fill('admin@agu.gov.br');
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  return mock;
}

test('admin: avatar na lista e Remover foto só com remover_foto, com confirmação', async ({
  page,
}) => {
  const mock = await entrarAdmin(page, { anaComFoto: true });
  await page.goto('/admin/usuarios');
  const linhaAna = page.getByRole('link', { name: /Ana Souza/ });
  await expect(linhaAna.locator('img')).toBeVisible();
  await expect(page.getByRole('link', { name: /admin@agu/ }).locator('img')).toHaveCount(0);

  // Sem remover_foto (Marcos): sem botão.
  await page.getByRole('link', { name: /admin@agu/ }).click();
  await expect(page.getByRole('heading', { name: 'Marcos Silva' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Remover foto' })).toHaveCount(0);

  await page.goto('/admin/usuarios');
  await page.getByRole('link', { name: /Ana Souza/ }).click();
  await page.getByRole('button', { name: 'Remover foto' }).click();
  await page.getByRole('button', { name: 'Cancelar' }).click();
  expect(mock.corpos.fotosRemovidas).toHaveLength(0);
  await page.getByRole('button', { name: 'Remover foto' }).click();
  await page.getByRole('button', { name: 'Confirmar' }).click();
  await expect(page.getByText('Foto removida.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Remover foto' })).toHaveCount(0);
  expect(mock.corpos.fotosRemovidas).toHaveLength(1);
});

test('admin: erro da API na remoção da foto é exibido como veio', async ({ page }) => {
  await entrarAdmin(page, { anaComFoto: true, removerFotoNegado: true });
  await page.goto('/admin/usuarios');
  await page.getByRole('link', { name: /Ana Souza/ }).click();
  await page.getByRole('button', { name: 'Remover foto' }).click();
  await page.getByRole('button', { name: 'Confirmar' }).click();
  await expect(
    page.getByRole('alert').filter({ hasText: 'Você não tem permissão para esta ação.' }),
  ).toBeVisible();
});
