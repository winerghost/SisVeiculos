'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { supabase } from '@/lib/supabase';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

// Destino do link de redefinição do Supabase: o SDK abre a sessão de recuperação e
// aqui só se envia a nova senha ao próprio Supabase. A política de senha é dele.
export default function RedefinirSenha() {
  const router = useRouter();
  const [senha, setSenha] = React.useState('');
  const [erro, setErro] = React.useState<string | null>(null);
  const [feito, setFeito] = React.useState(false);

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    setErro(null);
    const { error } = await supabase().auth.updateUser({ password: senha });
    if (error) {
      setErro(error.message);
      return;
    }
    await supabase().auth.signOut({ scope: 'local' });
    setFeito(true);
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-5 bg-white p-6">
      <h1 className="text-2xl font-semibold">Redefinir senha</h1>
      {erro ? <Alerta>{erro}</Alerta> : null}
      {feito ? (
        <>
          <p role="status">Senha alterada. Entre com a nova senha.</p>
          <Button tamanho="cheio" onClick={() => router.replace('/login')}>
            Ir para o login
          </Button>
        </>
      ) : (
        <form onSubmit={(e) => void enviar(e)} className="flex flex-col gap-4">
          <div>
            <Label htmlFor="nova-senha">Nova senha</Label>
            <Input
              id="nova-senha"
              type="password"
              autoComplete="new-password"
              required
              value={senha}
              onChange={(e) => setSenha(e.target.value)}
            />
          </div>
          <Button type="submit" tamanho="cheio">
            Salvar nova senha
          </Button>
        </form>
      )}
    </main>
  );
}
