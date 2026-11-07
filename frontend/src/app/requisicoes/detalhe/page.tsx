'use client';

import * as React from 'react';
import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { Check } from 'lucide-react';
import { api } from '@/lib/api';
import { dataHora, numero } from '@/lib/formato';
import { useCarga } from '@/lib/use-carga';
import { useAcao } from '@/lib/use-acao';
import type { Requisicao } from '@/lib/tipos';
import { AppShell } from '@/components/app-shell';
import { BotoesAcao, versaoChecklist } from '@/components/cartao-requisicao';
import { ChecklistRegistrado } from '@/components/checklist-registrado';
import { Alerta } from '@/components/ui/alert';
import { StatusChip } from '@/components/ui/status-chip';

// Fallback para respostas sem `por`: nomes antigos por rótulo do histórico. A API é a fonte.
function porFallback(r: Requisicao, rotulo: string): string | null {
  const mapa: Record<string, string | null> = {
    Autorizada: r.autorizado_por_nome,
    Negada: r.autorizado_por_nome,
    'Saída registrada': r.saida_registrada_por_nome,
    'Chegada registrada': r.chegada_registrada_por_nome,
  };
  return mapa[rotulo] ?? null;
}

function Detalhe({ id }: { id: string }) {
  const req = useCarga(() => api.requisicao(id));
  const acao = useAcao(async () => {
    await req.recarregar();
  });

  if (req.carregando) {
    return (
      <p role="status" className="text-texto-2">
        Carregando...
      </p>
    );
  }
  const r = req.dados;
  if (!r) return <Alerta>{req.erro ?? 'Requisição não encontrada.'}</Alerta>;

  return (
    <>
      {req.erro ? <Alerta className="mb-4">{req.erro}</Alerta> : null}
      {acao.erro ? <Alerta className="mb-4">{acao.erro}</Alerta> : null}

      <section aria-label="Resumo" className="rounded-xl border border-borda bg-white p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="font-medium text-texto-2">{r.veiculo_descricao ?? 'Veículo'}</p>
          <StatusChip status={r.status} />
        </div>
        <p className="mt-2 text-xl font-semibold break-words">{r.trajeto}</p>
        <p className="mt-2 text-texto-2">
          Solicitante: {r.nome_solicitante} · {r.unidade}
        </p>
        <p className="mt-1 text-texto-2">Saída prevista: {dataHora(r.hora_saida)}</p>
        {r.saida_real_em ? (
          <p className="mt-1 text-texto-2">Saída registrada: {dataHora(r.saida_real_em)}</p>
        ) : null}
        {r.chegada_real_em ? (
          <p className="mt-1 text-texto-2">Chegada registrada: {dataHora(r.chegada_real_em)}</p>
        ) : null}
        {r.km_saida !== null && r.km_saida !== undefined ? (
          <p className="mt-1 text-texto-2">Km de saída: {numero(r.km_saida)}</p>
        ) : null}
        {r.km_chegada !== null && r.km_chegada !== undefined ? (
          <p className="mt-1 text-texto-2">Km de chegada: {numero(r.km_chegada)}</p>
        ) : null}
        {r.observacao ? <p className="mt-2 break-words">Obs.: {r.observacao}</p> : null}
      </section>

      <h2 className="mt-6 mb-2 font-medium text-texto-2">Histórico</h2>
      <ol className="flex flex-col gap-4 rounded-xl border border-borda bg-white p-4">
        {r.historico.map((e) => {
          const por = e.por ?? porFallback(r, e.rotulo);
          return (
            <li key={e.rotulo} className="flex items-start gap-3">
              <span
                aria-hidden
                className="flex size-9 shrink-0 items-center justify-center rounded-full bg-sucesso text-white"
              >
                <Check className="size-5" />
              </span>
              <div>
                <p className="font-semibold">{e.rotulo}</p>
                <p className="text-texto-2">
                  {dataHora(e.quando)}
                  {por ? ` · por ${por}` : ''}
                </p>
                {e.km !== undefined && e.km !== null ? (
                  <p className="text-texto-2">Km: {numero(e.km)}</p>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>

      <ChecklistRegistrado requisicaoId={r.id} versao={versaoChecklist(r)} />

      <BotoesAcao
        req={r}
        ocupado={acao.ocupado}
        onAcao={(rq, a, c) => void acao.executar(rq, a, c)}
        aoRegistrarChecklist={req.recarregar}
      />
    </>
  );
}

function PorId() {
  const id = useSearchParams().get('id');
  return id ? <Detalhe id={id} /> : <Alerta>Requisição não informada.</Alerta>;
}

export default function DetalheRequisicao() {
  return (
    <AppShell titulo="Requisição" voltarPara="/requisicoes">
      <Suspense fallback={null}>
        <PorId />
      </Suspense>
    </AppShell>
  );
}
