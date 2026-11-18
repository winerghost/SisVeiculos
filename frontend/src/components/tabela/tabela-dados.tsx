'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { ArrowDown, ArrowUp, ChevronsUpDown } from 'lucide-react';
import {
  createColumnHelper,
  createPaginatedRowModel,
  createSortedRowModel,
  rowPaginationFeature,
  rowSortingFeature,
  sortFn_alphanumeric,
  sortFn_basic,
  sortFn_datetime,
  sortFn_text,
  tableFeatures,
  useTable,
  type ColumnDef,
  type PaginationState,
  type RowData,
  type SortingState,
} from '@tanstack/react-table';
import { cn } from '@/lib/utils';
import { Paginacao } from './paginacao';

/**
 * Motor headless (TanStack Table v9): só ordenação e paginação. Filtro e busca ficam na API
 * (modo manual) ou na página (modo cliente); nada de regra de negócio aqui.
 */
export const recursosTabela = tableFeatures({
  rowSortingFeature,
  rowPaginationFeature,
  sortedRowModel: createSortedRowModel(),
  paginatedRowModel: createPaginatedRowModel(),
  sortFns: {
    alphanumeric: sortFn_alphanumeric,
    text: sortFn_text,
    datetime: sortFn_datetime,
    basic: sortFn_basic,
  },
});
export type RecursosTabela = typeof recursosTabela;
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- colunas com tipos de valor distintos
export type ColunaTabela<T extends RowData> = ColumnDef<RecursosTabela, T, any>;
export const ajudanteColunas = <T extends RowData>() => createColumnHelper<RecursosTabela, T>();
export type { PaginationState, SortingState };

// Clique em controles internos da linha não abre o detalhe.
const INTERATIVO = 'a,button,input,select,textarea,label,[role="button"],[role="dialog"]';

export function TabelaDados<T extends RowData>({
  legenda,
  legendaVisivel = false,
  colunas,
  dados,
  idDaLinha,
  ordenacao,
  aoOrdenar,
  paginacao,
  aoPaginar,
  manual = false,
  ordenacaoManual = manual,
  total,
  hrefDaLinha,
  linhaExtra,
  ocupado,
  aoMudarTamanho,
}: {
  /** `<caption>` da tabela (leitor de tela); visível só com `legendaVisivel`. */
  legenda: string;
  legendaVisivel?: boolean;
  colunas: ColunaTabela<T>[];
  dados: readonly T[];
  idDaLinha: (item: T) => string;
  ordenacao: SortingState;
  aoOrdenar: (s: SortingState) => void;
  paginacao: PaginationState;
  aoPaginar: (p: PaginationState) => void;
  /** true: ordenação e paginação feitas pela API; `dados` já é a página e `total` vem da API. */
  manual?: boolean;
  /** Ordenação já aplicada pela página (paginação segue no cliente). Padrão: igual a `manual`. */
  ordenacaoManual?: boolean;
  total?: number;
  /** A linha inteira leva a este endereço (a célula principal deve ter o link para o teclado). */
  hrefDaLinha?: (item: T) => string;
  /** Conteúdo exibido numa linha logo abaixo (ex.: ações expandidas); null = nada. */
  linhaExtra?: (item: T) => React.ReactNode;
  ocupado?: boolean;
  aoMudarTamanho?: (tamanho: number) => void;
}) {
  const router = useRouter();
  const tabela = useTable({
    features: recursosTabela,
    columns: colunas,
    data: dados,
    getRowId: (item: T) => idDaLinha(item),
    state: { sorting: ordenacao, pagination: paginacao },
    onSortingChange: (u) => aoOrdenar(typeof u === 'function' ? u(ordenacao) : u),
    onPaginationChange: (u) => aoPaginar(typeof u === 'function' ? u(paginacao) : u),
    manualSorting: ordenacaoManual,
    enableSortingRemoval: false,
    manualPagination: manual,
    rowCount: manual ? total : undefined,
    enableMultiSort: false,
    autoResetPageIndex: false,
  });
  const linhas = tabela.getRowModel().rows;
  const totalLinhas = manual ? (total ?? dados.length) : dados.length;

  return (
    <div>
      <div
        role="region"
        aria-label={legenda}
        tabIndex={0}
        className="overflow-x-auto rounded-xl border border-borda bg-white"
      >
        <table
          className="w-full border-collapse text-left text-sm"
          aria-busy={ocupado || undefined}
        >
          <caption
            className={cn(
              legendaVisivel ? 'px-4 pt-3 text-left font-medium text-texto-2' : 'sr-only',
            )}
          >
            {legenda}
          </caption>
          <thead className="bg-fundo">
            {tabela.getHeaderGroups().map((grupo) => (
              <tr key={grupo.id}>
                {grupo.headers.map((h) => {
                  const coluna = h.column;
                  const podeOrdenar = coluna.getCanSort();
                  const sentido = coluna.getIsSorted();
                  const conteudo = h.isPlaceholder ? null : <tabela.FlexRender header={h} />;
                  return (
                    <th
                      key={h.id}
                      scope="col"
                      aria-sort={
                        !podeOrdenar
                          ? undefined
                          : sentido === 'asc'
                            ? 'ascending'
                            : sentido === 'desc'
                              ? 'descending'
                              : 'none'
                      }
                      className="border-b border-borda px-3 py-1 font-semibold whitespace-nowrap text-texto"
                    >
                      {podeOrdenar ? (
                        <button
                          type="button"
                          disabled={ocupado}
                          onClick={coluna.getToggleSortingHandler()}
                          className="-mx-2 inline-flex min-h-11 items-center gap-1 rounded-padrao px-2 hover:bg-white"
                        >
                          {conteudo}
                          {sentido === 'asc' ? (
                            <ArrowUp aria-hidden className="size-4" />
                          ) : sentido === 'desc' ? (
                            <ArrowDown aria-hidden className="size-4" />
                          ) : (
                            <ChevronsUpDown aria-hidden className="size-4 text-texto-2" />
                          )}
                        </button>
                      ) : (
                        <span className="inline-flex min-h-11 items-center">{conteudo}</span>
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody className="lista-animada">
            {linhas.map((linha) => {
              const href = hrefDaLinha?.(linha.original);
              const extra = linhaExtra?.(linha.original);
              return (
                <React.Fragment key={linha.id}>
                  <tr
                    onClick={
                      href
                        ? (e) => {
                            const alvo = e.target as HTMLElement;
                            if (alvo.closest(INTERATIVO)) return;
                            router.push(href);
                          }
                        : undefined
                    }
                    className={cn(
                      'border-b border-borda align-middle last:border-b-0',
                      href && 'cursor-pointer hover:bg-fundo',
                    )}
                  >
                    {linha.getAllCells().map((celula) => (
                      <td key={celula.id} className="px-3 py-2">
                        <tabela.FlexRender cell={celula} />
                      </td>
                    ))}
                  </tr>
                  {extra ? (
                    <tr className="border-b border-borda bg-fundo/50">
                      <td colSpan={linha.getAllCells().length} className="px-3 pb-3">
                        {extra}
                      </td>
                    </tr>
                  ) : null}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <Paginacao
        pagina={paginacao.pageIndex}
        tamanho={paginacao.pageSize}
        total={totalLinhas}
        ocupado={ocupado}
        aoMudarPagina={(p) => tabela.setPageIndex(p)}
        aoMudarTamanho={aoMudarTamanho}
      />
    </div>
  );
}
