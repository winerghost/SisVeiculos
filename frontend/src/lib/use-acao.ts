'use client';

import * as React from 'react';
import { ACOES, api } from './api';
import { useAuth } from './auth';
import type { Acao, CorpoAcao, Requisicao } from './tipos';

/** Executa uma ação da API sobre uma requisição; o erro é exibido como veio. */
export function useAcao(depois: (atualizada: Requisicao) => void | Promise<void>) {
  const { tratarErro } = useAuth();
  const [ocupado, setOcupado] = React.useState(false);
  const [erro, setErro] = React.useState<string | null>(null);

  const executar = React.useCallback(
    async (req: Requisicao, acao: Acao, corpo?: CorpoAcao) => {
      setOcupado(true);
      setErro(null);
      try {
        const atualizada = await api.executar(req.id, ACOES[acao].rota, corpo);
        await depois(atualizada);
      } catch (e) {
        setErro(tratarErro(e));
        // Estado pode ter mudado no servidor (409): recarrega para refletir acoes_permitidas atuais.
        await depois(req);
      } finally {
        setOcupado(false);
      }
    },
    [depois, tratarErro],
  );

  return { ocupado, erro, executar };
}
