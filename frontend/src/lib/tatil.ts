'use client';

import * as React from 'react';
import { useAuth } from './auth';

/** Padrões curtos e discretos (ms); nenhum passa de 150 ms no total. */
export const PADROES = {
  sucesso: [30],
  confirmacao: [20],
  saida: [40],
  chegada: [40],
  checklist: [30, 40, 30],
  leitura: [25],
  erro: [50, 40, 50],
} as const satisfies Record<string, readonly number[]>;

export type NomeTatil = keyof typeof PADROES;

/** Vibra se o aparelho suportar e o usuário não pediu redução de movimento. Nunca lança erro. */
export function vibrar(padrao: readonly number[]): void {
  try {
    if (typeof navigator === 'undefined' || !('vibrate' in navigator)) return;
    if (
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ) {
      return;
    }
    navigator.vibrate([...padrao]);
  } catch {
    // Complemento opcional: falha silenciosa.
  }
}

export type Tatil = Record<NomeTatil, () => void>;

const INATIVO: Tatil = {
  sucesso: () => {},
  confirmacao: () => {},
  saida: () => {},
  chegada: () => {},
  checklist: () => {},
  leitura: () => {},
  erro: () => {},
};

const ATIVO: Tatil = Object.fromEntries(
  (Object.keys(PADROES) as NomeTatil[]).map((k) => [k, () => vibrar(PADROES[k])]),
) as Tatil;

/** Funções ativas só se a API habilitou `recursos.feedback_tatil`; senão, no-op. */
export function useTatil(): Tatil {
  const { eu } = useAuth();
  const ligado = eu?.recursos?.feedback_tatil === true;
  return React.useMemo(() => (ligado ? ATIVO : INATIVO), [ligado]);
}
