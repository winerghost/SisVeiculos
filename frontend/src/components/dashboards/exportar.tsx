'use client';

import Link from 'next/link';
import { FileDown } from 'lucide-react';
import { useAuth } from '@/lib/auth';
import { Button } from '@/components/ui/button';

const FILTROS_PAINEL = ['inicio', 'fim', 'unidade', 'veiculo'];

/**
 * "Exportar este painel": só aparece se o catálogo de relatórios mapear este painel. O relatório
 * e os parâmetros vêm do servidor (exportacao_paineis); os filtros efetivos do painel seguem
 * como pré-preenchimento e o servidor valida tudo na tela de relatórios.
 */
export function ExportarPainel({
  painel,
  efetivos,
}: {
  painel: string;
  efetivos?: Record<string, string | null | undefined>;
}) {
  const { relatorios } = useAuth();
  const destino = relatorios?.exportacao_paineis[painel];
  if (!destino) return null;

  const q = new URLSearchParams({ tipo: destino.tipo });
  for (const nome of FILTROS_PAINEL) {
    const v = efetivos?.[nome];
    if (v) q.set(nome, v);
  }
  for (const [nome, v] of Object.entries(destino.parametros)) {
    if (v !== null && v !== undefined) q.set(nome, String(v));
  }

  return (
    <Button asChild variante="contorno" className="mb-4">
      <Link href={`/relatorios?${q.toString()}`}>
        <FileDown aria-hidden className="size-5" /> Exportar este painel
      </Link>
    </Button>
  );
}
