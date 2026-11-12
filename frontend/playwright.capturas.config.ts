import { defineConfig, devices } from '@playwright/test';

// Gera os PNGs de docs/mockups a partir das telas reais (API mockada). Não faz parte do `test:e2e`.
// Uso: servir `out` em 3100 (npx serve out -l 3100) e rodar `npx playwright test -c playwright.capturas.config.ts`.
export default defineConfig({
  testDir: './e2e/capturas',
  testMatch: /.*\.cap\.ts/,
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:3100',
    locale: 'pt-BR',
    timezoneId: 'America/Sao_Paulo',
    serviceWorkers: 'block',
    reducedMotion: 'reduce',
    ...devices['Pixel 7'],
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
  },
  projects: [{ name: 'capturas' }],
});
