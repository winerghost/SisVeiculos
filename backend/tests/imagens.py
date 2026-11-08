"""Geradores de imagens de teste (Pillow real)."""

from __future__ import annotations

import io

from PIL import Image

VERMELHO = (220, 20, 20)
AZUL = (20, 20, 220)


def salvar(img: Image.Image, formato: str, **kw: object) -> bytes:
    b = io.BytesIO()
    img.save(b, formato, **kw)  # type: ignore[arg-type]
    return b.getvalue()


def jpeg(tamanho: tuple[int, int] = (320, 200), cor: tuple[int, int, int] = VERMELHO) -> bytes:
    return salvar(Image.new("RGB", tamanho, cor), "JPEG")


def png(tamanho: tuple[int, int] = (64, 64)) -> bytes:
    return salvar(Image.new("RGB", tamanho, AZUL), "PNG")


def webp(tamanho: tuple[int, int] = (64, 64)) -> bytes:
    return salvar(Image.new("RGB", tamanho, AZUL), "WEBP")


def jpeg_com_exif_gps() -> bytes:
    """JPEG com EXIF (marca, orientação, GPS) e perfil ICC."""
    exif = Image.Exif()
    exif[0x010F] = "MarcaSecreta"
    exif[0x0112] = 1
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "S", (15.0, 46.0, 30.0)
    gps[3], gps[4] = "W", (47.0, 52.0, 10.0)
    return salvar(
        Image.new("RGB", (300, 300), VERMELHO), "JPEG", exif=exif, icc_profile=b"ICCFAKE" * 40
    )


def jpeg_orientacao_6() -> bytes:
    """200x100: metade esquerda vermelha, direita azul; EXIF manda girar 90 graus (horário)."""
    img = Image.new("RGB", (200, 100), VERMELHO)
    img.paste(Image.new("RGB", (100, 100), AZUL), (100, 0))
    exif = Image.Exif()
    exif[0x0112] = 6
    return salvar(img, "JPEG", exif=exif, quality=95)


def segmentos_jpeg(dados: bytes) -> list[int]:
    """Marcadores dos segmentos antes do início da varredura (ex.: 0xE0 = APP0, 0xE1 = APP1)."""
    i, marcas = 2, []
    while i + 4 <= len(dados) and dados[i] == 0xFF:
        marca = dados[i + 1]
        if marca == 0xDA:  # início dos dados da imagem
            break
        marcas.append(marca)
        i += 2 + int.from_bytes(dados[i + 2 : i + 4], "big")
    return marcas
