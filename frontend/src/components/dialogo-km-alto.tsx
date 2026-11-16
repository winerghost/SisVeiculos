'use client';

import { Button } from '@/components/ui/button';
import { Dialogo } from '@/components/ui/dialogo';
import type { ConfirmacaoKm } from '@/lib/use-acao';

/**
 * Confirmação de distância alta na chegada (409 `km_alto_confirmar`). Exibe a mensagem da API
 * como veio; a conta e o limite são do servidor.
 */
export function DialogoKmAlto({ confirmacao }: { confirmacao: ConfirmacaoKm | null }) {
  return (
    <Dialogo
      aberto={!!confirmacao}
      titulo="Confirmar quilometragem"
      descricao={confirmacao?.mensagem}
      bloqueado={!!confirmacao?.ocupado}
      // Durante o envio nada fecha o diálogo como "corrigir": a chegada ainda pode ser gravada.
      onFechar={() => {
        if (!confirmacao?.ocupado) confirmacao?.corrigir();
      }}
    >
      <div className="flex flex-col gap-3">
        <Button
          type="button"
          tamanho="cheio"
          variante="contorno"
          data-autofocus
          disabled={confirmacao?.ocupado}
          onClick={() => confirmacao?.corrigir()}
        >
          Corrigir km
        </Button>
        <Button
          type="button"
          tamanho="cheio"
          disabled={confirmacao?.ocupado}
          carregando={confirmacao?.ocupado}
          onClick={() => confirmacao?.confirmar()}
        >
          {confirmacao?.ocupado ? 'Registrando chegada...' : 'Confirmar e registrar'}
        </Button>
      </div>
    </Dialogo>
  );
}
