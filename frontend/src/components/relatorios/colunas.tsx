'use client';

import * as React from 'react';
import { ArrowDown, ArrowUp, Columns3 } from 'lucide-react';
import { chavesMarcadas, itensIniciais, mover, type ItemColuna } from '@/lib/relatorios';
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

  const porChave = new Map(disponiveis.map((c) => [c.chave, c]));
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
            Marque as colunas e use as setas para mudar a ordem. O servidor valida a escolha.
          </DrawerDescription>
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
                    className="flex items-center gap-2 rounded-padrao border border-borda bg-white pr-1"
                  >
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
