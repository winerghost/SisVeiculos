import * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * Interruptor acessível (role=switch). Alvo de toque de 44px; ligado #0F3D75, desligado #8493AB.
 * Só exibe e dispara onChange: quem decide se pode ligar/desligar é a API (acoes_permitidas).
 */
export function Switch({
  checked,
  onChange,
  disabled,
  ...props
}: {
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'onChange' | 'onClick'>) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={onChange}
      className="flex min-h-11 min-w-14 shrink-0 items-center justify-center disabled:opacity-50"
      {...props}
    >
      <span
        aria-hidden
        className={cn(
          'flex h-8 w-14 items-center rounded-full p-1 transition-colors',
          checked ? 'justify-end bg-primaria' : 'justify-start bg-borda-campo',
        )}
      >
        <span className="size-6 rounded-full bg-white" />
      </span>
    </button>
  );
}
