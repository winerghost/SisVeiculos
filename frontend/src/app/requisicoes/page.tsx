'use client';

import * as React from 'react';
import Link from 'next/link';
import { Car, Plus } from 'lucide-react';
import { api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useCarga } from '@/lib/use-carga';
import { useAcao } from '@/lib/use-acao';
import { AppShell } from '@/components/app-shell';
import { CartaoRequisicao } from '@/components/cartao-requisicao';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';

function Conteudo() {
  const { eu } = useAuth();
  const reqs = useCarga(() => api.requisicoes().then((r) => r.itens));
  const veiculos = useCarga(() => api.veiculos());
  const acao = useAcao(async () => {
    await Promise.all([reqs.recarregar(), veiculos.recarregar()]);
  });
  const podeCriar = eu?.permissoes.includes('criar_requisicao') ?? false;
  const todas = eu?.permissoes.includes('ver_todas_requisicoes') ?? false;

  return (
    <>
      {veiculos.dados?.map((v) => (
        <section
          key={v.id}
          aria-label={v.descricao}
          className="mb-5 flex items-center gap-4 rounded-xl border border-borda bg-white p-4"
        >
          <span className="flex size-14 items-center justify-center rounded-xl bg-[#d9f2e6] text-sucesso">
            <Car aria-hidden />
          </span>
          <div>
            <p className="text-lg font-semibold">
              {v.descricao} {v.disponivel ? 'livre agora' : 'indisponível'}
            </p>
            {v.motivo_indisponibilidade ? (
              <p className="text-texto-2">{v.motivo_indisponibilidade}</p>
            ) : null}
          </div>
        </section>
      ))}

      {reqs.erro ? <Alerta className="mb-4">{reqs.erro}</Alerta> : null}
      {acao.erro ? <Alerta className="mb-4">{acao.erro}</Alerta> : null}

      <h2 className="mb-3 text-base font-medium text-texto-2">{todas ? 'Todas' : 'Recentes'}</h2>
      {reqs.carregando ? (
        <p role="status" className="text-texto-2">
          Carregando...
        </p>
      ) : reqs.dados && reqs.dados.length === 0 ? (
        <p className="text-texto-2">Nenhuma requisição ainda.</p>
      ) : (
        <ul className="lista-animada flex flex-col gap-3">
          {reqs.dados?.map((r) => (
            <CartaoRequisicao
              key={r.id}
              req={r}
              mostrarSolicitante={todas}
              ocupado={acao.ocupado}
              onAcao={(req, a, c) => void acao.executar(req, a, c)}
            />
          ))}
        </ul>
      )}

      {podeCriar ? (
        <div className="fixed inset-x-0 bottom-16 z-20 mx-auto max-w-md bg-gradient-to-t from-fundo via-fundo to-transparent px-5 pt-4 pb-3">
          <Button asChild tamanho="cheio">
            <Link href="/requisicoes/nova">
              <Plus aria-hidden /> Nova requisição
            </Link>
          </Button>
        </div>
      ) : null}
    </>
  );
}

export default function MinhasRequisicoes() {
  return (
    <AppShell titulo="Requisições">
      <Conteudo />
    </AppShell>
  );
}
