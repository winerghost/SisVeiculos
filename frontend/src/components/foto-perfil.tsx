'use client';

import * as React from 'react';
import { ImagePlus } from 'lucide-react';
import { api, ApiErro } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { reprocessarImagem } from '@/lib/imagem';
import { supabase } from '@/lib/supabase';
import { Avatar } from '@/components/avatar';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';

type Etapa = 'vazio' | 'preparando' | 'selecionada' | 'enviando' | 'processando' | 'pronta';

const STATUS: Partial<Record<Etapa, string>> = {
  preparando: 'Preparando a imagem...',
  selecionada: 'Pronta para enviar.',
  enviando: 'Enviando...',
  processando: 'Processando...',
  pronta: 'Foto atualizada.',
};

// Reprocessamento no navegador: quadrado central de até 256 px, JPEG, sem EXIF/GPS.
const OPCOES_IMAGEM = { ladoMax: 256, quadrado: true, qualidade: 0.8 };

/**
 * Foto do perfil: seleção, pré-visualização, envio por URL assinada e remoção. O consentimento é
 * um texto fixo; limites e validação de conteúdo são da API (aqui só a mensagem recebida).
 */
export function FotoPerfil() {
  const { eu, recarregar, tratarErro } = useAuth();
  const [etapa, setEtapa] = React.useState<Etapa>('vazio');
  const [blob, setBlob] = React.useState<Blob | null>(null);
  const [preview, setPreview] = React.useState<string | null>(null);
  const [erro, setErro] = React.useState<string | null>(null);
  const [removida, setRemovida] = React.useState(false);
  const [removendo, setRemovendo] = React.useState(false);

  React.useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview);
    };
  }, [preview]);

  if (!eu) return null;
  const ocupado = etapa === 'preparando' || etapa === 'enviando' || etapa === 'processando';

  async function escolher(arquivo: File | undefined) {
    if (!arquivo) return;
    setErro(null);
    setRemovida(false);
    setEtapa('preparando');
    try {
      const b = await reprocessarImagem(arquivo, OPCOES_IMAGEM);
      setBlob(b);
      setPreview(URL.createObjectURL(b));
      setEtapa('selecionada');
    } catch {
      setBlob(null);
      setPreview(null);
      setEtapa('vazio');
      setErro('Não foi possível ler esta imagem. Escolha outro arquivo.');
    }
  }

  async function enviar() {
    if (!blob || !eu) return;
    setErro(null);
    setRemovida(false);
    setEtapa('enviando');
    try {
      const dados = { mime: blob.type, tamanho: blob.size };
      const up = await api.solicitarUploadFotoPerfil(dados);
      // Staging do próprio usuário: `tmp/<usuario_id>/<id>` (bucket avatares).
      const { error } = await supabase()
        .storage.from('avatares')
        .uploadToSignedUrl(`tmp/${eu.id}/${up.id}`, up.token, blob, { contentType: dados.mime });
      if (error) throw new Error('storage');
      setEtapa('processando');
      await api.confirmarFotoPerfil(up.id);
      await recarregar(); // o avatar do cabeçalho vem de /api/eu
      setBlob(null);
      setPreview(null);
      setEtapa('pronta');
    } catch (e) {
      setErro(e instanceof ApiErro ? tratarErro(e) : 'Falha ao enviar a foto. Tente novamente.');
      setEtapa('selecionada'); // mantém a pré-visualização para nova tentativa
    }
  }

  async function remover() {
    setErro(null);
    setRemovida(false);
    setRemovendo(true);
    try {
      await api.removerFotoPerfil();
      await recarregar();
      setBlob(null);
      setPreview(null);
      setEtapa('vazio');
      setRemovida(true);
    } catch (e) {
      setErro(tratarErro(e));
    } finally {
      setRemovendo(false);
    }
  }

  return (
    <section
      aria-labelledby="foto-titulo"
      className="mb-6 rounded-xl border border-borda bg-white p-4"
    >
      <h2 id="foto-titulo" className="text-lg font-semibold">
        Foto do perfil
      </h2>
      <p className="mt-1 mb-4 text-texto-2">
        A foto é opcional e serve apenas para identificar você no sistema. Ela não é usada para
        reconhecimento facial. Você pode remover a qualquer momento.
      </p>

      <div className="mb-4 flex items-center gap-4">
        <div className="flex flex-col items-center gap-1">
          <Avatar nome={eu.nome} fotoUrl={eu.foto_url} tamanho="grande" />
          <span className="text-sm text-texto-2">Atual</span>
        </div>
        {preview ? (
          <div className="flex flex-col items-center gap-1">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={preview}
              alt="Pré-visualização da nova foto"
              className="size-24 rounded-full object-cover"
            />
            <span className="text-sm text-texto-2">Nova</span>
          </div>
        ) : null}
      </div>

      {erro ? <Alerta className="mb-3">{erro}</Alerta> : null}
      <p role="status" className="mb-3 min-h-6 text-texto-2">
        {removida ? 'Foto removida.' : (STATUS[etapa] ?? '')}
      </p>

      <input
        id="foto-arquivo"
        type="file"
        accept="image/jpeg,image/png,image/webp"
        disabled={ocupado || removendo}
        onChange={(e) => {
          void escolher(e.target.files?.[0]);
          e.target.value = '';
        }}
        className="peer sr-only"
      />
      <Label
        htmlFor="foto-arquivo"
        className="mb-3 flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-padrao border border-borda-campo bg-white px-3 peer-focus-visible:outline-3 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-foco"
      >
        <ImagePlus aria-hidden className="size-5" /> Escolher foto
      </Label>

      <div className="flex flex-col gap-3">
        <Button
          type="button"
          tamanho="cheio"
          disabled={!blob || ocupado || removendo}
          onClick={() => void enviar()}
        >
          Enviar foto
        </Button>
        {eu.foto_url ? (
          <Button
            type="button"
            variante="perigo"
            tamanho="cheio"
            disabled={ocupado || removendo}
            onClick={() => void remover()}
          >
            Remover foto
          </Button>
        ) : null}
      </div>
    </section>
  );
}
