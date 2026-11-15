import * as React from 'react';
import { AlertCircle } from 'lucide-react';
import { cn } from '@/lib/utils';

/** Mensagem de erro exibida exatamente como veio da API. role=alert anuncia ao leitor de tela. */
export function Alerta({
  id,
  titulo,
  children,
  className,
}: {
  /** Permite ligar a mensagem a um campo via aria-describedby. */
  id?: string;
  titulo?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      id={id}
      role="alert"
      className={cn(
        'flex gap-3 rounded-padrao border border-erro/40 bg-erro-fundo p-3 text-erro',
        className,
      )}
    >
      <AlertCircle aria-hidden className="mt-0.5 size-5 shrink-0" />
      <div className="text-base">
        {titulo ? <p className="font-semibold">{titulo}</p> : null}
        <p>{children}</p>
      </div>
    </div>
  );
}
