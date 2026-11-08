'use client';

import * as React from 'react';
import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { useCarga } from '@/lib/use-carga';
import { useAcao } from '@/lib/use-acao';
import { AppShell } from '@/components/app-shell';
import { DadosViagem } from '@/components/cartao-viagem';
import { BotoesAcao } from '@/components/cartao-requisicao';
import { LinhaDoTempo } from '@/components/linha-do-tempo';
import { Alerta } from '@/components/ui/alert';
import { StatusChip } from '@/components/ui/status-chip';

function Detalhe({ id }: { id: string }) {
  const viagem = useCarga(() => api.viagemMotorista(id));
  const acao = useAcao(async () => {
    await viagem.recarregar();
  });

  if (viagem.carregando) {
    return (
      <p role="status" className="text-texto-2">
        Carregando...
      </p>
    );
  }
  const v = viagem.dados;
  if (!v) return <Alerta>{viagem.erro ?? 'Viagem não encontrada.'}</Alerta>;

  return (
    <>
      {viagem.erro ? <Alerta className="mb-4">{viagem.erro}</Alerta> : null}
      {acao.erro ? <Alerta className="mb-4">{acao.erro}</Alerta> : null}

      <section aria-label="Resumo" className="rounded-xl border border-borda bg-white p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="font-medium text-texto-2">{v.veiculo_descricao ?? 'Veículo'}</p>
          <StatusChip status={v.status} />
        </div>
        <p className="mt-2 text-xl font-semibold break-words">{v.trajeto}</p>
        <p className="mt-2 text-texto-2">
          Solicitante: {v.nome_solicitante} · {v.unidade}
        </p>
        <DadosViagem viagem={v} />
        {v.observacao ? <p className="mt-2 break-words">Obs.: {v.observacao}</p> : null}
      </section>

      <h2 className="mt-6 mb-2 font-medium text-texto-2">Acompanhamento da viagem</h2>
      <section
        aria-label="Acompanhamento da viagem"
        className="rounded-xl border border-borda bg-white p-4"
      >
        <LinhaDoTempo linhaDoTempo={v.linha_do_tempo} variante="completa" />
      </section>

      <BotoesAcao
        req={v}
        ocupado={acao.ocupado}
        onAcao={(rq, a, c) => void acao.executar(rq, a, c)}
        aoRegistrarChecklist={viagem.recarregar}
      />
    </>
  );
}

function PorId() {
  const id = useSearchParams().get('id');
  return id ? <Detalhe id={id} /> : <Alerta>Viagem não informada.</Alerta>;
}

export default function DetalheViagem() {
  return (
    <AppShell titulo="Viagem" voltarPara="/motorista">
      <Suspense fallback={null}>
        <PorId />
      </Suspense>
    </AppShell>
  );
}
