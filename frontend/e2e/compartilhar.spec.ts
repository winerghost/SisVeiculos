import { readFileSync } from 'node:fs';
import { expect, test, type Download, type Page } from '@playwright/test';
import {
  ACAO_IMAGEM,
  ID_AUTORIZADA,
  ID_CONCLUIDA,
  ID_DO_SOLICITANTE,
  ID_SEM_RECURSO,
  ID_SOLICITADA,
  NOMES_EQUIPE,
  OBSERVACAO,
  mockarCompartilhar,
  mockarShare,
  type ChamadaShare,
  type ModoShare,
} from './mock-compartilhar';

// Compartilhar a requisição como imagem PNG (FAB/speed dial em /requisicoes/detalhe).
// Contratos: 01-analise-plano.md §5.1 e 02-decisoes.md. A visibilidade dos itens de imagem vem
// de `acoes_permitidas` (`compartilhar_imagem`), decidida pelo backend; "Copiar link" é sempre
// oferecido. A geração é local: nenhuma consulta extra de dados.

const MSG = {
  gerando: 'Gerando imagem…',
  baixada: 'Imagem baixada.',
  semSuporte: 'Seu aparelho não compartilha arquivos. A imagem foi baixada.',
  copiado: 'Link copiado.',
  falha: 'Não foi possível gerar a imagem. Tente novamente.',
};

async function entrar(page: Page, email: string) {
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.waitForURL(/\/requisicoes/);
}

const fab = (page: Page) =>
  page.getByRole('button', {
    name: /^(Compartilhar requisição|Fechar menu de compartilhamento)$/,
  });
const menu = (page: Page) => page.getByRole('menu', { name: 'Opções de compartilhamento' });
const item = (page: Page, nome: string) => page.getByRole('menuitem', { name: nome });

async function preparar(
  page: Page,
  opcoes: { modo?: ModoShare; email?: string; id?: string } = {},
) {
  await mockarShare(page, opcoes.modo ?? 'ok');
  const mock = await mockarCompartilhar(page);
  await entrar(page, opcoes.email ?? 'autorizador@agu.gov.br');
  await page.goto(`/requisicoes/detalhe?id=${opcoes.id ?? ID_CONCLUIDA}`);
  await expect(page.getByRole('region', { name: 'Resumo' })).toBeVisible();
  await expect(fab(page)).toBeVisible();
  return mock;
}

async function abrirMenu(page: Page) {
  await fab(page).click();
  await expect(fab(page)).toHaveAttribute('aria-expanded', 'true');
  await expect(menu(page)).toBeVisible();
}

// Data da geração (aaaammdd) no fuso do sistema, America/Sao_Paulo.
const hojeSp = () =>
  new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Sao_Paulo' })
    .format(new Date())
    .replace(/-/g, '');
const nomeArquivo = (id: string) => new RegExp(`^requisicao-${id.slice(0, 8)}-${hojeSp()}\\.png$`);

async function lerPng(d: Download) {
  const caminho = await d.path();
  const b = readFileSync(caminho);
  return {
    assinatura: b.subarray(0, 4).toString('hex'),
    // IHDR: largura e altura (big-endian) nos bytes 16-19 e 20-23.
    largura: b.readUInt32BE(16),
    altura: b.readUInt32BE(20),
  };
}

/**
 * Pedidos de rede que a geração NÃO pode fazer: qualquer /api/ (exceto o polling de tempo real,
 * que é da tela), qualquer outra origem (Storage, CDN, telemetria) e /storage/. Assets da mesma
 * origem (fonte woff2, logo) são esperados. Ligar depois que a tela carregou.
 */
function vigiarRede(page: Page) {
  const origem = new URL(page.url()).origin;
  const pedidos: string[] = [];
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (u.protocol === 'data:' || u.protocol === 'blob:') return;
    if (u.pathname === '/api/atualizacoes' && u.origin === origem) return;
    if (u.origin !== origem || u.pathname.startsWith('/api/') || u.pathname.includes('/storage/')) {
      pedidos.push(`${r.method()} ${u.origin}${u.pathname}`);
    }
  });
  return pedidos;
}

const shares = (page: Page) =>
  page.evaluate(() => (window as unknown as { __shares: ChamadaShare[] }).__shares);
const cartoes = (page: Page) =>
  page.evaluate(
    () =>
      (
        window as unknown as {
          __cartoes: { texto: string; ariaHidden: string | null; inert: boolean }[];
        }
      ).__cartoes,
  );

// ---- 1. Compartilhar (Web Share API com arquivo) ----

test('compartilhar: share recebe 1 PNG com nome padronizado, title e sem text', async ({
  page,
}) => {
  await preparar(page);
  const rede = vigiarRede(page);
  const downloads: Download[] = [];
  page.on('download', (d) => downloads.push(d));
  await abrirMenu(page);
  await item(page, 'Compartilhar imagem').click();

  await expect.poll(async () => (await shares(page)).length).toBe(1);
  expect(rede).toEqual([]);
  const s = (await shares(page))[0]!;
  expect(s.chaves).toEqual(['files', 'title']);
  expect(s.title).toBe('Requisição de veículo');
  expect(s.text).toBeUndefined();
  expect(s.arquivos).toHaveLength(1);
  expect(s.arquivos[0]!.tipo).toBe('image/png');
  expect(s.arquivos[0]!.nome).toMatch(nomeArquivo(ID_CONCLUIDA));
  expect(s.arquivos[0]!.tamanho).toBeGreaterThan(1000);
  expect(downloads).toHaveLength(0);
  await expect(page.getByRole('alert').filter({ hasText: MSG.falha })).toHaveCount(0);
});

// ---- 2. Cancelamento pelo usuário ----

test('compartilhar: AbortError do share é silencioso (sem erro e sem download)', async ({
  page,
}) => {
  await preparar(page, { modo: 'abort' });
  const downloads: Download[] = [];
  page.on('download', (d) => downloads.push(d));
  await abrirMenu(page);
  await item(page, 'Compartilhar imagem').click();
  await expect.poll(async () => (await shares(page)).length).toBe(1);
  await page.waitForTimeout(500);
  await expect(page.getByText(MSG.falha)).toHaveCount(0);
  await expect(page.getByText(MSG.semSuporte)).toHaveCount(0);
  await expect(page.getByText(MSG.baixada)).toHaveCount(0);
  expect(downloads).toHaveLength(0);
});

// ---- 3. Sem suporte a arquivos: baixa e avisa ----

test('compartilhar sem Web Share: baixa PNG 1080 px e avisa', async ({ page }) => {
  await preparar(page, { modo: 'ausente' });
  await abrirMenu(page);
  const [d] = await Promise.all([
    page.waitForEvent('download'),
    item(page, 'Compartilhar imagem').click(),
  ]);
  // O aviso some sozinho em 6 s: conferir antes de ler o arquivo (lento sob carga).
  await expect(page.getByRole('status').filter({ hasText: MSG.semSuporte })).toBeVisible();
  expect(d.suggestedFilename()).toMatch(nomeArquivo(ID_CONCLUIDA));
  const png = await lerPng(d);
  expect(png.assinatura).toBe('89504e47');
  expect(png.largura).toBe(1080);
});

test('share sem suporte a arquivos (canShare false): baixa, avisa e não chama share', async ({
  page,
}) => {
  await preparar(page, { modo: 'sem-arquivos' });
  await abrirMenu(page);
  const [d] = await Promise.all([
    page.waitForEvent('download'),
    item(page, 'Compartilhar imagem').click(),
  ]);
  await expect(page.getByRole('status').filter({ hasText: MSG.semSuporte })).toBeVisible();
  expect(d.suggestedFilename()).toMatch(nomeArquivo(ID_CONCLUIDA));
  expect((await lerPng(d)).assinatura).toBe('89504e47');
  // Nunca troca o arquivo por text/url.
  expect(await shares(page)).toEqual([]);
});

// ---- 4. Baixar imagem: altura cresce com a linha do tempo; sem consulta extra de dados ----

async function baixarPng(page: Page) {
  await abrirMenu(page);
  const [d] = await Promise.all([
    page.waitForEvent('download'),
    item(page, 'Baixar imagem').click(),
  ]);
  await expect(page.getByRole('status').filter({ hasText: MSG.baixada })).toBeVisible();
  return d;
}

test('baixar: PNG da concluída é mais alto que o da solicitada; geração sem novas consultas', async ({
  page,
}) => {
  const mock = await preparar(page, { id: ID_CONCLUIDA });
  const pedidos = vigiarRede(page);
  const leiturasAntes = mock.leituras.length;
  const d1 = await baixarPng(page);
  expect(d1.suggestedFilename()).toMatch(nomeArquivo(ID_CONCLUIDA));
  const alta = await lerPng(d1);
  expect(alta.assinatura).toBe('89504e47');
  expect(alta.largura).toBe(1080);
  // 5.4: o cartão usa só os dados já carregados.
  expect(pedidos).toEqual([]);
  expect(mock.leituras.length).toBe(leiturasAntes);

  await page.goto(`/requisicoes/detalhe?id=${ID_SOLICITADA}`);
  await expect(fab(page)).toBeVisible();
  const d2 = await baixarPng(page);
  expect(d2.suggestedFilename()).toMatch(nomeArquivo(ID_SOLICITADA));
  const baixa = await lerPng(d2);
  expect(baixa.largura).toBe(1080);
  expect(alta.altura).toBeGreaterThan(baixa.altura);
});

// ---- 5. Copiar link ----

test('copiar link: copia o endereço da tela (exige login) e avisa', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await preparar(page);
  await abrirMenu(page);
  await item(page, 'Copiar link').click();
  await expect(page.getByRole('status').filter({ hasText: MSG.copiado })).toBeVisible();
  const origem = new URL(page.url()).origin;
  const copiado = await page.evaluate(() => navigator.clipboard.readText());
  expect(copiado).toBe(`${origem}/requisicoes/detalhe?id=${ID_CONCLUIDA}`);
});

// ---- 6. Fechar: ESC, clique fora e segundo clique no FAB ----

test('menu fecha com ESC (foco volta ao FAB), clique fora e segundo clique', async ({ page }) => {
  await preparar(page);
  await expect(fab(page)).toHaveAttribute('aria-expanded', 'false');
  await expect(fab(page)).toHaveAttribute('aria-haspopup', 'menu');

  await abrirMenu(page);
  await expect(fab(page)).toHaveAccessibleName('Fechar menu de compartilhamento');
  const controla = await fab(page).getAttribute('aria-controls');
  expect(controla).toBeTruthy();
  expect(await menu(page).getAttribute('id')).toBe(controla);
  await page.keyboard.press('Escape');
  await expect(fab(page)).toHaveAttribute('aria-expanded', 'false');
  await expect(menu(page)).toBeHidden();
  await expect(fab(page)).toBeFocused();
  await expect(fab(page)).toHaveAccessibleName('Compartilhar requisição');

  await abrirMenu(page);
  await page.getByRole('heading', { name: 'Linha do tempo' }).click();
  await expect(fab(page)).toHaveAttribute('aria-expanded', 'false');
  await expect(menu(page)).toBeHidden();

  await abrirMenu(page);
  await fab(page).click();
  await expect(fab(page)).toHaveAttribute('aria-expanded', 'false');
  await expect(menu(page)).toBeHidden();
});

// ---- 7. Teclado ----

test('teclado: Tab até o FAB, Enter abre, setas navegam e Enter executa', async ({
  page,
  context,
}) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  // Solicitada sem observação: itens Compartilhar imagem, Baixar imagem, Copiar link.
  await preparar(page, { id: ID_SOLICITADA });
  let achou = false;
  for (let i = 0; i < 40 && !achou; i++) {
    await page.keyboard.press('Tab');
    achou = await fab(page).evaluate((el) => el === document.activeElement);
  }
  expect(achou).toBe(true);
  await page.keyboard.press('Enter');
  await expect(fab(page)).toHaveAttribute('aria-expanded', 'true');
  await expect(item(page, 'Compartilhar imagem')).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(item(page, 'Baixar imagem')).toBeFocused();
  await page.keyboard.press('ArrowUp');
  await expect(item(page, 'Compartilhar imagem')).toBeFocused();
  await page.keyboard.press('End');
  await expect(item(page, 'Copiar link')).toBeFocused();
  await page.keyboard.press('Home');
  await expect(item(page, 'Compartilhar imagem')).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowDown');
  await expect(item(page, 'Copiar link')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('status').filter({ hasText: MSG.copiado })).toBeVisible();
});

// ---- 8. Clique duplo ----

test('clique duplo em Baixar imagem gera um único download; item ocupado durante a geração', async ({
  page,
}) => {
  await preparar(page);
  // Atrasa a fonte que a geração embute: a geração fica "em andamento" por tempo observável.
  await page.route('**/_next/static/media/*.woff2', async (route) => {
    await new Promise((r) => setTimeout(r, 1500));
    await route.continue();
  });
  const downloads: Download[] = [];
  page.on('download', (d) => downloads.push(d));
  await abrirMenu(page);
  const baixar = item(page, 'Baixar imagem');
  // Dois cliques no mesmo tique: só a trava por ref os separa (o React ainda não re-renderizou).
  await baixar.evaluate((el) => {
    (el as HTMLElement).click();
    (el as HTMLElement).click();
  });
  // Ocupado: item desabilitado/aria-busy (ou o FAB, se o menu fechar ao clicar).
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          document.querySelectorAll(
            '[role="menuitem"][aria-busy="true"], [role="menuitem"]:disabled, [role="menuitem"][aria-disabled="true"], button[aria-busy="true"]',
          ).length,
      ),
    )
    .toBeGreaterThan(0);
  await expect.poll(() => downloads.length, { timeout: 15_000 }).toBe(1);
  await page.waitForTimeout(1500);
  expect(downloads).toHaveLength(1);
});

// ---- 9. Desktop ----

test.describe('desktop', () => {
  test.use({ viewport: { width: 1280, height: 800 }, isMobile: false, hasTouch: false });

  test('FAB fica na coluna central e não cobre os botões de ação no fim da rolagem', async ({
    page,
  }) => {
    await preparar(page, { id: ID_AUTORIZADA });
    const resumo = await page.getByRole('region', { name: 'Resumo' }).boundingBox();
    const caixaFab = await fab(page).boundingBox();
    expect(resumo && caixaFab).toBeTruthy();
    // Coluna max-w-md (448 px) centralizada; o resumo tem 16 px de respiro de cada lado.
    expect(caixaFab!.x).toBeGreaterThanOrEqual(resumo!.x - 16);
    expect(caixaFab!.x + caixaFab!.width).toBeLessThanOrEqual(resumo!.x + resumo!.width + 16);
    expect(caixaFab!.width).toBeGreaterThanOrEqual(56);

    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await page.waitForTimeout(200);
    const fabFim = (await fab(page).boundingBox())!;
    for (const nome of ['Registrar saída', 'Cancelar requisição']) {
      const b = page.getByRole('button', { name: nome, exact: true });
      await expect(b).toBeVisible();
      const c = (await b.boundingBox())!;
      const cruza =
        c.x < fabFim.x + fabFim.width &&
        fabFim.x < c.x + c.width &&
        c.y < fabFim.y + fabFim.height &&
        fabFim.y < c.y + c.height;
      expect(cruza, `FAB cobre o botão "${nome}"`).toBe(false);
    }
  });
});

// ---- 10. Movimento reduzido ----

async function maiorDuracao(page: Page) {
  return item(page, 'Copiar link').evaluate((el) => {
    const s = (v: string) =>
      Math.max(
        ...v.split(',').map((x) => {
          const t = x.trim();
          return t.endsWith('ms') ? parseFloat(t) / 1000 : parseFloat(t) || 0;
        }),
      );
    let maior = 0;
    for (let n: Element | null = el; n && n.getAttribute('role') !== 'menu'; n = n.parentElement) {
      const cs = getComputedStyle(n);
      maior = Math.max(maior, s(cs.transitionDuration), s(cs.animationDuration));
    }
    return maior;
  });
}

test('movimento: itens animam ≤ 200 ms e zeram com prefers-reduced-motion', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await preparar(page);
  await abrirMenu(page);
  const normal = await maiorDuracao(page);
  expect(normal).toBeGreaterThan(0);
  expect(normal).toBeLessThanOrEqual(0.2);
  await page.keyboard.press('Escape');

  await page.emulateMedia({ reducedMotion: 'reduce' });
  await abrirMenu(page);
  expect(await maiorDuracao(page)).toBeLessThanOrEqual(0.001);
});

// ---- 11. Privacidade e visibilidade ----

test('privacidade: cartão do solicitante sem nomes da equipe; observação só com a caixa', async ({
  page,
}) => {
  // A minimização (sem nomes/ids da equipe) é do backend (pytest); aqui é a regressão do lado do
  // front: o cartão não inventa nem busca nada além do que a API mandou (nem a foto de perfil).
  await preparar(page, { email: 'marcos@agu.gov.br', id: ID_DO_SOLICITANTE });
  const rede = vigiarRede(page);
  await baixarPng(page);
  let lista = await cartoes(page);
  expect(lista.length).toBeGreaterThan(0);
  let ultimo = lista[lista.length - 1]!;
  // Montado fora da tela, escondido do leitor de tela e inerte.
  expect(ultimo.ariaHidden).toBe('true');
  expect(ultimo.inert).toBe(true);
  expect(ultimo.texto).toContain('Marcos Silva');
  expect(ultimo.texto).toContain(`Nº ${ID_DO_SOLICITANTE.slice(0, 8)}`);
  expect(ultimo.texto).toContain('Uso interno – AGU');
  for (const nome of NOMES_EQUIPE) expect(ultimo.texto).not.toContain(nome);
  expect(ultimo.texto).not.toContain(OBSERVACAO);
  // O cartão nunca leva e-mail.
  expect(ultimo.texto).not.toMatch(/@/);

  if (!(await menu(page).isVisible())) await abrirMenu(page);
  const caixa = page.getByRole('menuitemcheckbox', { name: 'Incluir observação' });
  await expect(caixa).toHaveAttribute('aria-checked', 'false');
  await caixa.click();
  await expect(caixa).toHaveAttribute('aria-checked', 'true');
  const [d] = await Promise.all([
    page.waitForEvent('download'),
    item(page, 'Baixar imagem').click(),
  ]);
  expect((await lerPng(d)).largura).toBe(1080);
  lista = await cartoes(page);
  ultimo = lista[lista.length - 1]!;
  expect(ultimo.texto).toContain(OBSERVACAO);
  for (const nome of NOMES_EQUIPE) expect(ultimo.texto).not.toContain(nome);
  expect(rede).toEqual([]);
});

test('sem observação na requisição, a caixa "Incluir observação" não aparece', async ({ page }) => {
  await preparar(page, { id: ID_SOLICITADA });
  await abrirMenu(page);
  await expect(item(page, 'Compartilhar imagem')).toBeVisible();
  await expect(page.getByRole('menuitemcheckbox', { name: 'Incluir observação' })).toHaveCount(0);
});

test(`sem a ação ${ACAO_IMAGEM} (recurso desligado): só "Copiar link" e nenhuma geração`, async ({
  page,
}) => {
  await preparar(page, { id: ID_SEM_RECURSO });
  await abrirMenu(page);
  await expect(page.getByRole('menuitem')).toHaveText(['Copiar link']);
  await expect(item(page, 'Compartilhar imagem')).toHaveCount(0);
  await expect(item(page, 'Baixar imagem')).toHaveCount(0);
  await page.waitForTimeout(300);
  expect(await cartoes(page)).toHaveLength(0);
});
