import { expect, test, type Page } from '@playwright/test';
import { mockarApi, type Opcoes } from './mock-api';

// Fase 4 (ditado por voz). A Web Speech API é substituída por uma classe falsa controlada pelo
// teste: nenhum microfone real é usado. O playwright.config já liga reducedMotion: 'reduce'.

type Modo = 'servico' | 'local' | 'ausente';

interface Instancia {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  processLocally?: boolean;
}
interface Estado {
  construidos: number;
  starts: number;
  stops: number;
  aborts: number;
  midias: number;
  instancias: Instancia[];
}
type JanelaVoz = Window & {
  __voz: {
    estado: Estado;
    ultimo: () => {
      onstart: (() => void) | null;
      onresult: ((e: unknown) => void) | null;
      onerror: ((e: { error: string }) => void) | null;
      onend: (() => void) | null;
    };
  };
};

/** Instala o reconhecedor falso (ou o remove) antes de qualquer script da página. */
async function instalarVoz(page: Page, modo: Modo) {
  await page.addInitScript((m: Modo) => {
    const w = window as unknown as Record<string, unknown>;
    const estado: Estado = {
      construidos: 0,
      starts: 0,
      stops: 0,
      aborts: 0,
      midias: 0,
      instancias: [],
    };
    // Gravação de áudio: qualquer uso é registrado (o código real nunca deve usar).
    w.MediaRecorder = class {
      constructor() {
        estado.midias += 1;
      }
    };
    const md = navigator.mediaDevices as MediaDevices | undefined;
    if (md) {
      md.getUserMedia = () => {
        estado.midias += 1;
        return Promise.reject(new DOMException('negado (teste)', 'NotAllowedError'));
      };
    }
    let ultimo: Record<string, unknown> | null = null;
    class Falso {
      lang = '';
      continuous = true;
      interimResults = false;
      maxAlternatives = 0;
      processLocally?: boolean;
      onstart: (() => void) | null = null;
      onresult: ((e: unknown) => void) | null = null;
      onerror: ((e: { error: string }) => void) | null = null;
      onend: (() => void) | null = null;
      constructor() {
        estado.construidos += 1;
        ultimo = this as unknown as Record<string, unknown>;
      }
      start() {
        estado.starts += 1;
        estado.instancias.push({
          lang: this.lang,
          continuous: this.continuous,
          interimResults: this.interimResults,
          maxAlternatives: this.maxAlternatives,
          processLocally: this.processLocally,
        });
        setTimeout(() => this.onstart?.(), 0);
      }
      stop() {
        estado.stops += 1;
        setTimeout(() => this.onend?.(), 0);
      }
      abort() {
        estado.aborts += 1;
      }
      static available = m === 'local' ? () => Promise.resolve('available') : undefined;
      static install = undefined;
    }
    if (m !== 'ausente') {
      w.SpeechRecognition = Falso;
      w.webkitSpeechRecognition = Falso;
    } else {
      delete w.SpeechRecognition;
      delete w.webkitSpeechRecognition;
    }
    w.__voz = { estado, ultimo: () => ultimo };
  }, modo);
}

const estadoVoz = (page: Page) =>
  page.evaluate(
    () => JSON.parse(JSON.stringify((window as unknown as JanelaVoz).__voz.estado)) as Estado,
  );

/** Dispara onresult com os trechos (cada um já uma alternativa) no reconhecedor ativo. */
const falar = (page: Page, trechos: string[]) =>
  page.evaluate((t) => {
    const r = (window as unknown as JanelaVoz).__voz.ultimo();
    r.onresult?.({ resultIndex: 0, results: t.map((x) => [{ transcript: x }]) });
  }, trechos);

const encerrar = (page: Page) =>
  page.evaluate(() => (window as unknown as JanelaVoz).__voz.ultimo().onend?.());

const falhar = (page: Page, codigo: string) =>
  page.evaluate((c) => {
    const r = (window as unknown as JanelaVoz).__voz.ultimo();
    r.onerror?.({ error: c });
    r.onend?.();
  }, codigo);

function coletarErros(page: Page) {
  const erros: string[] = [];
  page.on('pageerror', (e) => erros.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) {
      erros.push(`console: ${m.text()}`);
    }
  });
  return erros;
}

interface Pedido {
  metodo: string;
  url: string;
  tipo: string;
}
/** Registra todas as requisições a partir da chamada (para contar POSTs e achar áudio/n8n). */
function espiarRede(page: Page) {
  const pedidos: Pedido[] = [];
  page.on('request', (r) =>
    pedidos.push({
      metodo: r.method(),
      url: r.url(),
      tipo: r.headers()['content-type'] ?? '',
    }),
  );
  return pedidos;
}
const postsApi = (p: Pedido[]) => p.filter((x) => x.metodo === 'POST' && /\/api\//.test(x.url));

async function entrar(
  page: Page,
  opcoes: Opcoes,
  modo: Modo = 'servico',
  email = 'solicitante@exemplo.com',
) {
  await instalarVoz(page, modo);
  const mock = await mockarApi(page, opcoes);
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await expect(page).toHaveURL(/\/requisicoes$/);
  return mock;
}

async function irParaNova(page: Page) {
  await page.getByRole('link', { name: 'Nova requisição' }).click();
  await expect(page.getByLabel('Trajeto')).toBeVisible();
}

const MIC = (page: Page) => page.getByRole('button', { name: 'Falar para preencher o campo' });
const OUVINDO = (page: Page) => page.getByRole('status').filter({ hasText: 'Ouvindo...' });
const CONSENTIMENTO = (page: Page) =>
  page.getByRole('dialog', { name: 'Usar reconhecimento de voz?' });
const OBSERVACAO = (page: Page) => page.getByLabel('Observação');
const REVISAO = (page: Page) => page.getByLabel('Texto reconhecido');

/** Clica em Falar (modo local, sem consentimento) e espera o indicador. */
async function comecarAOuvir(page: Page) {
  await MIC(page).click();
  await expect(OUVINDO(page)).toBeVisible();
}

/** Ditado completo: ouve, recebe o texto final e para; a área de revisão aparece. */
async function ditar(page: Page, texto: string) {
  await comecarAOuvir(page);
  await falar(page, [texto]);
  await page.getByRole('button', { name: /Parar de ouvir/ }).click();
  await expect(REVISAO(page)).toHaveValue(texto);
}

// ---- flag e suporte ------------------------------------------------------------------------
test.describe('disponibilidade', () => {
  test('flag desligada (ausente ou false): nenhum microfone, mesmo com suporte do navegador', async ({
    page,
  }) => {
    await entrar(page, {}, 'servico');
    await irParaNova(page);
    await expect(OBSERVACAO(page)).toBeVisible();
    await expect(MIC(page)).toHaveCount(0);
    await expect(page.getByRole('button', { name: /Falar/ })).toHaveCount(0);
    expect((await estadoVoz(page)).construidos).toBe(0);
  });

  test('flag explicitamente false: nenhum microfone', async ({ page }) => {
    await entrar(page, { vozCampos: false }, 'local');
    await irParaNova(page);
    await expect(OBSERVACAO(page)).toBeVisible();
    await expect(MIC(page)).toHaveCount(0);
  });

  test('sem SpeechRecognition: só digitação, sem botão e sem erro', async ({ page }) => {
    const erros = coletarErros(page);
    const mock = await entrar(page, { vozCampos: true }, 'ausente');
    await irParaNova(page);
    await expect(MIC(page)).toHaveCount(0);
    await expect(page.getByText(/reconhecimento de voz|microfone/i)).toHaveCount(0);
    await OBSERVACAO(page).fill('Só digitado');
    await page.getByLabel('Veículo', { exact: true }).selectOption({ index: 1 });
    await page.getByRole('button', { name: /Escolha o dia e o horário/ }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Confirmar' }).click();
    await page.getByLabel('Trajeto').fill('Sede -> Fórum');
    await page.getByRole('button', { name: 'Enviar requisição' }).click();
    await expect(page).toHaveURL(/\/requisicoes\/detalhe/);
    expect(mock.corpos).toHaveLength(1);
    expect(mock.corpos[0]).toMatchObject({ observacao: 'Só digitado' });
    expect(erros).toEqual([]);
  });

  test('flag ligada com suporte: microfone na observação; o microfone NUNCA liga ao carregar', async ({
    page,
  }) => {
    await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    await expect(MIC(page)).toBeVisible();
    await page.waitForTimeout(500);
    const antes = await estadoVoz(page);
    expect(antes.construidos).toBe(0);
    expect(antes.starts).toBe(0);
    await expect(OUVINDO(page)).toHaveCount(0);
    await comecarAOuvir(page);
    const depois = await estadoVoz(page);
    expect(depois.starts).toBe(1);
    expect(depois.instancias[0]).toMatchObject({
      lang: 'pt-BR',
      continuous: false,
      interimResults: true,
      maxAlternatives: 1,
    });
  });

  test('campos de identificação, trajeto, e-mail e senha não têm microfone', async ({ page }) => {
    await instalarVoz(page, 'local');
    await mockarApi(page, { vozCampos: true });
    await page.goto('/login');
    await expect(page.getByLabel('E-mail')).toBeVisible();
    await expect(MIC(page)).toHaveCount(0); // nem logado: eu é nulo
    await page.getByLabel('E-mail').fill('solicitante@exemplo.com');
    await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
    await page.getByRole('button', { name: 'Entrar' }).click();
    await expect(page).toHaveURL(/\/requisicoes$/);
    await irParaNova(page);
    // Só a observação: nome, unidade, veículo, trajeto não ganham microfone.
    await expect(MIC(page)).toHaveCount(1);
    const doCampo = page.locator('div', { has: OBSERVACAO(page) }).last();
    await expect(doCampo.getByRole('button', { name: /Falar/ })).toHaveCount(1);
    for (const rotulo of ['Nome do solicitante', 'Trajeto']) {
      const alvo = page.getByLabel(rotulo);
      await expect(alvo.locator('xpath=..').getByRole('button', { name: /Falar/ })).toHaveCount(0);
    }
  });

  test('campos de km não têm microfone (Quilometragem ligada)', async ({ page }) => {
    await entrar(
      page,
      { vozCampos: true, km: true, sementes: [{ status: 'autorizada' }] },
      'local',
      'autorizador@exemplo.com',
    );
    await page.getByRole('link', { name: 'Autorizar' }).click();
    await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
    await expect(page.getByLabel(/km/i).first()).toBeVisible();
    await expect(MIC(page)).toHaveCount(0);
  });
});

// ---- consentimento -------------------------------------------------------------------------
test.describe('consentimento (sem processamento local)', () => {
  test('diálogo antes do primeiro uso: Cancelar não inicia; Concordar inicia; só em memória', async ({
    page,
  }) => {
    await entrar(page, { vozCampos: true }, 'servico');
    await irParaNova(page);

    await MIC(page).click();
    await expect(CONSENTIMENTO(page)).toBeVisible();
    await expect(CONSENTIMENTO(page)).toContainText('serviço do fornecedor do navegador');
    expect((await estadoVoz(page)).starts).toBe(0);
    expect((await estadoVoz(page)).construidos).toBe(0);

    await CONSENTIMENTO(page).getByRole('button', { name: 'Cancelar' }).click();
    await expect(CONSENTIMENTO(page)).toHaveCount(0);
    await expect(OUVINDO(page)).toHaveCount(0);
    expect((await estadoVoz(page)).starts).toBe(0);

    const guardado = () =>
      page.evaluate(() => ({
        local: JSON.stringify({ ...localStorage }),
        sessao: JSON.stringify({ ...sessionStorage }),
      }));
    const antes = await guardado();

    await MIC(page).click();
    await CONSENTIMENTO(page).getByRole('button', { name: 'Concordar e usar' }).click();
    await expect(OUVINDO(page)).toBeVisible();
    expect((await estadoVoz(page)).starts).toBe(1);
    expect((await estadoVoz(page)).instancias[0]?.processLocally).toBeUndefined();

    // Consentimento não vai para localStorage/sessionStorage.
    const depois = await guardado();
    expect(depois).toEqual(antes);
    expect(depois.local + depois.sessao).not.toMatch(/consent|voz/i);

    // Mesmo carregamento: não pergunta de novo.
    await page.getByRole('button', { name: 'Cancelar' }).click();
    await MIC(page).click();
    await expect(OUVINDO(page)).toBeVisible();
    await expect(CONSENTIMENTO(page)).toHaveCount(0);
    expect((await estadoVoz(page)).starts).toBe(2);

    // Recarregar apaga o consentimento: o diálogo volta.
    await page.reload();
    await expect(OBSERVACAO(page)).toBeVisible();
    await MIC(page).click();
    await expect(CONSENTIMENTO(page)).toBeVisible();
    expect((await estadoVoz(page)).starts).toBe(0);
  });

  test('Esc/fechar no diálogo equivale a cancelar: nada inicia', async ({ page }) => {
    await entrar(page, { vozCampos: true }, 'servico');
    await irParaNova(page);
    await MIC(page).click();
    await expect(CONSENTIMENTO(page)).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(CONSENTIMENTO(page)).toHaveCount(0);
    expect((await estadoVoz(page)).starts).toBe(0);
    expect((await estadoVoz(page)).construidos).toBe(0);
  });

  test('com processamento local: sem diálogo de consentimento e processLocally ligado', async ({
    page,
  }) => {
    await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    await MIC(page).click();
    await expect(OUVINDO(page)).toBeVisible();
    await expect(CONSENTIMENTO(page)).toHaveCount(0);
    const e = await estadoVoz(page);
    expect(e.starts).toBe(1);
    expect(e.instancias[0]?.processLocally).toBe(true);
  });
});

// ---- captura e revisão ---------------------------------------------------------------------
test.describe('captura e revisão', () => {
  test('indicador Ouvindo em role=status; Parar leva à revisão; nada vai ao campo nem à API', async ({
    page,
  }) => {
    const mock = await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    const rede = espiarRede(page);
    await OBSERVACAO(page).fill('Anterior');

    await comecarAOuvir(page);
    await expect(page.getByRole('button', { name: /Parar de ouvir/ })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Cancelar' })).toBeVisible();
    await expect(MIC(page)).toHaveCount(0);

    // Parcial aparece como texto de apoio, não no campo.
    await falar(page, ['olá mun']);
    await expect(page.getByText('olá mun')).toBeVisible();
    await expect(REVISAO(page)).toHaveCount(0);
    await expect(OBSERVACAO(page)).toHaveValue('Anterior');

    await falar(page, ['olá mundo']);
    await page.getByRole('button', { name: /Parar de ouvir/ }).click();
    expect((await estadoVoz(page)).stops).toBe(1);

    await expect(REVISAO(page)).toHaveValue('olá mundo');
    await expect(OUVINDO(page)).toHaveCount(0);
    // Revisão editável, campo intacto e nenhuma chamada à API.
    await expect(OBSERVACAO(page)).toHaveValue('Anterior');
    expect(postsApi(rede)).toHaveLength(0);
    expect(mock.corpos).toHaveLength(0);
  });

  test('Inserir no fim preserva o digitado e usa o texto EDITADO na revisão', async ({ page }) => {
    await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    await OBSERVACAO(page).fill('Texto digitado');
    await ditar(page, 'texto falado errdo');

    await REVISAO(page).fill('texto falado corrigido');
    await page.getByRole('button', { name: 'Inserir no fim do campo' }).click();

    await expect(OBSERVACAO(page)).toHaveValue('Texto digitado texto falado corrigido');
    await expect(REVISAO(page)).toHaveCount(0);
  });

  test('Inserir em campo vazio não deixa espaço sobrando', async ({ page }) => {
    await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    await ditar(page, 'primeira frase');
    await page.getByRole('button', { name: 'Inserir no fim do campo' }).click();
    await expect(OBSERVACAO(page)).toHaveValue('primeira frase');
    // Sem texto na revisão (apagado), Inserir fica desabilitado.
    await ditar(page, 'segunda');
    await REVISAO(page).fill('   ');
    await expect(page.getByRole('button', { name: 'Inserir no fim do campo' })).toBeDisabled();
  });

  test('Substituir exige confirmação; Voltar não altera; confirmar troca o conteúdo', async ({
    page,
  }) => {
    await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    await OBSERVACAO(page).fill('Conteúdo antigo');
    await ditar(page, 'conteúdo novo');

    await page.getByRole('button', { name: 'Substituir', exact: true }).click();
    await expect(page.getByRole('alertdialog', { name: 'Confirmar substituição' })).toBeVisible();
    await expect(OBSERVACAO(page)).toHaveValue('Conteúdo antigo');

    await page.getByRole('button', { name: 'Voltar' }).click();
    await expect(OBSERVACAO(page)).toHaveValue('Conteúdo antigo');
    await expect(REVISAO(page)).toBeVisible();

    await page.getByRole('button', { name: 'Substituir', exact: true }).click();
    await page.getByRole('button', { name: 'Sim, substituir' }).click();
    await expect(OBSERVACAO(page)).toHaveValue('conteúdo novo');
    await expect(REVISAO(page)).toHaveCount(0);
  });

  test('Substituir só é oferecido quando o campo já tem texto', async ({ page }) => {
    await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    await ditar(page, 'dito');
    await expect(page.getByRole('button', { name: 'Substituir', exact: true })).toHaveCount(0);
  });

  test('Descartar não altera o campo e fecha a revisão', async ({ page }) => {
    const mock = await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    await OBSERVACAO(page).fill('Mantido');
    await ditar(page, 'descartado');
    await page.getByRole('button', { name: 'Descartar' }).click();
    await expect(REVISAO(page)).toHaveCount(0);
    await expect(OBSERVACAO(page)).toHaveValue('Mantido');
    expect(mock.corpos).toHaveLength(0);
  });

  test('Cancelar durante a escuta aborta, sem revisão e sem alterar o campo', async ({ page }) => {
    await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    await OBSERVACAO(page).fill('Intacto');
    await comecarAOuvir(page);
    await falar(page, ['parcial']);
    await page.getByRole('button', { name: 'Cancelar' }).click();
    await expect(OUVINDO(page)).toHaveCount(0);
    await expect(REVISAO(page)).toHaveCount(0);
    await expect(MIC(page)).toBeVisible();
    const e = await estadoVoz(page);
    expect(e.aborts).toBe(1);
    expect(e.stops).toBe(0);
    await expect(OBSERVACAO(page)).toHaveValue('Intacto');
  });

  test('nenhum POST ao Flask até enviar o formulário; o POST final leva o texto do campo', async ({
    page,
  }) => {
    const mock = await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    const rede = espiarRede(page);

    await ditar(page, 'trajeto longo pela avenida');
    await REVISAO(page).fill('observação ditada e revisada');
    await page.getByRole('button', { name: 'Inserir no fim do campo' }).click();
    await page.getByLabel('Veículo', { exact: true }).selectOption({ index: 1 });
    await page.getByRole('button', { name: /Escolha o dia e o horário/ }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Confirmar' }).click();
    await page.getByLabel('Trajeto').fill('Sede -> Fórum');

    expect(postsApi(rede)).toHaveLength(0);
    expect(mock.corpos).toHaveLength(0);

    await page.getByRole('button', { name: 'Enviar requisição' }).click();
    await expect(page).toHaveURL(/\/requisicoes\/detalhe/);
    expect(postsApi(rede)).toHaveLength(1);
    expect(mock.corpos).toHaveLength(1);
    expect(mock.corpos[0]).toMatchObject({ observacao: 'observação ditada e revisada' });
  });

  test('texto reconhecido não enviado se o usuário só descarta e envia o formulário', async ({
    page,
  }) => {
    const mock = await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    await ditar(page, 'não deve ser enviado');
    await page.getByRole('button', { name: 'Descartar' }).click();
    await page.getByLabel('Veículo', { exact: true }).selectOption({ index: 1 });
    await page.getByRole('button', { name: /Escolha o dia e o horário/ }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Confirmar' }).click();
    await page.getByLabel('Trajeto').fill('Sede -> Fórum');
    await page.getByRole('button', { name: 'Enviar requisição' }).click();
    await expect(page).toHaveURL(/\/requisicoes\/detalhe/);
    expect(JSON.stringify(mock.corpos)).not.toContain('não deve ser enviado');
    expect(mock.corpos[0]).not.toHaveProperty('observacao');
  });
});

// ---- erros ---------------------------------------------------------------------------------
test.describe('erros do reconhecedor', () => {
  test('permissão negada: mensagem, sem "tentar de novo" e a digitação segue', async ({ page }) => {
    const erros = coletarErros(page);
    await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    await comecarAOuvir(page);
    await falhar(page, 'not-allowed');

    await expect(page.getByText(/O acesso ao microfone foi negado/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Tentar de novo' })).toHaveCount(0);
    await expect(OUVINDO(page)).toHaveCount(0);
    await expect(REVISAO(page)).toHaveCount(0);

    await OBSERVACAO(page).fill('Digitado manualmente');
    await expect(OBSERVACAO(page)).toHaveValue('Digitado manualmente');
    expect(erros).toEqual([]);
  });

  for (const [codigo, mensagem] of [
    ['no-speech', /Não ouvi nenhuma fala/],
    ['network', /O serviço de reconhecimento de voz não respondeu/],
  ] as const) {
    test(`${codigo}: mensagem e "Tentar de novo" volta a ouvir`, async ({ page }) => {
      await entrar(page, { vozCampos: true }, 'local');
      await irParaNova(page);
      await comecarAOuvir(page);
      await falhar(page, codigo);
      await expect(page.getByText(mensagem)).toBeVisible();
      expect((await estadoVoz(page)).starts).toBe(1);

      await page.getByRole('button', { name: 'Tentar de novo' }).click();
      await expect(OUVINDO(page)).toBeVisible();
      expect((await estadoVoz(page)).starts).toBe(2);
      await expect(page.getByText(mensagem)).toHaveCount(0);
    });
  }

  test('fim sem fala e sem erro explícito: trata como "não ouvi"', async ({ page }) => {
    await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    await comecarAOuvir(page);
    await encerrar(page);
    await expect(page.getByText(/Não ouvi nenhuma fala/)).toBeVisible();
    await expect(REVISAO(page)).toHaveCount(0);
  });

  test('o fim espontâneo (onend) com texto final leva à revisão', async ({ page }) => {
    await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    await comecarAOuvir(page);
    await falar(page, ['frase completa']);
    await encerrar(page);
    await expect(REVISAO(page)).toHaveValue('frase completa');
  });
});

// ---- parada automática ---------------------------------------------------------------------
test.describe('parada automática', () => {
  test('aba oculta: para a captura (stop)', async ({ page }) => {
    await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    await comecarAOuvir(page);
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await expect.poll(async () => (await estadoVoz(page)).stops).toBe(1);
    await expect(OUVINDO(page)).toHaveCount(0);
  });

  test('ao navegar para outra página: aborta a captura', async ({ page }) => {
    await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    await comecarAOuvir(page);
    await page.goBack();
    await expect(page).toHaveURL(/\/requisicoes$/);
    await expect.poll(async () => (await estadoVoz(page)).aborts).toBe(1);
    await expect(OUVINDO(page)).toHaveCount(0);
  });

  test('ao desmontar o campo (checklist: trocar para "Sem avarias"): aborta', async ({ page }) => {
    await entrar(
      page,
      { vozCampos: true, checklist: true, sementes: [{ status: 'autorizada' }] },
      'local',
      'autorizador@exemplo.com',
    );
    await page.getByRole('link', { name: 'Autorizar' }).click();
    await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
    await page.locator('label', { hasText: 'Há avarias' }).click();
    await comecarAOuvir(page);
    await page.locator('label', { hasText: 'Sem avarias' }).click();
    await expect(page.getByLabel('Descrição das avarias')).toHaveCount(0);
    await expect.poll(async () => (await estadoVoz(page)).aborts).toBe(1);
  });

  test('sair do campo (blur para fora do bloco de voz) para a captura', async ({ page }) => {
    await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    await OBSERVACAO(page).focus();
    await comecarAOuvir(page);
    await OBSERVACAO(page).focus();
    await page.getByLabel('Trajeto').focus();
    await expect.poll(async () => (await estadoVoz(page)).stops).toBe(1);
  });
});

// ---- privacidade ---------------------------------------------------------------------------
test.describe('privacidade', () => {
  test('nenhuma gravação de áudio, nenhuma requisição com áudio e nada ao n8n', async ({
    page,
  }) => {
    const mock = await entrar(page, { vozCampos: true }, 'servico');
    await irParaNova(page);
    const rede = espiarRede(page);

    await MIC(page).click();
    await CONSENTIMENTO(page).getByRole('button', { name: 'Concordar e usar' }).click();
    await expect(OUVINDO(page)).toBeVisible();
    await falar(page, ['dito sigiloso']);
    await page.getByRole('button', { name: /Parar de ouvir/ }).click();
    await expect(REVISAO(page)).toHaveValue('dito sigiloso');
    await page.getByRole('button', { name: 'Inserir no fim do campo' }).click();

    const e = await estadoVoz(page);
    expect(e.midias).toBe(0); // MediaRecorder/getUserMedia nunca chamados
    expect(
      rede.filter((r) => /^(audio|video)\//.test(r.tipo) || /multipart|octet-stream/.test(r.tipo)),
    ).toEqual([]);
    expect(rede.filter((r) => /n8n|webhook/i.test(r.url))).toEqual([]);
    expect(postsApi(rede)).toHaveLength(0);
    expect(mock.corpos).toHaveLength(0);
  });
});

// ---- checklist -----------------------------------------------------------------------------
test.describe('checklist', () => {
  async function abrirChecklist(page: Page) {
    await entrar(
      page,
      { vozCampos: true, checklist: true, sementes: [{ status: 'autorizada' }] },
      'local',
      'autorizador@exemplo.com',
    );
    await page.getByRole('link', { name: 'Autorizar' }).click();
    await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
    await expect(page.getByRole('heading', { name: 'Checklist de saída' })).toBeVisible();
  }

  test('descrição das avarias tem microfone só quando há avarias; respeita maxLength 500', async ({
    page,
  }) => {
    await abrirChecklist(page);
    await expect(MIC(page)).toHaveCount(0); // sem o campo de avarias, sem microfone
    await page.locator('label', { hasText: 'Há avarias' }).click();
    await expect(MIC(page)).toHaveCount(1);

    const descricao = page.getByLabel('Descrição das avarias');
    await descricao.fill('a'.repeat(490));
    await ditar(page, 'bb '.repeat(10).trim());
    await page.getByRole('button', { name: 'Inserir no fim do campo' }).click();
    const valor = await descricao.inputValue();
    expect(valor).toHaveLength(500);
    expect(valor.startsWith('a'.repeat(490))).toBe(true);
    await expect(page.getByText(/passou do limite de 500 caracteres e foi cortado/)).toBeVisible();

    // Substituir também respeita o limite.
    const longo = 'cc '.repeat(200).trim();
    await ditar(page, longo);
    await page.getByRole('button', { name: 'Substituir', exact: true }).click();
    await page.getByRole('button', { name: 'Sim, substituir' }).click();
    await expect(descricao).toHaveValue(longo.slice(0, 500));
    await expect(page.getByText('500/500')).toBeVisible();
  });

  test('o texto ditado entra no checklist, mas só vai à API ao registrar', async ({ page }) => {
    await abrirChecklist(page);
    const rede = espiarRede(page);
    await page.locator('label', { hasText: '1/2' }).click();
    await page.locator('label', { hasText: 'Há avarias' }).click();
    await ditar(page, 'arranhão na porta');
    await page.getByRole('button', { name: 'Inserir no fim do campo' }).click();
    await expect(page.getByLabel('Descrição das avarias')).toHaveValue('arranhão na porta');
    expect(postsApi(rede)).toHaveLength(0);
  });
});

// ---- viewport desktop ----------------------------------------------------------------------
test.describe('viewport desktop', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('fluxo completo e alvos de toque >= 44px também no desktop', async ({ page }) => {
    await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    await expect(MIC(page)).toBeVisible();
    expect((await MIC(page).boundingBox())?.height).toBeGreaterThanOrEqual(44);
    await comecarAOuvir(page);
    for (const nome of [/Parar de ouvir/, 'Cancelar']) {
      const caixa = await page.getByRole('button', { name: nome }).boundingBox();
      expect(caixa?.height).toBeGreaterThanOrEqual(44);
    }
    await falar(page, ['desktop ok']);
    await page.getByRole('button', { name: /Parar de ouvir/ }).click();
    await expect(REVISAO(page)).toHaveValue('desktop ok');
    for (const nome of ['Inserir no fim do campo', 'Descartar']) {
      const caixa = await page.getByRole('button', { name: nome }).boundingBox();
      expect(caixa?.height).toBeGreaterThanOrEqual(44);
    }
    await page.getByRole('button', { name: 'Inserir no fim do campo' }).click();
    await expect(OBSERVACAO(page)).toHaveValue('desktop ok');
  });
});

test.describe('viewport mobile', () => {
  test('alvos de toque >= 44px e sem rolagem horizontal durante a revisão', async ({ page }) => {
    await entrar(page, { vozCampos: true }, 'local');
    await irParaNova(page);
    expect((await MIC(page).boundingBox())?.height).toBeGreaterThanOrEqual(44);
    await ditar(page, 'mobile ok');
    for (const nome of ['Inserir no fim do campo', 'Descartar']) {
      const caixa = await page.getByRole('button', { name: nome }).boundingBox();
      expect(caixa?.height).toBeGreaterThanOrEqual(44);
    }
    const largura = await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    );
    expect(largura).toBe(true);
  });
});
