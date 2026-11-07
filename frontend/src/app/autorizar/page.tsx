'use client';

import * as React from 'react';
import { api } from '@/lib/api';
import { useCarga } from '@/lib/use-carga';
import { useAcao } from '@/lib/use-acao';
import type { ListaRequisicoes, Status } from '@/lib/tipos';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { AppShell } from '@/components/app-shell';
import { CartaoRequisicao } from '@/components/cartao-requisicao';
import { Alerta } from '@/components/ui/alert';

const ABAS: { status: Status; rotulo: string }[] = [
  { status: 'solicitada', rotulo: 'Pendentes' },
  { status: 'autorizada', rotulo: 'Autorizadas' },
  { status: 'em_uso', rotulo: 'Em uso' },
];

function Painel() {
  const [aba, setAba] = React.useState<Status>('solicitada');
  const listas = useCarga(async () => {
    const [a, b, c] = await Promise.all(ABAS.map((x) => api.requisicoes(x.status)));
    return { solicitada: a, autorizada: b, em_uso: c } as Record<string, ListaRequisicoes>;
  });
  const acao = useAcao(async () => {
    await listas.recarregar();
  });
  const itens = listas.dados?.[aba]?.itens ?? [];

  return (
    <>
      <div role="tablist" aria-label="Situação" className="mb-4 flex rounded-xl bg-[#e8ecf2] p-1">
        {ABAS.map((a) => (
          <button
            key={a.status}
            type="button"
            role="tab"
            id={`aba-${a.status}`}
            aria-selected={aba === a.status}
            aria-controls="painel-lista"
            onClick={() => setAba(a.status)}
            className={cn(
              'min-h-11 flex-1 rounded-padrao px-1 text-sm font-medium',
              aba === a.status ? 'bg-white font-semibold text-texto' : 'text-texto-2',
            )}
          >
            {a.rotulo} · {listas.dados?.[a.status]?.total ?? 0}
          </button>
        ))}
      </div>

      {listas.erro ? <Alerta className="mb-4">{listas.erro}</Alerta> : null}
      {acao.erro ? <Alerta className="mb-4">{acao.erro}</Alerta> : null}

      <div id="painel-lista" role="tabpanel" aria-labelledby={`aba-${aba}`}>
        {listas.carregando ? (
          <p role="status" className="text-texto-2">
            Carregando...
          </p>
        ) : itens.length === 0 ? (
          <p className="text-texto-2">Nada por aqui.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {itens.map((r) => (
              <CartaoRequisicao
                key={r.id}
                req={r}
                mostrarSolicitante
                ocupado={acao.ocupado}
                onAcao={(req, a, c) => void acao.executar(req, a, c)}
                aoRegistrarChecklist={listas.recarregar}
              />
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

export default function PainelAutorizador() {
  const { eu } = useAuth();
  return (
    <AppShell titulo="Painel do autorizador" sobretitulo={eu?.rotulo_perfil}>
      <Painel />
    </AppShell>
  );
}
