import type { Metadata } from 'next';

// Título estático e genérico (sem dados de usuários). A página segue sendo client component.
export const metadata: Metadata = { title: 'Acessos' };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
