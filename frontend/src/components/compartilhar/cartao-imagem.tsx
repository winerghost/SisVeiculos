import * as React from 'react';
import { Ban, Check, X } from 'lucide-react';
import { StatusChip } from '@/components/ui/status-chip';
import { rotuloRole } from '@/components/ui/chip';
import { dataHora, duracao, numero } from '@/lib/formato';
import { cn } from '@/lib/utils';
import type { EtapaLinhaDoTempo, Requisicao, SituacaoEtapa } from '@/lib/tipos';

/**
 * Cartão que vira a imagem PNG de compartilhamento da requisição (não é captura da tela).
 *
 * Só apresentação: usa `req` exatamente como veio da API (a minimização por perfil já foi feita no
 * servidor) e não calcula nada. Sem fetch, sem efeitos, sem animação, sem botões nem links.
 * Ficam fora, por decisão LGPD do dono (2026-11-17): fotos (avatar só com a inicial), e-mail,
 * telefone, checklist e localização. A observação só entra com `incluirObservacao`.
 *
 * Largura lógica fixa (540 px; exportado com pixelRatio 2 = 1080 px) e altura livre: a linha do
 * tempo vertical própria faz o cartão crescer. Cores vêm das variáveis CSS do tema.
 */
export interface CartaoImagemProps {
  /** `req.dados` como veio da API. */
  req: Requisicao;
  /** Escolha do usuário no menu (padrão false). */
  incluirObservacao: boolean;
}

export const LARGURA_CARTAO_PX = 540;

const SITUACAO_TEXTO: Record<SituacaoEtapa, string> = {
  concluida: 'Concluída',
  atual: 'Etapa atual',
  pendente: 'Pendente',
  negada: 'Negada',
  cancelada: 'Cancelada',
};

const COR_LINHA: Record<SituacaoEtapa, string> = {
  concluida: 'border-primaria border-solid',
  atual: 'border-primaria border-solid',
  pendente: 'border-borda-campo border-dashed',
  negada: 'border-erro border-solid',
  cancelada: 'border-erro border-solid',
};

const temValor = <T,>(v: T | null | undefined): v is T => v !== null && v !== undefined;

/** Marcador estático (sem animate-pulse): ícone + forma; o texto da situação vem ao lado. */
function Marcador({ situacao }: { situacao: SituacaoEtapa }) {
  const base = 'relative z-10 flex size-9 shrink-0 items-center justify-center rounded-full';
  switch (situacao) {
    case 'concluida':
      return (
        <span aria-hidden className={cn(base, 'bg-primaria text-white')}>
          <Check className="size-5" />
        </span>
      );
    case 'atual':
      return (
        <span aria-hidden className={cn(base, 'border-2 border-foco bg-white ring-4 ring-foco/20')}>
          <span className="size-3 rounded-full bg-foco" />
        </span>
      );
    case 'negada':
      return (
        <span aria-hidden className={cn(base, 'bg-erro text-white')}>
          <X className="size-5" />
        </span>
      );
    case 'cancelada':
      return (
        <span aria-hidden className={cn(base, 'border-2 border-erro bg-white text-erro')}>
          <Ban className="size-5" />
        </span>
      );
    default:
      return <span aria-hidden className={cn(base, 'border-2 border-borda-campo bg-white')} />;
  }
}

function responsavel(e: EtapaLinhaDoTempo): string {
  if (!e.por) return '';
  if (e.por.nome) return e.por.nome;
  return e.por.papel ? rotuloRole(e.por.papel) : '';
}

function Etapa({ etapa, proxima }: { etapa: EtapaLinhaDoTempo; proxima?: EtapaLinhaDoTempo }) {
  const por = responsavel(etapa);
  const erro = etapa.situacao === 'negada' || etapa.situacao === 'cancelada';
  return (
    <li className="relative flex min-w-0 gap-3 pb-5 last:pb-0">
      {proxima ? (
        <span
          aria-hidden
          className={cn(
            'absolute top-10 bottom-0 left-[17px] border-l-2',
            COR_LINHA[proxima.situacao],
          )}
        />
      ) : null}
      <Marcador situacao={etapa.situacao} />
      <div className="min-w-0 pt-1 break-words">
        <p
          className={cn(
            'text-base font-semibold',
            etapa.situacao === 'atual' && 'text-foco',
            erro && 'text-erro',
            etapa.situacao === 'pendente' && 'text-texto-2',
          )}
        >
          {etapa.rotulo}
          <span className="font-normal text-texto-2"> · {SITUACAO_TEXTO[etapa.situacao]}</span>
        </p>
        {etapa.em ? <p className="text-[15px] text-texto-2">{dataHora(etapa.em)}</p> : null}
        {por ? <p className="text-[15px] text-texto-2">por {por}</p> : null}
        {temValor(etapa.duracao_desde_anterior_s) ? (
          <p className="text-[15px] text-texto-2">
            {duracao(etapa.duracao_desde_anterior_s)} após a etapa anterior
          </p>
        ) : null}
        {etapa.situacao === 'atual' && temValor(etapa.decorrido_s) ? (
          <p className="text-[15px] text-texto-2">Decorrido: {duracao(etapa.decorrido_s)}</p>
        ) : null}
      </div>
    </li>
  );
}

function Linha({ rotulo, children }: { rotulo: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[150px_1fr] gap-3 py-1.5">
      <dt className="text-[15px] text-texto-2">{rotulo}</dt>
      <dd className="min-w-0 text-base font-medium break-words text-texto">{children}</dd>
    </div>
  );
}

export const CartaoImagem = React.forwardRef<HTMLDivElement, CartaoImagemProps>(
  function CartaoImagem({ req, incluirObservacao }, ref) {
    const etapas = req.linha_do_tempo?.etapas ?? [];
    const geradoEm = dataHora(req.linha_do_tempo?.gerado_em ?? null);
    const observacao = incluirObservacao && req.observacao ? req.observacao : null;

    return (
      <div
        ref={ref}
        data-testid="cartao-imagem"
        style={{ width: LARGURA_CARTAO_PX }}
        className="bg-fundo p-6 font-sans text-texto antialiased"
      >
        <header className="mb-4 flex items-center gap-3">
          {/* Mesma origem: a biblioteca de geração embute o logo. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/logo-agu.webp"
            alt="AGU"
            width={44}
            height={44}
            className="size-11 object-contain"
          />
          <div className="min-w-0">
            <p className="text-lg leading-tight font-semibold text-primaria">
              Controle de Veículos
            </p>
            <p className="text-[15px] leading-tight text-texto-2">Requisição de veículo</p>
          </div>
        </header>

        <section className="rounded-xl border border-borda bg-white p-5">
          <div className="flex items-center justify-between gap-3">
            <p className="text-base font-semibold text-texto-2">Nº {req.id.slice(0, 8)}</p>
            <div className="[&>span]:text-[15px]">
              <StatusChip status={req.status} />
            </div>
          </div>
          <p className="mt-3 text-[15px] text-texto-2">Trajeto</p>
          <p className="text-xl leading-snug font-semibold break-words">{req.trajeto}</p>

          <dl className="mt-3 border-t border-borda pt-2">
            {req.veiculo_descricao ? <Linha rotulo="Veículo">{req.veiculo_descricao}</Linha> : null}
            <Linha rotulo="Solicitante">
              <span className="flex items-center gap-2">
                <span
                  aria-hidden
                  className="flex size-8 shrink-0 items-center justify-center rounded-full bg-[#dcebff] text-sm font-semibold text-[#0b3c7a]"
                >
                  {req.nome_solicitante.charAt(0).toUpperCase()}
                </span>
                <span className="min-w-0">
                  {req.nome_solicitante} · {req.unidade}
                </span>
              </span>
            </Linha>
            {req.motorista?.nome ? <Linha rotulo="Motorista">{req.motorista.nome}</Linha> : null}
            {req.hora_saida ? (
              <Linha rotulo="Saída prevista">{dataHora(req.hora_saida)}</Linha>
            ) : null}
            {req.saida_real_em ? (
              <Linha rotulo="Saída registrada">{dataHora(req.saida_real_em)}</Linha>
            ) : null}
            {req.chegada_real_em ? (
              <Linha rotulo="Chegada registrada">{dataHora(req.chegada_real_em)}</Linha>
            ) : null}
            {temValor(req.km_saida) ? (
              <Linha rotulo="Km de saída">{numero(req.km_saida)}</Linha>
            ) : null}
            {temValor(req.km_chegada) ? (
              <Linha rotulo="Km de chegada">{numero(req.km_chegada)}</Linha>
            ) : null}
            {temValor(req.km_percorrido) ? (
              <Linha rotulo="Km percorrido">{numero(req.km_percorrido)} km</Linha>
            ) : null}
            {observacao ? (
              <Linha rotulo="Observação">
                <span className="font-normal whitespace-pre-line">{observacao}</span>
              </Linha>
            ) : null}
          </dl>
        </section>

        {etapas.length > 0 ? (
          <section className="mt-4 rounded-xl border border-borda bg-white p-5">
            <h2 className="mb-4 text-base font-semibold text-texto-2">Linha do tempo</h2>
            <ol>
              {etapas.map((e, i) => (
                <Etapa key={e.chave} etapa={e} proxima={etapas[i + 1]} />
              ))}
            </ol>
          </section>
        ) : null}

        <footer className="mt-4 text-center text-[15px] text-texto-2">
          Uso interno – AGU{geradoEm ? ` · situação em ${geradoEm}` : ''}
        </footer>
      </div>
    );
  },
);
