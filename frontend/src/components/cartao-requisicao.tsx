'use client';

import * as React from 'react';
import Link from 'next/link';
import { ACOES, api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { obterPosicao } from '@/lib/geolocalizacao';
import { dataCurta, numero } from '@/lib/formato';
import type { Acao, CampoAcao, CampoKm, CorpoAcao, Motorista, Requisicao } from '@/lib/tipos';
import { Avatar } from '@/components/avatar';
import { LinhaDoTempo } from '@/components/linha-do-tempo';
import { ChecklistRegistrado } from '@/components/checklist-registrado';
import { FormularioChecklist } from '@/components/formulario-checklist';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input, Select } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { StatusChip } from '@/components/ui/status-chip';

type OnAcao = (req: Requisicao, acao: Acao, corpo?: CorpoAcao) => void;

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
  onAcao,
}: {
  req: Requisicao;
  acao: Acao;
  campos: CampoAcao[];
  ocupado: boolean;
  onAcao: OnAcao;
}) {
  const [valores, setValores] = React.useState<Partial<Record<CampoKm, string>>>({});
  const [obtendo, setObtendo] = React.useState(false);
  const [erroLocal, setErroLocal] = React.useState<string | null>(null);
  const camposKm = campos.filter((c): c is CampoKm => c in ROTULO_CAMPO);
  const exigeLocal = campos.some((c) => c === 'latitude' || c === 'longitude');
  const enviar = async (e: React.FormEvent) => {
    e.preventDefault();
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
        disabled={ocupado || obtendo}
      >
        {erroLocal ? 'Tentar novamente' : ACOES[acao].rotulo}
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
      <Button type="submit" tamanho="cheio" variante="contorno" disabled={ocupado || enviando}>
        Salvar motorista
      </Button>
    </form>
  );
}

/** Botões de ação: um por item de acoes_permitidas devolvido pela API (nada além disso). */
export function BotoesAcao({
  req,
  ocupado,
  onAcao,
  aoRegistrarChecklist,
  motoristas,
  motoristaPadrao,
}: {
  req: Requisicao;
  ocupado: boolean;
  onAcao: OnAcao;
  /** Recarrega a requisição após o checklist (o botão volta) ou a troca de motorista. */
  aoRegistrarChecklist?: () => void | Promise<void>;
  /** Candidatos devolvidos por GET /api/motoristas; vazio/ausente = sem seletor. */
  motoristas?: Motorista[];
  /** `padrao` devolvido por GET /api/motoristas: vem pré-selecionado até o usuário escolher. */
  motoristaPadrao?: string | null;
}) {
  // null = o usuário ainda não mexeu; vale o padrão da API (que pode chegar depois da montagem).
  const [escolha, setEscolha] = React.useState<string | null>(null);
  const motoristaId = escolha ?? motoristaPadrao ?? '';
  const acoes = req.acoes_permitidas.filter((a): a is Acao => a in ACOES);
  const podeTrocarMotorista = req.acoes_permitidas.includes('atribuir_motorista');
  if (acoes.length === 0 && !podeTrocarMotorista) return null;
  const camposDe = (a: Acao) => req.campos_acao?.[a]?.campos_obrigatorios ?? [];
  // Ação com checklist pendente: o formulário vem antes e o botão só reaparece depois dele.
  const pendente = (a: Acao) => req.campos_acao?.[a]?.checklist_pendente;
  const comChecklist = acoes.filter((a) => pendente(a));
  const liberadas = acoes.filter((a) => !pendente(a));
  const comCampos = liberadas.filter((a) => camposDe(a).length > 0);
  const simples = liberadas.filter((a) => camposDe(a).length === 0);
  const unica = simples.length === 1;
  // O seletor só acompanha a ação autorizar (que a API liberou); a escolha é opcional e o
  // servidor valida o motorista.
  const comSeletor = simples.includes('autorizar') && !!motoristas && motoristas.length > 0;
  const enviar: OnAcao = (rq, a, c) =>
    onAcao(rq, a, a === 'autorizar' && motoristaId ? { ...c, motorista_id: motoristaId } : c);
  return (
    <div className="mt-4 flex flex-col gap-3">
      {comSeletor ? (
        <div>
          <Label htmlFor={`${req.id}-motorista`}>
            {motoristaPadrao ? 'Motorista' : 'Motorista (opcional)'}
          </Label>
          <Select
            id={`${req.id}-motorista`}
            value={motoristaId}
            disabled={ocupado}
            onChange={(e) => setEscolha(e.target.value)}
          >
            <option value="">Sem motorista designado</option>
            {motoristas.map((m) => (
              <option key={m.id} value={m.id}>
                {m.nome}
              </option>
            ))}
          </Select>
        </div>
      ) : null}
      {podeTrocarMotorista ? (
        <TrocarMotorista
          key={`${req.id}:${req.motorista?.id ?? '-'}`}
          req={req}
          ocupado={ocupado}
          aoAtualizar={aoRegistrarChecklist}
        />
      ) : null}
      {comChecklist.map((a) => {
        const m = pendente(a);
        return m ? (
          <FormularioChecklist
            key={`${req.id}:${m}`}
            requisicaoId={req.id}
            momento={m}
            aoRegistrar={() => aoRegistrarChecklist?.()}
          />
        ) : null;
      })}
      {comCampos.map((a) => (
        <AcaoComCampos
          key={`${a}:${camposDe(a).join(',')}`}
          req={req}
          acao={a}
          campos={camposDe(a)}
          ocupado={ocupado}
          onAcao={onAcao}
        />
      ))}
      {simples.length > 0 ? (
        <div className={unica ? '' : 'grid grid-cols-2 gap-3'}>
          {simples.map((a) => (
            <Button
              key={a}
              type="button"
              tamanho="cheio"
              variante={ACOES[a].variante}
              disabled={ocupado}
              onClick={() => enviar(req, a)}
              className={unica ? '' : a === 'negar' ? 'order-1' : 'order-2'}
            >
              {ACOES[a].rotulo}
            </Button>
          ))}
        </div>
      ) : null}
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
  aoRegistrarChecklist,
  mostrarChecklist,
  motoristas,
  motoristaPadrao,
}: {
  req: Requisicao;
  mostrarSolicitante?: boolean;
  ocupado: boolean;
  onAcao: OnAcao;
  aoRegistrarChecklist?: () => void | Promise<void>;
  mostrarChecklist?: boolean;
  motoristas?: Motorista[];
  motoristaPadrao?: string | null;
}) {
  return (
    <li className="rounded-xl border border-borda bg-white p-4">
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
            <p className="text-lg font-semibold">{dataCurta(req.hora_saida)}</p>
          )}
          <StatusChip status={req.status} />
        </div>
        {mostrarSolicitante ? (
          <p className="mt-1 text-texto-2">
            {req.unidade} · {dataCurta(req.hora_saida)}
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
      {mostrarChecklist ? (
        <ChecklistRegistrado requisicaoId={req.id} versao={versaoChecklist(req)} />
      ) : null}
      <BotoesAcao
        req={req}
        ocupado={ocupado}
        onAcao={onAcao}
        aoRegistrarChecklist={aoRegistrarChecklist}
        motoristas={motoristas}
        motoristaPadrao={motoristaPadrao}
      />
    </li>
  );
}
