import * as React from 'react';
import { Ban, Check, X } from 'lucide-react';
import { dataHora, duracao } from '@/lib/formato';
import { cn } from '@/lib/utils';
import { rotuloRole } from '@/components/ui/chip';
import type { EtapaLinhaDoTempo, LinhaDoTempo, SituacaoEtapa } from '@/lib/tipos';

// Só apresentação: a API decide etapas, situação, durações e o desfecho. Nada é calculado aqui.
const SITUACAO_TEXTO: Record<SituacaoEtapa, string> = {
  concluida: 'concluída',
  atual: 'etapa atual',
  pendente: 'pendente',
  negada: 'negada',
  cancelada: 'cancelada',
};

const COR_LINHA: Record<SituacaoEtapa, string> = {
  concluida: 'border-primaria border-solid',
  atual: 'border-primaria border-solid',
  pendente: 'border-borda-campo border-dashed',
  negada: 'border-erro border-solid',
  cancelada: 'border-erro border-solid',
};

function Marcador({ situacao, compacto }: { situacao: SituacaoEtapa; compacto: boolean }) {
  const tamanho = compacto ? 'size-6' : 'size-9';
  const icone = compacto ? 'size-3.5' : 'size-5';
  const base = cn('relative z-10 flex shrink-0 items-center justify-center rounded-full', tamanho);
  switch (situacao) {
    case 'concluida':
      return (
        <span aria-hidden className={cn(base, 'bg-primaria text-white')}>
          <Check className={icone} />
        </span>
      );
    case 'atual':
      return (
        <span
          aria-hidden
          className={cn(
            base,
            'border-2 border-foco bg-white ring-4 ring-foco/20 motion-safe:animate-pulse',
          )}
        >
          <span className={cn('rounded-full bg-foco', compacto ? 'size-2' : 'size-3')} />
        </span>
      );
    case 'negada':
      return (
        <span aria-hidden className={cn(base, 'bg-erro text-white')}>
          <X className={icone} />
        </span>
      );
    case 'cancelada':
      return (
        <span aria-hidden className={cn(base, 'border-2 border-erro bg-white text-erro')}>
          <Ban className={icone} />
        </span>
      );
    default:
      return <span aria-hidden className={cn(base, 'border-2 border-borda-campo bg-white')} />;
  }
}

const ehErro = (s: SituacaoEtapa) => s === 'negada' || s === 'cancelada';

function quem(e: EtapaLinhaDoTempo): string {
  if (!e.por) return '';
  if (e.por.nome) return e.por.nome;
  return e.por.papel ? rotuloRole(e.por.papel) : '';
}

function EtapaCompacta({
  etapa,
  proxima,
}: {
  etapa: EtapaLinhaDoTempo;
  proxima: EtapaLinhaDoTempo | undefined;
}) {
  return (
    <li
      aria-current={etapa.situacao === 'atual' ? 'step' : undefined}
      className="relative flex min-w-0 flex-1 flex-col items-center text-center"
    >
      {proxima ? (
        <span
          aria-hidden
          className={cn(
            'absolute top-[11px] left-[calc(50%+16px)] w-[calc(100%-32px)] border-t-2',
            COR_LINHA[proxima.situacao],
          )}
        />
      ) : null}
      <Marcador situacao={etapa.situacao} compacto />
      <span
        className={cn(
          'mt-1 px-0.5 text-xs leading-tight break-words',
          etapa.situacao === 'atual' && 'font-semibold text-foco',
          etapa.situacao === 'concluida' && 'font-medium text-texto',
          ehErro(etapa.situacao) && 'font-semibold text-erro',
          etapa.situacao === 'pendente' && 'text-texto-2',
        )}
      >
        {etapa.rotulo}
        <span className="sr-only"> ({SITUACAO_TEXTO[etapa.situacao]})</span>
      </span>
      {etapa.duracao_desde_anterior_s !== null && etapa.duracao_desde_anterior_s !== undefined ? (
        <span className="mt-0.5 text-[11px] leading-tight text-texto-2">
          {duracao(etapa.duracao_desde_anterior_s)}
        </span>
      ) : null}
    </li>
  );
}

function EtapaCompleta({
  etapa,
  proxima,
}: {
  etapa: EtapaLinhaDoTempo;
  proxima: EtapaLinhaDoTempo | undefined;
}) {
  const por = quem(etapa);
  const linha = proxima ? COR_LINHA[proxima.situacao] : '';
  return (
    <li
      aria-current={etapa.situacao === 'atual' ? 'step' : undefined}
      className="relative flex min-w-0 gap-3 pb-6 last:pb-0 md:flex-1 md:flex-col md:pr-2 md:pb-0"
    >
      {proxima ? (
        <>
          {/* Vertical (mobile): do marcador ao próximo. */}
          <span
            aria-hidden
            className={cn('absolute top-10 bottom-0 left-[17px] border-l-2 md:hidden', linha)}
          />
          {/* Horizontal (a partir de md). */}
          <span
            aria-hidden
            className={cn('absolute top-[17px] right-1 left-11 hidden border-t-2 md:block', linha)}
          />
        </>
      ) : null}
      <Marcador situacao={etapa.situacao} compacto={false} />
      <div className="min-w-0 break-words">
        <p
          className={cn(
            'font-semibold',
            etapa.situacao === 'atual' && 'text-foco',
            ehErro(etapa.situacao) && 'text-erro',
            etapa.situacao === 'pendente' && 'text-texto-2',
          )}
        >
          {etapa.rotulo}
          <span className="sr-only"> ({SITUACAO_TEXTO[etapa.situacao]})</span>
        </p>
        {etapa.em ? <p className="text-texto-2">{dataHora(etapa.em)}</p> : null}
        {por ? <p className="text-texto-2">por {por}</p> : null}
        {etapa.duracao_desde_anterior_s !== null && etapa.duracao_desde_anterior_s !== undefined ? (
          <p className="text-sm text-texto-2">
            {duracao(etapa.duracao_desde_anterior_s)} após a etapa anterior
          </p>
        ) : null}
        {etapa.situacao === 'atual' &&
        etapa.decorrido_s !== null &&
        etapa.decorrido_s !== undefined ? (
          <p className="text-sm text-texto-2">Decorrido: {duracao(etapa.decorrido_s)}</p>
        ) : null}
        {!etapa.em && etapa.situacao === 'pendente' ? (
          <p className="text-sm text-texto-2">Pendente</p>
        ) : null}
      </div>
    </li>
  );
}

/**
 * Linha do tempo da requisição. Exibe exatamente o que a API enviou (etapas, situação, durações);
 * "compacta" (cartões) é horizontal e sem rolagem; "completa" (detalhe) é vertical no celular e
 * horizontal a partir de md. O estado nunca depende só de cor: há ícone, texto e aria-current.
 */
export function LinhaDoTempo({
  linhaDoTempo,
  variante = 'completa',
  className,
}: {
  linhaDoTempo: LinhaDoTempo | null | undefined;
  variante?: 'compacta' | 'completa';
  className?: string;
}) {
  if (!linhaDoTempo || linhaDoTempo.etapas.length === 0) return null;
  const { etapas, texto_equivalente } = linhaDoTempo;
  const compacta = variante === 'compacta';
  return (
    <div className={className}>
      <p className="sr-only">{texto_equivalente}</p>
      <ol
        aria-label="Linha do tempo da requisição"
        data-variante={variante}
        className={cn(compacta ? 'flex gap-0' : 'flex flex-col md:flex-row')}
      >
        {etapas.map((e, i) =>
          compacta ? (
            <EtapaCompacta key={e.chave} etapa={e} proxima={etapas[i + 1]} />
          ) : (
            <EtapaCompleta key={e.chave} etapa={e} proxima={etapas[i + 1]} />
          ),
        )}
      </ol>
    </div>
  );
}
