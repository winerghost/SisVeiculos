import type { Metadata } from 'next';

// Título estático e genérico (sem dados do usuário). A página segue sendo client component.
export const metadata: Metadata = {
  title: { absolute: 'Detalhe da requisição | Controle de Veículos' },
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
