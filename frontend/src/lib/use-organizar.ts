'use client';

import * as React from 'react';
import { api } from './api';
import { useAuth } from './auth';
import type { AcaoOrganizar } from './tipos';

export interface AvisoOrganizar {
  texto: string;
  /** Ação inversa, oferecida como "Desfazer" só se a API a liberou na resposta. */
  desfazer: { id: string; acao: AcaoOrganizar } | null;
}

const INVERSA: Record<AcaoOrganizar, AcaoOrganizar> = {
  arquivar: 'desarquivar',
  desarquivar: 'arquivar',
};

/**
 * Arquivar/desarquivar (preferência pessoal; a API decide se pode em `acoes_permitidas`).
 * O erro é exibido como veio e a lista é sempre recarregada, pois o estado pode ter mudado.
 * `viagem` usa as rotas do motorista (/api/motorista/viagens/...).
 */
export function useOrganizar(depois: () => void | Promise<void>, viagem = false) {
  const { tratarErro } = useAuth();
  const [processando, setProcessando] = React.useState<ReadonlySet<string>>(() => new Set());
  const emVoo = React.useRef<Set<string>>(new Set());
  const [erro, setErro] = React.useState<string | null>(null);
  const [aviso, setAviso] = React.useState<AvisoOrganizar | null>(null);
  const nome = viagem ? 'Viagem' : 'Requisição';
  const ocupado = processando.size > 0;

  const executar = React.useCallback(
    async (id: string, acao: AcaoOrganizar, oferecerDesfazer = true) => {
      if (emVoo.current.has(id)) return;
      emVoo.current.add(id);
      setProcessando(new Set(emVoo.current));
      setErro(null);
      setAviso(null);
      try {
        const r = await (viagem ? api.organizarViagem : api.organizar)(id, acao);
        const inversa = INVERSA[acao];
        setAviso({
          texto: `${nome} ${acao === 'arquivar' ? 'arquivada' : 'desarquivada'}.`,
          desfazer:
            oferecerDesfazer && r.acoes_permitidas.includes(inversa) ? { id, acao: inversa } : null,
        });
      } catch (e) {
        setErro(tratarErro(e));
      } finally {
        try {
          await depois();
        } finally {
          emVoo.current.delete(id);
          setProcessando(new Set(emVoo.current));
        }
      }
    },
    [depois, nome, tratarErro, viagem],
  );

  const limpar = React.useCallback(() => {
    setErro(null);
    setAviso(null);
  }, []);

  return { ocupado, processando, erro, aviso, executar, limpar };
}
