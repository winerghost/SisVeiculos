"""Recodificação da foto de perfil no servidor (Pillow). Função pura: bytes entram, bytes saem.

A imagem enviada pelo navegador NUNCA é servida: ela só é lida aqui, decodificada e reconstruída
do zero (RGB, 256x256, JPEG), de modo que nada do arquivo original sobrevive (EXIF/GPS, ICC,
XMP, comentários, trilha de bytes depois do fim da imagem, polyglots).

Decisões de segurança:
- Formatos aceitos: só JPEG, PNG e WebP (`formats=` do Pillow; SVG, GIF, HTML etc. nem chegam
  a um decodificador). O formato real tem de bater com o MIME esperado.
- Bomba de descompressão: `Image.MAX_IMAGE_PIXELS` baixo + checagem própria do tamanho lido do
  cabeçalho ANTES de decodificar (não depende do estado global de `warnings`, que não é seguro
  entre threads). JPEG usa `draft` para decodificar já reduzido.
- Animação (WebP/APNG com mais de um quadro): REJEITADA. É a opção mais segura: não decodifica
  quadros extras (custo de CPU/memória) e evita decidir qual quadro é "a foto".
- Qualquer falha do Pillow vira `ImagemInvalida` sem mensagem: o detalhe nunca chega ao cliente
  nem ao log (pode citar bytes do arquivo).
- No máximo `SIMULTANEAS` recodificações por vez no processo (1 worker x 8 threads) e espera
  curta pela vaga (503 ocupado + Retry-After). Pico medido por recodificação no limite de pixels
  (4032x3024): PNG RGBA ~60 MB, WebP lossless ~190 MB (o decodificador do Pillow/libwebp faz
  cópias internas que não dá para evitar). A redução em faixas evita as cópias de PNG/JPEG.
"""

from __future__ import annotations

import io
import logging
import threading
import warnings
from typing import Final

from PIL import Image, ImageOps

from app.errors import SistemaOcupado

LADO: Final = 256
QUALIDADE: Final = 85
MAX_PIXELS: Final = 4032 * 3024  # ~12,2 Mpx: foto de celular comum
MAX_SAIDA: Final = 1_048_576
SIMULTANEAS: Final = 1  # WebP lossless no limite chega a ~190 MB de pico: uma por vez
ESPERA_VAGA_S: Final = 1.5  # curto: não prende as threads da API inteira

# MIME esperado -> nome do formato no Pillow.
FORMATO_POR_MIME: Final = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
MSG_OCUPADO: Final = "O sistema está ocupado no momento. Tente novamente em instantes."

Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # o Pillow recusa de vez acima do dobro disto
# Entre 1x e 2x o limite o Pillow só AVISA: vira erro (filtro global, definido uma vez no import).
warnings.simplefilter("error", Image.DecompressionBombWarning)

# Em DEBUG o Pillow registra TAGS EXIF (marca, GPS...) das imagens lidas: nunca em log (LGPD).
logging.getLogger("PIL").setLevel(logging.WARNING)

_VAGAS = threading.BoundedSemaphore(SIMULTANEAS)


class ImagemInvalida(Exception):  # noqa: N818
    """Arquivo que não é uma foto aceitável. `animada` distingue a causa mais comum."""

    def __init__(self, *, animada: bool = False) -> None:
        super().__init__("imagem inválida")  # texto fixo: nunca o erro do Pillow
        self.animada = animada


def _abrir(dados: bytes, formato: str) -> Image.Image:
    img = Image.open(io.BytesIO(dados), formats=[formato])
    if img.format != formato:
        raise ImagemInvalida
    largura, altura = img.size
    if largura < 1 or altura < 1 or largura * altura > MAX_PIXELS:
        raise ImagemInvalida
    return img


_TRANSPOSICAO: Final = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}
_MODOS_16_BITS: Final = {"I", "I;16", "I;16L", "I;16B", "F"}
_FAIXA_LINHAS: Final = 64  # linhas de saída por faixa (a faixa de entrada tem fator x isto)


def _tem_alfa(img: Image.Image) -> bool:
    return img.mode in {"RGBA", "LA", "PA"} or (img.mode == "P" and "transparency" in img.info)


def _faixa_rgb(faixa: Image.Image, alfa: bool) -> Image.Image:
    """Uma faixa (pequena) em RGB/RGBA de 8 bits, qualquer que seja o modo de origem."""
    if faixa.mode in _MODOS_16_BITS:
        # 16 bits por canal: o convert direto estoura/clipa; reduz a faixa para 8 bits antes.
        faixa = faixa.point(lambda v: v / 256).convert("L")  # type: ignore[operator]
    return faixa.convert("RGBA" if alfa else "RGB")


def _quadrado_reduzido(img: Image.Image) -> Image.Image:
    """Recorta o quadrado central e o reduz (média por blocos) a ~512-1000 px de lado.

    Trabalha em FAIXAS: só uma faixa da imagem por vez é copiada/convertida/reduzida, então o
    pico de memória é a imagem decodificada (já limitada em pixels) mais algumas dezenas de KB,
    em vez de várias cópias dela (RGBA -> premultiplicado -> RGB...). Devolve RGB sobre fundo
    branco (transparência achatada).
    """
    largura, altura = img.size
    lado = min(largura, altura)
    x0, y0 = (largura - lado) // 2, (altura - lado) // 2
    fator = max(1, lado // (LADO * 2))
    alfa = _tem_alfa(img)
    saida_lado = -(-lado // fator)  # teto, como o Image.reduce
    canvas = Image.new("RGBA" if alfa else "RGB", (saida_lado, saida_lado))
    passo = fator * _FAIXA_LINHAS
    for ya in range(0, lado, passo):
        yb = min(ya + passo, lado)
        faixa = _faixa_rgb(img.crop((x0, y0 + ya, x0 + lado, y0 + yb)), alfa)
        canvas.paste(faixa.reduce(fator), (0, ya // fator))
    if not alfa:
        return canvas
    fundo = Image.new("RGB", canvas.size, (255, 255, 255))
    fundo.paste(canvas, mask=canvas.getchannel("A"))
    return fundo


def _processar(dados: bytes, formato: str) -> bytes:
    # 1) verify() pega arquivos corrompidos/truncados e invalida o objeto; reabre em seguida.
    with _abrir(dados, formato) as primeira:
        primeira.verify()
    img = _abrir(dados, formato)
    if getattr(img, "is_animated", False) or getattr(img, "n_frames", 1) > 1:
        raise ImagemInvalida(animada=True)
    orientacao = img.getexif().get(0x0112)  # lida antes: a redução descarta os metadados
    if formato == "JPEG":
        img.draft("RGB", (LADO * 2, LADO * 2))  # decodifica reduzido: menos memória e tempo
    img.load()
    quadrado = _quadrado_reduzido(img)
    img.close()
    if orientacao in _TRANSPOSICAO:  # mesma regra do ImageOps.exif_transpose, na imagem pequena
        quadrado = quadrado.transpose(_TRANSPOSICAO[orientacao])
    final = ImageOps.fit(quadrado, (LADO, LADO), Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    # O Pillow reaproveita `info` ao salvar (ex.: comentário do JPEG): zera tudo antes.
    final.info = {}
    saida = io.BytesIO()
    # Sem exif=, icc_profile= nem qualquer metadado: só os pixels.
    final.save(saida, format="JPEG", quality=QUALIDADE, optimize=True)
    resultado = saida.getvalue()
    if not 0 < len(resultado) <= MAX_SAIDA:
        raise ImagemInvalida
    return resultado


def recodificar(dados: bytes, mime: str) -> bytes:
    """JPEG 256x256 (RGB, sem metadados) a partir de uma imagem JPEG/PNG/WebP estática.

    Levanta `ImagemInvalida` para qualquer arquivo recusado e `SistemaOcupado` se o servidor
    estiver ocupado com outras recodificações.
    """
    formato = FORMATO_POR_MIME.get(mime)
    if formato is None:
        raise ImagemInvalida
    if not _VAGAS.acquire(timeout=ESPERA_VAGA_S):
        raise SistemaOcupado(MSG_OCUPADO)  # 503 com Retry-After
    try:
        return _processar(dados, formato)
    except (ImagemInvalida, SistemaOcupado):
        raise
    except Exception:  # Pillow levanta OSError, SyntaxError, ValueError, bomba...
        raise ImagemInvalida from None
    finally:
        _VAGAS.release()
