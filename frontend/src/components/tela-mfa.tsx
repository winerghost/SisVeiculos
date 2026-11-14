'use client';

import * as React from 'react';
import { useAuth } from '@/lib/auth';
import { supabase } from '@/lib/supabase';
import { CadastroTotp } from '@/components/cadastro-totp';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Spinner } from '@/components/ui/spinner';

/**
 * Verificação do segundo fator (TOTP do Supabase Auth). Só é exibida quando a API
 * responde 403 mfa_requerido; a decisão de exigir MFA é do servidor. Se a conta ainda não
 * tem autenticador cadastrado (segundo o SDK), oferece o cadastro em vez da verificação.
 */
export function TelaMfa() {
  const { aviso, verificarMfa, sair, recarregar } = useAuth();
  const [codigo, setCodigo] = React.useState('');
  const [erro, setErro] = React.useState<string | null>(null);
  const [enviando, setEnviando] = React.useState(false);
  const [semFator, setSemFator] = React.useState<boolean | null>(null);

  React.useEffect(() => {
    let ativo = true;
    void supabase()
      .auth.mfa.listFactors()
      .then(({ data, error }) => {
        // Se a consulta falhar, mantém a verificação normal (o servidor decide o resto).
        if (ativo) setSemFator(!error && (data?.totp.length ?? 0) === 0);
      });
    return () => {
      ativo = false;
    };
  }, []);

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    if (enviando) return;
    setEnviando(true);
    try {
      setErro(await verificarMfa(codigo.trim()));
    } catch {
      setErro('Não foi possível verificar o código. Tente novamente.');
    } finally {
      setEnviando(false);
    }
  }

  if (semFator === null) {
    return (
      <div role="status" className="flex min-h-dvh items-center justify-center gap-2 text-texto-2">
        <Spinner />
        Carregando...
      </div>
    );
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-5 bg-white p-6">
      <h1 className="text-2xl font-semibold">Verificação em duas etapas</h1>
      <p className="text-texto-2">
        {aviso ?? 'Informe o código de 6 dígitos do seu aplicativo autenticador.'}
      </p>
      {semFator ? (
        <>
          <p className="text-texto-2">Esta conta ainda não tem um autenticador cadastrado.</p>
          <CadastroTotp aoConcluir={recarregar} />
          <Button type="button" variante="contorno" tamanho="cheio" onClick={() => void sair()}>
            Sair
          </Button>
        </>
      ) : (
        <>
          {erro ? <Alerta>{erro}</Alerta> : null}
          <form onSubmit={(e) => void enviar(e)} className="flex flex-col gap-4">
            <div>
              <Label htmlFor="codigo-mfa">Código do autenticador</Label>
              <Input
                id="codigo-mfa"
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
            <Button type="submit" tamanho="cheio" carregando={enviando}>
              {enviando ? 'Verificando...' : 'Verificar'}
            </Button>
            <Button type="button" variante="contorno" tamanho="cheio" onClick={() => void sair()}>
              Sair
            </Button>
          </form>
        </>
      )}
    </main>
  );
}
