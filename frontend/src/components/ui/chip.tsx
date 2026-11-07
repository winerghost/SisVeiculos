import * as React from 'react';
import { cn } from '@/lib/utils';

// Chips de apresentação (cores em docs/mockups/README.md).
const TONS = {
  neutro: 'bg-[#e8ecf2] text-[#3b4658]',
  aviso: 'bg-[#fff1d6] text-[#7a4a00]',
  info: 'bg-[#dcebff] text-[#0b3c7a]',
  sucesso: 'bg-[#d9f2e6] text-[#0a5a3a]',
  perigo: 'bg-[#fde2e0] text-[#8a1f17]',
  admin: 'bg-[#e9e3fa] text-[#3f2a7a]',
} as const;

export function Chip({
  tom = 'neutro',
  children,
}: {
  tom?: keyof typeof TONS;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn('rounded-full px-3 py-1 text-sm font-semibold whitespace-nowrap', TONS[tom])}
    >
      {children}
    </span>
  );
}

const TOM_ROLE: Record<string, keyof typeof TONS> = {
  admin: 'admin',
  autorizador: 'info',
  solicitante: 'neutro',
};
const ROTULO_ROLE: Record<string, string> = {
  admin: 'Administrador',
  autorizador: 'Autorizador',
  solicitante: 'Solicitante',
};

/** Rótulos dos valores de `role` (apresentação; a API valida o valor enviado). */
export const ROLES = ['solicitante', 'autorizador', 'admin'] as const;
export const rotuloRole = (role: string) => ROTULO_ROLE[role] ?? role;

export function ChipRole({ role }: { role: string }) {
  return <Chip tom={TOM_ROLE[role] ?? 'neutro'}>{rotuloRole(role)}</Chip>;
}
