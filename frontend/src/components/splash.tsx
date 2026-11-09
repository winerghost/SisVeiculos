'use client';

import * as React from 'react';

const CHAVE = 'splash-visto';
// Reserva caso o navegador não dispare o fim da animação (aba em segundo plano, por exemplo).
const LIMITE_MS = 3000;

function jaVista(): boolean {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return true;
  try {
    return sessionStorage.getItem(CHAVE) !== null;
  } catch {
    return false;
  }
}

/**
 * Abertura em tela cheia, só visual (não carrega nem decide nada). Uma vez por sessão,
 * ignorada com "reduzir movimento". Toque para pular. `aoTerminar(true)` se exibiu.
 */
export function Splash({ aoTerminar }: { aoTerminar: (exibiu: boolean) => void }) {
  const encerrado = React.useRef(false);

  const terminar = React.useCallback(
    (exibiu: boolean) => {
      if (encerrado.current) return;
      encerrado.current = true;
      try {
        sessionStorage.setItem(CHAVE, '1');
      } catch {
        // Sem sessionStorage: a abertura apenas reaparece na próxima visita.
      }
      aoTerminar(exibiu);
    },
    [aoTerminar],
  );

  React.useEffect(() => {
    if (jaVista()) {
      terminar(false);
      return;
    }
    const t = window.setTimeout(() => terminar(true), LIMITE_MS);
    return () => window.clearTimeout(t);
  }, [terminar]);

  return (
    <div
      role="presentation"
      onClick={() => terminar(true)}
      onAnimationEnd={(e) => {
        if (e.animationName === 'splash-saida') terminar(true);
      }}
      className="splash fixed inset-0 z-50 flex items-center justify-center"
    >
      <div className="splash-logo">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/logo-agu.webp" alt="AGU - Advocacia-Geral da União" className="h-28 w-auto" />
      </div>
    </div>
  );
}
