'use client';

import * as React from 'react';
import Link from 'next/link';
import { ACOES, api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { obterPosicao } from '@/lib/geolocalizacao';
import { dataCurta, numero } from '@/lib/formato';
import {
  ORDEM_GRUPOS,
  type Acao,
  type AcaoOrganizar,
  type CampoAcao,
  type CampoKm,
  type CorpoAcao,
  type GrupoAcao,
  type Motorista,
  type Requisicao,
} from '@/lib/tipos';
import { Avatar } from '@/components/avatar';
import { LinhaDoTempo } from '@/components/linha-do-tempo';
import { ChecklistRegistrado } from '@/components/checklist-registrado';
import { FormularioChecklist } from '@/components/formulario-checklist';
import { ItemArrastavel, type PapelGesto } from '@/components/item-arrastavel';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Dialogo } from '@/components/ui/dialogo';
import { Input, Select } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { HoraSaida } from '@/components/ui/hora-saida';
import { StatusChip } from '@/components/ui/status-chip';

type OnAcao = (req: Requisicao, acao: Acao, corpo?: CorpoAcao) => void;
export type OnOrganizar = (req: Requisicao, acao: AcaoOrganizar) => void;

// Só apresentação: rótulo (para leitor de tela) de cada categoria de `grupos_acao`.
const ROTULO_GRUPO: Record<GrupoAcao, string> = {
  solicitar: 'Solicitação',
  decidir: 'Decisão',
  operar: 'Registro da viagem',
  designar: 'Motorista',
  consultar: 'Consulta',
  organizar: 'Organizar a lista',
};

const ROTULO_ORGANIZAR: Record<AcaoOrganizar, string> = {
  arquivar: 'Arquivar',
  desarquivar: 'Desarquivar',
};

/** Só apresentação: texto do botão enquanto a API responde à ação (`acaoEmAndamento`). */
export const ROTULO_ANDAMENTO: Partial<Record<Acao, string>> = {
  autorizar: 'Autorizando...',
  negar: 'Negando...',
  cancelar: 'Cancelando...',
  registrar_saida: 'Registrando saída...',
  registrar_chegada: 'Registrando chegada...',
};

export const ehOrganizar = (a: string): a is AcaoOrganizar =>
  a === 'arquivar' || a === 'desarquivar';

// Negar e cancelar sempre passam por diálogo de confirmação.
type Decisao = Extract<Acao, 'negar' | 'cancelar'>;
const ehDecisao = (a: string): a is Decisao => a === 'negar' || a === 'cancelar';

const ROTULO_CAMPO: Record<CampoKm, string> = {
  km_saida: 'Km de saída',
  km_chegada: 'Km de chegada',
};

/**
 * Ação que a API manda acompanhar de campos (campos_acao). Só os campos listados são exibidos
 * e enviados; o servidor valida tudo (obrigatoriedade, chegada >= saída, limites). Quando a API
 * lista latitude/longitude, a posição do aparelho é lida ao confirmar e vai no corpo; sem a
 * posição nada é enviado (a regra e o erro final são da API).
 */
function AcaoComCampos({
  req,
  acao,
  campos,
  ocupado,
  emAndamento,
  onAcao,
}: {
  req: Requisicao;
  acao: Acao;
  campos: CampoAcao[];
  ocupado: boolean;
  emAndamento?: boolean;
  onAcao: OnAcao;
}) {
  const [valores, setValores] = React.useState<Partial<Record<CampoKm, string>>>({});
  const [obtendo, setObtendo] = React.useState(false);
  const [erroLocal, setErroLocal] = React.useState<string | null>(null);
  const camposKm = campos.filter((c): c is CampoKm => c in ROTULO_CAMPO);
  const exigeLocal = campos.some((c) => c === 'latitude' || c === 'longitude');
  const enviar = async (e: React.FormEvent) => {
    e.preventDefault();
    if (obtendo || emAndamento) return;
    const corpo: CorpoAcao = {};
    for (const c of camposKm) {
      const v = valores[c] ?? '';
      if (v !== '') corpo[c] = Number(v);
    }
    if (exigeLocal) {
      setErroLocal(null);
      setObtendo(true);
      try {
        Object.assign(corpo, await obterPosicao());
      } catch (err) {
        setErroLocal(
          err instanceof Error ? err.message : 'Não foi possível obter sua localização.',
        );
        setObtendo(false);
        return;
      }
      setObtendo(false);
    }
    onAcao(req, acao, corpo);
  };
  return (
    <form onSubmit={(e) => void enviar(e)} className="flex flex-col gap-3">
      {camposKm.map((c) => {
        const id = `${req.id}-${acao}-${c}`;
        return (
          <div key={c}>
            <Label htmlFor={id}>{ROTULO_CAMPO[c]}</Label>
            <Input
              id={id}
              name={c}
              type="number"
              inputMode="numeric"
              min={0}
              step={1}
              required
              value={valores[c] ?? ''}
              onChange={(e) => setValores((v) => ({ ...v, [c]: e.target.value }))}
            />
          </div>
        );
      })}
      {exigeLocal ? (
        <>
          <p className="text-sm text-texto-2">
            Sua localização será registrada neste momento e visível apenas ao administrador.
          </p>
          <p role="status" aria-live="polite" className="text-sm text-texto-2">
            {obtendo ? 'Obtendo localização...' : ''}
          </p>
          {erroLocal ? <Alerta>{erroLocal}</Alerta> : null}
        </>
      ) : null}
      <Button
        type="submit"
        tamanho="cheio"
        variante={ACOES[acao].variante}
        disabled={ocupado}
        carregando={obtendo || emAndamento}
      >
        {obtendo
          ? 'Obtendo localização...'
          : emAndamento
            ? (ROTULO_ANDAMENTO[acao] ?? ACOES[acao].rotulo)
            : erroLocal
              ? 'Tentar novamente'
              : ACOES[acao].rotulo}
      </Button>
    </form>
  );
}

/**
 * Troca de motorista (ação `atribuir_motorista` da API). Os candidatos vêm de GET /api/motoristas
 * e a validação é do servidor; o erro é exibido como veio.
 */
function TrocarMotorista({
  req,
  ocupado,
  aoAtualizar,
}: {
  req: Requisicao;
  ocupado: boolean;
  aoAtualizar?: () => void | Promise<void>;
}) {
  const { tratarErro } = useAuth();
  const [candidatos, setCandidatos] = React.useState<Motorista[]>([]);
  const [escolhido, setEscolhido] = React.useState(req.motorista?.id ?? '');
  const [enviando, setEnviando] = React.useState(false);
  const [erro, setErro] = React.useState<string | null>(null);
  React.useEffect(() => {
    let ativo = true;
    api
      .motoristas()
      .then((r) => ativo && setCandidatos(r.itens))
      .catch((e) => ativo && setErro(tratarErro(e)));
    return () => {
      ativo = false;
    };
  }, [tratarErro]);
  const enviar = async (e: React.FormEvent) => {
    e.preventDefault();
    if (enviando) return;
    setEnviando(true);
    setErro(null);
    try {
      await api.atribuirMotorista(req.id, escolhido);
    } catch (err) {
      setErro(tratarErro(err));
    }
    try {
      await aoAtualizar?.();
    } finally {
      setEnviando(false);
    }
  };
  const id = `${req.id}-trocar-motorista`;
  return (
    <form onSubmit={(e) => void enviar(e)} className="flex flex-col gap-3">
      <div>
        <Label htmlFor={id}>{req.motorista ? 'Trocar motorista' : 'Atribuir motorista'}</Label>
        <Select
          id={id}
          required
          value={escolhido}
          disabled={ocupado || enviando}
          onChange={(e) => setEscolhido(e.target.value)}
        >
          <option value="">Selecione um motorista</option>
          {candidatos.map((m) => (
            <option key={m.id} value={m.id}>
              {m.nome}
            </option>
          ))}
        </Select>
      </div>
      {erro ? <Alerta>{erro}</Alerta> : null}
      <Button
        type="submit"
        tamanho="cheio"
        variante="contorno"
        disabled={ocupado}
        carregando={enviando}
      >
        {enviando ? 'Salvando...' : 'Salvar motorista'}
      </Button>
    </form>
  );
}

/** Confirmação de negar/cancelar: só as ações que a API liberou, mais "Voltar". */
function DialogoDecidir({
  acoes,
  ocupado,
  acaoEmAndamento,
  onEscolher,
  onFechar,
}: {
  acoes: Decisao[] | null;
  ocupado: boolean;
  acaoEmAndamento?: Acao | null;
  onEscolher: (a: Decisao) => void;
  onFechar: () => void;
}) {
  const lista = acoes ?? [];
  const titulo =
    lista.length === 1
      ? lista[0] === 'negar'
        ? 'Negar esta requisição?'
        : 'Cancelar esta requisição?'
      : 'Negar ou cancelar esta requisição?';
  return (
    <Dialogo
      aberto={lista.length > 0}
      titulo={titulo}
      descricao="Esta ação não pode ser desfeita."
      onFechar={onFechar}
    >
      <div className="flex flex-col gap-3">
        {lista.map((a) => (
          <Button
            key={a}
            type="button"
            tamanho="cheio"
            variante={ACOES[a].variante}
            disabled={ocupado}
            carregando={acaoEmAndamento === a}
            onClick={() => onEscolher(a)}
          >
            {acaoEmAndamento === a ? (ROTULO_ANDAMENTO[a] ?? ACOES[a].rotulo) : ACOES[a].rotulo}
          </Button>
        ))}
        <Button type="button" tamanho="cheio" variante="contorno" data-autofocus onClick={onFechar}>
          Voltar
        </Button>
      </div>
    </Dialogo>
  );
}

function EscolhaMotorista({
  motoristas,
  padrao,
  ocupado,
  onConfirmar,
  onFechar,
}: {
  motoristas: Motorista[];
  padrao: string | null;
  ocupado: boolean;
  onConfirmar: (motoristaId: string) => void;
  onFechar: () => void;
}) {
  const [valor, setValor] = React.useState(padrao ?? '');
  const id = React.useId();
  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        onConfirmar(valor);
      }}
    >
      <div>
        <Label htmlFor={id}>Motorista</Label>
        <Select id={id} value={valor} onChange={(e) => setValor(e.target.value)}>
          <option value="">Sem motorista designado</option>
          {motoristas.map((m) => (
            <option key={m.id} value={m.id}>
              {m.nome}
            </option>
          ))}
        </Select>
      </div>
      <Button type="submit" tamanho="cheio" disabled={ocupado}>
        {ACOES.autorizar.rotulo}
      </Button>
      <Button type="button" tamanho="cheio" variante="contorno" onClick={onFechar}>
        Voltar
      </Button>
    </form>
  );
}

/** Seleção do motorista ao autorizar por gesto (a API informou mais de um candidato). */
function DialogoAutorizar({
  aberto,
  motoristas,
  padrao,
  ocupado,
  onConfirmar,
  onFechar,
}: {
  aberto: boolean;
  motoristas: Motorista[];
  padrao: string | null;
  ocupado: boolean;
  onConfirmar: (motoristaId: string) => void;
  onFechar: () => void;
}) {
  return (
    <Dialogo
      aberto={aberto}
      role="dialog"
      titulo="Autorizar requisição"
      descricao="Escolha o motorista desta viagem."
      onFechar={onFechar}
    >
      <EscolhaMotorista
        motoristas={motoristas}
        padrao={padrao}
        ocupado={ocupado}
        onConfirmar={onConfirmar}
        onFechar={onFechar}
      />
    </Dialogo>
  );
}

/**
 * Botões de ação: um por item de acoes_permitidas devolvido pela API (nada além disso),
 * agrupados por `grupos_acao` na ordem fixa solicitar, decidir, operar, designar, consultar e
 * organizar. Sem `grupos_acao` (API antiga), tudo num bloco só.
 */
export function BotoesAcao({
  req,
  ocupado,
  onAcao,
  onOrganizar,
  aoRegistrarChecklist,
  motoristas,
  motoristaPadrao,
  arquivando,
  acaoEmAndamento,
}: {
  req: Requisicao;
  ocupado: boolean;
  onAcao: OnAcao;
  /** Ação em andamento na API (vem do pai): o botão correspondente mostra spinner e o gerúndio. */
  acaoEmAndamento?: Acao | null;
  /** Arquivar/desarquivar em andamento: o botão mostra o spinner e fica desabilitado. */
  arquivando?: boolean;
  /** Arquivar/desarquivar; sem isto (ex.: telas de detalhe) esses botões não são exibidos. */
  onOrganizar?: OnOrganizar;
  /** Recarrega a requisição após o checklist (o botão volta) ou a troca de motorista. */
  aoRegistrarChecklist?: () => void | Promise<void>;
  /** Candidatos devolvidos por GET /api/motoristas; sem isto vale `campos_acao.autorizar`. */
  motoristas?: Motorista[];
  /** `padrao` de GET /api/motoristas: vem pré-selecionado até o usuário escolher. */
  motoristaPadrao?: string | null;
}) {
  // null = o usuário ainda não mexeu; vale o padrão da API (que pode chegar depois da montagem).
  const [escolha, setEscolha] = React.useState<string | null>(null);
  const [pedido, setPedido] = React.useState<Decisao[] | null>(null);
  const campoAutorizar = req.campos_acao?.autorizar;
  const candidatos =
    motoristas && motoristas.length > 0 ? motoristas : (campoAutorizar?.motoristas ?? []);
  const padrao = motoristaPadrao ?? campoAutorizar?.motorista_padrao ?? null;
  const motoristaId = escolha ?? padrao ?? '';
  const permitidas = req.acoes_permitidas;
  const temOrganizar = !!onOrganizar && permitidas.some(ehOrganizar);
  const podeTrocarMotorista = permitidas.includes('atribuir_motorista');
  if (!permitidas.some((a) => a in ACOES) && !podeTrocarMotorista && !temOrganizar) return null;

  const camposDe = (a: Acao) => req.campos_acao?.[a]?.campos_obrigatorios ?? [];
  // Ação com checklist pendente: o formulário vem antes e o botão só reaparece depois dele.
  const pendente = (a: Acao) => req.campos_acao?.[a]?.checklist_pendente;
  const enviar: OnAcao = (rq, a, c) =>
    onAcao(rq, a, a === 'autorizar' && motoristaId ? { ...c, motorista_id: motoristaId } : c);

  function bloco(lista: string[]): React.ReactNode[] {
    const acoes = lista.filter((a): a is Acao => a in ACOES);
    const comChecklist = acoes.filter((a) => pendente(a));
    const liberadas = acoes.filter((a) => !pendente(a));
    const comCampos = liberadas.filter((a) => camposDe(a).length > 0);
    // Negar vem antes de Autorizar no DOM: a ordem de foco é a visual (mockup 04), sem CSS order.
    const simples = liberadas
      .filter((a) => camposDe(a).length === 0)
      .sort((x, y) => Number(y === 'negar') - Number(x === 'negar'));
    const unica = simples.length === 1;
    // O seletor só acompanha a ação autorizar (que a API liberou); a escolha é opcional e o
    // servidor valida o motorista.
    const comSeletor = simples.includes('autorizar') && candidatos.length > 0;
    const nos: React.ReactNode[] = [];
    if (comSeletor) {
      nos.push(
        <div key="seletor">
          <Label htmlFor={`${req.id}-motorista`}>
            {padrao ? 'Motorista' : 'Motorista (opcional)'}
          </Label>
          <Select
            id={`${req.id}-motorista`}
            value={motoristaId}
            disabled={ocupado}
            onChange={(e) => setEscolha(e.target.value)}
          >
            <option value="">Sem motorista designado</option>
            {candidatos.map((m) => (
              <option key={m.id} value={m.id}>
                {m.nome}
              </option>
            ))}
          </Select>
        </div>,
      );
    }
    if (lista.includes('atribuir_motorista')) {
      nos.push(
        <TrocarMotorista
          key={`troca:${req.id}:${req.motorista?.id ?? '-'}`}
          req={req}
          ocupado={ocupado}
          aoAtualizar={aoRegistrarChecklist}
        />,
      );
    }
    for (const a of comChecklist) {
      const m = pendente(a);
      if (!m) continue;
      nos.push(
        <FormularioChecklist
          key={`${req.id}:${m}`}
          requisicaoId={req.id}
          momento={m}
          aoRegistrar={() => aoRegistrarChecklist?.()}
        />,
      );
    }
    for (const a of comCampos) {
      nos.push(
        <AcaoComCampos
          key={`${a}:${camposDe(a).join(',')}`}
          req={req}
          acao={a}
          campos={camposDe(a)}
          ocupado={ocupado}
          emAndamento={acaoEmAndamento === a}
          onAcao={onAcao}
        />,
      );
    }
    if (simples.length > 0) {
      nos.push(
        <div key="simples" className={unica ? '' : 'grid grid-cols-2 gap-3'}>
          {simples.map((a) => (
            <Button
              key={a}
              type="button"
              tamanho="cheio"
              variante={ACOES[a].variante}
              disabled={ocupado}
              carregando={acaoEmAndamento === a}
              onClick={() => (ehDecisao(a) ? setPedido([a]) : enviar(req, a))}
            >
              {acaoEmAndamento === a ? (ROTULO_ANDAMENTO[a] ?? ACOES[a].rotulo) : ACOES[a].rotulo}
            </Button>
          ))}
        </div>,
      );
    }
    const organizar = onOrganizar ? lista.filter(ehOrganizar) : [];
    for (const a of organizar) {
      nos.push(
        <Button
          key={a}
          type="button"
          tamanho="cheio"
          variante="contorno"
          disabled={ocupado}
          carregando={arquivando}
          onClick={() => onOrganizar?.(req, a)}
        >
          {arquivando
            ? a === 'arquivar'
              ? 'Arquivando...'
              : 'Desarquivando...'
            : ROTULO_ORGANIZAR[a]}
        </Button>,
      );
    }
    return nos;
  }

  const grupos = req.grupos_acao;
  const blocos = grupos
    ? ORDEM_GRUPOS.map((g) => {
        const lista = (grupos[g] ?? []).filter((a) => permitidas.includes(a));
        const nos = bloco(lista);
        return nos.length === 0 ? null : (
          <div
            key={g}
            role="group"
            aria-label={ROTULO_GRUPO[g]}
            data-grupo={g}
            className={
              g === 'organizar'
                ? 'flex flex-col gap-3 border-t border-borda pt-3'
                : 'flex flex-col gap-3'
            }
          >
            {nos}
          </div>
        );
      })
    : bloco(permitidas);

  return (
    <div className="mt-4 flex flex-col gap-3">
      {blocos}
      <DialogoDecidir
        acoes={pedido}
        ocupado={ocupado}
        acaoEmAndamento={acaoEmAndamento}
        onFechar={() => setPedido(null)}
        onEscolher={(a) => {
          setPedido(null);
          onAcao(req, a);
        }}
      />
    </div>
  );
}

/** Muda quando o checklist pendente some ou a requisição avança: dispara nova leitura (GET). */
export function versaoChecklist(req: Requisicao): string {
  const p = (a: Acao) => req.campos_acao?.[a]?.checklist_pendente ?? '-';
  return `${req.status}:${req.atualizado_em}:${p('registrar_saida')}:${p('registrar_chegada')}`;
}

export function CartaoRequisicao({
  req,
  mostrarSolicitante,
  ocupado,
  onAcao,
  onOrganizar,
  gestos,
  aoRegistrarChecklist,
  mostrarChecklist,
  motoristas,
  motoristaPadrao,
  arquivando,
  acaoEmAndamento,
}: {
  req: Requisicao;
  mostrarSolicitante?: boolean;
  /** Ação em andamento na API (passada pela página): spinner nos botões e cobertura no swipe. */
  acaoEmAndamento?: Acao | null;
  ocupado: boolean;
  onAcao: OnAcao;
  onOrganizar?: OnOrganizar;
  /** Arquivar/desarquivar em andamento (vem de `processando` do hook): cobertura e spinner. */
  arquivando?: boolean;
  /** Liga os gestos de arrastar (só dos lados cuja ação a API liberou em acoes_permitidas). */
  gestos?: boolean;
  aoRegistrarChecklist?: () => void | Promise<void>;
  mostrarChecklist?: boolean;
  motoristas?: Motorista[];
  motoristaPadrao?: string | null;
}) {
  const [dialogo, setDialogo] = React.useState<'decidir' | 'autorizar' | null>(null);
  const permitidas = req.acoes_permitidas;
  const decisoes = permitidas.filter(ehDecisao);
  const campoAutorizar = req.campos_acao?.autorizar;
  const candidatos =
    motoristas && motoristas.length > 0 ? motoristas : (campoAutorizar?.motoristas ?? []);
  const padrao = motoristaPadrao ?? campoAutorizar?.motorista_padrao ?? null;
  const autorizar = (motoristaId: string) =>
    onAcao(req, 'autorizar', motoristaId ? { motorista_id: motoristaId } : undefined);

  let direita: PapelGesto | undefined;
  let esquerda: PapelGesto | undefined;
  if (gestos) {
    if (permitidas.includes('autorizar')) {
      direita = {
        rotulo: ACOES.autorizar.rotulo,
        cor: 'primaria',
        // Mais de um candidato: o usuário escolhe no diálogo; com um só, ele já vai pré-selecionado.
        onAcionar: () =>
          candidatos.length > 1 ? setDialogo('autorizar') : autorizar(padrao ?? ''),
      };
    } else if (onOrganizar && permitidas.includes('arquivar')) {
      direita = {
        rotulo: ROTULO_ORGANIZAR.arquivar,
        cor: 'neutra',
        onAcionar: () => onOrganizar(req, 'arquivar'),
      };
    }
    if (onOrganizar && permitidas.includes('desarquivar')) {
      esquerda = {
        rotulo: ROTULO_ORGANIZAR.desarquivar,
        cor: 'neutra',
        onAcionar: () => onOrganizar(req, 'desarquivar'),
      };
    } else if (decisoes.length > 0) {
      esquerda = {
        rotulo: decisoes.length === 1 ? ACOES[decisoes[0]!].rotulo : 'Negar ou cancelar',
        cor: 'erro',
        onAcionar: () => setDialogo('decidir'),
      };
    }
  }

  return (
    <ItemArrastavel
      className="rounded-xl border border-borda bg-white"
      classeInterna="gap-0 p-4"
      ocupado={ocupado}
      processando={
        arquivando
          ? permitidas.includes('arquivar')
            ? 'Arquivando...'
            : 'Desarquivando...'
          : acaoEmAndamento
            ? ROTULO_ANDAMENTO[acaoEmAndamento]
            : undefined
      }
      direita={direita}
      esquerda={esquerda}
      limiar={permitidas.includes('autorizar') ? 0.5 : undefined}
    >
      <Link
        href={`/requisicoes/detalhe?id=${encodeURIComponent(req.id)}`}
        className="block rounded-padrao"
      >
        <div className="flex items-start justify-between gap-3">
          {mostrarSolicitante ? (
            <p className="flex min-w-0 items-center gap-2 text-lg font-semibold">
              <Avatar nome={req.nome_solicitante} fotoUrl={req.solicitante_foto_url} decorativo />
              <span className="min-w-0 break-words">{req.nome_solicitante}</span>
            </p>
          ) : (
            <p className="text-lg font-semibold">
              <HoraSaida texto={dataCurta(req.hora_saida)} situacao={req.situacao_horario} />
            </p>
          )}
          <StatusChip status={req.status} />
        </div>
        {mostrarSolicitante ? (
          <p className="mt-1 text-texto-2">
            {req.unidade} ·{' '}
            <HoraSaida texto={dataCurta(req.hora_saida)} situacao={req.situacao_horario} />
          </p>
        ) : null}
        <p className="mt-2 text-base break-words">{req.trajeto}</p>
        {!mostrarSolicitante ? <p className="mt-1 text-sm text-texto-2">{req.unidade}</p> : null}
        {req.km_saida !== null && req.km_saida !== undefined ? (
          <p className="mt-1 text-sm text-texto-2">Km de saída: {numero(req.km_saida)}</p>
        ) : null}
        {req.km_chegada !== null && req.km_chegada !== undefined ? (
          <p className="mt-1 text-sm text-texto-2">Km de chegada: {numero(req.km_chegada)}</p>
        ) : null}
        {req.motorista ? (
          <p className="mt-1 text-sm text-texto-2">Motorista: {req.motorista.nome}</p>
        ) : null}
        {mostrarSolicitante && req.observacao ? (
          <p className="mt-1 text-texto-2">Obs.: {req.observacao}</p>
        ) : null}
        <LinhaDoTempo linhaDoTempo={req.linha_do_tempo} variante="compacta" className="mt-3" />
      </Link>
      {mostrarChecklist && permitidas.includes('ver_checklist') ? (
        <ChecklistRegistrado requisicaoId={req.id} versao={versaoChecklist(req)} />
      ) : null}
      <BotoesAcao
        req={req}
        ocupado={ocupado}
        onAcao={onAcao}
        onOrganizar={onOrganizar}
        arquivando={arquivando}
        acaoEmAndamento={acaoEmAndamento}
        aoRegistrarChecklist={aoRegistrarChecklist}
        motoristas={motoristas}
        motoristaPadrao={motoristaPadrao}
      />
      <DialogoDecidir
        acoes={dialogo === 'decidir' ? decisoes : null}
        ocupado={ocupado}
        acaoEmAndamento={acaoEmAndamento}
        onFechar={() => setDialogo(null)}
        onEscolher={(a) => {
          setDialogo(null);
          onAcao(req, a);
        }}
      />
      <DialogoAutorizar
        aberto={dialogo === 'autorizar'}
        motoristas={candidatos}
        padrao={padrao}
        ocupado={ocupado}
        onFechar={() => setDialogo(null)}
        onConfirmar={(id) => {
          setDialogo(null);
          autorizar(id);
        }}
      />
    </ItemArrastavel>
  );
}
