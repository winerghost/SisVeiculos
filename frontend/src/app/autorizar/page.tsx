'use client';

import * as React from 'react';
import { api, listarRequisicoes } from '@/lib/api';
import { useCarga } from '@/lib/use-carga';
import { useAcao } from '@/lib/use-acao';
import type { ListaRequisicoes, Motorista, OrdenacaoRequisicoes, Status } from '@/lib/tipos';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { AppShell } from '@/components/app-shell';
import { CartaoRequisicao } from '@/components/cartao-requisicao';
import { DialogoKmAlto } from '@/components/dialogo-km-alto';
import { Alerta } from '@/components/ui/alert';
import { Spinner } from '@/components/ui/spinner';
import { BarraFiltros, FILTRO_VAZIO, type ValoresFiltro } from '@/components/tabela/barra-filtros';
import { Paginacao } from '@/components/tabela/paginacao';
import { OPCOES_ORDENAR, TabelaRequisicoes } from '@/components/tabela/tabela-requisicoes';
import { useTelaMedia } from '@/components/tabela/use-tela-media';

// A aba já fixa o status: ordenar por status não faz sentido aqui.
const ORDENAR_AUTORIZAR = OPCOES_ORDENAR.filter((o) => !o.valor.endsWith('status'));
// Celular: até 100 cartões por página (o que a lista já mostrava); tabela: 20 por padrão.
const POR_PAGINA_CELULAR = 100;

const ABAS: { status: Status; rotulo: string }[] = [
  { status: 'solicitada', rotulo: 'Pendentes' },
  { status: 'autorizada', rotulo: 'Autorizadas' },
  { status: 'em_uso', rotulo: 'Em uso' },
];

function Painel() {
  const { eu } = useAuth();
  const [aba, setAba] = React.useState<Status>('solicitada');
  const telaMedia = useTelaMedia();
  const [filtros, setFiltros] = React.useState<ValoresFiltro>(FILTRO_VAZIO);
  const [pagina, setPagina] = React.useState(0);
  const [porPaginaTabela, setPorPaginaTabela] = React.useState(20);
  const porPagina = telaMedia ? porPaginaTabela : POR_PAGINA_CELULAR;
  // Só monta a consulta (a API filtra). A aba aberta traz a página; as outras só o total
  // (limite=1) para o contador da aba, com os mesmos filtros.
  const base = {
    q: filtros.q,
    veiculo_id: filtros.veiculo_id || undefined,
    unidade: filtros.unidade || undefined,
    ordenar: (filtros.ordenar || undefined) as OrdenacaoRequisicoes | undefined,
  };
  const chave = JSON.stringify({ base, aba, pagina, porPagina });
  const listas = useCarga(
    async () => {
      const [a, b, c] = await Promise.all(
        ABAS.map((x) =>
          listarRequisicoes(
            x.status === aba
              ? { ...base, status: x.status, limite: porPagina, offset: pagina * porPagina }
              : { ...base, status: x.status, limite: 1 },
          ),
        ),
      );
      return { solicitada: a, autorizada: b, em_uso: c } as Record<string, ListaRequisicoes>;
    },
    chave,
    { recursos: ['requisicoes'] },
  );
  const veiculos = useCarga(() => api.veiculos(), '', { recursos: ['veiculos'] });
  const mudarFiltros = React.useCallback((v: ValoresFiltro) => {
    setFiltros(v);
    setPagina(0);
  }, []);
  const total = listas.dados?.[aba]?.total ?? 0;
  // Ação que tira o último item da última página: volta para a página que ainda existe.
  const pagAtual = listas.dados?.[aba];
  if (pagAtual && pagAtual.itens.length === 0 && pagina > 0) {
    const alvo = Math.max(0, Math.ceil(pagAtual.total / porPagina) - 1);
    if (alvo !== pagina) setPagina(alvo);
  }
  const acao = useAcao(async () => {
    await listas.recarregar();
  });
  const itens = listas.dados?.[aba]?.itens ?? [];

  // Candidatos a motorista vêm da API. Recurso desligado ou recusa: sem seletor, sem erro na tela.
  const [motoristas, setMotoristas] = React.useState<Motorista[]>([]);
  const [padrao, setPadrao] = React.useState<string | null>(null);
  React.useEffect(() => {
    let ativo = true;
    api
      .motoristas()
      .then((r) => {
        if (!ativo) return;
        setMotoristas(Array.isArray(r?.itens) ? r.itens : []);
        setPadrao(r?.padrao ?? null);
      })
      .catch(() => {
        if (!ativo) return;
        setMotoristas([]);
        setPadrao(null);
      });
    return () => {
      ativo = false;
    };
  }, []);

  return (
    <>
      <div role="tablist" aria-label="Situação" className="mb-4 flex rounded-xl bg-[#e8ecf2] p-1">
        {ABAS.map((a) => (
          <button
            key={a.status}
            type="button"
            role="tab"
            id={`aba-${a.status}`}
            aria-selected={aba === a.status}
            aria-controls="painel-lista"
            onClick={() => {
              if (a.status === aba) return;
              setPagina(0);
              setAba(a.status);
            }}
            className={cn(
              'min-h-11 flex-1 rounded-padrao px-1 text-sm font-medium',
              aba === a.status ? 'bg-white font-semibold text-texto' : 'text-texto-2',
            )}
          >
            {a.rotulo} · {listas.dados?.[a.status]?.total ?? 0}
          </button>
        ))}
      </div>

      <BarraFiltros
        id="filtro-autorizar"
        valores={filtros}
        aoMudar={mudarFiltros}
        rotuloBusca="Buscar por solicitante ou trajeto"
        veiculos={veiculos.dados?.map((v) => ({
          valor: v.id,
          rotulo: v.placa ? `${v.descricao} · ${v.placa}` : v.descricao,
        }))}
        unidades={eu?.unidades.map((u) => ({ valor: u, rotulo: u }))}
        ordenacoes={ORDENAR_AUTORIZAR}
        recolhivel={!telaMedia}
      />

      {listas.erro ? <Alerta className="mb-4">{listas.erro}</Alerta> : null}
      {acao.erro ? <Alerta className="mb-4">{acao.erro}</Alerta> : null}
      <DialogoKmAlto confirmacao={acao.confirmacaoKm} />

      <div id="painel-lista" role="tabpanel" aria-labelledby={`aba-${aba}`}>
        {listas.carregando ? (
          <p role="status" className="flex items-center gap-2 text-texto-2">
            <Spinner />
            Carregando...
          </p>
        ) : itens.length === 0 ? (
          <p className="text-texto-2">Nada por aqui.</p>
        ) : telaMedia ? (
          <TabelaRequisicoes
            legenda={`Requisições: ${ABAS.find((x) => x.status === aba)?.rotulo ?? ''}`}
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
            mostrarSolicitante
            ordenarPorStatus={false}
            ocupado={acao.ocupado}
            onAcao={(req, a, c) => void acao.executar(req, a, c)}
            acaoEmAndamento={acao.emAndamento}
            aoRegistrarChecklist={listas.recarregar}
            motoristas={motoristas}
            motoristaPadrao={padrao}
          />
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
                motoristas={motoristas}
                motoristaPadrao={padrao}
              />
            ))}
          </ul>
        )}
        {!telaMedia && total > POR_PAGINA_CELULAR ? (
          <Paginacao pagina={pagina} tamanho={porPagina} total={total} aoMudarPagina={setPagina} />
        ) : null}
      </div>
    </>
  );
}

export default function PainelAutorizador() {
  const { eu } = useAuth();
  return (
    <AppShell titulo="Painel do autorizador" sobretitulo={eu?.rotulo_perfil} largo>
      <Painel />
    </AppShell>
  );
}
