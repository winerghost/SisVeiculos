'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

const LIMIAR_PADRAO = 0.4;
const INICIO_PX = 6;
// Toques que começam na borda da tela costumam ser o "voltar" do sistema (iOS/Android/PWA).
const BORDA_PX = 16;

export type CorGesto = 'primaria' | 'erro' | 'neutra';

const COR_FUNDO: Record<CorGesto, string> = {
  primaria: 'bg-primaria',
  erro: 'bg-erro',
  // Cinza-azulado escuro (texto do chip "concluída"): contraste alto com o texto branco.
  neutra: 'bg-[#3b4658]',
};

/** O que acontece quando o item é arrastado para um dos lados. */
export interface PapelGesto {
  /** Texto do fundo revelado (não depende só de cor, WCAG 1.4.1). */
  rotulo: string;
  cor: CorGesto;
  /** Chamado ao passar do limiar (ou, com `confirmar`, ao confirmar). */
  onAcionar: () => void;
  /** Com isto, o gesto só pede a confirmação inline; a ação roda no botão de confirmar. */
  confirmar?: { pergunta: string; rotulo: string; rotuloAria: string };
}

/** Entregue ao conteúdo quando `children` é função: botões próprios podem acionar os gestos. */
export interface ControleGesto {
  /** Aciona o papel do lado (com `confirmar`, abre a confirmação inline). */
  acionar: (lado: 'direita' | 'esquerda') => void;
  /** Confirmação de ação destrutiva aberta: o conteúdo pode esconder suas outras ações. */
  confirmando: boolean;
}

interface Props {
  children: React.ReactNode | ((controle: ControleGesto) => React.ReactNode);
  /** Arrastar para a direita (ou ArrowRight, se `focavel`). Ausente: gesto desabilitado. */
  direita?: PapelGesto;
  /** Arrastar para a esquerda (ou ArrowLeft, se `focavel`). Ausente: gesto desabilitado. */
  esquerda?: PapelGesto;
  ocupado?: boolean;
  /** Classes do `<li>` (borda, raio, fundo). */
  className?: string;
  /** Classes da caixa interna que desliza (espaçamento). */
  classeInterna?: string;
  /**
   * Torna o contêiner uma parada de foco com atalho de setas e dica. Fora disso (padrão) não há
   * parada de foco extra: a alternativa ao gesto são os botões do próprio conteúdo (WCAG 2.5.7).
   */
  focavel?: boolean;
  /** Fração da largura que o arrasto precisa percorrer. */
  limiar?: number;
}

/**
 * Item de lista com gesto de arrastar (toque e mouse). Os papéis de cada lado são configurados
 * pelo pai, que só os passa conforme `acoes_permitidas` da API. O gesto também funciona sobre
 * links; depois de um arrasto o clique que o navegador gera em seguida é descartado para não
 * navegar. Respeita `prefers-reduced-motion` (a regra global zera a transição de retorno).
 */
export function ItemArrastavel({
  children,
  direita,
  esquerda,
  ocupado,
  className,
  classeInterna,
  focavel = false,
  limiar = LIMIAR_PADRAO,
}: Props) {
  const dicaId = React.useId();
  const inicio = React.useRef<{ x: number; id: number; ativo: boolean } | null>(null);
  const suprimirClique = React.useRef(false);
  const [dx, setDx] = React.useState(0);
  const [arrastando, setArrastando] = React.useState(false);
  const [confirmando, setConfirmando] = React.useState<PapelGesto | null>(null);

  function limitar(v: number, largura: number) {
    const max = direita ? largura : 0;
    const min = esquerda ? -largura : 0;
    return Math.max(min, Math.min(max, v));
  }

  function acionar(papel: PapelGesto) {
    if (papel.confirmar) setConfirmando(papel);
    else papel.onAcionar();
  }

  function aoPressionar(e: React.PointerEvent<HTMLDivElement>) {
    if (ocupado || confirmando) return;
    // Links são permitidos (o cartão é quase todo um link); controles de formulário não.
    if ((e.target as HTMLElement).closest('button, input, select, textarea, label')) return;
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    if (
      e.pointerType !== 'mouse' &&
      (e.clientX < BORDA_PX || e.clientX > window.innerWidth - BORDA_PX)
    )
      return;
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
    if (i.ativo) {
      // O navegador ainda dispara `click` depois do arrasto; descarta-o (e limpa a flag logo).
      suprimirClique.current = true;
      window.setTimeout(() => {
        suprimirClique.current = false;
      }, 150);
    }
    const passou = i.ativo && Math.abs(dx) >= e.currentTarget.offsetWidth * limiar;
    const papel = dx < 0 ? esquerda : direita;
    setArrastando(false);
    setDx(0);
    if (!passou || e.type === 'pointercancel' || !papel) return;
    acionar(papel);
  }

  function aoClicarCaptura(e: React.MouseEvent<HTMLDivElement>) {
    if (!suprimirClique.current) return;
    suprimirClique.current = false;
    e.preventDefault();
    e.stopPropagation();
  }

  function aoTeclar(e: React.KeyboardEvent<HTMLDivElement>) {
    if (!focavel || e.target !== e.currentTarget || ocupado) return;
    if (e.key === 'ArrowLeft' && esquerda) {
      e.preventDefault();
      acionar(esquerda);
    } else if (e.key === 'ArrowRight' && direita) {
      e.preventDefault();
      acionar(direita);
    }
  }

  const controle: ControleGesto = {
    acionar: (lado) => {
      const papel = lado === 'direita' ? direita : esquerda;
      if (papel && !ocupado) acionar(papel);
    },
    confirmando: confirmando !== null,
  };
  const conteudo = typeof children === 'function' ? children(controle) : children;

  if (!direita && !esquerda) {
    return (
      <li className={cn('rounded-padrao', className)}>
        <div className={cn('flex flex-col gap-2 p-3', classeInterna)}>{conteudo}</div>
      </li>
    );
  }

  const dica = [
    esquerda ? `seta para a esquerda: ${esquerda.rotulo}` : '',
    direita ? `seta para a direita: ${direita.rotulo}` : '',
  ]
    .filter(Boolean)
    .join('; ');
  const lado = dx < 0 ? esquerda : dx > 0 ? direita : undefined;

  return (
    <li className={cn('relative overflow-hidden rounded-padrao', className)}>
      <div
        aria-hidden="true"
        className={cn(
          'absolute inset-0 flex items-center px-4 text-base font-semibold text-white',
          dx < 0 ? 'justify-end' : 'justify-start',
          lado ? COR_FUNDO[lado.cor] : 'bg-primaria',
          dx === 0 && 'invisible',
        )}
      >
        {lado?.rotulo}
      </div>
      <div
        {...(focavel ? { tabIndex: 0, 'aria-describedby': dicaId } : {})}
        onPointerDown={aoPressionar}
        onPointerMove={aoMover}
        onPointerUp={aoSoltar}
        onPointerCancel={aoSoltar}
        onKeyDown={aoTeclar}
        onClickCapture={aoClicarCaptura}
        onDragStart={(e) => e.preventDefault()}
        style={{ transform: `translateX(${dx}px)`, touchAction: 'pan-y' }}
        className={cn(
          'relative flex flex-col gap-2 bg-white p-3',
          arrastando
            ? 'cursor-grabbing select-none'
            : 'cursor-grab motion-safe:transition-transform',
          classeInterna,
        )}
      >
        {conteudo}
        {focavel && !confirmando ? (
          <p id={dicaId} className="text-xs text-texto-2">
            Arraste ou use as setas
            <span className="sr-only">: {dica}</span>
          </p>
        ) : null}
        {confirmando?.confirmar ? (
          <div
            role="alertdialog"
            aria-label={confirmando.confirmar.rotuloAria}
            className="flex flex-wrap items-center gap-2"
            onKeyDown={(e) => {
              if (e.key === 'Escape') setConfirmando(null);
            }}
          >
            <span className="text-base font-medium">{confirmando.confirmar.pergunta}</span>
            <Button
              type="button"
              variante="perigo"
              autoFocus
              disabled={ocupado}
              onClick={() => {
                const papel = confirmando;
                setConfirmando(null);
                papel.onAcionar();
              }}
            >
              {confirmando.confirmar.rotulo}
            </Button>
            <Button
              type="button"
              variante="contorno"
              disabled={ocupado}
              onClick={() => setConfirmando(null)}
            >
              Cancelar
            </Button>
          </div>
        ) : null}
      </div>
    </li>
  );
}
