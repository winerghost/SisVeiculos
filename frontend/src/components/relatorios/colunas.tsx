'use client';

import * as React from 'react';
import { ArrowDown, ArrowUp, Columns3, GripVertical } from 'lucide-react';
import {
  chavesMarcadas,
  itensIniciais,
  mover,
  trocaPorArraste,
  type ItemColuna,
} from '@/lib/relatorios';
import { cn } from '@/lib/utils';
import type { ColunaDisponivel, FiltroRelatorio } from '@/lib/tipos';
import { Button } from '@/components/ui/button';
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerTitle,
} from '@/components/ui/drawer';

/**
 * Seletor de colunas. `selecao === null` significa "padrão do servidor" (nada é enviado).
 * A lista, os rótulos e as colunas que exigem flag vêm do catálogo; o servidor valida (400).
 */
export function ColunasRelatorio({
  disponiveis,
  filtros,
  valores,
  selecao,
  onAplicar,
  onRestaurar,
}: {
  disponiveis: ColunaDisponivel[];
  filtros: FiltroRelatorio[];
  /** Valores efetivos dos filtros na tela (definem as colunas marcadas por padrão). */
  valores: Record<string, string>;
  selecao: string[] | null;
  onAplicar: (chaves: string[]) => void;
  onRestaurar: () => void;
}) {
  const [aberto, setAberto] = React.useState(false);
  const [itens, setItens] = React.useState<ItemColuna[]>([]);

  const [arrastada, setArrastada] = React.useState<{ chave: string; dy: number } | null>(null);
  const [aviso, setAviso] = React.useState('');
  const gesto = React.useRef<{
    chave: string;
    y: number;
    id: number;
    antes: ItemColuna[];
  } | null>(null);
  const linhas = React.useRef(new Map<string, HTMLLIElement>());
  const itensRef = React.useRef<ItemColuna[]>([]);
  React.useEffect(() => {
    itensRef.current = itens;
  }, [itens]);

  const porChave = new Map(disponiveis.map((c) => [c.chave, c]));
  const rotuloDe = (chave: string) => porChave.get(chave)?.rotulo ?? chave;

  /** Altura da linha vizinha mais o espaçamento (gap-2 = 8px); null se não houver vizinho. */
  function alturaVizinho(indice: number): number | null {
    const outra = itensRef.current[indice];
    const el = outra ? linhas.current.get(outra.chave) : undefined;
    return el ? el.offsetHeight + 8 : null;
  }

  function anunciar(lista: ItemColuna[], chave: string) {
    const pos = lista.findIndex((x) => x.chave === chave) + 1;
    setAviso(`${rotuloDe(chave)}, posição ${pos} de ${lista.length}`);
  }

  function iniciarArraste(e: React.PointerEvent<HTMLButtonElement>, chave: string) {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    gesto.current = { chave, y: e.clientY, id: e.pointerId, antes: itensRef.current };
    setArrastada({ chave, dy: 0 });
  }

  function moverArraste(e: React.PointerEvent<HTMLButtonElement>) {
    const g = gesto.current;
    if (!g || g.id !== e.pointerId) return;
    let dy = e.clientY - g.y;
    const i = itensRef.current.findIndex((x) => x.chave === g.chave);
    const delta = trocaPorArraste(
      dy,
      i > 0 ? alturaVizinho(i - 1) : null,
      i < itensRef.current.length - 1 ? alturaVizinho(i + 1) : null,
    );
    if (delta !== 0) {
      const h = alturaVizinho(i + delta) ?? 0;
      // A linha mudou de lugar no fluxo: compensa a origem para ela seguir o ponteiro.
      g.y += delta * h;
      dy -= delta * h;
      const nova = mover(itensRef.current, i, delta);
      itensRef.current = nova;
      setItens(nova);
      anunciar(nova, g.chave);
    }
    setArrastada({ chave: g.chave, dy });
  }

  function terminarArraste(e: React.PointerEvent<HTMLButtonElement>, cancelar: boolean) {
    const g = gesto.current;
    if (!g || g.id !== e.pointerId) return;
    gesto.current = null;
    if (cancelar) {
      itensRef.current = g.antes;
      setItens(g.antes);
      setAviso(`Reordenação cancelada. ${rotuloDe(g.chave)} voltou à posição anterior`);
    }
    setArrastada(null);
  }

  // Escape durante o arraste cancela só o arraste. Em alguns navegadores o clique do mouse não dá
  // foco à alça, então o ouvinte fica na janela (fase de captura, antes do fechamento do Drawer).
  const cancelarPorEscape = React.useEffectEvent((e: KeyboardEvent) => {
    const g = gesto.current;
    if (!g || e.key !== 'Escape') return;
    e.preventDefault();
    e.stopPropagation();
    gesto.current = null;
    itensRef.current = g.antes;
    setItens(g.antes);
    setArrastada(null);
    setAviso(`Reordenação cancelada. ${rotuloDe(g.chave)} voltou à posição anterior`);
  });
  const arrastando = arrastada !== null;
  React.useEffect(() => {
    if (!arrastando) return;
    const ouvir = (e: KeyboardEvent) => cancelarPorEscape(e);
    window.addEventListener('keydown', ouvir, true);
    return () => window.removeEventListener('keydown', ouvir, true);
  }, [arrastando]);

  function teclarAlca(e: React.KeyboardEvent<HTMLButtonElement>, chave: string) {
    if (gesto.current && e.key === 'Escape') {
      // Escape durante o arraste cancela e não fecha o Drawer.
      e.preventDefault();
      e.stopPropagation();
      const g = gesto.current;
      gesto.current = null;
      itensRef.current = g.antes;
      setItens(g.antes);
      setArrastada(null);
      setAviso(`Reordenação cancelada. ${rotuloDe(chave)} voltou à posição anterior`);
      return;
    }
    if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
    e.preventDefault();
    const i = itensRef.current.findIndex((x) => x.chave === chave);
    const nova = mover(itensRef.current, i, e.key === 'ArrowUp' ? -1 : 1);
    if (nova === itensRef.current) return;
    itensRef.current = nova;
    setItens(nova);
    anunciar(nova, chave);
  }
  const rotuloFlag = (nome: string) => filtros.find((f) => f.nome === nome)?.rotulo ?? nome;

  function abrir() {
    if (selecao) {
      // Marcadas na ordem escolhida, depois as demais na ordem do catálogo.
      const resto = disponiveis.filter((c) => !selecao.includes(c.chave)).map((c) => c.chave);
      setItens([
        ...selecao.map((chave) => ({ chave, marcada: true })),
        ...resto.map((chave) => ({ chave, marcada: false })),
      ]);
    } else {
      setItens(itensIniciais(disponiveis, valores));
    }
    setAberto(true);
  }

  return (
    <div>
      <Button type="button" variante="contorno" onClick={abrir}>
        <Columns3 aria-hidden className="size-5" /> Colunas
      </Button>
      <p className="mt-2 text-sm text-texto-2" aria-label="Colunas aplicadas">
        {selecao
          ? selecao.map((k) => porChave.get(k)?.rotulo ?? k).join(' · ')
          : 'Colunas padrão do relatório.'}
      </p>

      <Drawer open={aberto} onOpenChange={setAberto}>
        <DrawerContent>
          <DrawerTitle className="text-xl font-semibold">Colunas</DrawerTitle>
          <DrawerDescription className="mb-3 text-texto-2">
            Marque as colunas e arraste a alça (ou use as setas) para mudar a ordem. O servidor
            valida a escolha.
          </DrawerDescription>
          <p role="status" aria-live="polite" className="sr-only">
            {aviso}
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              onAplicar(chavesMarcadas(itens));
              setAberto(false);
            }}
            className="flex flex-col gap-3"
          >
            <ul className="lista-animada flex flex-col gap-2">
              {itens.map((item, i) => {
                const col = porChave.get(item.chave);
                if (!col) return null;
                const id = `rel-coluna-${col.chave}`;
                return (
                  <li
                    key={col.chave}
                    ref={(el) => {
                      if (el) linhas.current.set(col.chave, el);
                      else linhas.current.delete(col.chave);
                    }}
                    style={
                      arrastada?.chave === col.chave
                        ? { transform: `translateY(${arrastada.dy}px)` }
                        : undefined
                    }
                    className={cn(
                      'relative flex items-center gap-2 rounded-padrao border border-borda bg-white pr-1',
                      arrastada?.chave === col.chave &&
                        'z-10 border-primaria shadow-lg ring-2 ring-foco',
                    )}
                  >
                    <button
                      type="button"
                      data-testid={`alca-coluna-${col.chave}`}
                      aria-label={`Arrastar ${col.rotulo}`}
                      style={{ touchAction: 'none' }}
                      className={cn(
                        'flex size-11 min-h-11 shrink-0 select-none items-center justify-center rounded-padrao text-texto-2',
                        'focus-visible:outline-2 focus-visible:outline-foco',
                        arrastada?.chave === col.chave ? 'cursor-grabbing' : 'cursor-grab',
                      )}
                      onPointerDown={(e) => iniciarArraste(e, col.chave)}
                      onPointerMove={moverArraste}
                      onPointerUp={(e) => terminarArraste(e, false)}
                      onPointerCancel={(e) => terminarArraste(e, true)}
                      onKeyDown={(e) => teclarAlca(e, col.chave)}
                    >
                      <GripVertical aria-hidden className="size-5" />
                    </button>
                    <label
                      htmlFor={id}
                      className="flex min-h-11 flex-1 cursor-pointer items-center gap-3 px-3"
                    >
                      <input
                        id={id}
                        type="checkbox"
                        checked={item.marcada}
                        onChange={() =>
                          setItens((l) =>
                            l.map((x) =>
                              x.chave === item.chave ? { ...x, marcada: !x.marcada } : x,
                            ),
                          )
                        }
                        className="size-5 shrink-0 accent-primaria"
                      />
                      <span>
                        <span className="block text-base">{col.rotulo}</span>
                        {col.exige_flag ? (
                          <span className="block text-sm text-texto-2">
                            Exige: {rotuloFlag(col.exige_flag)}
                          </span>
                        ) : null}
                      </span>
                    </label>
                    <Button
                      type="button"
                      variante="contorno"
                      className="size-11 min-h-11 px-0"
                      disabled={i === 0}
                      aria-label={`Subir ${col.rotulo}`}
                      onClick={() => setItens((l) => mover(l, i, -1))}
                    >
                      <ArrowUp aria-hidden className="size-5" />
                    </Button>
                    <Button
                      type="button"
                      variante="contorno"
                      className="size-11 min-h-11 px-0"
                      disabled={i === itens.length - 1}
                      aria-label={`Descer ${col.rotulo}`}
                      onClick={() => setItens((l) => mover(l, i, 1))}
                    >
                      <ArrowDown aria-hidden className="size-5" />
                    </Button>
                  </li>
                );
              })}
            </ul>
            <div className="mt-2 grid grid-cols-2 gap-3">
              <Button
                type="button"
                variante="contorno"
                tamanho="cheio"
                onClick={() => {
                  onRestaurar();
                  setAberto(false);
                }}
              >
                Restaurar padrão
              </Button>
              <Button type="submit" tamanho="cheio">
                Aplicar
              </Button>
            </div>
            <DrawerClose asChild>
              <Button type="button" variante="link" className="self-center">
                Fechar
              </Button>
            </DrawerClose>
          </form>
        </DrawerContent>
      </Drawer>
    </div>
  );
}
