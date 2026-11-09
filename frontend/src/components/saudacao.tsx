'use client';

import * as React from 'react';
import { usePathname } from 'next/navigation';
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

/** Banner discreto de boas-vindas. Sem texto da API (ou com falha), não renderiza nada. */
export function Saudacao() {
  const { eu } = useAuth();
  const caminho = usePathname();
  const usuario = eu?.id ?? null;
  const [texto, setTexto] = React.useState<string | null>(null);

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
  return (
    <p
      role="status"
      className="mb-4 rounded-padrao border border-borda bg-white px-4 py-3 text-base text-texto-2"
    >
      {texto}
    </p>
  );
}
