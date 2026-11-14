'use client';

import * as React from 'react';
import Link from 'next/link';
import { Plus } from 'lucide-react';
import { admin } from '@/lib/api';
import { useCarga } from '@/lib/use-carga';
import { AdminShell } from '@/components/admin-shell';
import { Avatar } from '@/components/avatar';
import { Alerta } from '@/components/ui/alert';
import { Spinner } from '@/components/ui/spinner';
import { Button } from '@/components/ui/button';
import { Chip, ChipRole, ROLES, rotuloRole } from '@/components/ui/chip';
import { Input, Select } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { rotuloUltimoAcesso } from '@/lib/formato';

const LIMITE = 20;

function Conteudo() {
  const [role, setRole] = React.useState('');
  const [ativo, setAtivo] = React.useState('');
  const [offset, setOffset] = React.useState(0);
  const [busca, setBusca] = React.useState('');
  const [q, setQ] = React.useState('');
  // Debounce curto: o texto digitado só vira parâmetro `q` depois de uma pausa; volta à página 1.
  React.useEffect(() => {
    const t = setTimeout(() => {
      setQ(busca.trim());
      setOffset(0);
    }, 350);
    return () => clearTimeout(t);
  }, [busca]);
  // Busca, filtros e paginação são parâmetros da API; o servidor devolve `itens` e `total`.
  const lista = useCarga(
    () => admin.usuarios({ q, role, ativo, limite: LIMITE, offset }),
    `${q}|${role}|${ativo}|${offset}`,
  );

  const itens = lista.dados?.itens ?? [];
  const total = lista.dados?.total ?? 0;

  return (
    <>
      <div className="mb-3">
        <Label htmlFor="busca" className="sr-only">
          Buscar por nome ou e-mail
        </Label>
        <Input
          id="busca"
          type="search"
          placeholder="Buscar por nome ou e-mail"
          maxLength={80}
          value={busca}
          onChange={(e) => setBusca(e.target.value)}
        />
      </div>
      <div className="mb-4 grid grid-cols-2 gap-3">
        <div>
          <Label htmlFor="filtro-role">Perfil</Label>
          <Select
            id="filtro-role"
            value={role}
            onChange={(e) => {
              setRole(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">Todos</option>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {rotuloRole(r)}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="filtro-ativo">Situação</Label>
          <Select
            id="filtro-ativo"
            value={ativo}
            onChange={(e) => {
              setAtivo(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">Todas</option>
            <option value="true">Ativos</option>
            <option value="false">Inativos</option>
          </Select>
        </div>
      </div>

      {lista.erro ? <Alerta className="mb-4">{lista.erro}</Alerta> : null}
      {lista.dados?.busca_parcial === true ? (
        <p role="status" className="mb-3 text-texto-2">
          A busca pode estar incompleta. Refine o termo.
        </p>
      ) : null}

      {lista.carregando ? (
        <p role="status" className="flex items-center gap-2 text-texto-2">
          <Spinner />
          Carregando...
        </p>
      ) : itens.length === 0 ? (
        <p className="text-texto-2">Nenhum usuário encontrado.</p>
      ) : (
        <ul className="overflow-hidden rounded-xl border border-borda bg-white">
          {itens.map((u) => (
            <li key={u.id} className="border-b border-borda last:border-b-0">
              <Link
                href={`/admin/usuarios/detalhe?id=${encodeURIComponent(u.id)}`}
                className={`flex min-h-16 items-center gap-3 p-4 ${u.ativo ? '' : 'text-texto-2'}`}
              >
                <Avatar nome={u.nome} fotoUrl={u.foto_url} decorativo />
                {/* min-w-0 + truncate: o e-mail termina em reticências em telas estreitas. */}
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-semibold">{u.nome}</span>
                  <span className="block truncate text-texto-2">
                    {u.unidade}
                    {u.email ? ` · ${u.email}` : ''}
                  </span>
                  <span className="block truncate text-sm text-texto-2">
                    {rotuloUltimoAcesso(u.ultimo_acesso_em)}
                  </span>
                </span>
                {u.ativo ? <ChipRole role={u.role} /> : <Chip tom="perigo">Inativo</Chip>}
              </Link>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-4 flex items-center justify-between gap-3">
        <Button
          variante="contorno"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - LIMITE))}
        >
          Anterior
        </Button>
        <p role="status" className="text-center text-texto-2">
          {total === 0
            ? '0 usuários'
            : `${offset + 1}–${offset + (lista.dados?.itens.length ?? 0)} de ${total}`}
        </p>
        <Button
          variante="contorno"
          disabled={offset + LIMITE >= total}
          onClick={() => setOffset(offset + LIMITE)}
        >
          Próxima
        </Button>
      </div>

      {lista.dados?.acoes_permitidas.includes('criar') ? (
        <div className="mt-5">
          <Button asChild tamanho="cheio">
            <Link href="/admin/usuarios/novo">
              <Plus aria-hidden /> Convidar usuário
            </Link>
          </Button>
        </div>
      ) : null}
    </>
  );
}

export default function Usuarios() {
  return (
    <AdminShell titulo="Usuários">
      <Conteudo />
    </AdminShell>
  );
}
