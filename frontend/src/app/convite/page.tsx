'use client';

import * as React from 'react';
import { Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Eye, EyeOff } from 'lucide-react';
import { api, ApiErro } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { supabase } from '@/lib/supabase';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Spinner } from '@/components/ui/spinner';

type Fase =
  'verificando' | 'sem_sessao' | 'outra_conta' | 'saiu' | 'senha' | 'aceitando' | 'encerrado';

// Códigos em que repetir o aceite não adianta: a mensagem da API é exibida e só resta o login.
const DEFINITIVOS = new Set(['convite_expirado', 'convite_encerrado', 'convite_invalido']);

/**
 * Aceite de convite. O convidado chega do e-mail do Supabase com a sessão na URL (o SDK a lê
 * sozinho). Aqui: define a senha (SDK) e chama POST /api/convites/aceitar com o token da query
 * `?convite=`. Papel e unidade vêm do convite no servidor; o cliente não envia nenhum.
 */
function Aceite() {
  const router = useRouter();
  const { recarregar } = useAuth();
  const tokenDaUrl = useSearchParams().get('convite');
  // O token fica só em memória: a URL é limpa logo após a montagem (ver efeito abaixo).
  const tokenRef = React.useRef<string | null>(tokenDaUrl);
  const [fase, setFase] = React.useState<Fase>('verificando');
  const [exigeNome, setExigeNome] = React.useState(false);
  const [nome, setNome] = React.useState('');
  const [senha, setSenha] = React.useState('');
  const [ver, setVer] = React.useState(false);
  const [erro, setErro] = React.useState<string | null>(null);
  const [ocupado, setOcupado] = React.useState(false);
  const [senhaDefinida, setSenhaDefinida] = React.useState(false);

  React.useEffect(() => {
    let ativo = true;
    if (!tokenRef.current)
      tokenRef.current = new URLSearchParams(window.location.search).get('convite');
    // Dois formatos de link chegam aqui com o mesmo ?convite=<token>: convite novo (type=invite)
    // e RECUPERAÇÃO DE SENHA (type=recovery, conta já confirmada sem perfil; o SDK emite o evento
    // PASSWORD_RECOVERY). Ambos trazem a sessão do convidado no fragmento (#access_token=...) e
    // seguem o mesmo caminho: updateUser({password}) e depois o aceite. Sem sessão no link, uma
    // sessão existente é de uma conta anterior, não do convite.
    let recuperacao = false;
    const { data: inscricao } = supabase().auth.onAuthStateChange((evento) => {
      if (evento === 'PASSWORD_RECOVERY') recuperacao = true;
    });
    const sessaoNoLink = window.location.hash.includes('access_token=');
    void supabase()
      .auth.getSession() // aguarda o SDK consumir a sessão que veio na URL
      .then(({ data }) => {
        // Só agora a URL é limpa: antes disso o SDK ainda precisa ler o fragmento. Token e
        // sessão não ficam na barra de endereço nem no histórico.
        window.history.replaceState(null, '', '/convite');
        if (!ativo) return;
        if (!data.session || !tokenRef.current) setFase('sem_sessao');
        else setFase(sessaoNoLink || recuperacao ? 'senha' : 'outra_conta');
      });
    return () => {
      ativo = false;
      inscricao.subscription.unsubscribe();
    };
  }, []);

  async function aceitar() {
    const token = tokenRef.current;
    if (!token) return;
    setFase('aceitando');
    try {
      await api.aceitarConvite(token, exigeNome ? nome.trim() : undefined);
      await recarregar();
      router.replace('/requisicoes');
    } catch (e) {
      const definitivo = e instanceof ApiErro && DEFINITIVOS.has(e.codigo);
      // O backend responde 400 `nome_obrigatorio` quando o cadastro do Auth não traz o nome.
      if (e instanceof ApiErro && e.status === 400 && e.codigo === 'nome_obrigatorio') {
        setExigeNome(true);
      }
      setErro(e instanceof ApiErro ? e.message : 'Não foi possível concluir o cadastro.');
      setFase(definitivo ? 'encerrado' : 'senha');
    }
  }

  async function sairDaOutraConta() {
    await supabase().auth.signOut({ scope: 'local' });
    setFase('saiu');
  }

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    if (ocupado) return;
    setErro(null);
    setOcupado(true);
    try {
      if (!senhaDefinida) {
        const { error } = await supabase().auth.updateUser({ password: senha });
        if (error) {
          // Política de senha é do Supabase; a mensagem dele é exibida como veio.
          setErro(error.message);
          return;
        }
        setSenhaDefinida(true);
      }
      await aceitar();
    } catch {
      setErro('Não foi possível concluir o cadastro.');
    } finally {
      setOcupado(false);
    }
  }

  async function irParaLogin() {
    await supabase().auth.signOut({ scope: 'local' });
    router.replace('/login');
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-5 p-6">
      <div className="abertura-logo mx-auto">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/logo-agu.webp" alt="AGU - Advocacia-Geral da União" className="h-24 w-auto" />
      </div>
      <h1
        className="abertura-item text-center text-2xl font-semibold"
        style={{ '--atraso': '0.5s' } as React.CSSProperties}
      >
        Ativar minha conta
      </h1>

      {fase === 'verificando' ? (
        <p role="status" className="flex items-center gap-2 text-texto-2">
          <Spinner />
          Verificando o convite...
        </p>
      ) : null}

      {fase === 'sem_sessao' ? (
        <>
          <Alerta>
            O link do convite é inválido ou expirou. Peça ao administrador para reenviar o convite.
          </Alerta>
          <Button tamanho="cheio" onClick={() => router.replace('/login')}>
            Ir para o login
          </Button>
        </>
      ) : null}

      {fase === 'outra_conta' ? (
        <>
          <Alerta>
            Você está logado em outra conta. Saia dela antes de continuar com este convite.
          </Alerta>
          <Button tamanho="cheio" onClick={() => void sairDaOutraConta()}>
            Sair da conta atual
          </Button>
        </>
      ) : null}

      {fase === 'saiu' ? (
        <>
          <p role="status" className="text-texto-2">
            Sessão encerrada. Abra novamente o link do convite recebido por e-mail para continuar.
          </p>
          <Button tamanho="cheio" onClick={() => router.replace('/login')}>
            Ir para o login
          </Button>
        </>
      ) : null}

      {fase === 'encerrado' ? (
        <>
          {erro ? <Alerta>{erro}</Alerta> : null}
          <Button tamanho="cheio" onClick={() => void irParaLogin()}>
            Ir para o login
          </Button>
        </>
      ) : null}

      {fase === 'senha' || fase === 'aceitando' ? (
        <form onSubmit={(e) => void enviar(e)} className="flex flex-col gap-4">
          <p className="text-texto-2">Crie a senha da sua conta para concluir o cadastro.</p>
          {erro ? <Alerta>{erro}</Alerta> : null}
          <div>
            <Label htmlFor="nova-senha">Nova senha</Label>
            <div className="relative">
              <Input
                id="nova-senha"
                type={ver ? 'text' : 'password'}
                autoComplete="new-password"
                required
                value={senha}
                onChange={(e) => setSenha(e.target.value)}
                disabled={senhaDefinida}
                className="pr-12"
              />
              <button
                type="button"
                onClick={() => setVer((v) => !v)}
                aria-label={ver ? 'Ocultar senha' : 'Mostrar senha'}
                aria-pressed={ver}
                className="absolute inset-y-0 right-0 flex w-12 items-center justify-center text-texto-2"
              >
                {ver ? <EyeOff aria-hidden /> : <Eye aria-hidden />}
              </button>
            </div>
          </div>
          {exigeNome ? (
            <div>
              <Label htmlFor="nome">Nome completo</Label>
              <Input
                id="nome"
                autoComplete="name"
                required
                value={nome}
                onChange={(e) => setNome(e.target.value)}
              />
            </div>
          ) : null}
          <Button type="submit" tamanho="cheio" carregando={ocupado || fase === 'aceitando'}>
            {ocupado || fase === 'aceitando'
              ? 'Concluindo...'
              : senhaDefinida
                ? 'Tentar novamente'
                : 'Concluir cadastro'}
          </Button>
        </form>
      ) : null}
    </main>
  );
}

export default function Convite() {
  return (
    <Suspense fallback={null}>
      <Aceite />
    </Suspense>
  );
}
