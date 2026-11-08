"""Admin troca a foto de perfil de outra conta (upload-url + confirmação)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.errors import ErroInterno
from app.repositorios.armazenamento_avatares import caminho_final, caminho_staging
from tests.fakes_admin import erro_db
from tests.fakes_foto import novo_id
from tests.imagens import jpeg
from tests.test_foto_perfil import (
    GIF,
    MB,
    _app_com,
    cod,
    definir,
    inativar,
)
from tests.test_foto_perfil_infra import repo_mock

CORPO = {"mime": "image/jpeg", "tamanho": 1000}


def url_up(client, auth, admin, alvo_id, corpo=None):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/admin/usuarios/{alvo_id}/foto/upload-url",
        headers=auth(admin.id),
        json=CORPO if corpo is None else corpo,
    )


def confirmar(client, auth, admin, alvo_id, foto_id):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/admin/usuarios/{alvo_id}/foto", headers=auth(admin.id), json={"id": foto_id}
    )


def subir(client, auth, avatares, admin, alvo_id, dados=None, mime="image/jpeg"):  # type: ignore[no-untyped-def]
    r = url_up(client, auth, admin, alvo_id)
    assert r.status_code == 201, r.get_json()
    j = r.get_json()
    avatares.enviar(j["caminho"], mime, jpeg() if dados is None else dados)
    return j["id"]


def test_upload_url_devolve_caminho_de_staging_sob_o_admin(client, auth, c, avatares):  # type: ignore[no-untyped-def]
    r = url_up(client, auth, c.adm, c.sol.id)
    assert r.status_code == 201
    j = r.get_json()
    assert set(j) == {"id", "url_upload", "token", "caminho"}
    assert j["caminho"] == f"tmp/{c.adm.id}/{j['id']}"
    assert avatares.urls_upload == [j["caminho"]]  # nunca sob o id do alvo


def test_troca_com_sucesso(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    antiga_id, _ = definir(client, auth, avatares, c.sol)
    antiga = caminho_final(c.sol.id, antiga_id, "image/jpeg")
    foto_id = subir(client, auth, avatares, c.adm, c.sol.id)
    staging = caminho_staging(c.adm.id, foto_id)
    r = confirmar(client, auth, c.adm, c.sol.id, foto_id)
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    final = caminho_final(c.sol.id, foto_id, "image/jpeg")
    assert j["id"] == c.sol.id and final in j["foto_url"]
    assert "trocar_foto" in j["acoes_permitidas"] and "remover_foto" in j["acoes_permitidas"]
    assert repo.foto_ativa_de(c.sol.id)["caminho"] == final
    assert antiga in avatares.removidos and staging in avatares.removidos
    assert final in avatares.objetos and staging not in avatares.objetos
    # ator = admin do JWT; alvo = o da URL
    assert repo.chamadas_trocar_por == [
        {
            "usuario_id": c.sol.id,
            "ator_id": c.adm.id,
            "foto_id": foto_id,
            "mime": "image/jpeg",
            "tamanho": repo.chamadas_trocar_por[0]["tamanho"],
        }
    ]


def test_corpo_nao_define_ator_nem_alvo(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    foto_id = subir(client, auth, avatares, c.adm, c.sol.id)
    r = client.post(
        f"/api/admin/usuarios/{c.sol.id}/foto",
        headers=auth(c.adm.id),
        json={"id": foto_id, "ator_id": c.outro.id, "usuario_id": c.outro.id},
    )
    assert r.status_code == 400
    assert repo.chamadas_trocar_por == []
    ok = confirmar(client, auth, c.adm, c.sol.id, foto_id)
    assert ok.status_code == 200 and repo.chamadas_trocar_por[0]["usuario_id"] == c.sol.id


def test_admin_troca_a_propria_foto(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    foto_id = subir(client, auth, avatares, c.adm, c.adm.id)
    r = confirmar(client, auth, c.adm, c.adm.id, foto_id)
    assert r.status_code == 200 and r.get_json()["id"] == c.adm.id
    assert repo.foto_ativa_de(c.adm.id) is not None


def test_so_admin_e_com_token(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    for sufixo in ("foto/upload-url", "foto"):
        url = f"/api/admin/usuarios/{c.sol.id}/{sufixo}"
        assert client.post(url, json=CORPO).status_code == 401
        for p in (c.sol, c.outro, c.aut):
            r = client.post(url, headers=auth(p.id), json=CORPO)
            assert r.status_code == 403 and cod(r) == "sem_permissao"
    assert repo.chamadas_trocar_por == [] and avatares.urls_upload == []


def test_alvo_inexistente_404(client, auth, c, avatares):  # type: ignore[no-untyped-def]
    r = url_up(client, auth, c.adm, novo_id())
    assert r.status_code == 404 and cod(r) == "nao_encontrado"
    r = confirmar(client, auth, c.adm, novo_id(), novo_id())
    assert r.status_code == 404 and cod(r) == "nao_encontrado"
    assert avatares.urls_upload == []


def test_alvo_inativo_recusado(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    foto_id = subir(client, auth, avatares, c.adm, c.sol.id)
    inativar(repo, c.sol)
    for r in (
        url_up(client, auth, c.adm, c.sol.id),
        confirmar(client, auth, c.adm, c.sol.id, foto_id),
    ):
        assert r.status_code == 403 and cod(r) == "conta_inativa"
    assert repo.chamadas_trocar_por == []


def test_id_invalido_400(client, auth, c):  # type: ignore[no-untyped-def]
    assert url_up(client, auth, c.adm, "xx").status_code == 400
    assert confirmar(client, auth, c.adm, "xx", novo_id()).status_code == 400
    r = confirmar(client, auth, c.adm, c.sol.id, "nao-e-uuid")
    assert r.status_code == 400 and cod(r) == "requisicao_invalida"
    assert (
        url_up(client, auth, c.adm, c.sol.id, {"mime": "text/html", "tamanho": 5}).status_code
        == 400
    )


def test_staging_de_outro_usuario_ou_admin_nao_registra(client, auth, c, repo, avatares, adm):  # type: ignore[no-untyped-def]
    # staging do alvo (uso pelo próprio usuário) e de outro admin não valem
    alheio = novo_id()
    avatares.enviar(caminho_staging(c.sol.id, alheio), "image/jpeg", jpeg())
    r = confirmar(client, auth, c.adm, c.sol.id, alheio)
    assert r.status_code == 400 and cod(r) == "foto_nao_enviada"
    outro_foto = subir(client, auth, avatares, adm, c.sol.id)  # sob o outro admin
    r = confirmar(client, auth, c.adm, c.sol.id, outro_foto)
    assert r.status_code == 400 and cod(r) == "foto_nao_enviada"
    assert repo.chamadas_trocar_por == [] and repo.foto_ativa_de(c.sol.id) is None


def test_staging_do_proprio_usuario_nao_e_reaproveitado_pelo_admin(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    r = client.post("/api/eu/foto/upload-url", headers=auth(c.sol.id), json=CORPO)
    fid = r.get_json()["id"]
    avatares.enviar(caminho_staging(c.sol.id, fid), "image/jpeg", jpeg())
    assert confirmar(client, auth, c.adm, c.sol.id, fid).status_code == 400


@pytest.mark.parametrize(
    ("dados", "mime", "status", "codigo"),
    [
        (b"nao e imagem", "image/jpeg", 400, "foto_invalida"),
        (GIF, "image/jpeg", 400, "foto_invalida"),
        (b"\xff\xd8\xff" + b"0" * (MB + 10), "image/jpeg", 400, "foto_grande"),
    ],
    ids=["lixo", "gif", "grande"],
)
def test_imagem_invalida_ou_grande_usa_mensagens_atuais(  # type: ignore[no-untyped-def]
    client, auth, c, repo, avatares, dados, mime, status, codigo
):
    foto_id = subir(client, auth, avatares, c.adm, c.sol.id, dados, mime)
    r = confirmar(client, auth, c.adm, c.sol.id, foto_id)
    assert r.status_code == status and cod(r) == codigo
    assert repo.chamadas_trocar_por == []
    assert caminho_staging(c.adm.id, foto_id) in avatares.removidos


def test_cota_de_emissao_do_admin_e_separada_da_propria_foto(repo, avatares, auth, c):  # type: ignore[no-untyped-def]
    cli = _app_com(repo, avatares, max_emissoes_foto_perfil=2).test_client()
    assert url_up(cli, auth, c.adm, c.sol.id).status_code == 201
    assert url_up(cli, auth, c.adm, c.sol.id).status_code == 201
    r = url_up(cli, auth, c.adm, c.sol.id)
    assert r.status_code == 429 and cod(r) == "limite_fotos"
    r = cli.post("/api/eu/foto/upload-url", headers=auth(c.adm.id), json=CORPO)
    assert r.status_code == 201  # a cota da própria foto não foi gasta


def test_rate_limit_por_usuario(repo, avatares, auth, c):  # type: ignore[no-untyped-def]
    cli = _app_com(
        repo, avatares, max_emissoes_foto_perfil=1000, limite_foto_perfil="2 per minute"
    ).test_client()
    assert url_up(cli, auth, c.adm, c.sol.id).status_code == 201
    assert url_up(cli, auth, c.adm, c.sol.id).status_code == 201
    assert url_up(cli, auth, c.adm, c.sol.id).status_code == 429


def test_falha_definitiva_da_rpc_apaga_final_e_staging(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    foto_id = subir(client, auth, avatares, c.adm, c.sol.id)
    repo.falha_trocar = erro_db("23514", "Violação da regra ck_perfil_fotos_tamanho: x")
    r = confirmar(client, auth, c.adm, c.sol.id, foto_id)
    # o fake de _por delega a trocar_foto_perfil, que levanta falha_trocar
    assert r.status_code == 400
    assert caminho_staging(c.adm.id, foto_id) in avatares.removidos
    assert caminho_final(c.sol.id, foto_id, "image/jpeg") in avatares.removidos


def test_falha_transitoria_da_rpc_mantem_staging_e_permite_repetir(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    foto_id = subir(client, auth, avatares, c.adm, c.sol.id)
    staging = caminho_staging(c.adm.id, foto_id)
    repo.falha_trocar = ErroInterno("Serviço de dados indisponível. Tente novamente.")
    assert confirmar(client, auth, c.adm, c.sol.id, foto_id).status_code == 503
    assert staging in avatares.objetos and staging not in avatares.removidos
    repo.falha_trocar = None
    assert confirmar(client, auth, c.adm, c.sol.id, foto_id).status_code == 200
    assert staging not in avatares.objetos


def test_banco_recusa_ator_nao_admin(c, repo):  # type: ignore[no-untyped-def]
    from app.errors import SemPermissao

    with pytest.raises(SemPermissao):
        repo.trocar_foto_perfil_por(
            usuario_id=c.sol.id, ator_id=c.outro.id, foto_id=novo_id(), mime="image/jpeg", tamanho=9
        )
    assert repo.foto_ativa_de(c.sol.id) is None


def test_anterior_estranho_nao_e_apagado(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    foto_id = subir(client, auth, avatares, c.adm, c.sol.id)
    repo.trocar_foto_perfil_por = lambda **_k: f"{c.outro.id}/{novo_id()}.jpg"  # type: ignore[method-assign]
    assert confirmar(client, auth, c.adm, c.sol.id, foto_id).status_code == 200
    assert not any(r.startswith(f"{c.outro.id}/") for r in avatares.removidos)


def test_acoes_trocar_foto_so_para_conta_ativa(client, auth, c, repo, avatares):  # type: ignore[no-untyped-def]
    j = client.get(f"/api/admin/usuarios/{c.sol.id}", headers=auth(c.adm.id)).get_json()
    assert "trocar_foto" in j["acoes_permitidas"] and "remover_foto" not in j["acoes_permitidas"]
    inativar(repo, c.sol)
    j = client.get(f"/api/admin/usuarios/{c.sol.id}", headers=auth(c.adm.id)).get_json()
    assert "trocar_foto" not in j["acoes_permitidas"]
    lista = client.get("/api/admin/usuarios?limite=100", headers=auth(c.adm.id)).get_json()
    por_id = {i["id"]: i for i in lista["itens"]}
    assert "trocar_foto" in por_id[c.adm.id]["acoes_permitidas"]
    assert "trocar_foto" not in por_id[c.sol.id]["acoes_permitidas"]


def test_logs_sem_caminho_nem_url(client, auth, c, repo, avatares, caplog):  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    avatares.falha_remover = True  # força o caminho de aviso da limpeza
    definir(client, auth, avatares, c.sol)
    foto_id = subir(client, auth, avatares, c.adm, c.sol.id)
    r = confirmar(client, auth, c.adm, c.sol.id, foto_id)
    assert r.status_code == 200
    for trecho in (foto_id, c.sol.id, c.adm.id, "storage.exemplo", "segredo-do-storage", "tmp/"):
        assert trecho not in caplog.text


# ----------------------------------------------------------------- repositório real
def test_repo_real_envia_parametros_e_le_texto():
    r, cli = repo_mock()
    uid, aid, fid = novo_id(), novo_id(), novo_id()
    cli.rpc.return_value.execute.return_value = SimpleNamespace(data=f"{uid}/antiga.jpg")
    out = r.trocar_foto_perfil_por(
        usuario_id=uid, ator_id=aid, foto_id=fid, mime="image/jpeg", tamanho=900
    )
    assert out == f"{uid}/antiga.jpg"
    cli.rpc.assert_called_once_with(
        "trocar_foto_perfil_por",
        {"p_usuario": uid, "p_ator": aid, "p_foto": fid, "p_mime": "image/jpeg", "p_tamanho": 900},
    )


@pytest.mark.parametrize("data", [None, "", []])
def test_repo_real_sem_anterior(data):  # type: ignore[no-untyped-def]
    r, cli = repo_mock()
    cli.rpc.return_value.execute.return_value = SimpleNamespace(data=data)
    assert (
        r.trocar_foto_perfil_por(usuario_id="u", ator_id="a", foto_id="f", mime="m", tamanho=1)
        is None
    )


# ------------------------------------------- revisão M1: registrar_para se protege sozinho
def test_registrar_para_recusa_alvo_alheio_sem_via_admin(repo, avatares) -> None:  # type: ignore[no-untyped-def]
    from app.errors import SemPermissao
    from app.services.foto_perfil import ServicoFotoPerfil

    sol = repo.novo_perfil("solicitante")
    outro = repo.novo_perfil("solicitante")
    svc = ServicoFotoPerfil(repo, avatares)
    with pytest.raises(SemPermissao):
        svc.registrar_para(sol, outro.id, {"id": "00000000-0000-4000-8000-000000000000"})


def test_registrar_para_recusa_via_admin_com_ator_nao_admin(repo, avatares) -> None:  # type: ignore[no-untyped-def]
    from app.errors import SemPermissao
    from app.services.foto_perfil import ServicoFotoPerfil

    aut = repo.novo_perfil("autorizador")
    outro = repo.novo_perfil("solicitante")
    svc = ServicoFotoPerfil(repo, avatares)
    with pytest.raises(SemPermissao):
        svc.registrar_para(
            aut, outro.id, {"id": "00000000-0000-4000-8000-000000000000"}, via_admin=True
        )
