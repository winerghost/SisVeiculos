import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const botao = cva(
  'inline-flex min-h-11 items-center justify-center gap-2 rounded-padrao px-4 text-base font-semibold transition-colors disabled:pointer-events-none disabled:opacity-60',
  {
    variants: {
      variante: {
        primario: 'bg-primaria text-primaria-fg hover:bg-primaria/90',
        sucesso: 'bg-sucesso text-white hover:bg-sucesso/90',
        perigo: 'border border-[#8a1f17] bg-white text-[#8a1f17] hover:bg-[#fdf0ef]',
        contorno: 'border border-borda-campo bg-white text-texto hover:bg-fundo',
        link: 'px-2 text-primaria underline-offset-4 hover:underline',
      },
      tamanho: { padrao: 'h-12', cheio: 'h-12 w-full' },
    },
    defaultVariants: { variante: 'primario', tamanho: 'padrao' },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof botao> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variante, tamanho, asChild, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return <Comp ref={ref} className={cn(botao({ variante, tamanho }), className)} {...props} />;
  },
);
Button.displayName = 'Button';
