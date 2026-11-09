'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { AlertTriangle, BellOff, CircleDot } from 'lucide-react';
import { api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { dataHora, dataRelativa } from '@/lib/formato';
import type { Notificacao } from '@/lib/tipos';
import { useCarga } from '@/lib/use-carga';
import { cn } from '@/lib/utils';
import { AppShell } from '@/components/app-shell';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';

const TAMANHO_PAGINA = 20;

/** Só aceita caminho interno relativo vindo do servidor (nunca URL externa). */
function destinoSeguro(destino: string | null): string | null {
  if (!destino || !destino.startsWith('/')) return null;
  if (destino.startsWith('//') || destino.startsWith('/\\')) return null;
  try {
    const u = new URL(destino, window.location.origin);
    return u.origin === window.location.origin ? destino : null;
  } catch {
    return null;
  }
}

function Cartao({
  n,
  agora,
  onToque,
}: {
  n: Notificacao;
  agora: number;
  onToque: (n: Notificacao) => void;
}) {
  const naoLida = n.lida_em === null;
  const interativo = naoLida || destinoSeguro(n.destino) !== null;
  const conteudo = (
    <>
      <span className="flex flex-wrap items-center gap-2">
        {naoLida ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-primaria px-2 py-0.5 text-sm font-semibold text-white">
            <CircleDot aria-hidden className="size-4" />
            Nova
          </span>
        ) : null}
        {n.critica ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-[#fde2e0] px-2 py-0.5 text-sm font-semibold text-[#8a1f17]">
            <AlertTriangle aria-hidden className="size-4" />
            Importante
          </span>
        ) : null}
      </span>
      <span className={cn('mt-1 block break-words', naoLida ? 'font-semibold' : 'font-medium')}>
        {n.titulo}
      </span>
      <span className="mt-1 block break-words text-texto-2">{n.texto}</span>
      <time
        dateTime={n.criada_em}
        title={dataHora(n.criada_em)}
        className="mt-2 block text-sm text-texto-2"
      >
        {dataRelativa(n.criada_em, agora)}
      </time>
    </>
  );
  const classes = cn(
    'block min-h-11 w-full rounded-xl border bg-white p-4 text-left',
    naoLida ? 'border-primaria' : 'border-borda',
  );
  return (
    <li>
      {interativo ? (
        <button type="button" onClick={() => onToque(n)} className={classes}>
          {conteudo}
        </button>
      ) : (
        <div className={classes}>{conteudo}</div>
      )}
    </li>
  );
}

function Conteudo() {
  const { tratarErro, recarregarNaoLidas, naoLidas } = useAuth();
  const router = useRouter();
  const [somenteNaoLidas, setSomenteNaoLidas] = React.useState(false);
  // Páginas seguintes ao "Carregar mais" (a primeira vem de useCarga) e marcações locais.
  const [extras, setExtras] = React.useState<{ itens: Notificacao[]; total: number } | null>(null);
  const [lidas, setLidas] = React.useState<Record<string, string>>({});
  const [maisCarregando, setMaisCarregando] = React.useState(false);
  const [ocupado, setOcupado] = React.useState(false);
  const [erroAcao, setErroAcao] = React.useState<string | null>(null);
  const [agora, setAgora] = React.useState(() => Date.now());

  React.useEffect(() => {
    const id = window.setInterval(() => setAgora(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, []);

  const primeira = useCarga(async () => {
    const r = await api.notificacoes({
      limite: TAMANHO_PAGINA,
      offset: 0,
      somente_nao_lidas: somenteNaoLidas,
    });
    void recarregarNaoLidas();
    return r;
  }, String(somenteNaoLidas));

  const base = primeira.dados?.itens ?? [];
  const ids = new Set(base.map((x) => x.id));
  const itens = [...base, ...(extras?.itens ?? []).filter((x) => !ids.has(x.id))].map((x) =>
    lidas[x.id] ? { ...x, lida_em: lidas[x.id] ?? null } : x,
  );
  const total = extras?.total ?? primeira.dados?.total ?? 0;
  const erro = erroAcao ?? primeira.erro;

  async function maisItens() {
    setMaisCarregando(true);
    try {
      const r = await api.notificacoes({
        limite: TAMANHO_PAGINA,
        offset: itens.length,
        somente_nao_lidas: somenteNaoLidas,
      });
      setExtras((atual) => ({ itens: [...(atual?.itens ?? []), ...r.itens], total: r.total }));
      setErroAcao(null);
    } catch (e) {
      setErroAcao(tratarErro(e));
    } finally {
      setMaisCarregando(false);
    }
  }

  async function tocar(n: Notificacao) {
    if (n.lida_em === null) {
      try {
        const r = await api.marcarNotificacaoLida(n.id);
        setLidas((atual) => ({ ...atual, [n.id]: r.lida_em }));
        void recarregarNaoLidas();
      } catch (e) {
        // Melhor esforço: a falha em marcar não impede de abrir o destino.
        setErroAcao(tratarErro(e));
      }
    }
    const destino = destinoSeguro(n.destino);
    if (destino) router.push(destino);
  }

  async function marcarTodas() {
    setOcupado(true);
    setErroAcao(null);
    try {
      await api.marcarNotificacoesLidas();
      setExtras(null);
      setLidas({});
      await primeira.recarregar();
    } catch (e) {
      setErroAcao(tratarErro(e));
    } finally {
      setOcupado(false);
    }
  }

  const carregando = primeira.carregando;

  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <span id="rotulo-nao-lidas" className="font-medium">
          Somente não lidas
        </span>
        <Switch
          checked={somenteNaoLidas}
          onChange={() => {
            setExtras(null);
            setErroAcao(null);
            setSomenteNaoLidas((v) => !v);
          }}
          aria-labelledby="rotulo-nao-lidas"
        />
      </div>
      {naoLidas > 0 ? (
        <Button
          variante="contorno"
          tamanho="cheio"
          className="mt-3"
          disabled={ocupado}
          onClick={() => void marcarTodas()}
        >
          Marcar todas como lidas
        </Button>
      ) : null}

      {erro ? <Alerta className="mt-4">{erro}</Alerta> : null}

      {carregando ? (
        <p role="status" className="mt-4 text-texto-2">
          Carregando...
        </p>
      ) : itens.length === 0 ? (
        <div className="mt-8 flex flex-col items-center gap-2 text-center text-texto-2">
          <BellOff aria-hidden className="size-8" />
          <p role="status">
            {somenteNaoLidas ? 'Nenhuma notificação não lida.' : 'Você não tem notificações.'}
          </p>
        </div>
      ) : (
        <>
          <ul aria-label="Notificações" className="lista-animada mt-4 flex flex-col gap-3">
            {itens.map((n) => (
              <Cartao key={n.id} n={n} agora={agora} onToque={(x) => void tocar(x)} />
            ))}
          </ul>
          {itens.length < total ? (
            <Button
              variante="contorno"
              tamanho="cheio"
              className="mt-4"
              disabled={maisCarregando}
              onClick={() => void maisItens()}
            >
              Carregar mais
            </Button>
          ) : null}
        </>
      )}
    </>
  );
}

export default function Notificacoes() {
  return (
    <AppShell titulo="Notificações">
      <Conteudo />
    </AppShell>
  );
}
