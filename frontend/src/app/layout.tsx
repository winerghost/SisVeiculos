import type { Metadata, Viewport } from 'next';
import { AuthProvider } from '@/lib/auth';
import { RegistrarSw } from '@/components/registrar-sw';
import './globals.css';

export const metadata: Metadata = {
  title: 'Controle de veículos - AGU',
  description: 'Solicitação, autorização e registro de saída e chegada de veículos da AGU.',
  manifest: '/manifest.webmanifest',
  icons: { icon: '/icons/icon-192.png', apple: '/icons/icon-192.png' },
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
