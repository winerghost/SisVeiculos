'use client';

import * as React from 'react';
import jsQR from 'jsqr';

export type EstadoCamera = 'parada' | 'abrindo' | 'ativa' | 'negada' | 'indisponivel';

interface DetectorNativo {
  detect(fonte: CanvasImageSource): Promise<{ rawValue: string }[]>;
}
type ConstrutorDetector = new (opcoes: { formats: string[] }) => DetectorNativo;

const INTERVALO_MS = 200; // ~5 leituras por segundo bastam e poupam bateria
const IGNORAR_REPETIDO_MS = 3000;
const LADO_MAX = 480; // reduz o quadro antes do jsQR (custo cai muito)

/** Detector nativo (Chrome/Android) se existir e aceitar QR; senão null e usa o jsQR. */
async function criarDetectorNativo(): Promise<DetectorNativo | null> {
  const Ctor = (
    globalThis as {
      BarcodeDetector?: ConstrutorDetector & { getSupportedFormats?: () => Promise<string[]> };
    }
  ).BarcodeDetector;
  if (!Ctor) return null;
  try {
    const formatos = (await Ctor.getSupportedFormats?.()) ?? ['qr_code'];
    if (!formatos.includes('qr_code')) return null;
    return new Ctor({ formats: ['qr_code'] });
  } catch {
    return null;
  }
}

/**
 * Câmera + decodificação. Nenhum quadro é salvo nem enviado: só o texto lido sai daqui.
 * `aoLer` recebe o texto bruto; a antiduplicidade (mesmo texto em ~3 s) é feita aqui.
 */
export function useLeitorQr(aoLer: (texto: string) => void) {
  const videoRef = React.useRef<HTMLVideoElement>(null);
  const [estado, setEstado] = React.useState<EstadoCamera>('parada');
  const fluxoRef = React.useRef<MediaStream | null>(null);
  const timerRef = React.useRef<number | null>(null);
  const aoLerRef = React.useRef(aoLer);
  const ultimoRef = React.useRef<{ texto: string; em: number } | null>(null);
  const geracaoRef = React.useRef(0);

  React.useEffect(() => {
    aoLerRef.current = aoLer;
  }, [aoLer]);

  const parar = React.useCallback(() => {
    geracaoRef.current += 1; // invalida aberturas e laços em andamento
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    fluxoRef.current?.getTracks().forEach((t) => t.stop());
    fluxoRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setEstado((e) => (e === 'negada' || e === 'indisponivel' ? e : 'parada'));
  }, []);

  const iniciar = React.useCallback(async () => {
    if (fluxoRef.current) return;
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      setEstado('indisponivel');
      return;
    }
    const geracao = ++geracaoRef.current;
    setEstado('abrindo');
    let fluxo: MediaStream;
    try {
      try {
        fluxo = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' } },
          audio: false,
        });
      } catch (e) {
        if (
          e instanceof DOMException &&
          (e.name === 'NotAllowedError' || e.name === 'SecurityError')
        )
          throw e;
        fluxo = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      }
    } catch (e) {
      if (geracao !== geracaoRef.current) return;
      const negada =
        e instanceof DOMException && (e.name === 'NotAllowedError' || e.name === 'SecurityError');
      setEstado(negada ? 'negada' : 'indisponivel');
      return;
    }
    if (geracao !== geracaoRef.current) {
      fluxo.getTracks().forEach((t) => t.stop());
      return;
    }
    fluxoRef.current = fluxo;
    const video = videoRef.current;
    if (!video) {
      parar();
      return;
    }
    video.srcObject = fluxo;
    try {
      await video.play();
    } catch {
      /* o autoplay mudo costuma bastar; o laço espera o quadro ficar pronto */
    }
    if (geracao !== geracaoRef.current) return;
    setEstado('ativa');

    const nativo = await criarDetectorNativo();
    const tela = document.createElement('canvas');
    const ctx = tela.getContext('2d', { willReadFrequently: true });

    const ciclo = async () => {
      if (geracao !== geracaoRef.current) return;
      try {
        if (video.readyState >= 2 && video.videoWidth > 0) {
          let texto: string | null = null;
          if (nativo) {
            texto = (await nativo.detect(video))[0]?.rawValue ?? null;
          } else if (ctx) {
            const escala = Math.min(1, LADO_MAX / Math.max(video.videoWidth, video.videoHeight));
            tela.width = Math.round(video.videoWidth * escala);
            tela.height = Math.round(video.videoHeight * escala);
            ctx.drawImage(video, 0, 0, tela.width, tela.height);
            const img = ctx.getImageData(0, 0, tela.width, tela.height);
            texto =
              jsQR(img.data, img.width, img.height, { inversionAttempts: 'dontInvert' })?.data ??
              null;
          }
          if (texto && geracao === geracaoRef.current) {
            const agora = Date.now();
            const u = ultimoRef.current;
            if (!u || u.texto !== texto || agora - u.em > IGNORAR_REPETIDO_MS) {
              ultimoRef.current = { texto, em: agora };
              aoLerRef.current(texto);
              parar(); // leitura feita: encerra a câmera (o usuário reabre se quiser)
              return;
            }
          }
        }
      } catch {
        /* quadro ruim: tenta o próximo */
      }
      if (geracao === geracaoRef.current)
        timerRef.current = window.setTimeout(() => void ciclo(), INTERVALO_MS);
    };
    void ciclo();
  }, [parar]);

  // Para ao desmontar e ao ocultar a aba (a câmera nunca fica ligada em segundo plano).
  React.useEffect(() => {
    const aoOcultar = () => {
      if (document.hidden) parar();
    };
    document.addEventListener('visibilitychange', aoOcultar);
    return () => {
      document.removeEventListener('visibilitychange', aoOcultar);
      parar();
    };
  }, [parar]);

  const lerDeNovo = React.useCallback(() => {
    ultimoRef.current = null;
  }, []);

  return { videoRef, estado, iniciar, parar, lerDeNovo };
}
