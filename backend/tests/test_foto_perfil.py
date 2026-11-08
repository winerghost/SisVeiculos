"""Foto de perfil: upload em 3 passos, recodificação, troca, remoção (dono e admin), anti-IDOR,
exibição (foto_url), desativação, privacidade nos logs e erros do banco."""

from __future__ import annotations

import dataclasses
import io
import logging
import threading
from typing import Any

import pytest
from PIL import Image

from app import create_app
from app.auth.jwt import VerificadorJWT
from app.config import Config
from app.errors import ErroInterno
from app.extensions import limiter
from app.modelos import Perfil
from app.repositorios.armazenamento_avatares import (
    caminho_final,
    caminho_final_do_usuario,
    caminho_staging,
)
from app.services.foto_perfil import EMISSOES_FOTO
from tests.conftest import _CHAVE, AUDIENCE, ISSUER
from tests.fakes import RepoMemoria
from tests.fakes_admin import AuthAdminMemoria, erro_db
from tests.fakes_foto import novo_id
from tests.imagens import (
    jpeg,
    jpeg_com_exif_gps,
    png,
    salvar,
    segmentos_jpeg,
    webp,
)
from tests.test_requisicoes import acao, criar

MB = 1_048_576
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<html><script>alert(1)</script></html>"
GIF = salvar(Image.new("RGB", (5, 5)), "GIF")


def cod(r: Any) -> str:
    return str(r.get_json()["erro"]["codigo"])


def pedir_url(client, auth, perfil, corpo=None):  # type: ignore[no-untyped-def]
    corpo = {"mime": "image/jpeg", "tamanho": 1000} if corpo is None else corpo
    return client.post("/api/eu/foto/upload-url", headers=auth(perfil.id), json=corpo)


def confirmar(client, auth, perfil, foto_id):  # type: ignore[no-untyped-def]
    return client.post("/api/eu/foto", headers=auth(perfil.id), json={"id": foto_id})


def subir(  # type: ignore[no-untyped-def]
    client, auth, avatares, perfil, dados=None, mime="image/jpeg"
):
    """Passos 1 e 2: pede a URL e 'envia' o arquivo ao staging. Devolve o id."""
    r = pedir_url(client, auth, perfil)
    assert r.status_code == 201, r.get_json()
    foto_id = r.get_json()["id"]
    avatares.enviar(caminho_staging(perfil.id, foto_id), mime, jpeg() if dados is None else dados)
    return foto_id


def definir(client, auth, avatares, perfil, dados=None, mime="image/jpeg"):  # type: ignore[no-untyped-def]
    foto_id = subir(client, auth, avatares, perfil, dados, mime)
    return foto_id, confirmar(client, auth, perfil, foto_id)


def inativar(repo: RepoMemoria, perfil: Perfil) -> None:
    repo.perfis[perfil.id] = dataclasses.replace(perfil, ativo=False)


# ======================================================================= passo 1: upload-url
def test_upload_url_devolve_id_url_e_token_para_o_staging_do_proprio_usuario(  # type: ignore[no-untyped-def]
    client, auth, c, avatares
):
    r = pedir_url(client, auth, c.sol)
    assert r.status_code == 201
    j = r.get_json()
    assert set(j) == {"id", "url_upload", "token"} and j["token"] and j["url_upload"]
    assert avatares.urls_upload == [f"tmp/{c.sol.id}/{j['id']}"]


def test_upload_url_usuario_vem_so_do_jwt_nunca_do_corpo(client, auth, c, avatares):  # type: ignore[no-untyped-def]
    r = pedir_url(
        client, auth, c.sol, {"mime": "image/png", "tamanho": 5, "usuario_id": c.outro.id}
    )
    assert r.status_code == 400 and avatares.urls_upload == []


@pytest.mark.parametrize(
    "corpo",
    [
        None,
        [],
        "x",
        {},
        {"mime": "image/gif", "tamanho": 10},
        {"mime": "image/svg+xml", "tamanho": 10},
        {"mime": "image/png"},
        {"tamanho": 10},
        {"mime": "image/png", "tamanho": 0},
        {"mime": "image/png", "tamanho": -1},
        {"mime": "image/png", "tamanho": "10"},
        {"mime": "image/png", "tamanho": True},
        {"mime": "image/png", "tamanho": 1.5},
        {"mime": "image/png", "tamanho": 10, "extra": 1},
    ],
)
def test_upload_url_corpo_invalido_400(client, auth, c, avatares, corpo):  # type: ignore[no-untyped-def]
    r = client.post("/api/eu/foto/upload-url", headers=auth(c.sol.id), json=corpo)
    assert r.status_code == 400 and avatares.urls_upload == []


def test_upload_url_tamanho_acima_de_1mb_foto_grande(client, auth, c):  # type: ignore[no-untyped-def]
    ok = pedir_url(client, auth, c.sol, {"mime": "image/png", "tamanho": MB})
    assert ok.status_code == 201
    r = pedir_url(client, auth, c.sol, {"mime": "image/png", "tamanho": MB + 1})
    assert r.status_code == 400 and cod(r) == "foto_grande"


def test_upload_url_exige_login(client):  # type: ignore[no-untyped-def]
    r = client.post("/api/eu/foto/upload-url", json={"mime": "image/png", "tamanho": 5})
    assert r.status_code == 401


def test_qualquer_papel_ativo_pode_pedir(client, auth, c):  # type: ignore[no-untyped-def]
    for p in (c.sol, c.aut, c.adm):
        assert pedir_url(client, auth, p).status_code == 201


def test_conta_inativa_nao_envia(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    inativar(repo, c.sol)
    assert pedir_url(client, auth, c.sol).status_code == 403
    assert confirmar(client, auth, c.sol, novo_id()).status_code == 403
    assert client.delete("/api/eu/foto", headers=auth(c.sol.id)).status_code == 403
    assert avatares.urls_upload == [] and repo.chamadas_trocar == []


def _app_com(repo, avatares, **cfg):  # type: ignore[no-untyped-def]
    verificador = VerificadorJWT(
        issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
    )
    app = create_app(
        Config(jwt_issuer=ISSUER, jwt_audience=AUDIENCE, testing=True, **cfg),
        repo=repo,
        verificador=verificador,
        auth_admin=AuthAdminMemoria(repo),
        armazenamento_avatares=avatares,
    )
    limiter.reset()
    EMISSOES_FOTO.reiniciar()
    return app


def test_cota_de_emissao_por_usuario(repo, avatares, auth, c):  # type: ignore[no-untyped-def]
    cli = _app_com(repo, avatares, max_emissoes_foto_perfil=2).test_client()
    assert pedir_url(cli, auth, c.sol).status_code == 201
    assert pedir_url(cli, auth, c.sol).status_code == 201
    r = pedir_url(cli, auth, c.sol)
    assert r.status_code == 429 and cod(r) == "limite_fotos"
    assert len(avatares.urls_upload) == 2
    assert pedir_url(cli, auth, c.outro).status_code == 201  # a cota é por usuário


def test_falha_do_storage_devolve_a_cota(repo, avatares, auth, c):  # type: ignore[no-untyped-def]
    cli = _app_com(repo, avatares, max_emissoes_foto_perfil=1).test_client()
    avatares.indisponivel = True
    assert pedir_url(cli, auth, c.sol).status_code == 503
    avatares.indisponivel = False
    assert pedir_url(cli, auth, c.sol).status_code == 201  # a tentativa falha não gastou cota


def test_rate_limit_por_usuario_e_por_ip(repo, avatares, auth, c):  # type: ignore[no-untyped-def]
    cli = _app_com(repo, avatares, max_emissoes_foto_perfil=1000).test_client()
    codigos = [pedir_url(cli, auth, c.sol).status_code for _ in range(11)]
    assert codigos[:10] == [201] * 10 and codigos[10] == 429
    # outro usuário (outro IP lógico não é necessário): o limite por usuário não o atinge, mas o
    # limite por IP (30/min) ainda não estourou
    assert pedir_url(cli, auth, c.outro).status_code == 201


def test_cors_permite_delete(repo, avatares):  # type: ignore[no-untyped-def]
    cli = _app_com(repo, avatares, cors_origins=["https://app.exemplo.gov.br"]).test_client()
    r = cli.options(
        "/api/eu/foto",
        headers={
            "Origin": "https://app.exemplo.gov.br",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert r.status_code in {200, 204}
    assert "DELETE" in r.headers["Access-Control-Allow-Methods"]


# ======================================================================= passo 3: confirmar
def test_fluxo_feliz_recodifica_grava_troca_e_limpa(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    original = jpeg_com_exif_gps()
    foto_id, r = definir(client, auth, avatares, c.sol, original)
    assert r.status_code == 200, r.get_json()
    final = caminho_final(c.sol.id, foto_id, "image/jpeg")
    assert r.get_json() == {"foto_url": f"https://storage.exemplo/leitura/{final}?exp=120"}
    # RPC: usuário do JWT, mesmo id do upload, JPEG e tamanho do arquivo recodificado
    mime, gravado = avatares.objetos[final]
    assert repo.chamadas_trocar == [
        {"usuario_id": c.sol.id, "foto_id": foto_id, "mime": "image/jpeg", "tamanho": len(gravado)}
    ]
    assert mime == "image/jpeg" and 0 < len(gravado) < MB
    # o objeto gravado é a imagem RECODIFICADA: 256x256, sem EXIF/GPS/ICC
    img = Image.open(io.BytesIO(gravado))
    assert img.size == (256, 256) and img.mode == "RGB" and img.format == "JPEG"
    assert not dict(img.getexif()) and "icc_profile" not in img.info
    for lixo in (b"Exif", b"MarcaSecreta", b"ICCFAKE", b"GPS"):
        assert lixo not in gravado
    assert set(segmentos_jpeg(gravado)) <= {0xE0, 0xDB, 0xC0, 0xC4, 0xC2}
    # staging removido; só o objeto final sobra no bucket
    assert caminho_staging(c.sol.id, foto_id) in avatares.removidos
    assert list(avatares.objetos) == [final]
    assert repo.foto_ativa_de(c.sol.id)["caminho"] == final  # type: ignore[index]


def test_aceita_png_e_webp(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    _, r = definir(client, auth, avatares, c.sol, png(), "image/png")
    assert r.status_code == 200
    _, r = definir(client, auth, avatares, c.outro, webp(), "image/webp")
    assert r.status_code == 200
    assert {x["mime"] for x in repo.fotos} == {"image/jpeg"}  # sempre JPEG no banco


def test_corpo_do_confirmar(client, auth, c, avatares):  # type: ignore[no-untyped-def]
    for corpo in (None, {}, {"id": "nao-e-uuid"}, {"id": novo_id(), "usuario_id": c.outro.id}, []):
        r = client.post("/api/eu/foto", headers=auth(c.sol.id), json=corpo)
        assert r.status_code == 400, corpo


def test_troca_remove_objeto_anterior_e_marca_a_antiga(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    id1, r1 = definir(client, auth, avatares, c.sol)
    id2, r2 = definir(client, auth, avatares, c.sol)
    assert r1.status_code == r2.status_code == 200
    f1, f2 = (caminho_final(c.sol.id, i, "image/jpeg") for i in (id1, id2))
    assert f1 in avatares.removidos and f2 not in avatares.removidos
    assert list(avatares.objetos) == [f2]
    assert repo.foto_ativa_de(c.sol.id)["id"] == id2  # type: ignore[index]
    assert [e["acao"] for e in repo.eventos if e["tabela"] == "perfil_fotos"] == [
        "foto_perfil_alterada",
        "foto_perfil_removida",
        "foto_perfil_alterada",
    ]


def test_troca_nao_remove_caminho_estranho_devolvido_pelo_banco(  # type: ignore[no-untyped-def]
    client, auth, c, repo, avatares
):
    """Defesa em profundidade: só apaga caminho no formato final e do próprio usuário."""
    alheio = f"{c.outro.id}/{novo_id()}.jpg"
    avatares.enviar(alheio, "image/jpeg", b"x")
    repo.trocar_foto_perfil = lambda **_k: alheio  # type: ignore[method-assign]
    _, r = definir(client, auth, avatares, c.sol)
    assert r.status_code == 200 and alheio in avatares.objetos and alheio not in avatares.removidos


def test_staging_inexistente(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    r = confirmar(client, auth, c.sol, novo_id())
    assert r.status_code == 400 and cod(r) == "foto_nao_enviada"
    assert repo.chamadas_trocar == []


def test_id_de_outro_usuario_nao_registra_upload_alheio(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    id_da_vitima = subir(client, auth, avatares, c.outro)
    staging_vitima = caminho_staging(c.outro.id, id_da_vitima)
    r = confirmar(client, auth, c.sol, id_da_vitima)  # o atacante usa o id da vítima
    assert r.status_code == 400 and cod(r) == "foto_nao_enviada"
    assert repo.chamadas_trocar == [] and repo.foto_ativa_de(c.sol.id) is None
    assert staging_vitima in avatares.objetos and avatares.removidos == []  # nada foi tocado
    # a vítima ainda consegue concluir o seu próprio upload
    assert confirmar(client, auth, c.outro, id_da_vitima).status_code == 200


def test_um_usuario_so_age_sobre_a_propria_foto(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    definir(client, auth, avatares, c.outro)
    r = client.delete("/api/eu/foto", headers=auth(c.sol.id))  # só afeta a própria
    assert r.status_code == 200
    assert repo.foto_ativa_de(c.outro.id) is not None
    assert repo.chamadas_remover == [{"usuario_id": c.sol.id, "ator_id": c.sol.id}]
    # e não há rota para remover a de outra pessoa sem ser admin
    alvo = f"/api/admin/usuarios/{c.outro.id}/foto"
    assert client.delete(alvo, headers=auth(c.sol.id)).status_code == 403
    assert client.delete(alvo, headers=auth(c.aut.id)).status_code == 403
    assert repo.foto_ativa_de(c.outro.id) is not None


@pytest.mark.parametrize(
    ("meta", "esperado"),
    [
        ({"mime": "image/gif", "tamanho": 100}, "foto_divergente"),
        ({"mime": "", "tamanho": -1}, "foto_divergente"),
        ({"mime": "image/png", "tamanho": 0}, "foto_divergente"),
        ({"mime": "image/png", "tamanho": MB + 1}, "foto_grande"),
        ({"mime": "image/jpeg", "tamanho": 3}, "foto_divergente"),  # tamanho != bytes reais
    ],
)
def test_metadados_incoerentes_removem_o_staging(  # type: ignore[no-untyped-def]
    client, auth, c, repo, avatares, meta, esperado
):
    foto_id = subir(client, auth, avatares, c.sol)
    staging = caminho_staging(c.sol.id, foto_id)
    avatares.meta_forcada[staging] = meta
    r = confirmar(client, auth, c.sol, foto_id)
    assert r.status_code == 400 and cod(r) == esperado
    assert staging in avatares.removidos and repo.chamadas_trocar == []


def test_arquivo_acima_de_1mb_e_recusado_mesmo_com_metadado_mentiroso(  # type: ignore[no-untyped-def]
    client, auth, c, repo, avatares
):
    foto_id = subir(client, auth, avatares, c.sol, jpeg() + bytes(MB))
    staging = caminho_staging(c.sol.id, foto_id)
    avatares.meta_forcada[staging] = {"mime": "image/jpeg", "tamanho": 1000}
    r = confirmar(client, auth, c.sol, foto_id)
    assert r.status_code == 400 and cod(r) == "foto_grande"
    assert staging in avatares.removidos and repo.chamadas_trocar == []


def test_mime_declarado_diferente_do_real(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    # PNG de verdade enviado como image/jpeg: tipo errado (mas é imagem aceita) => divergente
    foto_id, r = definir(client, auth, avatares, c.sol, png(), "image/jpeg")
    assert r.status_code == 400 and cod(r) == "foto_divergente"
    assert caminho_staging(c.sol.id, foto_id) in avatares.removidos
    assert repo.chamadas_trocar == [] and avatares.gravados == []


@pytest.mark.parametrize("dados", [SVG, HTML, GIF, b"texto qualquer", bytes(500)])
@pytest.mark.parametrize("mime", ["image/jpeg", "image/png", "image/webp"])
def test_svg_gif_html_e_lixo_sao_recusados(client, auth, c, repo, avatares, dados, mime):  # type: ignore[no-untyped-def]
    foto_id, r = definir(client, auth, avatares, c.sol, dados, mime)
    assert r.status_code == 400 and cod(r) == "foto_invalida"
    assert caminho_staging(c.sol.id, foto_id) in avatares.removidos
    assert repo.chamadas_trocar == [] and avatares.gravados == []


def test_polyglot_com_cabecalho_jpeg_e_corpo_invalido(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    falso = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"<script>alert(1)</script>" * 30
    foto_id, r = definir(client, auth, avatares, c.sol, falso)
    assert r.status_code == 400 and cod(r) == "foto_invalida"
    assert caminho_staging(c.sol.id, foto_id) in avatares.removidos and avatares.gravados == []


def test_bomba_de_pixels_e_recusada(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    bomba = salvar(Image.new("1", (6000, 6000)), "PNG")  # 36 Mpx em poucos KB
    foto_id, r = definir(client, auth, avatares, c.sol, bomba, "image/png")
    assert r.status_code == 400 and cod(r) == "foto_invalida"
    assert caminho_staging(c.sol.id, foto_id) in avatares.removidos and avatares.gravados == []


def test_animacao_e_recusada_com_mensagem_propria(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    quadros = [Image.new("RGB", (30, 30), (i * 60, 0, 0)) for i in range(3)]
    animada = salvar(quadros[0], "WEBP", save_all=True, append_images=quadros[1:])
    foto_id, r = definir(client, auth, avatares, c.sol, animada, "image/webp")
    assert r.status_code == 400 and cod(r) == "foto_invalida"
    assert "animada" in r.get_json()["erro"]["mensagem"]
    assert caminho_staging(c.sol.id, foto_id) in avatares.removidos and avatares.gravados == []


def test_mensagens_de_erro_sao_fixas_sem_detalhe_interno(client, auth, c, avatares):  # type: ignore[no-untyped-def]
    segredo = b"SEGREDO-DO-ARQUIVO-123"
    _, r = definir(client, auth, avatares, c.sol, segredo + bytes(50))
    texto = r.get_data(as_text=True)
    assert "SEGREDO" not in texto and "PIL" not in texto and "Traceback" not in texto
    assert "tmp/" not in texto and c.sol.id not in texto


def test_staging_so_vale_uma_vez(client, auth, c, avatares):  # type: ignore[no-untyped-def]
    foto_id, r1 = definir(client, auth, avatares, c.sol)
    assert r1.status_code == 200
    r2 = confirmar(client, auth, c.sol, foto_id)  # reenvio do mesmo id
    assert r2.status_code == 400 and cod(r2) == "foto_nao_enviada"


def test_storage_indisponivel_no_confirmar_nao_perde_o_staging(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    foto_id = subir(client, auth, avatares, c.sol)
    avatares.indisponivel = True
    r = confirmar(client, auth, c.sol, foto_id)
    assert r.status_code == 503
    avatares.indisponivel = False
    assert confirmar(client, auth, c.sol, foto_id).status_code == 200  # pode tentar de novo
    assert len(repo.chamadas_trocar) == 1


# ------------------------------------------------------------------ falhas da RPC
@pytest.mark.parametrize(
    ("falha", "status", "codigo"),
    [
        (erro_db("AG017", "Usuário inexistente ou inativo."), 403, "conta_inativa"),
        (erro_db("23514", "ck_perfil_fotos_tamanho SEGREDO"), 400, "foto_invalida"),
        (erro_db("23505", "perfil_fotos_pkey Key (id)=(x) SEGREDO"), 409, "foto_ja_registrada"),
    ],
)
def test_falha_definitiva_da_rpc_remove_final_e_staging(  # type: ignore[no-untyped-def]
    client, auth, c, repo, avatares, falha, status, codigo
):
    repo.falha_trocar = falha
    foto_id = subir(client, auth, avatares, c.sol)
    r = confirmar(client, auth, c.sol, foto_id)
    assert r.status_code == status and cod(r) == codigo
    assert "SEGREDO" not in r.get_data(as_text=True)
    final = caminho_final(c.sol.id, foto_id, "image/jpeg")
    assert final in avatares.removidos and caminho_staging(c.sol.id, foto_id) in avatares.removidos
    assert avatares.objetos == {}


@pytest.mark.parametrize(
    ("falha", "codigo"),
    [
        (erro_db("55P03", "lock timeout"), "ocupado"),
        (erro_db("40P01", "deadlock"), "ocupado"),
        (erro_db("40001", "serializacao"), "ocupado"),
        (erro_db("XX000", "SEGREDO interno"), "indisponivel"),
    ],
)
def test_falha_transitoria_da_rpc_remove_so_o_final_e_permite_repetir_sem_nova_cota(  # type: ignore[no-untyped-def]
    repo, avatares, auth, c, falha, codigo
):
    cli = _app_com(repo, avatares, max_emissoes_foto_perfil=1).test_client()
    repo.falha_trocar = falha
    foto_id = subir(cli, auth, avatares, c.sol)  # gasta a única emissão da cota
    r = confirmar(cli, auth, c.sol, foto_id)
    assert r.status_code == 503 and cod(r) == codigo
    final = caminho_final(c.sol.id, foto_id, "image/jpeg")
    staging = caminho_staging(c.sol.id, foto_id)
    assert avatares.removidos == [final] and list(avatares.objetos) == [staging]
    repo.falha_trocar = None  # o banco voltou: repete o MESMO id, sem pedir nova URL
    r2 = confirmar(cli, auth, c.sol, foto_id)
    assert r2.status_code == 200 and final in avatares.objetos
    assert staging not in avatares.objetos  # agora o staging é consumido
    assert pedir_url(cli, auth, c.sol).status_code == 429  # a cota segue gasta (1 emissão)


def test_503_ocupado_traz_retry_after(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    repo.falha_trocar = erro_db("55P03", "lock")
    foto_id = subir(client, auth, avatares, c.sol)
    r = confirmar(client, auth, c.sol, foto_id)
    # Nas rotas com rate limit o Flask-Limiter ajusta o valor para o reset da janela (<= 60 s).
    assert r.status_code == 503 and int(r.headers["Retry-After"]) >= 1


def test_vaga_de_recodificacao_ocupada_devolve_503_com_retry_after(  # type: ignore[no-untyped-def]
    client, auth, c, avatares, monkeypatch
):
    from app.services import imagem_perfil

    monkeypatch.setattr(imagem_perfil, "ESPERA_VAGA_S", 0.05)
    foto_id = subir(client, auth, avatares, c.sol)
    tomadas = [imagem_perfil._VAGAS.acquire() for _ in range(imagem_perfil.SIMULTANEAS)]
    try:
        r = confirmar(client, auth, c.sol, foto_id)
    finally:
        for _ in tomadas:
            imagem_perfil._VAGAS.release()
    assert r.status_code == 503 and cod(r) == "ocupado" and r.headers["Retry-After"]
    assert caminho_staging(c.sol.id, foto_id) in avatares.objetos  # nada foi perdido
    assert confirmar(client, auth, c.sol, foto_id).status_code == 200


def test_resposta_perdida_com_troca_confirmada_nao_apaga_o_objeto(  # type: ignore[no-untyped-def]
    client, auth, c, repo, avatares
):
    repo.perder_resposta_trocar = True  # o banco gravou, mas a resposta se perdeu
    foto_id = subir(client, auth, avatares, c.sol)
    r = confirmar(client, auth, c.sol, foto_id)
    final = caminho_final(c.sol.id, foto_id, "image/jpeg")
    assert r.status_code == 200 and final in avatares.objetos and final not in avatares.removidos


def test_resposta_perdida_sem_como_confirmar_nao_apaga_nada(  # type: ignore[no-untyped-def]
    client, auth, c, repo, avatares
):
    repo.falha_trocar = ErroInterno("Serviço de dados indisponível. Tente novamente.")
    repo.falha_leitura_fotos = RuntimeError("fora do ar")
    foto_id = subir(client, auth, avatares, c.sol)
    assert confirmar(client, auth, c.sol, foto_id).status_code == 503
    assert avatares.removidos == []  # na dúvida, órfão é melhor que foto apontando para o nada


def test_resposta_perdida_sem_gravar_remove_so_o_final(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    repo.falha_trocar = ErroInterno("Serviço de dados indisponível. Tente novamente.")
    foto_id = subir(client, auth, avatares, c.sol)
    assert confirmar(client, auth, c.sol, foto_id).status_code == 503
    final = caminho_final(c.sol.id, foto_id, "image/jpeg")
    assert avatares.removidos == [final] and list(avatares.objetos) == [
        caminho_staging(c.sol.id, foto_id)
    ]


def test_falha_ao_remover_anterior_nao_falha_a_troca(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    definir(client, auth, avatares, c.sol)
    avatares.falha_remover = True
    _, r = definir(client, auth, avatares, c.sol)
    assert r.status_code == 200


def test_falha_ao_assinar_a_leitura_nao_desfaz_a_troca(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    avatares.falha_assinar = True
    _, r = definir(client, auth, avatares, c.sol)
    assert r.status_code == 200 and r.get_json() == {"foto_url": None}
    assert repo.foto_ativa_de(c.sol.id) is not None


# ------------------------------------------------------------------ concorrência
def test_mesmo_id_confirmado_em_paralelo_so_um_vence_e_nada_do_vencedor_e_apagado(  # type: ignore[no-untyped-def]
    app, auth, c, repo, avatares
):
    foto_id = subir(app.test_client(), auth, avatares, c.sol)
    final = caminho_final(c.sol.id, foto_id, "image/jpeg")
    barreira = threading.Barrier(4)
    respostas: list[Any] = []

    def uma() -> None:
        cli = app.test_client()
        barreira.wait()
        respostas.append(confirmar(cli, auth, c.sol, foto_id))

    ts = [threading.Thread(target=uma) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    status = sorted(r.status_code for r in respostas)
    assert status.count(200) == 1 and all(s in {200, 400, 409} for s in status)
    assert len(repo.chamadas_trocar) == 1 and final in avatares.objetos
    assert final not in avatares.removidos
    assert repo.foto_ativa_de(c.sol.id)["caminho"] == final  # type: ignore[index]


def test_trocas_simultaneas_deixam_uma_foto_ativa_e_um_objeto(app, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    cli = app.test_client()
    ids = [subir(cli, auth, avatares, c.sol) for _ in range(5)]
    barreira = threading.Barrier(5)
    respostas: list[int] = []

    def uma(i: str) -> None:
        barreira.wait()
        respostas.append(confirmar(app.test_client(), auth, c.sol, i).status_code)

    ts = [threading.Thread(target=uma, args=(i,)) for i in ids]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert respostas == [200] * 5
    ativas = [f for f in repo.fotos if f["removida_em"] is None]
    assert len(ativas) == 1
    assert list(avatares.objetos) == [ativas[0]["caminho"]]  # sobra só o objeto da foto ativa


# ======================================================================= remoção
def test_dono_remove_a_propria_foto_e_e_idempotente(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    foto_id, _ = definir(client, auth, avatares, c.sol)
    final = caminho_final(c.sol.id, foto_id, "image/jpeg")
    r = client.delete("/api/eu/foto", headers=auth(c.sol.id))
    assert r.status_code == 200 and r.get_json() == {"foto_url": None}
    assert repo.chamadas_remover[-1] == {"usuario_id": c.sol.id, "ator_id": c.sol.id}
    assert final in avatares.removidos and repo.foto_ativa_de(c.sol.id) is None
    avatares.removidos.clear()
    r2 = client.delete("/api/eu/foto", headers=auth(c.sol.id))  # sem foto: idempotente
    assert r2.status_code == 200 and avatares.removidos == []
    removidas = [e for e in repo.eventos if e["acao"] == "foto_perfil_removida"]
    assert len(removidas) == 1 and removidas[0]["ator_id"] == c.sol.id
    assert removidas[0]["detalhes"]["por_admin"] is False


def test_remover_exige_login(client):  # type: ignore[no-untyped-def]
    assert client.delete("/api/eu/foto").status_code == 401


def test_falha_do_storage_ao_remover_nao_falha_a_remocao(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    definir(client, auth, avatares, c.sol)
    avatares.falha_remover = True
    assert client.delete("/api/eu/foto", headers=auth(c.sol.id)).status_code == 200
    assert repo.foto_ativa_de(c.sol.id) is None


def test_admin_remove_a_foto_de_outro_usuario_com_ator_do_jwt(  # type: ignore[no-untyped-def]
    client, auth, c, repo, avatares
):
    foto_id, _ = definir(client, auth, avatares, c.sol)
    final = caminho_final(c.sol.id, foto_id, "image/jpeg")
    r = client.delete(f"/api/admin/usuarios/{c.sol.id}/foto", headers=auth(c.adm.id))
    assert r.status_code == 200
    j = r.get_json()
    assert (
        j["id"] == c.sol.id
        and j["foto_url"] is None
        and "remover_foto" not in j["acoes_permitidas"]
    )
    assert repo.chamadas_remover == [{"usuario_id": c.sol.id, "ator_id": c.adm.id}]  # ator = JWT
    assert final in avatares.removidos and repo.foto_ativa_de(c.sol.id) is None
    removidas = [e for e in repo.eventos if e["acao"] == "foto_perfil_removida"]
    assert len(removidas) == 1  # uma só linha de auditoria (a do gatilho), sem duplicar
    assert removidas[0]["ator_id"] == c.adm.id and removidas[0]["detalhes"]["por_admin"] is True


def test_rota_admin_nao_aceita_ator_no_corpo_nem_na_query(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    definir(client, auth, avatares, c.sol)
    r = client.delete(
        f"/api/admin/usuarios/{c.sol.id}/foto?ator={c.outro.id}",
        headers=auth(c.adm.id),
        json={"ator_id": c.outro.id},
    )
    assert r.status_code == 200
    assert repo.chamadas_remover[-1]["ator_id"] == c.adm.id


def test_rota_admin_exige_admin(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    definir(client, auth, avatares, c.sol)
    url = f"/api/admin/usuarios/{c.sol.id}/foto"
    assert client.delete(url).status_code == 401
    for p in (c.sol, c.outro, c.aut):
        r = client.delete(url, headers=auth(p.id))
        assert r.status_code == 403 and cod(r) == "sem_permissao"
    assert repo.chamadas_remover == [] and repo.foto_ativa_de(c.sol.id) is not None


def test_rota_admin_usuario_inexistente_e_id_invalido(client, auth, c):  # type: ignore[no-untyped-def]
    assert (
        client.delete(f"/api/admin/usuarios/{novo_id()}/foto", headers=auth(c.adm.id)).status_code
        == 404
    )
    assert client.delete("/api/admin/usuarios/xx/foto", headers=auth(c.adm.id)).status_code == 400


def test_admin_pode_remover_foto_de_conta_inativa(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    definir(client, auth, avatares, c.sol)
    inativar(repo, c.sol)
    r = client.get(f"/api/admin/usuarios/{c.sol.id}", headers=auth(c.adm.id))
    assert "remover_foto" in r.get_json()["acoes_permitidas"] and r.get_json()["foto_url"] is None
    assert (
        client.delete(f"/api/admin/usuarios/{c.sol.id}/foto", headers=auth(c.adm.id)).status_code
        == 200
    )
    assert repo.foto_ativa_de(c.sol.id) is None


def test_banco_recusa_ator_nao_admin_e_a_api_traduz(c, repo):  # type: ignore[no-untyped-def]
    """Defesa do banco (AG022): mesmo que a rota falhe, a RPC não aceita ator comum."""
    from app.errors import SemPermissao

    repo.trocar_foto_perfil(usuario_id=c.sol.id, foto_id=novo_id(), mime="image/jpeg", tamanho=10)
    with pytest.raises(SemPermissao):
        repo.remover_foto_perfil_por(usuario_id=c.sol.id, ator_id=c.outro.id)
    assert repo.foto_ativa_de(c.sol.id) is not None


# ======================================================================= acoes_permitidas
def test_remover_foto_so_aparece_para_quem_tem_foto_ativa(client, auth, c, avatares):  # type: ignore[no-untyped-def]
    definir(client, auth, avatares, c.sol)
    lista = client.get("/api/admin/usuarios?limite=100", headers=auth(c.adm.id)).get_json()
    por_id = {i["id"]: i for i in lista["itens"]}
    assert "remover_foto" in por_id[c.sol.id]["acoes_permitidas"]
    for outro in (c.outro, c.aut, c.adm):
        assert "remover_foto" not in por_id[outro.id]["acoes_permitidas"]
    det = client.get(f"/api/admin/usuarios/{c.sol.id}", headers=auth(c.adm.id)).get_json()
    assert "remover_foto" in det["acoes_permitidas"]
    det2 = client.get(f"/api/admin/usuarios/{c.outro.id}", headers=auth(c.adm.id)).get_json()
    assert "remover_foto" not in det2["acoes_permitidas"] and det2["foto_url"] is None


# ======================================================================= desativar conta
def test_desativar_remove_a_foto_sem_falhar(client, auth, c, repo, avatares, auth_admin):  # type: ignore[no-untyped-def]
    foto_id, _ = definir(client, auth, avatares, c.sol)
    final = caminho_final(c.sol.id, foto_id, "image/jpeg")
    r = client.post(f"/api/admin/usuarios/{c.sol.id}/desativar", headers=auth(c.adm.id))
    assert r.status_code == 200
    j = r.get_json()
    assert j["ativo"] is False and j["foto_url"] is None
    assert "remover_foto" not in j["acoes_permitidas"]
    assert repo.chamadas_remover == [{"usuario_id": c.sol.id, "ator_id": c.adm.id}]
    assert repo.foto_ativa_de(c.sol.id) is None and final in avatares.removidos
    assert auth_admin.contas[c.sol.id]["banido"] is True


@pytest.mark.parametrize(
    "falha", [ErroInterno("fora"), erro_db("55P03", "lock"), RuntimeError("boom-segredo")]
)
def test_falha_ao_remover_a_foto_nao_falha_a_desativacao(  # type: ignore[no-untyped-def]
    client, auth, c, repo, avatares, auth_admin, falha, caplog
):
    definir(client, auth, avatares, c.sol)
    repo.falha_remover_foto = falha
    with caplog.at_level(logging.DEBUG):
        r = client.post(f"/api/admin/usuarios/{c.sol.id}/desativar", headers=auth(c.adm.id))
    assert r.status_code == 200 and r.get_json()["ativo"] is False
    assert auth_admin.contas[c.sol.id]["banido"] is True
    assert "boom-segredo" not in caplog.text
    # a foto ficou, mas não é servida, e o admin ainda pode removê-la
    assert r.get_json()["foto_url"] is None and "remover_foto" in r.get_json()["acoes_permitidas"]


def test_desativar_conta_sem_foto_nao_audita_nada(client, auth, c, repo):  # type: ignore[no-untyped-def]
    r = client.post(f"/api/admin/usuarios/{c.outro.id}/desativar", headers=auth(c.adm.id))
    assert r.status_code == 200
    assert not [e for e in repo.eventos if e["acao"] == "foto_perfil_removida"]


# ======================================================================= exibição (foto_url)
def test_eu_traz_foto_url_propria_ou_null(client, auth, c, avatares):  # type: ignore[no-untyped-def]
    assert client.get("/api/eu", headers=auth(c.sol.id)).get_json()["foto_url"] is None
    foto_id, _ = definir(client, auth, avatares, c.sol)
    j = client.get("/api/eu", headers=auth(c.sol.id)).get_json()
    assert j["foto_url"] == f"https://storage.exemplo/leitura/{c.sol.id}/{foto_id}.jpg?exp=120"
    # a de outro usuário não aparece
    assert client.get("/api/eu", headers=auth(c.outro.id)).get_json()["foto_url"] is None


def _com_fotos(client, auth, c, avatares):  # type: ignore[no-untyped-def]
    for p in (c.sol, c.outro, c.aut, c.adm):
        definir(client, auth, avatares, p)
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    acao(client, auth, c.aut, rid, "autorizar")
    return rid


def test_requisicao_traz_fotos_de_quem_aparece_nela(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    rid = _com_fotos(client, auth, c, avatares)
    avatares.lotes_leitura.clear()
    j = client.get(f"/api/requisicoes/{rid}", headers=auth(c.sol.id)).get_json()
    assert str(c.sol.id) in j["solicitante_foto_url"]
    assert str(c.aut.id) in j["autorizado_por_foto_url"]
    assert (
        j["saida_registrada_por_foto_url"] is None and j["chegada_registrada_por_foto_url"] is None
    )
    # só foram assinadas as fotos de quem aparece (solicitante e autorizador), nunca a de `outro`
    assinadas = {x for lote in avatares.lotes_leitura for x in lote}
    esperadas = {repo.foto_ativa_de(p.id)["caminho"] for p in (c.sol, c.aut)}  # type: ignore[index]
    assert assinadas == esperadas
    assert all(
        u.startswith("https://storage.exemplo/leitura/") for u in (j["solicitante_foto_url"],)
    )


def test_listagem_do_solicitante_so_assina_fotos_do_que_ele_ve(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    _com_fotos(client, auth, c, avatares)
    avatares.lotes_leitura.clear()
    j = client.get("/api/requisicoes", headers=auth(c.outro.id)).get_json()  # não tem requisição
    assert j["itens"] == [] and avatares.lotes_leitura == []  # não vira diretório de usuários
    j = client.get("/api/requisicoes", headers=auth(c.sol.id)).get_json()
    assert len(j["itens"]) == 1 and j["itens"][0]["solicitante_foto_url"]
    assinadas = {x for lote in avatares.lotes_leitura for x in lote}
    assert repo.foto_ativa_de(c.outro.id)["caminho"] not in assinadas  # type: ignore[index]


def test_requisicao_alheia_continua_404_sem_foto(client, auth, c, avatares):  # type: ignore[no-untyped-def]
    rid = _com_fotos(client, auth, c, avatares)
    r = client.get(f"/api/requisicoes/{rid}", headers=auth(c.outro.id))
    assert r.status_code == 404 and "foto" not in r.get_data(as_text=True)


def test_fotos_nas_acoes_e_na_lista_do_autorizador(client, auth, c, avatares):  # type: ignore[no-untyped-def]
    rid = _com_fotos(client, auth, c, avatares)
    r = acao(client, auth, c.aut, rid, "saida")
    assert r.status_code == 200
    j = r.get_json()
    assert j["saida_registrada_por_foto_url"] and j["autorizado_por_foto_url"]
    itens = client.get("/api/requisicoes", headers=auth(c.aut.id)).get_json()["itens"]
    assert itens[0]["solicitante_foto_url"]


def test_usuario_inativo_nunca_serve_foto(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    rid = _com_fotos(client, auth, c, avatares)
    inativar(repo, c.aut)  # foto ainda ativa no banco, conta inativa
    j = client.get(f"/api/requisicoes/{rid}", headers=auth(c.sol.id)).get_json()
    assert j["autorizado_por_foto_url"] is None and j["solicitante_foto_url"]
    inativar(repo, c.sol)
    # lista do admin: inativo sem URL, mas com a ação de remover
    item = client.get(f"/api/admin/usuarios/{c.sol.id}", headers=auth(c.adm.id)).get_json()
    assert item["foto_url"] is None and "remover_foto" in item["acoes_permitidas"]


def test_sem_foto_e_null(client, auth, c):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    j = client.get(f"/api/requisicoes/{rid}", headers=auth(c.sol.id)).get_json()
    for campo in (
        "solicitante_foto_url",
        "autorizado_por_foto_url",
        "saida_registrada_por_foto_url",
        "chegada_registrada_por_foto_url",
    ):
        assert campo in j and j[campo] is None


def test_lista_de_usuarios_do_admin_traz_foto_url_em_lote(client, auth, c, avatares):  # type: ignore[no-untyped-def]
    for p in (c.sol, c.outro):
        definir(client, auth, avatares, p)
    avatares.lotes_leitura.clear()
    itens = client.get("/api/admin/usuarios?limite=100", headers=auth(c.adm.id)).get_json()["itens"]
    por_id = {i["id"]: i for i in itens}
    assert por_id[c.sol.id]["foto_url"] and por_id[c.outro.id]["foto_url"]
    assert por_id[c.aut.id]["foto_url"] is None
    assert len(avatares.lotes_leitura) == 1  # uma única assinatura em lote para a página


@pytest.mark.parametrize("quebra", ["storage", "assinatura", "banco"])
def test_falha_de_foto_nunca_derruba_listagem_nem_eu(  # type: ignore[no-untyped-def]
    client, auth, c, repo, avatares, quebra, caplog
):
    rid = _com_fotos(client, auth, c, avatares)
    if quebra == "storage":
        avatares.indisponivel = True
    elif quebra == "assinatura":
        avatares.falha_assinar = True
    else:
        repo.falha_leitura_fotos = RuntimeError("banco-segredo-xyz")
    with caplog.at_level(logging.DEBUG):
        r1 = client.get("/api/requisicoes", headers=auth(c.sol.id))
        r2 = client.get(f"/api/requisicoes/{rid}", headers=auth(c.aut.id))
        r3 = client.get("/api/eu", headers=auth(c.sol.id))
        r4 = client.get("/api/admin/usuarios", headers=auth(c.adm.id))
    assert (r1.status_code, r2.status_code, r3.status_code, r4.status_code) == (200, 200, 200, 200)
    assert r1.get_json()["itens"][0]["solicitante_foto_url"] is None
    assert r3.get_json()["foto_url"] is None
    assert "banco-segredo-xyz" not in caplog.text


# ======================================================================= privacidade
def test_nada_sensivel_vai_para_o_log(client, auth, c, repo, avatares, caplog):  # type: ignore[no-untyped-def]
    avatares.falha_remover = True  # força o ramo de falha de remoção (loga só o tipo)
    with caplog.at_level(logging.DEBUG):
        id1, r1 = definir(client, auth, avatares, c.sol, jpeg_com_exif_gps())
        id2, r2 = definir(client, auth, avatares, c.sol, png(), "image/png")
        definir(client, auth, avatares, c.outro, SVG)  # recusada
        client.get("/api/eu", headers=auth(c.sol.id))
        client.delete("/api/eu/foto", headers=auth(c.sol.id))
        client.delete(f"/api/admin/usuarios/{c.sol.id}/foto", headers=auth(c.adm.id))
    assert r1.status_code == r2.status_code == 200
    texto = caplog.text
    for proibido in (
        "storage.exemplo",
        "tmp/",
        ".jpg",
        id1,
        id2,
        "token=",
        "exp=",
        "falha segredo",
        "MarcaSecreta",
        "alert(1)",
        "JFIF",
    ):
        assert proibido not in texto, proibido


def test_payload_do_n8n_nunca_leva_foto():
    from app.notificacoes.n8n import montar_payload

    req = {
        "id": novo_id(),
        "status": "autorizada",
        "usuario_id": novo_id(),
        "solicitante_foto_url": "https://storage.exemplo/leitura/a?x",
        "autorizado_por_foto_url": "https://storage.exemplo/leitura/b?x",
        "foto_url": "https://storage.exemplo/leitura/c?x",
    }
    texto = str(montar_payload("requisicao.autorizada", req))
    assert "foto" not in texto and "storage.exemplo" not in texto


def test_respostas_de_foto_nao_sao_cacheaveis(client, auth, c, avatares):  # type: ignore[no-untyped-def]
    r = pedir_url(client, auth, c.sol)
    assert r.headers["Cache-Control"] == "no-store"


# ======================================================================= caminhos
def test_caminhos_espelham_o_banco_e_validam_o_dono():
    u, i = novo_id(), novo_id()
    assert caminho_staging(u, i) == f"tmp/{u}/{i}"
    assert caminho_final(u, i, "image/jpeg") == f"{u}/{i}.jpg"
    assert caminho_final(u, i, "image/png") == f"{u}/{i}.png"
    assert caminho_final(u, i, "image/webp") == f"{u}/{i}.webp"
    assert caminho_final_do_usuario(u, f"{u}/{i}.jpg")
    outro = novo_id()
    for ruim in (
        f"{outro}/{i}.jpg",
        f"tmp/{u}/{i}",
        f"{u}/../{outro}/{i}.jpg",
        f"{u}/{i}.svg",
        f"{u}/{i}.jpg/x",
        "",
        None,
    ):
        assert not caminho_final_do_usuario(u, ruim)
