'use client';

import Link from 'next/link';
import { Car, ChevronRight, Mail, Settings, ShieldCheck, Users } from 'lucide-react';
import { AdminShell } from '@/components/admin-shell';

const ITENS = [
  { href: '/admin/veiculos', rotulo: 'Veículos', ajuda: 'Cadastrar e ativar veículos', Icone: Car },
  { href: '/admin/usuarios', rotulo: 'Usuários', ajuda: 'Contas, perfis e convites', Icone: Users },
  { href: '/admin/convites', rotulo: 'Convites', ajuda: 'Reenviar ou revogar', Icone: Mail },
  {
    href: '/admin/configuracoes',
    rotulo: 'Configurações',
    ajuda: 'Recursos opcionais',
    Icone: Settings,
  },
  {
    href: '/seguranca',
    rotulo: 'Segurança da conta',
    ajuda: 'Autenticador (verificação em duas etapas)',
    Icone: ShieldCheck,
  },
];

// Ponto de entrada da administração. A guarda por `administrar` está no AdminShell.
export default function Admin() {
  return (
    <AdminShell titulo="Administração">
      <ul className="lista-animada flex flex-col gap-3">
        {ITENS.map(({ href, rotulo, ajuda, Icone }) => (
          <li key={href}>
            <Link
              href={href}
              className="flex min-h-16 items-center gap-4 rounded-xl border border-borda bg-white p-4"
            >
              <Icone aria-hidden className="size-6 text-primaria" />
              <span className="flex-1">
                <span className="block text-lg font-semibold">{rotulo}</span>
                <span className="block text-texto-2">{ajuda}</span>
              </span>
              <ChevronRight aria-hidden className="text-texto-2" />
            </Link>
          </li>
        ))}
      </ul>
    </AdminShell>
  );
}
