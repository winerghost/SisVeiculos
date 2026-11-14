'use client';

import * as React from 'react';
import { Sparkles } from 'lucide-react';
import type { ResumoGerado } from '@/lib/tipos';
import { useEnvio } from '@/lib/use-envio';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';

/**
 * Resumo narrativo sob demanda. Só aparece quando a API libera (`habilitado`) e só gera no clique.
 * O texto fica em estado (memória), nunca em storage, e é exibido como texto puro. Quem usa deve
 * passar `key` com os filtros em vigor: ao mudarem, o componente é recriado e o resumo some.
 */
export function ResumoIA({
  habilitado,
  gerar,
  aviso,
  bloqueado = false,
}: {
  habilitado: boolean;
  gerar: () => Promise<ResumoGerado>;
  /** Texto do selo (o que conferir). */
  aviso: string;
  /** Botão desabilitado (ex.: dados ainda não correspondem aos filtros escolhidos). */
  bloqueado?: boolean;
}) {
  const envio = useEnvio();
  const [resumo, setResumo] = React.useState<ResumoGerado | null>(null);

  if (!habilitado) return null;

  async function resumir() {
    if (envio.ocupado) return;
    setResumo(null);
    const r = await envio.executar(gerar);
    if (r.ok) setResumo(r.valor);
  }

  return (
    <section aria-label="Resumo por IA" className="my-4 flex flex-col gap-3">
      <Button
        type="button"
        variante="contorno"
        className="self-start"
        carregando={envio.ocupado}
        disabled={bloqueado}
        onClick={() => void resumir()}
      >
        {envio.ocupado ? null : <Sparkles aria-hidden className="size-5" />}
        {envio.ocupado ? 'Gerando resumo...' : 'Resumir com IA'}
      </Button>
      <div role="status" aria-live="polite">
        {envio.ocupado ? <span className="sr-only">Gerando resumo...</span> : null}
      </div>
      {envio.erro ? <Alerta>{envio.erro}</Alerta> : null}
      {resumo ? (
        <div className="rounded-xl border border-borda bg-white p-4">
          <p className="mb-2 inline-block rounded-padrao bg-[#e9e3fa] px-2 py-1 text-sm font-medium text-[#3f2a7a]">
            {aviso}
          </p>
          {resumo.escopo ? <p className="mb-2 text-sm text-texto-2">{resumo.escopo}</p> : null}
          <p className="text-base whitespace-pre-line">{resumo.texto}</p>
        </div>
      ) : null}
    </section>
  );
}
