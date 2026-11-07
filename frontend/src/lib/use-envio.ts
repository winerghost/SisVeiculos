'use client';

import * as React from 'react';
import { useAuth } from './auth';

/** Envia uma escrita à API; em erro, guarda a mensagem como veio (inclusive 409/403). */
export function useEnvio() {
  const { tratarErro } = useAuth();
  const [ocupado, setOcupado] = React.useState(false);
  const [erro, setErro] = React.useState<string | null>(null);

  const executar = React.useCallback(
    async <T>(fn: () => Promise<T>): Promise<{ ok: true; valor: T } | { ok: false }> => {
      setOcupado(true);
      setErro(null);
      try {
        return { ok: true, valor: await fn() };
      } catch (e) {
        setErro(tratarErro(e));
        return { ok: false };
      } finally {
        setOcupado(false);
      }
    },
    [tratarErro],
  );

  return { ocupado, erro, setErro, executar };
}
