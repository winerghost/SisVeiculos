import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockarApi, type Opcoes } from './mock-api';

// Estado de carregamento ao arquivar (botão e swipe). A resposta do arquivamento é segurada por
// uma rota que o teste controla (registrada depois da do mock, logo com prioridade): liberar()
// repassa ao mock (sucesso) e falhar() responde com erro da API.
const CARTAO = 'ul.lista-animada > li';
const MSG_ERRO = 'Falha simulada ao arquivar.';

interface Segurada {
  url: string;
  decidir: (r: { ok: true } | { ok: false; status: number }) => void;
}

async function segurarArquivar(page: Page) {
  const chamadas: Segurada[] = [];
  await page.route(/\/api\/requisicoes\/[^/?]+\/(arquivar|desarquivar)(\?.*)?$/, async (route) => {
    const resultado = await new Promise<{ ok: true } | { ok: false; status: number }>((ok) => {
      chamadas.push({ url: route.request().url(), decidir: ok });
    });
    if (resultado.ok) return route.fallback();
    return route.fulfill({
      status: resultado.status,
      json: { erro: { codigo: 'erro_simulado', mensagem: MSG_ERRO } },
    });
  });
  return {
    chamadas,
    liberar: (i: number) => chamadas[i]?.decidir({ ok: true }),
    falhar: (i: number, status: number) => chamadas[i]?.decidir({ ok: false, status }),
  };
}

async function entrar(page: Page, opcoes: Opcoes = {}) {
  const mock = await mockarApi(page, opcoes);
  const gate = await segurarArquivar(page);
  await page.goto('/login');
  await page.getByLabel('E-mail').fill('autorizador@exemplo.com');
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.getByRole('link', { name: 'Requisições' }).click();
  await expect(page.getByRole('heading', { name: 'Requisições', level: 1 })).toBeVisible();
  return { mock, gate };
}

/** Arrasta o cartão começando sobre o link. fracao > 0 = direita. */
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

const cobertura = (cartao: Locator) =>
  cartao.getByRole('status').filter({ hasText: 'Arquivando...' }).first();

for (const [nome, viewport] of [
  ['mobile', { width: 390, height: 844 }],
  ['desktop', { width: 1280, height: 800 }],
] as const) {
  test.describe(`arquivar com loading (${nome})`, () => {
    test.use({ viewport });
    const desktop = nome === 'desktop';

    // Desktop: linha da tabela -> "Mais" -> diálogo "Mais ações" -> Arquivar. Mobile: botão no cartão.
    const item = desktop ? 'tbody tr' : CARTAO;
    const dialogo = (page: Page) => page.getByRole('dialog', { name: 'Mais ações' });
    /** Contêiner onde fica o botão Arquivar do item (o diálogo no desktop, o cartão no mobile). */
    async function abrirAcoes(page: Page, alvo: Locator) {
      if (!desktop) return alvo;
      await alvo.getByRole('button', { name: /^Mais ações/ }).click();
      const d = dialogo(page);
      await expect(d).toBeVisible();
      return d;
    }

    test('botão: mostra Arquivando..., fica desabilitado e clique duplo gera 1 só chamada', async ({
      page,
    }) => {
      const { mock, gate } = await entrar(page, { sementes: [{ status: 'concluida' }] });
      const cartao = page.locator(item).first();
      const acoes = await abrirAcoes(page, cartao);
      await acoes.getByRole('button', { name: 'Arquivar' }).click();

      const ocupado = acoes.getByRole('button', { name: /Arquivando/ });
      await expect(ocupado).toBeVisible();
      await expect(ocupado).toBeDisabled();
      await expect(ocupado).toHaveAttribute('aria-busy', 'true');
      if (!desktop) await expect(cobertura(cartao)).toBeVisible();

      // Dois cliques síncronos no mesmo tick: a guarda do hook (useRef) precisa barrar o 2º.
      await ocupado.evaluate((el) => {
        (el as HTMLButtonElement).click();
        (el as HTMLButtonElement).click();
      });
      await ocupado.dblclick({ force: true }).catch(() => undefined);
      await page.waitForTimeout(200);
      expect(gate.chamadas).toHaveLength(1);

      gate.liberar(0);
      await expect(page.locator(item)).toHaveCount(0);
      await expect(page.getByRole('status').getByText('Requisição arquivada.')).toBeVisible();
      expect(mock.organizacoes.map((o) => o.acao)).toEqual(['arquivar']);
    });

    // Gestos de arrastar só existem nos cartões (mobile).
    test('swipe para a direita: cobertura imediata, gestos repetidos não geram 2ª chamada', async ({
      page,
    }) => {
      test.skip(desktop, 'swipe só nos cartões do mobile');
      const { mock, gate } = await entrar(page, { sementes: [{ status: 'concluida' }] });
      const cartao = page.locator(CARTAO).first();
      const xAntes = (await cartao.getByRole('link').first().boundingBox())!.x;

      await arrastar(page, cartao, 0.7);
      await expect(cobertura(cartao)).toBeVisible();
      await expect(cartao).toHaveAttribute('aria-busy', 'true');
      expect(gate.chamadas).toHaveLength(1);
      // O cartão já voltou à posição (dx = 0) enquanto aguarda a resposta.
      await expect
        .poll(async () =>
          Math.abs((await cartao.getByRole('link').first().boundingBox())!.x - xAntes),
        )
        .toBeLessThan(2);

      await arrastar(page, cartao, 0.7);
      await arrastar(page, cartao, -0.6);
      await page.waitForTimeout(200);
      expect(gate.chamadas).toHaveLength(1);
      await expect(page.getByRole('alertdialog')).toHaveCount(0);

      gate.liberar(0);
      await expect(page.locator(CARTAO)).toHaveCount(0);
      expect(mock.organizacoes.map((o) => o.acao)).toEqual(['arquivar']);
    });

    for (const status of [409, 500]) {
      test(`erro ${status} no botão: indicador some, item fica, mensagem aparece e dá para tentar de novo`, async ({
        page,
      }) => {
        const { mock, gate } = await entrar(page, { sementes: [{ status: 'concluida' }] });
        const cartao = page.locator(item).first();
        let acoes = await abrirAcoes(page, cartao);
        await acoes.getByRole('button', { name: 'Arquivar' }).click();
        if (!desktop) await expect(cobertura(cartao)).toBeVisible();
        await expect.poll(() => gate.chamadas.length).toBe(1);

        gate.falhar(0, status);
        if (desktop) await expect(dialogo(page)).toBeHidden();
        else await expect(cobertura(cartao)).toHaveCount(0);
        await expect(page.getByText(MSG_ERRO)).toBeVisible();
        await expect(page.locator(item)).toHaveCount(1);
        expect(mock.organizacoes).toEqual([]);

        acoes = await abrirAcoes(page, cartao);
        const botao = acoes.getByRole('button', { name: 'Arquivar' });
        await expect(botao).toBeEnabled();
        await botao.click();
        await expect.poll(() => gate.chamadas.length).toBe(2);
        gate.liberar(1);
        await expect(page.locator(item)).toHaveCount(0);
        expect(mock.organizacoes.map((o) => o.acao)).toEqual(['arquivar']);
      });
    }

    test('erro 500 no swipe: cobertura some, cartão no lugar e novo swipe funciona', async ({
      page,
    }) => {
      test.skip(desktop, 'swipe só nos cartões do mobile');
      const { mock, gate } = await entrar(page, { sementes: [{ status: 'concluida' }] });
      const cartao = page.locator(CARTAO).first();
      await arrastar(page, cartao, 0.7);
      await expect(cobertura(cartao)).toBeVisible();

      gate.falhar(0, 500);
      await expect(cobertura(cartao)).toHaveCount(0);
      await expect(page.getByText(MSG_ERRO)).toBeVisible();
      await expect(page.locator(CARTAO)).toHaveCount(1);

      await arrastar(page, cartao, 0.7);
      await expect.poll(() => gate.chamadas.length).toBe(2);
      gate.liberar(1);
      await expect(page.locator(CARTAO)).toHaveCount(0);
      expect(mock.organizacoes.map((o) => o.acao)).toEqual(['arquivar']);
    });

    test('dois itens: processar um não bloqueia o outro', async ({ page }) => {
      const { mock, gate } = await entrar(page, {
        sementes: [{ status: 'concluida' }, { status: 'negada' }],
      });
      const lista = page.locator(item);
      await expect(lista).toHaveCount(2);
      const primeiro = lista.nth(0);
      const segundo = lista.nth(1);

      const acoes1 = await abrirAcoes(page, primeiro);
      await acoes1.getByRole('button', { name: 'Arquivar' }).click();
      await expect.poll(() => gate.chamadas.length).toBe(1);

      if (desktop) {
        // O diálogo do 1º é modal e fica aberto até a resposta; o "Mais" do 2º segue habilitado
        // (o bloqueio é só da linha em processamento).
        await expect(segundo.getByRole('button', { name: /^Mais ações/ })).toBeEnabled();
        gate.liberar(0);
        await expect(page.locator(item)).toHaveCount(1);
        const acoes2 = await abrirAcoes(page, page.locator(item).first());
        await acoes2.getByRole('button', { name: 'Arquivar' }).click();
        await expect.poll(() => gate.chamadas.length).toBe(2);
        gate.liberar(1);
        await expect(page.locator(item)).toHaveCount(0);
        expect(mock.organizacoes.map((o) => o.acao)).toEqual(['arquivar', 'arquivar']);
        return;
      }

      await expect(cobertura(primeiro)).toBeVisible();
      // O segundo segue livre: botão habilitado, sem cobertura.
      await expect(cobertura(segundo)).toHaveCount(0);
      const botao2 = segundo.getByRole('button', { name: 'Arquivar' });
      await expect(botao2).toBeEnabled();

      await botao2.click();
      await expect.poll(() => gate.chamadas.length).toBe(2);
      await expect(cobertura(segundo)).toBeVisible();

      // Libera só a segunda: ela sai e a primeira continua em processamento.
      gate.liberar(1);
      await expect(page.locator(CARTAO)).toHaveCount(1);
      await expect(cobertura(page.locator(CARTAO).first())).toBeVisible();

      gate.liberar(0);
      await expect(page.locator(CARTAO)).toHaveCount(0);
      expect(mock.organizacoes.map((o) => o.acao).sort()).toEqual(['arquivar', 'arquivar']);
    });
  });
}
