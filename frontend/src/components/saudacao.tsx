'use client';

import * as React from 'react';
import { usePathname } from 'next/navigation';
import { Sparkles, X } from 'lucide-react';
import { useAuth } from '@/lib/auth';
import { api } from '@/lib/api';
import { supabase } from '@/lib/supabase';

/**
 * Cache só em memória (some ao recarregar a página ou sair): nada vai para storage. Uma busca por
 * usuário por carga de página; o texto vem pronto da API e é exibido como texto puro.
 */
const memoria: {
  usuario: string | null;
  busca: Promise<string | null> | null;
  tela: string | null;
} = { usuario: null, busca: null, tela: null };

let ouvindoSaida = false;

function esquecer() {
  memoria.usuario = null;
  memoria.busca = null;
  memoria.tela = null;
}

function buscar(usuario: string): Promise<string | null> {
  if (!ouvindoSaida) {
    ouvindoSaida = true;
    // Ao sair, nada da sessão anterior fica na memória.
    supabase().auth.onAuthStateChange((evento) => {
      if (evento === 'SIGNED_OUT') esquecer();
    });
  }
  if (memoria.usuario !== usuario || !memoria.busca) {
    memoria.usuario = usuario;
    memoria.tela = null;
    const busca: Promise<string | null> = api
      .saudacao()
      .then((r) => (typeof r.texto === 'string' && r.texto.trim() !== '' ? r.texto : null))
      .catch(() => {
        // Falha não é memorizada: a próxima montagem tenta de novo.
        if (memoria.busca === busca) memoria.busca = null;
        return null;
      });
    memoria.busca = busca;
  }
  return memoria.busca;
}

/** Banner de boas-vindas (entra animado, pode ser fechado). Sem texto da API (ou com falha), não renderiza nada. */
export function Saudacao() {
  const { eu } = useAuth();
  const caminho = usePathname();
  const usuario = eu?.id ?? null;
  const [texto, setTexto] = React.useState<string | null>(null);
  const [saindo, setSaindo] = React.useState(false);

  React.useEffect(() => {
    if (!usuario) return;
    let ativo = true;
    void buscar(usuario).then((t) => {
      if (!ativo || !t) return;
      // Aparece só na primeira tela em que foi exibida; as demais telas da sessão ficam limpas.
      if (memoria.tela === null) memoria.tela = caminho;
      if (memoria.tela === caminho) setTexto(t);
    });
    return () => {
      ativo = false;
    };
  }, [usuario, caminho]);

  if (!texto) return null;

  // Palavras reveladas em cascata (visual); o texto segue puro e contínuo para leitores de tela.
  const palavras = texto.split(/\s+/).filter(Boolean);

  return (
    <div
      onTransitionEnd={(e) => {
        if (saindo && e.target === e.currentTarget) setTexto(null);
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
            onClick={() => setSaindo(true)}
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
