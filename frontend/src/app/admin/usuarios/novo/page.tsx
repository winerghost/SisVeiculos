'use client';

import * as React from 'react';
import Link from 'next/link';
import { Info } from 'lucide-react';
import { admin } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useEnvio } from '@/lib/use-envio';
import { dataHora } from '@/lib/formato';
import type { Convite } from '@/lib/tipos';
import { AdminShell } from '@/components/admin-shell';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { ROLES, rotuloRole } from '@/components/ui/chip';
import { Input, Select } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

function Formulario() {
  const { eu } = useAuth();
  const envio = useEnvio();
  const [email, setEmail] = React.useState('');
  const [nome, setNome] = React.useState('');
  const [unidade, setUnidade] = React.useState(eu?.unidades[0] ?? '');
  const [role, setRole] = React.useState('solicitante');
  const [validade, setValidade] = React.useState('');
  const [telefone, setTelefone] = React.useState('');
  const [enviado, setEnviado] = React.useState<Convite | null>(null);
  const telefoneInvalido = Boolean(envio.erro) && envio.campoErro === 'telefone';

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    if (envio.ocupado) return;
    const r = await envio.executar(() =>
      admin.convidar({
        email: email.trim(),
        nome: nome.trim(),
        unidade,
        role,
        // Em branco: o servidor aplica o prazo padrão.
        ...(validade ? { validade_dias: Number(validade) } : {}),
        // Opcional: vai como digitado; o servidor normaliza e valida.
        ...(telefone.trim() ? { telefone: telefone.trim() } : {}),
      }),
    );
    if (r.ok) setEnviado(r.valor.convite);
  }

  if (enviado) {
    return (
      <div className="flex flex-col gap-4">
        <p role="status" className="rounded-padrao border border-borda bg-white p-4">
          Convite enviado para <strong className="break-all">{enviado.email}</strong>. Válido até{' '}
          {dataHora(enviado.expira_em)}.
        </p>
        <Button asChild tamanho="cheio">
          <Link href="/admin/convites">Ver convites</Link>
        </Button>
        <Button asChild variante="contorno" tamanho="cheio">
          <Link href="/admin/usuarios">Voltar aos usuários</Link>
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={(e) => void enviar(e)} className="flex flex-col gap-4">
      <p className="flex gap-3 rounded-xl bg-[#e8ecf2] p-3 text-texto-2">
        <Info aria-hidden className="mt-0.5 size-5 shrink-0" />O convite vai por e-mail,
        institucional ou Gmail. É de uso único e tem prazo de validade.
      </p>
      {envio.erro ? (
        <Alerta id="envio-erro" titulo="Não foi possível enviar">
          {envio.erro}
        </Alerta>
      ) : null}
      <div>
        <Label htmlFor="email">E-mail</Label>
        <Input
          id="email"
          type="email"
          required
          autoComplete="off"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </div>
      <div>
        <Label htmlFor="nome">Nome completo</Label>
        <Input
          id="nome"
          required
          autoComplete="off"
          value={nome}
          onChange={(e) => setNome(e.target.value)}
        />
      </div>
      <div>
        <Label htmlFor="unidade">Unidade</Label>
        <Select id="unidade" required value={unidade} onChange={(e) => setUnidade(e.target.value)}>
          {(eu?.unidades ?? []).map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </Select>
      </div>
      <div>
        <Label htmlFor="perfil">Perfil</Label>
        <Select id="perfil" required value={role} onChange={(e) => setRole(e.target.value)}>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {rotuloRole(r)}
            </option>
          ))}
        </Select>
      </div>
      <div>
        <Label htmlFor="telefone">
          Telefone (WhatsApp) <span className="font-normal text-texto-2">(opcional)</span>
        </Label>
        <Input
          id="telefone"
          type="tel"
          inputMode="numeric"
          // Telefone de terceiro: sem autopreenchimento (não sugere nem guarda o do admin).
          autoComplete="off"
          placeholder="5561999998888"
          // A API indica o campo recusado (erro.campo); o front só liga a mensagem ao campo.
          aria-invalid={telefoneInvalido || undefined}
          aria-describedby={telefoneInvalido ? 'telefone-dica envio-erro' : 'telefone-dica'}
          value={telefone}
          onChange={(e) => setTelefone(e.target.value)}
        />
        <p id="telefone-dica" className="mt-1 text-sm text-texto-2">
          Só números, com 55 e DDD.
        </p>
      </div>
      <div>
        <Label htmlFor="validade">
          Validade em dias <span className="font-normal text-texto-2">(opcional)</span>
        </Label>
        <Input
          id="validade"
          type="number"
          inputMode="numeric"
          placeholder="Em branco: prazo padrão"
          value={validade}
          onChange={(e) => setValidade(e.target.value)}
        />
      </div>
      <Button type="submit" tamanho="cheio" carregando={envio.ocupado} className="mt-4">
        {envio.ocupado ? 'Enviando...' : 'Enviar convite'}
      </Button>
    </form>
  );
}

export default function ConvidarUsuario() {
  return (
    <AdminShell titulo="Convidar usuário" voltarPara="/admin/usuarios">
      <Formulario />
    </AdminShell>
  );
}
