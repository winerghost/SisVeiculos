'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * Diálogo modal nativo (<dialog> + showModal): prende o foco, fecha com Esc e deixa o resto da
 * página inerte. `onFechar` roda em qualquer fechamento (Esc, botão "Voltar" ou clique no fundo).
 * O conteúdo só é montado enquanto aberto, então o estado interno recomeça a cada abertura.
 */
export function Dialogo({
  aberto,
  titulo,
  descricao,
  onFechar,
  role = 'alertdialog',
  children,
}: {
  aberto: boolean;
  titulo: string;
  descricao?: string;
  onFechar: () => void;
  role?: 'dialog' | 'alertdialog';
  children: React.ReactNode;
}) {
  const ref = React.useRef<HTMLDialogElement>(null);
  const tituloId = React.useId();
  const descId = React.useId();

  React.useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (aberto && !d.open) {
      d.showModal();
      // Foco inicial seguro (ex.: "Voltar" em confirmações destrutivas), quando o conteúdo pede.
      d.querySelector<HTMLElement>('[data-autofocus]')?.focus();
    }
    if (!aberto && d.open) d.close();
  }, [aberto]);

  return (
    <dialog
      ref={ref}
      role={role}
      aria-labelledby={tituloId}
      aria-describedby={descricao ? descId : undefined}
      onClose={onFechar}
      // Eventos do diálogo não podem iniciar o arrasto do cartão que o contém no DOM.
      onPointerDown={(e) => e.stopPropagation()}
      onPointerMove={(e) => e.stopPropagation()}
      onPointerUp={(e) => e.stopPropagation()}
      onClick={(e) => {
        // Clique no fundo (o próprio <dialog>, fora da caixa de conteúdo) = voltar.
        if (e.target === e.currentTarget) onFechar();
      }}
      className={cn(
        'dialogo-entra m-auto w-[calc(100%-2rem)] max-w-md rounded-xl border border-borda bg-white p-0 text-texto',
      )}
    >
      {aberto ? (
        <div className="flex flex-col gap-4 p-5">
          <h2 id={tituloId} className="text-lg font-semibold">
            {titulo}
          </h2>
          {descricao ? (
            <p id={descId} className="text-texto-2">
              {descricao}
            </p>
          ) : null}
          {children}
        </div>
      ) : null}
    </dialog>
  );
}
