import { expect, test, type Page } from '@playwright/test';
import { mockarAdmin, type OpcoesAdmin } from '../mock-admin';

// Gera os PNGs de docs/mockups (telas do admin) a partir das telas reais com API mockada.
const ANA_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const saida = (nome: string) => `../docs/mockups/${nome}.png`;

async function entrarAdmin(page: Page, opcoes: OpcoesAdmin = {}) {
  const mock = await mockarAdmin(page, opcoes);
  await page.goto('/login');
  await page.getByLabel('E-mail').fill('admin@agu.gov.br');
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await expect(page).toHaveURL(/\/$|\/requisicoes|\/admin/);
  return mock;
}

async function foto(page: Page, nome: string) {
  // Aguarda fontes e fim de requisições para evitar captura no meio do carregamento.
  await page.evaluate(() => document.fonts.ready);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(400);
  await page.screenshot({ path: saida(nome) });
}

const em = (dias: number) => new Date(Date.now() + dias * 864e5).toISOString();

test('06-veiculos', async ({ page }) => {
  await entrarAdmin(page);
  await page.goto('/admin/veiculos');
  await expect(page.getByText('placa ABC1D23')).toBeVisible();
  await foto(page, '06-veiculos');
});

test('07-usuarios', async ({ page }) => {
  await entrarAdmin(page);
  await page.goto('/admin/usuarios');
  await expect(page.getByRole('link', { name: /Ana Souza/ })).toBeVisible();
  await foto(page, '07-usuarios');
});

test('08-convidar-usuario', async ({ page }) => {
  await entrarAdmin(page);
  await page.goto('/admin/usuarios/novo');
  await page.getByLabel('E-mail').fill('pedro.alves@gmail.com');
  await page.getByLabel('Nome completo').fill('Pedro Alves');
  await page.getByLabel('Unidade').selectOption('CJU');
  await page.getByLabel('Perfil').selectOption('autorizador');
  await page.getByLabel('Perfil').blur();
  await foto(page, '08-convidar-usuario');
});

test('09-detalhe-usuario', async ({ page }) => {
  await entrarAdmin(page);
  await page.goto(`/admin/usuarios/detalhe?id=${ANA_ID}`);
  await expect(page.getByRole('button', { name: 'Desativar conta' })).toBeVisible();
  await foto(page, '09-detalhe-usuario');
});

test('10-configuracoes', async ({ page }) => {
  await entrarAdmin(page);
  await page.goto('/admin/configuracoes');
  await expect(page.getByRole('switch', { name: 'Quilometragem' })).toBeVisible();
  await foto(page, '10-configuracoes');
});

test('30-admin-inicio', async ({ page }) => {
  await entrarAdmin(page);
  await page.goto('/admin');
  await expect(page.getByRole('link', { name: /Usuários/ }).first()).toBeVisible();
  await foto(page, '30-admin-inicio');
});

test('31-convites-pendentes', async ({ page }) => {
  await entrarAdmin(page);
  const convite = (id: string, email: string, role: string, unidade: string, estado: string) => ({
    id,
    email,
    role,
    unidade,
    estado,
    criado_em: em(-2),
    expira_em: em(estado === 'expirado' ? -1 : 5),
    usado_em: null,
    revogado_em: null,
    acoes_permitidas: estado === 'aberto' ? ['reenviar', 'revogar'] : [],
  });
  // Sobrescreve a lista de convites do mock (registrada depois, vence a genérica).
  await page.route('**/api/admin/convites**', (route) =>
    route.fulfill({
      json: {
        itens: [
          convite('c1', 'maria.santos@gmail.com', 'solicitante', 'PU', 'aberto'),
          convite('c2', 'joao.pereira@agu.gov.br', 'autorizador', 'UEA', 'aberto'),
          convite('c3', 'carla.lima@agu.gov.br', 'solicitante', 'CJU', 'expirado'),
        ],
        total: 3,
      },
    }),
  );
  await page.goto('/admin/convites');
  await expect(page.getByText('maria.santos@gmail.com')).toBeVisible();
  await foto(page, '31-convites-pendentes');
});

test('32-acessos', async ({ page }) => {
  await entrarAdmin(page);
  await page.goto('/admin/acessos');
  await expect(page.getByText('1–20 de 45')).toBeVisible();
  await foto(page, '32-acessos');
});

test('33-configuracoes-recursos-ligados', async ({ page }) => {
  await entrarAdmin(page);
  const item = (chave: string, rotulo: string, descricao: string) => ({
    chave,
    rotulo,
    descricao,
    habilitado: true,
    exige: chave === 'abastecimento_manutencao' ? ['quilometragem'] : [],
    motivo_indisponibilidade: null,
    acoes_permitidas: ['desligar'],
  });
  await page.route('**/api/admin/configuracoes', (route) =>
    route.fulfill({
      json: {
        recursos: { quilometragem: true, abastecimento_manutencao: true },
        itens: [
          item('quilometragem', 'Quilometragem', 'Exige km de saída e de chegada.'),
          item(
            'abastecimento_manutencao',
            'Abastecimento e manutenção',
            'Registra abastecimentos e manutenções.',
          ),
          item(
            'checklist',
            'Checklist',
            'Obrigatório na saída e na chegada (avarias, combustível, fotos).',
          ),
          item('mfa', 'Verificação em duas etapas (MFA)', 'Exige segundo fator no login.'),
        ],
        acoes_permitidas: ['editar'],
      },
    }),
  );
  await page.goto('/admin/configuracoes');
  const checklist = page.getByRole('switch', { name: 'Checklist' });
  await expect(checklist).toHaveAttribute('aria-checked', 'true');
  await page.evaluate(() => window.scrollTo(0, 400));
  await foto(page, '33-configuracoes-recursos-ligados');
});

test('34-configuracoes-saudacoes-ia', async ({ page }) => {
  await entrarAdmin(page, { saudacaoLigada: true });
  await page.goto('/admin/configuracoes');
  const titulo = page.getByRole('heading', { name: /Saudações/ }).first();
  await expect(titulo).toBeVisible();
  await titulo.evaluate((el) => el.scrollIntoView({ block: 'start' }));
  await page.evaluate(() => window.scrollBy(0, -24));
  await foto(page, '34-configuracoes-saudacoes-ia');
});

test('35-seguranca-mfa', async ({ page }) => {
  await entrarAdmin(page, { fatores: 0 });
  await page.goto('/seguranca');
  await page.getByRole('button', { name: 'Cadastrar autenticador' }).click();
  await expect(page.getByAltText('QR code para cadastrar o autenticador')).toBeVisible();
  await page.getByLabel('Código do autenticador').fill('123456');
  // O QR do mock é um SVG vazio; troca por um padrão de exemplo só para a captura.
  await page
    .getByAltText('QR code para cadastrar o autenticador')
    .evaluate((img: HTMLImageElement) => {
      const q = Array.from({ length: 169 }, (_, i) =>
        (i * 7 + ((i / 13) | 0) * 3) % 5 < 2
          ? `<rect x="${(i % 13) * 8}" y="${((i / 13) | 0) * 8}" width="8" height="8"/>`
          : '',
      ).join('');
      img.src = `data:image/svg+xml;utf-8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 104 104" fill="black">${q}</svg>`;
    });
  await page
    .getByRole('heading', { name: 'Verificação em duas etapas' })
    .evaluate((el) => el.scrollIntoView({ block: 'start' }));
  await foto(page, '35-seguranca-mfa');
});

test('36-detalhe-usuario-foto', async ({ page }) => {
  // Foto de exemplo (círculo com iniciais) no lugar da URL assinada fictícia.
  await page.route('**/storage/v1/object/sign/avatares/**', (route) =>
    route.fulfill({
      contentType: 'image/svg+xml',
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96"><rect width="96" height="96" fill="#0F3D75"/><text x="48" y="60" font-size="36" font-family="Inter,sans-serif" fill="#fff" text-anchor="middle">AS</text></svg>',
    }),
  );
  await entrarAdmin(page, { anaComFoto: true });
  await page.goto(`/admin/usuarios/detalhe?id=${ANA_ID}`);
  await expect(page.getByRole('button', { name: 'Remover foto' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Trocar foto de Ana Souza' })).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 100));
  await foto(page, '36-detalhe-usuario-foto');
});
