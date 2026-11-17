'use client';

import * as React from 'react';
import { createPortal } from 'react-dom';
import { Check, Download, Link as LinkIcone, Share2, X } from 'lucide-react';
import { CartaoImagem } from '@/components/compartilhar/cartao-imagem';
import { Spinner } from '@/components/ui/spinner';
import * as l from '@/lib/compartilhar-imagem';
import { cn } from '@/lib/utils';
import type { Requisicao } from '@/lib/tipos';

/**
 * Speed dial de compartilhamento da requisição (canto inferior direito da coluna).
 *
 * Só exibe o que a API liberou: os itens de imagem aparecem com `compartilhar_imagem` em
 * `acoes_permitidas` (recurso opcional decidido no backend); "Copiar link" sempre (o link continua
 * exigindo login). O cartão é montado fora da tela só durante a geração, com os dados já
 * carregados pela tela, e desmontado em seguida. A geração é local.
 */

const ACAO_IMAGEM = 'compartilhar_imagem';

const MSG = {
  gerando: 'Gerando imagem…',
  baixada: 'Imagem baixada.',
  semSuporte: 'Seu aparelho não compartilha arquivos. A imagem foi baixada.',
  copiado: 'Link copiado.',
  pronta: 'Imagem pronta. Toque para compartilhar.',
  bloqueadoBaixada: 'Não foi possível abrir o compartilhamento. A imagem foi baixada.',
  falha: 'Não foi possível gerar a imagem. Tente novamente.',
  falhaCopiar: 'Não foi possível copiar o link.',
} as const;

type Tarefa = 'compartilhar' | 'baixar' | 'copiar';
type Aviso = { texto: string; erro?: boolean } | null;
type CartaoMontado = {
  req: Requisicao;
  incluirObservacao: boolean;
  aoMontar: (no: HTMLDivElement) => void;
};

export function MenuCompartilhar({ req }: { req: Requisicao }) {
  const podeImagem = req.acoes_permitidas.includes(ACAO_IMAGEM);
  const temObservacao = podeImagem && !!req.observacao;

  const [aberto, setAberto] = React.useState(false);
  const [incluirObservacao, setIncluirObservacao] = React.useState(false);
  const [tarefa, setTarefa] = React.useState<Tarefa | null>(null);
  const [aviso, setAviso] = React.useState<Aviso>(null);
  const [cartao, setCartao] = React.useState<CartaoMontado | null>(null);

  const raizRef = React.useRef<HTMLDivElement>(null);
  const fabRef = React.useRef<HTMLButtonElement>(null);
  const menuRef = React.useRef<HTMLDivElement>(null);
  /** Trava síncrona contra clique duplo (o estado só muda no próximo render). */
  const travaRef = React.useRef(false);
  /** Geração atual: chave dos dados + promessa do PNG (também serve de cache da pré-geração). */
  const geracaoRef = React.useRef<{
    chave: string;
    promessa: Promise<Blob>;
    pronto: boolean;
  } | null>(null);
  const filaRef = React.useRef<Promise<unknown>>(Promise.resolve());
  /** Depois de `NotAllowedError`: o próximo toque compartilha o PNG pronto; se falhar, baixa. */
  const aguardandoToqueRef = React.useRef(false);
  const avisoTimerRef = React.useRef<number | undefined>(undefined);

  const idMenu = React.useId();
  const obs = temObservacao && incluirObservacao;
  const chave = `${req.id}|${req.atualizado_em}|${obs ? 1 : 0}`;
  // Dados mais recentes para a geração (a tela recarrega em tempo real).
  const atualRef = React.useRef({ req, obs, chave });
  // Layout effect: roda antes dos efeitos comuns (a pré-geração já lê os dados novos).
  React.useLayoutEffect(() => {
    atualRef.current = { req, obs, chave };
  }, [req, obs, chave]);

  const mostrarAviso = React.useCallback((a: Aviso, fixo = false) => {
    window.clearTimeout(avisoTimerRef.current);
    setAviso(a);
    if (a && !fixo) avisoTimerRef.current = window.setTimeout(() => setAviso(null), 6000);
  }, []);
  React.useEffect(() => () => window.clearTimeout(avisoTimerRef.current), []);

  // Ao sair da tela: libera uma geração presa esperando a montagem e descarta o PNG da memória.
  const montadoRef = React.useRef(true);
  const rejeitarMontagemRef = React.useRef<((e: Error) => void) | null>(null);
  React.useEffect(() => {
    montadoRef.current = true;
    return () => {
      montadoRef.current = false;
      rejeitarMontagemRef.current?.(new Error('desmontado'));
      geracaoRef.current = null;
    };
  }, []);

  /** Monta o cartão fora da tela, gera o PNG e desmonta. Serializado: um cartão por vez. */
  const obterBlob = React.useCallback((): Promise<Blob> => {
    const { req: dados, obs: comObs, chave: k } = atualRef.current;
    const atual = geracaoRef.current;
    if (atual && atual.chave === k) return atual.promessa;
    const promessa = filaRef.current
      .catch(() => undefined)
      .then(async () => {
        if (!montadoRef.current) throw new Error('desmontado');
        const no = await new Promise<HTMLDivElement>((resolve, reject) => {
          rejeitarMontagemRef.current = reject;
          setCartao({ req: dados, incluirObservacao: comObs, aoMontar: resolve });
        });
        rejeitarMontagemRef.current = null;
        try {
          return await l.gerarBlob(no);
        } finally {
          setCartao(null);
        }
      });
    filaRef.current = promessa;
    const registro = { chave: k, promessa, pronto: false };
    geracaoRef.current = registro;
    promessa.then(
      () => {
        registro.pronto = true;
      },
      () => {
        if (geracaoRef.current === registro) geracaoRef.current = null;
      },
    );
    return promessa;
  }, []);

  // Pré-geração ao abrir (o clique no FAB é o gesto; o Safari conserva a ativação no item) e
  // sempre que os dados ou a caixa "Incluir observação" mudarem com o menu aberto.
  React.useEffect(() => {
    if (aberto && podeImagem) obterBlob().catch(() => undefined);
  }, [aberto, podeImagem, chave, obterBlob]);

  const fechar = React.useCallback((devolverFoco: boolean) => {
    setAberto(false);
    aguardandoToqueRef.current = false;
    // Não guarda o PNG (com dados pessoais) depois de fechar; a próxima abertura refaz.
    if (!travaRef.current) geracaoRef.current = null;
    if (devolverFoco) fabRef.current?.focus();
  }, []);

  // Foco no 1º item ao abrir.
  React.useEffect(() => {
    if (!aberto) return;
    menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [aberto]);

  // ESC fecha e devolve o foco; toque/clique fora fecha.
  React.useEffect(() => {
    if (!aberto) return;
    const tecla = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        fechar(true);
      }
    };
    const fora = (e: PointerEvent) => {
      if (raizRef.current && !raizRef.current.contains(e.target as Node)) fechar(false);
    };
    document.addEventListener('keydown', tecla);
    document.addEventListener('pointerdown', fora);
    return () => {
      document.removeEventListener('keydown', tecla);
      document.removeEventListener('pointerdown', fora);
    };
  }, [aberto, fechar]);

  function navegar(e: React.KeyboardEvent<HTMLDivElement>) {
    const itens = Array.from(
      menuRef.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? [],
    );
    if (itens.length === 0) return;
    const i = itens.indexOf(document.activeElement as HTMLElement);
    let alvo: number | null = null;
    if (e.key === 'ArrowDown') alvo = i < 0 ? 0 : (i + 1) % itens.length;
    else if (e.key === 'ArrowUp') alvo = i <= 0 ? itens.length - 1 : i - 1;
    else if (e.key === 'Home') alvo = 0;
    else if (e.key === 'End') alvo = itens.length - 1;
    else if (e.key === 'Tab') {
      fechar(false); // Tab sai do menu e o fecha
      return;
    }
    if (alvo !== null) {
      e.preventDefault();
      itens[alvo]?.focus();
    }
  }

  async function executar(t: Tarefa) {
    if (travaRef.current) return;
    travaRef.current = true;
    setTarefa(t);
    try {
      if (t === 'copiar') {
        const url = `${window.location.origin}/requisicoes/detalhe?id=${encodeURIComponent(req.id)}`;
        const ok = await l.copiarLink(url);
        mostrarAviso(ok ? { texto: MSG.copiado } : { texto: MSG.falhaCopiar, erro: true });
        if (ok) fechar(true);
        return;
      }
      const g = geracaoRef.current;
      const pronto = !!g && g.pronto && g.chave === atualRef.current.chave;
      if (!pronto) mostrarAviso({ texto: MSG.gerando }, true);
      let blob: Blob;
      try {
        blob = await obterBlob();
      } catch {
        mostrarAviso({ texto: MSG.falha, erro: true });
        return;
      }
      const nome = l.nomeArquivo(req.id);
      if (t === 'baixar') {
        l.baixar(blob, nome);
        mostrarAviso({ texto: MSG.baixada });
        fechar(true);
        return;
      }
      const r = await l.compartilharOuBaixar(blob, nome);
      if (r === 'bloqueado') {
        if (aguardandoToqueRef.current) {
          aguardandoToqueRef.current = false;
          l.baixar(blob, nome);
          mostrarAviso({ texto: MSG.bloqueadoBaixada });
          fechar(true);
        } else {
          // A geração consumiu a ativação: o próximo toque compartilha o PNG já pronto.
          aguardandoToqueRef.current = true;
          mostrarAviso({ texto: MSG.pronta }, true);
        }
        return;
      }
      aguardandoToqueRef.current = false;
      if (r === 'baixado_sem_suporte') mostrarAviso({ texto: MSG.semSuporte });
      else mostrarAviso(null); // compartilhado ou cancelado: silêncio
      fechar(true);
    } catch {
      mostrarAviso({ texto: t === 'copiar' ? MSG.falhaCopiar : MSG.falha, erro: true });
    } finally {
      travaRef.current = false;
      setTarefa(null);
    }
  }

  const ocupado = tarefa !== null;
  let indice = 0;
  const item = (t: Tarefa, rotulo: string, icone: React.ReactNode) => {
    const esta = tarefa === t;
    return (
      <div
        role="none"
        className="speed-dial-item"
        style={{ '--i': indice++ } as React.CSSProperties}
      >
        <button
          type="button"
          role="menuitem"
          aria-disabled={ocupado || undefined}
          aria-busy={esta || undefined}
          onClick={() => void executar(t)}
          className={cn(
            'group flex min-h-11 items-center gap-3 rounded-full py-0.5 pl-1 text-right active:scale-95',
            ocupado && !esta && 'opacity-60',
          )}
        >
          <span className="rounded-padrao border border-borda bg-white px-3 py-1.5 text-base font-medium text-texto shadow-md transition-colors group-hover:bg-fundo">
            {rotulo}
          </span>
          <span
            aria-hidden
            className="flex size-11 shrink-0 items-center justify-center rounded-full border border-borda bg-white text-primaria shadow-md transition-colors group-hover:bg-fundo"
          >
            {esta ? <Spinner className="size-5" /> : icone}
          </span>
        </button>
      </div>
    );
  };

  return (
    <>
      <div
        ref={raizRef}
        className="pointer-events-none fixed inset-x-0 bottom-0 z-20 mx-auto flex max-w-md flex-col items-end gap-3 px-5 pb-[calc(1rem+env(safe-area-inset-bottom,0px))]"
      >
        {aberto ? (
          <div
            ref={menuRef}
            id={idMenu}
            role="menu"
            aria-label="Opções de compartilhamento"
            aria-busy={ocupado || undefined}
            onKeyDown={navegar}
            className="pointer-events-auto flex flex-col items-end gap-2 pr-1.5"
          >
            {temObservacao ? (
              <div
                role="none"
                className="speed-dial-item"
                style={{ '--i': indice++ } as React.CSSProperties}
              >
                <button
                  type="button"
                  role="menuitemcheckbox"
                  aria-checked={incluirObservacao}
                  aria-disabled={ocupado || undefined}
                  onClick={() => {
                    if (!travaRef.current) setIncluirObservacao((v) => !v);
                  }}
                  className="group flex min-h-11 items-center gap-2 rounded-padrao border border-borda bg-white px-3 text-base font-medium text-texto shadow-md transition-colors hover:bg-fundo active:scale-95"
                >
                  <span
                    aria-hidden
                    className={cn(
                      'flex size-5 items-center justify-center rounded border-2',
                      incluirObservacao
                        ? 'border-primaria bg-primaria text-white'
                        : 'border-borda-campo bg-white',
                    )}
                  >
                    {incluirObservacao ? <Check className="size-4" /> : null}
                  </span>
                  Incluir observação
                </button>
              </div>
            ) : null}
            {podeImagem
              ? item('compartilhar', 'Compartilhar imagem', <Share2 className="size-5" />)
              : null}
            {podeImagem ? item('baixar', 'Baixar imagem', <Download className="size-5" />) : null}
            {item('copiar', 'Copiar link', <LinkIcone className="size-5" />)}
          </div>
        ) : null}

        <div className="flex w-full items-end justify-end gap-3">
          <div className="min-w-0 flex-1">
            <div role="status" aria-live="polite" className="flex justify-end">
              {aviso && !aviso.erro ? (
                <p className="speed-dial-aviso pointer-events-auto rounded-padrao border border-borda bg-white px-3 py-2 text-base font-medium text-texto shadow-md">
                  {aviso.texto}
                </p>
              ) : null}
            </div>
            {aviso?.erro ? (
              <p
                role="alert"
                className="speed-dial-aviso pointer-events-auto rounded-padrao border border-erro bg-erro-fundo px-3 py-2 text-base font-medium text-erro shadow-md"
              >
                {aviso.texto}
              </p>
            ) : null}
          </div>
          <button
            ref={fabRef}
            type="button"
            aria-label={aberto ? 'Fechar menu de compartilhamento' : 'Compartilhar requisição'}
            title={aberto ? 'Fechar' : 'Compartilhar'}
            aria-haspopup="menu"
            aria-expanded={aberto}
            aria-controls={idMenu}
            aria-busy={ocupado || undefined}
            onClick={() => (aberto ? fechar(false) : setAberto(true))}
            className="speed-dial-fab pointer-events-auto flex size-14 shrink-0 items-center justify-center rounded-full bg-primaria text-primaria-fg shadow-lg hover:bg-primaria/90 active:scale-95"
          >
            {ocupado && !aberto ? (
              <Spinner className="size-6" />
            ) : aberto ? (
              <X aria-hidden className="size-6" />
            ) : (
              <Share2 aria-hidden className="size-6" />
            )}
          </button>
        </div>
      </div>

      {cartao
        ? createPortal(
            <div
              aria-hidden="true"
              inert
              className="pointer-events-none fixed top-0 -left-[10000px]"
            >
              <CartaoImagem
                ref={(no) => {
                  if (no) cartao.aoMontar(no);
                }}
                req={cartao.req}
                incluirObservacao={cartao.incluirObservacao}
              />
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
