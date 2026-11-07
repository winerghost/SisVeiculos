import { cn } from '@/lib/utils';
import type { Status } from '@/lib/tipos';

// Cores dos chips: docs/mockups/README.md. Só apresentação do status vindo da API.
const NEUTRO = 'bg-[#e8ecf2] text-[#3b4658]';
const ESTILO: Record<Status, { rotulo: string; classe: string }> = {
  solicitada: { rotulo: 'Solicitada', classe: 'bg-[#fff1d6] text-[#7a4a00]' },
  autorizada: { rotulo: 'Autorizada', classe: 'bg-[#dcebff] text-[#0b3c7a]' },
  em_uso: { rotulo: 'Em uso', classe: 'bg-[#d9f2e6] text-[#0a5a3a]' },
  concluida: { rotulo: 'Concluída', classe: NEUTRO },
  negada: { rotulo: 'Negada', classe: 'bg-[#fde2e0] text-[#8a1f17]' },
  cancelada: { rotulo: 'Cancelada', classe: NEUTRO },
};

export function StatusChip({ status }: { status: Status }) {
  const e = ESTILO[status] ?? { rotulo: status, classe: NEUTRO };
  return (
    <span
      className={cn('rounded-full px-3 py-1 text-sm font-semibold whitespace-nowrap', e.classe)}
    >
      {e.rotulo}
    </span>
  );
}
