'use client';

import * as React from 'react';
import { useAuth } from './auth';
import { emEdicao, useTempoReal } from './tempo-real';
import type { RecursoTempoReal } from './tipos';

type Opcoes = {
  /**
   * Recursos de GET /api/atualizacoes que afetam esta tela: quando a versão de um deles muda, os
   * dados são buscados de novo em silêncio (sem spinner). Com formulário em edição, a recarga
   * fica guardada e a faixa "Recarregar" aparece (o usuário decide).
   */
  recursos?: readonly RecursoTempoReal[];
};

/**
 * Carrega dados da API; erros (inclusive 401/403) passam por tratarErro e a mensagem é exibida.
 * `chave` (opcional) identifica os parâmetros da busca: ao mudar, os dados são recarregados.
 */
export function useCarga<T>(buscar: () => Promise<T>, chave = '', opcoes: Opcoes = {}) {
  const { tratarErro } = useAuth();
  const tempoReal = useTempoReal();
  const id = React.useId();
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

  const liberar = tempoReal?.liberar;
  const recarregar = React.useCallback(async () => {
    const minha = ++ultimaBusca.current;
    // Qualquer recarga (ação, botão, tempo real) já traz o estado atual: some a faixa desta tela.
    liberar?.(id);
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
  }, [tratarErro, liberar, id]);

  React.useEffect(() => {
    void recarregar();
  }, [recarregar, chave]);

  // Lista fechada e pequena: a string evita reassinar a cada render por causa do array literal.
  const recursosChave = (opcoes.recursos ?? []).join(',');
  const assinar = tempoReal?.assinar;
  const segurar = tempoReal?.segurar;
  React.useEffect(() => {
    if (!assinar || !segurar || !recursosChave) return;
    const recursos = recursosChave.split(',') as RecursoTempoReal[];
    const cancelar = assinar(recursos, () => {
      if (emEdicao()) segurar(id, recursos, () => void recarregar());
      else void recarregar();
    });
    return () => {
      cancelar();
      liberar?.(id);
    };
  }, [assinar, segurar, liberar, recursosChave, recarregar, id]);

  return { dados, setDados, erro, carregando, recarregar };
}
