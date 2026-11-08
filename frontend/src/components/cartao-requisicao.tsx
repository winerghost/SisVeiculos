'use client';

import * as React from 'react';
import Link from 'next/link';
import { ACOES } from '@/lib/api';
import { dataCurta, numero } from '@/lib/formato';
import type { Acao, CampoAcao, CorpoAcao, Requisicao } from '@/lib/tipos';
import { Avatar } from '@/components/avatar';
import { ChecklistRegistrado } from '@/components/checklist-registrado';
import { FormularioChecklist } from '@/components/formulario-checklist';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { StatusChip } from '@/components/ui/status-chip';

type OnAcao = (req: Requisicao, acao: Acao, corpo?: CorpoAcao) => void;

const ROTULO_CAMPO: Record<CampoAcao, string> = {
  km_saida: 'Km de saída',
  km_chegada: 'Km de chegada',
};

/**
 * Ação que a API manda acompanhar de campos (campos_acao). Só os campos listados são exibidos
 * e enviados; o servidor valida tudo (obrigatoriedade, chegada >= saída, limites).
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
  const [valores, setValores] = React.useState<Partial<Record<CampoAcao, string>>>({});
  const enviar = (e: React.FormEvent) => {
    e.preventDefault();
    const corpo: CorpoAcao = {};
    for (const c of campos) {
      const v = valores[c] ?? '';
      if (v !== '') corpo[c] = Number(v);
    }
    onAcao(req, acao, corpo);
  };
  return (
    <form onSubmit={enviar} className="flex flex-col gap-3">
      {campos.map((c) => {
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
      <Button type="submit" tamanho="cheio" variante={ACOES[acao].variante} disabled={ocupado}>
        {ACOES[acao].rotulo}
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
}: {
  req: Requisicao;
  ocupado: boolean;
  onAcao: OnAcao;
  /** Recarrega a requisição após o checklist: o `checklist_pendente` some e o botão volta. */
  aoRegistrarChecklist?: () => void | Promise<void>;
}) {
  const acoes = req.acoes_permitidas.filter((a): a is Acao => a in ACOES);
  if (acoes.length === 0) return null;
  const camposDe = (a: Acao) => req.campos_acao?.[a]?.campos_obrigatorios ?? [];
  // Ação com checklist pendente: o formulário vem antes e o botão só reaparece depois dele.
  const pendente = (a: Acao) => req.campos_acao?.[a]?.checklist_pendente;
  const comChecklist = acoes.filter((a) => pendente(a));
  const liberadas = acoes.filter((a) => !pendente(a));
  const comCampos = liberadas.filter((a) => camposDe(a).length > 0);
  const simples = liberadas.filter((a) => camposDe(a).length === 0);
  const unica = simples.length === 1;
  return (
    <div className="mt-4 flex flex-col gap-3">
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
              onClick={() => onAcao(req, a)}
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
}: {
  req: Requisicao;
  mostrarSolicitante?: boolean;
  ocupado: boolean;
  onAcao: OnAcao;
  aoRegistrarChecklist?: () => void | Promise<void>;
  mostrarChecklist?: boolean;
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
        {mostrarSolicitante && req.observacao ? (
          <p className="mt-1 text-texto-2">Obs.: {req.observacao}</p>
        ) : null}
      </Link>
      {mostrarChecklist ? (
        <ChecklistRegistrado requisicaoId={req.id} versao={versaoChecklist(req)} />
      ) : null}
      <BotoesAcao
        req={req}
        ocupado={ocupado}
        onAcao={onAcao}
        aoRegistrarChecklist={aoRegistrarChecklist}
      />
    </li>
  );
}
