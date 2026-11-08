import { expect, test, type Page } from '@playwright/test';
import { mockarApi, type Opcoes } from './mock-api';
import { mockarNotificacoes, nova, REQ_ID, type OpcoesNotificacoes } from './mock-notificacoes';

async function entrar(page: Page, email: string) {
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
}

async function abrir(
  page: Page,
  papel: 'autorizador' | 'solicitante',
  n?: OpcoesNotificacoes,
  api?: Opcoes,
) {
  const base = await mockarApi(page, api);
  const mock = await mockarNotificacoes(page, n);
  await entrar(page, `${papel}@exemplo.com`);
  return { base, mock };
}

const sino = (page: Page, rotulo: string) => page.getByRole('link', { name: rotulo });

test('sino com contador para solicitante e autorizador, dentro da tela e com alvo de 44px', async ({
  page,
}) => {
  await abrir(page, 'solicitante', {
    solicitante: [nova(), nova()],
    autorizador: [nova()],
  });
  await expect(sino(page, 'Notificações, 2 não lidas')).toBeVisible();
  const caixa = await sino(page, 'Notificações, 2 não lidas').boundingBox();
  expect(caixa!.width).toBeGreaterThanOrEqual(44);
  expect(caixa!.height).toBeGreaterThanOrEqual(44);
  // Cabeçalho não estoura a largura de 390px: sino, avatar e sair ficam dentro da tela.
  const alvos = [
    page.getByRole('link', { name: 'Notificações, 2 não lidas' }),
    page.getByRole('link', { name: /^Conta:/ }),
    page.getByRole('button', { name: 'Sair' }),
  ];
  for (const alvo of alvos) {
    const c = await alvo.boundingBox();
    expect(c!.x).toBeGreaterThanOrEqual(0);
    expect(c!.x + c!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
    page.viewportSize()!.width,
  );
  // Nada persistente: o contador não vai para o storage.
  const guardado = await page.evaluate(() =>
    JSON.stringify({ ...localStorage, ...sessionStorage }),
  );
  expect(guardado).not.toContain('nao_lidas');
});

test('autorizador também vê o sino com o próprio contador; zerado quando não há não lidas', async ({
  page,
}) => {
  await abrir(page, 'autorizador', { solicitante: [nova(), nova()], autorizador: [nova()] });
  await expect(sino(page, 'Notificações, 1 não lida')).toBeVisible();
});

test('sino sem não lidas não mostra o contador', async ({ page }) => {
  await abrir(page, 'solicitante', { solicitante: [] });
  await expect(sino(page, 'Notificações, 0 não lidas')).toBeVisible();
  await expect(page.getByTestId('contador-notificacoes')).toHaveCount(0);
});

test('lista mais recentes primeiro, com Nova e Importante em texto, e estado vazio', async ({
  page,
}) => {
  await abrir(page, 'solicitante', {
    solicitante: [
      nova({ titulo: 'Antiga lida', lida_em: new Date().toISOString() }, 300),
      nova({ titulo: 'Crítica nova', critica: true }, 2),
      nova({ titulo: 'Recente nova' }, 0),
    ],
  });
  await sino(page, 'Notificações, 2 não lidas').click();
  await expect(page.getByRole('heading', { name: 'Notificações', level: 1 })).toBeVisible();
  const itens = page.getByRole('list', { name: 'Notificações' }).getByRole('listitem');
  await expect(itens).toHaveCount(3);
  await expect(itens.nth(0)).toContainText('Recente nova');
  await expect(itens.nth(0)).toContainText('Nova');
  await expect(itens.nth(0)).toContainText('agora há pouco');
  await expect(itens.nth(1)).toContainText('Crítica nova');
  await expect(itens.nth(1)).toContainText('Importante');
  await expect(itens.nth(1)).toContainText('há 2 min');
  await expect(itens.nth(2)).toContainText('Antiga lida');
  await expect(itens.nth(2)).not.toContainText('Nova');
  await expect(itens.nth(2)).toContainText('há 5 h');
});

test('estado vazio', async ({ page }) => {
  await abrir(page, 'solicitante', { solicitante: [] });
  await page.goto('/notificacoes');
  await expect(page.getByText('Você não tem notificações.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Marcar todas como lidas' })).toHaveCount(0);
});

test('erro ao listar mostra a mensagem recebida', async ({ page }) => {
  await abrir(page, 'solicitante', {
    solicitante: [nova()],
    erro: { status: 500, codigo: 'erro_interno', mensagem: 'Falha ao buscar notificações.' },
  });
  await page.goto('/notificacoes');
  await expect(
    page.getByRole('alert').filter({ hasText: 'Falha ao buscar notificações.' }),
  ).toBeVisible();
});

test('filtro somente não lidas e carregar mais', async ({ page }) => {
  const muitas = Array.from({ length: 25 }, (_, i) => nova({ titulo: `Aviso lote ${i}` }, i + 1));
  const lida = nova({ titulo: 'Já lida', lida_em: new Date().toISOString() }, 0);
  const { mock } = await abrir(page, 'solicitante', { solicitante: [...muitas, lida] });
  await page.goto('/notificacoes');
  const itens = page.getByRole('list', { name: 'Notificações' }).getByRole('listitem');
  await expect(itens).toHaveCount(20);
  await page.getByRole('button', { name: 'Carregar mais' }).click();
  await expect(itens).toHaveCount(26);
  await expect(page.getByRole('button', { name: 'Carregar mais' })).toHaveCount(0);
  expect(mock.consultas.map((c) => c.offset)).toEqual([0, 20]);

  await page.getByRole('switch', { name: 'Somente não lidas' }).click();
  await expect(itens).toHaveCount(20);
  await expect(page.getByText('Já lida')).toHaveCount(0);
  expect(mock.consultas.at(-1)?.somente_nao_lidas).toBe('true');
});

test('tocar marca como lida e abre o detalhe com a linha do tempo completa', async ({ page }) => {
  const agora = new Date().toISOString();
  const { mock } = await abrir(
    page,
    'solicitante',
    {
      solicitante: [
        nova({
          titulo: 'Sua requisição foi autorizada',
          destino: `/requisicoes/detalhe?id=${REQ_ID}`,
        }),
        nova({ titulo: 'Sem destino' }),
      ],
    },
    {
      sementes: [
        {
          status: 'concluida',
          autorizado_em: agora,
          saida_real_em: agora,
          chegada_real_em: agora,
          saida_registrada_por_nome: 'Ana Souza',
          chegada_registrada_por_nome: 'Ana Souza',
        },
      ],
    },
  );
  await sino(page, 'Notificações, 2 não lidas').click();

  // Sem destino: só marca como lida.
  await page.getByRole('button', { name: /Sem destino/ }).click();
  await expect(sino(page, 'Notificações, 1 não lida')).toBeVisible();
  await expect(page).toHaveURL(/\/notificacoes\/?$/);
  expect(mock.marcadas).toHaveLength(1);

  await page.getByRole('button', { name: /Sua requisição foi autorizada/ }).click();
  await expect(page).toHaveURL(new RegExp(`/requisicoes/detalhe/?\\?id=${REQ_ID}`));
  await expect(page.getByRole('heading', { name: 'Linha do tempo' })).toBeVisible();
  await expect(page.locator('ol').getByText('Chegada registrada')).toBeVisible();
  expect(mock.marcadas).toHaveLength(2);
  await expect(sino(page, 'Notificações, 0 não lidas')).toBeVisible();
});

test('marcar todas como lidas', async ({ page }) => {
  const { mock } = await abrir(page, 'autorizador', { autorizador: [nova(), nova(), nova()] });
  await sino(page, 'Notificações, 3 não lidas').click();
  await page.getByRole('button', { name: 'Marcar todas como lidas' }).click();
  await expect(sino(page, 'Notificações, 0 não lidas')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Marcar todas como lidas' })).toHaveCount(0);
  await expect(page.getByText('Nova', { exact: true })).toHaveCount(0);
  expect(mock.state.todasMarcadas).toBe(1);
});

test('polling de 60 s atualiza o contador, só com a aba visível', async ({ page }) => {
  await page.clock.install();
  const { mock } = await abrir(page, 'solicitante', { solicitante: [nova()] });
  await expect(sino(page, 'Notificações, 1 não lida')).toBeVisible();

  mock.adicionar('solicitante', nova());
  await page.clock.fastForward(61_000);
  await expect(sino(page, 'Notificações, 2 não lidas')).toBeVisible();

  // Aba oculta: o intervalo dispara, mas nada é consultado.
  await page.evaluate(() =>
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' }),
  );
  const antes = mock.state.contagens;
  mock.adicionar('solicitante', nova());
  await page.clock.fastForward(61_000);
  expect(mock.state.contagens).toBe(antes);
  await expect(sino(page, 'Notificações, 2 não lidas')).toBeVisible();

  // Ao voltar à aba, atualiza na hora.
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(sino(page, 'Notificações, 3 não lidas')).toBeVisible();
});

test('autorizador registra a chegada e o solicitante recebe a notificação com o histórico', async ({
  page,
}) => {
  const agora = new Date().toISOString();
  await abrir(
    page,
    'autorizador',
    { notificarChegada: true },
    { sementes: [{ status: 'em_uso', saida_real_em: agora }] },
  );
  await page.getByRole('link', { name: 'Viagem' }).click();
  await page.getByRole('button', { name: 'Registrar chegada agora' }).click();
  await expect(page.getByText('Nenhuma viagem em uso.')).toBeVisible();

  await page.getByRole('button', { name: 'Sair' }).click();
  await expect(page).toHaveURL(/\/login$/);
  await entrar(page, 'solicitante@exemplo.com');
  await expect(sino(page, 'Notificações, 1 não lida')).toBeVisible();
  await sino(page, 'Notificações, 1 não lida').click();
  await page.getByRole('button', { name: /Chegada registrada/ }).click();
  await expect(page.getByRole('heading', { name: 'Linha do tempo' })).toBeVisible();
  await expect(page.locator('ol').getByText('Chegada registrada')).toBeVisible();
  await expect(sino(page, 'Notificações, 0 não lidas')).toBeVisible();
});

test('ao sair o contador é zerado', async ({ page }) => {
  await abrir(page, 'solicitante', { solicitante: [nova()] });
  await expect(sino(page, 'Notificações, 1 não lida')).toBeVisible();
  await page.getByRole('button', { name: 'Sair' }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByText(/Notificações/)).toHaveCount(0);
});

test('destino malicioso do servidor não navega para fora do app', async ({ page }) => {
  const { mock } = await abrir(page, 'solicitante', {
    solicitante: [
      nova({ titulo: 'Barra invertida', destino: '/\\evil.example/x' }),
      nova({ titulo: 'Protocolo relativo', destino: '//evil.example/x' }),
      nova({ titulo: 'URL absoluta', destino: 'https://evil.example/x' }),
    ],
  });
  await sino(page, 'Notificações, 3 não lidas').click();
  for (const t of ['Barra invertida', 'Protocolo relativo', 'URL absoluta']) {
    await page.getByRole('button', { name: new RegExp(t) }).click();
  }
  await expect(sino(page, 'Notificações, 0 não lidas')).toBeVisible();
  await expect(page).toHaveURL(/\/notificacoes\/?$/);
  expect(mock.marcadas).toHaveLength(3);
});
