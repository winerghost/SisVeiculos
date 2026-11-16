'use client';

import * as React from 'react';
import { ACOES, ApiErro, api } from './api';
import { useAuth } from './auth';
import { focarTituloSePerdido } from './foco';
import { useTatil } from './tatil';
import type { Acao, CorpoAcao, Requisicao } from './tipos';

/**
 * Pedido de confirmação devolvido pela API (409 `km_alto_confirmar`). A conta e o limite são do
 * servidor; o cliente só mostra a mensagem e, se confirmado, reenvia o mesmo corpo com a flag.
 */
export type ConfirmacaoKm = {
  mensagem: string;
  ocupado: boolean;
  confirmar: () => void;
  corrigir: () => void;
};

type Pendente = { req: Requisicao; acao: Acao; corpo?: CorpoAcao; mensagem: string };

/** Executa uma ação da API sobre uma requisição; o erro é exibido como veio. */
export function useAcao(depois: (atualizada: Requisicao) => void | Promise<void>) {
  const { tratarErro } = useAuth();
  const tatil = useTatil();
  const [ocupado, setOcupado] = React.useState(false);
  const [emAndamento, setEmAndamento] = React.useState<{ id: string; acao: Acao } | null>(null);
  const [erro, setErro] = React.useState<string | null>(null);
  const [pendente, setPendente] = React.useState<Pendente | null>(null);
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
        setPendente(null);
        if (acao === 'registrar_saida') tatil.saida();
        else if (acao === 'registrar_chegada') tatil.chegada();
        else tatil.confirmacao();
        await depois(atualizada);
        // O botão usado pode sumir na recarga (a ação sai de acoes_permitidas): o foco vai ao
        // título da página em vez de cair no <body>.
        focarTituloSePerdido();
      } catch (e) {
        if (!confirmado && e instanceof ApiErro && e.codigo === 'km_alto_confirmar') {
          // Nada foi gravado: a API pede confirmação. Mantém o formulário como está.
          setPendente({ req, acao, corpo, mensagem: e.message });
          return;
        }
        setPendente(null);
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

  const confirmacaoKm: ConfirmacaoKm | null = pendente
    ? {
        mensagem: pendente.mensagem,
        ocupado,
        confirmar: () =>
          void executar(pendente.req, pendente.acao, {
            ...pendente.corpo,
            confirmar_km_alto: true,
          }),
        corrigir: () => {
          const { req, acao } = pendente;
          setPendente(null);
          // Volta ao campo de km de chegada, com o valor digitado intacto.
          requestAnimationFrame(() =>
            document.getElementById(`${req.id}-${acao}-km_chegada`)?.focus(),
          );
        },
      }
    : null;

  return { ocupado, emAndamento, erro, executar, confirmacaoKm };
}
