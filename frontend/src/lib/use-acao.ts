'use client';

import * as React from 'react';
import { ACOES, api } from './api';
import { useAuth } from './auth';
import { useTatil } from './tatil';
import type { Acao, CorpoAcao, Requisicao } from './tipos';

/** Executa uma ação da API sobre uma requisição; o erro é exibido como veio. */
export function useAcao(depois: (atualizada: Requisicao) => void | Promise<void>) {
  const { tratarErro } = useAuth();
  const tatil = useTatil();
  const [ocupado, setOcupado] = React.useState(false);
  const [emAndamento, setEmAndamento] = React.useState<{ id: string; acao: Acao } | null>(null);
  const [erro, setErro] = React.useState<string | null>(null);
  const trava = React.useRef(false);

  const executar = React.useCallback(
    async (req: Requisicao, acao: Acao, corpo?: CorpoAcao) => {
      if (trava.current) return;
      trava.current = true;
      setEmAndamento({ id: req.id, acao });
      setOcupado(true);
      setErro(null);
      let confirmado = false;
      try {
        const atualizada = await api.executar(req.id, ACOES[acao].rota, corpo);
        // Só após a resposta do backend; complemento, nada depende dele.
        confirmado = true;
        if (acao === 'registrar_saida') tatil.saida();
        else if (acao === 'registrar_chegada') tatil.chegada();
        else tatil.confirmacao();
        await depois(atualizada);
      } catch (e) {
        if (!confirmado) tatil.erro();
        setErro(tratarErro(e));
        // Estado pode ter mudado no servidor (409): recarrega para refletir acoes_permitidas atuais.
        await depois(req);
      } finally {
        trava.current = false;
        setEmAndamento(null);
        setOcupado(false);
      }
    },
    [depois, tratarErro, tatil],
  );

  return { ocupado, emAndamento, erro, executar };
}
