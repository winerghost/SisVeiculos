'use client';

import * as React from 'react';
import { supabase } from '@/lib/supabase';
import { AppShell } from '@/components/app-shell';
import { CadastroTotp } from '@/components/cadastro-totp';
import { Alerta } from '@/components/ui/alert';

/** Segurança da conta: cadastro do autenticador TOTP (SDK do Supabase). */
function Conteudo() {
  const [fatores, setFatores] = React.useState<number | null>(null);
  const [falha, setFalha] = React.useState(false);

  React.useEffect(() => {
    let ativo = true;
    void supabase()
      .auth.mfa.listFactors()
      .then(({ data, error }) => {
        if (!ativo) return;
        setFalha(Boolean(error));
        setFatores(error ? null : (data?.totp.length ?? 0));
      });
    return () => {
      ativo = false;
    };
  }, []);

  return (
    <>
      <p className="mb-4 text-texto-2">
        A verificação em duas etapas usa um aplicativo autenticador. Quando o administrador a
        habilita, ela passa a ser exigida no login de administradores e autorizadores.
      </p>
      {falha ? <Alerta>Não foi possível consultar o autenticador. Tente novamente.</Alerta> : null}
      {fatores === null ? (
        falha ? null : (
          <p role="status" className="text-texto-2">
            Carregando...
          </p>
        )
      ) : fatores > 0 ? (
        <p role="status" className="rounded-padrao border border-borda bg-white p-4">
          Autenticador cadastrado nesta conta.
        </p>
      ) : (
        <section
          aria-label="Cadastro do autenticador"
          className="rounded-xl border border-borda bg-white p-4"
        >
          <CadastroTotp aoConcluir={() => setFatores(1)} />
        </section>
      )}
    </>
  );
}

export default function Seguranca() {
  return (
    <AppShell titulo="Segurança" voltarPara="/requisicoes">
      <Conteudo />
    </AppShell>
  );
}
