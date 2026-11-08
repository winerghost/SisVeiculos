'use client';

import * as React from 'react';
import { ImagePlus } from 'lucide-react';
import { admin, ApiErro } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { reprocessarImagem } from '@/lib/imagem';
import { supabase } from '@/lib/supabase';
import type { UsuarioAdmin } from '@/lib/tipos';
import { Avatar } from '@/components/avatar';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerTitle,
} from '@/components/ui/drawer';
import { Label } from '@/components/ui/label';

type Etapa = 'vazio' | 'preparando' | 'selecionada' | 'enviando' | 'processando' | 'pronta';

const STATUS: Partial<Record<Etapa, string>> = {
  preparando: 'Preparando a imagem...',
  selecionada: 'Pronta para enviar.',
  enviando: 'Enviando...',
  processando: 'Processando...',
  pronta: 'Foto atualizada.',
};

// Mesmo reprocessamento do FotoPerfil: quadrado central de até 256 px, JPEG, sem EXIF/GPS.
const OPCOES_IMAGEM = { ladoMax: 256, quadrado: true, qualidade: 0.8 };

function Gaveta({
  usuario,
  aoAtualizar,
  aoFechar,
}: {
  usuario: UsuarioAdmin;
  aoAtualizar: (u: UsuarioAdmin) => void;
  aoFechar: () => void;
}) {
  const { tratarErro } = useAuth();
  const [etapa, setEtapa] = React.useState<Etapa>('vazio');
  const [blob, setBlob] = React.useState<Blob | null>(null);
  const [preview, setPreview] = React.useState<string | null>(null);
  const [erro, setErro] = React.useState<string | null>(null);

  React.useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview);
    };
  }, [preview]);

  const ocupado = etapa === 'preparando' || etapa === 'enviando' || etapa === 'processando';

  async function escolher(arquivo: File | undefined) {
    if (!arquivo) return;
    setErro(null);
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
    if (!blob) return;
    setErro(null);
    setEtapa('enviando');
    try {
      const dados = { mime: blob.type, tamanho: blob.size };
      const up = await admin.solicitarUploadFotoUsuario(usuario.id, dados);
      // O objeto de staging vem pronto da API (`caminho`); o cliente não o monta.
      const { error } = await supabase()
        .storage.from('avatares')
        .uploadToSignedUrl(up.caminho, up.token, blob, { contentType: dados.mime });
      if (error) throw new Error('storage');
      setEtapa('processando');
      const atualizado = await admin.confirmarFotoUsuario(usuario.id, up.id);
      aoAtualizar(atualizado);
      setBlob(null);
      setPreview(null);
      setEtapa('pronta');
    } catch (e) {
      setErro(e instanceof ApiErro ? tratarErro(e) : 'Falha ao enviar a foto. Tente novamente.');
      setEtapa('selecionada');
    }
  }

  return (
    <Drawer open onOpenChange={(o) => (o ? undefined : aoFechar())}>
      <DrawerContent>
        <DrawerTitle className="text-xl font-semibold">Trocar foto de {usuario.nome}</DrawerTitle>
        <DrawerDescription className="mb-3 text-texto-2">
          A foto será exibida no perfil do usuário, que poderá removê-la a qualquer momento.
        </DrawerDescription>

        <div className="mb-4 flex items-center gap-4">
          <div className="flex flex-col items-center gap-1">
            <Avatar nome={usuario.nome} fotoUrl={usuario.foto_url} tamanho="grande" />
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
          {STATUS[etapa] ?? ''}
        </p>

        <input
          id="foto-usuario-arquivo"
          type="file"
          accept="image/jpeg,image/png,image/webp"
          disabled={ocupado}
          onChange={(e) => {
            void escolher(e.target.files?.[0]);
            e.target.value = '';
          }}
          className="peer sr-only"
        />
        <Label
          htmlFor="foto-usuario-arquivo"
          className="mb-3 flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-padrao border border-borda-campo bg-white px-3 peer-focus-visible:outline-3 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-foco"
        >
          <ImagePlus aria-hidden className="size-5" /> Escolher foto
        </Label>

        <div className="grid grid-cols-2 gap-3">
          <DrawerClose asChild>
            <Button type="button" variante="contorno" tamanho="cheio">
              {etapa === 'pronta' ? 'Fechar' : 'Cancelar'}
            </Button>
          </DrawerClose>
          <Button
            type="button"
            tamanho="cheio"
            disabled={!blob || ocupado}
            onClick={() => void enviar()}
          >
            Enviar foto
          </Button>
        </div>
      </DrawerContent>
    </Drawer>
  );
}

/** Botão "Trocar foto" (exibido só quando a API oferece `trocar_foto`) e a gaveta de envio. */
export function TrocarFotoUsuario({
  usuario,
  aoAtualizar,
}: {
  usuario: UsuarioAdmin;
  aoAtualizar: (u: UsuarioAdmin) => void;
}) {
  const [aberto, setAberto] = React.useState(false);
  return (
    <>
      <Button
        type="button"
        variante="contorno"
        tamanho="cheio"
        aria-label={`Trocar foto de ${usuario.nome}`}
        onClick={() => setAberto(true)}
      >
        Trocar foto
      </Button>
      {aberto ? (
        <Gaveta usuario={usuario} aoAtualizar={aoAtualizar} aoFechar={() => setAberto(false)} />
      ) : null}
    </>
  );
}
