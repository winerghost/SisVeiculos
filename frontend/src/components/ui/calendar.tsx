'use client';

import { DayPicker, type DayPickerProps } from 'react-day-picker';
import { ptBR } from 'react-day-picker/locale';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';

/** Calendário em pt-BR (shadcn Calendar sobre react-day-picker). Não calcula disponibilidade. */
export function Calendar({ className, ...props }: DayPickerProps) {
  return (
    <DayPicker
      locale={ptBR}
      weekStartsOn={0}
      showOutsideDays={false}
      className={cn('relative w-full', className)}
      classNames={{
        root: 'relative w-full',
        months: 'w-full',
        month: 'w-full',
        month_caption: 'flex h-11 items-center justify-center',
        caption_label: 'text-base font-semibold',
        nav: 'absolute inset-x-0 top-0 flex h-11 items-center justify-between',
        button_previous:
          'inline-flex size-11 items-center justify-center rounded-padrao border border-borda',
        button_next:
          'inline-flex size-11 items-center justify-center rounded-padrao border border-borda',
        month_grid: 'mt-2 w-full border-collapse',
        weekday: 'h-9 text-sm font-medium text-texto-2',
        day: 'p-0 text-center',
        day_button:
          'mx-auto inline-flex size-11 items-center justify-center rounded-padrao text-base hover:bg-fundo',
        selected: '[&>button]:bg-primaria [&>button]:text-white [&>button]:hover:bg-primaria',
        today: '[&>button]:border [&>button]:border-borda-campo',
      }}
      components={{
        Chevron: ({ orientation }) =>
          orientation === 'left' ? (
            <ChevronLeft aria-hidden className="size-5" />
          ) : (
            <ChevronRight aria-hidden className="size-5" />
          ),
      }}
      {...props}
    />
  );
}
