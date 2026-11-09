'use client';

import * as React from 'react';
import { usePathname } from 'next/navigation';
import { Sparkles, X } from 'lucide-react';
import { useAuth } from '@/lib/auth';
import { api } from '@/lib/api';
import { supabase } from '@/lib/supabase';

/**
 * Memória só do módulo (some ao recarregar a página; nada vai para storage, por LGPD). O front não
 * calcula período nem data: compara a `chave` recebida e usa `valido_ate` como validade do cache.
 */
interface Memoria {
  usuario: string | null;
  buscou: boolean;
  texto: string | null;
  chave: string | null;
  validoAte: number | null;
  tela: string | null;
  dispensada: string | null;
  busca: Promise<void> | null;
  /** Instante da última tentativa de busca (sucesso ou falha), base do throttle. */
  ultimaBusca: number;
}

const vazia = (): Memoria => ({
  usuario: null,
  buscou: false,
  texto: null,
  chave: null,
  validoAte: null,
  tela: null,
  dispensada: null,
  busca: null,
  ultimaBusca: 0,
});

let memoria: Memoria = vazia();
let ouvindoSaida = false;

/** Identidade do momento: a chave da API (ou o próprio texto, se a API ainda não enviar chave). */
const identidade = (m: Memoria) => m.chave ?? m.texto;

function esquecer() {
  memoria = vazia();
}

/** Intervalo mínimo entre tentativas (protege o rate limit mesmo com relógio do cliente adiantado). */
const INTERVALO_MIN_MS = 60_000;
/** Sem texto e sem validade (função desligada): revalida com esta folga, só em rota/foco. */
const REVALIDAR_SEM_TEXTO_MS = 10 * 60_000;

function precisaBuscar(): boolean {
  if (!memoria.buscou) return true;
  const agora = Date.now();
  const desde = agora - memoria.ultimaBusca;
  if (desde < INTERVALO_MIN_MS) return false;
  if (memoria.validoAte !== null) return agora >= memoria.validoAte;
  return memoria.texto === null && desde >= REVALIDAR_SEM_TEXTO_MS;
}

/** Garante dados válidos: busca na primeira vez ou após `valido_ate`; buscas concorrentes são unificadas. */
function garantir(usuario: string): Promise<void> {
  if (!ouvindoSaida) {
    ouvindoSaida = true;
    // Ao sair, nada da sessão anterior (inclusive a dispensa) fica na memória.
    supabase().auth.onAuthStateChange((evento) => {
      if (evento === 'SIGNED_OUT') esquecer();
    });
  }
  if (memoria.usuario !== usuario) {
    esquecer();
    memoria.usuario = usuario;
  }
  if (memoria.busca) return memoria.busca;
  if (Date.now() - memoria.ultimaBusca < INTERVALO_MIN_MS && memoria.ultimaBusca !== 0) {
    return Promise.resolve();
  }
  if (!precisaBuscar()) return Promise.resolve();

  const dono = memoria;
  dono.ultimaBusca = Date.now();
  const busca: Promise<void> = api
    .saudacao()
    .then((r) => {
      if (memoria !== dono) return;
      const texto = typeof r.texto === 'string' && r.texto.trim() !== '' ? r.texto : null;
      const chave = typeof r.chave === 'string' && r.chave !== '' ? r.chave : null;
      const ate = typeof r.valido_ate === 'string' ? Date.parse(r.valido_ate) : NaN;
      const anterior = identidade(dono);
      dono.texto = texto;
      dono.chave = chave;
      dono.validoAte = Number.isNaN(ate) ? null : ate;
      dono.buscou = true;
      // Momento novo: volta a aparecer na tela corrente, que vira o novo primeiro local de exibição.
      if (identidade(dono) !== anterior) dono.tela = null;
    })
    .catch(() => {
      // Falha não é memorizada: a próxima checagem tenta de novo.
    })
    .finally(() => {
      if (dono.busca === busca) dono.busca = null;
    });
  dono.busca = busca;
  return busca;
}

interface Exibida {
  id: string;
  texto: string;
}

/** Banner de boas-vindas (entra animado, pode ser fechado). Sem texto da API (ou com falha), não renderiza nada. */
export function Saudacao() {
  const { eu } = useAuth();
  const caminho = usePathname();
  const usuario = eu?.id ?? null;
  const [exibida, setExibida] = React.useState<Exibida | null>(null);
  const [saindo, setSaindo] = React.useState(false);
  const idExibido = React.useRef<string | null>(null);

  React.useEffect(() => {
    if (!usuario) return;
    let ativo = true;
    const verificar = () => {
      void garantir(usuario).then(() => {
        if (!ativo || memoria.usuario !== usuario) return;
        const id = identidade(memoria);
        if (!memoria.texto || id === null || id === memoria.dispensada) {
          idExibido.current = null;
          setExibida(null);
          return;
        }
        // Aparece só na primeira tela em que foi exibida naquele momento; as demais ficam limpas.
        if (memoria.tela === null) memoria.tela = caminho;
        if (memoria.tela !== caminho) {
          idExibido.current = null;
          setExibida(null);
          return;
        }
        const texto = memoria.texto;
        if (idExibido.current !== id) {
          idExibido.current = id;
          setSaindo(false);
          setExibida({ id, texto });
        }
      });
    };
    verificar();
    // Ao voltar para a aba, renova se a validade passou (sem timers nem buscas a cada render).
    const aoVoltar = () => {
      if (document.visibilityState === 'visible') verificar();
    };
    document.addEventListener('visibilitychange', aoVoltar);
    return () => {
      ativo = false;
      document.removeEventListener('visibilitychange', aoVoltar);
    };
  }, [usuario, caminho]);

  function fechar() {
    if (exibida && memoria.usuario === usuario) memoria.dispensada = exibida.id;
    setSaindo(true);
  }

  if (!usuario || !exibida) return null;
  const texto = exibida.texto;

  // Palavras reveladas em cascata (visual); o texto segue puro e contínuo para leitores de tela.
  const palavras = texto.split(/\s+/).filter(Boolean);

  return (
    <div
      key={exibida.id}
      onTransitionEnd={(e) => {
        if (saindo && e.target === e.currentTarget) {
          idExibido.current = null;
          setExibida(null);
        }
      }}
      className={`grid transition-[grid-template-rows,opacity] duration-500 ease-in-out ${
        saindo ? 'grid-rows-[0fr] opacity-0' : 'grid-rows-[1fr] opacity-100'
      }`}
    >
      <div className="min-h-0 overflow-hidden">
        <div
          role="status"
          className="saudacao relative mb-4 flex items-start gap-3 overflow-hidden rounded-padrao border border-borda border-l-4 border-l-primaria bg-white px-4 py-3"
        >
          <span
            aria-hidden
            className="saudacao-icone flex size-8 shrink-0 items-center justify-center rounded-full bg-primaria/10 text-primaria"
          >
            <Sparkles className="size-4" />
          </span>
          <p className="min-w-0 flex-1 self-center text-base text-texto">
            {palavras.map((p, i) => (
              <React.Fragment key={i}>
                {i > 0 ? ' ' : null}
                <span
                  className="saudacao-palavra"
                  style={{ '--i': Math.min(i, 24) } as React.CSSProperties}
                >
                  {p}
                </span>
              </React.Fragment>
            ))}
          </p>
          <button
            type="button"
            onClick={fechar}
            aria-label="Fechar saudação"
            className="-my-1 -mr-2 flex size-11 shrink-0 items-center justify-center rounded-padrao text-texto-2"
          >
            <X aria-hidden className="size-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
