'use client';

import * as React from 'react';
import Link from 'next/link';
import { Car, Plus } from 'lucide-react';
import { api, listarRequisicoes } from '@/lib/api';
import type { AcaoOrganizar, FiltrosRequisicoes, OrdenacaoRequisicoes, Status } from '@/lib/tipos';
import { useAuth } from '@/lib/auth';
import { useCarga } from '@/lib/use-carga';
import { useAcao } from '@/lib/use-acao';
import { useOrganizar } from '@/lib/use-organizar';
import { AppShell } from '@/components/app-shell';
import { AbasArquivadas } from '@/components/abas-arquivadas';
import { AvisoOrganizarRegiao } from '@/components/aviso-organizar';
import { CartaoRequisicao } from '@/components/cartao-requisicao';
import { DialogoKmAlto } from '@/components/dialogo-km-alto';
import { Alerta } from '@/components/ui/alert';
import { Spinner } from '@/components/ui/spinner';
import { Button } from '@/components/ui/button';
import { BarraFiltros, FILTRO_VAZIO, type ValoresFiltro } from '@/components/tabela/barra-filtros';
import { Paginacao } from '@/components/tabela/paginacao';
import {
  OPCOES_ORDENAR,
  OPCOES_STATUS,
  TabelaRequisicoes,
} from '@/components/tabela/tabela-requisicoes';
import { useTelaMedia } from '@/components/tabela/use-tela-media';

// Celular: até 100 cartões por página (o que a lista já mostrava); tabela: 20 por padrão.
const POR_PAGINA_CELULAR = 100;

function Conteudo() {
  const { eu } = useAuth();
  const [arquivadas, setArquivadas] = React.useState(false);
  const telaMedia = useTelaMedia();
  const [filtros, setFiltros] = React.useState<ValoresFiltro>(FILTRO_VAZIO);
  const [pagina, setPagina] = React.useState(0);
  const [porPaginaTabela, setPorPaginaTabela] = React.useState(20);
  const porPagina = telaMedia ? porPaginaTabela : POR_PAGINA_CELULAR;
  // Só monta a consulta; busca, filtros, ordenação e visibilidade são da API.
  const consulta: FiltrosRequisicoes = {
    q: filtros.q,
    status: (filtros.status || undefined) as Status | undefined,
    veiculo_id: filtros.veiculo_id || undefined,
    unidade: filtros.unidade || undefined,
    ordenar: (filtros.ordenar || undefined) as OrdenacaoRequisicoes | undefined,
    arquivadas,
    limite: porPagina,
    offset: pagina * porPagina,
  };
  const reqs = useCarga(() => listarRequisicoes(consulta), JSON.stringify(consulta), {
    recursos: ['requisicoes'],
  });
  const veiculos = useCarga(() => api.veiculos(), '', { recursos: ['requisicoes', 'veiculos'] });
  const acao = useAcao(async () => {
    await Promise.all([reqs.recarregar(), veiculos.recarregar()]);
  });
  const organizar = useOrganizar(async () => {
    await reqs.recarregar();
  });
  const titulo = React.useRef<HTMLHeadingElement>(null);
  const itens = reqs.dados?.itens ?? [];
  const total = reqs.dados?.total ?? 0;
  const mudarAba = (v: boolean) => {
    if (v === arquivadas) return;
    organizar.limpar();
    reqs.setDados(null);
    setPagina(0);
    setArquivadas(v);
  };
  const mudarFiltros = React.useCallback((v: ValoresFiltro) => {
    setFiltros(v);
    setPagina(0);
  }, []);
  // Arquivar o último item da última página: volta para a página que ainda existe (total da API).
  if (reqs.dados && reqs.dados.itens.length === 0 && pagina > 0) {
    const alvo = Math.max(0, Math.ceil(reqs.dados.total / porPagina) - 1);
    if (alvo !== pagina) setPagina(alvo);
  }
  const filtrando = !!(filtros.q.trim() || filtros.status || filtros.veiculo_id || filtros.unidade);
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
              className="mb-5 flex items-center gap-4 rounded-xl border border-borda bg-white p-4 md:border-[#bfe3cd] md:bg-[#e8f6ee]"
            >
              <span className="flex size-14 shrink-0 items-center justify-center rounded-xl bg-[#d9f2e6] text-sucesso">
                <Car aria-hidden />
              </span>
              <div className="min-w-0 break-words">
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

      <BarraFiltros
        id="filtro-requisicoes"
        valores={filtros}
        aoMudar={mudarFiltros}
        rotuloBusca="Buscar por solicitante ou trajeto"
        status={OPCOES_STATUS}
        veiculos={veiculos.dados?.map((v) => ({
          valor: v.id,
          rotulo: v.placa ? `${v.descricao} · ${v.placa}` : v.descricao,
        }))}
        unidades={eu?.unidades.map((u) => ({ valor: u, rotulo: u }))}
        ordenacoes={OPCOES_ORDENAR}
        recolhivel={!telaMedia}
      />

      {reqs.erro ? <Alerta className="mb-4">{reqs.erro}</Alerta> : null}
      {acao.erro ? <Alerta className="mb-4">{acao.erro}</Alerta> : null}
      <DialogoKmAlto confirmacao={acao.confirmacaoKm} />

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
      {comGestos && !telaMedia ? (
        <p className="mb-3 text-sm text-texto-2">
          Você também pode arrastar um cartão para a direita ou para a esquerda; os botões do cartão
          fazem o mesmo.
        </p>
      ) : null}
      {reqs.dados === null && !reqs.erro ? (
        <p role="status" className="flex items-center gap-2 text-texto-2">
          <Spinner />
          Carregando...
        </p>
      ) : reqs.dados && itens.length === 0 ? (
        <p className="text-texto-2">
          {filtrando
            ? 'Nenhuma requisição encontrada com esses filtros.'
            : arquivadas
              ? 'Nenhuma requisição arquivada.'
              : 'Nenhuma requisição ainda.'}
        </p>
      ) : telaMedia ? (
        <TabelaRequisicoes
          legenda={arquivadas ? 'Requisições arquivadas' : 'Requisições'}
          itens={itens}
          total={total}
          ordenar={filtros.ordenar}
          aoOrdenar={(o) => mudarFiltros({ ...filtros, ordenar: o })}
          paginacao={{ pageIndex: pagina, pageSize: porPagina }}
          aoPaginar={(p) => setPagina(p.pageIndex)}
          aoMudarTamanho={(t) => {
            setPorPaginaTabela(t);
            setPagina(0);
          }}
          mostrarSolicitante={todas}
          ocupado={acao.ocupado}
          onAcao={(req, a, c) => void acao.executar(req, a, c)}
          onOrganizar={(req, a) => void aoOrganizar(req.id, a)}
          processando={organizar.processando}
          acaoEmAndamento={acao.emAndamento}
          erroAcao={acao.erro}
        />
      ) : (
        <ul className="lista-animada flex flex-col gap-3">
          {itens.map((r) => (
            <CartaoRequisicao
              key={r.id}
              req={r}
              mostrarSolicitante={todas}
              ocupado={acao.ocupado || organizar.processando.has(r.id)}
              acaoEmAndamento={acao.emAndamento?.id === r.id ? acao.emAndamento.acao : null}
              arquivando={organizar.processando.has(r.id)}
              gestos
              onAcao={(req, a, c) => void acao.executar(req, a, c)}
              onOrganizar={(req, a) => void aoOrganizar(req.id, a)}
            />
          ))}
        </ul>
      )}
      {!telaMedia && total > POR_PAGINA_CELULAR ? (
        <Paginacao pagina={pagina} tamanho={porPagina} total={total} aoMudarPagina={setPagina} />
      ) : null}

      {podeCriar ? <div aria-hidden className="h-24" /> : null}
      {podeCriar ? (
        <div className="fixed inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))] z-20 mx-auto max-w-md md:max-w-5xl bg-gradient-to-t from-fundo via-fundo to-transparent px-5 pt-4 pb-3">
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
    <AppShell titulo="Requisições" subtitulo="Gestão de solicitações de veículos" largo>
      <Conteudo />
    </AppShell>
  );
}
