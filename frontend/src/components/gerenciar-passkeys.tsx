'use client';

import * as React from 'react';
import { KeyRound } from 'lucide-react';
import { detectarSuportePasskey, type SuportePasskey } from '@/lib/passkeys';
import { dataHora, horaDe } from '@/lib/formato';
import { useAuth } from '@/lib/auth';
import { usePasskeys } from '@/lib/use-passkeys';
import type { Passkey } from '@/lib/tipos';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Dialogo } from '@/components/ui/dialogo';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

const MSG_SUPORTE = {
  sem_https: 'Passkeys exigem conexão segura (HTTPS). Você continua entrando com e-mail e senha.',
  sem_suporte:
    'Este navegador não oferece passkeys. Você continua entrando normalmente com e-mail e senha.',
} as const;

const semAssinatura = () => () => {};

/** Passkeys do usuário (WebAuthn). Só aparece se a API habilitou `recursos.passkeys`. */
export function GerenciarPasskeys() {
  const { eu } = useAuth();
  if (eu?.recursos?.passkeys !== true) return null;
  return <Conteudo />;
}

function Conteudo() {
  const p = usePasskeys();
  // Detecção só no cliente (null no pré-render, sem descompasso de hidratação).
  const suporte = React.useSyncExternalStore<SuportePasskey | null>(
    semAssinatura,
    detectarSuportePasskey,
    () => null,
  );
  const [apelido, setApelido] = React.useState('');
  const [revogando, setRevogando] = React.useState<Passkey | null>(null);
  const apelidoId = React.useId();

  if (p.indisponivel || suporte === null) return null;

  const ocupado = p.ocupado !== null;

  return (
    <section
      aria-labelledby="titulo-passkeys"
      className="mt-6 flex flex-col gap-3 rounded-xl border border-borda bg-white p-4"
    >
      <h2 id="titulo-passkeys" className="flex items-center gap-2 text-lg font-semibold">
        <KeyRound aria-hidden className="size-5 text-primaria" />
        Passkeys
      </h2>
      <p className="text-texto-2">
        Passkeys confirmam que é você em ações sensíveis, usando o desbloqueio do aparelho. O login
        com e-mail e senha continua igual.
      </p>

      {suporte !== 'ok' ? <Alerta>{MSG_SUPORTE[suporte]}</Alerta> : null}

      {p.itens === null ? (
        <p role="status" className="text-texto-2">
          Carregando...
        </p>
      ) : p.itens.length === 0 ? (
        <p className="text-texto-2">Nenhuma passkey cadastrada.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {p.itens.map((k) => (
            <li key={k.id} className="flex flex-col gap-2 rounded-padrao border border-borda p-3">
              <div>
                <p className="font-medium">{k.apelido || 'Passkey sem apelido'}</p>
                <p className="text-sm text-texto-2">Criada em {dataHora(k.criada_em)}</p>
                <p className="text-sm text-texto-2">
                  {k.ultimo_uso_em ? `Último uso: ${dataHora(k.ultimo_uso_em)}` : 'Ainda não usada'}
                </p>
              </div>
              {k.acoes_permitidas.includes('revogar') ? (
                <Button
                  variante="contorno"
                  tamanho="cheio"
                  disabled={ocupado}
                  onClick={() => setRevogando(k)}
                >
                  Revogar
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {suporte === 'ok' ? (
        <>
          {p.acoes.includes('cadastrar') ? (
            <>
              <div>
                <Label htmlFor={apelidoId}>Apelido (opcional)</Label>
                <Input
                  id={apelidoId}
                  maxLength={40}
                  value={apelido}
                  autoComplete="off"
                  placeholder="Ex.: Celular pessoal"
                  onChange={(e) => setApelido(e.target.value)}
                />
              </div>
              <Button
                tamanho="cheio"
                carregando={p.ocupado === 'adicionar'}
                disabled={ocupado}
                onClick={() =>
                  void p.adicionar(apelido).then((ok) => {
                    if (ok) setApelido('');
                  })
                }
              >
                {p.ocupado === 'adicionar' ? 'Aguardando o aparelho...' : 'Adicionar passkey'}
              </Button>
            </>
          ) : null}
          {p.acoes.includes('reautenticar') ? (
            <Button
              variante="contorno"
              tamanho="cheio"
              carregando={p.ocupado === 'confirmar'}
              disabled={ocupado}
              onClick={() => void p.confirmar()}
            >
              {p.ocupado === 'confirmar' ? 'Aguardando o aparelho...' : 'Confirmar com passkey'}
            </Button>
          ) : null}
        </>
      ) : null}

      {p.reautenticadoAte ? (
        <p role="status" className="text-texto-2">
          Confirmado até {horaDe(p.reautenticadoAte)}.
        </p>
      ) : null}

      {p.erro ? (
        <Alerta>
          {p.erro}
          {p.reautRequerida
            ? ' Use “Confirmar com passkey” ou saia e entre de novo para continuar.'
            : ''}
        </Alerta>
      ) : null}

      <Dialogo
        aberto={revogando !== null}
        titulo="Revogar passkey?"
        descricao="Esta passkey deixará de funcionar. Você pode cadastrar outra depois."
        onFechar={() => setRevogando(null)}
      >
        <div className="flex flex-col gap-2">
          <Button
            tamanho="cheio"
            onClick={() => {
              const alvo = revogando;
              setRevogando(null);
              if (alvo) void p.revogar(alvo.id);
            }}
          >
            Revogar
          </Button>
          <Button
            variante="contorno"
            tamanho="cheio"
            data-autofocus
            onClick={() => setRevogando(null)}
          >
            Voltar
          </Button>
        </div>
      </Dialogo>
    </section>
  );
}
