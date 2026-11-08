'use client';

import Link from 'next/link';
import { Bell } from 'lucide-react';
import { useAuth } from '@/lib/auth';

/** Sino do cabeçalho: leva a /notificacoes e mostra o contador de não lidas informado pela API. */
export function SinoNotificacoes() {
  const { naoLidas } = useAuth();
  return (
    <Link
      href="/notificacoes"
      aria-label={`Notificações, ${naoLidas} não ${naoLidas === 1 ? 'lida' : 'lidas'}`}
      className="relative flex size-11 shrink-0 items-center justify-center rounded-full text-texto-2"
    >
      <Bell aria-hidden />
      {naoLidas > 0 ? (
        <span
          aria-hidden
          data-testid="contador-notificacoes"
          className="absolute top-0.5 right-0.5 flex min-w-5 items-center justify-center rounded-full bg-erro px-1 text-xs leading-5 font-semibold text-white"
        >
          {naoLidas > 99 ? '99+' : naoLidas}
        </span>
      ) : null}
    </Link>
  );
}
