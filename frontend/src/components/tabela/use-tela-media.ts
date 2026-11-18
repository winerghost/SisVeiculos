'use client';

import * as React from 'react';

const CONSULTA_MD = '(min-width: 768px)';

function assinar(aoMudar: () => void) {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {};
  const mq = window.matchMedia(CONSULTA_MD);
  mq.addEventListener('change', aoMudar);
  return () => mq.removeEventListener('change', aoMudar);
}

function ler() {
  return typeof window.matchMedia === 'function' && window.matchMedia(CONSULTA_MD).matches;
}

/**
 * true a partir do breakpoint `md` (768 px). Só apresentação: decide entre cartões (celular) e
 * tabela. Renderiza só uma das duas (sem duplicar ids no DOM). Antes de montar vale o celular.
 */
export function useTelaMedia(): boolean {
  return React.useSyncExternalStore(assinar, ler, () => false);
}
