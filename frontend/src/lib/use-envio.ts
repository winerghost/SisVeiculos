'use client';

import * as React from 'react';
import { ApiErro } from './api';
import { useAuth } from './auth';

/**
 * Envia uma escrita à API; em erro, guarda a mensagem como veio (inclusive 409/403) e o
 * `campo` que a API indicar (só para ligar o erro ao campo via aria; nada é validado aqui).
 */
export function useEnvio() {
  const { tratarErro } = useAuth();
  const [ocupado, setOcupado] = React.useState(false);
  const [erro, setErro] = React.useState<string | null>(null);
  const [campoErro, setCampoErro] = React.useState<string | null>(null);

  const executar = React.useCallback(
    async <T>(fn: () => Promise<T>): Promise<{ ok: true; valor: T } | { ok: false }> => {
      setOcupado(true);
      setErro(null);
      setCampoErro(null);
      try {
        return { ok: true, valor: await fn() };
      } catch (e) {
        setErro(tratarErro(e));
        setCampoErro(e instanceof ApiErro ? e.campo : null);
        return { ok: false };
      } finally {
        setOcupado(false);
      }
    },
    [tratarErro],
  );

  return { ocupado, erro, campoErro, setErro, executar };
}
