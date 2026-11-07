'use client';

import * as React from 'react';

/** Registra o service worker só em produção (o export estático servido por HTTPS). */
export function RegistrarSw() {
  React.useEffect(() => {
    if (process.env.NODE_ENV === 'production' && 'serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch(() => undefined);
    }
  }, []);
  return null;
}
