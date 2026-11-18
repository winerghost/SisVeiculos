'use client';

import * as React from 'react';
import Link from 'next/link';
import { Ban, Check, ChevronDown, FileText } from 'lucide-react';
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
import {
  BotoesAcao,
  DialogoDecidir,
  ehOrganizar,
  ROTULO_ANDAMENTO,
  type OnOrganizar,
} from '@/components/cartao-requisicao';
import { Button } from '@/components/ui/button';
import { Dialogo } from '@/components/ui/dialogo';
import { HoraSaida } from '@/components/ui/hora-saida';
import { StatusChip } from '@/components/ui/status-chip';
import type { Opcao } from './barra-filtros';
import { DialogoAutorizarRequisicao } from './dialogo-autorizar-requisicao';
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

/** Ações com botão direto na célula (as demais vão para "Mais"). */
const DIRETAS: readonly Acao[] = ['autorizar', 'negar'];

/** Mesmo critério de exibição de BotoesAcao, descontadas as ações ocultas. */
function temAcoes(r: Requisicao, comOrganizar: boolean, ocultar: readonly Acao[] = []): boolean {
  const p = r.acoes_permitidas.filter((a) => !(ocultar as readonly string[]).includes(a));
  return (
    p.some((a) => a in ACOES) ||
    p.includes('atribuir_motorista') ||
    (comOrganizar && p.some(ehOrganizar))
  );
}

const CLASSE_BOTAO = 'h-11 shrink-0 gap-1.5 px-2.5 text-sm whitespace-nowrap [&_svg]:shrink-0';

/**
 * Ações da linha (desktop), sempre vindas de `acoes_permitidas`. Autorizar só abre o modal; Negar
 * usa a confirmação existente; o resto (cancelar, arquivar, motorista, saída, chegada, checklist)
 * fica em "Mais", que reaproveita BotoesAcao. Com campos ou checklist exigidos para autorizar, o
 * fluxo completo fica em "Mais" (nada é exigido ou validado aqui).
 */
function AcoesDaLinha({
  req,
  comOrganizar,
  ocupado,
  onAcao,
  onOrganizar,
  processando,
  acaoEmAndamento,
  aoRegistrarChecklist,
  motoristas,
  motoristaPadrao,
  erroAcao,
}: {
  req: Requisicao;
  comOrganizar: boolean;
  ocupado: boolean;
  onAcao: OnAcao;
  onOrganizar?: OnOrganizar;
  processando?: ReadonlySet<string>;
  acaoEmAndamento?: { id: string; acao: Acao } | null;
  aoRegistrarChecklist?: () => void | Promise<void>;
  motoristas?: Motorista[];
  motoristaPadrao?: string | null;
  erroAcao?: string | null;
}) {
  const [modal, setModal] = React.useState<'autorizar' | 'negar' | 'mais' | null>(null);
  const permitidas = req.acoes_permitidas;
  const campoAutorizar = req.campos_acao?.autorizar;
  const exigeFluxoCompleto =
    !!campoAutorizar?.checklist_pendente || (campoAutorizar?.campos_obrigatorios ?? []).length > 0;
  const diretas = permitidas.includes('autorizar') && !exigeFluxoCompleto;
  const ocultar = diretas ? DIRETAS : [];
  const candidatos =
    motoristas && motoristas.length > 0 ? motoristas : (campoAutorizar?.motoristas ?? []);
  const padrao = motoristaPadrao ?? campoAutorizar?.motorista_padrao ?? null;
  const arquivando = !!processando?.has(req.id);
  const meuAndamento = acaoEmAndamento?.id === req.id ? acaoEmAndamento.acao : null;
  const autorizando = meuAndamento === 'autorizar';
  const bloqueada = ocupado || arquivando;
  const mostrarMais = temAcoes(req, comOrganizar, ocultar);
  const nome = req.nome_solicitante;

  // Ao terminar a chamada os diálogos fecham, exceto o de Autorizar com erro da API: esse fica
  // aberto (motorista preservado) e mostra a mensagem dentro dele.
  const emCurso = meuAndamento !== null || arquivando;
  // Só mostra o erro depois de uma tentativa feita neste modal (evita erro antigo de outra ação).
  const [tentou, setTentou] = React.useState(false);
  const [emCursoAntes, setEmCursoAntes] = React.useState(emCurso);
  if (emCursoAntes !== emCurso) {
    setEmCursoAntes(emCurso);
    if (emCursoAntes && !(modal === 'autorizar' && erroAcao)) setModal(null);
  }

  return (
    <div className="flex items-center gap-1.5 whitespace-nowrap">
      {diretas ? (
        <>
          <Button
            type="button"
            aria-label={`Autorizar requisição de ${nome}`}
            aria-haspopup="dialog"
            className={CLASSE_BOTAO}
            disabled={bloqueada}
            carregando={autorizando}
            onClick={() => {
              setTentou(false);
              setModal('autorizar');
            }}
          >
            {autorizando ? null : <Check aria-hidden className="size-4" />}
            {autorizando ? (ROTULO_ANDAMENTO.autorizar ?? 'Autorizando...') : 'Autorizar'}
          </Button>
          {permitidas.includes('negar') ? (
            <Button
              type="button"
              variante="perigo"
              aria-label={`Negar requisição de ${nome}`}
              aria-haspopup="dialog"
              className={CLASSE_BOTAO}
              disabled={bloqueada}
              carregando={meuAndamento === 'negar'}
              onClick={() => setModal('negar')}
            >
              {meuAndamento === 'negar' ? null : <Ban aria-hidden className="size-4" />}
              {meuAndamento === 'negar' ? (ROTULO_ANDAMENTO.negar ?? 'Negando...') : 'Negar'}
            </Button>
          ) : null}
        </>
      ) : (
        <Button asChild variante="contorno" className={CLASSE_BOTAO}>
          <Link
            href={`/requisicoes/detalhe?id=${encodeURIComponent(req.id)}`}
            aria-label={`Ver detalhes da requisição de ${nome}`}
          >
            <FileText aria-hidden className="size-4" />
            Ver detalhes
          </Link>
        </Button>
      )}
      {mostrarMais ? (
        <Button
          type="button"
          variante="contorno"
          aria-label={`Mais ações da requisição de ${nome}`}
          aria-haspopup="dialog"
          className={CLASSE_BOTAO}
          disabled={bloqueada}
          onClick={() => setModal('mais')}
        >
          Mais
          <ChevronDown aria-hidden className="size-4" />
        </Button>
      ) : null}
      {diretas ? (
        <>
          <DialogoAutorizarRequisicao
            req={req}
            aberto={modal === 'autorizar'}
            motoristas={candidatos}
            motoristaPadrao={padrao}
            ocupado={bloqueada}
            autorizando={autorizando}
            erro={modal === 'autorizar' && tentou ? erroAcao : null}
            onFechar={() => setModal(null)}
            onConfirmar={(id) => {
              setTentou(true);
              onAcao(req, 'autorizar', id ? { motorista_id: id } : undefined);
            }}
          />
          <DialogoDecidir
            acoes={modal === 'negar' ? ['negar'] : null}
            ocupado={bloqueada}
            acaoEmAndamento={meuAndamento}
            onFechar={() => setModal(null)}
            onEscolher={(a) => {
              setModal(null);
              onAcao(req, a);
            }}
          />
        </>
      ) : null}
      {mostrarMais ? (
        <Dialogo
          aberto={modal === 'mais'}
          role="dialog"
          comBotaoFechar
          bloqueado={emCurso}
          titulo="Mais ações"
          descricao={`Requisição de ${nome}`}
          onFechar={() => setModal(null)}
        >
          <BotoesAcao
            req={req}
            ocultar={ocultar}
            ocupado={bloqueada}
            onAcao={onAcao}
            onOrganizar={onOrganizar}
            arquivando={arquivando}
            acaoEmAndamento={meuAndamento}
            aoRegistrarChecklist={aoRegistrarChecklist}
            motoristas={motoristas}
            motoristaPadrao={motoristaPadrao}
          />
        </Dialogo>
      ) : null}
    </div>
  );
}

type PropsAcoes = Omit<Parameters<typeof AcoesDaLinha>[0], 'req'>;
// Contexto estável: a célula não é recriada (e o modal aberto não é perdido) a cada mudança de props.
const ContextoAcoes = React.createContext<PropsAcoes | null>(null);

function CelulaAcoes({ req }: { req: Requisicao }) {
  const props = React.useContext(ContextoAcoes);
  return props ? <AcoesDaLinha req={req} {...props} /> : null;
}

/**
 * Tabela de requisições (a partir de `md`). Ordenação e paginação são do servidor; a linha leva
 * ao detalhe e as ações (só as de acoes_permitidas) ficam na própria célula: Autorizar, Negar e
 * "Mais" (BotoesAcao: diálogos, checklist, km e motorista inclusos).
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
  erroAcao,
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
  /** Erro da última ação (useAcao.erro): o modal de Autorizar o exibe e permanece aberto. */
  erroAcao?: string | null;
}) {
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
        cell: (c) => (
          <span className="inline-block max-w-[6.5rem]">{dataHoraNumerica(c.getValue())}</span>
        ),
      }),
      h.display({
        id: 'acoes',
        header: 'Ações',
        cell: (c) => <CelulaAcoes req={c.row.original} />,
      }),
    );
    return lista;
  }, [mostrarSolicitante, ordenarPorStatus]);

  const contexto: PropsAcoes = {
    comOrganizar,
    ocupado,
    onAcao,
    onOrganizar,
    processando,
    acaoEmAndamento,
    aoRegistrarChecklist,
    motoristas,
    motoristaPadrao,
    erroAcao,
  };

  return (
    <ContextoAcoes.Provider value={contexto}>
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
        compacta
        classeDaLinha={(r) => (r.status === 'solicitada' ? 'bg-[#fffaf0]' : undefined)}
      />
    </ContextoAcoes.Provider>
  );
}
