"""S-06/S-07: fotos do checklist recodificadas no servidor (sem EXIF/GPS) antes do registro."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.errors import SistemaOcupado
from app.modelos import Configuracoes
from app.services import imagem_perfil
from app.services.imagem_checklist import sanitizar
from app.services.imagem_perfil import ImagemInvalida
from tests.imagens import AZUL, VERMELHO, jpeg, jpeg_com_exif_gps, jpeg_orientacao_6, salvar
from tests.test_checklist import BASE, cod, foto, nova, post

LIGADO = Configuracoes(checklist=True)


def _aberta(dados: bytes) -> Image.Image:
    return Image.open(io.BytesIO(dados))


# --------------------------------------------------------------------- função pura
def test_jpeg_perde_exif_gps_e_icc():
    original = jpeg_com_exif_gps()
    assert b"MarcaSecreta" in original
    limpa = sanitizar(original, "image/jpeg")
    assert b"MarcaSecreta" not in limpa and b"ICCFAKE" not in limpa
    img = _aberta(limpa)
    assert img.format == "JPEG" and img.size == (300, 300)  # sem reduzir
    assert not img.getexif() and "icc_profile" not in img.info


def test_orientacao_exif_e_aplicada_aos_pixels():
    img = _aberta(sanitizar(jpeg_orientacao_6(), "image/jpeg"))
    assert img.size == (100, 200)  # 200x100 girado 90 graus
    assert not img.getexif().get(0x0112)


def test_png_e_webp_mantem_formato_e_descartam_texto_e_icc():
    base = Image.new("RGB", (40, 30), AZUL)
    base.info["icc_profile"] = b"ICCFAKE" * 10
    from PIL.PngImagePlugin import PngInfo

    meta = PngInfo()
    meta.add_text("Autor", "SegredoDoServidor")
    png_sujo = salvar(base, "PNG", pnginfo=meta)
    assert b"SegredoDoServidor" in png_sujo
    limpo = sanitizar(png_sujo, "image/png")
    assert b"SegredoDoServidor" not in limpo and _aberta(limpo).format == "PNG"
    exif = Image.Exif()
    exif[0x010F] = "MarcaSecreta"
    webp_sujo = salvar(base, "WEBP", exif=exif)
    assert b"MarcaSecreta" in webp_sujo
    limpo = sanitizar(webp_sujo, "image/webp")
    assert b"MarcaSecreta" not in limpo and _aberta(limpo).format == "WEBP"


def test_png_com_transparencia_continua_com_alfa():
    rgba = salvar(Image.new("RGBA", (10, 10), (1, 2, 3, 100)), "PNG")
    assert _aberta(sanitizar(rgba, "image/png")).mode == "RGBA"


def test_bytes_apos_o_fim_da_imagem_nao_sobrevivem():
    limpa = sanitizar(jpeg() + b"<script>alert(1)</script>", "image/jpeg")
    assert b"<script>" not in limpa


def test_pixels_acima_do_limite_sao_recusados(monkeypatch):
    monkeypatch.setattr(imagem_perfil, "MAX_PIXELS", 100)
    with pytest.raises(ImagemInvalida):
        sanitizar(jpeg((20, 20)), "image/jpeg")


@pytest.mark.parametrize("mime", ["image/gif", "image/svg+xml", "text/html", ""])
def test_mime_fora_da_lista_recusado(mime):
    with pytest.raises(ImagemInvalida):
        sanitizar(jpeg(), mime)


def test_conteudo_nao_bate_com_o_mime_ou_e_lixo():
    gif = salvar(Image.new("RGB", (5, 5), VERMELHO), "GIF")
    for dados, mime in ((gif, "image/jpeg"), (jpeg(), "image/png"), (b"lixo", "image/webp")):
        with pytest.raises(ImagemInvalida):
            sanitizar(dados, mime)


def test_webp_animado_recusado():
    quadros = [Image.new("RGB", (8, 8), c) for c in (VERMELHO, AZUL)]
    anim = salvar(quadros[0], "WEBP", save_all=True, append_images=quadros[1:], duration=50)
    with pytest.raises(ImagemInvalida) as e:
        sanitizar(anim, "image/webp")
    assert e.value.animada is True


def test_servidor_ocupado_da_503(monkeypatch):
    monkeypatch.setattr(imagem_perfil, "ESPERA_VAGA_S", 0.01)
    assert imagem_perfil._VAGAS.acquire(timeout=1)
    try:
        with pytest.raises(SistemaOcupado):
            sanitizar(jpeg(), "image/jpeg")
    finally:
        imagem_perfil._VAGAS.release()


# --------------------------------------------------------------------- no registro
def test_registro_regrava_foto_sanitizada_e_grava_o_novo_tamanho(  # type: ignore[no-untyped-def]
    client, auth, c, repo, storage
):
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    caminho = f"{rid}/saida/{f['id']}.jpg"
    sujo = jpeg_com_exif_gps()
    storage.enviar(caminho, "image/jpeg", f["tamanho"], dados=sujo)
    r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [f]})
    assert r.status_code == 201, r.get_json()
    assert storage.sobrescritos == [caminho]
    gravado = storage.conteudos[caminho]
    assert b"MarcaSecreta" not in gravado and not _aberta(gravado).getexif()
    registrada = repo.checklists[(rid, "saida")]["fotos"][0]
    assert registrada["tamanho_bytes"] == len(gravado) != f["tamanho"]


@pytest.mark.parametrize(
    "corrompido",
    [
        b"\xff\xd8\xff\xe0" + b"<html><script>x</script>" * 30,  # polyglot: cabeçalho JPEG
        jpeg()[:60],  # truncado
    ],
)
def test_foto_nao_decodificavel_e_recusada_e_removida(  # type: ignore[no-untyped-def]
    client, auth, c, repo, storage, corrompido
):
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    caminho = f"{rid}/saida/{f['id']}.jpg"
    storage.enviar(caminho, "image/jpeg", f["tamanho"], dados=corrompido)
    r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [f]})
    assert r.status_code == 400 and cod(r) == "foto_invalida"
    assert storage.removidos == [caminho] and storage.sobrescritos == []
    assert repo.chamadas_rpc == []


def test_foto_com_conteudo_de_outro_formato_e_recusada(  # type: ignore[no-untyped-def]
    client, auth, c, repo, storage
):
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    caminho = f"{rid}/saida/{f['id']}.jpg"
    # Passa pelos magic bytes de JPEG, mas o decodificador acusa que é GIF.
    gif = b"\xff\xd8\xff" + salvar(Image.new("RGB", (5, 5)), "GIF")
    storage.enviar(caminho, "image/jpeg", f["tamanho"], dados=gif)
    r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [f]})
    assert cod(r) == "foto_invalida" and storage.removidos == [caminho]


def test_foto_acima_do_limite_de_pixels_e_recusada(  # type: ignore[no-untyped-def]
    client, auth, c, repo, storage, monkeypatch
):
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    monkeypatch.setattr(imagem_perfil, "MAX_PIXELS", 50)
    r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [f]})
    assert cod(r) == "foto_invalida" and repo.chamadas_rpc == []


def test_servidor_ocupado_no_registro_da_503_sem_remover_a_foto(  # type: ignore[no-untyped-def]
    client, auth, c, repo, storage, monkeypatch
):
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    monkeypatch.setattr(imagem_perfil, "ESPERA_VAGA_S", 0.01)
    assert imagem_perfil._VAGAS.acquire(timeout=1)
    try:
        r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [f]})
    finally:
        imagem_perfil._VAGAS.release()
    assert r.status_code == 503 and storage.removidos == [] and repo.chamadas_rpc == []
