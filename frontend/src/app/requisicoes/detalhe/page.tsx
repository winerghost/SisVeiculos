'use client';

import * as React from 'react';
import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { dataHora, numero } from '@/lib/formato';
import { useCarga } from '@/lib/use-carga';
import { useAcao } from '@/lib/use-acao';
import { AppShell } from '@/components/app-shell';
import { Avatar } from '@/components/avatar';
import { LinhaDoTempo } from '@/components/linha-do-tempo';
import { BotoesAcao, versaoChecklist } from '@/components/cartao-requisicao';
import { LocalizacaoRegistroSecao } from '@/components/localizacao-registro';
import { ChecklistRegistrado } from '@/components/checklist-registrado';
import { Alerta } from '@/components/ui/alert';
import { HoraSaida } from '@/components/ui/hora-saida';
import { StatusChip } from '@/components/ui/status-chip';

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
        <p className="mt-2 flex items-center gap-2 text-texto-2">
          <Avatar nome={r.nome_solicitante} fotoUrl={r.solicitante_foto_url} decorativo />
          <span className="min-w-0 break-words">
            Solicitante: {r.nome_solicitante} · {r.unidade}
          </span>
        </p>
        {r.motorista ? <p className="mt-1 text-texto-2">Motorista: {r.motorista.nome}</p> : null}
        <p className="mt-1 text-texto-2">
          Saída prevista: <HoraSaida texto={dataHora(r.hora_saida)} situacao={r.situacao_horario} />
        </p>
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

      <h2 className="mt-6 mb-2 font-medium text-texto-2">Linha do tempo</h2>
      <section aria-label="Linha do tempo" className="rounded-xl border border-borda bg-white p-4">
        <LinhaDoTempo linhaDoTempo={r.linha_do_tempo} variante="completa" />
      </section>

      {r.acoes_permitidas.includes('ver_checklist') ? (
        <ChecklistRegistrado requisicaoId={r.id} versao={versaoChecklist(r)} />
      ) : null}

      {r.acoes_permitidas.includes('ver_localizacao') ? (
        <LocalizacaoRegistroSecao requisicaoId={r.id} versao={versaoChecklist(r)} />
      ) : null}

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
