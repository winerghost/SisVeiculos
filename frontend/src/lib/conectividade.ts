'use client';

import * as React from 'react';

/**
 * Conectividade só informativa: sem fila, sem escrita offline, sem repetir operações.
 * `navigator.onLine` apenas detecta ausência de rede; quem prova que o Flask responde é a sonda
 * GET /api/saude (HTTP 200 com {status:'ok'}). Qualquer outro resultado = servidor indisponível.
 */
export type EstadoConexao =
  'conectado' | 'sem_internet' | 'servidor_indisponivel' | 'restabelecida';

const TIMEOUT_SONDA_MS = 4000;
const INTERVALO_OK_MS = 30_000;
const INTERVALO_FALHA_MS = 10_000;
const DURACAO_RESTABELECIDA_MS = 4000;

async function sondar(): Promise<boolean> {
  const controle = new AbortController();
  const timer = setTimeout(() => controle.abort(), TIMEOUT_SONDA_MS);
  try {
    const resp = await fetch('/api/saude', {
      cache: 'no-store',
      credentials: 'omit',
      signal: controle.signal,
    });
    if (resp.status !== 200) return false;
    const corpo: unknown = await resp.json();
    return (corpo as { status?: unknown } | null)?.status === 'ok';
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export function useConectividade(): { estado: EstadoConexao; verificarAgora: () => void } {
  const [estado, setEstado] = React.useState<EstadoConexao>('conectado');
  const estadoRef = React.useRef<EstadoConexao>('conectado');
  const ocupadoRef = React.useRef(false);
  const ativoRef = React.useRef(true);
  const timerSondaRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const timerRestabRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const verificarRef = React.useRef<() => void>(() => undefined);

  React.useEffect(() => {
    ativoRef.current = true;

    const aplicar = (novo: EstadoConexao) => {
      estadoRef.current = novo;
      setEstado(novo);
    };
    const limparSonda = () => {
      if (timerSondaRef.current) clearTimeout(timerSondaRef.current);
      timerSondaRef.current = null;
    };
    const limparRestab = () => {
      if (timerRestabRef.current) clearTimeout(timerRestabRef.current);
      timerRestabRef.current = null;
    };

    const verificar = async (): Promise<void> => {
      limparSonda();
      if (!navigator.onLine) {
        limparRestab();
        aplicar('sem_internet');
        return; // offline: sem sondar; o evento "online" retoma
      }
      if (ocupadoRef.current) return; // no máximo uma sonda por vez
      ocupadoRef.current = true;
      let ok = false;
      try {
        ok = await sondar();
      } finally {
        ocupadoRef.current = false;
      }
      if (!ativoRef.current) return;
      if (!navigator.onLine) {
        limparRestab();
        aplicar('sem_internet');
        return;
      }
      const agendar = (ms: number) => {
        limparSonda();
        if (document.hidden) return; // pausa; a sonda volta ao ficar visível
        timerSondaRef.current = setTimeout(() => void verificar(), ms);
      };
      if (ok) {
        if (estadoRef.current === 'sem_internet' || estadoRef.current === 'servidor_indisponivel') {
          aplicar('restabelecida');
          limparRestab();
          timerRestabRef.current = setTimeout(() => {
            if (estadoRef.current === 'restabelecida') aplicar('conectado');
          }, DURACAO_RESTABELECIDA_MS);
        }
        agendar(INTERVALO_OK_MS);
      } else {
        limparRestab();
        aplicar('servidor_indisponivel');
        agendar(INTERVALO_FALHA_MS);
      }
    };
    verificarRef.current = () => void verificar();

    const aoFicarOffline = () => {
      limparSonda();
      limparRestab();
      aplicar('sem_internet');
    };
    const aoFicarOnline = () => void verificar();
    const aoMudarVisibilidade = () => {
      if (document.hidden) limparSonda();
      else void verificar();
    };

    window.addEventListener('offline', aoFicarOffline);
    window.addEventListener('online', aoFicarOnline);
    document.addEventListener('visibilitychange', aoMudarVisibilidade);
    if (!navigator.onLine) aoFicarOffline();
    else if (!document.hidden) void verificar();

    return () => {
      ativoRef.current = false;
      limparSonda();
      limparRestab();
      window.removeEventListener('offline', aoFicarOffline);
      window.removeEventListener('online', aoFicarOnline);
      document.removeEventListener('visibilitychange', aoMudarVisibilidade);
    };
  }, []);

  const verificarAgora = React.useCallback(() => verificarRef.current(), []);
  return { estado, verificarAgora };
}
