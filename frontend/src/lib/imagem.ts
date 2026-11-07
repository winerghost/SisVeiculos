// Privacidade: reprocessa a imagem no navegador (canvas -> JPEG). O redesenho descarta EXIF,
// inclusive geolocalização, e reduz o tamanho. Não há regra de negócio aqui.
const LADO_MAX = 2048;
const QUALIDADE = 0.85;

export async function reprocessarImagem(arquivo: File): Promise<Blob> {
  // imageOrientation aplica a rotação do EXIF antes de ele ser descartado.
  const img = await createImageBitmap(arquivo, { imageOrientation: 'from-image' });
  try {
    const escala = Math.min(1, LADO_MAX / Math.max(img.width, img.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(img.width * escala));
    canvas.height = Math.max(1, Math.round(img.height * escala));
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('canvas');
    ctx.fillStyle = '#ffffff'; // PNG/WebP com transparência não viram fundo preto
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise<Blob | null>((ok) => canvas.toBlob(ok, 'image/jpeg', QUALIDADE));
    if (!blob) throw new Error('toBlob');
    return blob;
  } finally {
    img.close();
  }
}
