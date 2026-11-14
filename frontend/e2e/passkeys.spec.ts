import { expect, test, type Page } from '@playwright/test';
import { mockarApi, type Opcoes } from './mock-api';

// Fase 5 (Passkeys/WebAuthn, 1ª etapa). O autenticador é simulado no navegador
// (navigator.credentials.create/get + PublicKeyCredential falsos); a criptografia real é do pytest.
// Aqui se valida o contrato com a API e a UI. A API fake é a de mock-api.ts (/api/passkeys*).
const ATE = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();
const EXISTENTE = { id: 'pk-1', apelido: 'Notebook', criada_em: ATE(3), ultimo_uso_em: ATE(1) };
const BASE64URL = /^[A-Za-z0-9_-]+$/;

type Modo = 'ok' | 'cancelar' | 'ja_cadastrada' | 'abortar' | 'tempo' | 'nao_suportado' | 'travar';
interface EstadoPk {
  modo: Modo;
  create: { challenge: string; userId: string; excluidas: string[]; uv: string | null }[];
  get: { challenge: string; permitidas: string[] }[];
  liberar: (() => void) | null;
}
type JanelaPk = Window & { __pk: EstadoPk };

// Valores binários reconhecíveis: nada disso pode aparecer em storage, console ou tela.
const RAW_ID = 'CRED-RAW-ID-NOVO';
const MARCAS = ['CLIENT-DATA-FAKE', 'ATTEST-OBJ-FAKE', 'AUTH-DATA-FAKE', 'ASSINATURA-FAKE', RAW_ID];
const b64u = (t: string) => Buffer.from(t).toString('base64url');

/** Instala um autenticador falso antes de qualquer script da página. Sem `suporte`, remove a API. */
async function instalarAutenticador(page: Page, suporte = true) {
  await page.addInitScript(
    (c: { suporte: boolean; rawId: string }) => {
      if (!c.suporte) {
        Object.defineProperty(window, 'PublicKeyCredential', {
          configurable: true,
          writable: true,
          value: undefined,
        });
        return;
      }
      const buf = (t: string) => new TextEncoder().encode(t).buffer;
      const para64 = (b: ArrayBuffer | ArrayBufferView) => {
        const u = new Uint8Array(b instanceof ArrayBuffer ? b : (b.buffer as ArrayBuffer));
        let s = '';
        u.forEach((x) => (s += String.fromCharCode(x)));
        return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
      };
      const estado: EstadoPk = { modo: 'ok', create: [], get: [], liberar: null };
      (window as unknown as JanelaPk).__pk = estado;
      class PublicKeyCredentialFalso {
        id = 'Q1JFRC1SQVctSUQtTk9WTw';
        type = 'public-key';
        authenticatorAttachment = 'platform';
        rawId: ArrayBuffer;
        response: object;
        constructor(response: object) {
          this.rawId = buf(c.rawId);
          this.response = response;
        }
        getClientExtensionResults() {
          return {};
        }
      }
      Object.defineProperty(window, 'PublicKeyCredential', {
        configurable: true,
        writable: true,
        value: PublicKeyCredentialFalso,
      });
      const decidir = async (): Promise<void> => {
        if (estado.modo === 'travar') {
          await new Promise<void>((r) => (estado.liberar = r));
          return;
        }
        const erros: Record<string, [string, string]> = {
          cancelar: ['The operation either timed out or was not allowed.', 'NotAllowedError'],
          ja_cadastrada: ['The user attempted to register an authenticator.', 'InvalidStateError'],
          abortar: ['The operation was aborted.', 'AbortError'],
          tempo: ['Timed out.', 'TimeoutError'],
          nao_suportado: ['No compatible authenticator.', 'NotSupportedError'],
        };
        const e = erros[estado.modo];
        if (e) throw new DOMException(e[0], e[1]);
      };
      const credenciais = {
        async create(o: CredentialCreationOptions) {
          const p = o.publicKey as PublicKeyCredentialCreationOptions;
          estado.create.push({
            challenge: para64(p.challenge as ArrayBuffer),
            userId: para64(p.user.id as ArrayBuffer),
            excluidas: (p.excludeCredentials ?? []).map((d) => para64(d.id as ArrayBuffer)),
            uv: p.authenticatorSelection?.userVerification ?? null,
          });
          await decidir();
          return new PublicKeyCredentialFalso({
            clientDataJSON: buf('CLIENT-DATA-FAKE'),
            attestationObject: buf('ATTEST-OBJ-FAKE'),
            getTransports: () => ['internal'],
          });
        },
        async get(o: CredentialRequestOptions) {
          const p = o.publicKey as PublicKeyCredentialRequestOptions;
          estado.get.push({
            challenge: para64(p.challenge as ArrayBuffer),
            permitidas: (p.allowCredentials ?? []).map((d) => para64(d.id as ArrayBuffer)),
          });
          await decidir();
          return new PublicKeyCredentialFalso({
            clientDataJSON: buf('CLIENT-DATA-FAKE'),
            authenticatorData: buf('AUTH-DATA-FAKE'),
            signature: buf('ASSINATURA-FAKE'),
            userHandle: null,
          });
        },
      };
      Object.defineProperty(navigator, 'credentials', { configurable: true, value: credenciais });
    },
    { suporte, rawId: RAW_ID },
  );
}

const definirModo = (page: Page, modo: Modo) =>
  page.evaluate((m) => ((window as unknown as JanelaPk).__pk.modo = m), modo);
const estadoPk = (page: Page) => page.evaluate(() => (window as unknown as JanelaPk).__pk);

function coletarErros(page: Page) {
  const erros: string[] = [];
  page.on('pageerror', (e) => erros.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    // 4xx/5xx simulados geram "Failed to load resource" do próprio navegador; o resto é ruído do app.
    if (!/Failed to load resource|Service Worker registration blocked/.test(m.text()))
      erros.push(`console.${m.type()}: ${m.text()}`);
  });
  return erros;
}

async function guardado(page: Page) {
  return page.evaluate(() => {
    const ler = (s: Storage) => Object.fromEntries(Object.keys(s).map((k) => [k, s.getItem(k)]));
    return { local: ler(localStorage), sessao: ler(sessionStorage) };
  });
}

/** Nenhuma chave nova e nenhum valor com credencial, desafio ou resposta WebAuthn. */
async function conferirStorage(page: Page, antes: Awaited<ReturnType<typeof guardado>>) {
  const depois = await guardado(page);
  expect(Object.keys(depois.local).sort()).toEqual(Object.keys(antes.local).sort());
  expect(Object.keys(depois.sessao).sort()).toEqual(Object.keys(antes.sessao).sort());
  const tudo = JSON.stringify(depois);
  const proibidos = [
    ...MARCAS.map(b64u),
    b64u('desafio-registro-0001'),
    b64u('desafio-autenticacao-0001'),
    'clientDataJSON',
    'attestationObject',
    'signature',
  ];
  for (const p of proibidos) expect(tudo).not.toContain(p);
  expect(Object.keys({ ...depois.local, ...depois.sessao }).join(' ')).not.toMatch(
    /passkey|webauthn|credencial|credential/i,
  );
}

async function abrir(
  page: Page,
  opcoes: Opcoes = {},
  { suporte = true, email = 'solicitante@exemplo.com' } = {},
) {
  await instalarAutenticador(page, suporte);
  const erros = coletarErros(page);
  const mock = await mockarApi(page, { passkeys: true, ...opcoes });
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await expect(page).toHaveURL(/\/requisicoes$/);
  await page.getByRole('link', { name: /^Conta:/ }).click();
  await expect(page).toHaveURL(/\/seguranca\/?$/);
  return { mock, erros };
}

const secao = (page: Page) => page.getByRole('region', { name: 'Passkeys' });
const adicionar = (page: Page) =>
  page.getByRole('button', { name: /Adicionar passkey|Aguardando/ });
const confirmar = (page: Page) =>
  page.getByRole('button', { name: /Confirmar com passkey|Aguardando/ }).last();
const caminhos = (m: Awaited<ReturnType<typeof mockarApi>>) =>
  m.chamadasPasskeys.map((c) => `${c.metodo} ${c.caminho}`);

test.describe('recurso e suporte', () => {
  test('flag desligada (ou ausente): nenhuma seção de passkeys e a API recusa', async ({
    page,
  }) => {
    for (const passkeys of [false, undefined]) {
      const ctx = await page.context().browser()!.newContext({ locale: 'pt-BR' });
      const p = await ctx.newPage();
      await instalarAutenticador(p);
      const mock = await mockarApi(p, { passkeys });
      await p.goto('/login');
      await p.getByLabel('E-mail').fill('solicitante@exemplo.com');
      await p.getByLabel('Senha', { exact: true }).fill('senha-correta');
      await p.getByRole('button', { name: 'Entrar' }).click();
      await p.getByRole('link', { name: /^Conta:/ }).click();
      await expect(p.getByRole('heading', { name: 'Verificação em duas etapas' })).toBeVisible();
      await expect(p.getByRole('heading', { name: 'Passkeys' })).toHaveCount(0);
      await expect(p.getByRole('button', { name: /passkey/i })).toHaveCount(0);
      // Sem flag, o front nem consulta a API.
      expect(mock.chamadasPasskeys).toHaveLength(0);
      await ctx.close();
    }
  });

  test('flag ligada mas API recusa a listagem (recurso desligado no servidor): seção some sem ruído', async ({
    page,
  }) => {
    const { erros } = await abrir(page, {
      passkeysApi: {
        erroLista: { status: 404, codigo: 'recurso_desabilitado', mensagem: 'Desligado.' },
      },
    });
    await expect(page.getByRole('heading', { name: 'Verificação em duas etapas' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Passkeys' })).toHaveCount(0);
    expect(erros).toEqual([]);
  });

  test('sem window.PublicKeyCredential: mensagem, sem botões de passkey e login por senha intacto', async ({
    page,
  }) => {
    const { mock, erros } = await abrir(page, {}, { suporte: false });
    await expect(secao(page)).toBeVisible();
    await expect(
      secao(page).getByText(
        'Este navegador não oferece passkeys. Você continua entrando normalmente com e-mail e senha.',
      ),
    ).toBeVisible();
    await expect(page.getByRole('button', { name: /Adicionar passkey/ })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /Confirmar com passkey/ })).toHaveCount(0);
    // O login (e-mail/senha) já funcionou em abrir(); a listagem continua disponível.
    expect(caminhos(mock)).toEqual(['GET /']);
    expect(erros).toEqual([]);
  });
});

test.describe('listar e adicionar', () => {
  test('lista só metadados (apelido e datas), sem chave nem credential_id; vazia mostra aviso', async ({
    page,
  }) => {
    await abrir(page, {
      passkeysApi: {
        itens: [EXISTENTE, { ...EXISTENTE, id: 'pk-2', apelido: null, ultimo_uso_em: null }],
      },
    });
    const s = secao(page);
    await expect(s.getByRole('listitem')).toHaveCount(2);
    await expect(s.getByText('Notebook')).toBeVisible();
    await expect(s.getByText('Passkey sem apelido')).toBeVisible();
    await expect(s.getByText('Ainda não usada')).toBeVisible();
    await expect(s.getByText(/Último uso:/)).toBeVisible();
    const texto = (await s.innerText()).toLowerCase();
    for (const proibido of ['credential', 'chave pública', 'public key', 'pk-1']) {
      expect(texto).not.toContain(proibido);
    }
    // Alvos >= 44 px.
    for (const nome of ['Adicionar passkey', 'Confirmar com passkey']) {
      const caixa = await s.getByRole('button', { name: nome }).boundingBox();
      expect(caixa!.height).toBeGreaterThanOrEqual(44);
    }
  });

  test('lista vazia: "Nenhuma passkey cadastrada."', async ({ page }) => {
    await abrir(page);
    await expect(secao(page).getByText('Nenhuma passkey cadastrada.')).toBeVisible();
  });

  test('adicionar com apelido: opções, create, confirmar (binários em base64url), nada além do pedido', async ({
    page,
  }) => {
    const { mock, erros } = await abrir(page);
    const antes = await guardado(page);
    await page.getByLabel('Apelido (opcional)').fill('  Celular pessoal ');
    await adicionar(page).click();

    await expect(secao(page).getByText('Celular pessoal')).toBeVisible();
    await expect(page.getByLabel('Apelido (opcional)')).toHaveValue('');
    expect(caminhos(mock)).toEqual([
      'GET /',
      'POST /registro/opcoes',
      'POST /registro/confirmar',
      'GET /',
    ]);
    const [, opcoes, conf] = mock.chamadasPasskeys;
    expect(opcoes!.corpo).toEqual({ apelido: 'Celular pessoal' });
    // JWT do Supabase segue sendo o único credencial de sessão.
    expect(opcoes!.auth).toBe('Bearer tok-solicitante');

    const corpo = conf!.corpo as { apelido?: string; credencial: Record<string, unknown> };
    expect(Object.keys(corpo).sort()).toEqual(['apelido', 'credencial']);
    expect(corpo.apelido).toBe('Celular pessoal');
    const c = corpo.credencial as {
      id: string;
      rawId: string;
      type: string;
      response: Record<string, string>;
      transports?: string[];
    };
    expect(Object.keys(c).sort()).toEqual(
      [
        'authenticatorAttachment',
        'clientExtensionResults',
        'id',
        'rawId',
        'response',
        'transports',
        'type',
      ].sort(),
    );
    expect(c.type).toBe('public-key');
    expect(c.rawId).toBe(b64u(RAW_ID));
    expect(c.rawId).toMatch(BASE64URL);
    expect(c.id).toMatch(BASE64URL);
    expect(Object.keys(c.response).sort()).toEqual(['attestationObject', 'clientDataJSON']);
    expect(c.response.clientDataJSON).toBe(b64u('CLIENT-DATA-FAKE'));
    expect(c.response.attestationObject).toBe(b64u('ATTEST-OBJ-FAKE'));
    expect(c.transports).toEqual(['internal']);

    // O navegador recebeu o desafio e os ids como ArrayBuffer decodificados de base64url.
    const pk = await estadoPk(page);
    expect(pk.create).toHaveLength(1);
    expect(pk.create[0]!.challenge).toBe(b64u('desafio-registro-0001'));
    expect(pk.create[0]!.userId).toBe(b64u('usuario-0001'));
    expect(pk.create[0]!.excluidas).toEqual([b64u('cred-existente')]);
    expect(pk.create[0]!.uv).toBe('required');

    // Nenhuma resposta da API devolve JWT/sessão e nada foi guardado.
    await conferirStorage(page, antes);
    expect(erros).toEqual([]);
  });

  test('adicionar sem apelido: nenhum campo apelido é enviado', async ({ page }) => {
    const { mock } = await abrir(page);
    await adicionar(page).click();
    await expect(secao(page).getByText('Passkey sem apelido')).toBeVisible();
    expect(mock.chamadasPasskeys[1]!.corpo).toEqual({});
    expect(Object.keys(mock.chamadasPasskeys[2]!.corpo as object)).toEqual(['credencial']);
  });

  test('clique duplo em Adicionar: uma só chamada, Spinner e botões travados', async ({ page }) => {
    const { mock } = await abrir(page, { passkeysApi: { atrasoMs: 400 } });
    await definirModo(page, 'travar');
    const b = adicionar(page);
    await b.dblclick();
    await expect(b).toHaveAttribute('aria-busy', 'true');
    await expect(b).toContainText('Aguardando o aparelho...');
    await expect(b.locator('svg, [role="status"]').first()).toBeVisible();
    // Sem passkeys a API não permite reautenticar: o botão nem existe.
    await expect(page.getByRole('button', { name: 'Confirmar com passkey' })).toHaveCount(0);
    // Espera o navegador receber o create (as opções chegam após o atraso da API) e só então libera.
    await expect.poll(async () => (await estadoPk(page)).create.length).toBe(1);
    await page.evaluate(() => (window as unknown as JanelaPk).__pk.liberar?.());
    await expect(secao(page).getByText('Passkey sem apelido')).toBeVisible();
    expect(mock.chamadasPasskeys.filter((c) => c.caminho === '/registro/opcoes')).toHaveLength(1);
    expect((await estadoPk(page)).create).toHaveLength(1);
    expect(mock.chamadasPasskeys.filter((c) => c.caminho === '/registro/confirmar')).toHaveLength(
      1,
    );
  });
});

test.describe('reautenticação', () => {
  test('"Confirmar com passkey" mostra "Confirmado até" vindo da API, sem JWT novo guardado', async ({
    page,
  }) => {
    const { mock, erros } = await abrir(page, { passkeysApi: { itens: [EXISTENTE] } });
    const antes = await guardado(page);
    await expect(page.getByText(/Confirmado até/)).toHaveCount(0);
    await page.getByRole('button', { name: 'Confirmar com passkey' }).click();
    await expect(page.getByText(/Confirmado até \d{2}:\d{2}\./)).toBeVisible();

    expect(caminhos(mock)).toEqual([
      'GET /',
      'POST /autenticacao/opcoes',
      'POST /autenticacao/confirmar',
      'GET /', // recarrega a lista após confirmar
    ]);
    const corpo = mock.chamadasPasskeys[2]!.corpo as { credencial: { response: object } };
    expect(Object.keys(corpo)).toEqual(['credencial']);
    expect(Object.keys(corpo.credencial.response).sort()).toEqual([
      'authenticatorData',
      'clientDataJSON',
      'signature',
      'userHandle',
    ]);
    expect(corpo.credencial.response).toMatchObject({
      authenticatorData: b64u('AUTH-DATA-FAKE'),
      signature: b64u('ASSINATURA-FAKE'),
      userHandle: null,
    });
    const pk = await estadoPk(page);
    expect(pk.get[0]).toEqual({
      challenge: b64u('desafio-autenticacao-0001'),
      permitidas: [b64u('cred-existente')],
    });
    // Mesmo token de sempre: nenhum JWT novo é aceito do fluxo de passkey.
    expect(mock.chamadasPasskeys.every((c) => c.auth === 'Bearer tok-solicitante')).toBe(true);
    await conferirStorage(page, antes);
    expect(erros).toEqual([]);
  });

  test('listagem com identidade_confirmada: a UI não inventa o prazo (só a confirmação mostra "Confirmado até")', async ({
    page,
  }) => {
    const { erros } = await abrir(page, {
      passkeysApi: {
        itens: [EXISTENTE],
        reautenticadoAte: new Date(Date.now() + 120_000).toISOString(),
      },
    });
    await expect(secao(page).getByText('Notebook')).toBeVisible();
    // A listagem traz só o booleano; o horário vem apenas da resposta de confirmar.
    await expect(page.getByText(/Confirmado até/)).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Confirmar com passkey' })).toBeVisible();
    expect(erros).toEqual([]);
  });

  test('403 reautenticacao_requerida ao adicionar: mensagem da API, oferta de confirmar e depois adiciona', async ({
    page,
  }) => {
    const { mock } = await abrir(page, {
      passkeysApi: { itens: [EXISTENTE], exigirReautParaAdicionar: true },
    });
    await adicionar(page).click();
    const alerta = secao(page).getByRole('alert');
    await expect(alerta).toContainText('Confirme sua identidade com uma passkey para continuar.');
    await expect(alerta).toContainText('Use “Confirmar com passkey”');
    // Nenhuma passkey foi criada no aparelho e o usuário continua logado.
    expect((await estadoPk(page)).create).toHaveLength(0);
    await expect(adicionar(page)).toBeEnabled();

    await page.getByRole('button', { name: 'Confirmar com passkey' }).click();
    await expect(page.getByText(/Confirmado até/)).toBeVisible();
    await expect(secao(page).getByRole('alert')).toHaveCount(0);
    await adicionar(page).click();
    await expect(secao(page).getByRole('listitem')).toHaveCount(2);
    expect(caminhos(mock).filter((c) => c === 'POST /registro/opcoes')).toHaveLength(2);
  });

  test('usuário sem passkey (404 na autenticação): mensagem da API', async ({ page }) => {
    // A UI só mostra Confirmar se a API permite; aqui ela o permite e responde 404 ao usar.
    await abrir(page, { passkeysApi: { acoesLista: ['cadastrar', 'reautenticar'] } });
    await page.getByRole('button', { name: 'Confirmar com passkey' }).click();
    await expect(secao(page).getByRole('alert')).toContainText('Nenhuma passkey cadastrada.');
    await expect(page.getByRole('button', { name: 'Confirmar com passkey' })).toBeEnabled();
    expect((await estadoPk(page)).get).toHaveLength(0);
  });
});

test.describe('botões conforme acoes_permitidas da API', () => {
  test('sem passkey: só Adicionar (a API não devolve reautenticar); Confirmar ausente', async ({
    page,
  }) => {
    await abrir(page);
    await expect(adicionar(page)).toBeVisible();
    await expect(page.getByRole('button', { name: /Confirmar com passkey/ })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Revogar' })).toHaveCount(0);
  });

  test('com passkey: Adicionar, Confirmar e Revogar aparecem', async ({ page }) => {
    await abrir(page, { passkeysApi: { itens: [EXISTENTE] } });
    await expect(adicionar(page)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Confirmar com passkey' })).toBeVisible();
    await expect(secao(page).getByRole('button', { name: 'Revogar' })).toBeVisible();
  });

  test('limite atingido (API sem "cadastrar"): Adicionar e apelido ausentes; Confirmar e Revogar seguem', async ({
    page,
  }) => {
    await abrir(page, { passkeysApi: { itens: [EXISTENTE], limite: 1 } });
    await expect(secao(page).getByText('Notebook')).toBeVisible();
    await expect(page.getByRole('button', { name: /Adicionar passkey/ })).toHaveCount(0);
    await expect(page.getByLabel('Apelido (opcional)')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Confirmar com passkey' })).toBeVisible();
    await expect(secao(page).getByRole('button', { name: 'Revogar' })).toBeVisible();
  });

  test('API sem "reautenticar": Confirmar ausente mesmo com passkeys listadas', async ({
    page,
  }) => {
    await abrir(page, { passkeysApi: { itens: [EXISTENTE], acoesLista: ['cadastrar'] } });
    await expect(secao(page).getByText('Notebook')).toBeVisible();
    await expect(adicionar(page)).toBeVisible();
    await expect(page.getByRole('button', { name: /Confirmar com passkey/ })).toHaveCount(0);
  });

  test('item sem "revogar" em acoes_permitidas: Revogar ausente', async ({ page }) => {
    await abrir(page, { passkeysApi: { itens: [EXISTENTE], acoesItem: [] } });
    await expect(secao(page).getByText('Notebook')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Revogar' })).toHaveCount(0);
  });

  test('API sem nenhuma ação: nenhum botão de passkey', async ({ page }) => {
    await abrir(page, { passkeysApi: { itens: [EXISTENTE], acoesLista: [], acoesItem: [] } });
    await expect(secao(page).getByText('Notebook')).toBeVisible();
    await expect(secao(page).getByRole('button')).toHaveCount(0);
  });
});

test.describe('erros do navegador e da API', () => {
  const NEUTRA = 'Operação cancelada ou o tempo acabou. Você pode tentar de novo quando quiser.';

  test('cancelamento (NotAllowedError): mensagem neutra, nada é confirmado e o botão volta', async ({
    page,
  }) => {
    const { mock, erros } = await abrir(page);
    await definirModo(page, 'cancelar');
    await adicionar(page).click();
    await expect(secao(page).getByRole('alert')).toContainText(NEUTRA);
    await expect(adicionar(page)).toBeEnabled();
    await expect(adicionar(page)).toHaveText('Adicionar passkey');
    expect(caminhos(mock)).not.toContain('POST /registro/confirmar');
    // Tentar de novo funciona e limpa o erro.
    await definirModo(page, 'ok');
    await adicionar(page).click();
    await expect(secao(page).getByRole('listitem')).toHaveCount(1);
    await expect(secao(page).getByRole('alert')).toHaveCount(0);
    expect(erros).toEqual([]);
  });

  test('AbortError e TimeoutError têm mensagens neutras próprias', async ({ page }) => {
    await abrir(page);
    await definirModo(page, 'abortar');
    await adicionar(page).click();
    await expect(secao(page).getByRole('alert')).toContainText(NEUTRA);
    await definirModo(page, 'tempo');
    await adicionar(page).click();
    await expect(secao(page).getByRole('alert')).toContainText(
      'O tempo para confirmar acabou. Tente novamente.',
    );
    await definirModo(page, 'nao_suportado');
    await adicionar(page).click();
    await expect(secao(page).getByRole('alert')).toContainText(
      'Este aparelho ou navegador não tem um autenticador compatível.',
    );
    await expect(adicionar(page)).toBeEnabled();
  });

  test('InvalidStateError (já cadastrada neste aparelho)', async ({ page }) => {
    const { mock } = await abrir(page);
    await definirModo(page, 'ja_cadastrada');
    await adicionar(page).click();
    await expect(secao(page).getByRole('alert')).toContainText(
      'Este aparelho já tem uma passkey cadastrada para a sua conta.',
    );
    expect(caminhos(mock)).not.toContain('POST /registro/confirmar');
    await expect(adicionar(page)).toBeEnabled();
  });

  test('desafio expirado/reutilizado (400 desafio_invalido): mensagem fixa da API e tentar de novo', async ({
    page,
  }) => {
    const { mock } = await abrir(page, {
      passkeysApi: {
        erroConfirmarRegistro: {
          status: 400,
          codigo: 'desafio_invalido',
          mensagem: 'Desafio inválido ou expirado. Inicie o processo novamente.',
        },
      },
    });
    await adicionar(page).click();
    await expect(secao(page).getByRole('alert')).toContainText(
      'Desafio inválido ou expirado. Inicie o processo novamente.',
    );
    await expect(secao(page).getByText('Nenhuma passkey cadastrada.')).toBeVisible();
    await expect(adicionar(page)).toBeEnabled();
    await adicionar(page).click();
    await expect(secao(page).getByRole('alert')).toContainText('Desafio inválido ou expirado.');
    // Cada tentativa pede opções novas (o desafio é de uso único no servidor).
    expect(caminhos(mock).filter((c) => c === 'POST /registro/opcoes')).toHaveLength(2);
  });

  test('credencial revogada/recusada na confirmação (400 passkey_invalida): mensagem genérica e sem confirmado', async ({
    page,
  }) => {
    await abrir(page, {
      passkeysApi: {
        itens: [EXISTENTE],
        erroConfirmarAutenticacao: {
          status: 400,
          codigo: 'passkey_invalida',
          mensagem: 'Não foi possível validar a passkey.',
        },
      },
    });
    await page.getByRole('button', { name: 'Confirmar com passkey' }).click();
    await expect(secao(page).getByRole('alert')).toContainText(
      'Não foi possível validar a passkey.',
    );
    await expect(page.getByText(/Confirmado até/)).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Confirmar com passkey' })).toBeEnabled();
  });

  test('401 na confirmação: sessão encerrada pelo tratamento padrão, com a mensagem da API', async ({
    page,
  }) => {
    await abrir(page, {
      passkeysApi: {
        itens: [EXISTENTE],
        erroConfirmarAutenticacao: {
          status: 401,
          codigo: 'nao_autenticado',
          mensagem: 'Sessão inválida ou expirada. Faça login novamente.',
        },
      },
    });
    await page.getByRole('button', { name: 'Confirmar com passkey' }).click();
    await expect(page).toHaveURL(/\/login\/?$/);
    await expect(
      page.getByText('Sessão inválida ou expirada. Faça login novamente.'),
    ).toBeVisible();
  });
});

test.describe('revogar', () => {
  test('revogar: diálogo de confirmação (Voltar não chama a API), Spinner e lista atualizada', async ({
    page,
  }) => {
    const { mock, erros } = await abrir(page, {
      passkeysApi: { itens: [EXISTENTE], atrasoMs: 300 },
    });
    await secao(page).getByRole('button', { name: 'Revogar' }).click();
    const dialogo = page.getByRole('alertdialog', { name: 'Revogar passkey?' });
    await expect(dialogo).toBeVisible();
    await dialogo.getByRole('button', { name: 'Voltar' }).click();
    await expect(dialogo).toBeHidden();
    expect(caminhos(mock).some((c) => c.startsWith('DELETE'))).toBe(false);

    await secao(page).getByRole('button', { name: 'Revogar' }).click();
    await dialogo.getByRole('button', { name: 'Revogar' }).dblclick();
    await expect(dialogo).toBeHidden();
    await expect(secao(page).getByText('Nenhuma passkey cadastrada.')).toBeVisible();
    // Clique duplo = uma só revogação.
    expect(caminhos(mock).filter((c) => c.startsWith('DELETE'))).toEqual(['DELETE /pk-1']);
    expect(erros).toEqual([]);
  });

  test('revogar em andamento: botões da seção travados (estado ocupado)', async ({ page }) => {
    await abrir(page, { passkeysApi: { itens: [EXISTENTE], atrasoMs: 600 } });
    await secao(page).getByRole('button', { name: 'Revogar' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Revogar' }).click();
    await expect(adicionar(page)).toBeDisabled();
    await expect(confirmar(page)).toBeDisabled();
    await expect(secao(page).getByText('Nenhuma passkey cadastrada.')).toBeVisible();
    await expect(adicionar(page)).toBeEnabled();
  });

  test('erro da API ao revogar é exibido como veio', async ({ page }) => {
    await abrir(page, {
      passkeysApi: {
        itens: [EXISTENTE],
        erroRevogar: {
          status: 404,
          codigo: 'passkey_nao_encontrada',
          mensagem: 'Passkey não encontrada.',
        },
      },
    });
    await secao(page).getByRole('button', { name: 'Revogar' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Revogar' }).click();
    await expect(secao(page).getByRole('alert')).toContainText('Passkey não encontrada.');
    await expect(secao(page).getByText('Notebook')).toBeVisible();
  });
});

test.describe('desktop (teclado e mouse)', () => {
  test.use({ viewport: { width: 1280, height: 800 }, isMobile: false, hasTouch: false });

  test('listar, adicionar e revogar em tela larga', async ({ page }) => {
    const { erros } = await abrir(page, { passkeysApi: { itens: [EXISTENTE] } });
    await expect(secao(page).getByText('Notebook')).toBeVisible();
    await page.getByLabel('Apelido (opcional)').fill('Chave USB');
    await adicionar(page).click();
    await expect(secao(page).getByText('Chave USB')).toBeVisible();
    await secao(page).getByRole('button', { name: 'Revogar' }).last().click();
    // Foco inicial no "Voltar" (ação destrutiva); Esc fecha sem revogar.
    await expect(page.getByRole('button', { name: 'Voltar' })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('alertdialog')).toBeHidden();
    await expect(secao(page).getByRole('listitem')).toHaveCount(2);
    await secao(page).getByRole('button', { name: 'Revogar' }).last().click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Revogar' }).click();
    await expect(secao(page).getByRole('listitem')).toHaveCount(1);
    expect(erros).toEqual([]);
  });

  test('sem suporte em tela larga: mensagem e login por senha intactos', async ({ page }) => {
    await abrir(page, {}, { suporte: false });
    await expect(secao(page).getByText(/Este navegador não oferece passkeys/)).toBeVisible();
  });
});
