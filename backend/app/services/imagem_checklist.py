"""Sanitização das fotos do checklist no servidor (Pillow): bytes entram, bytes saem.

O arquivo enviado pelo navegador (via URL assinada) é não confiável. Aqui ele é decodificado e
reconstruído do zero, no MESMO formato (JPEG->JPEG, PNG->PNG, WebP->WebP, o caminho do Storage
já carrega a extensão), sem nenhum metadado: EXIF/GPS, ICC, XMP, comentários e bytes após o fim
da imagem não sobrevivem (LGPD: a foto do veículo pode ter sido tirada com localização).

Reaproveita a lógica de `imagem_perfil` (abertura segura, limite de pixels, rejeição de
animação, semáforo de memória). Diferente do avatar, NÃO recorta nem reduz: as avarias precisam
de detalhe. A orientação EXIF é aplicada aos pixels antes de descartar o EXIF.
"""

from __future__ import annotations

import io
from typing import Final

from PIL import Image

from app.errors import SistemaOcupado
from app.services import imagem_perfil as _base
from app.services.imagem_perfil import ImagemInvalida

MAX_SAIDA: Final = 5_242_880  # espelha o limite do bucket e o CHECK de checklist_fotos
QUALIDADE_JPEG: Final = 88
QUALIDADE_WEBP: Final = 85


def _normalizar_modo(img: Image.Image) -> Image.Image:
    """8 bits por canal: RGB ou RGBA (PNG/WebP com transparência). JPEG nunca tem alfa."""
    if img.mode in _base._MODOS_16_BITS:
        img = img.point(lambda v: v / 256).convert("L")  # type: ignore[operator]
    if _base._tem_alfa(img):
        return img.convert("RGBA")
    return img.convert("RGB")


def _processar(dados: bytes, formato: str) -> bytes:
    with _base._abrir(dados, formato) as primeira:
        primeira.verify()  # pega arquivos corrompidos/truncados
    img = _base._abrir(dados, formato)
    if getattr(img, "is_animated", False) or getattr(img, "n_frames", 1) > 1:
        raise ImagemInvalida(animada=True)
    orientacao = img.getexif().get(0x0112)  # lida antes: a conversão descarta os metadados
    img.load()
    limpa = _normalizar_modo(img)
    img.close()
    if orientacao in _base._TRANSPOSICAO:
        limpa = limpa.transpose(_base._TRANSPOSICAO[orientacao])
    if formato == "JPEG":
        limpa = limpa.convert("RGB")
    limpa.info = {}  # o Pillow reaproveita `info` ao salvar (comentário, ICC...): zera tudo
    saida = io.BytesIO()
    # Sem exif=, icc_profile=, pnginfo= nem qualquer metadado: só os pixels.
    if formato == "JPEG":
        limpa.save(saida, format="JPEG", quality=QUALIDADE_JPEG, optimize=True)
    elif formato == "PNG":
        limpa.save(saida, format="PNG", optimize=True)
    else:
        limpa.save(saida, format="WEBP", quality=QUALIDADE_WEBP)
    resultado = saida.getvalue()
    if not 0 < len(resultado) <= MAX_SAIDA:
        raise ImagemInvalida
    return resultado


def sanitizar(dados: bytes, mime: str) -> bytes:
    """Reconstrói a foto (mesmo formato, sem metadados, orientação aplicada).

    Levanta `ImagemInvalida` para qualquer arquivo recusado (formato fora de JPEG/PNG/WebP,
    animado, acima do limite de pixels, corrompido, ou que não bate com o MIME) e
    `SistemaOcupado` se o servidor estiver ocupado com outras recodificações.
    """
    formato = _base.FORMATO_POR_MIME.get(mime)
    if formato is None:
        raise ImagemInvalida
    if not _base._VAGAS.acquire(timeout=_base.ESPERA_VAGA_S):
        raise SistemaOcupado(_base.MSG_OCUPADO)  # 503 com Retry-After
    try:
        return _processar(dados, formato)
    except (ImagemInvalida, SistemaOcupado):
        raise
    except Exception:  # Pillow levanta OSError, SyntaxError, ValueError, bomba...
        raise ImagemInvalida from None
    finally:
        _base._VAGAS.release()
