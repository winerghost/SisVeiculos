'use client';

import * as React from 'react';
import { api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useCarga } from '@/lib/use-carga';
import { useAcao } from '@/lib/use-acao';
import type { ListaRequisicoes, Status } from '@/lib/tipos';
import { cn } from '@/lib/utils';
import { AppShell } from '@/components/app-shell';
import { CartaoRequisicao } from '@/components/cartao-requisicao';
import { Alerta } from '@/components/ui/alert';
import { Spinner } from '@/components/ui/spinner';

const ABAS: { status: Status; rotulo: string; vazio: string }[] = [
  { status: 'autorizada', rotulo: 'Autorizadas', vazio: 'Nenhuma requisição autorizada.' },
  { status: 'em_uso', rotulo: 'Em uso', vazio: 'Nenhuma viagem em uso.' },
];

function Viagens() {
  const { eu } = useAuth();
  // Quem recebe `registrar_saida` em /api/eu vê também as autorizadas (aguardando saída).
  // A API decide o que é listado e os botões de cada cartão; aqui só se organiza a exibição.
  const comAutorizadas = eu?.permissoes.includes('registrar_saida') ?? false;
  const abas = comAutorizadas ? ABAS : ABAS.slice(1);
  const [aba, setAba] = React.useState<Status>('em_uso');
  const listas = useCarga(async () => {
    const r = await Promise.all(abas.map((x) => api.requisicoes(x.status)));
    return Object.fromEntries(abas.map((x, i) => [x.status, r[i]])) as Record<
      string,
      ListaRequisicoes
    >;
  });
  const acao = useAcao(async () => {
    await listas.recarregar();
  });
  const atual = abas.find((x) => x.status === aba) ?? abas[abas.length - 1]!;
  const itens = listas.dados?.[atual.status]?.itens ?? [];

  return (
    <>
      {comAutorizadas ? (
        <div role="tablist" aria-label="Situação" className="mb-4 flex rounded-xl bg-[#e8ecf2] p-1">
          {abas.map((a) => (
            <button
              key={a.status}
              type="button"
              role="tab"
              id={`aba-viagem-${a.status}`}
              aria-selected={atual.status === a.status}
              aria-controls="painel-viagem"
              onClick={() => setAba(a.status)}
              className={cn(
                'min-h-11 flex-1 rounded-padrao px-1 text-sm font-medium',
                atual.status === a.status ? 'bg-white font-semibold text-texto' : 'text-texto-2',
              )}
            >
              {a.rotulo} · {listas.dados?.[a.status]?.total ?? 0}
            </button>
          ))}
        </div>
      ) : null}

      {listas.erro ? <Alerta className="mb-4">{listas.erro}</Alerta> : null}
      {acao.erro ? <Alerta className="mb-4">{acao.erro}</Alerta> : null}

      <div id="painel-viagem" role={comAutorizadas ? 'tabpanel' : undefined}>
        {listas.carregando ? (
          <p role="status" className="flex items-center gap-2 text-texto-2">
            <Spinner />
            Carregando...
          </p>
        ) : listas.dados && itens.length === 0 ? (
          <p className="text-texto-2">{atual.vazio}</p>
        ) : (
          <ul className="lista-animada flex flex-col gap-3">
            {itens.map((r) => (
              <CartaoRequisicao
                key={r.id}
                req={r}
                mostrarSolicitante
                ocupado={acao.ocupado}
                acaoEmAndamento={acao.emAndamento?.id === r.id ? acao.emAndamento.acao : null}
                onAcao={(req, a, c) => void acao.executar(req, a, c)}
                aoRegistrarChecklist={listas.recarregar}
                mostrarChecklist
              />
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

export default function Viagem() {
  return (
    <AppShell titulo="Viagem" sobretitulo="Saída e chegada">
      <Viagens />
    </AppShell>
  );
}
