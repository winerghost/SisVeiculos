'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

const LIMIAR = 0.4;
const INICIO_PX = 6;

interface Props {
  children: React.ReactNode;
  /** Arrastar para a esquerda (ou ArrowLeft). Ausente: gesto desabilitado. */
  onAprovar?: () => void;
  /** Confirmado após arrastar para a direita (ou ArrowRight). Ausente: gesto desabilitado. */
  onExcluir?: () => void;
  ocupado?: boolean;
  className?: string;
}

/**
 * Item de lista com gesto de arrastar (toque e mouse) e alternativa por teclado (WCAG 2.5.1).
 * Só dispara o que a API liberou (o pai passa os callbacks conforme `acoes_permitidas`).
 * A exclusão, irreversível, pede confirmação inline.
 */
export function ItemArrastavel({ children, onAprovar, onExcluir, ocupado, className }: Props) {
  const dicaId = React.useId();
  const caixa = React.useRef<HTMLDivElement>(null);
  const inicio = React.useRef<{ x: number; id: number; ativo: boolean } | null>(null);
  const [dx, setDx] = React.useState(0);
  const [arrastando, setArrastando] = React.useState(false);
  const [confirmando, setConfirmando] = React.useState(false);

  function limitar(v: number, largura: number) {
    const max = onExcluir ? largura : 0;
    const min = onAprovar ? -largura : 0;
    return Math.max(min, Math.min(max, v));
  }

  function aoPressionar(e: React.PointerEvent<HTMLDivElement>) {
    if (ocupado || confirmando) return;
    if ((e.target as HTMLElement).closest('button, a, input, select, textarea')) return;
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    inicio.current = { x: e.clientX, id: e.pointerId, ativo: false };
  }

  function aoMover(e: React.PointerEvent<HTMLDivElement>) {
    const i = inicio.current;
    if (!i || i.id !== e.pointerId) return;
    const delta = e.clientX - i.x;
    if (!i.ativo) {
      if (Math.abs(delta) < INICIO_PX) return;
      i.ativo = true;
      e.currentTarget.setPointerCapture(e.pointerId);
      setArrastando(true);
    }
    setDx(limitar(delta, e.currentTarget.offsetWidth));
  }

  function aoSoltar(e: React.PointerEvent<HTMLDivElement>) {
    const i = inicio.current;
    if (!i || i.id !== e.pointerId) return;
    inicio.current = null;
    const passou = i.ativo && Math.abs(dx) >= e.currentTarget.offsetWidth * LIMIAR;
    const esquerda = dx < 0;
    setArrastando(false);
    setDx(0);
    if (!passou || e.type === 'pointercancel') return;
    if (esquerda) onAprovar?.();
    else if (onExcluir) setConfirmando(true);
  }

  function aoTeclar(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.target !== e.currentTarget || ocupado) return;
    if (e.key === 'ArrowLeft' && onAprovar) {
      e.preventDefault();
      onAprovar();
    } else if (e.key === 'ArrowRight' && onExcluir) {
      e.preventDefault();
      setConfirmando(true);
    }
  }

  const habilitado = Boolean(onAprovar || onExcluir);
  if (!habilitado) return <li className={cn('flex flex-col gap-2 p-3', className)}>{children}</li>;

  const dica = [
    onAprovar ? 'seta para a esquerda aprova' : '',
    onExcluir ? 'seta para a direita exclui' : '',
  ]
    .filter(Boolean)
    .join('; ');

  return (
    <li className={cn('relative overflow-hidden rounded-padrao', className)}>
      <div
        aria-hidden="true"
        className={cn(
          'absolute inset-0 flex items-center px-4 text-base font-semibold text-white',
          dx < 0 ? 'justify-end bg-primaria' : 'justify-start bg-erro',
          dx === 0 && 'invisible',
        )}
      >
        {dx < 0 ? 'Aprovar' : 'Excluir'}
      </div>
      <div
        ref={caixa}
        tabIndex={0}
        aria-describedby={dicaId}
        onPointerDown={aoPressionar}
        onPointerMove={aoMover}
        onPointerUp={aoSoltar}
        onPointerCancel={aoSoltar}
        onKeyDown={aoTeclar}
        style={{ transform: `translateX(${dx}px)`, touchAction: 'pan-y' }}
        className={cn(
          'relative flex flex-col gap-2 bg-white p-3',
          arrastando
            ? 'cursor-grabbing select-none'
            : 'cursor-grab motion-safe:transition-transform',
        )}
      >
        {children}
        <p id={dicaId} className="text-xs text-texto-2">
          Arraste ou use as setas
          <span className="sr-only">: {dica}</span>
        </p>
        {confirmando ? (
          <div
            role="alertdialog"
            aria-label="Confirmar exclusão"
            className="flex flex-wrap items-center gap-2"
            onKeyDown={(e) => {
              if (e.key === 'Escape') setConfirmando(false);
            }}
          >
            <span className="text-base font-medium">Excluir definitivamente?</span>
            <Button
              type="button"
              variante="perigo"
              autoFocus
              disabled={ocupado}
              onClick={() => {
                setConfirmando(false);
                onExcluir?.();
              }}
            >
              Confirmar
            </Button>
            <Button
              type="button"
              variante="contorno"
              disabled={ocupado}
              onClick={() => setConfirmando(false)}
            >
              Cancelar
            </Button>
          </div>
        ) : null}
      </div>
    </li>
  );
}
