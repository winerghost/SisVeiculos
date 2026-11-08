// Privacidade: reprocessa a imagem no navegador (canvas -> JPEG). O redesenho descarta EXIF,
// inclusive geolocalização, e reduz o tamanho. Não há regra de negócio aqui.
const LADO_MAX = 2048;
const QUALIDADE = 0.85;

export interface OpcoesImagem {
  /** Maior lado (ou lado do quadrado) de saída, em px. */
  ladoMax?: number;
  /** Recorte quadrado central (ex.: foto de perfil). */
  quadrado?: boolean;
  qualidade?: number;
}

export async function reprocessarImagem(
  arquivo: File,
  { ladoMax = LADO_MAX, quadrado = false, qualidade = QUALIDADE }: OpcoesImagem = {},
): Promise<Blob> {
  // imageOrientation aplica a rotação do EXIF antes de ele ser descartado.
  const img = await createImageBitmap(arquivo, { imageOrientation: 'from-image' });
  try {
    const lado = quadrado ? Math.min(img.width, img.height) : Math.max(img.width, img.height);
    const escala = Math.min(1, ladoMax / lado);
    // Origem do recorte: central no modo quadrado; imagem inteira nos demais.
    const sw = quadrado ? lado : img.width;
    const sh = quadrado ? lado : img.height;
    const sx = (img.width - sw) / 2;
    const sy = (img.height - sh) / 2;
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(sw * escala));
    canvas.height = Math.max(1, Math.round(sh * escala));
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('canvas');
    ctx.fillStyle = '#ffffff'; // PNG/WebP com transparência não viram fundo preto
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise<Blob | null>((ok) => canvas.toBlob(ok, 'image/jpeg', qualidade));
    if (!blob) throw new Error('toBlob');
    return blob;
  } finally {
    img.close();
  }
}
