'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import {
  BarChart3,
  CalendarDays,
  ChevronLeft,
  Car,
  Clock,
  FileText,
  ListChecks,
  LogOut,
  ScanLine,
  Settings,
  ShieldCheck,
} from 'lucide-react';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { ID_TITULO_PAGINA } from '@/lib/foco';
import { urlDeLogin } from '@/lib/voltar';
import { Button } from '@/components/ui/button';
import { Alerta } from '@/components/ui/alert';
import { Avatar } from '@/components/avatar';
import { TelaMfa } from '@/components/tela-mfa';
import { SinoNotificacoes } from '@/components/sino-notificacoes';
import { Saudacao } from '@/components/saudacao';
import { IndicadorConexao } from '@/components/indicador-conexao';
import { FaixaAtualizacao } from '@/components/faixa-atualizacao';

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
  largo = false,
  children,
}: {
  titulo: string;
  sobretitulo?: string;
  /** Tela interna (formulário): mostra seta de voltar e oculta o menu inferior. */
  voltarPara?: string;
  /** Telas com tabela ou calendário: a partir de `md` a coluna alarga (no celular nada muda). */
  largo?: boolean;
  children: React.ReactNode;
}) {
  const { estado, eu, paineis, relatorios, aviso, sair, recarregar } = useAuth();
  const router = useRouter();
  const caminho = usePathname();

  // Esta tela já teve sessão? Se sim, a ida ao login é logout/expiração e segue sem `voltar`;
  // se não (link aberto sem sessão), o login devolve o usuário a esta tela depois de entrar.
  const teveSessao = React.useRef(false);
  React.useEffect(() => {
    if (estado === 'ativo' || estado === 'mfa' || estado === 'falha') teveSessao.current = true;
  }, [estado]);

  React.useEffect(() => {
    if (estado !== 'anonimo') return;
    router.replace(
      teveSessao.current ? '/login' : urlDeLogin(window.location.pathname, window.location.search),
    );
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
  // Perfil Motorista: a API devolve `ver_viagens_atribuidas` (e o recurso ligado em `recursos`).
  // Quem tem a página dedicada não vê os itens genéricos de requisições/viagem.
  const comViagens = permissoes.has('ver_viagens_atribuidas') && eu.recursos.motorista === true;
  const itens: ItemNav[] = comViagens
    ? [{ href: '/motorista', rotulo: 'Minhas viagens', icone: <Car aria-hidden /> }]
    : [{ href: '/requisicoes', rotulo: 'Requisições', icone: <ListChecks aria-hidden /> }];
  if (permissoes.has('autorizar')) {
    itens.push({ href: '/autorizar', rotulo: 'Autorizar', icone: <ShieldCheck aria-hidden /> });
  }
  if ((permissoes.has('registrar_chegada') || permissoes.has('registrar_saida')) && !comViagens) {
    itens.push({ href: '/viagem', rotulo: 'Viagem', icone: <Clock aria-hidden /> });
  }
  // Calendário (somente leitura): só com `ver_calendario` devolvida pela API.
  if (permissoes.has('ver_calendario')) {
    itens.push({ href: '/calendario', rotulo: 'Calendário', icone: <CalendarDays aria-hidden /> });
  }
  // Visível só se GET /api/dashboards respondeu com ao menos um painel.
  if (paineis && paineis.length > 0) {
    itens.push({ href: '/dashboards', rotulo: 'Dashboards', icone: <BarChart3 aria-hidden /> });
  }
  // Visível só se GET /api/relatorios respondeu (o servidor filtra o catálogo por perfil).
  if (relatorios && relatorios.relatorios.length > 0) {
    itens.push({ href: '/relatorios', rotulo: 'Relatórios', icone: <FileText aria-hidden /> });
  }
  // Só com o recurso ligado pela API (eu.recursos); a própria API também recusa a consulta.
  if (eu.recursos.leitor_qr_veiculos === true) {
    itens.push({ href: '/veiculos/ler', rotulo: 'Ler QR', icone: <ScanLine aria-hidden /> });
  }
  if (permissoes.has('administrar')) {
    itens.push({ href: '/admin', rotulo: 'Admin', icone: <Settings aria-hidden /> });
  }

  if (voltarPara) {
    return (
      <div className="mx-auto flex min-h-dvh max-w-md flex-col bg-white">
        <header className="flex items-center gap-2 px-3 pt-[max(1.25rem,env(safe-area-inset-top))]">
          <Link
            href={voltarPara}
            aria-label="Voltar"
            className="flex size-11 items-center justify-center rounded-full"
          >
            <ChevronLeft aria-hidden />
          </Link>
          <h1
            id={ID_TITULO_PAGINA}
            tabIndex={-1}
            className="min-w-0 flex-1 text-2xl font-semibold break-words outline-none"
          >
            {titulo}
          </h1>
          <SinoNotificacoes />
        </header>
        <IndicadorConexao />
        <FaixaAtualizacao />
        <main className="flex-1 px-5 pt-3 pb-8">{children}</main>
      </div>
    );
  }

  return (
    <div
      className={cn('mx-auto flex min-h-dvh max-w-md flex-col bg-fundo', largo && 'md:max-w-5xl')}
    >
      <header className="flex items-start justify-between gap-2 px-5 pt-[max(1.25rem,env(safe-area-inset-top))]">
        <div className="min-w-0">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/logo-agu.webp"
            alt="AGU - Advocacia-Geral da União"
            className="h-10 w-auto max-w-full"
          />
          {sobretitulo ? (
            <p className="mt-3 text-sm font-medium tracking-wide text-texto-2 uppercase">
              {sobretitulo}
            </p>
          ) : null}
          <h1
            id={ID_TITULO_PAGINA}
            tabIndex={-1}
            className={cn(
              'mt-1 font-semibold break-words outline-none',
              // Títulos longos ficam numa linha só ao lado dos ícones do cabeçalho.
              titulo.length > 12 ? 'text-2xl' : 'text-3xl',
            )}
          >
            {titulo}
          </h1>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <SinoNotificacoes />
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
      <IndicadorConexao />
      <FaixaAtualizacao />

      <main className="flex-1 px-5 pt-5 pb-[calc(7rem+env(safe-area-inset-bottom))]">
        <Saudacao />
        <div key={caminho} className="pagina-entra">
          {children}
        </div>
      </main>

      <nav
        aria-label="Navegação principal"
        className={cn(
          'fixed inset-x-0 bottom-0 z-30 mx-auto flex h-[calc(4rem+env(safe-area-inset-bottom))] max-w-md overflow-x-auto border-t border-borda bg-white pb-[env(safe-area-inset-bottom)] [scrollbar-width:none]',
          largo && 'md:max-w-5xl',
        )}
      >
        {itens.map((i) => {
          const ativo = caminho === i.href || caminho.startsWith(`${i.href}/`);
          return (
            <Link
              key={i.href}
              href={i.href}
              aria-current={ativo ? 'page' : undefined}
              className={cn(
                'flex min-h-11 min-w-[3.75rem] flex-1 shrink-0 flex-col items-center justify-center gap-0.5 px-0.5 font-medium [&_svg]:size-6',
                itens.length > 5 ? 'text-[0.6875rem] tracking-tight' : 'text-sm',
                ativo ? 'font-semibold text-primaria' : 'text-texto-2',
              )}
            >
              {i.icone}
              <span className="max-w-full truncate">{i.rotulo}</span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
