import { expect, test, type Page } from '@playwright/test';
import { mockarApi } from './mock-api';

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);
const foto = (nome: string) => ({ name: nome, mimeType: 'image/png', buffer: PNG });

async function comoAutorizador(page: Page, opcoes: Parameters<typeof mockarApi>[1]) {
  const mock = await mockarApi(page, opcoes);
  await page.goto('/login');
  await page.getByLabel('E-mail').fill('autorizador@exemplo.com');
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  return mock;
}

async function irParaAutorizadas(page: Page) {
  await page.getByRole('link', { name: 'Autorizar' }).click();
  await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
}

test('checklist de saída com avarias: formulário antes do botão, fotos reprocessadas e retry', async ({
  page,
}) => {
  const mock = await comoAutorizador(page, {
    checklist: true,
    falharPrimeiroUpload: true,
    sementes: [{ status: 'autorizada' }],
  });
  await irParaAutorizadas(page);

  // Pendente: só o formulário, sem o botão de saída.
  await expect(page.getByRole('heading', { name: 'Checklist de saída' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Registrar saída' })).toHaveCount(0);
  await expect(page.getByLabel('Descrição das avarias')).toHaveCount(0);

  await page.locator('label', { hasText: '1/2' }).click();
  await page.locator('label', { hasText: 'Há avarias' }).click();
  await expect(page.getByLabel('Descrição das avarias')).toHaveAttribute('maxlength', '500');
  await page.getByLabel('Descrição das avarias').fill('Arranhão na porta direita');
  await page.locator('input[type=file]').setInputFiles([foto('a.png'), foto('b.png')]);
  await expect(page.getByAltText('Pré-visualização de a.png')).toBeVisible();
  await expect(page.getByAltText('Pré-visualização de b.png')).toBeVisible();
  await page.getByRole('button', { name: 'Remover foto b.png' }).click();
  await expect(page.getByAltText('Pré-visualização de b.png')).toHaveCount(0);

  // 1º envio ao Storage falha: erro na foto, formulário preservado, nada registrado.
  await page.getByRole('button', { name: 'Registrar checklist de saída' }).click();
  await expect(page.getByText('Falha ao enviar a foto. Tente novamente.').first()).toBeVisible();
  expect(mock.registrosChecklist).toHaveLength(0);
  await expect(page.getByLabel('Descrição das avarias')).toHaveValue('Arranhão na porta direita');

  // Retry: envia e registra; o pendente some e o botão normal aparece.
  await page.getByRole('button', { name: 'Registrar checklist de saída' }).click();
  await expect(page.getByRole('button', { name: 'Registrar saída' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Checklist de saída' })).toHaveCount(0);

  // Privacidade: o arquivo foi reprocessado para JPEG antes de pedir a URL de upload.
  expect(mock.pedidosUpload[0]).toMatchObject({ mime: 'image/jpeg' });
  const reg = mock.registrosChecklist[0] as Record<string, unknown>;
  expect(reg).toMatchObject({
    combustivel: 'meio',
    avarias: true,
    descricao_avarias: 'Arranhão na porta direita',
  });
  expect((reg.fotos as { mime: string }[])[0]?.mime).toBe('image/jpeg');

  await page.getByRole('button', { name: 'Registrar saída' }).click();
  await expect(page.getByRole('tab', { name: /Em uso · 1/ })).toBeVisible();

  // Viagem: checklist de saída aparece com miniatura, texto pt-BR e quem registrou.
  await page.getByRole('link', { name: 'Viagem' }).click();
  const bloco = page.getByRole('region', { name: 'Checklist de saída' });
  await expect(bloco.getByText('Combustível: 1/2')).toBeVisible();
  await expect(bloco.getByText('Avarias: Sim')).toBeVisible();
  await expect(bloco.getByText('Arranhão na porta direita')).toBeVisible();
  await expect(bloco.getByText(/por Ana Souza/)).toBeVisible();
  const img = bloco.getByRole('img', { name: /Foto 1/ });
  await expect(img).toBeVisible();
  await expect
    .poll(() => img.evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0))
    .toBe(true);

  // Chegada sem avarias, sem fotos.
  await expect(page.getByRole('heading', { name: 'Checklist de chegada' })).toBeVisible();
  await page.locator('label', { hasText: 'Cheio' }).click();
  await page.locator('label', { hasText: 'Sem avarias' }).click();
  await page.getByRole('button', { name: 'Registrar checklist de chegada' }).click();
  await expect(page.getByRole('region', { name: 'Checklist de chegada' })).toBeVisible();
  expect(mock.registrosChecklist[1]).toEqual({
    combustivel: 'cheio',
    avarias: false,
    fotos: [],
  });
  await page.getByRole('button', { name: 'Registrar chegada agora' }).click();
  await expect(page.getByText('Nenhuma viagem em uso.')).toBeVisible();

  // Detalhe mostra os dois checklists.
  await page.getByRole('link', { name: 'Requisições' }).click();
  await page.getByText('Sede -> Fórum').click();
  await expect(page.getByRole('region', { name: 'Checklist de saída' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Checklist de chegada' })).toBeVisible();
});

test('erro da API no registro é exibido como veio e mantém o formulário preenchido', async ({
  page,
}) => {
  await comoAutorizador(page, { checklist: true, sementes: [{ status: 'autorizada' }] });
  await irParaAutorizadas(page);
  await page.locator('label', { hasText: 'Vazio' }).click();
  await page.locator('label', { hasText: 'Há avarias' }).click();
  // Descrição vazia: a API (mock) recusa com checklist_invalido.
  await page.getByRole('button', { name: 'Registrar checklist de saída' }).click();
  await expect(page.getByText('Descreva as avarias em descricao_avarias.')).toBeVisible();
  await expect(page.locator('label', { hasText: 'Vazio' }).locator('input')).toBeChecked();
  await expect(page.getByRole('button', { name: 'Registrar saída' })).toHaveCount(0);
});

test('409 checklist_obrigatorio: mensagem da API e o formulário passa a aparecer', async ({
  page,
}) => {
  await comoAutorizador(page, {
    checklist: true,
    pendenteSoApos409: true,
    sementes: [{ status: 'autorizada' }],
  });
  await irParaAutorizadas(page);
  await expect(page.getByRole('heading', { name: 'Checklist de saída' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Registrar saída' }).click();
  await expect(page.getByText('Registre o checklist antes de registrar a saída')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Checklist de saída' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Registrar saída' })).toHaveCount(0);
});

test('checklist desligado: nada aparece e nenhuma chamada ao checklist é feita', async ({
  page,
}) => {
  const chamadas: string[] = [];
  page.on('request', (r) => {
    if (r.url().includes('/checklist')) chamadas.push(r.url());
  });
  await comoAutorizador(page, { sementes: [{ status: 'autorizada' }] });
  await irParaAutorizadas(page);
  await expect(page.getByRole('button', { name: 'Registrar saída' })).toBeVisible();
  await expect(page.getByRole('heading', { name: /Checklist/ })).toHaveCount(0);
  await expect(page.locator('input[type=file]')).toHaveCount(0);
  await page.getByText('Sede -> Fórum').click();
  await expect(page.getByRole('heading', { name: 'Histórico' })).toBeVisible();
  expect(chamadas).toEqual([]);
});
