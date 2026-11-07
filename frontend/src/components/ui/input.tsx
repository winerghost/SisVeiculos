import * as React from 'react';
import { cn } from '@/lib/utils';

// Campo com 44px+ e borda #8493AB (contraste >= 3:1). Erro: borda em #B3261E.
export const campoBase =
  'block min-h-11 w-full rounded-padrao border border-borda-campo bg-white px-3 text-base text-texto placeholder:text-texto-2 aria-[invalid=true]:border-erro';

export const Input = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, ...props }, ref) => (
  <input ref={ref} className={cn(campoBase, 'h-12', className)} {...props} />
));
Input.displayName = 'Input';

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea ref={ref} className={cn(campoBase, 'min-h-24 py-2', className)} {...props} />
));
Textarea.displayName = 'Textarea';

export const Select = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(({ className, ...props }, ref) => (
  <select ref={ref} className={cn(campoBase, 'h-12', className)} {...props} />
));
Select.displayName = 'Select';
