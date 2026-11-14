'use client';

import * as React from 'react';
import { api, ApiErro } from './api';
import { inscricaoAtual } from './push';
import { supabase } from './supabase';
import type { Eu, PainelMenu, RespostaRelatorios } from './tipos';

type Estado = 'carregando' | 'anonimo' | 'mfa' | 'ativo' | 'falha';

interface Contexto {
  estado: Estado;
  eu: Eu | null;
  /**
   * Painéis de dashboard que a API liberou (GET /api/dashboards). `null` = sem acesso ou ainda
   * não carregado: o item de menu só aparece quando a chamada dá certo (nunca por role no cliente).
   */
  paineis: PainelMenu[] | null;
  /**
   * Catálogo de relatórios (GET /api/relatorios). `null` = sem acesso ou não carregado: o item
   * de menu e os botões de exportar só aparecem quando a chamada dá certo. Só em memória.
   */
  relatorios: RespostaRelatorios | null;
  /** Notificações não lidas (GET /api/notificacoes/contagem). Só em memória; zera ao sair. */
  naoLidas: number;
  recarregarNaoLidas: () => Promise<void>;
  /** Mensagem recebida da API/SDK para exibir na tela de login ou de erro. */
  aviso: string | null;
  entrar: (email: string, senha: string) => Promise<string | null>;
  sair: () => Promise<void>;
  verificarMfa: (codigo: string) => Promise<string | null>;
  recarregar: () => Promise<void>;
  /** Trata 401/403 de qualquer chamada e devolve a mensagem a exibir. */
  tratarErro: (e: unknown) => string;
}

const Ctx = React.createContext<Contexto | null>(null);

export function useAuth(): Contexto {
  const c = React.useContext(Ctx);
  if (!c) throw new Error('useAuth fora do AuthProvider');
  return c;
}

async function revogarPushDesteAparelho(): Promise<void> {
  try {
    if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return;
    const insc = await inscricaoAtual();
    if (!insc) return;
    try {
      // Revogação no servidor é melhor esforço, com prazo próprio.
      await Promise.race([
        api.pushRevogar(insc.endpoint),
        new Promise<void>((r) => window.setTimeout(r, 1500)),
      ]);
    } catch {
      // Sem rede/401/recurso desligado: o cancelamento local abaixo segue mesmo assim.
    }
    // Sempre cancela a inscrição LOCAL, para o aparelho não receber avisos do usuário anterior.
    await insc.unsubscribe();
  } catch {
    // Sem service worker/inscrição: segue o logout.
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [estado, setEstado] = React.useState<Estado>('carregando');
  const [eu, setEu] = React.useState<Eu | null>(null);
  const [relatorios, setRelatorios] = React.useState<RespostaRelatorios | null>(null);
  const [aviso, setAviso] = React.useState<string | null>(null);
  const [paineis, setPaineis] = React.useState<PainelMenu[] | null>(null);

  const [naoLidas, setNaoLidas] = React.useState(0);

  // Trava de reentrada: dois logouts/401 simultâneos resultam em um único encerramento.
  const encerrando = React.useRef(false);

  // Encerra a sessão. Logout manual usa escopo global (revoga os refresh tokens de todos os
  // dispositivos); expiração/erro de sessão usa escopo local. O estado local e os caches de
  // páginas são limpos ANTES de aguardar a rede, e o signOut global tem prazo curto (3 s) com
  // queda para o escopo local, para que rede lenta nunca prenda o usuário na tela.
  const encerrar = React.useCallback(async (mensagem: string | null, global: boolean) => {
    if (encerrando.current) return;
    encerrando.current = true;
    try {
      const auth = supabase().auth;
      // Melhor esforço, antes do signOut (precisa do token): revoga a inscrição push DESTE
      // aparelho, com prazo curto para nunca prender o logout.
      await Promise.race([
        revogarPushDesteAparelho(),
        new Promise<void>((r) => window.setTimeout(r, 2000)),
      ]);
      // Dispara a revogação já (lê o token do armazenamento) antes de limpar o estado.
      const remoto = auth
        .signOut({ scope: global ? 'global' : 'local' })
        .then(({ error }) => error)
        .catch(() => new Error('falha'));

      setEu(null);
      setPaineis(null);
      setRelatorios(null);
      setNaoLidas(0);
      setAviso(mensagem);
      setEstado('anonimo');

      // Só as páginas (HTML sem dado de usuário, mas por precaução); assets-* são estáticos.
      try {
        if (typeof caches !== 'undefined') {
          const nomes = await caches.keys();
          await Promise.all(
            nomes.filter((n) => n.startsWith('paginas-')).map((n) => caches.delete(n)),
          );
        }
      } catch {
        // Cache Storage indisponível: nada a limpar.
      }

      const prazo = new Promise<'prazo'>((r) => window.setTimeout(() => r('prazo'), 3000));
      const r = await Promise.race([remoto, prazo]);
      // Global que estourou o prazo ou falhou (sem rede/token inválido): encerra ao menos o local.
      if (global && r) await auth.signOut({ scope: 'local' }).catch(() => undefined);
    } finally {
      encerrando.current = false;
    }
  }, []);

  const sairLocal = React.useCallback((m: string | null) => encerrar(m, false), [encerrar]);

  const tratarErro = React.useCallback(
    (e: unknown): string => {
      if (e instanceof ApiErro) {
        if (e.status === 401) {
          void sairLocal(e.message);
        } else if (e.status === 403 && e.codigo === 'mfa_requerido') {
          setAviso(e.message);
          setEstado('mfa');
        } else if (e.status === 403 && e.codigo === 'conta_inativa') {
          void sairLocal(e.message);
        }
        return e.message;
      }
      return 'Não foi possível concluir a operação.';
    },
    [sairLocal],
  );

  const recarregarNaoLidas = React.useCallback(async () => {
    try {
      setNaoLidas((await api.contagemNotificacoes()).nao_lidas);
    } catch (e) {
      // Falha de contagem não bloqueia o app; 401/403 seguem o tratamento padrão.
      tratarErro(e);
    }
  }, [tratarErro]);

  // Contagem inicial e polling a cada 60s apenas com a aba visível (e ao voltar a ela).
  React.useEffect(() => {
    if (estado !== 'ativo') return;
    const tick = () => {
      if (document.visibilityState === 'visible') void recarregarNaoLidas();
    };
    tick();
    const id = window.setInterval(tick, 60_000);
    document.addEventListener('visibilitychange', tick);
    return () => {
      window.clearInterval(id);
      document.removeEventListener('visibilitychange', tick);
    };
  }, [estado, recarregarNaoLidas]);

  const carregarEu = React.useCallback(async () => {
    try {
      setEu(await api.eu());
      setAviso(null);
      setEstado('ativo');
      // Só em memória (sem persistência). Qualquer falha (inclusive 403) apenas oculta o menu.
      api
        .paineisDashboard()
        .then((r) => setPaineis(r.paineis))
        .catch(() => setPaineis(null));
      api
        .relatorios()
        .then(setRelatorios)
        .catch(() => setRelatorios(null));
    } catch (e) {
      const msg = tratarErro(e);
      if (!(e instanceof ApiErro) || (e.status !== 401 && e.status !== 403)) {
        setAviso(msg);
        setEstado('falha');
      }
    }
  }, [tratarErro]);

  React.useEffect(() => {
    let ativo = true;
    void (async () => {
      const { data } = await supabase().auth.getSession();
      if (!ativo) return;
      // Página de aceite de convite: a conta ainda não tem perfil e /api/eu responderia 403
      // conta_inativa, o que encerraria a sessão do convite. A própria página conduz o fluxo.
      if (window.location.pathname.replace(/\/$/, '') === '/convite') setEstado('anonimo');
      else if (data.session) await carregarEu();
      else setEstado('anonimo');
    })();
    return () => {
      ativo = false;
    };
  }, [carregarEu]);

  const entrar = React.useCallback(
    async (email: string, senha: string): Promise<string | null> => {
      const { error } = await supabase().auth.signInWithPassword({ email, password: senha });
      if (error) return 'E-mail ou senha incorretos.';
      setEstado('carregando');
      await carregarEu();
      return null;
    },
    [carregarEu],
  );

  const sair = React.useCallback(() => encerrar(null, true), [encerrar]);

  // Segundo fator (TOTP): só é chamado quando a API responde mfa_requerido.
  const verificarMfa = React.useCallback(
    async (codigo: string): Promise<string | null> => {
      const auth = supabase().auth;
      const { data: fatores, error: e1 } = await auth.mfa.listFactors();
      const fator = fatores?.totp?.[0];
      if (e1 || !fator) {
        return 'Nenhum autenticador cadastrado para esta conta. Procure o administrador.';
      }
      const { data: desafio, error: e2 } = await auth.mfa.challenge({ factorId: fator.id });
      if (e2 || !desafio) return 'Não foi possível iniciar a verificação. Tente novamente.';
      const { error: e3 } = await auth.mfa.verify({
        factorId: fator.id,
        challengeId: desafio.id,
        code: codigo,
      });
      if (e3) return 'Código inválido ou expirado. Tente novamente.';
      await carregarEu();
      return null;
    },
    [carregarEu],
  );

  const valor = React.useMemo<Contexto>(
    () => ({
      estado,
      eu,
      paineis,
      relatorios,
      naoLidas,
      recarregarNaoLidas,
      aviso,
      entrar,
      sair,
      verificarMfa,
      recarregar: carregarEu,
      tratarErro,
    }),
    [
      estado,
      eu,
      paineis,
      relatorios,
      naoLidas,
      recarregarNaoLidas,
      aviso,
      entrar,
      sair,
      verificarMfa,
      carregarEu,
      tratarErro,
    ],
  );
  return <Ctx.Provider value={valor}>{children}</Ctx.Provider>;
}
