// Fuso fixo do sistema: America/Sao_Paulo (UTC-03:00, sem horário de verão desde 2019).
// Só formatação e montagem do texto ISO; o servidor valida data e hora.
export const FUSO = 'America/Sao_Paulo';
export const OFFSET = '-03:00';

const fmtData = new Intl.DateTimeFormat('pt-BR', {
  timeZone: FUSO,
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
});
const fmtHora = new Intl.DateTimeFormat('pt-BR', {
  timeZone: FUSO,
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

export function dataHora(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${fmtData.format(d)} às ${fmtHora.format(d)}`;
}

export function dataCurta(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${fmtData.format(d).slice(0, 5)} · ${fmtHora.format(d)}`;
}

/** "dd/mm/aaaa hh:mm" no fuso do sistema (sem "às"). */
export function dataHoraNumerica(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${fmtData.format(d)} ${fmtHora.format(d)}`;
}

/** "Último acesso: dd/mm/aaaa hh:mm" ou "Nunca acessou" (null/ausente/ilegível). */
export function rotuloUltimoAcesso(iso: string | null | undefined): string {
  const t = dataHoraNumerica(iso);
  return t ? `Último acesso: ${t}` : 'Nunca acessou';
}

/**
 * Converte o valor de um input date ("aaaa-mm-dd") em ISO com fuso fixo, à meia-noite.
 * Com `diaSeguinte`, devolve 00:00 do dia seguinte (limite exclusivo de um intervalo).
 * Só montagem de texto; a API valida o intervalo.
 */
export function diaParaIso(dia: string, diaSeguinte = false): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dia);
  if (!m) return '';
  const [, a, mes, d] = m;
  if (!diaSeguinte) return `${a}-${mes}-${d}T00:00:00${OFFSET}`;
  const prox = new Date(Date.UTC(Number(a), Number(mes) - 1, Number(d) + 1));
  return `${prox.getUTCFullYear()}-${dois(prox.getUTCMonth() + 1)}-${dois(prox.getUTCDate())}T00:00:00${OFFSET}`;
}

const fmtNumero = new Intl.NumberFormat('pt-BR');

/** Número com separador de milhar pt-BR (ex.: 12.345). */
export const numero = (n: number): string => fmtNumero.format(n);

const dois = (n: number) => String(n).padStart(2, '0');

/** Monta "2026-11-10T08:30:00-03:00" a partir do dia escolhido no calendário e hora/minuto. */
export function montarHoraSaida(dia: Date, hora: number, minuto: number): string {
  return `${dia.getFullYear()}-${dois(dia.getMonth() + 1)}-${dois(dia.getDate())}T${dois(hora)}:${dois(minuto)}:00${OFFSET}`;
}

export function rotuloHoraSaida(dia: Date, hora: number, minuto: number): string {
  return `${dois(dia.getDate())}/${dois(dia.getMonth() + 1)}/${dia.getFullYear()} às ${dois(hora)}:${dois(minuto)}`;
}

/** "12 min", "2 h 05 min", "1 d 3 h". Só exibição de segundos vindos da API. */
export function duracao(segundos: number | null | undefined): string {
  if (segundos === null || segundos === undefined || !Number.isFinite(segundos)) return '';
  const s = Math.abs(segundos);
  if (s < 60) return 'menos de 1 min';
  const min = Math.round(s / 60);
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  if (h < 24) return m === 0 ? `${h} h` : `${h} h ${dois(m)} min`;
  const d = Math.floor(h / 24);
  const hr = h % 24;
  return hr === 0 ? `${d} d` : `${d} d ${hr} h`;
}

/** Diferença assinada em texto neutro: "12 min antes do previsto" / "5 min depois do previsto". */
export function duracaoEmRelacaoAoPrevisto(segundos: number | null | undefined): string {
  if (segundos === null || segundos === undefined || !Number.isFinite(segundos)) return '';
  if (Math.abs(segundos) < 60) return 'no horário previsto';
  return `${duracao(segundos)} ${segundos < 0 ? 'antes' : 'depois'} do previsto`;
}

/** Percentual (já em 0..100 vindo da API) com até 1 casa decimal. */
export const percentual = (n: number): string =>
  `${new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(n)}%`;

/** Hora "hh:mm" no fuso do sistema. */
export function horaDe(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : fmtHora.format(d);
}

/** Número com até 1 casa decimal (pt-BR). */
export const decimal = (n: number): string =>
  new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(n);

/** "2026-11-08" -> "08/11/2026"; "2026-11" -> "11/2026"; outros valores ficam como vieram. */
export function rotuloPeriodo(p: string): string {
  const d = /^(\d{4})-(\d{2})-(\d{2})/.exec(p);
  if (d) return `${d[3]}/${d[2]}/${d[1]}`;
  const m = /^(\d{4})-(\d{2})$/.exec(p);
  if (m) return `${m[2]}/${m[1]}`;
  return p;
}

/**
 * Tempo decorrido em texto curto ("agora há pouco", "há 5 min", "há 3 h"); a partir de 24 h usa a
 * data e hora completas. `agora` (ms) é passado por quem chama para manter a função pura.
 */
export function dataRelativa(iso: string | null | undefined, agora: number): string {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '';
  const min = Math.floor((agora - t) / 60_000);
  if (min < 1) return 'agora há pouco';
  if (min < 60) return `há ${min} min`;
  if (min < 1440) return `há ${Math.floor(min / 60)} h`;
  return dataHora(iso);
}
