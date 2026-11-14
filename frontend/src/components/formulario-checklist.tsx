'use client';

import * as React from 'react';
import { ImagePlus, X } from 'lucide-react';
import { api, ApiErro } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { COMBUSTIVEIS, rotuloMomento } from '@/lib/checklist';
import { reprocessarImagem } from '@/lib/imagem';
import { supabase } from '@/lib/supabase';
import { useTatil } from '@/lib/tatil';
import type { FotoEnviada, MomentoChecklist } from '@/lib/tipos';
import { cn } from '@/lib/utils';
import { CampoVoz } from '@/components/campo-voz';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/input';

// Teto de seleção no seletor (UX); a API revalida limite, tipo e tamanho.
const MAX_FOTOS_UI = 6;
// Erros após os quais o objeto no Storage não vale mais: as fotos serão reenviadas.
const REENVIAR = new Set(['foto_nao_enviada', 'foto_divergente', 'foto_invalida']);

type EstadoFoto = 'processando' | 'pronta' | 'enviando' | 'enviada' | 'erro';
interface Foto {
  chave: string;
  nome: string;
  blob: Blob | null;
  preview: string | null;
  estado: EstadoFoto;
  erro: string | null;
  enviada: FotoEnviada | null;
}

const ROTULO_ESTADO: Record<EstadoFoto, string> = {
  processando: 'Preparando...',
  pronta: 'Pronta para enviar',
  enviando: 'Enviando...',
  enviada: 'Enviada',
  erro: 'Falha',
};

const opcao =
  'flex min-h-11 cursor-pointer items-center justify-center rounded-padrao border px-1 text-base font-medium has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-foco';
const opcaoOn = 'border-primaria bg-primaria text-primaria-fg';
const opcaoOff = 'border-borda-campo bg-white text-texto';

/**
 * Checklist do momento indicado por `checklist_pendente`. Só envia a intenção do usuário; a API
 * valida tudo. As fotos são reprocessadas (JPEG, sem EXIF/GPS) antes de subir ao Storage.
 */
export function FormularioChecklist({
  requisicaoId,
  momento,
  aoRegistrar,
}: {
  requisicaoId: string;
  momento: MomentoChecklist;
  aoRegistrar: () => void | Promise<void>;
}) {
  const { tratarErro } = useAuth();
  const tatil = useTatil();
  const [combustivel, setCombustivel] = React.useState('');
  const [avarias, setAvarias] = React.useState<boolean | null>(null);
  const [descricao, setDescricao] = React.useState('');
  const [fotos, setFotos] = React.useState<Foto[]>([]);
  const [enviando, setEnviando] = React.useState(false);
  const [erro, setErro] = React.useState<string | null>(null);
  const previews = React.useRef(new Set<string>());
  const pre = `ck-${requisicaoId}-${momento}`;

  React.useEffect(() => {
    const conjunto = previews.current;
    return () => {
      conjunto.forEach((u) => URL.revokeObjectURL(u));
      conjunto.clear();
    };
  }, []);

  const atualizar = (chave: string, p: Partial<Foto>) =>
    setFotos((fs) => fs.map((f) => (f.chave === chave ? { ...f, ...p } : f)));

  const adicionar = async (lista: FileList | null) => {
    if (!lista) return;
    const novos = Array.from(lista).slice(0, Math.max(0, MAX_FOTOS_UI - fotos.length));
    for (const arq of novos) {
      const chave = crypto.randomUUID();
      setFotos((fs) => [
        ...fs,
        {
          chave,
          nome: arq.name,
          blob: null,
          preview: null,
          estado: 'processando',
          erro: null,
          enviada: null,
        },
      ]);
      try {
        const blob = await reprocessarImagem(arq);
        const preview = URL.createObjectURL(blob);
        previews.current.add(preview);
        atualizar(chave, { blob, preview, estado: 'pronta' });
      } catch {
        atualizar(chave, { estado: 'erro', erro: 'Não foi possível ler esta imagem.' });
      }
    }
  };

  const remover = (f: Foto) => {
    if (f.preview) {
      URL.revokeObjectURL(f.preview);
      previews.current.delete(f.preview);
    }
    setFotos((fs) => fs.filter((x) => x.chave !== f.chave));
  };

  const subir = async (f: Foto): Promise<FotoEnviada | null> => {
    if (f.enviada) return f.enviada;
    if (!f.blob) return null;
    atualizar(f.chave, { estado: 'enviando', erro: null });
    try {
      const dados = { mime: f.blob.type, tamanho: f.blob.size };
      const up = await api.solicitarUploadFoto(requisicaoId, momento, dados);
      const { error } = await supabase()
        .storage.from('checklists')
        .uploadToSignedUrl(up.caminho, up.token, f.blob, { contentType: dados.mime });
      if (error) throw new Error('storage');
      const ok = { id: up.foto_id, ...dados };
      atualizar(f.chave, { estado: 'enviada', enviada: ok });
      return ok;
    } catch (e) {
      const msg = e instanceof ApiErro ? tratarErro(e) : 'Falha ao enviar a foto. Tente novamente.';
      atualizar(f.chave, { estado: 'erro', erro: msg });
      return null;
    }
  };

  const enviar = async (e: React.FormEvent) => {
    e.preventDefault();
    if (enviando) return;
    setEnviando(true);
    setErro(null);
    let registrado = false;
    try {
      const enviadas: FotoEnviada[] = [];
      let falhou = false;
      for (const f of fotos) {
        const r = f.estado === 'processando' ? null : await subir(f);
        if (r) enviadas.push(r);
        else falhou = true;
      }
      if (falhou) {
        setErro('Há fotos com problema. Tente novamente ou remova-as antes de registrar.');
        return;
      }
      await api.registrarChecklist(requisicaoId, momento, {
        combustivel,
        avarias: avarias === true,
        ...(avarias === true ? { descricao_avarias: descricao } : {}),
        fotos: enviadas,
      });
      registrado = true;
      tatil.checklist();
      await aoRegistrar();
    } catch (err) {
      if (!registrado) tatil.erro();
      setErro(tratarErro(err));
      if (err instanceof ApiErro && REENVIAR.has(err.codigo)) {
        setFotos((fs) => fs.map((f) => ({ ...f, estado: 'pronta', enviada: null })));
      }
    } finally {
      setEnviando(false);
    }
  };

  const grupo = 'flex flex-col gap-2 border-0 p-0';
  return (
    <form
      onSubmit={(e) => void enviar(e)}
      aria-label={`Checklist de ${rotuloMomento(momento)}`}
      className="flex flex-col gap-4 rounded-xl border border-borda bg-white p-4"
    >
      <h3 className="text-lg font-semibold">Checklist de {rotuloMomento(momento)}</h3>
      {erro ? <Alerta>{erro}</Alerta> : null}

      <fieldset className={grupo}>
        <legend className="mb-2 font-medium">Combustível</legend>
        <div className="grid grid-cols-5 gap-2">
          {COMBUSTIVEIS.map((c) => (
            <label
              key={c.valor}
              className={cn(opcao, combustivel === c.valor ? opcaoOn : opcaoOff)}
            >
              <input
                type="radio"
                name={`${pre}-combustivel`}
                value={c.valor}
                required
                checked={combustivel === c.valor}
                onChange={() => setCombustivel(c.valor)}
                className="sr-only"
              />
              {c.rotulo}
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset className={grupo}>
        <legend className="mb-2 font-medium">Avarias</legend>
        <div className="grid grid-cols-2 gap-2">
          {[
            { v: false, r: 'Sem avarias' },
            { v: true, r: 'Há avarias' },
          ].map((o) => (
            <label key={String(o.v)} className={cn(opcao, avarias === o.v ? opcaoOn : opcaoOff)}>
              <input
                type="radio"
                name={`${pre}-avarias`}
                required
                checked={avarias === o.v}
                onChange={() => setAvarias(o.v)}
                className="sr-only"
              />
              {o.r}
            </label>
          ))}
        </div>
      </fieldset>

      {avarias === true ? (
        <div>
          <Label htmlFor={`${pre}-descricao`}>Descrição das avarias</Label>
          <CampoVoz
            id={`${pre}-descricao`}
            valor={descricao}
            aoMudar={setDescricao}
            maxLength={500}
          >
            <Textarea
              id={`${pre}-descricao`}
              name="descricao_avarias"
              maxLength={500}
              value={descricao}
              onChange={(e) => setDescricao(e.target.value)}
            />
          </CampoVoz>
          <p className="mt-1 text-sm text-texto-2">{descricao.length}/500</p>
        </div>
      ) : null}

      <fieldset className={grupo}>
        <legend className="mb-2 font-medium">Fotos (até {MAX_FOTOS_UI})</legend>
        {fotos.length > 0 ? (
          <ul className="grid grid-cols-2 gap-3">
            {fotos.map((f) => (
              <li key={f.chave} className="rounded-padrao border border-borda p-2">
                {f.preview ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={f.preview}
                    alt={`Pré-visualização de ${f.nome}`}
                    className="h-28 w-full rounded-padrao object-cover"
                  />
                ) : (
                  <div className="h-28 rounded-padrao bg-fundo" />
                )}
                <p
                  className={cn('mt-1 text-sm', f.estado === 'erro' ? 'text-erro' : 'text-texto-2')}
                  role={f.estado === 'erro' ? 'alert' : 'status'}
                >
                  {f.erro ?? ROTULO_ESTADO[f.estado]}
                </p>
                <Button
                  type="button"
                  variante="contorno"
                  tamanho="cheio"
                  disabled={enviando}
                  onClick={() => remover(f)}
                  aria-label={`Remover foto ${f.nome}`}
                  className="mt-1"
                >
                  <X aria-hidden className="size-4" /> Remover
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
        {fotos.length < MAX_FOTOS_UI ? (
          <>
            <input
              id={`${pre}-fotos`}
              type="file"
              accept="image/*"
              multiple
              disabled={enviando}
              onChange={(e) => {
                void adicionar(e.target.files);
                e.target.value = '';
              }}
              className="sr-only"
            />
            <Label
              htmlFor={`${pre}-fotos`}
              className="mb-0 flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-padrao border border-borda-campo bg-white px-3"
            >
              <ImagePlus aria-hidden className="size-5" /> Adicionar fotos
            </Label>
          </>
        ) : null}
      </fieldset>

      <Button type="submit" tamanho="cheio" carregando={enviando}>
        {enviando ? 'Enviando...' : `Registrar checklist de ${rotuloMomento(momento)}`}
      </Button>
    </form>
  );
}
