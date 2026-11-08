'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { ChevronLeft, Clock, ListChecks, LogOut, Settings, ShieldCheck } from 'lucide-react';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Alerta } from '@/components/ui/alert';
import { Avatar } from '@/components/avatar';
import { TelaMfa } from '@/components/tela-mfa';

function Carregando() {
  return (
    <div role="status" className="flex min-h-dvh items-center justify-center text-texto-2">
      Carregando...
    </div>
  );
}

interface ItemNav {
  href: string;
  rotulo: string;
  icone: React.ReactNode;
}

/**
 * Guarda de rota + cabeçalho + menu inferior. Os itens do menu vêm de `permissoes`
 * devolvidas por /api/eu (calculadas no servidor); o cliente não deduz nada por role.
 */
export function AppShell({
  titulo,
  sobretitulo,
  voltarPara,
  children,
}: {
  titulo: string;
  sobretitulo?: string;
  /** Tela interna (formulário): mostra seta de voltar e oculta o menu inferior. */
  voltarPara?: string;
  children: React.ReactNode;
}) {
  const { estado, eu, aviso, sair, recarregar } = useAuth();
  const router = useRouter();
  const caminho = usePathname();

  React.useEffect(() => {
    if (estado === 'anonimo') router.replace('/login');
  }, [estado, router]);

  if (estado === 'carregando' || estado === 'anonimo') return <Carregando />;
  if (estado === 'mfa') return <TelaMfa />;
  if (estado === 'falha' || !eu) {
    return (
      <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-4 p-6">
        <Alerta>{aviso ?? 'Não foi possível carregar seus dados.'}</Alerta>
        <Button onClick={() => void recarregar()}>Tentar novamente</Button>
        <Button variante="contorno" onClick={() => void sair()}>
          Sair
        </Button>
      </main>
    );
  }

  const permissoes = new Set(eu.permissoes);
  const itens: ItemNav[] = [
    { href: '/requisicoes', rotulo: 'Requisições', icone: <ListChecks aria-hidden /> },
  ];
  if (permissoes.has('autorizar')) {
    itens.push({ href: '/autorizar', rotulo: 'Autorizar', icone: <ShieldCheck aria-hidden /> });
  }
  if (permissoes.has('registrar_chegada')) {
    itens.push({ href: '/viagem', rotulo: 'Viagem', icone: <Clock aria-hidden /> });
  }
  if (permissoes.has('administrar')) {
    itens.push({ href: '/admin', rotulo: 'Admin', icone: <Settings aria-hidden /> });
  }

  if (voltarPara) {
    return (
      <div className="mx-auto flex min-h-dvh max-w-md flex-col bg-white">
        <header className="flex items-center gap-2 px-3 pt-5">
          <Link
            href={voltarPara}
            aria-label="Voltar"
            className="flex size-11 items-center justify-center rounded-full"
          >
            <ChevronLeft aria-hidden />
          </Link>
          <h1 className="text-2xl font-semibold">{titulo}</h1>
        </header>
        <main className="flex-1 px-5 pt-3 pb-8">{children}</main>
      </div>
    );
  }

  return (
    <div className="mx-auto flex min-h-dvh max-w-md flex-col bg-fundo">
      <header className="flex items-start justify-between px-5 pt-5">
        <div>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/logo-agu.webp" alt="AGU - Advocacia-Geral da União" className="h-10 w-auto" />
          {sobretitulo ? (
            <p className="mt-3 text-sm font-medium tracking-wide text-texto-2 uppercase">
              {sobretitulo}
            </p>
          ) : null}
          <h1 className="mt-1 text-3xl font-semibold">{titulo}</h1>
        </div>
        <div className="flex items-center gap-1">
          <Link
            href="/seguranca"
            className="flex size-11 items-center justify-center rounded-full"
            title="Segurança da conta"
            aria-label={`Conta: ${eu.nome} (segurança)`}
          >
            <Avatar
              nome={eu.nome}
              fotoUrl={eu.foto_url}
              decorativo
              className="bg-primaria text-white"
            />
          </Link>
          <button
            type="button"
            onClick={() => void sair()}
            aria-label="Sair"
            className="flex size-11 items-center justify-center rounded-padrao text-texto-2"
          >
            <LogOut aria-hidden />
          </button>
        </div>
      </header>

      <main className="flex-1 px-5 pt-5 pb-28">{children}</main>

      <nav
        aria-label="Navegação principal"
        className="fixed inset-x-0 bottom-0 z-30 mx-auto flex h-16 max-w-md border-t border-borda bg-white"
      >
        {itens.map((i) => {
          const ativo = caminho === i.href || caminho.startsWith(`${i.href}/`);
          return (
            <Link
              key={i.href}
              href={i.href}
              aria-current={ativo ? 'page' : undefined}
              className={cn(
                'flex min-h-11 flex-1 flex-col items-center justify-center gap-0.5 text-sm font-medium [&_svg]:size-6',
                ativo ? 'font-semibold text-primaria' : 'text-texto-2',
              )}
            >
              {i.icone}
              {i.rotulo}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
