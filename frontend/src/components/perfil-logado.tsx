'use client';

import { ChevronDown } from 'lucide-react';
import { useAuth } from '@/lib/auth';
import { ChipRole, rotuloRole } from '@/components/ui/chip';
import type { Eu } from '@/lib/tipos';

/** Texto do avatar (aria-label e title): inclui o perfil informado pela API (`eu.role`). */
export function descricaoConta(eu: Pick<Eu, 'nome' | 'role'>): string {
  return `Conta: ${eu.nome}, perfil ${rotuloRole(eu.role)} (segurança)`;
}

/**
 * Nome e selo do perfil do usuário logado, ao lado do avatar. Só aparece de `md` para cima; no
 * celular o componente não ocupa espaço (o perfil vai só no aria-label do avatar). O perfil vem
 * de `eu.role` (API); o cliente não deduz nada.
 */
export function PerfilLogado() {
  const { eu } = useAuth();
  if (!eu) return null;
  return (
    <span className="hidden min-w-0 items-center gap-2 md:flex">
      <span className="max-w-48 truncate text-sm font-medium text-texto">{eu.nome}</span>
      <span data-testid="perfil-selo" className="text-xs [&>span]:px-2 [&>span]:py-0.5">
        <ChipRole role={eu.role} />
      </span>
      <ChevronDown aria-hidden className="size-4 shrink-0 text-texto-2" />
    </span>
  );
}
