import { expect, test, type Page } from '@playwright/test';
import qrcode from 'qrcode-generator';
import { CODIGO_QR_VALIDO, mockarApi, type Opcoes } from './mock-api';

// Fase 3 (leitor de QR dos veículos). Câmera e decodificador são simulados no navegador:
// getUserMedia devolve um MediaStream de canvas.captureStream() e o BarcodeDetector é falso
// e controlável (ou ausente, para o fallback jsQR com um QR real gerado aqui).
const VEICULO_ID = '11111111-1111-4111-8111-111111111111';
const DESCONHECIDO = 'zzzz0000zzzz0000zz'; // formato válido, mas a API responde 404 uniforme
const CONTEUDO = `AGU-VEICULO:${CODIGO_QR_VALIDO}`;

type ModoCamera = 'ok' | 'negada' | 'nao-encontrada' | 'sem-mediadevices';
interface ConfigCamera {
  modo?: ModoCamera;
  detector?: 'falso' | 'ausente';
  /** Módulos do QR a desenhar no canvas da câmera (cenário jsQR). */
  matriz?: boolean[][];
}
interface EstadoCam {
  chamadas: string[];
  streams: MediaStream[];
  paradas: number;
  detects: number;
}
type JanelaCamera = Window & { __cam: EstadoCam; __qr: string | null };

/** Matriz de módulos de um QR real (mesma lib de geração do admin). */
function matrizQr(texto: string): boolean[][] {
  const qr = qrcode(0, 'M');
  qr.addData(texto);
  qr.make();
  const n = qr.getModuleCount();
  return Array.from({ length: n }, (_, y) => Array.from({ length: n }, (_, x) => qr.isDark(y, x)));
}
const escuros = (m: boolean[][]) => m.flat().filter(Boolean).length;

/** Instala câmera e detector falsos antes de qualquer script da página. */
async function instalarCamera(page: Page, cfg: ConfigCamera = {}) {
  await page.addInitScript(
    (c: Required<Pick<ConfigCamera, 'modo' | 'detector'>> & { matriz: boolean[][] | null }) => {
      const w = window as unknown as JanelaCamera;
      w.__cam = { chamadas: [], streams: [], paradas: 0, detects: 0 };
      w.__qr = null;

      if (c.detector === 'ausente') {
        Object.defineProperty(window, 'BarcodeDetector', {
          configurable: true,
          writable: true,
          value: undefined,
        });
      } else {
        class DetectorFalso {
          static async getSupportedFormats() {
            return ['qr_code'];
          }
          async detect() {
            w.__cam.detects += 1;
            return w.__qr ? [{ rawValue: w.__qr }] : [];
          }
        }
        Object.defineProperty(window, 'BarcodeDetector', {
          configurable: true,
          writable: true,
          value: DetectorFalso,
        });
      }

      if (c.modo === 'sem-mediadevices') {
        Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: undefined });
        return;
      }
      const getUserMedia = async (restricoes: MediaStreamConstraints) => {
        w.__cam.chamadas.push(JSON.stringify(restricoes));
        if (c.modo === 'negada') throw new DOMException('Permission denied', 'NotAllowedError');
        if (c.modo === 'nao-encontrada') {
          throw new DOMException('Requested device not found', 'NotFoundError');
        }
        const m = c.matriz;
        const escala = 8;
        const margem = 4;
        const canvas = document.createElement('canvas');
        canvas.width = m ? (m.length + margem * 2) * escala : 320;
        canvas.height = m ? (m.length + margem * 2) * escala : 240;
        const ctx = canvas.getContext('2d');
        let n = 0;
        const desenhar = () => {
          if (!ctx) return;
          if (m) {
            ctx.fillStyle = '#fff';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.fillStyle = '#000';
            m.forEach((linha, y) =>
              linha.forEach((escuro, x) => {
                if (escuro) {
                  ctx.fillRect((x + margem) * escala, (y + margem) * escala, escala, escala);
                }
              }),
            );
          } else {
            n += 1;
            ctx.fillStyle = `hsl(${(n * 7) % 360} 40% 50%)`;
            ctx.fillRect(0, 0, canvas.width, canvas.height);
          }
        };
        desenhar();
        const stream = canvas.captureStream(15);
        const timer = window.setInterval(() => {
          if (stream.getTracks().every((t) => t.readyState === 'ended')) {
            window.clearInterval(timer);
            return;
          }
          desenhar();
        }, 100);
        stream.getTracks().forEach((t) => {
          const original = t.stop.bind(t);
          t.stop = () => {
            w.__cam.paradas += 1;
            original();
          };
        });
        w.__cam.streams.push(stream);
        return stream;
      };
      Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: { getUserMedia },
      });
    },
    { modo: cfg.modo ?? 'ok', detector: cfg.detector ?? 'falso', matriz: cfg.matriz ?? null },
  );
}

const camera = (page: Page) =>
  page.evaluate(() => {
    const c = (window as unknown as JanelaCamera).__cam;
    const trilhas = c.streams.flatMap((s) => s.getTracks());
    return {
      chamadas: c.chamadas.map((j) => JSON.parse(j) as { video: unknown; audio: unknown }),
      trilhas: trilhas.length,
      ativas: trilhas.filter((t) => t.readyState === 'live').length,
      paradas: c.paradas,
      detects: c.detects,
    };
  });

/** Faz o detector falso "enxergar" o texto; mantém por `manterMs` e depois limpa. */
async function emitirQr(page: Page, texto: string, manterMs = 0) {
  const antes = (await camera(page)).detects;
  await page.evaluate((t) => ((window as unknown as JanelaCamera).__qr = t), texto);
  // A câmera encerra sozinha após a leitura: basta o detector ver o QR ou as trilhas terem acabado.
  await page.waitForFunction((n0) => {
    const c = (window as unknown as JanelaCamera).__cam;
    const trilhas = c.streams.flatMap((s) => s.getTracks());
    const fechada = trilhas.length > 0 && trilhas.every((t) => t.readyState === 'ended');
    return c.detects >= n0 + 2 || fechada;
  }, antes);
  if (manterMs) await page.waitForTimeout(manterMs);
  await page.evaluate(() => ((window as unknown as JanelaCamera).__qr = null));
}

type Chamada = { padrao: number | number[] };
type JanelaVib = Window & { __vib: Chamada[] };
async function instalarVibracao(page: Page) {
  await page.addInitScript(() => {
    const w = window as unknown as JanelaVib;
    w.__vib = [];
    Object.defineProperty(navigator, 'vibrate', {
      configurable: true,
      writable: true,
      value: (padrao: number | number[]) => {
        w.__vib.push({ padrao });
        return true;
      },
    });
  });
}
const vibracoes = async (page: Page) =>
  (await page.evaluate(() => (window as unknown as JanelaVib).__vib)).map((c) => c.padrao);

/** Tudo que a página escreve no console e todas as requisições (a partir da chamada). */
function observar(page: Page) {
  const consoles: string[] = [];
  const erros: string[] = [];
  const reqs: { metodo: string; url: string; corpo: string | null; tipo: string }[] = [];
  page.on('console', (m) => consoles.push(`${m.type()}: ${m.text()}`));
  page.on('pageerror', (e) => erros.push(e.message));
  page.on('request', (r) =>
    reqs.push({
      metodo: r.method(),
      url: r.url(),
      corpo: r.postData(),
      tipo: r.headers()['content-type'] ?? '',
    }),
  );
  return { consoles, erros, reqs };
}

async function entrar(page: Page, opcoes: Opcoes, email = 'autorizador@exemplo.com') {
  const mock = await mockarApi(page, opcoes);
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await expect(page).toHaveURL(/\/requisicoes$/);
  return mock;
}

/** Login e navegação pelo item de menu "Ler QR" (cliente, sem recarregar a página). */
async function abrirLeitor(page: Page, opcoes: Opcoes = {}, cfg: ConfigCamera = {}) {
  await instalarCamera(page, cfg);
  const mock = await entrar(page, { leitorQr: true, ...opcoes });
  await page.getByRole('link', { name: 'Ler QR', exact: true }).click();
  await expect(page).toHaveURL(/\/veiculos\/ler\/?$/);
  await expect(page.getByRole('button', { name: 'Ler QR Code' })).toBeVisible();
  return mock;
}

async function iniciarCamera(page: Page) {
  await page.getByRole('button', { name: 'Ler QR Code' }).click();
  await expect(page.getByText('Posicione o QR Code dentro da moldura.')).toBeVisible();
}

const cartao = (page: Page) => page.locator('article');

test.describe('flag leitor_qr_veiculos desligada', () => {
  for (const opcoes of [{ leitorQr: false }, {}] as Opcoes[]) {
    test(`flag ${JSON.stringify(opcoes)}: sem item de menu e a página recusa`, async ({ page }) => {
      await instalarCamera(page);
      const mock = await entrar(page, opcoes);
      await expect(page.getByRole('link', { name: 'Ler QR', exact: true })).toHaveCount(0);
      await page.goto('/veiculos/ler');
      await expect(page.getByText('O leitor de QR Code não está habilitado.')).toBeVisible();
      await expect(page.getByRole('button', { name: 'Ler QR Code' })).toHaveCount(0);
      await expect(page.getByLabel('Digitar o código do veículo')).toHaveCount(0);
      await page.waitForTimeout(300);
      expect((await camera(page)).chamadas).toEqual([]);
      expect(mock.consultasQr).toEqual([]);
    });
  }

  test('mesmo forçando a consulta, a API recusa (403) com o recurso desligado', async ({
    page,
  }) => {
    const mock = await entrar(page, {});
    const status = await page.evaluate(async (c) => {
      const sessao = Object.entries(localStorage).find(([k]) => k.includes('auth-token'))?.[1];
      const token = (JSON.parse(sessao ?? '{}') as { access_token?: string }).access_token ?? '';
      const r = await fetch('/api/veiculos/por-codigo', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ codigo: c }),
      });
      return r.status;
    }, CODIGO_QR_VALIDO);
    expect(status).toBe(403);
    expect(mock.consultasQr).toEqual([CODIGO_QR_VALIDO]);
  });
});

test.describe('leitor com a flag ligada (mobile)', () => {
  test('item de menu aparece; a câmera NUNCA abre ao carregar, só no clique; pede câmera traseira', async ({
    page,
  }) => {
    await abrirLeitor(page);
    await page.waitForTimeout(800);
    expect((await camera(page)).chamadas).toHaveLength(0);
    // Também ao recarregar direto na página.
    await page.reload();
    await expect(page.getByRole('button', { name: 'Ler QR Code' })).toBeVisible();
    await page.waitForTimeout(800);
    expect((await camera(page)).chamadas).toHaveLength(0);

    await iniciarCamera(page);
    const { chamadas, audio } = await camera(page).then((c) => ({
      chamadas: c.chamadas,
      audio: c.chamadas[0]?.audio,
    }));
    expect(chamadas).toHaveLength(1);
    expect(chamadas[0]?.video).toEqual({ facingMode: { ideal: 'environment' } });
    expect(audio).toBe(false);
  });

  test('QR válido: 1 consulta POST (código só no corpo) e exibe descrição, placa, disponibilidade; Solicitar leva ao formulário pré-selecionado', async ({
    page,
  }) => {
    const mock = await abrirLeitor(page);
    const rede = observar(page);
    await iniciarCamera(page);
    await emitirQr(page, CONTEUDO);
    await expect(cartao(page).getByRole('heading', { name: 'Veículo 01' })).toBeVisible();
    await expect(cartao(page).getByText('placa ABC-1D23')).toBeVisible();
    await expect(cartao(page).getByText('Disponível', { exact: true })).toBeVisible();
    expect(mock.consultasQr).toEqual([CODIGO_QR_VALIDO]);
    const consultas = rede.reqs.filter((r) => r.url.includes('/api/veiculos/por-codigo'));
    expect(consultas).toHaveLength(1);
    expect(consultas[0]?.metodo).toBe('POST');
    expect(new URL(consultas[0]?.url ?? '').pathname).toBe('/api/veiculos/por-codigo');
    expect(JSON.parse(consultas[0]?.corpo ?? '{}')).toEqual({ codigo: CODIGO_QR_VALIDO });
    expect(rede.reqs.filter((r) => r.url.includes(CODIGO_QR_VALIDO))).toEqual([]);
    const solicitar = page.getByRole('link', { name: 'Solicitar este veículo' });
    await expect(solicitar).toBeVisible();
    expect((await solicitar.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    await solicitar.click();
    await expect(page).toHaveURL(new RegExp(`/requisicoes/nova/?\\?veiculo=${VEICULO_ID}$`));
    await expect(page.locator('#veiculo')).toHaveValue(VEICULO_ID);
    // A câmera parou ao sair da página.
    expect((await camera(page)).ativas).toBe(0);
  });

  test('veículo indisponível: mostra motivo da API e não oferece Solicitar', async ({ page }) => {
    await abrirLeitor(page, {
      veiculoQr: { disponivel: false, motivo: 'Em manutenção até amanhã.' },
    });
    await iniciarCamera(page);
    await emitirQr(page, CONTEUDO);
    await expect(cartao(page).getByText('Indisponível', { exact: true })).toBeVisible();
    await expect(cartao(page).getByText('Em manutenção até amanhã.')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Solicitar este veículo' })).toHaveCount(0);
  });

  test('Solicitar só aparece se acoes_permitidas incluir solicitar (mesmo "disponível")', async ({
    page,
  }) => {
    await abrirLeitor(page, { veiculoQr: { disponivel: true, acoes: [] } });
    await iniciarCamera(page);
    await emitirQr(page, CONTEUDO);
    await expect(cartao(page).getByRole('heading', { name: 'Veículo 01' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Solicitar este veículo' })).toHaveCount(0);
  });

  test('leituras repetidas do mesmo código em < 3 s geram 1 só consulta (mesmo reabrindo a câmera)', async ({
    page,
  }) => {
    const mock = await abrirLeitor(page);
    await iniciarCamera(page);
    await emitirQr(page, CONTEUDO, 300);
    await expect(cartao(page).getByRole('heading', { name: 'Veículo 01' })).toBeVisible();
    // Reabre e o mesmo QR continua à vista: a antiduplicidade ignora a repetição.
    await iniciarCamera(page);
    const antes = (await camera(page)).detects;
    await emitirQr(page, CONTEUDO, 1200);
    expect((await camera(page)).detects - antes).toBeGreaterThanOrEqual(2); // o detector viu de novo
    expect((await camera(page)).ativas).toBeGreaterThanOrEqual(1); // e a câmera seguiu aberta
    expect(mock.consultasQr).toEqual([CODIGO_QR_VALIDO]);
  });

  test('após leitura bem-sucedida a câmera encerra sozinha (todas as trilhas ended) e reabre em Ler QR Code', async ({
    page,
  }) => {
    const mock = await abrirLeitor(page);
    await iniciarCamera(page);
    await emitirQr(page, CONTEUDO, 200);
    await expect(cartao(page).getByRole('heading', { name: 'Veículo 01' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Ler QR Code' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Encerrar câmera' })).toHaveCount(0);
    const c = await camera(page);
    expect(c.trilhas).toBeGreaterThanOrEqual(1);
    expect(c.ativas).toBe(0);
    expect(c.paradas).toBe(c.trilhas);
    const estados = await page.evaluate(() =>
      (window as unknown as JanelaCamera).__cam.streams
        .flatMap((s) => s.getTracks())
        .map((t) => t.readyState),
    );
    expect(estados.every((e) => e === 'ended')).toBe(true);
    // O detector não roda mais com a câmera fechada.
    const n = c.detects;
    await page.waitForTimeout(600);
    expect((await camera(page)).detects).toBe(n);
    // Reabrir pede uma nova câmera.
    await iniciarCamera(page);
    expect((await camera(page)).chamadas).toHaveLength(2);
    expect((await camera(page)).ativas).toBeGreaterThanOrEqual(1);
    expect(mock.consultasQr).toEqual([CODIGO_QR_VALIDO]);
  });

  test('consulta em andamento mostra Spinner e uma consulta por vez', async ({ page }) => {
    const mock = await abrirLeitor(page);
    let liberar!: () => void;
    const portao = new Promise<void>((ok) => (liberar = ok));
    await page.route('**/api/veiculos/por-codigo', async (route) => {
      await portao;
      await route.fallback();
    });
    await iniciarCamera(page);
    await emitirQr(page, CONTEUDO, 600);
    await expect(page.getByText('Consultando veículo...')).toBeVisible();
    await expect(page.locator('p[role="status"] svg[class*="lucide-loader"]')).toHaveCount(1);
    liberar();
    await expect(cartao(page).getByRole('heading', { name: 'Veículo 01' })).toBeVisible();
    expect(mock.consultasQr).toEqual([CODIGO_QR_VALIDO]);
  });

  const INVALIDOS: [string, string][] = [
    ['URL', `https://exemplo.com/veiculo/${CODIGO_QR_VALIDO}`],
    ['JWT', 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJh'],
    ['JSON', `{"codigo":"${CODIGO_QR_VALIDO}"}`],
    ['texto livre', 'veículo da frota'],
    ['prefixo certo, código curto', 'AGU-VEICULO:abc123'],
    ['código em maiúsculas', `AGU-VEICULO:${CODIGO_QR_VALIDO.toUpperCase()}`],
    ['código com sufixo', `AGU-VEICULO:${CODIGO_QR_VALIDO}?x=1`],
  ];
  for (const [nome, texto] of INVALIDOS) {
    test(`QR inválido (${nome}): "QR Code inválido" e ZERO chamadas à API`, async ({ page }) => {
      const mock = await abrirLeitor(page);
      const rede = observar(page);
      await iniciarCamera(page);
      await emitirQr(page, texto, 300);
      await expect(page.getByText('QR Code inválido.')).toBeVisible();
      await expect(cartao(page)).toHaveCount(0);
      expect(mock.consultasQr).toEqual([]);
      expect(rede.reqs.filter((r) => r.url.includes('/api/veiculos/por-codigo'))).toEqual([]);
    });
  }

  test('veículo inexistente (404 uniforme): exibe a mensagem da API', async ({ page }) => {
    const mock = await abrirLeitor(page);
    await iniciarCamera(page);
    await emitirQr(page, `AGU-VEICULO:${DESCONHECIDO}`);
    await expect(page.getByText('Veículo não encontrado.')).toBeVisible();
    await expect(cartao(page)).toHaveCount(0);
    expect(mock.consultasQr).toEqual([DESCONHECIDO]);
  });

  test('permissão da câmera negada (NotAllowedError): orientação e digitação manual funciona', async ({
    page,
  }) => {
    const mock = await abrirLeitor(page, {}, { modo: 'negada' });
    await page.getByRole('button', { name: 'Ler QR Code' }).click();
    await expect(page.getByText(/permissão da câmera foi negada/)).toBeVisible();
    // Sem segunda tentativa de getUserMedia quando o usuário negou.
    expect((await camera(page)).chamadas).toHaveLength(1);
    await expect(page.getByRole('button', { name: 'Ler QR Code' })).toBeVisible();
    await page.getByLabel('Digitar o código do veículo').fill(CODIGO_QR_VALIDO);
    await page.getByRole('button', { name: 'Consultar' }).click();
    await expect(cartao(page).getByRole('heading', { name: 'Veículo 01' })).toBeVisible();
    expect(mock.consultasQr).toEqual([CODIGO_QR_VALIDO]);
  });

  test('câmera inexistente (NotFoundError): orientação e digitação manual funciona', async ({
    page,
  }) => {
    const mock = await abrirLeitor(page, {}, { modo: 'nao-encontrada' });
    await page.getByRole('button', { name: 'Ler QR Code' }).click();
    await expect(page.getByText(/Não foi possível usar a câmera/)).toBeVisible();
    await page.getByLabel('Digitar o código do veículo').fill(CODIGO_QR_VALIDO);
    await page.getByRole('button', { name: 'Consultar' }).click();
    await expect(cartao(page).getByRole('heading', { name: 'Veículo 01' })).toBeVisible();
    expect(mock.consultasQr).toEqual([CODIGO_QR_VALIDO]);
  });

  test('sem navigator.mediaDevices (sem HTTPS/aparelho): orientação e digitação manual funciona', async ({
    page,
  }) => {
    const mock = await abrirLeitor(page, {}, { modo: 'sem-mediadevices' });
    await page.getByRole('button', { name: 'Ler QR Code' }).click();
    await expect(page.getByText(/Não foi possível usar a câmera/)).toBeVisible();
    await page.getByLabel('Digitar o código do veículo').fill(CONTEUDO);
    await page.getByRole('button', { name: 'Consultar' }).click();
    await expect(cartao(page).getByRole('heading', { name: 'Veículo 01' })).toBeVisible();
    expect(mock.consultasQr).toEqual([CODIGO_QR_VALIDO]);
  });

  test('digitação manual: válida (aceita maiúsculas) consulta; inválida não chama a API', async ({
    page,
  }) => {
    const mock = await abrirLeitor(page);
    const campo = page.getByLabel('Digitar o código do veículo');
    await campo.fill('abc');
    await page.getByRole('button', { name: 'Consultar' }).click();
    await expect(page.getByText('QR Code inválido.')).toBeVisible();
    expect(mock.consultasQr).toEqual([]);

    await campo.fill(`  ${CODIGO_QR_VALIDO.toUpperCase()} `);
    await page.getByRole('button', { name: 'Consultar' }).click();
    await expect(cartao(page).getByRole('heading', { name: 'Veículo 01' })).toBeVisible();
    await expect(page.getByText('QR Code inválido.')).toHaveCount(0);
    expect(mock.consultasQr).toEqual([CODIGO_QR_VALIDO]);

    await campo.fill(`AGU-VEICULO:${DESCONHECIDO}`);
    await page.getByRole('button', { name: 'Consultar' }).click();
    await expect(page.getByText('Veículo não encontrado.')).toBeVisible();
    await expect(cartao(page)).toHaveCount(0);
  });

  test('campos e alvos de toque >= 44 px', async ({ page }) => {
    await abrirLeitor(page);
    for (const alvo of [
      page.getByRole('button', { name: 'Ler QR Code' }),
      page.getByLabel('Digitar o código do veículo'),
      page.getByRole('button', { name: 'Consultar' }),
    ]) {
      expect((await alvo.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    }
  });

  test('Encerrar câmera para TODAS as trilhas', async ({ page }) => {
    await abrirLeitor(page);
    await iniciarCamera(page);
    const aberta = await camera(page);
    expect(aberta.trilhas).toBeGreaterThanOrEqual(1);
    expect(aberta.ativas).toBe(aberta.trilhas);
    await page.getByRole('button', { name: 'Encerrar câmera' }).click();
    await expect(page.getByRole('button', { name: 'Ler QR Code' })).toBeVisible();
    const parada = await camera(page);
    expect(parada.ativas).toBe(0);
    expect(parada.paradas).toBe(parada.trilhas);
    // Detector não é mais chamado depois de encerrar.
    const n = parada.detects;
    await page.waitForTimeout(600);
    expect((await camera(page)).detects).toBe(n);
  });

  test('ao sair da página a câmera é encerrada', async ({ page }) => {
    await abrirLeitor(page);
    await iniciarCamera(page);
    expect((await camera(page)).ativas).toBeGreaterThanOrEqual(1);
    await page.getByRole('link', { name: 'Requisições', exact: true }).click();
    await expect(page).toHaveURL(/\/requisicoes\/?$/);
    const c = await camera(page);
    expect(c.ativas).toBe(0);
    expect(c.paradas).toBe(c.trilhas);
  });

  test('com a aba oculta a câmera é encerrada', async ({ page }) => {
    await abrirLeitor(page);
    await iniciarCamera(page);
    expect((await camera(page)).ativas).toBeGreaterThanOrEqual(1);
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await expect(page.getByRole('button', { name: 'Ler QR Code' })).toBeVisible();
    const c = await camera(page);
    expect(c.ativas).toBe(0);
    expect(c.paradas).toBe(c.trilhas);
  });

  test('nenhuma imagem/frame é enviada: só o POST do código, no corpo e nunca na URL', async ({
    page,
  }) => {
    const mock = await abrirLeitor(page);
    const rede = observar(page);
    await iniciarCamera(page);
    await emitirQr(page, CONTEUDO, 400);
    await expect(cartao(page).getByRole('heading', { name: 'Veículo 01' })).toBeVisible();
    // A única escrita é a consulta; qualquer outro POST/PUT/PATCH ou payload de imagem reprova.
    const consultas = rede.reqs.filter((r) => r.url.includes('/api/veiculos/por-codigo'));
    expect(consultas).toHaveLength(1);
    const consulta = consultas[0];
    expect(consulta?.metodo).toBe('POST');
    expect(consulta?.tipo).toContain('application/json');
    expect(JSON.parse(consulta?.corpo ?? '{}')).toEqual({ codigo: CODIGO_QR_VALIDO });
    expect(new URL(consulta?.url ?? '').pathname).toBe('/api/veiculos/por-codigo');
    const outras = rede.reqs.filter((r) => r !== consulta);
    expect(outras.filter((r) => r.metodo !== 'GET' && r.metodo !== 'HEAD')).toEqual([]);
    expect(outras.filter((r) => r.corpo !== null)).toEqual([]);
    expect(rede.reqs.filter((r) => /^(image|video)\//.test(r.tipo))).toEqual([]);
    expect(rede.reqs.filter((r) => /^(blob|data):/.test(r.url))).toEqual([]);
    // O código não aparece em nenhuma URL (path ou query).
    expect(rede.reqs.filter((r) => r.url.includes(CODIGO_QR_VALIDO))).toEqual([]);
    expect(mock.consultasQr).toEqual([CODIGO_QR_VALIDO]);
  });

  test('o código lido não vai a localStorage, sessionStorage, cookies, console nem à URL', async ({
    page,
  }) => {
    await abrirLeitor(page);
    const rede = observar(page);
    await iniciarCamera(page);
    await emitirQr(page, CONTEUDO, 300);
    await expect(cartao(page).getByRole('heading', { name: 'Veículo 01' })).toBeVisible();
    const guardado = await page.evaluate(() =>
      JSON.stringify({ l: { ...localStorage }, s: { ...sessionStorage }, c: document.cookie }),
    );
    expect(guardado).not.toContain(CODIGO_QR_VALIDO);
    expect(rede.consoles.join('\n')).not.toContain(CODIGO_QR_VALIDO);
    expect(rede.erros).toEqual([]);
    expect(page.url()).not.toContain(CODIGO_QR_VALIDO);
    // Nem após encerrar e recarregar.
    await page.reload();
    await expect(page.getByRole('button', { name: 'Ler QR Code' })).toBeVisible();
    expect(
      await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage })),
    ).not.toContain(CODIGO_QR_VALIDO);
  });
});

test.describe('fallback jsQR (sem BarcodeDetector, QR real desenhado no canvas)', () => {
  test('lê AGU-VEICULO:<codigo> e consulta uma vez', async ({ page }) => {
    const mock = await abrirLeitor(page, {}, { detector: 'ausente', matriz: matrizQr(CONTEUDO) });
    expect(
      await page.evaluate(() => typeof (window as { BarcodeDetector?: unknown }).BarcodeDetector),
    ).toBe('undefined');
    // O QR é lido já no primeiro quadro: a câmera encerra antes do texto de orientação aparecer.
    await page.getByRole('button', { name: 'Ler QR Code' }).click();
    await expect(cartao(page).getByRole('heading', { name: 'Veículo 01' })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole('button', { name: 'Ler QR Code' })).toBeVisible();
    expect((await camera(page)).ativas).toBe(0);
    expect(mock.consultasQr).toEqual([CODIGO_QR_VALIDO]);
    expect((await camera(page)).detects).toBe(0); // o detector nativo nunca foi usado
  });

  test('QR real com conteúdo inválido (URL): "QR Code inválido" e zero chamadas', async ({
    page,
  }) => {
    const mock = await abrirLeitor(
      page,
      {},
      { detector: 'ausente', matriz: matrizQr(`https://exemplo.com/${CODIGO_QR_VALIDO}`) },
    );
    // O QR é lido já no primeiro quadro: a câmera encerra antes do texto de orientação aparecer.
    await page.getByRole('button', { name: 'Ler QR Code' }).click();
    await expect(page.getByText('QR Code inválido.')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole('button', { name: 'Ler QR Code' })).toBeVisible();
    expect(mock.consultasQr).toEqual([]);
  });
});

test.describe('vibração de leitura (feedback_tatil)', () => {
  test.use({ reducedMotion: 'no-preference' });

  test('flag ligada: vibra UMA vez ([25]) após a consulta bem-sucedida', async ({ page }) => {
    await instalarVibracao(page);
    await abrirLeitor(page, { tatil: true });
    await iniciarCamera(page);
    expect(await vibracoes(page)).toEqual([]); // abrir a câmera não vibra
    await emitirQr(page, CONTEUDO, 500);
    await expect(cartao(page).getByRole('heading', { name: 'Veículo 01' })).toBeVisible();
    await expect.poll(() => vibracoes(page)).toEqual([[25]]);
  });

  test('flag ligada: QR inválido e 404 não vibram', async ({ page }) => {
    await instalarVibracao(page);
    await abrirLeitor(page, { tatil: true });
    await iniciarCamera(page);
    await emitirQr(page, 'texto livre', 300);
    await expect(page.getByText('QR Code inválido.')).toBeVisible();
    await iniciarCamera(page); // a leitura anterior encerrou a câmera
    await emitirQr(page, `AGU-VEICULO:${DESCONHECIDO}`, 300);
    await expect(page.getByText('Veículo não encontrado.')).toBeVisible();
    expect(await vibracoes(page)).toEqual([]);
  });

  for (const opcoes of [{ tatil: false }, {}] as Opcoes[]) {
    test(`flag feedback_tatil ${JSON.stringify(opcoes)}: leitura bem-sucedida não vibra`, async ({
      page,
    }) => {
      await instalarVibracao(page);
      await abrirLeitor(page, opcoes);
      await iniciarCamera(page);
      await emitirQr(page, CONTEUDO, 500);
      await expect(cartao(page).getByRole('heading', { name: 'Veículo 01' })).toBeVisible();
      await page.waitForTimeout(300);
      expect(await vibracoes(page)).toEqual([]);
    });
  }
});

test.describe('admin: QR do veículo', () => {
  /** Stub de window.print (popup de impressão) em todas as páginas do contexto. */
  async function entrarAdmin(page: Page, opcoes: Opcoes) {
    await page.context().addInitScript(() => {
      const w = window as unknown as { __impressoes?: number };
      window.print = () => {
        w.__impressoes = (w.__impressoes ?? 0) + 1;
      };
    });
    const mock = await entrar(page, { comAdmin: true, ...opcoes });
    await page.goto('/admin/veiculos');
    await expect(page.getByRole('heading', { name: 'Veículo 01' })).toBeVisible();
    return mock;
  }
  const svgQr = (page: Page) => page.getByRole('img', { name: 'QR Code do veículo Veículo 01' });

  test('com codigo_publico: renderiza o QR (conteúdo AGU-VEICULO:<codigo>), Imprimir e Rotacionar', async ({
    page,
  }) => {
    await entrarAdmin(page, { adminVeiculosQr: { codigoPublico: CODIGO_QR_VALIDO } });
    await expect(svgQr(page)).toBeVisible();
    const d = (await svgQr(page).locator('path').getAttribute('d')) ?? '';
    expect(d.match(/M/g)?.length).toBe(escuros(matrizQr(CONTEUDO)));
    const imprimir = page.getByRole('button', { name: 'Imprimir QR Code de Veículo 01' });
    const rotacionar = page.getByRole('button', { name: 'Rotacionar código de Veículo 01' });
    await expect(imprimir).toBeVisible();
    await expect(rotacionar).toBeVisible();
    expect((await imprimir.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    expect((await rotacionar.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    // O código em si não aparece como texto na tela.
    await expect(page.getByText(CODIGO_QR_VALIDO)).toHaveCount(0);
  });

  for (const [nome, codigo] of [
    ['null', null],
    ['ausente', undefined],
  ] as const) {
    test(`codigo_publico ${nome}: sem QR, sem Imprimir e sem Rotacionar`, async ({ page }) => {
      await entrarAdmin(page, { adminVeiculosQr: { codigoPublico: codigo } });
      await expect(svgQr(page)).toHaveCount(0);
      await expect(page.getByRole('button', { name: /Imprimir QR Code/ })).toHaveCount(0);
      await expect(page.getByRole('button', { name: /Rotacionar código/ })).toHaveCount(0);
    });
  }

  test('sem rotacionar_codigo em acoes_permitidas: Rotacionar não aparece', async ({ page }) => {
    await entrarAdmin(page, {
      adminVeiculosQr: { codigoPublico: CODIGO_QR_VALIDO, acoes: ['editar', 'desativar'] },
    });
    await expect(svgQr(page)).toBeVisible();
    await expect(page.getByRole('button', { name: /Imprimir QR Code/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /Rotacionar código/ })).toHaveCount(0);
  });

  test('Imprimir abre janela só com o QR e aciona a impressão', async ({ page }) => {
    await entrarAdmin(page, { adminVeiculosQr: { codigoPublico: CODIGO_QR_VALIDO } });
    const popupPromise = page.waitForEvent('popup');
    await page.getByRole('button', { name: 'Imprimir QR Code de Veículo 01' }).click();
    const popup = await popupPromise;
    await expect(popup.locator('svg')).toBeVisible();
    await expect(popup.locator('h1')).toHaveText('Veículo 01');
    await expect(popup.getByText('ABC1D23')).toBeVisible();
    expect(await popup.content()).not.toContain(CODIGO_QR_VALIDO);
    await expect
      .poll(() => popup.evaluate(() => (window as { __impressoes?: number }).__impressoes ?? 0))
      .toBe(1);
  });

  test('janela de impressão bloqueada: avisa o usuário', async ({ page }) => {
    await entrarAdmin(page, { adminVeiculosQr: { codigoPublico: CODIGO_QR_VALIDO } });
    await page.evaluate(() => (window.open = () => null));
    await page.getByRole('button', { name: 'Imprimir QR Code de Veículo 01' }).click();
    await expect(page.getByText(/bloqueou a janela de impressão/)).toBeVisible();
  });

  test('Rotacionar: confirma em Dialogo, mostra Spinner, atualiza o QR; Voltar não chama a API', async ({
    page,
  }) => {
    const mock = await entrarAdmin(page, { adminVeiculosQr: { codigoPublico: CODIGO_QR_VALIDO } });
    const dAntes = (await svgQr(page).locator('path').getAttribute('d')) ?? '';
    const abrir = page.getByRole('button', { name: 'Rotacionar código de Veículo 01' });

    // Voltar fecha sem chamar a API.
    await abrir.click();
    const dialogo = page.getByRole('alertdialog');
    await expect(dialogo.getByRole('heading', { name: 'Rotacionar código?' })).toBeVisible();
    await expect(dialogo.getByText(/deixará de funcionar/)).toBeVisible();
    await dialogo.getByRole('button', { name: 'Voltar' }).click();
    await expect(dialogo).toBeHidden();
    expect(mock.rotacoesQr).toEqual([]);

    // Confirma, com a resposta segurada para observar o estado de carregamento.
    let liberar!: () => void;
    const portao = new Promise<void>((ok) => (liberar = ok));
    await page.route('**/api/admin/veiculos/*/rotacionar-codigo', async (route) => {
      await portao;
      await route.fallback();
    });
    await abrir.click();
    await dialogo.getByRole('button', { name: 'Rotacionar', exact: true }).click();
    const ocupado = dialogo.getByRole('button', { name: 'Rotacionando...' });
    await expect(ocupado).toBeDisabled();
    await expect(ocupado).toHaveAttribute('aria-busy', 'true');
    await expect(ocupado.locator('svg[class*="lucide-loader"]')).toHaveCount(1);
    await expect(dialogo.getByRole('button', { name: 'Voltar' })).toBeDisabled();
    expect(mock.rotacoesQr).toEqual([]); // ainda segurada
    liberar();
    await expect(dialogo).toBeHidden();
    expect(mock.rotacoesQr).toEqual(['POST']);
    await expect(svgQr(page)).toBeVisible();
    await expect
      .poll(async () => (await svgQr(page).locator('path').getAttribute('d')) ?? '')
      .not.toBe(dAntes);
  });

  test('erro da API ao rotacionar: mensagem exibida no Dialogo e o QR não muda', async ({
    page,
  }) => {
    await entrarAdmin(page, {
      adminVeiculosQr: { codigoPublico: CODIGO_QR_VALIDO },
      erroRotacao: {
        status: 409,
        codigo: 'conflito',
        mensagem: 'Não foi possível rotacionar agora.',
      },
    });
    const dAntes = (await svgQr(page).locator('path').getAttribute('d')) ?? '';
    await page.getByRole('button', { name: 'Rotacionar código de Veículo 01' }).click();
    const dialogo = page.getByRole('alertdialog');
    await dialogo.getByRole('button', { name: 'Rotacionar', exact: true }).click();
    await expect(dialogo.getByText('Não foi possível rotacionar agora.')).toBeVisible();
    await expect(dialogo.getByRole('button', { name: 'Rotacionar', exact: true })).toBeEnabled();
    await dialogo.getByRole('button', { name: 'Voltar' }).click();
    await expect(svgQr(page).locator('path')).toHaveAttribute('d', dAntes);
  });
});

test.describe('viewport desktop', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('leitor: leitura válida e layout sem rolagem horizontal', async ({ page }) => {
    const mock = await abrirLeitor(page);
    await iniciarCamera(page);
    await emitirQr(page, CONTEUDO, 300);
    await expect(cartao(page).getByRole('heading', { name: 'Veículo 01' })).toBeVisible();
    expect(mock.consultasQr).toEqual([CODIGO_QR_VALIDO]);
    const largura = await page.evaluate(() => ({
      conteudo: document.documentElement.scrollWidth,
      janela: window.innerWidth,
    }));
    expect(largura.conteudo).toBeLessThanOrEqual(largura.janela);
  });

  test('admin: QR renderizado e Rotacionar disponível', async ({ page }) => {
    await mockarApi(page, { comAdmin: true, adminVeiculosQr: { codigoPublico: CODIGO_QR_VALIDO } });
    await page.goto('/login');
    await page.getByLabel('E-mail').fill('autorizador@exemplo.com');
    await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
    await page.getByRole('button', { name: 'Entrar' }).click();
    await expect(page).toHaveURL(/\/requisicoes$/);
    await page.goto('/admin/veiculos');
    await expect(page.getByRole('img', { name: 'QR Code do veículo Veículo 01' })).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Rotacionar código de Veículo 01' }),
    ).toBeVisible();
  });
});
