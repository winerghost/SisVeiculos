'use client';

import * as React from 'react';
import { ApiErro, api } from './api';
import { useAuth } from './auth';
import type { RecursoTempoReal } from './tipos';

/**
 * Atualização em tempo real por versão (GET /api/atualizacoes a cada 10 s com a aba visível).
 * O servidor decide o que cada perfil enxerga e devolve só valores opacos por recurso; o cliente
 * compara com a resposta anterior e, se algum mudou, refaz os GETs de sempre das telas montadas.
 * Nenhuma regra de negócio aqui: só "este recurso mudou, recarregue".
 */

const INTERVALO_MS = 10_000;
const MAX_ATRASO_MS = 60_000;
/** Sem o endpoint (backend antigo): só o contador do sino, como antes. */
const INTERVALO_SEM_ENDPOINT_MS = 60_000;
/** Sem o endpoint: tenta de novo o /api/atualizacoes a cada N ciclos (deploy novo do backend). */
const CICLOS_ATE_REPROVAR = 5;
/** Espalha a recarga do /eu entre as abas quando as Configurações mudam (evita pico de 429). */
const JITTER_EU_MS = 2_000;
const DEBOUNCE_MS = 300;

type Ouvinte = () => void;

interface Contexto {
  /** Assina mudanças de recursos; devolve a função que cancela a assinatura. */
  assinar: (recursos: readonly RecursoTempoReal[], ouvinte: Ouvinte) => () => void;
  /** Tela com formulário em edição: guarda a recarga para quando o usuário pedir. */
  segurar: (id: string, recurso: readonly RecursoTempoReal[], recarregar: () => void) => void;
  liberar: (id: string) => void;
  /** Recursos com atualização guardada (para a faixa "Recarregar"). */
  seguros: ReadonlySet<RecursoTempoReal>;
  recarregarSeguros: () => void;
}

const Ctx = React.createContext<Contexto | null>(null);

/** Opcional: fora do provider (ex.: login) as telas funcionam sem tempo real. */
export function useTempoReal(): Contexto | null {
  return React.useContext(Ctx);
}

// Formulários em que o usuário digitou/escolheu algo e ainda não enviou. Um <form> sai daqui ao
// ser enviado, limpo ou desmontado (isConnected). Só estado de interface, sem regra.
const formsEditados = new Set<HTMLFormElement>();
let rastreando = false;

function rastrearFormularios() {
  if (rastreando || typeof document === 'undefined') return;
  rastreando = true;
  const marcar = (e: Event) => {
    const alvo = e.target;
    if (alvo instanceof Element) {
      const form = alvo.closest('form');
      if (form) formsEditados.add(form);
    }
  };
  const desmarcar = (e: Event) => {
    if (e.target instanceof HTMLFormElement) formsEditados.delete(e.target);
  };
  document.addEventListener('input', marcar, true);
  document.addEventListener('change', marcar, true);
  document.addEventListener('submit', desmarcar, true);
  document.addEventListener('reset', desmarcar, true);
}

/** Há formulário em edição na tela? Nesse caso a tela não é substituída sozinha. */
export function emEdicao(): boolean {
  for (const f of formsEditados) {
    if (!f.isConnected) formsEditados.delete(f);
  }
  return formsEditados.size > 0;
}

export function TempoRealProvider({ children }: { children: React.ReactNode }) {
  const {
    estado,
    tratarErro,
    atualizarEu,
    definirNaoLidas,
    recarregarNaoLidas,
  } = useAuth();

  const ouvintes = React.useRef(new Map<RecursoTempoReal, Set<Ouvinte>>());
  const pendentes = React.useRef(new Set<RecursoTempoReal>());
  const timerDebounce = React.useRef<number | undefined>(undefined);
  const segurados = React.useRef(
    new Map<string, { recursos: readonly RecursoTempoReal[]; recarregar: () => void }>(),
  );
  const [seguros, setSeguros] = React.useState<ReadonlySet<RecursoTempoReal>>(new Set());

  // Referências estáveis às funções do AuthProvider, para o laço não reiniciar a cada render.
  const auth = React.useRef({ tratarErro, atualizarEu, definirNaoLidas, recarregarNaoLidas });
  React.useEffect(() => {
    auth.current = { tratarErro, atualizarEu, definirNaoLidas, recarregarNaoLidas };
  });

  const atualizarSeguros = React.useCallback(() => {
    const s = new Set<RecursoTempoReal>();
    for (const v of segurados.current.values()) v.recursos.forEach((r) => s.add(r));
    setSeguros(s);
  }, []);

  const assinar = React.useCallback((recursos: readonly RecursoTempoReal[], ouvinte: Ouvinte) => {
    for (const r of recursos) {
      let s = ouvintes.current.get(r);
      if (!s) ouvintes.current.set(r, (s = new Set()));
      s.add(ouvinte);
    }
    return () => {
      for (const r of recursos) ouvintes.current.get(r)?.delete(ouvinte);
    };
  }, []);

  const segurar = React.useCallback(
    (id: string, recursos: readonly RecursoTempoReal[], recarregar: () => void) => {
      segurados.current.set(id, { recursos, recarregar });
      atualizarSeguros();
    },
    [atualizarSeguros],
  );

  const liberar = React.useCallback(
    (id: string) => {
      if (segurados.current.delete(id)) atualizarSeguros();
    },
    [atualizarSeguros],
  );

  const recarregarSeguros = React.useCallback(() => {
    const lista = [...segurados.current.values()];
    segurados.current.clear();
    atualizarSeguros();
    lista.forEach((s) => s.recarregar());
  }, [atualizarSeguros]);

  // Barramento com coalescência: uma rajada de mudanças vira uma recarga por tela.
  const invalidar = React.useCallback((recursos: RecursoTempoReal[]) => {
    recursos.forEach((r) => pendentes.current.add(r));
    if (timerDebounce.current !== undefined) return;
    timerDebounce.current = window.setTimeout(() => {
      timerDebounce.current = undefined;
      const disparar = new Set<Ouvinte>();
      for (const r of pendentes.current) ouvintes.current.get(r)?.forEach((o) => disparar.add(o));
      pendentes.current.clear();
      disparar.forEach((o) => o());
    }, DEBOUNCE_MS);
  }, []);

  React.useEffect(() => {
    rastrearFormularios();
  }, []);

  React.useEffect(() => {
    if (estado !== 'ativo') return;
    let parado = false;
    let timer: number | undefined;
    let emCurso = false;
    let atraso = INTERVALO_MS;
    let semEndpoint = false;
    let ciclosSemEndpoint = 0;
    let timerEu: number | undefined;
    let versoes: Record<string, string> | null = null;
    let etag: string | null = null;

    const agendar = (ms: number) => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = undefined;
      if (parado || document.visibilityState !== 'visible') return;
      timer = window.setTimeout(() => void consultar(), ms);
    };

    const consultar = async () => {
      if (parado || emCurso || document.visibilityState !== 'visible') return;
      if (timer !== undefined) window.clearTimeout(timer);
      timer = undefined;
      emCurso = true;
      try {
        if (semEndpoint && ++ciclosSemEndpoint < CICLOS_ATE_REPROVAR) {
          await auth.current.recarregarNaoLidas();
          agendar(INTERVALO_SEM_ENDPOINT_MS);
          return;
        }
        ciclosSemEndpoint = 0;
        const r = await api.atualizacoes(etag);
        if (parado) return;
        atraso = INTERVALO_MS;
        semEndpoint = false;
        if (r.dados) {
          etag = r.etag;
          auth.current.definirNaoLidas(r.dados.nao_lidas);
          const novas = r.dados.recursos;
          if (versoes) {
            const mudaram = (Object.keys(novas) as RecursoTempoReal[]).filter(
              (k) => versoes?.[k] !== undefined && versoes[k] !== novas[k],
            );
            if (mudaram.length > 0) invalidar(mudaram);
            // Configurações mudaram: recursos e ações do /eu podem ter mudado também. Recarga
            // silenciosa (falha não derruba a tela) e espalhada no tempo entre as abas.
            if (mudaram.includes('configuracoes') && timerEu === undefined) {
              timerEu = window.setTimeout(() => {
                timerEu = undefined;
                if (!parado) void auth.current.atualizarEu();
              }, Math.random() * JITTER_EU_MS);
            }
          }
          versoes = { ...novas };
        }
        agendar(INTERVALO_MS);
      } catch (e) {
        if (parado) return;
        if (
          e instanceof ApiErro &&
          (e.status === 401 ||
            (e.status === 403 && (e.codigo === 'mfa_requerido' || e.codigo === 'conta_inativa')))
        ) {
          // Sessão encerrada, conta inativa ou MFA: tratamento existente, sem insistir.
          auth.current.tratarErro(e);
          parado = true;
          return;
        }
        if (e instanceof ApiErro && e.status === 404) {
          // Backend sem o endpoint: o sino volta ao modo antigo JÁ (não espera 60 s).
          semEndpoint = true;
          ciclosSemEndpoint = 0;
          await auth.current.recarregarNaoLidas();
          agendar(INTERVALO_SEM_ENDPOINT_MS);
          return;
        }
        // Sem rede, 429, 5xx (ex.: função do banco ainda não aplicada) ou outro 403: o sino não
        // fica vazio nem congelado; a contagem vem do endpoint próprio enquanto isso.
        await auth.current.recarregarNaoLidas();
        if (parado) return;
        // Espera crescente 10 → 20 → 40 → 60 s.
        atraso = Math.min(atraso * 2, MAX_ATRASO_MS);
        agendar(atraso);
      } finally {
        emCurso = false;
      }
    };

    const agora = () => {
      if (document.visibilityState === 'visible') void consultar();
      else agendar(0); // limpa o timer: nada de consulta com a aba oculta
    };
    const aoVoltarRede = () => {
      atraso = INTERVALO_MS;
      void consultar();
    };
    // Push recebido pelo service worker: consulta já (o aviso não traz dados).
    const aoMensagemSw = (ev: MessageEvent) => {
      const d: unknown = ev.data;
      if (d && typeof d === 'object' && (d as { tipo?: unknown }).tipo === 'invalidar') {
        void consultar();
      }
    };

    void consultar();
    document.addEventListener('visibilitychange', agora);
    window.addEventListener('focus', agora);
    window.addEventListener('pageshow', agora);
    window.addEventListener('online', aoVoltarRede);
    const sw = 'serviceWorker' in navigator ? navigator.serviceWorker : null;
    sw?.addEventListener('message', aoMensagemSw);
    return () => {
      parado = true;
      if (timer !== undefined) window.clearTimeout(timer);
      if (timerEu !== undefined) window.clearTimeout(timerEu);
      document.removeEventListener('visibilitychange', agora);
      window.removeEventListener('focus', agora);
      window.removeEventListener('pageshow', agora);
      window.removeEventListener('online', aoVoltarRede);
      sw?.removeEventListener('message', aoMensagemSw);
    };
  }, [estado, invalidar]);

  React.useEffect(
    () => () => {
      if (timerDebounce.current !== undefined) window.clearTimeout(timerDebounce.current);
    },
    [],
  );

  const valor = React.useMemo<Contexto>(
    () => ({ assinar, segurar, liberar, seguros, recarregarSeguros }),
    [assinar, segurar, liberar, seguros, recarregarSeguros],
  );
  return <Ctx.Provider value={valor}>{children}</Ctx.Provider>;
}
