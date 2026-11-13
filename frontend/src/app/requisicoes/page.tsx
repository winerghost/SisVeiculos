'use client';

import * as React from 'react';
import Link from 'next/link';
import { Car, Plus } from 'lucide-react';
import { api } from '@/lib/api';
import type { AcaoOrganizar } from '@/lib/tipos';
import { useAuth } from '@/lib/auth';
import { useCarga } from '@/lib/use-carga';
import { useAcao } from '@/lib/use-acao';
import { useOrganizar } from '@/lib/use-organizar';
import { AppShell } from '@/components/app-shell';
import { AbasArquivadas } from '@/components/abas-arquivadas';
import { AvisoOrganizarRegiao } from '@/components/aviso-organizar';
import { CartaoRequisicao } from '@/components/cartao-requisicao';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';

function Conteudo() {
  const { eu } = useAuth();
  const [arquivadas, setArquivadas] = React.useState(false);
  const reqs = useCarga(() => api.requisicoes(undefined, arquivadas), String(arquivadas));
  const veiculos = useCarga(() => api.veiculos());
  const acao = useAcao(async () => {
    await Promise.all([reqs.recarregar(), veiculos.recarregar()]);
  });
  const organizar = useOrganizar(async () => {
    await reqs.recarregar();
  });
  const titulo = React.useRef<HTMLHeadingElement>(null);
  const itens = reqs.dados?.itens ?? [];
  const mudarAba = (v: boolean) => {
    if (v === arquivadas) return;
    organizar.limpar();
    reqs.setDados(null);
    setArquivadas(v);
  };
  const aoOrganizar = async (id: string, a: AcaoOrganizar, desfazer = true) => {
    await organizar.executar(id, a, desfazer);
    // O item sai da lista: o foco vai para o título, de onde o "Desfazer" é o próximo Tab.
    titulo.current?.focus();
  };
  // Dica dos gestos só quando algum cartão realmente tem ação de arrastar liberada pela API.
  const comGestos = itens.some((r) =>
    r.acoes_permitidas.some((a) =>
      ['autorizar', 'negar', 'cancelar', 'arquivar', 'desarquivar'].includes(a),
    ),
  );
  const podeCriar = eu?.permissoes.includes('criar_requisicao') ?? false;
  const todas = eu?.permissoes.includes('ver_todas_requisicoes') ?? false;

  return (
    <>
      <AbasArquivadas
        arquivadas={arquivadas}
        aoMudar={mudarAba}
        rotuloAtivas="Ativas"
        totalArquivadas={arquivadas ? reqs.dados?.total : undefined}
      />

      {!arquivadas
        ? veiculos.dados?.map((v) => (
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
          ))
        : null}

      {reqs.erro ? <Alerta className="mb-4">{reqs.erro}</Alerta> : null}
      {acao.erro ? <Alerta className="mb-4">{acao.erro}</Alerta> : null}

      <h2
        ref={titulo}
        tabIndex={-1}
        className="mb-3 text-base font-medium text-texto-2 outline-none"
      >
        {arquivadas ? 'Arquivadas' : todas ? 'Todas' : 'Recentes'}
      </h2>
      <AvisoOrganizarRegiao
        aviso={organizar.aviso}
        erro={organizar.erro}
        ocupado={organizar.ocupado || acao.ocupado}
        onDesfazer={(id, a) => void aoOrganizar(id, a, false)}
      />
      {comGestos ? (
        <p className="mb-3 text-sm text-texto-2">
          Você também pode arrastar um cartão para a direita ou para a esquerda; os botões do cartão
          fazem o mesmo.
        </p>
      ) : null}
      {reqs.dados === null && !reqs.erro ? (
        <p role="status" className="text-texto-2">
          Carregando...
        </p>
      ) : reqs.dados && itens.length === 0 ? (
        <p className="text-texto-2">
          {arquivadas ? 'Nenhuma requisição arquivada.' : 'Nenhuma requisição ainda.'}
        </p>
      ) : (
        <ul className="lista-animada flex flex-col gap-3">
          {itens.map((r) => (
            <CartaoRequisicao
              key={r.id}
              req={r}
              mostrarSolicitante={todas}
              ocupado={acao.ocupado || organizar.ocupado}
              gestos
              onAcao={(req, a, c) => void acao.executar(req, a, c)}
              onOrganizar={(req, a) => void aoOrganizar(req.id, a)}
            />
          ))}
        </ul>
      )}

      {podeCriar ? <div aria-hidden className="h-24" /> : null}
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
