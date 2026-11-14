'use client';

import * as React from 'react';
import { Mic, Square, X } from 'lucide-react';
import { useAuth } from '@/lib/auth';
import { MENSAGEM_ERRO_VOZ, useVoz } from '@/lib/use-voz';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Dialogo } from '@/components/ui/dialogo';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/input';

type Acao = 'inserir' | 'substituir';

/** Área de revisão: o texto reconhecido é editável e só entra no campo por ação explícita. */
function Revisao({
  inicial,
  idBase,
  temTexto,
  aoAplicar,
  aoDescartar,
}: {
  inicial: string;
  idBase: string;
  temTexto: boolean;
  aoAplicar: (texto: string, acao: Acao) => void;
  aoDescartar: () => void;
}) {
  const [texto, setTexto] = React.useState(inicial);
  const [confirmando, setConfirmando] = React.useState(false);

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-borda bg-fundo p-3">
      <div>
        <Label htmlFor={`${idBase}-revisao`}>Texto reconhecido</Label>
        <Textarea
          id={`${idBase}-revisao`}
          value={texto}
          onChange={(e) => setTexto(e.target.value)}
          className="focus-visible:border-foco"
        />
        <p className="mt-1 text-sm text-texto-2">Revise e corrija antes de inserir no campo.</p>
      </div>
      {confirmando ? (
        <div role="alertdialog" aria-label="Confirmar substituição" className="flex flex-col gap-2">
          <p className="font-medium">
            Substituir tudo o que já foi digitado no campo pelo texto reconhecido?
          </p>
          <div className="grid grid-cols-2 gap-2">
            <Button
              type="button"
              variante="perigo"
              data-autofocus
              onClick={() => aoAplicar(texto, 'substituir')}
            >
              Sim, substituir
            </Button>
            <Button type="button" variante="contorno" onClick={() => setConfirmando(false)}>
              Voltar
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <Button
            type="button"
            disabled={!texto.trim()}
            onClick={() => aoAplicar(texto, 'inserir')}
          >
            Inserir no fim do campo
          </Button>
          {temTexto ? (
            <Button
              type="button"
              variante="contorno"
              disabled={!texto.trim()}
              onClick={() => setConfirmando(true)}
            >
              Substituir
            </Button>
          ) : null}
          <Button type="button" variante="link" onClick={aoDescartar}>
            Descartar
          </Button>
        </div>
      )}
    </div>
  );
}

/**
 * Envolve um campo de texto livre e acrescenta o ditado por voz. Só aparece com
 * `eu.recursos.voz_campos === true` (a API decide) e navegador compatível; senão renderiza só o
 * campo. O microfone liga apenas no clique, e o texto nunca é enviado: só entra no campo
 * controlado pelo formulário, que o usuário envia como sempre.
 */
export function CampoVoz({
  id,
  valor,
  aoMudar,
  maxLength,
  children,
}: {
  id: string;
  valor: string;
  aoMudar: (novo: string) => void;
  maxLength?: number;
  children: React.ReactNode;
}) {
  const { eu } = useAuth();
  const voz = useVoz();
  const [aviso, setAviso] = React.useState<string | null>(null);

  if (eu?.recursos?.voz_campos !== true || voz.estado === 'indisponivel') return <>{children}</>;

  const { estado } = voz;
  const capturando =
    estado === 'pedindo_permissao' || estado === 'ouvindo' || estado === 'processando';

  const aplicar = (texto: string, acao: Acao) => {
    const novo = texto.trim();
    let final: string;
    if (acao === 'substituir') {
      final = novo;
    } else {
      const sep = valor && !/\s$/.test(valor) ? ' ' : '';
      final = `${valor}${sep}${novo}`;
    }
    if (maxLength !== undefined && final.length > maxLength) {
      final = final.slice(0, maxLength);
      setAviso(
        `O texto passou do limite de ${maxLength} caracteres e foi cortado. Confira o campo.`,
      );
    } else {
      setAviso(null);
    }
    aoMudar(final);
    voz.limparResultado();
  };

  return (
    <div
      className="flex flex-col gap-2"
      onBlur={(e) => {
        // Saiu do campo e dos controles de voz: encerra a captura.
        if (capturando && !e.currentTarget.contains(e.relatedTarget as Node | null)) voz.parar();
      }}
    >
      {children}

      <div className="flex flex-wrap items-center gap-2">
        {capturando ? (
          <>
            <div role="status" className="flex min-h-11 items-center gap-2 font-medium">
              <span aria-hidden className="size-3 rounded-full bg-erro motion-safe:animate-pulse" />
              {estado === 'pedindo_permissao'
                ? 'Aguardando o microfone...'
                : estado === 'ouvindo'
                  ? 'Ouvindo...'
                  : 'Processando...'}
            </div>
            <Button
              type="button"
              variante="contorno"
              aria-pressed={true}
              aria-label={`Parar de ouvir e revisar o texto do campo`}
              disabled={estado === 'processando'}
              onClick={voz.parar}
            >
              <Square aria-hidden className="size-4" /> Parar
            </Button>
            <Button type="button" variante="link" onClick={voz.cancelar}>
              <X aria-hidden className="size-4" /> Cancelar
            </Button>
          </>
        ) : (
          <Button
            type="button"
            variante="contorno"
            aria-pressed={false}
            aria-label="Falar para preencher o campo"
            aria-describedby={`${id}-voz-dica`}
            onClick={() => {
              setAviso(null);
              voz.iniciar();
            }}
          >
            <Mic aria-hidden className="size-5" /> Falar
          </Button>
        )}
      </div>
      {estado === 'ocioso' && !voz.resultado ? (
        <p id={`${id}-voz-dica`} className="text-sm text-texto-2">
          Vou ouvir sua voz; o navegador pode pedir acesso ao microfone. Você também pode digitar.
        </p>
      ) : null}
      {estado === 'ouvindo' && voz.parcial ? (
        <p className="min-w-0 text-sm break-words text-texto-2" aria-live="off">
          {voz.parcial}
        </p>
      ) : null}

      {estado === 'erro' && voz.erro ? (
        <Alerta>
          {MENSAGEM_ERRO_VOZ[voz.erro]}{' '}
          {voz.erro !== 'permissao_negada' ? (
            <button
              type="button"
              className="min-h-11 font-semibold underline"
              onClick={() => {
                voz.limparErro();
                voz.iniciar();
              }}
            >
              Tentar de novo
            </button>
          ) : null}
        </Alerta>
      ) : null}

      {voz.resultado ? (
        <Revisao
          key={voz.resultado}
          inicial={voz.resultado}
          idBase={id}
          temTexto={valor.trim().length > 0}
          aoAplicar={aplicar}
          aoDescartar={voz.limparResultado}
        />
      ) : null}

      {aviso ? (
        <p role="status" className="text-sm font-medium text-erro">
          {aviso}
        </p>
      ) : null}

      <Dialogo
        aberto={voz.aguardaConsentimento}
        titulo="Usar reconhecimento de voz?"
        role="dialog"
        onFechar={() => {
          if (voz.aguardaConsentimento) voz.cancelar();
        }}
      >
        <div className="flex flex-col gap-3 text-texto-2">
          <p>
            Este navegador não reconhece voz no próprio aparelho. Ao continuar, o áudio do seu
            microfone será processado pelo serviço do fornecedor do navegador (por exemplo, Google
            no Chrome ou Apple no Safari), sob a política de privacidade dele.
          </p>
          <p>
            O SisVeiculos não grava nem armazena o áudio e não o envia ao n8n. Só o texto
            reconhecido volta para você revisar; nada é enviado sem você confirmar o formulário.
          </p>
          <div className="grid grid-cols-2 gap-2">
            <Button type="button" onClick={voz.consentir}>
              Concordar e usar
            </Button>
            <Button type="button" variante="contorno" data-autofocus onClick={voz.cancelar}>
              Cancelar
            </Button>
          </div>
        </div>
      </Dialogo>
    </div>
  );
}
