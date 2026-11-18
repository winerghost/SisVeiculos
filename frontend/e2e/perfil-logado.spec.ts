import { expect, test, type Page } from '@playwright/test';
import { mockarApi } from './mock-api';

const ROTULOS = {
  solicitante: 'Solicitante',
  autorizador: 'Autorizador',
  admin: 'Administrador',
} as const;
type Role = keyof typeof ROTULOS;

/** Entra como solicitante e, depois, faz /api/eu responder com o `role` desejado (vem da API). */
async function entrarComo(page: Page, role: Role) {
  await mockarApi(page);
  await page.route('**/api/eu', (route) =>
    route.fulfill({
      json: {
        id: 'u-perfil',
        nome: 'Marcos Silva',
        unidade: 'UEA',
        rotulo_perfil: `UEA · ${ROTULOS[role]}`,
        unidades: ['PU', 'PF', 'CJU', 'UEA'],
        role,
        permissoes: ['criar_requisicao', 'ver_proprias_requisicoes', 'ver_todas_requisicoes'],
        recursos: {},
      },
    }),
  );
  await page.goto('/login');
  await page.getByLabel('E-mail').fill('solicitante@agu.gov.br');
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await expect(page).toHaveURL(/\/requisicoes/);
}

for (const role of Object.keys(ROTULOS) as Role[]) {
  const rotulo = ROTULOS[role];

  test(`desktop (${role}): nome e selo do perfil ao lado do avatar`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await entrarComo(page, role);
    const conta = page.getByRole('link', {
      name: `Conta: Marcos Silva, perfil ${rotulo} (segurança)`,
    });
    await expect(conta).toBeVisible();
    await expect(conta.getByText('Marcos Silva')).toBeVisible();
    await expect(conta.getByTestId('perfil-selo')).toBeVisible();
    await expect(conta.getByTestId('perfil-selo')).toHaveText(rotulo);
    await expect(page.getByText('Gestão de solicitações de veículos')).toBeVisible();
  });

  test(`mobile 390px (${role}): sem selo visível e aria-label com o perfil`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await entrarComo(page, role);
    const conta = page.getByRole('link', {
      name: `Conta: Marcos Silva, perfil ${rotulo} (segurança)`,
    });
    await expect(conta).toBeVisible();
    await expect(conta).toHaveAttribute(
      'title',
      `Conta: Marcos Silva, perfil ${rotulo} (segurança)`,
    );
    await expect(page.getByTestId('perfil-selo')).toBeHidden();
    await expect(page.getByText('Gestão de solicitações de veículos')).toBeHidden();
    // O avatar segue com 44 px de alvo e sem alargar no celular.
    const caixa = await conta.boundingBox();
    expect(Math.round(caixa?.width ?? 0)).toBe(44);
  });

  test(`/seguranca (${role}) mostra nome, unidade e perfil`, async ({ page }) => {
    await entrarComo(page, role);
    await page.goto('/seguranca');
    await expect(page.getByTestId('conta-nome')).toHaveText('Marcos Silva');
    await expect(page.getByTestId('conta-unidade')).toHaveText('Unidade: UEA');
    await expect(page.getByRole('region', { name: 'Dados da conta' })).toContainText(rotulo);
  });
}

test('mobile: abas Ativas/Arquivadas sem destaque azul; desktop com destaque', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await entrarComo(page, 'solicitante');
  const ativas = page.getByRole('button', { name: 'Ativas', exact: true }).first();
  await expect(ativas).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(ativas).toHaveCSS('background-color', 'rgb(15, 61, 117)');
  await expect(ativas).toHaveCSS('color', 'rgb(255, 255, 255)');
});
