import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

/** Indicador de carregamento decorativo. O texto acessível fica a cargo de quem o usa (role=status/aria-busy). */
export function Spinner({ className }: { className?: string }) {
  return <Loader2 aria-hidden className={cn('size-4 motion-safe:animate-spin', className)} />;
}
