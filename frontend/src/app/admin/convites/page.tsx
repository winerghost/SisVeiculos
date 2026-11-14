'use client';

import * as React from 'react';
import { admin } from '@/lib/api';
import { useCarga } from '@/lib/use-carga';
import { useEnvio } from '@/lib/use-envio';
import { dataHora } from '@/lib/formato';
import { AdminShell } from '@/components/admin-shell';
import { Alerta } from '@/components/ui/alert';
import { Spinner } from '@/components/ui/spinner';
import { Button } from '@/components/ui/button';
import { Chip, rotuloRole } from '@/components/ui/chip';
import { Select } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

const LIMITE = 20;
const ESTADOS: Record<string, { rotulo: string; tom: 'info' | 'aviso' | 'sucesso' | 'perigo' }> = {
  aberto: { rotulo: 'Aberto', tom: 'info' },
  expirado: { rotulo: 'Expirado', tom: 'aviso' },
  usado: { rotulo: 'Usado', tom: 'sucesso' },
  revogado: { rotulo: 'Revogado', tom: 'perigo' },
};

function Conteudo() {
  const [estado, setEstado] = React.useState('');
  const [offset, setOffset] = React.useState(0);
  const lista = useCarga(
    () => admin.convites({ estado, limite: LIMITE, offset }),
    `${estado}|${offset}`,
  );
  const envio = useEnvio();
  const [aviso, setAviso] = React.useState<string | null>(null);
  const [clicado, setClicado] = React.useState<string | null>(null);
  const total = lista.dados?.total ?? 0;

  async function executar(id: string, acao: 'reenviar' | 'revogar') {
    if (envio.ocupado) return;
    setAviso(null);
    setClicado(`${id}:${acao}`);
    try {
      const r = await envio.executar(() => admin.acaoConvite(id, acao));
      if (r.ok) setAviso(acao === 'reenviar' ? 'Convite reenviado.' : 'Convite revogado.');
      await lista.recarregar();
    } finally {
      setClicado(null);
    }
  }

  return (
    <>
      <div className="mb-4">
        <Label htmlFor="estado">Situação</Label>
        <Select
          id="estado"
          value={estado}
          onChange={(e) => {
            setEstado(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">Todos</option>
          {Object.entries(ESTADOS).map(([k, v]) => (
            <option key={k} value={k}>
              {v.rotulo}
            </option>
          ))}
        </Select>
      </div>

      {lista.erro ? <Alerta className="mb-4">{lista.erro}</Alerta> : null}
      {envio.erro ? <Alerta className="mb-4">{envio.erro}</Alerta> : null}
      {aviso ? (
        <p role="status" className="mb-4 rounded-padrao border border-borda bg-white p-3">
          {aviso}
        </p>
      ) : null}

      {lista.carregando ? (
        <p role="status" className="flex items-center gap-2 text-texto-2">
          <Spinner />
          Carregando...
        </p>
      ) : lista.dados?.itens.length === 0 ? (
        <p className="text-texto-2">Nenhum convite encontrado.</p>
      ) : (
        <ul className="lista-animada flex flex-col gap-3">
          {lista.dados?.itens.map((c) => {
            const e = ESTADOS[c.estado];
            return (
              <li key={c.id} className="rounded-xl border border-borda bg-white p-4">
                <div className="flex items-start justify-between gap-3">
                  <p className="min-w-0 flex-1 truncate font-semibold" title={c.email}>
                    {c.email}
                  </p>
                  <Chip tom={e?.tom ?? 'neutro'}>{e?.rotulo ?? c.estado}</Chip>
                </div>
                <p className="text-texto-2">
                  {c.unidade} · {rotuloRole(c.role)}
                </p>
                <p className="text-texto-2">Válido até {dataHora(c.expira_em)}</p>
                {c.acoes_permitidas.length > 0 ? (
                  <div className="mt-3 grid grid-cols-2 gap-3">
                    {c.acoes_permitidas.includes('reenviar') ? (
                      <Button
                        variante="contorno"
                        carregando={clicado === `${c.id}:reenviar`}
                        disabled={envio.ocupado}
                        onClick={() => void executar(c.id, 'reenviar')}
                        aria-label={`Reenviar convite para ${c.email}`}
                      >
                        {clicado === `${c.id}:reenviar` ? 'Reenviando...' : 'Reenviar'}
                      </Button>
                    ) : null}
                    {c.acoes_permitidas.includes('revogar') ? (
                      <Button
                        variante="perigo"
                        carregando={clicado === `${c.id}:revogar`}
                        disabled={envio.ocupado}
                        onClick={() => void executar(c.id, 'revogar')}
                        aria-label={`Revogar convite de ${c.email}`}
                      >
                        {clicado === `${c.id}:revogar` ? 'Revogando...' : 'Revogar'}
                      </Button>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
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
          {total} no total
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

export default function Convites() {
  return (
    <AdminShell titulo="Convites">
      <Conteudo />
    </AdminShell>
  );
}
