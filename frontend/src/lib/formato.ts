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
