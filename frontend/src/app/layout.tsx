import type { Metadata, Viewport } from 'next';
import { AuthProvider } from '@/lib/auth';
import { RegistrarSw } from '@/components/registrar-sw';
import './globals.css';

const NOME = 'Controle de Veículos | AGU';
const DESCRICAO =
  'Sistema interno para solicitar, autorizar e registrar a saída e a chegada de veículos da AGU. Acesso restrito a usuários cadastrados.';

// Sem domínio no código: a URL pública vem do build (NEXT_PUBLIC_SITE_URL).
export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? 'http://localhost:3000'),
  title: { default: NOME, template: '%s | Controle de Veículos' },
  description: DESCRICAO,
  manifest: '/manifest.webmanifest',
  robots: { index: false, follow: false },
  icons: {
    icon: [{ url: '/favicon.ico', sizes: '48x48' }],
    shortcut: '/favicon.ico',
    apple: [{ url: '/icons/apple-touch-icon.png', sizes: '180x180', type: 'image/png' }],
  },
  openGraph: {
    title: NOME,
    description: DESCRICAO,
    type: 'website',
    siteName: NOME,
    locale: 'pt_BR',
    url: '/',
    images: [
      {
        url: '/og/og-agu-v1.png',
        width: 1200,
        height: 630,
        alt: 'Controle de Veículos da AGU: saída e chegada de veículos',
      },
    ],
  },
  twitter: {
    card: 'summary_large_image',
    title: NOME,
    description: DESCRICAO,
    images: ['/og/og-agu-v1.png'],
  },
};

export const viewport: Viewport = {
  themeColor: '#0F3D75',
  width: 'device-width',
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>
        <a
          href="#conteudo"
          className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-padrao focus:bg-white focus:p-3"
        >
          Ir para o conteúdo
        </a>
        <AuthProvider>
          <div id="conteudo">{children}</div>
        </AuthProvider>
        <RegistrarSw />
      </body>
    </html>
  );
}
