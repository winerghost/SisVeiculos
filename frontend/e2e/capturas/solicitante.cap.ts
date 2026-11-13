import { expect, test, type Page } from '@playwright/test';
import { mockarApi, type Opcoes } from '../mock-api';
import { mockarAdmin } from '../mock-admin';
import { mockarMotorista } from '../mock-motorista';
import { mockarNotificacoes, nova, REQ_ID } from '../mock-notificacoes';

// Gera os PNGs de docs/mockups a partir das telas reais (API mockada, dados fictícios).
const PASTA = '../docs/mockups';
const ID0 = '33333333-3333-4333-8333-333333333330';

async function foto(page: Page, nome: string) {
  // Sem animações em curso nem foco residual de rota: aguarda fontes e uma pintura estável.
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${PASTA}/${nome}.png`, fullPage: false });
}

test.beforeEach(async ({ page }) => {
  // Pula a splash do login (marca de "já vista" na sessão).
  await page.addInitScript(() => sessionStorage.setItem('splash-visto', '1'));
});

async function entrar(page: Page, email: string, senha = 'senha-correta') {
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill(senha);
  await page.getByRole('button', { name: 'Entrar' }).click();
}

async function como(page: Page, papel: 'solicitante' | 'autorizador', opcoes: Opcoes = {}) {
  await mockarApi(page, opcoes);
  await entrar(page, `${papel}@exemplo.com`);
}

const T = (h: number, m = 0) => new Date(Date.UTC(2026, 10, 9, h, m)).toISOString();
const concluida = {
  status: 'concluida' as const,
  trajeto: 'Sede -> Fórum Federal, Palmas',
  hora_saida: T(12),
  criado_em: T(11, 20),
  autorizado_em: T(11, 40),
  saida_real_em: T(12, 5),
  chegada_real_em: T(14, 30),
  saida_registrada_por_nome: 'Ana Souza',
  chegada_registrada_por_nome: 'Ana Souza',
};

test('01-login', async ({ page }) => {
  await mockarApi(page);
  await page.goto('/login');
  await expect(page.getByRole('button', { name: 'Entrar' })).toBeVisible();
  await expect(page.getByLabel('E-mail')).toBeVisible();
  await foto(page, '01-login');
});

test('02-minhas-requisicoes', async ({ page }) => {
  await como(page, 'solicitante', {
    sementes: [
      { ...concluida },
      {
        status: 'negada',
        trajeto: 'Sede -> Receita Federal',
        hora_saida: T(15),
        autorizado_em: T(13),
        criado_em: T(12, 30),
      },
      {
        status: 'cancelada',
        trajeto: 'Sede -> Procuradoria Regional',
        hora_saida: T(18),
        cancelada_em: T(10),
      },
    ],
  });
  await expect(page).toHaveURL(/\/requisicoes$/);
  await expect(page.getByText('Sede -> Receita Federal')).toBeVisible();
  await expect(page.getByText('Sede -> Fórum Federal, Palmas')).toBeVisible();
  await foto(page, '02-minhas-requisicoes');
});

const REF = { data: '2026-11-13', hora: 14, minuto: 35 };

test('03-nova-requisicao', async ({ page }) => {
  await como(page, 'solicitante', { horarioReferencia: REF });
  // Erro de horário vindo do servidor (rota registrada depois prevalece sobre a do mock base).
  await page.route(/\/api\/requisicoes$/, (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    return route.fulfill({
      status: 409,
      json: {
        erro: {
          codigo: 'horario_indisponivel',
          mensagem:
            'Horário indisponível. O veículo já está reservado nesse horário. Escolha outro horário.',
        },
      },
    });
  });
  await page.goto('/requisicoes/nova');
  await page.getByLabel('Veículo', { exact: true }).selectOption({ index: 1 });
  await page.getByRole('button', { name: /Escolha o dia e o horário/ }).click();
  const gaveta = page.getByRole('dialog');
  // Confirma a sugestão do servidor sem mexer em nada.
  await expect(gaveta.getByLabel('Hora')).toHaveValue('14');
  await gaveta.getByRole('button', { name: 'Confirmar' }).click();
  await page.getByLabel('Trajeto').fill('Sede -> Fórum Federal, Palmas');
  await page.getByRole('button', { name: 'Enviar requisição' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Horário indisponível' })).toBeVisible();
  await page.getByLabel('Trajeto').focus();
  await foto(page, '03-nova-requisicao');
});

test('11-seletor-data-hora', async ({ page }) => {
  await como(page, 'solicitante', { horarioReferencia: REF });
  await page.goto('/requisicoes/nova');
  await page.getByRole('button', { name: /Escolha o dia e o horário/ }).click();
  const gaveta = page.getByRole('dialog');
  await expect(gaveta).toBeVisible();
  // Abre já na sugestão do servidor (13/11 às 14:35), sem escolha do usuário.
  await expect(gaveta.getByLabel('Hora')).toHaveValue('14');
  await expect(gaveta.getByLabel('Minuto')).toHaveValue('35');
  await foto(page, '11-seletor-data-hora');
});

test('04-painel-autorizador', async ({ page }) => {
  await como(page, 'autorizador', {
    sementes: [
      {
        status: 'solicitada',
        trajeto: 'Sede -> Fórum Federal, Palmas',
        hora_saida: em(120),
        situacao_horario: 'no_prazo',
      },
    ],
  });
  await page.getByRole('link', { name: 'Autorizar' }).click();
  await expect(page.getByRole('button', { name: 'Autorizar' })).toBeVisible();
  await foto(page, '04-painel-autorizador');
});

test('05-registrar-chegada', async ({ page }) => {
  await como(page, 'autorizador', {
    sementes: [
      {
        status: 'em_uso',
        trajeto: 'Sede -> Fórum Federal, Palmas',
        hora_saida: T(12),
        autorizado_em: T(11, 40),
        saida_real_em: T(12, 5),
        saida_registrada_por_nome: 'Ana Souza',
      },
    ],
  });
  await page.goto('/viagem');
  await expect(page.getByRole('button', { name: 'Registrar chegada agora' })).toBeVisible();
  await foto(page, '05-registrar-chegada');
});

test('12-detalhe-requisicao', async ({ page }) => {
  await como(page, 'solicitante', { sementes: [{ ...concluida }] });
  await page.goto(`/requisicoes/detalhe?id=${ID0}`);
  await expect(page.getByRole('heading', { name: 'Linha do tempo' })).toBeVisible();
  await foto(page, '12-detalhe-requisicao');
});

test('13-viagem', async ({ page }) => {
  await como(page, 'autorizador', {
    km: true,
    sementes: [
      {
        status: 'em_uso',
        trajeto: 'Sede -> Fórum Federal, Palmas',
        hora_saida: T(12),
        autorizado_em: T(11, 40),
        saida_real_em: T(12, 5),
        saida_registrada_por_nome: 'Ana Souza',
        km_saida: 12345,
      },
    ],
  });
  await page.goto('/viagem');
  await expect(page.getByText('Km de saída: 12.345')).toBeVisible();
  await foto(page, '13-viagem');
});

test('14-checklist-saida', async ({ page }) => {
  await como(page, 'autorizador', {
    checklist: true,
    sementes: [{ status: 'autorizada', hora_saida: T(17), autorizado_em: T(16) }],
  });
  await page.getByRole('link', { name: 'Autorizar' }).click();
  await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
  const titulo = page.getByRole('heading', { name: 'Checklist de saída' });
  await expect(titulo).toBeVisible();
  await page.locator('label', { hasText: '1/2' }).click();
  await titulo.scrollIntoViewIfNeeded();
  await titulo.evaluate((el) => el.scrollIntoView({ block: 'start' }));
  await foto(page, '14-checklist-saida');
});

test('15-motorista', async ({ page }) => {
  await mockarMotorista(page);
  await entrar(page, 'motorista@exemplo.com');
  await page.getByRole('link', { name: 'Minhas viagens' }).click();
  await expect(page.getByRole('heading', { name: /Próximas · 1/ })).toBeVisible();
  await foto(page, '15-motorista');
});

test('16-motorista-detalhe', async ({ page }) => {
  await mockarMotorista(page);
  await entrar(page, 'motorista@exemplo.com');
  await page.getByRole('link', { name: 'Minhas viagens' }).click();
  await page
    .getByRole('link', { name: /Sede -> Fórum Federal/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/motorista\/detalhe\?id=/);
  await expect(page.getByRole('heading', { name: 'Acompanhamento da viagem' })).toBeVisible();
  await foto(page, '16-motorista-detalhe');
});

test('17-notificacoes', async ({ page }) => {
  await mockarApi(page, { sementes: [{ ...concluida }] });
  await mockarNotificacoes(page, {
    solicitante: [
      nova(
        {
          tipo: 'chegada_registrada',
          titulo: 'Chegada registrada',
          texto: 'A chegada do veículo da sua requisição foi registrada.',
          destino: `/requisicoes/detalhe?id=${REQ_ID}`,
        },
        3,
      ),
      nova(
        {
          titulo: 'Requisição autorizada',
          texto: 'Sua requisição foi autorizada por Ana Souza.',
          critica: true,
        },
        90,
      ),
      nova(
        {
          titulo: 'Requisição enviada',
          texto: 'Sua requisição foi enviada para análise.',
          lida_em: T(10),
        },
        300,
      ),
    ],
  });
  await entrar(page, 'solicitante@exemplo.com');
  await page.getByRole('link', { name: /^Notificações/ }).click();
  await expect(page.getByRole('list', { name: 'Notificações' })).toBeVisible();
  await foto(page, '17-notificacoes');
});

test('18-redefinir-senha', async ({ page }) => {
  await mockarApi(page);
  await page.goto('/redefinir-senha');
  await expect(page.getByRole('heading', { name: 'Redefinir senha' })).toBeVisible();
  await page.getByLabel('Nova senha').focus();
  await foto(page, '18-redefinir-senha');
});

test('19-convite-aceite', async ({ page }) => {
  await mockarAdmin(page);
  const hash =
    '#access_token=tok-convidado&refresh_token=r-conv&expires_in=3600&token_type=bearer&type=invite';
  await page.goto(`/convite?convite=token-de-convite-com-mais-de-16-chars${hash}`);
  await expect(page.getByRole('heading', { name: 'Ativar minha conta' })).toBeVisible();
  await expect(page.getByLabel('Nova senha')).toBeVisible();
  await foto(page, '19-convite-aceite');
});

// ---- Novos (59+): arquivadas, organizar/gestos, checklist registrado, localização ----
const PNG_1X1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);
const CARTAO = 'ul.lista-animada > li';
const ID_VIAGEM_CONCLUIDA = '44444444-4444-4444-8444-444444444442';
const FINALIZADAS = [
  { ...concluida },
  { status: 'negada' as const, trajeto: 'Sede -> Receita Federal', hora_saida: T(15) },
  { status: 'cancelada' as const, trajeto: 'Sede -> Procuradoria Regional', hora_saida: T(18) },
];
const SOLICITADA = [
  { status: 'solicitada' as const, trajeto: 'Sede -> Fórum Federal, Palmas', hora_saida: T(17) },
];

/** Arrasta o primeiro cartão a partir do link; fracao < 0 = esquerda. `soltar: false` mantém o dedo. */
async function arrastar(page: Page, fracao: number, soltar = true) {
  const cartao = page.locator(CARTAO).first();
  await expect(cartao).toBeVisible();
  const li = (await cartao.boundingBox())!;
  const link = (await cartao.getByRole('link').first().boundingBox())!;
  const y = link.y + Math.min(link.height / 2, 24);
  const x0 = link.x + link.width / 2;
  await page.mouse.move(x0, y);
  await page.mouse.down();
  await page.mouse.move(x0 + (li.width * fracao) / 2, y, { steps: 5 });
  await page.mouse.move(x0 + li.width * fracao, y, { steps: 5 });
  if (soltar) await page.mouse.up();
}

test('59-requisicoes-arquivar-desfazer', async ({ page }) => {
  await como(page, 'solicitante', { sementes: FINALIZADAS });
  await expect(page.getByText('Sede -> Receita Federal')).toBeVisible();
  await page.locator(CARTAO).first().getByRole('button', { name: 'Arquivar' }).click();
  await expect(page.getByRole('status').getByText('Requisição arquivada.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Desfazer' })).toBeVisible();
  await foto(page, '59-requisicoes-arquivar-desfazer');
});

test('60-requisicoes-arquivadas', async ({ page }) => {
  await como(page, 'solicitante', {
    sementes: FINALIZADAS,
    arquivadas: [
      { papel: 'solicitante', indice: 1 },
      { papel: 'solicitante', indice: 2 },
    ],
  });
  await page.getByRole('button', { name: 'Arquivadas' }).click();
  await expect(page.getByRole('button', { name: 'Arquivadas · 2' })).toBeVisible();
  await expect(page.locator(CARTAO)).toHaveCount(2);
  await foto(page, '60-requisicoes-arquivadas');
});

test('61-requisicoes-gesto-arrastando', async ({ page }) => {
  await como(page, 'autorizador', { sementes: SOLICITADA });
  await arrastar(page, 0.4, false);
  await foto(page, '61-requisicoes-gesto-arrastando');
  await page.mouse.up();
});

test('62-dialogo-negar-ou-cancelar', async ({ page }) => {
  await como(page, 'autorizador', { sementes: SOLICITADA });
  await arrastar(page, -0.6);
  await expect(
    page.getByRole('alertdialog', { name: 'Negar ou cancelar esta requisição?' }),
  ).toBeVisible();
  await foto(page, '62-dialogo-negar-ou-cancelar');
});

test('63-autorizar-escolher-motorista', async ({ page }) => {
  await como(page, 'autorizador', {
    sementes: SOLICITADA,
    motoristas: [
      { id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', nome: 'Carlos Lima' },
      { id: 'ffffffff-ffff-4fff-8fff-ffffffffffff', nome: 'Rita Alves' },
    ],
    motoristaPadrao: 'ffffffff-ffff-4fff-8fff-ffffffffffff',
  });
  await arrastar(page, 0.7);
  await expect(page.getByRole('dialog', { name: 'Autorizar requisição' })).toBeVisible();
  await foto(page, '63-autorizar-escolher-motorista');
});

test('64-checklist-registrado-viagem', async ({ page }) => {
  await como(page, 'autorizador', {
    checklist: true,
    sementes: [{ status: 'autorizada', hora_saida: T(12), autorizado_em: T(11, 40) }],
  });
  await page.getByRole('link', { name: 'Autorizar' }).click();
  await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
  await page.locator('label', { hasText: '1/2' }).click();
  await page.locator('label', { hasText: 'Há avarias' }).click();
  await page.getByLabel('Descrição das avarias').fill('Arranhão na porta direita');
  await page
    .locator('input[type=file]')
    .setInputFiles([{ name: 'a.png', mimeType: 'image/png', buffer: PNG_1X1 }]);
  await page.getByRole('button', { name: 'Registrar checklist de saída' }).click();
  await page.getByRole('button', { name: 'Registrar saída' }).click();
  await page.getByRole('link', { name: 'Viagem' }).click();
  const bloco = page.getByRole('region', { name: 'Checklist de saída' });
  await expect(bloco.getByText('Avarias: Sim')).toBeVisible();
  await bloco.evaluate((el) => el.scrollIntoView({ block: 'start' }));
  await foto(page, '64-checklist-registrado-viagem');
});

test('65-motorista-aviso-localizacao', async ({ page }) => {
  await mockarMotorista(page, { localizacao: true });
  await entrar(page, 'motorista@exemplo.com');
  await page.getByRole('link', { name: 'Minhas viagens' }).click();
  await expect(page.getByText(/visível apenas ao administrador/).first()).toBeVisible();
  await foto(page, '65-motorista-aviso-localizacao');
});

test('66-motorista-localizacao-negada', async ({ page }) => {
  await mockarMotorista(page, { localizacao: true });
  await entrar(page, 'motorista@exemplo.com');
  await page.getByRole('link', { name: 'Minhas viagens' }).click();
  await page.getByRole('button', { name: 'Registrar saída' }).click();
  await expect(
    page.getByRole('alert').filter({ hasText: 'Não foi possível obter sua localização.' }),
  ).toBeVisible();
  await foto(page, '66-motorista-localizacao-negada');
});

test('67-detalhe-localizacao-registro', async ({ page }) => {
  await mockarMotorista(page, {
    localizacoesAdmin: [
      {
        momento: 'saida',
        latitude: -15.7939,
        longitude: -47.8828,
        precisao_m: 12.4,
        registrado_em: '2026-11-01T12:10:00Z',
        registrado_por_nome: 'Carlos Lima',
      },
      {
        momento: 'chegada',
        latitude: -15.8,
        longitude: -47.9,
        precisao_m: null,
        registrado_em: '2026-11-01T14:00:00Z',
        registrado_por_nome: 'Carlos Lima',
      },
    ],
  });
  await entrar(page, 'motorista@exemplo.com');
  await page.goto(`/requisicoes/detalhe?id=${ID_VIAGEM_CONCLUIDA}`);
  const secao = page.getByRole('region', { name: 'Localização do registro' });
  await expect(secao).toBeVisible();
  await secao.evaluate((el) => el.scrollIntoView({ block: 'center' }));
  await foto(page, '67-detalhe-localizacao-registro');
});

test('68-motorista-arquivadas', async ({ page }) => {
  await mockarMotorista(page, { arquivadasIds: [ID_VIAGEM_CONCLUIDA] });
  await entrar(page, 'motorista@exemplo.com');
  await page.getByRole('link', { name: 'Minhas viagens' }).click();
  await page.getByRole('button', { name: 'Arquivadas' }).click();
  await expect(page.getByRole('button', { name: 'Desarquivar' })).toBeVisible();
  await foto(page, '68-motorista-arquivadas');
});

test('69-vigilante-arquivar', async ({ page }) => {
  await mockarApi(page, { sementes: FINALIZADAS.slice(0, 1) });
  await entrar(page, 'vigilante@exemplo.com');
  await expect(
    page.locator(CARTAO).first().getByRole('button', { name: 'Arquivar' }),
  ).toBeVisible();
  await page.locator(CARTAO).first().getByRole('button', { name: 'Arquivar' }).click();
  await expect(page.getByRole('status').getByText('Requisição arquivada.')).toBeVisible();
  await foto(page, '69-vigilante-arquivar');
});

test('76-requisicoes-fim-da-lista', async ({ page }) => {
  await como(page, 'solicitante', { sementes: FINALIZADAS });
  await expect(page.locator(CARTAO)).toHaveCount(3);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await foto(page, '76-requisicoes-fim-da-lista');
});

// ---- Hora de saída: sugestão do servidor, erro de horário passado e cor da situação (77+) ----
test('77-seletor-sugestao-servidor', async ({ page }) => {
  await como(page, 'solicitante', {
    horarioReferencia: { data: '2026-11-13', hora: 16, minuto: 25 },
  });
  await page.goto('/requisicoes/nova');
  await page.getByRole('button', { name: /Escolha o dia e o horário/ }).click();
  const gaveta = page.getByRole('dialog');
  await expect(gaveta.getByLabel('Hora')).toHaveValue('16');
  await expect(gaveta.getByLabel('Minuto')).toHaveValue('25');
  await expect(gaveta.getByRole('gridcell', { name: /13 de novembro/ })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await gaveta.getByLabel('Hora').focus();
  await foto(page, '77-seletor-sugestao-servidor');
});

test('78-erro-horario-passado', async ({ page }) => {
  await como(page, 'solicitante', {
    horarioReferencia: REF,
    erroCriacao: {
      status: 400,
      codigo: 'hora_saida_passada',
      mensagem:
        'A hora de saída não pode ser anterior ao horário atual. Agora são 13/11/2026 às 14:32 (horário de Brasília). Escolha um horário a partir das 14:22.',
    },
  });
  await page.goto('/requisicoes/nova');
  await page.getByLabel('Veículo', { exact: true }).selectOption({ index: 1 });
  await page.getByRole('button', { name: /Escolha o dia e o horário/ }).click();
  const gaveta = page.getByRole('dialog');
  await gaveta.getByLabel('Hora').selectOption('9');
  await gaveta.getByLabel('Minuto').selectOption('0');
  await gaveta.getByRole('button', { name: 'Confirmar' }).click();
  await page.getByLabel('Trajeto').fill('Sede -> Fórum Federal, Palmas');
  await page.getByRole('button', { name: 'Enviar requisição' }).click();
  await expect(page.getByText('Não foi possível enviar')).toBeVisible();
  await expect(page.getByText(/Escolha um horário a partir das 14:22/)).toBeVisible();
  await foto(page, '78-erro-horario-passado');
});

// Horários relativos ao relógio real, coerentes com a situação que a API informaria.
const MIN = 60_000;
const em = (minutos: number) => new Date(Date.now() + minutos * MIN).toISOString();

test('79-lista-situacao-horario', async ({ page }) => {
  await como(page, 'solicitante', {
    sementes: [
      {
        status: 'solicitada',
        trajeto: 'Sede -> Fórum Federal, Palmas',
        hora_saida: em(120),
        criado_em: em(-30),
        situacao_horario: 'no_prazo',
      },
      {
        status: 'solicitada',
        trajeto: 'Sede -> Receita Federal',
        hora_saida: em(6),
        criado_em: em(-60),
        situacao_horario: 'proximo',
      },
      {
        status: 'autorizada',
        trajeto: 'Sede -> Procuradoria Regional',
        hora_saida: em(-25),
        criado_em: em(-120),
        situacao_horario: 'atrasado',
      },
    ],
  });
  await expect(page.locator('[data-situacao-horario="no_prazo"]').first()).toBeVisible();
  await expect(page.locator('[data-situacao-horario="proximo"]').first()).toBeVisible();
  await expect(page.locator('[data-situacao-horario="atrasado"]').first()).toBeVisible();
  // Três cartões não cabem numa tela: aumenta a altura só nesta captura.
  await page.setViewportSize({ width: 390, height: 1700 });
  await foto(page, '79-lista-situacao-horario');
});

test('80-detalhe-horario-vencido', async ({ page }) => {
  await como(page, 'solicitante', {
    sementes: [
      {
        status: 'solicitada',
        trajeto: 'Sede -> Fórum Federal, Palmas',
        hora_saida: em(-25),
        criado_em: em(-90),
        situacao_horario: 'atrasado',
      },
    ],
  });
  await page.goto(`/requisicoes/detalhe?id=${ID0}`);
  await expect(page.locator('[data-situacao-horario="atrasado"]').first()).toBeVisible();
  await foto(page, '80-detalhe-horario-vencido');
});
