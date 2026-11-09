'use client';

import * as React from 'react';
import { api } from '@/lib/api';
import { useCarga } from '@/lib/use-carga';
import { useAcao } from '@/lib/use-acao';
import type { Requisicao, Status } from '@/lib/tipos';
import { AppShell } from '@/components/app-shell';
import { CartaoViagem } from '@/components/cartao-viagem';
import { Alerta } from '@/components/ui/alert';

// Só agrupamento visual por status devolvido pela API; o que cada viagem permite vem de
// `acoes_permitidas`.
const GRUPOS: { chave: string; titulo: string; status: Status[]; vazio: string }[] = [
  {
    chave: 'andamento',
    titulo: 'Em andamento',
    status: ['em_uso'],
    vazio: 'Nenhuma viagem em andamento.',
  },
  {
    chave: 'proximas',
    titulo: 'Próximas',
    status: ['autorizada', 'solicitada'],
    vazio: 'Nenhuma viagem programada.',
  },
  {
    chave: 'concluidas',
    titulo: 'Concluídas',
    status: ['concluida', 'cancelada', 'negada'],
    vazio: 'Nenhuma viagem concluída.',
  },
];

function Viagens() {
  const lista = useCarga(() => api.viagensMotorista().then((r) => r.itens));
  const acao = useAcao(async () => {
    await lista.recarregar();
  });

  if (lista.carregando) {
    return (
      <p role="status" className="text-texto-2">
        Carregando...
      </p>
    );
  }
  const itens: Requisicao[] = lista.dados ?? [];

  return (
    <>
      {lista.erro ? <Alerta className="mb-4">{lista.erro}</Alerta> : null}
      {acao.erro ? <Alerta className="mb-4">{acao.erro}</Alerta> : null}
      {GRUPOS.map((g) => {
        const doGrupo = itens.filter((r) => g.status.includes(r.status));
        return (
          <section key={g.chave} aria-labelledby={`grupo-${g.chave}`} className="mb-6">
            <h2 id={`grupo-${g.chave}`} className="mb-2 font-medium text-texto-2">
              {g.titulo} · {doGrupo.length}
            </h2>
            {doGrupo.length === 0 ? (
              <p className="text-texto-2">{g.vazio}</p>
            ) : (
              <ul className="lista-animada flex flex-col gap-3">
                {doGrupo.map((v) => (
                  <CartaoViagem
                    key={v.id}
                    viagem={v}
                    ocupado={acao.ocupado}
                    onAcao={(req, a, c) => void acao.executar(req, a, c)}
                    aoRegistrarChecklist={lista.recarregar}
                  />
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </>
  );
}

export default function MinhasViagens() {
  return (
    <AppShell titulo="Minhas viagens" sobretitulo="Motorista">
      <Viagens />
    </AppShell>
  );
}
