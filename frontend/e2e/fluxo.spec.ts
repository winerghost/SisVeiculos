import { expect, test, type Page } from '@playwright/test';
import { mockarApi } from './mock-api';

async function entrar(page: Page, email: string, senha = 'senha-correta') {
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill(senha);
  await page.getByRole('button', { name: 'Entrar' }).click();
}

test('login -> solicitar -> autorizar -> saída -> chegada', async ({ page }) => {
  const mock = await mockarApi(page);

  // Solicitante cria a requisição.
  await entrar(page, 'solicitante@exemplo.com');
  await expect(page).toHaveURL(/\/requisicoes$/);
  await expect(page.getByText('Veículo 01 livre agora')).toBeVisible();
  // Solicitante não vê o menu de autorização (permissoes vêm da API).
  await expect(page.getByRole('link', { name: 'Autorizar' })).toHaveCount(0);

  await page.getByRole('link', { name: 'Nova requisição' }).click();
  await page.getByLabel('Veículo', { exact: true }).selectOption({ index: 1 });
  await page.getByRole('button', { name: /Escolha o dia e o horário/ }).click();
  const gaveta = page.getByRole('dialog');
  await gaveta.getByRole('button', { name: /, 28 de / }).click();
  await gaveta.getByLabel('Hora').selectOption('14');
  await gaveta.getByLabel('Minuto').selectOption('30');
  await gaveta.getByRole('button', { name: 'Confirmar' }).click();
  await expect(page.getByRole('button', { name: /28\/\d{2}\/\d{4} às 14:30/ })).toBeVisible();
  await page.getByLabel('Trajeto').fill('Sede -> Fórum Federal, Palmas');
  await page.getByRole('button', { name: 'Enviar requisição' }).click();

  await expect(page).toHaveURL(/\/requisicoes\/detalhe\?id=/);
  await expect(page.getByText('Solicitada', { exact: true }).first()).toBeVisible();
  // Só o que a API permitiu: cancelar. Nada de autorizar/saída/chegada.
  await expect(page.getByRole('button', { name: 'Cancelar requisição' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Autorizar' })).toHaveCount(0);
  expect(JSON.stringify(mock.corpos[0])).toContain('-03:00');

  // Autorizador decide, registra saída e chegada.
  await page.getByRole('link', { name: 'Voltar' }).click();
  await page.getByRole('button', { name: 'Sair' }).click();
  await expect(page).toHaveURL(/\/login$/);
  await entrar(page, 'autorizador@exemplo.com');
  await page.getByRole('link', { name: 'Autorizar' }).click();
  await expect(page.getByRole('tab', { name: /Pendentes · 1/ })).toBeVisible();
  await page.getByRole('button', { name: 'Autorizar' }).click();

  await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
  await page.getByRole('button', { name: 'Registrar saída' }).click();
  await expect(page.getByRole('tab', { name: /Em uso · 1/ })).toBeVisible();

  await page.getByRole('link', { name: 'Viagem' }).click();
  await page.getByRole('button', { name: 'Registrar chegada agora' }).click();
  await expect(page.getByText('Nenhuma viagem em uso.')).toBeVisible();

  await page.getByRole('link', { name: 'Requisições' }).click();
  await expect(page.getByText('Concluída', { exact: true })).toBeVisible();
  expect(mock.reqs[0]?.status).toBe('concluida');
});

test('login inválido exibe mensagem', async ({ page }) => {
  await mockarApi(page);
  await entrar(page, 'solicitante@exemplo.com', 'errada');
  await expect(page.getByRole('alert').filter({ hasText: 'E-mail ou senha' })).toBeVisible();
});

test('403 mfa_requerido exibe a mensagem da API e a verificação do segundo fator', async ({
  page,
}) => {
  await mockarApi(page, { exigirMfa: true });
  await entrar(page, 'autorizador@exemplo.com');
  await expect(page.getByRole('heading', { name: 'Verificação em duas etapas' })).toBeVisible();
  await expect(page.getByText('Este perfil exige verificação em duas etapas.')).toBeVisible();
  await expect(page.getByLabel('Código do autenticador')).toBeVisible();
});

test('erro 409 da criação é exibido como veio', async ({ page }) => {
  await mockarApi(page, { veiculoOcupado: true });
  await entrar(page, 'solicitante@exemplo.com');
  await page.getByRole('link', { name: 'Nova requisição' }).click();
  await page.getByLabel('Veículo', { exact: true }).selectOption({ index: 1 });
  await page.getByLabel('Trajeto').fill('Sede -> Receita Federal');
  await page.getByRole('button', { name: 'Enviar requisição' }).click();
  await expect(
    page.getByRole('alert').filter({ hasText: 'Este veículo já possui uma requisição em aberto.' }),
  ).toBeVisible();
});

test('manifest standalone com ícones maskable e service worker servidos', async ({ request }) => {
  const m = await (await request.get('/manifest.webmanifest')).json();
  expect(m.display).toBe('standalone');
  const icones = m.icons as { src: string; sizes: string; purpose: string }[];
  const maskable = icones.filter((i) => i.purpose === 'maskable');
  expect(maskable.map((i) => i.sizes)).toEqual(['192x192', '512x512']);
  // Maskable com arquivo próprio (safe zone), distinto dos ícones "any".
  const any = icones.filter((i) => i.purpose === 'any').map((i) => i.src);
  for (const i of maskable) {
    expect(any).not.toContain(i.src);
    expect((await request.get(i.src)).ok()).toBe(true);
  }
  expect((await request.get('/sw.js')).ok()).toBe(true);
});

async function comoAutorizador(page: Page, opcoes: Parameters<typeof mockarApi>[1]) {
  const mock = await mockarApi(page, opcoes);
  await entrar(page, 'autorizador@exemplo.com');
  return mock;
}

test('km ligado: saída pede só km_saida e envia no corpo; valor gravado aparece', async ({
  page,
}) => {
  const mock = await comoAutorizador(page, { km: true, sementes: [{ status: 'autorizada' }] });
  await page.getByRole('link', { name: 'Autorizar' }).click();
  await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
  await expect(page.getByLabel('Km de chegada')).toHaveCount(0);
  const campo = page.getByLabel('Km de saída');
  await expect(campo).toHaveAttribute('type', 'number');
  await expect(campo).toHaveAttribute('required', '');
  await campo.fill('12345');
  await page.getByRole('button', { name: 'Registrar saída' }).click();
  await expect(page.getByRole('tab', { name: /Em uso · 1/ })).toBeVisible();
  expect(mock.corposAcao[0]).toEqual({ acao: 'saida', corpo: { km_saida: 12345 } });

  await page.getByRole('tab', { name: /Em uso · 1/ }).click();
  await expect(page.getByText('Km de saída: 12.345')).toBeVisible();
  // Chegada: a API pede só km_chegada porque a saída já tem km; km_invalido preserva o valor.
  await expect(page.getByLabel('Km de saída')).toHaveCount(0);
  await page.getByLabel('Km de chegada').fill('100');
  await page.getByRole('button', { name: 'Registrar chegada agora' }).click();
  await expect(
    page.getByText('O km de chegada não pode ser menor que o km de saída.'),
  ).toBeVisible();
  await expect(page.getByLabel('Km de chegada')).toHaveValue('100');
  await page.getByLabel('Km de chegada').fill('12400');
  await page.getByRole('button', { name: 'Registrar chegada agora' }).click();
  await expect(page.getByRole('tab', { name: /Em uso · 0/ })).toBeVisible();
  expect(mock.corposAcao[2]).toEqual({ acao: 'chegada', corpo: { km_chegada: 12400 } });

  await page.getByRole('link', { name: 'Requisições' }).click();
  await page.getByText('Sede -> Fórum').click();
  await expect(page.getByText('Km de saída: 12.345')).toBeVisible();
  await expect(page.getByText('Km de chegada: 12.400')).toBeVisible();
});

test('km desligado: sem campos e POST sem corpo', async ({ page }) => {
  const mock = await comoAutorizador(page, { sementes: [{ status: 'autorizada' }] });
  await page.getByRole('link', { name: 'Autorizar' }).click();
  await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
  await expect(page.locator('input[type=number]')).toHaveCount(0);
  await page.getByRole('button', { name: 'Registrar saída' }).click();
  await expect(page.getByRole('tab', { name: /Em uso · 1/ })).toBeVisible();
  expect(mock.corposAcao[0]).toEqual({ acao: 'saida', corpo: null });
});

test('km ligado, viagem em uso sem km de saída: pede os dois campos', async ({ page }) => {
  const mock = await comoAutorizador(page, {
    km: true,
    sementes: [{ status: 'em_uso', saida_real_em: new Date().toISOString() }],
  });
  // goto (e não clique no link): este teste DIGITA logo após abrir a tela. No servidor de teste os
  // payloads de prefetch do Next dão 404 e o Next cai para um recarregamento completo que, sob
  // carga, pode ocorrer tarde e apagar o campo já preenchido (falha intermitente).
  await page.goto('/viagem');
  await expect(page.getByText('Km de saída:')).toHaveCount(0);
  await page.getByLabel('Km de chegada').fill('500');
  // Campo de saída vazio: a validação nativa (required) impede o envio.
  await page.getByRole('button', { name: 'Registrar chegada agora' }).click();
  expect(mock.corposAcao).toHaveLength(0);
  await page.getByLabel('Km de saída').fill('400');
  await page.getByRole('button', { name: 'Registrar chegada agora' }).click();
  await expect(page.getByText('Nenhuma viagem em uso.')).toBeVisible();
  expect(mock.corposAcao[0]).toEqual({
    acao: 'chegada',
    corpo: { km_saida: 400, km_chegada: 500 },
  });
});

test('km_obrigatorio da API é exibido como veio', async ({ page }) => {
  await comoAutorizador(page, { km: true, sementes: [{ status: 'autorizada' }] });
  await page.getByRole('link', { name: 'Autorizar' }).click();
  await page.getByRole('tab', { name: /Autorizadas · 1/ }).click();
  // Só no teste: ignora a validação nativa para o servidor (mock) recusar o corpo.
  await page.locator('form').evaluate((f) => f.setAttribute('novalidate', ''));
  await page.getByLabel('Km de saída').fill('');
  await page.getByRole('button', { name: 'Registrar saída' }).click();
  await expect(page.getByText('Informe a quilometragem.')).toBeVisible();
});

test('km gravado aparece na viagem com o recurso desligado', async ({ page }) => {
  await comoAutorizador(page, {
    sementes: [{ status: 'em_uso', saida_real_em: new Date().toISOString(), km_saida: 1234567 }],
  });
  await page.getByRole('link', { name: 'Viagem' }).click();
  await expect(page.getByText('Km de saída: 1.234.567')).toBeVisible();
  await expect(page.locator('input[type=number]')).toHaveCount(0);
});
