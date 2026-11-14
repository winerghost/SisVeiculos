'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { Eye, EyeOff } from 'lucide-react';
import { useAuth } from '@/lib/auth';
import { supabase } from '@/lib/supabase';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { TelaMfa } from '@/components/tela-mfa';
import { Splash } from '@/components/splash';

export default function Login() {
  const { estado, aviso, entrar } = useAuth();
  const router = useRouter();
  const [email, setEmail] = React.useState('');
  const [senha, setSenha] = React.useState('');
  const [ver, setVer] = React.useState(false);
  const [erro, setErro] = React.useState<string | null>(null);
  const [info, setInfo] = React.useState<string | null>(null);
  const [enviando, setEnviando] = React.useState(false);

  // Começa true (HTML estático já nasce com a abertura); o efeito pula se já foi vista.
  const [splash, setSplash] = React.useState(true);
  const [veioDaSplash, setVeioDaSplash] = React.useState(true);
  const fimSplash = React.useCallback((exibiu: boolean) => {
    setSplash(false);
    setVeioDaSplash(exibiu);
  }, []);

  React.useEffect(() => {
    if (estado === 'ativo') router.replace('/requisicoes');
  }, [estado, router]);

  if (estado === 'mfa') return <TelaMfa />;

  // Durante a splash o conteúdo fica oculto e só anima ao ser revelado.
  const anima = splash ? 'opacity-0' : 'abertura-item';
  const atraso = (s: number) => ({ '--atraso': `${s}s` }) as React.CSSProperties;

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    if (enviando) return;
    setEnviando(true);
    setErro(null);
    setInfo(null);
    try {
      setErro(await entrar(email.trim(), senha));
    } catch {
      setErro('Não foi possível entrar. Tente novamente.');
    } finally {
      setEnviando(false);
    }
  }

  async function esqueci() {
    setErro(null);
    setInfo(null);
    if (!email.trim()) {
      setErro('Informe o seu e-mail para receber o link de redefinição.');
      return;
    }
    // Fluxo de redefinição do próprio Supabase. A resposta é sempre a mesma (não revela contas).
    await supabase().auth.resetPasswordForEmail(email.trim(), {
      redirectTo: `${window.location.origin}/redefinir-senha`,
    });
    setInfo('Se o e-mail estiver cadastrado, você receberá um link para redefinir a senha.');
  }

  const mensagem = erro ?? aviso;

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col px-6 py-10">
      {splash ? <Splash aoTerminar={fimSplash} /> : null}
      <div className="flex flex-1 flex-col justify-center gap-6">
        {/* Após a splash a logo já foi animada: aqui ela só aparece. Sem splash, anima aqui. */}
        <div className={veioDaSplash ? 'mx-auto' : 'abertura-logo mx-auto'}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/logo-agu.webp" alt="AGU - Advocacia-Geral da União" className="h-24 w-auto" />
        </div>
        <div className={`${anima} text-center`} style={atraso(veioDaSplash ? 0.1 : 0.7)}>
          <h1 className="text-3xl font-semibold">Controle de veículos</h1>
          <p className="mt-2 text-texto-2">
            Acesse com o e-mail do seu cadastro para solicitar ou autorizar saídas.
          </p>
        </div>

        {mensagem ? <Alerta>{mensagem}</Alerta> : null}
        {info ? (
          <p role="status" className="rounded-padrao border border-borda bg-white p-3">
            {info}
          </p>
        ) : null}

        <form
          onSubmit={(e) => void enviar(e)}
          className={`${anima} flex flex-col gap-4`}
          style={atraso(veioDaSplash ? 0.3 : 0.9)}
        >
          <div>
            <Label htmlFor="email">E-mail</Label>
            <Input
              id="email"
              type="email"
              autoComplete="username"
              placeholder="seu.email@exemplo.com"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="senha">Senha</Label>
            <div className="relative">
              <Input
                id="senha"
                type={ver ? 'text' : 'password'}
                autoComplete="current-password"
                required
                value={senha}
                onChange={(e) => setSenha(e.target.value)}
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
          <div className="text-right">
            <Button type="button" variante="link" onClick={() => void esqueci()}>
              Esqueci minha senha
            </Button>
          </div>
          <Button type="submit" tamanho="cheio" carregando={enviando}>
            {enviando ? 'Entrando...' : 'Entrar'}
          </Button>
        </form>
      </div>
      <p className="mt-8 text-center text-sm text-texto-2">
        Primeiro acesso? Solicite o cadastro ao administrador.
      </p>
    </main>
  );
}
