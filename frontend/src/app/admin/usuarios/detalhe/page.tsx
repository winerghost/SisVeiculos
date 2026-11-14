'use client';

import * as React from 'react';
import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { admin } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useCarga } from '@/lib/use-carga';
import { useEnvio } from '@/lib/use-envio';
import type { UsuarioAdmin } from '@/lib/tipos';
import { AdminShell } from '@/components/admin-shell';
import { Avatar } from '@/components/avatar';
import { TrocarFotoUsuario } from '@/components/trocar-foto-usuario';
import { Alerta } from '@/components/ui/alert';
import { Spinner } from '@/components/ui/spinner';
import { Button } from '@/components/ui/button';
import { Chip, ChipRole, ROLES, rotuloRole } from '@/components/ui/chip';
import { Input, Select } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { rotuloUltimoAcesso } from '@/lib/formato';

function Edicao({
  u,
  aoAtualizar,
  recarregar,
}: {
  u: UsuarioAdmin;
  aoAtualizar: (u: UsuarioAdmin) => void;
  recarregar: () => Promise<void>;
}) {
  const { eu } = useAuth();
  const envio = useEnvio();
  const [nome, setNome] = React.useState(u.nome);
  const [unidade, setUnidade] = React.useState(u.unidade);
  const [role, setRole] = React.useState(u.role);
  const [confirmando, setConfirmando] = React.useState(false);
  const [confirmandoFoto, setConfirmandoFoto] = React.useState(false);
  const [fotoRemovida, setFotoRemovida] = React.useState(false);
  const [salvo, setSalvo] = React.useState(false);
  const [clicado, setClicado] = React.useState<'salvar' | 'ativar' | 'desativar' | 'foto' | null>(
    null,
  );

  // Alterações que o servidor autoriza vêm em acoes_permitidas; as regras (último admin,
  // auto-alteração) são dele e o erro é exibido como veio.
  const podeEditar = u.acoes_permitidas.includes('editar');
  const podeRole = u.acoes_permitidas.includes('alterar_role');
  const podeDesativar = u.acoes_permitidas.includes('desativar');
  const podeAtivar = u.acoes_permitidas.includes('ativar');
  const podeRemoverFoto = u.acoes_permitidas.includes('remover_foto');
  const podeTrocarFoto = u.acoes_permitidas.includes('trocar_foto');

  async function salvar(e: React.FormEvent) {
    e.preventDefault();
    if (envio.ocupado) return;
    setSalvo(false);
    setClicado('salvar');
    const r = await envio.executar(() =>
      admin.alterarUsuario(u.id, { nome, unidade, ...(podeRole ? { role } : {}) }),
    );
    setClicado(null);
    if (r.ok) {
      aoAtualizar(r.valor);
      setNome(r.valor.nome);
      setUnidade(r.valor.unidade);
      setRole(r.valor.role);
      setSalvo(true);
    } else {
      await recarregar(); // acoes_permitidas podem ter mudado no servidor
    }
  }

  async function mudarSituacao(acao: 'ativar' | 'desativar') {
    if (envio.ocupado) return;
    setSalvo(false);
    setClicado(acao);
    const r = await envio.executar(() => admin.acaoUsuario(u.id, acao));
    setClicado(null);
    setConfirmando(false);
    if (r.ok) {
      aoAtualizar(r.valor);
      setRole(r.valor.role);
    } else {
      await recarregar();
    }
  }

  async function removerFoto() {
    if (envio.ocupado) return;
    setSalvo(false);
    setFotoRemovida(false);
    setClicado('foto');
    const r = await envio.executar(() => admin.removerFotoUsuario(u.id));
    setClicado(null);
    setConfirmandoFoto(false);
    if (r.ok) {
      aoAtualizar(r.valor);
      setFotoRemovida(true);
    } else {
      await recarregar();
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <section className="flex items-center gap-4 rounded-xl border border-borda bg-white p-4">
        <Avatar nome={u.nome} fotoUrl={u.foto_url} decorativo className="size-14 text-lg" />
        <div className="min-w-0">
          <h2 className="truncate text-xl font-semibold">{u.nome}</h2>
          {u.email ? <p className="truncate text-texto-2">{u.email}</p> : null}
          <p className="text-texto-2">{rotuloUltimoAcesso(u.ultimo_acesso_em)}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <ChipRole role={u.role} />
            {u.ativo ? <Chip tom="sucesso">Conta ativa</Chip> : <Chip tom="perigo">Inativa</Chip>}
          </div>
        </div>
      </section>

      {envio.erro ? <Alerta>{envio.erro}</Alerta> : null}
      {salvo ? (
        <p role="status" className="rounded-padrao border border-borda bg-white p-3">
          Alterações salvas.
        </p>
      ) : null}

      <form
        onSubmit={(e) => void salvar(e)}
        className="flex flex-col gap-4 rounded-xl border border-borda bg-white p-4"
      >
        <h2 className="text-lg font-semibold">Dados e perfil</h2>
        <div>
          <Label htmlFor="nome">Nome</Label>
          <Input
            id="nome"
            required
            disabled={!podeEditar}
            value={nome}
            onChange={(e) => setNome(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="perfil">Perfil</Label>
          <Select
            id="perfil"
            disabled={!podeRole}
            value={role}
            onChange={(e) => setRole(e.target.value)}
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {rotuloRole(r)}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="unidade">Unidade</Label>
          <Select
            id="unidade"
            disabled={!podeEditar}
            value={unidade}
            onChange={(e) => setUnidade(e.target.value)}
          >
            {(eu?.unidades ?? []).map((x) => (
              <option key={x} value={x}>
                {x}
              </option>
            ))}
          </Select>
        </div>
        {podeEditar ? (
          <Button
            type="submit"
            tamanho="cheio"
            carregando={clicado === 'salvar'}
            disabled={envio.ocupado}
          >
            {clicado === 'salvar' ? 'Salvando...' : 'Salvar alterações'}
          </Button>
        ) : null}
      </form>

      {fotoRemovida ? (
        <p role="status" className="rounded-padrao border border-borda bg-white p-3">
          Foto removida.
        </p>
      ) : null}

      {podeRemoverFoto || podeTrocarFoto ? (
        <section className="flex flex-col gap-3 rounded-xl border border-borda bg-white p-4">
          <h2 className="text-lg font-semibold">Foto do perfil</h2>
          <p className="text-texto-2">
            A foto é opcional e serve apenas para identificar a pessoa no sistema.
          </p>
          {podeTrocarFoto ? (
            <TrocarFotoUsuario
              usuario={u}
              aoAtualizar={(novo) => {
                setFotoRemovida(false);
                aoAtualizar(novo);
              }}
            />
          ) : null}
          {!podeRemoverFoto ? null : confirmandoFoto ? (
            <div className="grid grid-cols-2 gap-3">
              <Button variante="contorno" onClick={() => setConfirmandoFoto(false)}>
                Cancelar
              </Button>
              <Button
                variante="perigo"
                carregando={clicado === 'foto'}
                disabled={envio.ocupado}
                onClick={() => void removerFoto()}
              >
                {clicado === 'foto' ? 'Removendo...' : 'Confirmar'}
              </Button>
            </div>
          ) : (
            <Button variante="perigo" tamanho="cheio" onClick={() => setConfirmandoFoto(true)}>
              Remover foto
            </Button>
          )}
        </section>
      ) : null}

      {podeDesativar || podeAtivar ? (
        <section className="rounded-xl border border-borda bg-white p-4">
          <h2 className="text-lg font-semibold">Conta</h2>
          <p className="mt-1 mb-3 text-texto-2">
            Uma conta inativa não faz login, mas o histórico de requisições é mantido.
          </p>
          {podeAtivar ? (
            <Button
              tamanho="cheio"
              carregando={clicado === 'ativar'}
              disabled={envio.ocupado}
              className={podeDesativar ? 'mb-3' : undefined}
              onClick={() => void mudarSituacao('ativar')}
            >
              {clicado === 'ativar' ? 'Ativando...' : 'Ativar conta'}
            </Button>
          ) : null}
          {!podeDesativar ? null : confirmando ? (
            <div className="grid grid-cols-2 gap-3">
              <Button variante="contorno" onClick={() => setConfirmando(false)}>
                Cancelar
              </Button>
              <Button
                variante="perigo"
                carregando={clicado === 'desativar'}
                disabled={envio.ocupado}
                onClick={() => void mudarSituacao('desativar')}
              >
                {clicado === 'desativar' ? 'Desativando...' : 'Confirmar'}
              </Button>
            </div>
          ) : (
            // Conta já inativa que ainda oferece `desativar`: a API refaz o bloqueio no Auth.
            <Button
              variante={u.ativo ? 'perigo' : 'contorno'}
              tamanho="cheio"
              onClick={() => setConfirmando(true)}
            >
              {u.ativo ? 'Desativar conta' : 'Reaplicar bloqueio'}
            </Button>
          )}
        </section>
      ) : null}
    </div>
  );
}

function PorId({ id }: { id: string }) {
  const usuario = useCarga(() => admin.usuario(id));
  if (usuario.carregando) {
    return (
      <p role="status" className="flex items-center gap-2 text-texto-2">
        <Spinner />
        Carregando...
      </p>
    );
  }
  if (!usuario.dados) return <Alerta>{usuario.erro ?? 'Usuário não encontrado.'}</Alerta>;
  return (
    <Edicao u={usuario.dados} aoAtualizar={usuario.setDados} recarregar={usuario.recarregar} />
  );
}

function Leitor() {
  const id = useSearchParams().get('id');
  return id ? <PorId id={id} /> : <Alerta>Usuário não informado.</Alerta>;
}

export default function DetalheUsuario() {
  return (
    <AdminShell titulo="Usuário" voltarPara="/admin/usuarios">
      <Suspense fallback={null}>
        <Leitor />
      </Suspense>
    </AdminShell>
  );
}
