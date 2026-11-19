'use client';

import * as React from 'react';
import { X } from 'lucide-react';
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
  bloqueado = false,
  icone,
  comBotaoFechar = false,
  larga = false,
  children,
}: {
  /** Opcional: ícone decorativo ao lado do título (cabeçalho em linha). */
  icone?: React.ReactNode;
  /** Opcional: botão X (aria-label "Fechar") no cabeçalho; desabilitado com `bloqueado`. */
  comBotaoFechar?: boolean;
  /** Opcional: caixa um pouco mais larga (max-w-lg). */
  larga?: boolean;
  aberto: boolean;
  titulo: string;
  descricao?: string;
  onFechar: () => void;
  role?: 'dialog' | 'alertdialog';
  /** Envio em andamento: Esc e clique no fundo não fecham (o resultado ainda vai chegar). */
  bloqueado?: boolean;
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
      aria-labelledby={aberto ? tituloId : undefined}
      aria-describedby={descricao ? descId : undefined}
      onClose={() => {
        // O Chrome ignora preventDefault no 2º Esc seguido: durante o envio, reabre em vez de fechar.
        if (bloqueado && aberto) {
          ref.current?.showModal();
          return;
        }
        onFechar();
      }}
      onCancel={(e) => {
        // Esc: o <dialog> fecharia sozinho; durante o envio, fica aberto.
        if (bloqueado) e.preventDefault();
      }}
      // Eventos do diálogo não podem iniciar o arrasto do cartão que o contém no DOM.
      onPointerDown={(e) => e.stopPropagation()}
      onPointerMove={(e) => e.stopPropagation()}
      onPointerUp={(e) => e.stopPropagation()}
      onClick={(e) => {
        // Clique no fundo (o próprio <dialog>, fora da caixa de conteúdo) = voltar.
        if (e.target === e.currentTarget && !bloqueado) onFechar();
      }}
      className={cn(
        'dialogo-entra m-auto max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] overflow-y-auto overscroll-contain rounded-xl border border-borda bg-white p-0 text-texto',
        larga ? 'max-w-lg' : 'max-w-md',
      )}
    >
      {aberto ? (
        <div className="flex flex-col gap-4 p-5">
          {icone || comBotaoFechar ? (
            <div className="flex items-start gap-3">
              {icone}
              <div className="min-w-0 flex-1">
                <h2 id={tituloId} className="text-lg font-semibold">
                  {titulo}
                </h2>
                {descricao ? (
                  <p id={descId} className="text-texto-2">
                    {descricao}
                  </p>
                ) : null}
              </div>
              {comBotaoFechar ? (
                <button
                  type="button"
                  aria-label="Fechar"
                  disabled={bloqueado}
                  onClick={onFechar}
                  className="-mt-1 -mr-2 inline-flex size-11 shrink-0 items-center justify-center rounded-padrao text-texto hover:bg-fundo disabled:opacity-60"
                >
                  <X aria-hidden className="size-5" />
                </button>
              ) : null}
            </div>
          ) : (
            <>
              <h2 id={tituloId} className="text-lg font-semibold">
                {titulo}
              </h2>
              {descricao ? (
                <p id={descId} className="text-texto-2">
                  {descricao}
                </p>
              ) : null}
            </>
          )}
          {children}
        </div>
      ) : null}
    </dialog>
  );
}
