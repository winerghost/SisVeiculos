import { defineConfig, devices } from '@playwright/test';

// Testes de ponta a ponta contra a API MOCKADA (page.route): sem credenciais nem rede reais.
// O export estático é construído com valores de teste e servido por `serve`.
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: false,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:3100',
    locale: 'pt-BR',
    timezoneId: 'America/Sao_Paulo',
    // O service worker enxergaria /api antes do page.route; nos testes ele é bloqueado.
    serviceWorkers: 'block',
    // Sem animação nos testes: a splash do login é pulada e nada atrasa os cliques.
    reducedMotion: 'reduce',
  },
  projects: [{ name: 'mobile', use: { ...devices['Pixel 7'] } }],
  webServer: {
    command: 'npm run build && npx serve out -l 3100 --no-clipboard',
    url: 'http://127.0.0.1:3100',
    reuseExistingServer: !process.env.CI,
    timeout: 240_000,
    env: {
      NEXT_PUBLIC_SUPABASE_URL: 'https://exemplo.supabase.co',
      NEXT_PUBLIC_SUPABASE_ANON_KEY: 'anon-key-de-teste',
      NEXT_PUBLIC_SITE_URL: 'https://sisv.exemplo.test',
      NEXT_TELEMETRY_DISABLED: '1',
    },
  },
});
