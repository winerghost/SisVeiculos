'use client';

import * as React from 'react';
import { api } from '@/lib/api';
import { useCarga } from '@/lib/use-carga';
import { useAcao } from '@/lib/use-acao';
import { useOrganizar } from '@/lib/use-organizar';
import type { AcaoOrganizar, Requisicao, Status } from '@/lib/tipos';
import { AppShell } from '@/components/app-shell';
import { AbasArquivadas } from '@/components/abas-arquivadas';
import { AvisoOrganizarRegiao } from '@/components/aviso-organizar';
import { CartaoViagem } from '@/components/cartao-viagem';
import { Alerta } from '@/components/ui/alert';
import { Spinner } from '@/components/ui/spinner';

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
  const [arquivadas, setArquivadas] = React.useState(false);
  const lista = useCarga(() => api.viagensMotorista(arquivadas), String(arquivadas));
  const acao = useAcao(async () => {
    await lista.recarregar();
  });
  const organizar = useOrganizar(async () => {
    await lista.recarregar();
  }, true);
  const titulo = React.useRef<HTMLHeadingElement>(null);

  const mudarAba = (v: boolean) => {
    if (v === arquivadas) return;
    organizar.limpar();
    lista.setDados(null);
    setArquivadas(v);
  };
  const aoOrganizar = async (id: string, a: AcaoOrganizar, desfazer = true) => {
    await organizar.executar(id, a, desfazer);
    // O item sai da lista: o foco vai para o título, de onde o "Desfazer" é o próximo Tab.
    titulo.current?.focus();
  };

  const itens: Requisicao[] = lista.dados?.itens ?? [];
  const ocupado = acao.ocupado || organizar.ocupado;
  const cartao = (v: Requisicao) => (
    <CartaoViagem
      key={v.id}
      viagem={v}
      ocupado={acao.ocupado || organizar.processando.has(v.id)}
      acaoEmAndamento={acao.emAndamento?.id === v.id ? acao.emAndamento.acao : null}
      arquivando={organizar.processando.has(v.id)}
      onAcao={(req, a, c) => void acao.executar(req, a, c)}
      onOrganizar={(req, a) => void aoOrganizar(req.id, a)}
      aoRegistrarChecklist={lista.recarregar}
    />
  );

  return (
    <>
      <AbasArquivadas
        arquivadas={arquivadas}
        aoMudar={mudarAba}
        rotuloAtivas="Viagens"
        totalArquivadas={arquivadas ? lista.dados?.total : undefined}
      />
      {lista.erro ? <Alerta className="mb-4">{lista.erro}</Alerta> : null}
      {acao.erro ? <Alerta className="mb-4">{acao.erro}</Alerta> : null}
      <h2 ref={titulo} tabIndex={-1} className="sr-only">
        {arquivadas ? 'Viagens arquivadas' : 'Viagens'}
      </h2>
      <AvisoOrganizarRegiao
        aviso={organizar.aviso}
        erro={organizar.erro}
        ocupado={ocupado}
        onDesfazer={(id, a) => void aoOrganizar(id, a, false)}
      />
      {lista.dados === null && !lista.erro ? (
        <p role="status" className="flex items-center gap-2 text-texto-2">
          <Spinner />
          Carregando...
        </p>
      ) : arquivadas ? (
        itens.length === 0 ? (
          <p className="text-texto-2">Nenhuma viagem arquivada.</p>
        ) : (
          <ul className="lista-animada flex flex-col gap-3">{itens.map(cartao)}</ul>
        )
      ) : (
        GRUPOS.map((g) => {
          const doGrupo = itens.filter((r) => g.status.includes(r.status));
          return (
            <section key={g.chave} aria-labelledby={`grupo-${g.chave}`} className="mb-6">
              <h2 id={`grupo-${g.chave}`} className="mb-2 font-medium text-texto-2">
                {g.titulo} · {doGrupo.length}
              </h2>
              {doGrupo.length === 0 ? (
                <p className="text-texto-2">{g.vazio}</p>
              ) : (
                <ul className="lista-animada flex flex-col gap-3">{doGrupo.map(cartao)}</ul>
              )}
            </section>
          );
        })
      )}
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
