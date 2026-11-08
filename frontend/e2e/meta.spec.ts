import { expect, test } from '@playwright/test';

const SITE = 'https://sisv.exemplo.test';

// A raiz redireciona (no cliente) para /login; por isso o HTML estático é lido sem executar JS.
test('metadados de marca, compartilhamento e indexação (HTML estático da raiz)', async ({
  request,
}) => {
  const html = await (await request.get('/')).text();
  const meta = (attr: string, nome: string) =>
    new RegExp(`<meta ${attr}="${nome}" content="([^"]*)"`).exec(html)?.[1];

  expect(html).toContain('<html lang="pt-BR"');
  expect(/<title>([^<]*)<\/title>/.exec(html)?.[1]).toBe('Controle de Veículos | AGU');
  expect(meta('property', 'og:title')).toBe('Controle de Veículos | AGU');
  const og = meta('property', 'og:image');
  expect(og).toBe(`${SITE}/og/og-agu-v1.png`);
  expect(meta('property', 'og:url')).toBe(SITE);
  expect(meta('name', 'twitter:card')).toBe('summary_large_image');
  expect(meta('name', 'twitter:image')).toBe(`${SITE}/og/og-agu-v1.png`);
  expect(meta('name', 'robots')).toBe('noindex, nofollow');
  expect(meta('name', 'theme-color')).toBe('#0F3D75');

  // O host do build é fictício: as imagens são pedidas no servidor de teste, pelo caminho.
  for (const caminho of [
    new URL(og as string).pathname,
    '/favicon.ico',
    '/icons/apple-touch-icon.png',
  ]) {
    expect((await request.get(caminho)).status()).toBe(200);
  }
});

test('título por segmento é genérico e sem dados, no navegador', async ({ page }) => {
  await page.goto('/login');
  await expect(page).toHaveTitle('Entrar | Controle de Veículos');
  await expect(page.locator('html')).toHaveAttribute('lang', 'pt-BR');
  await page.goto('/convite');
  await expect(page).toHaveTitle('Aceitar convite | Controle de Veículos');
});
