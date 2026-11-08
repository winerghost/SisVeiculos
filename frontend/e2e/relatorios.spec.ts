import { expect, test, type Page } from '@playwright/test';
import { mockarApi } from './mock-api';
import { mockarRelatorios, type OpcoesRelatorios } from './mock-relatorios';

async function entrar(page: Page, email: string) {
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
}

async function abrir(page: Page, papel: 'autorizador' | 'solicitante', o?: OpcoesRelatorios) {
  await mockarApi(page);
  const rel = await mockarRelatorios(page, o);
  await entrar(page, `${papel}@exemplo.com`);
  await page.getByRole('link', { name: 'Relatórios' }).click();
  await expect(page.getByRole('heading', { name: 'Relatórios', level: 1 })).toBeVisible();
  return rel;
}

test('menu Relatórios só aparece quando a API libera', async ({ page }) => {
  await mockarApi(page); // sem rota de relatórios: a chamada falha e o item não aparece
  await entrar(page, 'autorizador@exemplo.com');
  await expect(page.getByRole('link', { name: 'Requisições' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Relatórios' })).toHaveCount(0);
});

test('autorizador e solicitante veem catálogos diferentes (definidos pela API)', async ({
  page,
}) => {
  await abrir(page, 'autorizador');
  await expect(page.getByLabel('Relatório', { exact: true }).locator('option')).toHaveText([
    'Movimentação de veículos',
    'Pendências',
    'Uso dos veículos',
  ]);
  // Menu inferior com 5 itens cabe na tela e mantém alvos de 44px.
  for (const nome of ['Requisições', 'Autorizar', 'Viagem', 'Dashboards', 'Relatórios']) {
    const caixa = await page.getByRole('link', { name: nome }).boundingBox();
    expect(caixa!.height).toBeGreaterThanOrEqual(44);
    expect(caixa!.x + caixa!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  }
});

test('solicitante: catálogo reduzido e sem botões de canais desligados', async ({ page }) => {
  await abrir(page, 'solicitante');
  await expect(page.getByLabel('Relatório', { exact: true }).locator('option')).toHaveText([
    'Movimentação de veículos',
    'Pendências',
  ]);
  await expect(page.getByRole('button', { name: 'Baixar CSV' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Baixar XLSX' })).toBeVisible();
  await expect(page.getByRole('button', { name: /e-mail/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Compartilhar' })).toHaveCount(0);
});

test('prévia em tabela acessível, com filtros do catálogo e relatório sem filtros', async ({
  page,
}) => {
  const rel = await abrir(page, 'autorizador');
  await page.getByRole('button', { name: 'Filtros' }).click();
  const gaveta = page.getByRole('dialog');
  await gaveta.getByLabel('Início').fill('2026-11-01');
  await gaveta.getByLabel('Unidade').selectOption('UEA');
  await gaveta.getByRole('switch', { name: 'Incluir canceladas' }).click();
  await gaveta.getByRole('button', { name: 'Aplicar' }).click();
  await expect(page.getByLabel('Filtros aplicados')).toHaveText(
    'Início: 01/11/2026 · Unidade: UEA · Incluir canceladas: Sim',
  );

  await page.getByRole('button', { name: 'Visualizar' }).click();
  const tabela = page.getByRole('table', { name: /Movimentação de veículos: 2 linhas/ });
  await expect(tabela).toBeVisible();
  await expect(tabela.getByRole('columnheader')).toHaveText(['Data', 'Solicitante', 'Situação']);
  await expect(tabela.getByRole('row')).toHaveCount(3);
  await expect(page.getByText('Marcos Silva')).toBeVisible();
  await expect(page.getByRole('note')).toHaveCount(0);
  expect(rel.consultas.at(-1)).toEqual({
    rota: 'movimentacao/previa',
    query: { inicio: '2026-11-01', unidade: 'UEA', incluir_canceladas: 'true' },
  });

  // Trocar de relatório limpa a prévia; relatório sem filtros avisa.
  await page.getByLabel('Relatório', { exact: true }).selectOption('pendencias');
  await expect(tabela).toHaveCount(0);
  await expect(page.getByText('Este relatório não tem filtros.')).toBeVisible();
});

test('prévia truncada mostra aviso', async ({ page }) => {
  await abrir(page, 'autorizador', { truncado: true });
  await page.getByRole('button', { name: 'Visualizar' }).click();
  await expect(page.getByRole('note')).toHaveText(
    'Mostrando as primeiras 2 linhas. Baixe o arquivo para ver tudo.',
  );
});

test('arquivo grande demais: exibe a mensagem 422 do servidor', async ({ page }) => {
  await abrir(page, 'autorizador', { muitoGrande: true });
  await page.getByRole('button', { name: 'Baixar CSV' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'passa de 5000' })).toHaveText(
    'O relatório passa de 5000 linhas; estreite os filtros.',
  );
});

test('e-mail: lista parcial de destinatários e erros 422/429 exibidos como vieram', async ({
  page,
}) => {
  await abrir(page, 'autorizador', {
    email: true,
    destinatariosParcial: true,
    erroEmail: {
      status: 429,
      codigo: 'cota_envio_excedida',
      mensagem: 'Limite diário de envios atingido.',
    },
  });
  await page.getByRole('button', { name: 'Enviar por e-mail' }).click();
  const gaveta = page.getByRole('dialog');
  await expect(
    gaveta.getByText('Lista parcial; use um destinatário da lista exibida.'),
  ).toBeVisible();
  await gaveta.getByLabel('Destinatário').selectOption('dest-1');
  await gaveta.getByRole('button', { name: 'Enviar por e-mail' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Limite diário' })).toHaveText(
    'Limite diário de envios atingido.',
  );
});

test('menu com 6 itens (com Admin) cabe em 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockarApi(page, { comAdmin: true });
  await mockarRelatorios(page);
  await entrar(page, 'autorizador@exemplo.com');
  for (const nome of ['Requisições', 'Autorizar', 'Viagem', 'Dashboards', 'Relatórios', 'Admin']) {
    const caixa = await page.getByRole('link', { name: nome }).boundingBox();
    expect(caixa!.height).toBeGreaterThanOrEqual(44);
    expect(caixa!.width).toBeGreaterThanOrEqual(44);
    expect(caixa!.x + caixa!.width).toBeLessThanOrEqual(390);
  }
  const rotulo = page.getByRole('link', { name: 'Requisições' });
  const sobra = await rotulo.evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(sobra).toBeLessThanOrEqual(0);
});

test('download autenticado usa o nome do Content-Disposition', async ({ page }) => {
  const rel = await abrir(page, 'autorizador');
  const [csv] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: 'Baixar CSV' }).click(),
  ]);
  expect(csv.suggestedFilename()).toBe('movimentacao_2026-11-08.csv');
  await expect(page.getByText('Arquivo "movimentacao_2026-11-08.csv" baixado.')).toBeVisible();
  expect(rel.consultas.at(-1)?.query).toMatchObject({ formato: 'csv', via: 'download' });

  const [xlsx] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: 'Baixar XLSX' }).click(),
  ]);
  expect(xlsx.suggestedFilename()).toBe('movimentacao_2026-11-08.xlsx');
});

test('erro 422 do arquivo é exibido como veio', async ({ page }) => {
  await abrir(page, 'autorizador');
  await page.getByRole('button', { name: 'Filtros' }).click();
  const gaveta = page.getByRole('dialog');
  await gaveta.getByLabel('Início').fill('2026-11-10');
  await gaveta.getByLabel('Fim').fill('2026-11-01');
  await gaveta.getByRole('button', { name: 'Aplicar' }).click();
  await page.getByRole('button', { name: 'Baixar CSV' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'A data final' })).toHaveText(
    'A data final deve ser posterior à inicial.',
  );
});

test('filtro obrigatório usa o atributo nativo required', async ({ page }) => {
  await abrir(page, 'autorizador');
  await page.getByLabel('Relatório', { exact: true }).selectOption('uso_veiculos');
  await page.getByRole('button', { name: 'Filtros' }).click();
  await expect(page.getByRole('dialog').getByLabel(/Início/)).toHaveAttribute('required', '');
});

test('compartilhar: usa navigator.share com arquivo quando o aparelho permite', async ({
  page,
}) => {
  await page.addInitScript(() => {
    const w = window as unknown as { __compartilhado: string[] };
    w.__compartilhado = [];
    Object.defineProperty(navigator, 'canShare', { value: () => true, configurable: true });
    Object.defineProperty(navigator, 'share', {
      value: async (d: { files: File[] }) => {
        w.__compartilhado.push(...d.files.map((f) => `${f.name}|${f.type}`));
      },
      configurable: true,
    });
  });
  const rel = await abrir(page, 'autorizador', { whatsapp: true });
  await page.getByRole('button', { name: 'Compartilhar' }).click();
  const gaveta = page.getByRole('dialog');
  await gaveta.getByLabel('Formato').selectOption('xlsx');
  await gaveta.getByRole('button', { name: 'Compartilhar' }).click();
  await expect(page.getByText('Relatório compartilhado.')).toBeVisible();
  expect(rel.consultas.at(-1)?.query).toMatchObject({ formato: 'xlsx', via: 'compartilhar' });
  const enviados = await page.evaluate(
    () => (window as unknown as { __compartilhado: string[] }).__compartilhado,
  );
  expect(enviados).toHaveLength(1);
  expect(enviados[0]).toContain('movimentacao_2026-11-08.xlsx|');
});

test('compartilhar: sem suporte a arquivos, avisa para usar Baixar e não gera o arquivo', async ({
  page,
}) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'canShare', { value: undefined, configurable: true });
    Object.defineProperty(navigator, 'share', { value: undefined, configurable: true });
  });
  const rel = await abrir(page, 'autorizador', { whatsapp: true });
  await page.getByRole('button', { name: 'Compartilhar' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Compartilhar' }).click();
  await expect(
    page.getByText('Este aparelho não permite compartilhar arquivos; use Baixar'),
  ).toBeVisible();
  expect(rel.consultas.filter((c) => c.rota.endsWith('arquivo'))).toHaveLength(0);
});

test('e-mail: envia, acompanha até "enviado"', async ({ page }) => {
  const rel = await abrir(page, 'autorizador', { email: true });
  await page.getByRole('button', { name: 'Enviar por e-mail' }).click();
  const gaveta = page.getByRole('dialog');
  await gaveta.getByLabel('Destinatário').selectOption({ label: 'Carlos Lima' });
  await gaveta.getByLabel('Formato').selectOption('xlsx');
  await gaveta.getByRole('button', { name: 'Enviar por e-mail' }).click();

  await expect(page.getByRole('status').filter({ hasText: 'Envio na fila' })).toBeVisible();
  await expect(page.getByText('Relatório enviado por e-mail.')).toBeVisible({ timeout: 15_000 });
  expect(rel.corposEmail).toEqual([
    {
      formato: 'xlsx',
      destinatario_id: 'dest-2',
      filtros: { incluir_canceladas: false },
    },
  ]);
});

test('e-mail: falha mostra a mensagem recebida da API', async ({ page }) => {
  await abrir(page, 'autorizador', { email: true, envioFalha: true });
  await page.getByRole('button', { name: 'Enviar por e-mail' }).click();
  const gaveta = page.getByRole('dialog');
  await gaveta.getByLabel('Destinatário').selectOption('dest-1');
  await gaveta.getByRole('button', { name: 'Enviar por e-mail' }).click();
  await expect(page.getByText('Não foi possível enviar o e-mail.')).toBeVisible({
    timeout: 15_000,
  });
  await expect(
    page.getByText('O servidor de e-mail está indisponível. Tente mais tarde.'),
  ).toBeVisible();
});

test('exportar painel leva a /relatorios pré-preenchido (só painéis mapeados)', async ({
  page,
}) => {
  const rel = await (async () => {
    await mockarApi(page);
    const r = await mockarRelatorios(page);
    await entrar(page, 'autorizador@exemplo.com');
    return r;
  })();
  await page.getByRole('link', { name: 'Dashboards' }).click();
  await expect(page.getByRole('link', { name: 'Exportar este painel' })).toHaveCount(0); // operação

  await page.getByRole('tab', { name: 'Tempos' }).click();
  await expect(page.getByRole('button', { name: 'Filtros' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Exportar este painel' })).toHaveCount(0);

  await page.getByRole('tab', { name: 'Volume' }).click();
  await page.getByRole('link', { name: 'Exportar este painel' }).click();

  await expect(page).toHaveURL(/\/relatorios\?/);
  const url = new URL(page.url());
  expect(url.searchParams.get('tipo')).toBe('movimentacao');
  expect(url.searchParams.get('inicio')).toBe('2026-10-10');
  expect(url.searchParams.get('fim')).toBe('2026-11-08');
  expect(url.searchParams.get('situacao')).toBe('concluidas');

  await expect(page.getByLabel('Relatório', { exact: true })).toHaveValue('movimentacao');
  await expect(page.getByLabel('Filtros aplicados')).toHaveText(
    'Início: 10/10/2026 · Fim: 08/11/2026 · Situação: Somente concluídas · Incluir canceladas: Não',
  );
  await page.getByRole('button', { name: 'Visualizar' }).click();
  await expect(page.getByRole('table')).toBeVisible();
  expect(rel.consultas.at(-1)?.query).toMatchObject({
    inicio: '2026-10-10',
    fim: '2026-11-08',
    situacao: 'concluidas',
  });
});
