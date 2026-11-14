import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';
import { Spinner } from '@/components/ui/spinner';

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
  /** Mostra o spinner e bloqueia o clique enquanto a API responde. Ignorada com asChild. */
  carregando?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variante, tamanho, asChild, carregando, children, ...props }, ref) => {
    const classes = cn(botao({ variante, tamanho }), className);
    if (asChild) {
      return (
        <Slot ref={ref} className={classes} {...props}>
          {children}
        </Slot>
      );
    }
    return (
      <button
        ref={ref}
        className={classes}
        {...props}
        disabled={props.disabled || carregando}
        aria-busy={carregando ? true : undefined}
      >
        {carregando ? <Spinner /> : null}
        {children}
      </button>
    );
  },
);
Button.displayName = 'Button';
