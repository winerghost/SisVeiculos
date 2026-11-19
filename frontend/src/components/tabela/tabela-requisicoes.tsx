'use client';

import * as React from 'react';
import Link from 'next/link';
import { Ban, Check, ChevronDown, FileText } from 'lucide-react';
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
  temAcoesVisiveis,
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

const CLASSE_BOTAO = 'h-11 shrink-0 gap-1.5 px-2.5 text-sm whitespace-nowrap [&_svg]:shrink-0';

type TipoModal = 'autorizar' | 'negar' | 'mais';
/** Um só conjunto de diálogos para a tabela toda: qual linha e qual tipo estão abertos. */
type Modal = { id: string; tipo: TipoModal; aberto: boolean } | null;

type PropsAcoes = {
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
};

/** Valores de apresentação da linha, todos derivados do que a API devolveu (`acoes_permitidas`). */
function derivar(req: Requisicao, p: PropsAcoes) {
  const campoAutorizar = req.campos_acao?.autorizar;
  const exigeFluxoCompleto =
    !!campoAutorizar?.checklist_pendente || (campoAutorizar?.campos_obrigatorios ?? []).length > 0;
  const diretas = req.acoes_permitidas.includes('autorizar') && !exigeFluxoCompleto;
  const ocultar = diretas ? DIRETAS : [];
  const arquivando = !!p.processando?.has(req.id);
  const meuAndamento = p.acaoEmAndamento?.id === req.id ? p.acaoEmAndamento.acao : null;
  return {
    diretas,
    ocultar,
    candidatos:
      p.motoristas && p.motoristas.length > 0 ? p.motoristas : (campoAutorizar?.motoristas ?? []),
    padrao: p.motoristaPadrao ?? campoAutorizar?.motorista_padrao ?? null,
    arquivando,
    meuAndamento,
    autorizando: meuAndamento === 'autorizar',
    bloqueada: p.ocupado || arquivando,
    emCurso: meuAndamento !== null || arquivando,
    mostrarMais: temAcoesVisiveis(req, p.comOrganizar, ocultar),
  };
}

/**
 * Botões da linha (desktop), sempre vindos de `acoes_permitidas`. Autorizar só abre o modal; Negar
 * usa a confirmação existente; o resto (cancelar, arquivar, motorista, saída, chegada, checklist)
 * fica em "Mais", que reaproveita BotoesAcao. Com campos ou checklist exigidos para autorizar, o
 * fluxo completo fica em "Mais" (nada é exigido ou validado aqui). Os diálogos não ficam aqui:
 * existe um único conjunto para a tabela (DialogosDaTabela).
 */
function AcoesDaLinha({
  req,
  abrir,
  ...p
}: PropsAcoes & { req: Requisicao; abrir: (id: string, tipo: TipoModal) => void }) {
  const { diretas, meuAndamento, autorizando, bloqueada, mostrarMais } = derivar(req, p);
  const nome = req.nome_solicitante;
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
            onClick={() => abrir(req.id, 'autorizar')}
          >
            {autorizando ? null : <Check aria-hidden className="size-4" />}
            {autorizando ? (ROTULO_ANDAMENTO.autorizar ?? 'Autorizando...') : 'Autorizar'}
          </Button>
          {req.acoes_permitidas.includes('negar') ? (
            <Button
              type="button"
              variante="perigo"
              aria-label={`Negar requisição de ${nome}`}
              aria-haspopup="dialog"
              className={CLASSE_BOTAO}
              disabled={bloqueada}
              carregando={meuAndamento === 'negar'}
              onClick={() => abrir(req.id, 'negar')}
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
          onClick={() => abrir(req.id, 'mais')}
        >
          Mais
          <ChevronDown aria-hidden className="size-4" />
        </Button>
      ) : null}
    </div>
  );
}

/** Diálogos da linha escolhida (Autorizar, Negar e Mais), montados uma única vez para a tabela. */
function DialogosDaTabela({
  req,
  modal,
  tentou,
  aoTentar,
  aoFechar,
  ...p
}: PropsAcoes & {
  req: Requisicao;
  modal: NonNullable<Modal>;
  tentou: boolean;
  aoTentar: () => void;
  aoFechar: () => void;
}) {
  const d = derivar(req, p);
  return (
    <>
      {d.diretas ? (
        <>
          <DialogoAutorizarRequisicao
            key={req.id}
            req={req}
            aberto={modal.aberto && modal.tipo === 'autorizar'}
            motoristas={d.candidatos}
            motoristaPadrao={d.padrao}
            ocupado={d.bloqueada}
            autorizando={d.autorizando}
            erro={modal.tipo === 'autorizar' && tentou ? p.erroAcao : null}
            onFechar={aoFechar}
            onConfirmar={(id) => {
              aoTentar();
              p.onAcao(req, 'autorizar', id ? { motorista_id: id } : undefined);
            }}
          />
          <DialogoDecidir
            acoes={modal.aberto && modal.tipo === 'negar' ? ['negar'] : null}
            ocupado={d.bloqueada}
            acaoEmAndamento={d.meuAndamento}
            onFechar={aoFechar}
            onEscolher={(a) => {
              aoFechar();
              p.onAcao(req, a);
            }}
          />
        </>
      ) : null}
      {d.mostrarMais ? (
        <Dialogo
          aberto={modal.aberto && modal.tipo === 'mais'}
          role="dialog"
          comBotaoFechar
          bloqueado={d.emCurso}
          titulo="Mais ações"
          descricao={`Requisição de ${req.nome_solicitante}`}
          onFechar={aoFechar}
        >
          <BotoesAcao
            req={req}
            ocultar={d.ocultar}
            ocupado={d.bloqueada}
            onAcao={p.onAcao}
            onOrganizar={p.onOrganizar}
            arquivando={d.arquivando}
            acaoEmAndamento={d.meuAndamento}
            aoRegistrarChecklist={p.aoRegistrarChecklist}
            motoristas={p.motoristas}
            motoristaPadrao={p.motoristaPadrao}
          />
        </Dialogo>
      ) : null}
    </>
  );
}

// Contexto estável: a célula não é recriada a cada mudança de props.
type PropsCelula = PropsAcoes & { abrir: (id: string, tipo: TipoModal) => void };
const ContextoAcoes = React.createContext<PropsCelula | null>(null);

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

  const propsAcoes: PropsAcoes = {
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

  // Estado dos diálogos (um conjunto só). Ao terminar a chamada eles fecham, exceto o de Autorizar
  // com erro da API: esse fica aberto (motorista preservado) e mostra a mensagem dentro dele.
  const [modal, setModal] = React.useState<Modal>(null);
  // Só mostra o erro depois de uma tentativa feita neste modal (evita erro antigo de outra ação).
  const [tentou, setTentou] = React.useState(false);
  const reqModal = modal ? itens.find((r) => r.id === modal.id) : undefined;
  const emCursoModal = reqModal ? derivar(reqModal, propsAcoes).emCurso : false;
  const [emCursoAntes, setEmCursoAntes] = React.useState(emCursoModal);
  const fechar = () => setModal((m) => (m ? { ...m, aberto: false } : m));
  if (emCursoAntes !== emCursoModal) {
    setEmCursoAntes(emCursoModal);
    if (emCursoAntes && !(modal?.tipo === 'autorizar' && erroAcao)) fechar();
  }
  // A linha saiu da lista (arquivada, recarregada): o diálogo some junto.
  if (modal && !reqModal) setModal(null);
  const abrir = (id: string, tipo: TipoModal) => {
    setTentou(false);
    setModal({ id, tipo, aberto: true });
  };
  const contexto: PropsCelula = { ...propsAcoes, abrir };

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
      {modal && reqModal ? (
        <DialogosDaTabela
          {...propsAcoes}
          req={reqModal}
          modal={modal}
          tentou={tentou}
          aoTentar={() => setTentou(true)}
          aoFechar={fechar}
        />
      ) : null}
    </ContextoAcoes.Provider>
  );
}
