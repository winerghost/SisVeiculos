'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';

const TAMANHOS = {
  // 44 px é o alvo mínimo de toque; o pequeno é só ilustrativo (nunca o único alvo interativo).
  pequeno: 'size-8 text-sm',
  medio: 'size-11 text-lg',
  grande: 'size-24 text-4xl',
} as const;

/**
 * Foto de perfil ou, sem foto (ou se a imagem falhar), a inicial do nome.
 * - `decorativo`: use quando o nome já está escrito ao lado (alt vazio, sem ruído para leitores).
 * - A `fotoUrl` é uma URL assinada curta: não é gravada em storage, log nem usada como chave de
 *   cache; quando expira, o erro de carga cai para a inicial.
 */
export function Avatar({
  nome,
  fotoUrl,
  tamanho = 'medio',
  decorativo = false,
  className,
}: {
  nome: string;
  fotoUrl?: string | null;
  tamanho?: keyof typeof TAMANHOS;
  decorativo?: boolean;
  className?: string;
}) {
  // Guarda qual URL falhou: uma URL nova (recarga) volta a tentar a imagem.
  const [falhou, setFalhou] = React.useState<string | null>(null);
  const classes = cn(
    'flex shrink-0 items-center justify-center overflow-hidden rounded-full',
    TAMANHOS[tamanho],
    className,
  );

  if (fotoUrl && falhou !== fotoUrl) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={fotoUrl}
        alt={decorativo ? '' : `Foto de ${nome}`}
        loading="eager"
        decoding="async"
        referrerPolicy="no-referrer"
        draggable={false}
        onError={() => setFalhou(fotoUrl)}
        className={cn(classes, 'bg-fundo object-cover')}
      />
    );
  }
  return (
    <span
      {...(decorativo ? { 'aria-hidden': true } : { role: 'img', 'aria-label': nome })}
      className={cn(classes, 'bg-[#dcebff] font-semibold text-[#0b3c7a]')}
    >
      {nome.charAt(0).toUpperCase()}
    </span>
  );
}
