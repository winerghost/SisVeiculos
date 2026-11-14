"""Leitor de QR: POST /api/veiculos/por-codigo {"codigo"} e rotação do código (admin)."""

from __future__ import annotations

import logging

import pytest

from app.modelos import Configuracoes
from tests.fakes_qr import instalar_qr
from tests.horarios import hora_saida_futura

CODIGO = "abcd1234efgh5678ijkl"
OUTRO = "nnnn0000mmmm1111kkkk"
URL = "/api/veiculos/por-codigo"


def _cod(r) -> str:  # type: ignore[no-untyped-def]
    return r.get_json()["erro"]["codigo"]


@pytest.fixture
def qr(repo):  # type: ignore[no-untyped-def]
    instalar_qr(repo)
    repo.config = Configuracoes(leitor_qr_veiculos=True)
    vid = repo.novo_veiculo()
    repo.veiculos[vid].update(placa="ABC1D23", codigo_publico=CODIGO)
    return vid


def test_codigo_valido_devolve_contrato_da_lista(client, auth, repo, qr):  # type: ignore[no-untyped-def]
    sol = repo.novo_perfil("solicitante")
    r = client.post(URL, headers=auth(sol.id), json={"codigo": CODIGO})
    assert r.status_code == 200
    item = client.get("/api/veiculos", headers=auth(sol.id)).get_json()["itens"][0]
    assert r.get_json() == item  # mesmo contrato do item de GET /api/veiculos
    assert r.get_json()["acoes_permitidas"] == ["solicitar"] and r.get_json()["disponivel"] is True


def test_resposta_nunca_traz_codigo_publico(client, auth, repo, qr):  # type: ignore[no-untyped-def]
    for role in ("solicitante", "autorizador", "vigilante", "motorista", "admin"):
        p = repo.novo_perfil(role)
        r = client.post(URL, headers=auth(p.id), json={"codigo": CODIGO})
        assert r.status_code == 200, role
        assert "codigo_publico" not in r.get_json() and CODIGO not in r.get_data(as_text=True)


def test_veiculo_ocupado_sem_acoes(client, auth, repo, qr, c):  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/requisicoes",
        headers=auth(c.sol.id),
        json={
            "nome_solicitante": "Fulano",
            "unidade": "PU",
            "hora_saida": hora_saida_futura(),
            "trajeto": "A -> B",
            "veiculo_id": qr,
        },
    )
    assert r.status_code == 201, r.get_json()
    j = client.post(URL, headers=auth(c.aut.id), json={"codigo": CODIGO}).get_json()
    assert j["disponivel"] is False and j["acoes_permitidas"] == []
    assert j["motivo_indisponibilidade"]


def test_sem_jwt(client, repo, qr):  # type: ignore[no-untyped-def]
    r = client.post(URL, json={"codigo": CODIGO})
    assert r.status_code == 401 and not repo.consultas_codigo


@pytest.mark.parametrize(
    "codigo",
    ["curto", "A" * 20, "abcd1234efgh5678ijk!", "a" * 33, "abcd1234efgh5678ijkl-", "%20" * 8],
)
def test_formato_invalido_404_uniforme_sem_consultar(client, auth, repo, qr, codigo):  # type: ignore[no-untyped-def]
    sol = repo.novo_perfil("solicitante")
    r = client.post(URL, headers=auth(sol.id), json={"codigo": codigo})
    inexistente = client.post(URL, headers=auth(sol.id), json={"codigo": OUTRO})
    assert r.status_code == 404 and _cod(r) == "nao_encontrado"
    assert r.get_json() == inexistente.get_json()  # idêntico ao inexistente/inativo
    assert repo.consultas_codigo == [OUTRO]  # o de formato inválido nem chegou ao banco


@pytest.mark.parametrize(
    "corpo",
    [None, [], "x", {}, {"codigo": CODIGO, "extra": 1}, {"outro": CODIGO}],
)
def test_corpo_estrito_400(client, auth, repo, qr, corpo):  # type: ignore[no-untyped-def]
    sol = repo.novo_perfil("solicitante")
    r = client.post(URL, headers=auth(sol.id), json=corpo)
    assert r.status_code == 400 and _cod(r) == "requisicao_invalida"
    assert repo.consultas_codigo == []


@pytest.mark.parametrize("valor", [None, 123, ["a"], {"a": 1}, True])
def test_codigo_nao_string_404(client, auth, repo, qr, valor):  # type: ignore[no-untyped-def]
    sol = repo.novo_perfil("solicitante")
    r = client.post(URL, headers=auth(sol.id), json={"codigo": valor})
    assert r.status_code == 404 and repo.consultas_codigo == []


def test_get_antigo_removido(client, auth, repo, qr):  # type: ignore[no-untyped-def]
    sol = repo.novo_perfil("solicitante")
    assert client.get(f"{URL}/{CODIGO}", headers=auth(sol.id)).status_code in (404, 405)
    assert client.get(URL, headers=auth(sol.id)).status_code == 405


def test_inexistente_e_inativo_dao_404_identico(client, auth, repo, qr):  # type: ignore[no-untyped-def]
    inativo = repo.novo_veiculo(ativo=False)
    repo.veiculos[inativo]["codigo_publico"] = "zzzz9999yyyy8888xxxx"
    sol = repo.novo_perfil("solicitante")
    a = client.post(URL, headers=auth(sol.id), json={"codigo": OUTRO})
    b = client.post(URL, headers=auth(sol.id), json={"codigo": "zzzz9999yyyy8888xxxx"})
    assert a.status_code == b.status_code == 404
    assert a.get_json() == b.get_json() and _cod(a) == "nao_encontrado"


def test_recurso_desligado_recusa(client, auth, repo, qr):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(leitor_qr_veiculos=False)
    sol = repo.novo_perfil("solicitante")
    r = client.post(URL, headers=auth(sol.id), json={"codigo": CODIGO})
    assert r.status_code == 404 and _cod(r) == "recurso_desligado"
    assert repo.consultas_codigo == []


def test_ler_qr_nao_concede_permissao(client, auth, repo, qr, c):  # type: ignore[no-untyped-def]
    # Nenhuma rota nova de ação: o motorista lê o QR, mas continua sem poder criar requisição.
    mot = repo.novo_perfil("motorista")
    assert client.post(URL, headers=auth(mot.id), json={"codigo": CODIGO}).status_code == 200
    r = client.post("/api/requisicoes", headers=auth(mot.id), json={"veiculo_id": qr})
    assert r.status_code == 403
    assert client.get(f"{URL}/{CODIGO}", headers=auth(c.aut.id)).status_code in (404, 405)


def test_rate_limit_por_usuario(client, auth, repo, qr):  # type: ignore[no-untyped-def]
    a, b = repo.novo_perfil("solicitante"), repo.novo_perfil("solicitante")
    codigos = [
        client.post(URL, headers=auth(a.id), json={"codigo": CODIGO}).status_code for _ in range(31)
    ]
    assert codigos[:30] == [200] * 30 and codigos[30] == 429
    assert (
        client.post(URL, headers=auth(b.id), json={"codigo": CODIGO}).status_code == 200
    )  # outro usuário


def test_codigo_nao_vai_para_logs(client, auth, repo, qr, caplog):  # type: ignore[no-untyped-def]
    sol = repo.novo_perfil("solicitante")
    with caplog.at_level(logging.DEBUG):
        client.post(URL, headers=auth(sol.id), json={"codigo": CODIGO})
        client.post(URL, headers=auth(sol.id), json={"codigo": OUTRO})
        client.post(URL, headers=auth(sol.id), json={"codigo": "INVALIDO"})
        repo.config = Configuracoes(leitor_qr_veiculos=False)
        client.post(URL, headers=auth(sol.id), json={"codigo": CODIGO})
    texto = caplog.text
    assert CODIGO not in texto and OUTRO not in texto and "INVALIDO" not in texto


# -- admin -----------------------------------------------------------------------------
def test_admin_ve_codigo_e_outros_perfis_nao_acessam(client, auth, adm, repo, qr):  # type: ignore[no-untyped-def]
    lista = client.get("/api/admin/veiculos", headers=auth(adm.id)).get_json()["itens"]
    assert lista[0]["codigo_publico"] == CODIGO
    unico = client.get(f"/api/admin/veiculos/{qr}", headers=auth(adm.id)).get_json()
    assert unico["codigo_publico"] == CODIGO
    for role in ("solicitante", "autorizador", "vigilante", "motorista"):
        p = repo.novo_perfil(role)
        assert client.get("/api/admin/veiculos", headers=auth(p.id)).status_code == 403


def test_rotacao_so_admin_audita_e_invalida_o_antigo(client, auth, adm, repo, qr):  # type: ignore[no-untyped-def]
    for role in ("solicitante", "autorizador", "vigilante", "motorista"):
        p = repo.novo_perfil(role)
        r = client.post(f"/api/admin/veiculos/{qr}/rotacionar-codigo", headers=auth(p.id))
        assert r.status_code == 403
    assert repo.veiculos[qr]["codigo_publico"] == CODIGO
    assert not repo.eventos_de("veiculo_codigo_rotacionado")
    r = client.post(f"/api/admin/veiculos/{qr}/rotacionar-codigo", headers=auth(adm.id))
    assert r.status_code == 200
    novo = r.get_json()["codigo_publico"]
    assert r.get_json()["id"] == qr and novo != CODIGO
    ev = repo.eventos_de("veiculo_codigo_rotacionado")
    assert len(ev) == 1 and ev[0]["ator_id"] == adm.id and ev[0]["registro_id"] == qr
    sol = repo.novo_perfil("solicitante")
    assert client.post(URL, headers=auth(sol.id), json={"codigo": CODIGO}).status_code == 404
    assert client.post(URL, headers=auth(sol.id), json={"codigo": novo}).status_code == 200


def test_rotacao_sem_jwt_id_invalido_e_inexistente(client, auth, adm, repo, qr):  # type: ignore[no-untyped-def]
    assert client.post(f"/api/admin/veiculos/{qr}/rotacionar-codigo").status_code == 401
    r = client.post("/api/admin/veiculos/nao-e-uuid/rotacionar-codigo", headers=auth(adm.id))
    assert r.status_code == 400
    r = client.post(
        "/api/admin/veiculos/00000000-0000-4000-8000-000000000000/rotacionar-codigo",
        headers=auth(adm.id),
    )
    assert r.status_code == 404


def test_rotacao_rate_limit(client, auth, adm, repo, qr):  # type: ignore[no-untyped-def]
    url = f"/api/admin/veiculos/{qr}/rotacionar-codigo"
    codigos = [client.post(url, headers=auth(adm.id)).status_code for _ in range(21)]
    assert codigos[:20] == [200] * 20 and codigos[20] == 429


def test_acoes_do_veiculo_incluem_rotacionar_codigo(client, auth, adm, repo, qr):  # type: ignore[no-untyped-def]
    url = f"/api/admin/veiculos/{qr}"
    assert client.get(url, headers=auth(adm.id)).get_json()["acoes_permitidas"] == [
        "editar",
        "desativar",
        "rotacionar_codigo",
    ]
    client.post(url + "/desativar", headers=auth(adm.id))
    assert client.get(url, headers=auth(adm.id)).get_json()["acoes_permitidas"] == [
        "editar",
        "ativar",
        "rotacionar_codigo",
    ]
