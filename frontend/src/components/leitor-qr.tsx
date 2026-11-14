'use client';

import * as React from 'react';
import Link from 'next/link';
import { Camera, CameraOff, ScanLine } from 'lucide-react';
import { api } from '@/lib/api';
import { extrairCodigo, normalizarCodigoManual } from '@/lib/qr';
import { useTatil } from '@/lib/tatil';
import { useEnvio } from '@/lib/use-envio';
import { useLeitorQr } from '@/lib/use-leitor-qr';
import type { Veiculo } from '@/lib/tipos';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Chip } from '@/components/ui/chip';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Spinner } from '@/components/ui/spinner';

/**
 * Leitor de QR dos veículos. A câmera só abre após o clique; nenhum quadro sai do aparelho
 * (só o código lido vai à API). A API decide disponibilidade e ações; ler o QR não autoriza nada.
 */
export function LeitorQr() {
  const tatil = useTatil();
  const { executar, ocupado, erro, setErro } = useEnvio();
  const [veiculo, setVeiculo] = React.useState<Veiculo | null>(null);
  const [invalido, setInvalido] = React.useState(false);
  const [manual, setManual] = React.useState('');
  const emAndamento = React.useRef(false); // um envio por vez

  const consultar = React.useCallback(
    async (codigo: string) => {
      if (emAndamento.current) return;
      emAndamento.current = true;
      setInvalido(false);
      setVeiculo(null);
      try {
        const r = await executar(() => api.veiculoPorCodigo(codigo));
        if (r.ok) {
          setVeiculo(r.valor);
          tatil.leitura();
        }
      } finally {
        emAndamento.current = false;
      }
    },
    [executar, tatil],
  );

  const aoLer = React.useCallback(
    (texto: string) => {
      const codigo = extrairCodigo(texto);
      if (!codigo) {
        setVeiculo(null);
        setErro(null);
        setInvalido(true);
        return;
      }
      void consultar(codigo);
    },
    [consultar, setErro],
  );

  const { videoRef, estado, iniciar, parar } = useLeitorQr(aoLer);
  const camera = estado === 'ativa' || estado === 'abrindo';

  function enviarManual(e: React.FormEvent) {
    e.preventDefault();
    const codigo = normalizarCodigoManual(manual);
    if (!codigo) {
      setVeiculo(null);
      setErro(null);
      setInvalido(true);
      return;
    }
    void consultar(codigo);
  }

  return (
    <div className="flex flex-col gap-4">
      <section aria-label="Câmera" className="flex flex-col gap-3">
        {/* Um único <video>: oculto até a câmera abrir, para o ref existir antes do início. */}
        <div
          hidden={!camera}
          className="relative overflow-hidden rounded-xl border border-borda bg-black"
        >
          <video
            ref={videoRef}
            muted
            playsInline
            aria-label="Imagem da câmera"
            className="aspect-square w-full object-cover"
          />
          <div
            aria-hidden
            className="pointer-events-none absolute inset-[18%] rounded-xl border-4 border-white/90 shadow-[0_0_0_9999px_rgba(0,0,0,0.35)]"
          />
          <p
            role="status"
            className="absolute inset-x-0 bottom-0 bg-black/70 p-2 text-center text-sm text-white"
          >
            {estado === 'abrindo'
              ? 'Abrindo a câmera...'
              : 'Posicione o QR Code dentro da moldura.'}
          </p>
        </div>

        {camera ? (
          <Button variante="contorno" tamanho="cheio" onClick={parar}>
            <CameraOff aria-hidden /> Encerrar câmera
          </Button>
        ) : (
          <Button tamanho="cheio" onClick={() => void iniciar()}>
            <Camera aria-hidden /> Ler QR Code
          </Button>
        )}

        {estado === 'negada' ? (
          <Alerta>
            A permissão da câmera foi negada. Libere o acesso à câmera nas configurações do
            navegador para este site e toque em &quot;Ler QR Code&quot; de novo, ou digite o código
            abaixo.
          </Alerta>
        ) : null}
        {estado === 'indisponivel' ? (
          <Alerta>
            Não foi possível usar a câmera neste aparelho ou conexão (é preciso HTTPS). Digite o
            código do veículo abaixo.
          </Alerta>
        ) : null}
      </section>

      <form
        onSubmit={enviarManual}
        className="flex flex-col gap-2 rounded-xl border border-borda bg-white p-4"
      >
        <Label htmlFor="codigo-manual">Digitar o código do veículo</Label>
        <Input
          id="codigo-manual"
          value={manual}
          onChange={(e) => setManual(e.target.value)}
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          maxLength={64}
          required
        />
        <Button type="submit" variante="contorno" carregando={ocupado}>
          <ScanLine aria-hidden /> Consultar
        </Button>
      </form>

      <div aria-live="polite" className="flex flex-col gap-3">
        {ocupado ? (
          <p role="status" className="flex items-center gap-2 text-texto-2">
            <Spinner /> Consultando veículo...
          </p>
        ) : null}
        {invalido ? <Alerta>QR Code inválido.</Alerta> : null}
        {erro ? <Alerta>{erro}</Alerta> : null}
        {veiculo ? (
          <article className="rounded-xl border border-borda bg-white p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="text-xl font-semibold break-words">{veiculo.descricao}</h2>
                {veiculo.placa ? <p className="text-texto-2">placa {veiculo.placa}</p> : null}
              </div>
              <Chip tom={veiculo.disponivel ? 'sucesso' : 'perigo'}>
                {veiculo.disponivel ? 'Disponível' : 'Indisponível'}
              </Chip>
            </div>
            {veiculo.motivo_indisponibilidade ? (
              <p className="mt-2 text-texto-2">{veiculo.motivo_indisponibilidade}</p>
            ) : null}
            {veiculo.acoes_permitidas.includes('solicitar') ? (
              <Button asChild tamanho="cheio" className="mt-3">
                <Link href={`/requisicoes/nova?veiculo=${encodeURIComponent(veiculo.id)}`}>
                  Solicitar este veículo
                </Link>
              </Button>
            ) : null}
          </article>
        ) : null}
      </div>
    </div>
  );
}
