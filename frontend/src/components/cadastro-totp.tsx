'use client';

import * as React from 'react';
import { supabase } from '@/lib/supabase';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

interface Cadastro {
  factorId: string;
  qr: string;
  segredo: string;
}

/**
 * Cadastro (enroll) do autenticador TOTP pelo SDK do Supabase: gera o QR code e confirma com o
 * primeiro código. O cliente não decide se o MFA é exigido; só conduz o cadastro quando a tela
 * que o usa (verificação, perfil) o pede. O servidor valida o segundo fator a cada chamada.
 */
export function CadastroTotp({ aoConcluir }: { aoConcluir: () => void | Promise<void> }) {
  const [cadastro, setCadastro] = React.useState<Cadastro | null>(null);
  const [codigo, setCodigo] = React.useState('');
  const [erro, setErro] = React.useState<string | null>(null);
  const [ocupado, setOcupado] = React.useState(false);

  async function iniciar() {
    if (ocupado) return;
    setOcupado(true);
    setErro(null);
    try {
      const auth = supabase().auth;
      // Fatores não verificados de tentativas anteriores impedem novo cadastro: remove-os antes.
      const { data: lista } = await auth.mfa.listFactors();
      for (const f of lista?.all ?? []) {
        if (f.status === 'unverified') await auth.mfa.unenroll({ factorId: f.id });
      }
      const { data, error } = await auth.mfa.enroll({
        factorType: 'totp',
        friendlyName: `Autenticador ${new Date().toISOString()}`,
      });
      if (error || !data) {
        setErro('Não foi possível iniciar o cadastro do autenticador. Tente novamente.');
      } else {
        setCadastro({ factorId: data.id, qr: data.totp.qr_code, segredo: data.totp.secret });
      }
    } catch {
      setErro('Não foi possível iniciar o cadastro do autenticador. Tente novamente.');
    } finally {
      setOcupado(false);
    }
  }

  async function confirmar(e: React.FormEvent) {
    e.preventDefault();
    if (!cadastro || ocupado) return;
    setOcupado(true);
    setErro(null);
    try {
      const { error } = await supabase().auth.mfa.challengeAndVerify({
        factorId: cadastro.factorId,
        code: codigo.trim(),
      });
      if (error) {
        setErro('Código inválido ou expirado. Tente novamente.');
        return;
      }
      setCadastro(null);
      setCodigo('');
      await aoConcluir();
    } catch {
      setErro('Não foi possível confirmar o código. Tente novamente.');
    } finally {
      setOcupado(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {erro ? <Alerta>{erro}</Alerta> : null}
      {!cadastro ? (
        <Button tamanho="cheio" carregando={ocupado} onClick={() => void iniciar()}>
          {ocupado ? 'Preparando...' : 'Cadastrar autenticador'}
        </Button>
      ) : (
        <form onSubmit={(e) => void confirmar(e)} className="flex flex-col gap-4">
          <p className="text-texto-2">
            Leia o QR code no aplicativo autenticador (Google Authenticator, Microsoft Authenticator
            ou similar) e informe o código de 6 dígitos.
          </p>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={cadastro.qr}
            alt="QR code para cadastrar o autenticador"
            className="mx-auto size-48 rounded-padrao border border-borda bg-white p-2"
          />
          <p className="text-sm text-texto-2">
            Sem câmera? Digite esta chave no aplicativo:
            <code className="mt-1 block font-semibold break-all text-texto">
              {cadastro.segredo}
            </code>
          </p>
          <div>
            <Label htmlFor="codigo-totp">Código do autenticador</Label>
            <Input
              id="codigo-totp"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              required
              value={codigo}
              onChange={(e) => setCodigo(e.target.value)}
              aria-invalid={erro ? true : undefined}
            />
          </div>
          <Button type="submit" tamanho="cheio" carregando={ocupado}>
            {ocupado ? 'Confirmando...' : 'Confirmar cadastro'}
          </Button>
        </form>
      )}
    </div>
  );
}
