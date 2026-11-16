'use client';

import { RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useTempoReal } from '@/lib/tempo-real';

/**
 * Faixa exibida quando chegou atualização para a tela enquanto um formulário estava em edição:
 * nada é substituído sozinho; o usuário decide recarregar. A região aria-live existe sempre, para
 * o aviso ser anunciado quando aparecer.
 */
export function FaixaAtualizacao() {
  const tr = useTempoReal();
  const seguros = tr?.seguros;
  const visivel = !!seguros && seguros.size > 0;
  const texto = seguros?.has('requisicoes')
    ? 'Esta requisição foi atualizada.'
    : 'Esta tela foi atualizada.';
  return (
    <div aria-live="polite" className="px-5">
      {visivel ? (
        <div className="abertura-item mt-2 flex items-center justify-between gap-3 rounded-padrao border border-primaria/30 bg-white p-3">
          <p className="text-base">{texto}</p>
          <Button
            type="button"
            variante="contorno"
            className="shrink-0"
            onClick={() => tr?.recarregarSeguros()}
          >
            <RefreshCw aria-hidden className="size-4" />
            Recarregar
          </Button>
        </div>
      ) : null}
    </div>
  );
}
