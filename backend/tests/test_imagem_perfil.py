"""Recodificação da foto de perfil com Pillow REAL: metadados, orientação, alfa, bomba,
formatos proibidos, polyglot e animação."""

from __future__ import annotations

import io
import threading

import pytest
from PIL import Image

from app.errors import SistemaOcupado
from app.services import imagem_perfil
from app.services.imagem_perfil import ImagemInvalida, recodificar
from tests.imagens import (
    AZUL,
    VERMELHO,
    jpeg,
    jpeg_com_exif_gps,
    jpeg_orientacao_6,
    png,
    salvar,
    segmentos_jpeg,
    webp,
)


def abrir(dados: bytes) -> Image.Image:
    return Image.open(io.BytesIO(dados))


def perto(pixel: tuple[int, ...], alvo: tuple[int, int, int], tol: int = 40) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(pixel[:3], alvo, strict=True))


# ------------------------------------------------------------------ metadados
def test_exif_gps_e_icc_removidos_da_saida():
    entrada = jpeg_com_exif_gps()
    # prova de que a ENTRADA tinha mesmo o que deve sumir
    assert b"Exif" in entrada and b"MarcaSecreta" in entrada and b"ICCFAKE" in entrada
    assert dict(abrir(entrada).getexif().get_ifd(0x8825))
    saida = recodificar(entrada, "image/jpeg")
    img = abrir(saida)
    assert img.format == "JPEG" and img.size == (256, 256) and img.mode == "RGB"
    assert not dict(img.getexif()) and not dict(img.getexif().get_ifd(0x8825))
    assert "icc_profile" not in img.info and "exif" not in img.info
    for lixo in (b"Exif", b"MarcaSecreta", b"ICCFAKE", b"GPS", b"ICC_PROFILE"):
        assert lixo not in saida
    # só o cabeçalho JFIF (APP0): nenhum APP1 (EXIF/XMP), APP2 (ICC) nem comentário
    assert set(segmentos_jpeg(saida)) <= {0xE0, 0xDB, 0xC0, 0xC4, 0xC2}


def test_comentario_e_bytes_apos_o_fim_nao_sobrevivem():
    entrada = salvar(Image.new("RGB", (50, 50), VERMELHO), "JPEG", comment=b"segredo-no-comentario")
    entrada += b"<script>alert(1)</script>"  # lixo depois do EOI
    saida = recodificar(entrada, "image/jpeg")
    assert b"segredo" not in saida and b"<script>" not in saida


def test_metadados_de_png_e_webp_tambem_somem():
    exif = Image.Exif()
    exif[0x010F] = "MarcaSecreta"
    com_texto = Image.new("RGB", (40, 40), AZUL)
    for dados, mime in (
        (salvar(com_texto, "PNG", exif=exif), "image/png"),
        (salvar(com_texto, "WEBP", exif=exif), "image/webp"),
    ):
        saida = recodificar(dados, mime)
        assert b"MarcaSecreta" not in saida and not dict(abrir(saida).getexif())


# ------------------------------------------------------------------ geometria e cor
def test_orientacao_exif_e_aplicada():
    saida = abrir(recodificar(jpeg_orientacao_6(), "image/jpeg"))
    # Girada 90 graus horário: o lado esquerdo (vermelho) vai para o TOPO.
    assert perto(saida.getpixel((128, 10)), VERMELHO)
    assert perto(saida.getpixel((128, 245)), AZUL)
    assert not dict(saida.getexif())  # a tag de orientação não fica para trás


def test_png_com_alfa_vira_jpeg_rgb_sobre_fundo_branco():
    img = Image.new("RGBA", (80, 80), (255, 0, 0, 0))  # totalmente transparente
    img.paste(Image.new("RGBA", (40, 80), (255, 0, 0, 255)), (0, 0))  # metade esquerda opaca
    saida = abrir(recodificar(salvar(img, "PNG"), "image/png"))
    assert saida.mode == "RGB" and saida.format == "JPEG"
    assert perto(saida.getpixel((5, 128)), (255, 0, 0), 30)
    assert perto(saida.getpixel((250, 128)), (255, 255, 255), 10)  # transparente => branco


def test_paleta_com_transparencia_e_tons_de_cinza_16_bits():
    pal = Image.new("P", (20, 20), 0)
    pal.info["transparency"] = 0
    assert perto(
        abrir(recodificar(salvar(pal, "PNG", transparency=0), "image/png")).getpixel((3, 3)),
        (255, 255, 255),
        10,
    )
    cinza = Image.new("I;16", (30, 30), 40000)
    saida = abrir(recodificar(salvar(cinza, "PNG"), "image/png"))
    assert saida.mode == "RGB" and saida.size == (256, 256)


@pytest.mark.parametrize("tamanho", [(400, 100), (100, 400), (256, 256), (31, 17), (3000, 2000)])
def test_recorte_quadrado_central_e_redimensiona_para_256(tamanho):
    saida = abrir(recodificar(jpeg(tamanho), "image/jpeg"))
    assert saida.size == (256, 256)


def test_recorte_e_central():
    img = Image.new("RGB", (300, 100), VERMELHO)
    img.paste(Image.new("RGB", (100, 100), AZUL), (100, 0))  # faixa azul no meio
    saida = abrir(recodificar(salvar(img, "PNG"), "image/png"))
    assert perto(saida.getpixel((128, 128)), AZUL) and perto(saida.getpixel((128, 5)), AZUL)


def test_saida_pequena_e_deterministica_em_formato():
    saida = recodificar(jpeg((1600, 1200)), "image/jpeg")
    assert 0 < len(saida) < 100_000 and saida[:3] == bytes([0xFF, 0xD8, 0xFF])


@pytest.mark.parametrize(
    ("dados", "mime"), [(jpeg(), "image/jpeg"), (png(), "image/png"), (webp(), "image/webp")]
)
def test_tres_formatos_aceitos(dados, mime):
    assert abrir(recodificar(dados, mime)).size == (256, 256)


# ------------------------------------------------------------------ recusas
def test_bomba_de_descompressao_e_recusada():
    # 1 bit por pixel: arquivo minúsculo, 25 Mpx (acima do teto de 16 Mpx).
    bomba = salvar(Image.new("1", (5000, 5000)), "PNG")
    assert len(bomba) < 100_000
    with pytest.raises(ImagemInvalida):
        recodificar(bomba, "image/png")
    enorme = salvar(Image.new("1", (9000, 9000)), "PNG")  # acima de 2x: erro do próprio Pillow
    with pytest.raises(ImagemInvalida):
        recodificar(enorme, "image/png")


def test_imagem_no_limite_de_pixels_passa_e_um_pixel_a_mais_nao():
    ok = salvar(Image.new("1", (4032, 3024)), "PNG")  # exatamente 12.192.768 px
    assert abrir(recodificar(ok, "image/png")).size == (256, 256)
    acima = salvar(Image.new("1", (4033, 3024)), "PNG")
    with pytest.raises(ImagemInvalida):
        recodificar(acima, "image/png")


SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="9" height="9"><script>alert(1)</script></svg>'
)
HTML = b"<!doctype html><html><script>alert(1)</script></html>"
GIF = salvar(Image.new("RGB", (5, 5)), "GIF")


@pytest.mark.parametrize("dados", [SVG, HTML, GIF, b"", b"nada", bytes(2000)])
@pytest.mark.parametrize("mime", ["image/jpeg", "image/png", "image/webp"])
def test_formatos_proibidos_e_lixo_sao_recusados(dados, mime):
    with pytest.raises(ImagemInvalida):
        recodificar(dados, mime)


def test_polyglot_cabecalho_jpeg_com_corpo_invalido():
    falso = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"<html><script>alert(1)</script></html>" * 20
    with pytest.raises(ImagemInvalida):
        recodificar(falso, "image/jpeg")
    png_falso = bytes([0x89]) + b"PNG\r\n\x1a\n" + b"\x00" * 100
    with pytest.raises(ImagemInvalida):
        recodificar(png_falso, "image/png")


def test_imagem_truncada_e_recusada():
    ruido = Image.effect_noise((300, 300), 90).convert("RGB")
    dados = salvar(ruido, "JPEG")
    with pytest.raises(ImagemInvalida):
        recodificar(dados[: len(dados) // 2], "image/jpeg")


def test_mime_diferente_do_real_e_recusado():
    with pytest.raises(ImagemInvalida):
        recodificar(png(), "image/jpeg")
    with pytest.raises(ImagemInvalida):
        recodificar(jpeg(), "image/webp")
    with pytest.raises(ImagemInvalida):
        recodificar(jpeg(), "image/gif")


def test_animacao_e_rejeitada():
    quadros = [Image.new("RGB", (30, 30), (i * 60, 0, 0)) for i in range(3)]
    animado_webp = salvar(quadros[0], "WEBP", save_all=True, append_images=quadros[1:])
    apng = salvar(quadros[0], "PNG", save_all=True, append_images=quadros[1:])
    for dados, mime in ((animado_webp, "image/webp"), (apng, "image/png")):
        with pytest.raises(ImagemInvalida) as ei:
            recodificar(dados, mime)
        assert ei.value.animada is True


def test_erro_nao_vaza_detalhe_do_pillow():
    with pytest.raises(ImagemInvalida) as ei:
        recodificar(b"conteudo-secreto-xyz", "image/png")
    assert str(ei.value) == "imagem inválida" and ei.value.__cause__ is None
    assert "secreto" not in repr(ei.value)


# ------------------------------------------------------------------ concorrência
def test_limite_de_recodificacoes_simultaneas(monkeypatch):
    monkeypatch.setattr(imagem_perfil, "ESPERA_VAGA_S", 0.05)
    tomadas = [imagem_perfil._VAGAS.acquire() for _ in range(imagem_perfil.SIMULTANEAS)]
    try:
        with pytest.raises(SistemaOcupado):
            recodificar(jpeg(), "image/jpeg")
    finally:
        for _ in tomadas:
            imagem_perfil._VAGAS.release()
    assert abrir(recodificar(jpeg(), "image/jpeg")).size == (256, 256)  # liberou


def test_recodificacao_em_varias_threads():
    resultados: list[int] = []
    dados = jpeg((800, 600))

    def trabalho() -> None:
        resultados.append(len(recodificar(dados, "image/jpeg")))

    ts = [threading.Thread(target=trabalho) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(resultados) == 6 and len(set(resultados)) == 1


# ------------------------------------------------------------------ outros formatos (formats=[...])
def _outros_formatos() -> dict[str, bytes]:
    base = Image.new("RGB", (20, 20), VERMELHO)
    return {
        "bmp": salvar(base, "BMP"),
        "tiff": salvar(base, "TIFF"),
        "ico": salvar(base, "ICO"),
        "psd_cabecalho": b"8BPS\x00\x01" + bytes(6) + b"\x00\x03" + bytes(100),
        "eps": b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n" + b"showpage\n" * 5,
    }


@pytest.mark.parametrize("nome", ["bmp", "tiff", "ico", "psd_cabecalho", "eps"])
@pytest.mark.parametrize("mime", ["image/jpeg", "image/png", "image/webp"])
def test_outro_formato_e_recusado_mesmo_declarado_como_aceito(nome, mime):
    with pytest.raises(ImagemInvalida):
        recodificar(_outros_formatos()[nome], mime)


@pytest.mark.parametrize("nome", ["bmp", "tiff", "ico", "psd_cabecalho", "eps"])
def test_polyglot_magic_de_jpeg_ou_png_com_conteudo_de_outro_formato(nome):
    corpo = _outros_formatos()[nome]
    for magic, mime in (
        (bytes([0xFF, 0xD8, 0xFF, 0xE0]), "image/jpeg"),
        (bytes([0x89]) + b"PNG\r\n\x1a\n", "image/png"),
    ):
        with pytest.raises(ImagemInvalida):
            recodificar(magic + corpo, mime)


def test_jpeg_com_dimensoes_absurdas_no_cabecalho_e_recusado():
    dados = bytearray(salvar(Image.new("L", (16, 16)), "JPEG"))
    i = dados.index(b"\xff\xc0")  # SOF0: marcador, tamanho(2), precisão(1), altura(2), largura(2)
    dados[i + 5 : i + 9] = (30000).to_bytes(2, "big") * 2
    with pytest.raises(ImagemInvalida):
        recodificar(bytes(dados), "image/jpeg")


def test_webp_lossless_12000x12000_de_poucos_bytes_e_recusado():
    dados = bytearray(salvar(Image.new("RGB", (16, 16)), "WEBP", lossless=True))
    i = dados.index(b"VP8L") + 8
    assert dados[i] == 0x2F  # assinatura do VP8L; depois, 14 bits de largura-1 e 14 de altura-1
    antigo = int.from_bytes(dados[i + 1 : i + 5], "little")
    novo = (antigo & 0xF0000000) | 11999 | (11999 << 14)
    dados[i + 1 : i + 5] = novo.to_bytes(4, "little")
    assert len(dados) < 200
    with pytest.raises(ImagemInvalida):
        recodificar(bytes(dados), "image/webp")
