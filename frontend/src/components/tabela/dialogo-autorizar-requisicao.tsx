'use client';

import * as React from 'react';
import { ArrowLeft, Car, Check, Info } from 'lucide-react';
import { dataHora } from '@/lib/formato';
import type { Motorista, Requisicao } from '@/lib/tipos';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Dialogo } from '@/components/ui/dialogo';
import { Select } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

/**
 * Modal de autorização (desktop). Só exibe os dados da requisição devolvidos pela API e envia a
 * intenção do usuário: a escolha do motorista é opcional no cliente (a API não informa
 * obrigatoriedade) e o servidor valida tudo; o erro da API aparece dentro do modal, que fica aberto.
 */
export function DialogoAutorizarRequisicao({
  req,
  aberto,
  motoristas,
  motoristaPadrao,
  ocupado,
  autorizando,
  erro,
  onConfirmar,
  onFechar,
}: {
  req: Requisicao;
  aberto: boolean;
  /** Mensagem de erro da API para a última tentativa (exibida dentro do modal). */
  erro?: string | null;
  /** Candidatos listados pela API (vazio = recurso Motorista desligado: sem seletor). */
  motoristas: Motorista[];
  motoristaPadrao: string | null;
  /** Qualquer ação em andamento na página: desabilita os botões. */
  ocupado: boolean;
  /** Autorizar desta requisição em andamento: spinner e "Autorizando...". */
  autorizando: boolean;
  onConfirmar: (motoristaId: string) => void;
  onFechar: () => void;
}) {
  return (
    <Dialogo
      aberto={aberto}
      role="dialog"
      larga
      icone={
        <span
          aria-hidden
          className="flex size-11 shrink-0 items-center justify-center rounded-padrao bg-[#dceBff] text-primaria"
        >
          <Car className="size-6" />
        </span>
      }
      comBotaoFechar
      bloqueado={autorizando || ocupado}
      titulo="Autorizar requisição"
      descricao="Confira os dados da solicitação e selecione o motorista."
      onFechar={onFechar}
    >
      <Conteudo
        req={req}
        motoristas={motoristas}
        motoristaPadrao={motoristaPadrao}
        ocupado={ocupado}
        autorizando={autorizando}
        erro={erro}
        onConfirmar={onConfirmar}
        onFechar={onFechar}
      />
    </Dialogo>
  );
}

function Conteudo({
  req,
  motoristas,
  motoristaPadrao,
  ocupado,
  autorizando,
  erro,
  onConfirmar,
  onFechar,
}: {
  req: Requisicao;
  motoristas: Motorista[];
  motoristaPadrao: string | null;
  ocupado: boolean;
  autorizando: boolean;
  erro?: string | null;
  onConfirmar: (motoristaId: string) => void;
  onFechar: () => void;
}) {
  const [valor, setValor] = React.useState(motoristaPadrao ?? '');
  const [enviado, setEnviado] = React.useState(false);
  const id = React.useId();
  const descId = `${id}-ajuda`;
  // Se o hook ignorar a chamada (outra ação em curso), `ocupado`/`autorizando` não mudam e o
  // modal ficaria travado: a guarda local solta sozinha logo depois; quem trava de verdade é o hook.
  React.useEffect(() => {
    if (!enviado) return;
    const t = window.setTimeout(() => setEnviado(false), 800);
    return () => window.clearTimeout(t);
  }, [enviado]);
  const travado = ocupado || autorizando || enviado;
  const linhas: [string, string][] = [
    ['Solicitante', req.nome_solicitante],
    ['Veículo', req.veiculo_descricao ?? '—'],
    ['Trajeto', req.trajeto],
    ['Saída prevista', dataHora(req.hora_saida)],
  ];
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        // Guarda local contra duplo clique/Enter; o hook também trava, mas aqui é imediato.
        if (travado) return;
        setEnviado(true);
        onConfirmar(valor);
      }}
    >
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 rounded-padrao bg-fundo p-4">
        {linhas.map(([rotulo, texto]) => (
          <React.Fragment key={rotulo}>
            <dt className="font-semibold">{rotulo}:</dt>
            <dd className="min-w-0 break-words">{texto}</dd>
          </React.Fragment>
        ))}
      </dl>
      {motoristas.length > 0 ? (
        <div>
          <Label htmlFor={id}>Motorista</Label>
          <Select
            id={id}
            value={valor}
            disabled={travado}
            aria-describedby={descId}
            onChange={(e) => setValor(e.target.value)}
          >
            <option value="">Sem motorista designado</option>
            {motoristas.map((m) => (
              <option key={m.id} value={m.id}>
                {m.nome}
              </option>
            ))}
          </Select>
          <p id={descId} className="mt-2 flex items-start gap-2 text-sm text-texto-2">
            <Info aria-hidden className="mt-0.5 size-4 shrink-0 text-primaria" />
            Ao autorizar, o servidor confere a designação do motorista.
          </p>
        </div>
      ) : null}
      {erro ? <Alerta>{erro}</Alerta> : null}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[auto_1fr]">
        <Button
          type="button"
          variante="contorno"
          data-autofocus
          disabled={travado}
          onClick={onFechar}
        >
          <ArrowLeft aria-hidden className="size-5" />
          Voltar
        </Button>
        <Button type="submit" disabled={travado} carregando={autorizando}>
          {autorizando ? null : <Check aria-hidden className="size-5" />}
          {autorizando ? 'Autorizando...' : 'Confirmar autorização'}
        </Button>
      </div>
    </form>
  );
}
