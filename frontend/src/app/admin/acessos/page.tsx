'use client';

import * as React from 'react';
import { admin } from '@/lib/api';
import { useCarga } from '@/lib/use-carga';
import { dataHoraNumerica, diaParaIso } from '@/lib/formato';
import { AdminShell } from '@/components/admin-shell';
import { Alerta } from '@/components/ui/alert';
import { Spinner } from '@/components/ui/spinner';
import { Button } from '@/components/ui/button';
import { Input, Select } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

const LIMITE = 20;

// Texto amigável do que a API devolve (só apresentação; nenhuma regra).
function rotuloMetodo(m: string | null): string {
  if (m === 'password') return 'Senha';
  if (m === 'totp') return 'Segundo fator';
  return 'Outro';
}

function rotuloAal(a: string | null): string {
  if (a === 'aal1') return 'Verificação simples (aal1)';
  if (a === 'aal2') return 'Duas etapas (aal2)';
  return '—';
}

function Conteudo() {
  const [usuarioId, setUsuarioId] = React.useState('');
  const [de, setDe] = React.useState('');
  const [ate, setAte] = React.useState('');
  const [offset, setOffset] = React.useState(0);

  // Seletor de usuário: usa a própria lista de /api/admin/usuarios (nome e id).
  const usuarios = useCarga(() => admin.usuarios({ limite: 100, offset: 0 }));

  // `ate` é exclusivo: o dia final escolhido vira 00:00 do dia seguinte. A API valida o intervalo.
  const acessos = useCarga(
    () =>
      admin.acessos({
        usuario_id: usuarioId,
        de: diaParaIso(de),
        ate: diaParaIso(ate, true),
        limite: LIMITE,
        offset,
      }),
    `${usuarioId}|${de}|${ate}|${offset}`,
  );

  const itens = acessos.erro ? [] : (acessos.dados?.itens ?? []);
  const total = acessos.erro ? 0 : (acessos.dados?.total ?? 0);

  return (
    <>
      <p className="mb-4 rounded-padrao border border-borda bg-white p-3 text-texto-2">
        Registros de acesso são dados pessoais. Use apenas para segurança e auditoria.
      </p>

      <div className="mb-4 grid grid-cols-2 gap-3">
        <div className="col-span-2">
          <Label htmlFor="filtro-usuario">Usuário</Label>
          <Select
            id="filtro-usuario"
            value={usuarioId}
            onChange={(e) => {
              setUsuarioId(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">Todos</option>
            {(usuarios.dados?.itens ?? []).map((u) => (
              <option key={u.id} value={u.id}>
                {u.nome}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="filtro-de">Data inicial</Label>
          <Input
            id="filtro-de"
            type="date"
            value={de}
            onChange={(e) => {
              setDe(e.target.value);
              setOffset(0);
            }}
          />
        </div>
        <div>
          <Label htmlFor="filtro-ate">Data final</Label>
          <Input
            id="filtro-ate"
            type="date"
            value={ate}
            onChange={(e) => {
              setAte(e.target.value);
              setOffset(0);
            }}
          />
        </div>
      </div>

      {usuarios.erro ? <Alerta className="mb-4">{usuarios.erro}</Alerta> : null}
      {acessos.erro ? <Alerta className="mb-4">{acessos.erro}</Alerta> : null}

      {acessos.carregando ? (
        <p role="status" className="flex items-center gap-2 text-texto-2">
          <Spinner />
          Carregando...
        </p>
      ) : itens.length === 0 ? (
        acessos.erro ? null : (
          <p className="text-texto-2">Nenhum acesso encontrado.</p>
        )
      ) : (
        <ul
          aria-label="Acessos"
          className="overflow-hidden rounded-xl border border-borda bg-white"
        >
          {itens.map((a) => (
            <li key={a.id} className="border-b border-borda p-4 last:border-b-0">
              <p className="font-semibold">{a.nome ?? 'Usuário sem nome'}</p>
              <p className="text-texto-2">{dataHoraNumerica(a.ocorrido_em)}</p>
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                <dt className="text-texto-2">Método</dt>
                <dd>{rotuloMetodo(a.metodo)}</dd>
                <dt className="text-texto-2">Verificação</dt>
                <dd>{rotuloAal(a.aal)}</dd>
                <dt className="text-texto-2">Rede (IP mascarado)</dt>
                <dd>{a.ip_mascarado ?? '—'}</dd>
                <dt className="text-texto-2">Navegador</dt>
                <dd>{a.ua_familia ?? '—'}</dd>
                <dt className="text-sm text-texto-2">Sessão</dt>
                <dd className="text-sm text-texto-2">{a.sessao_ref ?? '—'}</dd>
              </dl>
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
          {total === 0 ? '0 acessos' : `${offset + 1}–${offset + itens.length} de ${total}`}
        </p>
        <Button
          variante="contorno"
          disabled={offset + LIMITE >= total}
          onClick={() => setOffset(offset + LIMITE)}
        >
          Próxima
        </Button>
      </div>
    </>
  );
}

export default function Acessos() {
  return (
    <AdminShell titulo="Acessos">
      <Conteudo />
    </AdminShell>
  );
}
