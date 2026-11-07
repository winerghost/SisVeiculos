'use client';

import * as React from 'react';
import { Drawer as Vaul } from 'vaul';
import { cn } from '@/lib/utils';

export const Drawer = Vaul.Root;
export const DrawerTrigger = Vaul.Trigger;
export const DrawerClose = Vaul.Close;
export const DrawerTitle = Vaul.Title;
export const DrawerDescription = Vaul.Description;

export function DrawerContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof Vaul.Content>) {
  return (
    <Vaul.Portal>
      <Vaul.Overlay className="fixed inset-0 z-40 bg-black/50" />
      <Vaul.Content
        className={cn(
          'fixed inset-x-0 bottom-0 z-50 mx-auto flex max-h-[95dvh] max-w-md flex-col rounded-t-2xl bg-white p-4 outline-none',
          className,
        )}
        {...props}
      >
        <div aria-hidden className="mx-auto mb-3 h-1.5 w-11 rounded-full bg-borda-campo/40" />
        <div className="overflow-y-auto">{children}</div>
      </Vaul.Content>
    </Vaul.Portal>
  );
}
