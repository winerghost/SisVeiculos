'use client';

import * as React from 'react';
import { api } from '@/lib/api';
import { useCarga } from '@/lib/use-carga';
import { useAcao } from '@/lib/use-acao';
import { AppShell } from '@/components/app-shell';
import { CartaoRequisicao } from '@/components/cartao-requisicao';
import { Alerta } from '@/components/ui/alert';

function Viagens() {
  const lista = useCarga(() => api.requisicoes('em_uso').then((r) => r.itens));
  const acao = useAcao(async () => {
    await lista.recarregar();
  });

  return (
    <>
      {lista.erro ? <Alerta className="mb-4">{lista.erro}</Alerta> : null}
      {acao.erro ? <Alerta className="mb-4">{acao.erro}</Alerta> : null}
      {lista.carregando ? (
        <p role="status" className="text-texto-2">
          Carregando...
        </p>
      ) : lista.dados && lista.dados.length === 0 ? (
        <p className="text-texto-2">Nenhuma viagem em uso.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {lista.dados?.map((r) => (
            <CartaoRequisicao
              key={r.id}
              req={r}
              mostrarSolicitante
              ocupado={acao.ocupado}
              onAcao={(req, a, c) => void acao.executar(req, a, c)}
              aoRegistrarChecklist={lista.recarregar}
              mostrarChecklist
            />
          ))}
        </ul>
      )}
    </>
  );
}

export default function Viagem() {
  return (
    <AppShell titulo="Viagem" sobretitulo="Veículo em uso">
      <Viagens />
    </AppShell>
  );
}
