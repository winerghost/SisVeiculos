'use client';

import * as React from 'react';
import { CheckCircle2, Loader2, XCircle } from 'lucide-react';
import { api, ApiErro } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { FORMATOS } from '@/lib/relatorios';
import type { EnvioRelatorio, FormatoRelatorio, ListaDestinatarios } from '@/lib/tipos';
import { useCarga } from '@/lib/use-carga';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerTitle,
} from '@/components/ui/drawer';
import { Select } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

function CampoFormato({
  valor,
  onChange,
}: {
  valor: FormatoRelatorio;
  onChange: (f: FormatoRelatorio) => void;
}) {
  return (
    <div>
      <Label htmlFor="rel-envio-formato">Formato</Label>
      <Select
        id="rel-envio-formato"
        value={valor}
        onChange={(e) => onChange(e.target.value as FormatoRelatorio)}
      >
        {FORMATOS.map((f) => (
          <option key={f.valor} value={f.valor}>
            {f.rotulo}
          </option>
        ))}
      </Select>
    </div>
  );
}

function Formulario({
  modo,
  ocupado,
  onConfirmar,
}: {
  modo: 'email' | 'compartilhar';
  ocupado: boolean;
  onConfirmar: (formato: FormatoRelatorio, destinatarioId: string) => void;
}) {
  const [formato, setFormato] = React.useState<FormatoRelatorio>('csv');
  const [destino, setDestino] = React.useState('');
  // Os destinatários só são buscados quando o Drawer de e-mail abre.
  const dest = useCarga(() =>
    modo === 'email'
      ? api.destinatariosRelatorio()
      : Promise.resolve<ListaDestinatarios>({ itens: [] }),
  );

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        onConfirmar(formato, destino);
      }}
    >
      {modo === 'email' ? (
        <div aria-busy={dest.carregando}>
          <Label htmlFor="rel-envio-destino">Destinatário</Label>
          <Select
            id="rel-envio-destino"
            required
            value={destino}
            onChange={(e) => setDestino(e.target.value)}
          >
            <option value="">{dest.carregando ? 'Carregando...' : 'Selecione'}</option>
            {(dest.dados?.itens ?? []).map((d) => (
              <option key={d.id} value={d.id}>
                {d.nome}
              </option>
            ))}
          </Select>
          {dest.dados?.truncado ? (
            <p role="note" className="mt-2 text-sm text-texto-2">
              Lista parcial; use um destinatário da lista exibida.
            </p>
          ) : null}
          {dest.erro ? <Alerta className="mt-2">{dest.erro}</Alerta> : null}
        </div>
      ) : null}
      <CampoFormato valor={formato} onChange={setFormato} />
      <Button type="submit" tamanho="cheio" carregando={ocupado}>
        {modo === 'email'
          ? ocupado
            ? 'Enviando...'
            : 'Enviar por e-mail'
          : ocupado
            ? 'Gerando...'
            : 'Compartilhar'}
      </Button>
      <DrawerClose asChild>
        <Button type="button" variante="link" className="self-center">
          Cancelar
        </Button>
      </DrawerClose>
    </form>
  );
}

/** Drawer para escolher formato (e destinatário, no e-mail). A ação em si é do chamador. */
export function DrawerEnvio({
  modo,
  aberto,
  onAbertoChange,
  ocupado,
  onConfirmar,
}: {
  modo: 'email' | 'compartilhar';
  aberto: boolean;
  onAbertoChange: (a: boolean) => void;
  ocupado: boolean;
  onConfirmar: (formato: FormatoRelatorio, destinatarioId: string) => void;
}) {
  return (
    <Drawer open={aberto} onOpenChange={onAbertoChange}>
      <DrawerContent>
        <DrawerTitle className="text-xl font-semibold">
          {modo === 'email' ? 'Enviar por e-mail' : 'Compartilhar relatório'}
        </DrawerTitle>
        <DrawerDescription className="mb-3 text-texto-2">
          {modo === 'email'
            ? 'O relatório é enviado com os filtros atuais.'
            : 'O arquivo é gerado com os filtros atuais e entregue ao compartilhamento do aparelho.'}
        </DrawerDescription>
        <Formulario modo={modo} ocupado={ocupado} onConfirmar={onConfirmar} />
      </DrawerContent>
    </Drawer>
  );
}

const INTERVALO_MS = 2000;
const MAX_CONSULTAS = 15; // ~30 s

const TEXTO: Record<EnvioRelatorio['status'], string> = {
  pendente: 'Envio na fila...',
  enviando: 'Enviando o e-mail...',
  enviado: 'Relatório enviado por e-mail.',
  falhou: 'Não foi possível enviar o e-mail.',
};

/**
 * Acompanha um envio por e-mail: consulta a cada 2 s (até ~30 s), só com a aba visível.
 * Use `key={id}` para reiniciar quando houver um novo envio.
 */
export function AcompanharEnvio({ id, inicial }: { id: string; inicial: EnvioRelatorio }) {
  const { tratarErro } = useAuth();
  const [envio, setEnvio] = React.useState<EnvioRelatorio>(inicial);
  const [erro, setErro] = React.useState<string | null>(null);
  const [esgotou, setEsgotou] = React.useState(false);

  React.useEffect(() => {
    let consultas = 0;
    let consultando = false;
    let ativo = true;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible' || consultando) return;
      consultando = true;
      consultas += 1;
      api
        .envioRelatorio(id)
        .then((r) => {
          if (!ativo) return;
          setEnvio(r);
          if (r.status === 'enviado' || r.status === 'falhou') window.clearInterval(timer);
          else if (consultas >= MAX_CONSULTAS) {
            window.clearInterval(timer);
            setEsgotou(true);
          }
        })
        .catch((e: unknown) => {
          if (!ativo) return;
          window.clearInterval(timer);
          setErro(e instanceof ApiErro ? tratarErro(e) : 'Não foi possível consultar o envio.');
        })
        .finally(() => {
          consultando = false;
        });
    }, INTERVALO_MS);
    return () => {
      ativo = false;
      window.clearInterval(timer);
    };
  }, [id, tratarErro]);

  const andando = envio.status === 'pendente' || envio.status === 'enviando';
  return (
    <div className="mt-4 rounded-xl border border-borda bg-white p-4" role="status">
      <p className="flex items-center gap-2 font-medium">
        {envio.status === 'enviado' ? (
          <CheckCircle2 aria-hidden className="size-5 text-sucesso" />
        ) : envio.status === 'falhou' ? (
          <XCircle aria-hidden className="size-5 text-erro" />
        ) : (
          <Loader2 aria-hidden className="size-5 animate-spin" />
        )}
        {TEXTO[envio.status]}
      </p>
      {envio.status === 'falhou' ? (
        <p className="mt-1 text-erro">
          {envio.mensagem ?? (envio.erro_codigo ? `Código: ${envio.erro_codigo}` : '')}
        </p>
      ) : null}
      {andando && esgotou ? (
        <p className="mt-1 text-texto-2">
          O envio ainda está em andamento. Volte mais tarde para conferir.
        </p>
      ) : null}
      {erro ? <p className="mt-1 text-erro">{erro}</p> : null}
    </div>
  );
}
