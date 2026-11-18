'use client';

import * as React from 'react';
import { supabase } from '@/lib/supabase';
import { AppShell } from '@/components/app-shell';
import { FotoPerfil } from '@/components/foto-perfil';
import { CadastroTotp } from '@/components/cadastro-totp';
import { GerenciarPasskeys } from '@/components/gerenciar-passkeys';
import { Alerta } from '@/components/ui/alert';
import { ChipRole } from '@/components/ui/chip';
import { useAuth } from '@/lib/auth';

/** Segurança da conta: cadastro do autenticador TOTP (SDK do Supabase). */
function Conteudo() {
  const [fatores, setFatores] = React.useState<number | null>(null);
  const [falha, setFalha] = React.useState(false);
  const { eu } = useAuth();

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
      <FotoPerfil />
      {eu ? (
        <section
          aria-label="Dados da conta"
          className="mb-6 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-borda bg-white p-4"
        >
          <p className="min-w-0 font-semibold break-words" data-testid="conta-nome">
            {eu.nome}
          </p>
          <ChipRole role={eu.role} />
          <p className="w-full text-sm text-texto-2" data-testid="conta-unidade">
            Unidade: {eu.unidade}
          </p>
        </section>
      ) : null}
      <h2 className="mb-2 text-lg font-semibold">Verificação em duas etapas</h2>
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
      <GerenciarPasskeys />
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
