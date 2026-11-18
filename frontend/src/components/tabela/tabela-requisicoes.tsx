'use client';

import * as React from 'react';
import Link from 'next/link';
import { ChevronDown } from 'lucide-react';
import { ACOES } from '@/lib/api';
import { dataCurta, dataHoraNumerica } from '@/lib/formato';
import type {
  Acao,
  CorpoAcao,
  Motorista,
  OrdenacaoRequisicoes,
  Requisicao,
  Status,
} from '@/lib/tipos';
import { BotoesAcao, ehOrganizar, type OnOrganizar } from '@/components/cartao-requisicao';
import { HoraSaida } from '@/components/ui/hora-saida';
import { StatusChip } from '@/components/ui/status-chip';
import { cn } from '@/lib/utils';
import type { Opcao } from './barra-filtros';
import {
  TabelaDados,
  ajudanteColunas,
  type ColunaTabela,
  type PaginationState,
  type SortingState,
} from './tabela-dados';

// ---- Mapeamento só de apresentação entre o cabeçalho da tabela e o parâmetro `ordenar` ----

const COLUNAS_ORDENAVEIS = ['criado_em', 'hora_saida', 'status'] as const;
type ColunaOrdenavel = (typeof COLUNAS_ORDENAVEIS)[number];
const ehOrdenavel = (id: string): id is ColunaOrdenavel =>
  (COLUNAS_ORDENAVEIS as readonly string[]).includes(id);

/** Padrão da API quando `ordenar` não é enviado. */
export const ORDENAR_PADRAO: OrdenacaoRequisicoes = '-criado_em';

export function ordenacaoParaTabela(ordenar: string): SortingState {
  const valor = ordenar || ORDENAR_PADRAO;
  const desc = valor.startsWith('-');
  return [{ id: desc ? valor.slice(1) : valor, desc }];
}

export function tabelaParaOrdenacao(s: SortingState): OrdenacaoRequisicoes | '' {
  const primeira = s[0];
  if (!primeira || !ehOrdenavel(primeira.id)) return '';
  return `${primeira.desc ? '-' : ''}${primeira.id}` as OrdenacaoRequisicoes;
}

export const OPCOES_ORDENAR: Opcao[] = [
  { valor: '-criado_em', rotulo: 'Mais recentes' },
  { valor: 'criado_em', rotulo: 'Mais antigas' },
  { valor: 'hora_saida', rotulo: 'Saída mais próxima' },
  { valor: '-hora_saida', rotulo: 'Saída mais distante' },
  { valor: 'status', rotulo: 'Status (A–Z)' },
  { valor: '-status', rotulo: 'Status (Z–A)' },
];

export const OPCOES_STATUS: Opcao[] = (
  [
    ['solicitada', 'Solicitada'],
    ['autorizada', 'Autorizada'],
    ['em_uso', 'Em uso'],
    ['concluida', 'Concluída'],
    ['negada', 'Negada'],
    ['cancelada', 'Cancelada'],
  ] as [Status, string][]
).map(([valor, rotulo]) => ({ valor, rotulo }));

type OnAcao = (req: Requisicao, acao: Acao, corpo?: CorpoAcao) => void;

/** Mesmo critério de exibição de BotoesAcao: há algo a mostrar vindo de acoes_permitidas. */
function temAcoes(r: Requisicao, comOrganizar: boolean): boolean {
  const p = r.acoes_permitidas;
  return (
    p.some((a) => a in ACOES) ||
    p.includes('atribuir_motorista') ||
    (comOrganizar && p.some(ehOrganizar))
  );
}

/**
 * Tabela de requisições (a partir de `md`). Ordenação e paginação são do servidor; a linha leva
 * ao detalhe e as ações (as mesmas do cartão, só as de acoes_permitidas) abrem numa linha
 * expandida, reaproveitando BotoesAcao (diálogos, checklist, km e motorista inclusos).
 */
export function TabelaRequisicoes({
  legenda,
  itens,
  total,
  ordenar,
  aoOrdenar,
  paginacao,
  aoPaginar,
  aoMudarTamanho,
  mostrarSolicitante,
  ordenarPorStatus = true,
  ocupado,
  carregando,
  onAcao,
  onOrganizar,
  processando,
  acaoEmAndamento,
  aoRegistrarChecklist,
  motoristas,
  motoristaPadrao,
}: {
  legenda: string;
  itens: Requisicao[];
  total: number;
  ordenar: string;
  aoOrdenar: (o: OrdenacaoRequisicoes | '') => void;
  paginacao: PaginationState;
  aoPaginar: (p: PaginationState) => void;
  aoMudarTamanho?: (t: number) => void;
  mostrarSolicitante?: boolean;
  /** false quando a tela já fixa o status (abas do autorizador). */
  ordenarPorStatus?: boolean;
  ocupado: boolean;
  carregando?: boolean;
  onAcao: OnAcao;
  onOrganizar?: OnOrganizar;
  /** Ids com arquivar/desarquivar em andamento. */
  processando?: ReadonlySet<string>;
  acaoEmAndamento?: { id: string; acao: Acao } | null;
  aoRegistrarChecklist?: () => void | Promise<void>;
  motoristas?: Motorista[];
  motoristaPadrao?: string | null;
}) {
  const [aberta, setAberta] = React.useState<string | null>(null);
  const comOrganizar = !!onOrganizar;

  const colunas = React.useMemo<ColunaTabela<Requisicao>[]>(() => {
    const h = ajudanteColunas<Requisicao>();
    const lista: ColunaTabela<Requisicao>[] = [
      h.accessor('hora_saida', {
        header: 'Saída prevista',
        sortDescFirst: false,
        cell: (c) => {
          const r = c.row.original;
          return (
            <Link
              href={`/requisicoes/detalhe?id=${encodeURIComponent(r.id)}`}
              className="inline-flex min-h-11 items-center rounded-padrao font-semibold whitespace-nowrap text-texto underline-offset-4 hover:underline"
            >
              <HoraSaida texto={dataCurta(r.hora_saida)} situacao={r.situacao_horario} />
              <span className="sr-only">, abrir detalhe</span>
            </Link>
          );
        },
      }),
    ];
    if (mostrarSolicitante) {
      lista.push(
        h.accessor('nome_solicitante', {
          header: 'Solicitante',
          enableSorting: false,
          cell: (c) => <span className="break-words">{c.getValue()}</span>,
        }),
      );
    }
    lista.push(
      h.accessor('unidade', { header: 'Unidade', enableSorting: false }),
      h.accessor((r) => r.veiculo_descricao ?? '', {
        id: 'veiculo',
        header: 'Veículo',
        enableSorting: false,
      }),
      h.accessor('trajeto', {
        header: 'Trajeto',
        enableSorting: false,
        cell: (c) => <span className="line-clamp-2 max-w-64 break-words">{c.getValue()}</span>,
      }),
      h.accessor('status', {
        header: 'Status',
        enableSorting: ordenarPorStatus,
        cell: (c) => <StatusChip status={c.getValue()} />,
      }),
      h.accessor('criado_em', {
        header: 'Criada em',
        sortDescFirst: true,
        cell: (c) => <span className="whitespace-nowrap">{dataHoraNumerica(c.getValue())}</span>,
      }),
      h.display({
        id: 'acoes',
        header: () => <span className="sr-only">Ações</span>,
        cell: (c) => {
          const r = c.row.original;
          if (!temAcoes(r, comOrganizar)) return null;
          const expandida = aberta === r.id;
          return (
            <button
              type="button"
              aria-expanded={expandida}
              aria-controls={expandida ? `acoes-${r.id}` : undefined}
              onClick={() => setAberta(expandida ? null : r.id)}
              className="inline-flex min-h-11 items-center gap-1 rounded-padrao border border-borda-campo bg-white px-3 font-semibold whitespace-nowrap text-texto hover:bg-fundo"
            >
              Ações
              <span className="sr-only"> de {r.nome_solicitante}</span>
              <ChevronDown
                aria-hidden
                className={cn('size-4 transition-transform', expandida && 'rotate-180')}
              />
            </button>
          );
        },
      }),
    );
    return lista;
  }, [mostrarSolicitante, ordenarPorStatus, aberta, comOrganizar]);

  return (
    <TabelaDados
      legenda={legenda}
      colunas={colunas}
      dados={itens}
      idDaLinha={(r) => r.id}
      ordenacao={ordenacaoParaTabela(ordenar)}
      aoOrdenar={(s) => aoOrdenar(tabelaParaOrdenacao(s))}
      paginacao={paginacao}
      aoPaginar={aoPaginar}
      aoMudarTamanho={aoMudarTamanho}
      manual
      total={total}
      ocupado={carregando}
      hrefDaLinha={(r) => `/requisicoes/detalhe?id=${encodeURIComponent(r.id)}`}
      linhaExtra={(r) =>
        aberta === r.id && temAcoes(r, comOrganizar) ? (
          <div id={`acoes-${r.id}`} className="max-w-md">
            <BotoesAcao
              req={r}
              ocupado={ocupado || !!processando?.has(r.id)}
              onAcao={onAcao}
              onOrganizar={onOrganizar}
              arquivando={processando?.has(r.id)}
              acaoEmAndamento={acaoEmAndamento?.id === r.id ? acaoEmAndamento.acao : null}
              aoRegistrarChecklist={aoRegistrarChecklist}
              motoristas={motoristas}
              motoristaPadrao={motoristaPadrao}
            />
          </div>
        ) : null
      }
    />
  );
}
