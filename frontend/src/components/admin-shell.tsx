'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { AppShell } from '@/components/app-shell';
import { Alerta } from '@/components/ui/alert';

const ABAS = [
  { href: '/admin/veiculos', rotulo: 'Veículos' },
  { href: '/admin/usuarios', rotulo: 'Usuários' },
  { href: '/admin/convites', rotulo: 'Convites' },
  { href: '/admin/configuracoes', rotulo: 'Configurações' },
];

function Corpo({ comAbas, children }: { comAbas: boolean; children: React.ReactNode }) {
  const { eu } = useAuth();
  const caminho = usePathname();
  // A permissão `administrar` vem de /api/eu (calculada no servidor); a API revalida cada chamada.
  if (!eu?.permissoes.includes('administrar')) {
    return <Alerta>Você não tem permissão para acessar a administração.</Alerta>;
  }
  return (
    <>
      {comAbas ? (
        <nav aria-label="Administração" className="-mx-5 mb-5 flex gap-2 overflow-x-auto px-5">
          {ABAS.map((a) => {
            const ativa = caminho === a.href || caminho.startsWith(`${a.href}/`);
            return (
              <Link
                key={a.href}
                href={a.href}
                aria-current={ativa ? 'page' : undefined}
                className={cn(
                  'flex min-h-11 items-center rounded-full border px-4 text-base font-medium whitespace-nowrap',
                  ativa
                    ? 'border-primaria bg-primaria text-white'
                    : 'border-borda-campo bg-white text-texto',
                )}
              >
                {a.rotulo}
              </Link>
            );
          })}
        </nav>
      ) : null}
      {children}
    </>
  );
}

/** Páginas de administração: AppShell + guarda por `administrar` + abas de navegação. */
export function AdminShell({
  titulo,
  voltarPara,
  children,
}: {
  titulo: string;
  /** Tela interna (formulário): seta de voltar, sem abas. */
  voltarPara?: string;
  children: React.ReactNode;
}) {
  return (
    <AppShell titulo={titulo} sobretitulo="Administração" voltarPara={voltarPara}>
      <Corpo comAbas={!voltarPara}>{children}</Corpo>
    </AppShell>
  );
}
