'use client';

import * as React from 'react';
import { useAuth } from './auth';

/**
 * Carrega dados da API; erros (inclusive 401/403) passam por tratarErro e a mensagem é exibida.
 * `chave` (opcional) identifica os parâmetros da busca: ao mudar, os dados são recarregados.
 */
export function useCarga<T>(buscar: () => Promise<T>, chave = '') {
  const { tratarErro } = useAuth();
  const [dados, setDados] = React.useState<T | null>(null);
  const [erro, setErro] = React.useState<string | null>(null);
  const [carregando, setCarregando] = React.useState(true);
  const buscarRef = React.useRef(buscar);
  React.useEffect(() => {
    buscarRef.current = buscar;
  });

  // Numera cada busca: só a resposta da busca mais recente é aplicada. Sem isso, uma resposta
  // antiga que chegue depois de uma recarga posterior (ex.: após registrar uma ação) sobrescreveria
  // a lista nova com dados velhos.
  const ultimaBusca = React.useRef(0);

  const recarregar = React.useCallback(async () => {
    const minha = ++ultimaBusca.current;
    try {
      const resultado = await buscarRef.current();
      if (minha !== ultimaBusca.current) return;
      setDados(resultado);
      setErro(null);
    } catch (e) {
      if (minha !== ultimaBusca.current) return;
      setErro(tratarErro(e));
    } finally {
      if (minha === ultimaBusca.current) setCarregando(false);
    }
  }, [tratarErro]);

  React.useEffect(() => {
    void recarregar();
  }, [recarregar, chave]);

  return { dados, setDados, erro, carregando, recarregar };
}
