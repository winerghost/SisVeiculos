'use client';

import { cn } from '@/lib/utils';

/** Alterna entre a lista ativa e a das arquivadas (o filtro em si é da API: `arquivadas=true`). */
export function AbasArquivadas({
  arquivadas,
  aoMudar,
  rotuloAtivas,
  totalArquivadas,
}: {
  arquivadas: boolean;
  aoMudar: (arquivadas: boolean) => void;
  rotuloAtivas: string;
  /** Só informado com a aba Arquivadas aberta (vem do `total` da API). */
  totalArquivadas?: number;
}) {
  const aba = (ativa: boolean, rotulo: string) => (
    <button
      type="button"
      aria-pressed={arquivadas === ativa}
      onClick={() => aoMudar(ativa)}
      className={cn(
        'flex min-h-11 flex-1 items-center justify-center rounded-padrao px-3 text-base font-medium',
        arquivadas === ativa ? 'bg-white font-semibold text-texto' : 'text-texto-2',
      )}
    >
      {rotulo}
    </button>
  );
  return (
    <div role="group" aria-label="Exibir" className="mb-4 flex rounded-xl bg-[#e8ecf2] p-1">
      {aba(false, rotuloAtivas)}
      {aba(true, totalArquivadas === undefined ? 'Arquivadas' : `Arquivadas · ${totalArquivadas}`)}
    </div>
  );
}
