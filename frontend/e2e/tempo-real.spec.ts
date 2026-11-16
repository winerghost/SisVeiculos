import { expect, test, type Page } from '@playwright/test';
import { mockarApi, respostaAtualizacoes } from './mock-api';

async function entrar(page: Page, email: string) {
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
}

test('configuração mudou e o /eu falhou: a tela continua (sem cartão de falha)', async ({
  page,
}) => {
  await page.clock.install();
  await mockarApi(page);
  const estado = { configuracoes: 'c0', chamadasEu: 0 };
  await page.route('**/api/atualizacoes', (route) => {
    const r = respostaAtualizacoes(0);
    return route.fulfill({
      json: { ...r, recursos: { ...r.recursos, configuracoes: estado.configuracoes } },
    });
  });
  await entrar(page, 'solicitante@exemplo.com');
  await expect(page.getByRole('link', { name: /^Notificações/ })).toBeVisible();

  // Depois do login, o /eu passa a falhar (instabilidade) e o admin muda uma configuração.
  await page.route('**/api/eu', (route) => {
    estado.chamadasEu += 1;
    return route.fulfill({
      status: 500,
      json: { erro: { codigo: 'interno', mensagem: 'Erro interno.' } },
    });
  });
  estado.configuracoes = 'c1';
  await page.clock.fastForward(11_000);
  await page.clock.fastForward(3_000); // espalhamento (jitter) da recarga do /eu
  await expect.poll(() => estado.chamadasEu).toBeGreaterThan(0);
  await expect(page.getByRole('button', { name: 'Tentar novamente' })).toHaveCount(0);
  await expect(page.getByText('Erro interno.')).toHaveCount(0);
  await expect(page.getByRole('link', { name: /^Notificações/ })).toBeVisible();
});
