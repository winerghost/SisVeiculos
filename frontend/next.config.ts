import type { NextConfig } from 'next';

// Export estático servido pelo nginx (gera out/). Sem SSR, route handlers ou Server Actions.
const config: NextConfig = {
  output: 'export',
  images: { unoptimized: true },
  reactStrictMode: true,
};

export default config;
