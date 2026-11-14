'use client';

import * as React from 'react';
import {
  LIMITE_VOZ_MS,
  concederConsentimento,
  consentimentoConcedido,
  criarReconhecedor,
  detectarModoVoz,
  vozSuportada,
  type EventoErroVoz,
  type EventoResultadoVoz,
  type ReconhecedorVoz,
} from './voz';

export type EstadoVoz =
  'indisponivel' | 'ocioso' | 'pedindo_permissao' | 'ouvindo' | 'processando' | 'erro';

export type MotivoErroVoz = 'permissao_negada' | 'sem_fala' | 'rede' | 'falha';

export const MENSAGEM_ERRO_VOZ: Record<MotivoErroVoz, string> = {
  permissao_negada:
    'O acesso ao microfone foi negado. Libere o microfone nas configurações do navegador para este site ou digite o texto manualmente.',
  sem_fala: 'Não ouvi nenhuma fala. Toque em "Falar" para tentar de novo ou digite o texto.',
  rede: 'O serviço de reconhecimento de voz não respondeu. Verifique a conexão e tente de novo, ou digite o texto.',
  falha: 'Não foi possível reconhecer a voz. Tente de novo ou digite o texto.',
};

function motivoDe(codigo: string): MotivoErroVoz | 'abortado' {
  switch (codigo) {
    case 'not-allowed':
    case 'service-not-allowed':
      return 'permissao_negada';
    case 'no-speech':
      return 'sem_fala';
    case 'network':
      return 'rede';
    case 'aborted':
      return 'abortado';
    default:
      return 'falha';
  }
}

/**
 * Captura de voz em uma única frase por vez. O microfone só liga em `iniciar()` (clique do
 * usuário). `resultado` é o texto final aguardando revisão; nada é enviado a lugar algum aqui.
 */
export function useVoz() {
  // No servidor (pré-render) e na hidratação: indisponível; depois, conforme o navegador.
  const suportado = React.useSyncExternalStore(
    () => () => {},
    vozSuportada,
    () => false,
  );
  const [estadoInterno, setEstado] = React.useState<EstadoVoz>('ocioso');
  const estado: EstadoVoz = suportado ? estadoInterno : 'indisponivel';
  const [parcial, setParcial] = React.useState('');
  const [resultado, setResultado] = React.useState('');
  const [erro, setErro] = React.useState<MotivoErroVoz | null>(null);
  const [aguardaConsentimento, setAguardaConsentimento] = React.useState(false);

  const recRef = React.useRef<ReconhecedorVoz | null>(null);
  const timerRef = React.useRef<number | null>(null);
  const geracaoRef = React.useRef(0);
  const textoRef = React.useRef('');
  const erroRef = React.useRef<MotivoErroVoz | null>(null);
  const canceladoRef = React.useRef(false);

  const limparTimer = () => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
  };

  const cancelar = React.useCallback(() => {
    geracaoRef.current += 1; // ignora eventos tardios
    canceladoRef.current = true;
    limparTimer();
    const r = recRef.current;
    recRef.current = null;
    if (r) {
      r.onstart = r.onresult = r.onerror = r.onend = null;
      try {
        r.abort();
      } catch {
        /* já encerrado */
      }
    }
    textoRef.current = '';
    setParcial('');
    setAguardaConsentimento(false);
    setEstado('ocioso');
  }, []);

  const parar = React.useCallback(() => {
    const r = recRef.current;
    if (!r) return;
    limparTimer();
    setEstado((e) => (e === 'ouvindo' || e === 'pedindo_permissao' ? 'processando' : e));
    try {
      r.stop();
    } catch {
      /* já encerrado */
    }
  }, []);

  const comecar = React.useCallback(async () => {
    const geracao = ++geracaoRef.current;
    canceladoRef.current = false;
    textoRef.current = '';
    erroRef.current = null;
    setErro(null);
    setParcial('');
    setResultado('');
    setEstado('pedindo_permissao');

    const modo = await detectarModoVoz();
    if (geracao !== geracaoRef.current) return;
    if (modo === 'servico' && !consentimentoConcedido()) {
      setAguardaConsentimento(true);
      setEstado('ocioso');
      return;
    }
    const r = criarReconhecedor(modo);
    if (!r) {
      setEstado('erro');
      setErro('falha');
      return;
    }
    recRef.current = r;
    r.onstart = () => {
      if (geracao !== geracaoRef.current) return;
      setEstado('ouvindo');
      timerRef.current = window.setTimeout(() => parar(), LIMITE_VOZ_MS);
    };
    r.onresult = (e: EventoResultadoVoz) => {
      if (geracao !== geracaoRef.current) return;
      let texto = '';
      for (let i = 0; i < e.results.length; i++) texto += e.results[i]?.[0]?.transcript ?? '';
      textoRef.current = texto.trim();
      setParcial(texto);
    };
    r.onerror = (e: EventoErroVoz) => {
      if (geracao !== geracaoRef.current) return;
      const m = motivoDe(e.error);
      if (m !== 'abortado') erroRef.current = m;
    };
    r.onend = () => {
      if (geracao !== geracaoRef.current) return;
      limparTimer();
      recRef.current = null;
      setParcial('');
      const final = textoRef.current;
      const motivo = erroRef.current ?? (final ? null : 'sem_fala');
      if (motivo) {
        setErro(motivo);
        setEstado('erro');
      } else {
        setResultado(final);
        setEstado('ocioso');
      }
    };
    try {
      r.start();
    } catch {
      erroRef.current = 'falha';
      setErro('falha');
      setEstado('erro');
      recRef.current = null;
    }
  }, [parar]);

  const iniciar = React.useCallback(() => {
    if (recRef.current || !vozSuportada()) return;
    void comecar();
  }, [comecar]);

  /** O usuário aceitou o aviso de privacidade: registra só em memória e inicia. */
  const consentir = React.useCallback(() => {
    concederConsentimento();
    setAguardaConsentimento(false);
    void comecar();
  }, [comecar]);

  const limparResultado = React.useCallback(() => setResultado(''), []);
  const limparErro = React.useCallback(() => {
    setErro(null);
    setEstado((e) => (e === 'erro' ? 'ocioso' : e));
  }, []);

  // Para ao desmontar e com a aba oculta (o microfone nunca fica ligado em segundo plano).
  React.useEffect(() => {
    const aoOcultar = () => {
      if (document.hidden) parar();
    };
    document.addEventListener('visibilitychange', aoOcultar);
    return () => {
      document.removeEventListener('visibilitychange', aoOcultar);
      cancelar();
    };
  }, [parar, cancelar]);

  return {
    estado,
    parcial,
    resultado,
    erro,
    aguardaConsentimento,
    iniciar,
    parar,
    cancelar,
    consentir,
    limparResultado,
    limparErro,
  };
}
