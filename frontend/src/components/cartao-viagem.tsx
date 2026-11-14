'use client';

import Link from 'next/link';
import { dataCurta, dataHora, numero } from '@/lib/formato';
import type { Acao, CorpoAcao, Requisicao } from '@/lib/tipos';
import { BotoesAcao, ROTULO_ANDAMENTO, type OnOrganizar } from '@/components/cartao-requisicao';
import { ItemArrastavel, type PapelGesto } from '@/components/item-arrastavel';
import { LinhaDoTempo } from '@/components/linha-do-tempo';
import { StatusChip } from '@/components/ui/status-chip';

/** Horários e veículo da viagem (previsto x real), usados no cartão e no detalhe. */
export function DadosViagem({ viagem }: { viagem: Requisicao }) {
  return (
    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
      <dt className="text-texto-2">Veículo</dt>
      <dd>{viagem.veiculo_descricao ?? 'Veículo'}</dd>
      <dt className="text-texto-2">Saída prevista</dt>
      <dd>{dataHora(viagem.hora_saida)}</dd>
      {viagem.saida_real_em ? (
        <>
          <dt className="text-texto-2">Saída real</dt>
          <dd>{dataHora(viagem.saida_real_em)}</dd>
        </>
      ) : null}
      {viagem.chegada_real_em ? (
        <>
          <dt className="text-texto-2">Chegada real</dt>
          <dd>{dataHora(viagem.chegada_real_em)}</dd>
        </>
      ) : null}
      {viagem.km_saida !== null && viagem.km_saida !== undefined ? (
        <>
          <dt className="text-texto-2">Km de saída</dt>
          <dd>{numero(viagem.km_saida)}</dd>
        </>
      ) : null}
      {viagem.km_chegada !== null && viagem.km_chegada !== undefined ? (
        <>
          <dt className="text-texto-2">Km de chegada</dt>
          <dd>{numero(viagem.km_chegada)}</dd>
        </>
      ) : null}
    </dl>
  );
}

/** Cartão de "Minhas viagens". Os botões são exatamente os de `acoes_permitidas` da API. */
export function CartaoViagem({
  viagem,
  ocupado,
  onAcao,
  onOrganizar,
  aoRegistrarChecklist,
  arquivando,
  acaoEmAndamento,
}: {
  viagem: Requisicao;
  ocupado: boolean;
  /** Ação em andamento na API (passada pela página): spinner nos botões. */
  acaoEmAndamento?: Acao | null;
  /** Arquivar/desarquivar em andamento: cobertura no cartão e spinner no botão. */
  arquivando?: boolean;
  onAcao: (req: Requisicao, acao: Acao, corpo?: CorpoAcao) => void;
  /** Arquivar/desarquivar (gesto e botão), só com a ação em `acoes_permitidas`. */
  onOrganizar?: OnOrganizar;
  aoRegistrarChecklist?: () => void | Promise<void>;
}) {
  // Direita arquiva e esquerda desarquiva, só se a API liberou a ação para esta viagem.
  const direita: PapelGesto | undefined =
    onOrganizar && viagem.acoes_permitidas.includes('arquivar')
      ? { rotulo: 'Arquivar', cor: 'neutra', onAcionar: () => onOrganizar(viagem, 'arquivar') }
      : undefined;
  const esquerda: PapelGesto | undefined =
    onOrganizar && viagem.acoes_permitidas.includes('desarquivar')
      ? {
          rotulo: 'Desarquivar',
          cor: 'neutra',
          onAcionar: () => onOrganizar(viagem, 'desarquivar'),
        }
      : undefined;
  return (
    <ItemArrastavel
      className="rounded-xl border border-borda bg-white"
      classeInterna="gap-0 p-4"
      ocupado={ocupado}
      processando={
        arquivando
          ? viagem.acoes_permitidas.includes('arquivar')
            ? 'Arquivando...'
            : 'Desarquivando...'
          : acaoEmAndamento
            ? ROTULO_ANDAMENTO[acaoEmAndamento]
            : undefined
      }
      direita={direita}
      esquerda={esquerda}
    >
      <Link
        href={`/motorista/detalhe?id=${encodeURIComponent(viagem.id)}`}
        className="block rounded-padrao"
      >
        <div className="flex items-start justify-between gap-3">
          <p className="text-lg font-semibold">{dataCurta(viagem.hora_saida)}</p>
          <StatusChip status={viagem.status} />
        </div>
        <p className="mt-2 text-base break-words">{viagem.trajeto}</p>
        <DadosViagem viagem={viagem} />
        <LinhaDoTempo linhaDoTempo={viagem.linha_do_tempo} variante="compacta" className="mt-3" />
      </Link>
      <BotoesAcao
        req={viagem}
        ocupado={ocupado}
        onAcao={onAcao}
        onOrganizar={onOrganizar}
        arquivando={arquivando}
        acaoEmAndamento={acaoEmAndamento}
        aoRegistrarChecklist={aoRegistrarChecklist}
      />
    </ItemArrastavel>
  );
}
