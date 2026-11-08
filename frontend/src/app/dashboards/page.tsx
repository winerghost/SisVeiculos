'use client';

import * as React from 'react';
import { api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useCarga } from '@/lib/use-carga';
import type { PainelId } from '@/lib/tipos';
import { cn } from '@/lib/utils';
import { AppShell } from '@/components/app-shell';
import { PainelComFiltros, PainelOperacao } from '@/components/dashboards/paineis';
import { Alerta } from '@/components/ui/alert';

// Quais painéis existem e seus rótulos vêm da API; aqui só se sabe como desenhar cada chave.
const CHAVES: PainelId[] = [
  'operacao',
  'volume',
  'tempos',
  'unidades',
  'veiculos',
  'governanca',
  'opcionais',
];
const ehPainel = (p: string): p is PainelId => (CHAVES as string[]).includes(p);

function Conteudo() {
  const { eu } = useAuth();
  const lista = useCarga(() => api.paineisDashboard());
  // Só para exibir descrições e opções de filtro; uma falha aqui não bloqueia os painéis.
  const veiculos = useCarga(() => api.veiculos());
  const [escolhido, setEscolhido] = React.useState<PainelId | null>(null);

  const paineis = (lista.dados?.paineis ?? []).filter((p) => ehPainel(p.chave));
  const chaves = paineis.map((p) => p.chave);
  const atual = (escolhido && chaves.includes(escolhido) ? escolhido : chaves[0]) as
    PainelId | undefined;

  if (lista.carregando) {
    return (
      <p role="status" className="text-texto-2">
        Carregando...
      </p>
    );
  }
  if (lista.erro || !atual) {
    return <Alerta>{lista.erro ?? 'Nenhum painel disponível.'}</Alerta>;
  }

  return (
    <>
      <div
        role="tablist"
        aria-label="Painéis"
        className="-mx-5 mb-5 flex gap-2 overflow-x-auto px-5"
      >
        {paineis.map(({ chave: p, rotulo }) => (
          <button
            key={p}
            type="button"
            role="tab"
            id={`painel-aba-${p}`}
            aria-selected={atual === p}
            aria-controls="painel-conteudo"
            onClick={() => setEscolhido(p as PainelId)}
            className={cn(
              'flex min-h-11 items-center rounded-full border px-4 text-base font-medium whitespace-nowrap',
              atual === p
                ? 'border-primaria bg-primaria text-white'
                : 'border-borda-campo bg-white text-texto',
            )}
          >
            {rotulo}
          </button>
        ))}
      </div>
      <div id="painel-conteudo" role="tabpanel" aria-labelledby={`painel-aba-${atual}`}>
        {atual === 'operacao' ? (
          <PainelOperacao />
        ) : (
          <PainelComFiltros
            key={atual}
            painel={atual}
            unidades={eu?.unidades ?? []}
            veiculos={veiculos.dados ?? []}
          />
        )}
      </div>
    </>
  );
}

export default function Dashboards() {
  const { eu } = useAuth();
  return (
    <AppShell titulo="Dashboards" sobretitulo={eu?.rotulo_perfil}>
      <Conteudo />
    </AppShell>
  );
}
