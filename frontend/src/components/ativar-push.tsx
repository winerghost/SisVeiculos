'use client';

import * as React from 'react';
import { BellRing } from 'lucide-react';
import { api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { detectarSuporte, inscreverAparelho, inscricaoAtual } from '@/lib/push';
import type { PushConfig } from '@/lib/tipos';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Dialogo } from '@/components/ui/dialogo';

type Estado = 'carregando' | 'oculto' | 'sem_suporte' | 'ios' | 'negada' | 'inativo' | 'ativo';

function Cartao({ children }: { children: React.ReactNode }) {
  return (
    <section
      aria-labelledby="titulo-push"
      className="mb-4 flex flex-col gap-3 rounded-xl border border-borda bg-white p-4"
    >
      <h2 id="titulo-push" className="flex items-center gap-2 text-base font-semibold">
        <BellRing aria-hidden className="size-5 text-primaria" />
        Avisos no aparelho
      </h2>
      {children}
    </section>
  );
}

/**
 * Ativação de notificações push neste aparelho. Só aparece se a API informar que o recurso está
 * habilitado. A permissão é pedida apenas após o clique, depois de explicar o que será enviado.
 */
export function AtivarPush() {
  const { tratarErro } = useAuth();
  // Ref evita reexecutar o efeito (e reenviar o POST) se a identidade da função mudar.
  const tratarErroRef = React.useRef(tratarErro);
  React.useEffect(() => {
    tratarErroRef.current = tratarErro;
  }, [tratarErro]);
  const [estado, setEstado] = React.useState<Estado>('carregando');
  const [config, setConfig] = React.useState<PushConfig | null>(null);
  const [explicando, setExplicando] = React.useState(false);
  const [ocupado, setOcupado] = React.useState(false);
  const [erro, setErro] = React.useState<string | null>(null);
  // Trava de reentrada: dois cliques na mesma tarefa geram uma só ativação.
  const emAndamento = React.useRef(false);

  React.useEffect(() => {
    let ativo = true;
    void (async () => {
      try {
        const c = await api.pushConfig();
        if (!ativo) return;
        if (!c.habilitado || !c.chave_publica) {
          setEstado('oculto');
          return;
        }
        setConfig(c);
        const suporte = detectarSuporte();
        if (suporte === 'ios_fora_da_tela_inicial') {
          setEstado('ios');
          return;
        }
        if (suporte === 'sem_suporte') {
          setEstado('sem_suporte');
          return;
        }
        if (Notification.permission === 'denied') {
          setEstado('negada');
          return;
        }
        const insc = Notification.permission === 'granted' ? await inscricaoAtual() : null;
        if (!insc) {
          if (ativo) setEstado('inativo');
          return;
        }
        // Reenvio idempotente: o servidor reatribui o endpoint ao usuário atual (aparelho
        // compartilhado). Só afirma "ativo" se a API confirmar.
        const json = insc.toJSON();
        const p256dh = json.keys?.p256dh;
        const auth = json.keys?.auth;
        if (!json.endpoint || !p256dh || !auth) {
          if (ativo) setEstado('inativo');
          return;
        }
        try {
          await api.pushInscrever({ endpoint: json.endpoint, keys: { p256dh, auth } });
        } catch (e) {
          if (ativo) {
            setErro(tratarErroRef.current(e));
            setEstado('inativo');
          }
          return;
        }
        if (ativo) setEstado('ativo');
      } catch {
        // Recurso desligado/indisponível: some sem ruído (o sino interno segue funcionando).
        if (ativo) setEstado('oculto');
      }
    })();
    return () => {
      ativo = false;
    };
  }, []);

  async function ativar() {
    if (!config?.chave_publica || emAndamento.current) return;
    emAndamento.current = true;
    setExplicando(false);
    setOcupado(true);
    setErro(null);
    try {
      const permissao = await Notification.requestPermission();
      if (permissao === 'denied') {
        setEstado('negada');
        return;
      }
      // Pedido fechado sem resposta: nada muda e não insistimos.
      if (permissao !== 'granted') return;
      const insc = await inscreverAparelho(config.chave_publica);
      const json = insc.toJSON();
      const p256dh = json.keys?.p256dh;
      const auth = json.keys?.auth;
      if (!json.endpoint || !p256dh || !auth) {
        await insc.unsubscribe().catch(() => false);
        setErro('Este navegador não devolveu os dados necessários para ativar os avisos.');
        return;
      }
      try {
        await api.pushInscrever({ endpoint: json.endpoint, keys: { p256dh, auth } });
      } catch (e) {
        await insc.unsubscribe().catch(() => false);
        throw e;
      }
      setEstado('ativo');
    } catch (e) {
      setErro(
        e instanceof Error && e.name === 'NotAllowedError'
          ? 'O navegador não permitiu ativar os avisos.'
          : tratarErro(e),
      );
    } finally {
      emAndamento.current = false;
      setOcupado(false);
    }
  }

  async function desativar() {
    setOcupado(true);
    setErro(null);
    try {
      const insc = await inscricaoAtual();
      if (insc) {
        await api.pushRevogar(insc.endpoint);
        await insc.unsubscribe();
      }
      setEstado('inativo');
    } catch (e) {
      setErro(tratarErro(e));
    } finally {
      setOcupado(false);
    }
  }

  if (estado === 'carregando' || estado === 'oculto') return null;

  return (
    <>
      <Cartao>
        {estado === 'sem_suporte' ? (
          <p className="text-texto-2">
            Este navegador não permite avisos no aparelho. Você continua vendo as notificações aqui,
            no sino do sistema.
          </p>
        ) : null}

        {estado === 'ios' ? (
          <div className="text-texto-2">
            <p>
              No iPhone e no iPad (iOS/iPadOS 16.4 ou mais novo), os avisos só funcionam com o
              sistema instalado na Tela de Início:
            </p>
            <ol className="mt-2 list-decimal space-y-1 pl-5">
              <li>Abra este endereço no Safari.</li>
              <li>Toque no botão Compartilhar.</li>
              <li>Escolha &quot;Adicionar à Tela de Início&quot;.</li>
              <li>Abra o sistema pelo novo ícone e volte a esta tela.</li>
            </ol>
          </div>
        ) : null}

        {estado === 'negada' ? (
          <p className="text-texto-2">
            Os avisos estão bloqueados neste navegador. Para ativá-los, libere as notificações deste
            site nas configurações do navegador (geralmente no cadeado ao lado do endereço) e
            recarregue a página.
          </p>
        ) : null}

        {estado === 'inativo' ? (
          <>
            <p className="text-texto-2">
              Receba avisos neste aparelho quando houver novidades para você.
            </p>
            <Button tamanho="cheio" carregando={ocupado} onClick={() => setExplicando(true)}>
              {ocupado ? 'Ativando...' : 'Ativar avisos neste aparelho'}
            </Button>
          </>
        ) : null}

        {estado === 'ativo' ? (
          <>
            <p role="status" className="text-texto-2">
              Avisos ativos neste aparelho.
            </p>
            <Button
              variante="contorno"
              tamanho="cheio"
              carregando={ocupado}
              onClick={() => void desativar()}
            >
              {ocupado ? 'Desativando...' : 'Desativar avisos neste aparelho'}
            </Button>
          </>
        ) : null}

        {erro ? <Alerta>{erro}</Alerta> : null}
      </Cartao>

      <Dialogo
        aberto={explicando}
        titulo="Ativar avisos neste aparelho?"
        descricao="Você receberá avisos genéricos, como “Você tem uma nova notificação”. Nenhum dado da viagem, de pessoas ou de veículos é enviado no aviso: os detalhes só aparecem depois que você entrar no sistema. Em seguida, o navegador vai pedir sua permissão."
        role="dialog"
        onFechar={() => setExplicando(false)}
      >
        <div className="flex flex-col gap-2">
          <Button tamanho="cheio" onClick={() => void ativar()}>
            Continuar
          </Button>
          <Button variante="contorno" tamanho="cheio" onClick={() => setExplicando(false)}>
            Voltar
          </Button>
        </div>
      </Dialogo>
    </>
  );
}
