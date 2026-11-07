import type { MomentoChecklist } from './tipos';

// Rótulos de apresentação (os valores são os da API).
export const COMBUSTIVEIS: { valor: string; rotulo: string }[] = [
  { valor: 'vazio', rotulo: 'Vazio' },
  { valor: 'um_quarto', rotulo: '1/4' },
  { valor: 'meio', rotulo: '1/2' },
  { valor: 'tres_quartos', rotulo: '3/4' },
  { valor: 'cheio', rotulo: 'Cheio' },
];

export const rotuloCombustivel = (v: string) =>
  COMBUSTIVEIS.find((c) => c.valor === v)?.rotulo ?? v;

export const rotuloMomento = (m: MomentoChecklist) => (m === 'saida' ? 'saída' : 'chegada');
