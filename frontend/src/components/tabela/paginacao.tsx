'use client';

import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react';
import { numero } from '@/lib/formato';
import { cn } from '@/lib/utils';
import { Select } from '@/components/ui/input';

export const TAMANHOS_PAGINA = [10, 20, 50, 100] as const;

/**
 * Controles de página (alvos de 44 px). `pagina` começa em 0. O total vem da API (ou da lista
 * inteira, no modo cliente); a conta das páginas é só apresentação.
 */
export function Paginacao({
  pagina,
  tamanho,
  total,
  aoMudarPagina,
  aoMudarTamanho,
  ocupado,
  rotulo = 'Paginação',
  className,
}: {
  pagina: number;
  tamanho: number;
  total: number;
  aoMudarPagina: (pagina: number) => void;
  /** Sem isto, o seletor de itens por página não aparece. */
  aoMudarTamanho?: (tamanho: number) => void;
  ocupado?: boolean;
  rotulo?: string;
  className?: string;
}) {
  const paginas = Math.max(1, Math.ceil(total / Math.max(1, tamanho)));
  const atual = Math.min(pagina, paginas - 1);
  const de = total === 0 ? 0 : atual * tamanho + 1;
  const ate = Math.min(total, (atual + 1) * tamanho);
  const podeVoltar = atual > 0 && !ocupado;
  const podeAvancar = atual < paginas - 1 && !ocupado;
  if (total === 0) return null;

  const botao = (
    rotuloBotao: string,
    destino: number,
    habilitado: boolean,
    Icone: typeof ChevronLeft,
  ) => (
    <button
      type="button"
      aria-label={rotuloBotao}
      title={rotuloBotao}
      disabled={!habilitado}
      onClick={() => aoMudarPagina(destino)}
      className="inline-flex size-11 items-center justify-center rounded-padrao border border-borda-campo bg-white text-texto hover:bg-fundo disabled:pointer-events-none disabled:opacity-50"
    >
      <Icone aria-hidden className="size-5" />
    </button>
  );

  return (
    <nav aria-label={rotulo} className={cn('mt-4 flex flex-wrap items-center gap-3', className)}>
      <p className="min-w-0 flex-1 text-sm text-texto-2" aria-live="polite">
        {numero(de)}–{numero(ate)} de {numero(total)} · página {numero(atual + 1)} de{' '}
        {numero(paginas)}
      </p>
      {aoMudarTamanho ? (
        <label className="flex items-center gap-2 text-sm text-texto-2">
          Por página
          <Select
            value={tamanho}
            disabled={ocupado}
            onChange={(e) => aoMudarTamanho(Number(e.target.value))}
            className="h-11 w-20"
          >
            {TAMANHOS_PAGINA.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </Select>
        </label>
      ) : null}
      <div className="flex items-center gap-2">
        {botao('Primeira página', 0, podeVoltar, ChevronsLeft)}
        {botao('Página anterior', atual - 1, podeVoltar, ChevronLeft)}
        {botao('Próxima página', atual + 1, podeAvancar, ChevronRight)}
        {botao('Última página', paginas - 1, podeAvancar, ChevronsRight)}
      </div>
    </nav>
  );
}
