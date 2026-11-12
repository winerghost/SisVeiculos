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

test('03-nova-requisicao', async ({ page }) => {
  await como(page, 'solicitante');
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
  await gaveta.getByRole('button', { name: /, 12 de / }).click();
  await gaveta.getByLabel('Hora').selectOption('14');
  await gaveta.getByLabel('Minuto').selectOption('30');
  await gaveta.getByRole('button', { name: 'Confirmar' }).click();
  await page.getByLabel('Trajeto').fill('Sede -> Fórum Federal, Palmas');
  await page.getByRole('button', { name: 'Enviar requisição' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Horário indisponível' })).toBeVisible();
  await page.getByLabel('Trajeto').focus();
  await foto(page, '03-nova-requisicao');
});

test('11-seletor-data-hora', async ({ page }) => {
  await como(page, 'solicitante');
  await page.goto('/requisicoes/nova');
  await page.getByRole('button', { name: /Escolha o dia e o horário/ }).click();
  const gaveta = page.getByRole('dialog');
  await expect(gaveta).toBeVisible();
  await gaveta.getByRole('button', { name: /, 12 de / }).click();
  await gaveta.getByLabel('Hora').selectOption('14');
  await gaveta.getByLabel('Minuto').selectOption('30');
  await foto(page, '11-seletor-data-hora');
});

test('04-painel-autorizador', async ({ page }) => {
  await como(page, 'autorizador', {
    sementes: [
      { status: 'solicitada', trajeto: 'Sede -> Fórum Federal, Palmas', hora_saida: T(17) },
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
