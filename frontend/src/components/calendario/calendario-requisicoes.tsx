'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import FullCalendar, {
  useCalendarController,
  type DatesSetInfo,
  type EventClickInfo,
  type EventDisplayInfo,
  type EventInput,
} from '@fullcalendar/react';
import classicTheme from '@fullcalendar/react/themes/classic';
import dayGridPlugin from '@fullcalendar/react/daygrid';
import timeGridPlugin from '@fullcalendar/react/timegrid';
import listPlugin from '@fullcalendar/react/list';
import ptBr from '@fullcalendar/react/locales/pt-br';
import '@fullcalendar/react/skeleton.css';
import '@fullcalendar/react/themes/classic/theme.css';
import '@fullcalendar/react/themes/classic/palette.css';
import './calendario.css';

import { calendario } from '@/lib/api';
import { useCarga } from '@/lib/use-carga';
import type { StatusCalendario } from '@/lib/tipos';
import { cn } from '@/lib/utils';
import { Alerta } from '@/components/ui/alert';
import { StatusChip } from '@/components/ui/status-chip';

/**
 * Calendário SOMENTE informativo: sem arrastar, redimensionar, selecionar nem criar. Mostra o que
 * GET /api/calendario devolve (visibilidade, status e intervalo decididos no servidor) e leva ao
 * detalhe da requisição no clique.
 */

// Fuso fixo do projeto: o calendário desenha as datas como o servidor as entende.
const FUSO = 'America/Sao_Paulo';

// Cores dos chips de status: docs/mockups/README.md (fundo / texto). Só apresentação.
const ESTILO: Record<StatusCalendario, { rotulo: string; fundo: string; texto: string }> = {
  solicitada: { rotulo: 'Solicitada', fundo: '#fff1d6', texto: '#7a4a00' },
  autorizada: { rotulo: 'Autorizada', fundo: '#dcebff', texto: '#0b3c7a' },
  em_uso: { rotulo: 'Em uso', fundo: '#d9f2e6', texto: '#0a5a3a' },
  concluida: { rotulo: 'Concluída', fundo: '#e8ecf2', texto: '#3b4658' },
};
const NEUTRO = { fundo: '#e8ecf2', texto: '#3b4658' };

const VISOES = [
  { id: 'dayGridMonth', rotulo: 'Mês' },
  { id: 'timeGridWeek', rotulo: 'Semana' },
  { id: 'listMonth', rotulo: 'Lista' },
] as const;

// Abaixo de md (768px) o calendário abre em lista; só decide a visão inicial.
const CONSULTA_MD = '(min-width: 768px)';
function assinarTela(aviso: () => void) {
  const mq = window.matchMedia(CONSULTA_MD);
  mq.addEventListener('change', aviso);
  return () => mq.removeEventListener('change', aviso);
}
const telaLarga = () => window.matchMedia(CONSULTA_MD).matches;
const semTela = () => null;

/** "2026-11-30" → "2026-11-29" (aritmética de calendário, só para montar a query). */
function diaAnterior(dia: string): string {
  const [a = 0, m = 1, d = 1] = dia.split('-').map(Number);
  return new Date(Date.UTC(a, m - 1, d - 1)).toISOString().slice(0, 10);
}

interface Periodo {
  /** YYYY-MM-DD, primeiro dia visível. */
  inicio: string;
  /** YYYY-MM-DD, último dia visível (inclusivo, como a API pede). */
  fim: string;
  /** Fim exclusivo da janela visível (ISO do calendário), usado só para desenhar. */
  fimExclusivo: string;
}

export function CalendarioRequisicoes() {
  const router = useRouter();
  const controller = useCalendarController();
  const larga = React.useSyncExternalStore(assinarTela, telaLarga, semTela);
  const [periodo, setPeriodo] = React.useState<Periodo | null>(null);

  const carga = useCarga(
    () =>
      periodo
        ? calendario.listar({ inicio: periodo.inicio, fim: periodo.fim })
        : Promise.resolve(null),
    periodo ? `${periodo.inicio}|${periodo.fim}` : '',
    { recursos: ['requisicoes', 'veiculos'] },
  );

  const aoMudarDatas = React.useCallback((info: DatesSetInfo) => {
    const inicio = info.startStr.slice(0, 10);
    const fim = diaAnterior(info.endStr.slice(0, 10));
    setPeriodo((p) =>
      p && p.inicio === inicio && p.fim === fim ? p : { inicio, fim, fimExclusivo: info.endStr },
    );
  }, []);

  const eventos = React.useMemo<EventInput[]>(() => {
    const itens = carga.dados?.itens ?? [];
    return itens.map((i) => {
      const e = ESTILO[i.status] ?? { rotulo: i.status, ...NEUTRO };
      // Viagem em uso sem chegada: desenhada até o fim da janela visível (só visual; inicio/fim
      // e o status vêm prontos da API).
      const fim = i.fim ?? (i.status === 'em_uso' ? periodo?.fimExclusivo : undefined);
      return {
        id: i.id,
        title: `${i.veiculo_rotulo} · ${e.rotulo}`,
        start: i.inicio,
        end: fim,
        allDay: false,
        color: e.fundo,
        contrastColor: e.texto,
        // Somente leitura, mesmo que alguma opção global mude no futuro.
        editable: false,
        startEditable: false,
        durationEditable: false,
        interactive: true,
        extendedProps: { status: i.status, veiculo_rotulo: i.veiculo_rotulo },
      };
    });
  }, [carga.dados, periodo?.fimExclusivo]);

  const aoClicar = React.useCallback(
    (info: EventClickInfo) => {
      info.jsEvent.preventDefault();
      router.push(`/requisicoes/detalhe?id=${encodeURIComponent(info.event.id)}`);
    },
    [router],
  );

  // Na lista, o status aparece como o chip padrão do sistema (o ponto colorido do tema some no
  // fundo claro). Nas demais visões, conteúdo padrão (hora + título com o status por extenso).
  const conteudoEvento = React.useCallback((info: EventDisplayInfo) => {
    if (!info.view.type.startsWith('list')) return true;
    const status = info.event.extendedProps.status as StatusCalendario;
    const rotulo = String(info.event.extendedProps.veiculo_rotulo ?? '');
    return (
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1">
        {info.timeText ? <span className="text-texto-2 tabular-nums">{info.timeText}</span> : null}
        <span className="font-medium">{rotulo}</span>
        <StatusChip status={status} />
      </span>
    );
  }, []);

  if (larga === null) {
    return (
      <p role="status" className="text-texto-2">
        Carregando...
      </p>
    );
  }

  const visaoAtual = controller.view?.type;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => controller.prev()}
            aria-label="Período anterior"
            className="flex size-11 shrink-0 items-center justify-center rounded-padrao border border-borda-campo bg-white text-texto"
          >
            <ChevronLeft aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => controller.today()}
            className="flex min-h-11 shrink-0 items-center rounded-padrao border border-borda-campo bg-white px-4 font-medium text-texto"
          >
            Hoje
          </button>
          <button
            type="button"
            onClick={() => controller.next()}
            aria-label="Próximo período"
            className="flex size-11 shrink-0 items-center justify-center rounded-padrao border border-borda-campo bg-white text-texto"
          >
            <ChevronRight aria-hidden />
          </button>
          <h2
            aria-live="polite"
            className="min-w-40 flex-1 text-right text-lg font-semibold break-words first-letter:uppercase"
          >
            {controller.view?.title ?? ''}
          </h2>
        </div>
        <div role="group" aria-label="Visualização" className="flex gap-2">
          {VISOES.map((v) => {
            const ativa = visaoAtual === v.id;
            return (
              <button
                key={v.id}
                type="button"
                aria-pressed={ativa}
                onClick={() => controller.changeView(v.id)}
                className={cn(
                  'flex min-h-11 flex-1 items-center justify-center rounded-full border px-4 text-base font-medium',
                  ativa
                    ? 'border-primaria bg-primaria text-white'
                    : 'border-borda-campo bg-white text-texto',
                )}
              >
                {v.rotulo}
              </button>
            );
          })}
        </div>
      </div>

      {carga.erro ? <Alerta>{carga.erro}</Alerta> : null}
      {carga.dados?.truncado ? (
        <p
          role="status"
          className="rounded-padrao border border-[#7a4a00]/40 bg-[#fff1d6] p-3 text-[#7a4a00]"
        >
          Este período tem mais requisições do que o calendário consegue mostrar. Use a visão Semana
          para ver todas.
        </p>
      ) : null}
      {periodo && !carga.dados && !carga.erro ? (
        <p role="status" className="text-texto-2">
          Carregando requisições...
        </p>
      ) : null}

      <div className="calendario-agu">
        <FullCalendar
          controller={controller}
          plugins={[classicTheme, dayGridPlugin, timeGridPlugin, listPlugin]}
          locale={ptBr}
          timeZone={FUSO}
          initialView={larga ? 'dayGridMonth' : 'listMonth'}
          headerToolbar={false}
          height="auto"
          // Somente informativo: nada é arrastado, redimensionado, selecionado ou criado.
          editable={false}
          eventStartEditable={false}
          selectable={false}
          navLinks={false}
          eventInteractive
          events={eventos}
          eventDisplay="block"
          dayMaxEvents={2}
          nowIndicator
          datesSet={aoMudarDatas}
          eventClick={aoClicar}
          eventContent={conteudoEvento}
          rowEventClass="cal-evento-linha"
          columnEventClass="cal-evento-coluna"
          listItemEventClass="cal-evento-lista"
          moreLinkClass="cal-mais"
          noEventsClass="cal-sem-eventos"
          noEventsContent="Nenhuma requisição neste período."
        />
      </div>
    </div>
  );
}
