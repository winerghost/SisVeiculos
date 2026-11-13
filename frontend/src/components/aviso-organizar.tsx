'use client';

import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import type { AcaoOrganizar } from '@/lib/tipos';
import type { AvisoOrganizar } from '@/lib/use-organizar';

/**
 * Resultado de arquivar/desarquivar: região de status (anunciada por leitor de tela), com
 * "Desfazer" quando a API liberou a ação inversa, e o erro da API como veio.
 */
export function AvisoOrganizarRegiao({
  aviso,
  erro,
  ocupado,
  onDesfazer,
}: {
  aviso: AvisoOrganizar | null;
  erro: string | null;
  ocupado: boolean;
  onDesfazer: (id: string, acao: AcaoOrganizar) => void;
}) {
  return (
    <>
      <div role="status" aria-live="polite">
        {aviso ? (
          <div className="mb-3 flex flex-wrap items-center gap-2 rounded-padrao border border-borda bg-white p-2 pl-3">
            <span className="text-base font-medium">{aviso.texto}</span>
            {aviso.desfazer ? (
              <Button
                type="button"
                variante="contorno"
                disabled={ocupado}
                onClick={() => onDesfazer(aviso.desfazer!.id, aviso.desfazer!.acao)}
              >
                Desfazer
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
      {erro ? <Alerta className="mb-4">{erro}</Alerta> : null}
    </>
  );
}
