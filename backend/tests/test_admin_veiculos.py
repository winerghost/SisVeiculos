"""Veículos (admin): criar, editar, ativar/desativar, placa única. Nunca apagar."""

from __future__ import annotations

import uuid

import pytest

from tests.horarios import hora_saida_futura


def _cod(r) -> str:  # type: ignore[no-untyped-def]
    return r.get_json()["erro"]["codigo"]


def criar(client, auth, adm, **extra):  # type: ignore[no-untyped-def]
    corpo = {"descricao": "Fiat Argo", "placa": "abc-1d23", **extra}
    return client.post("/api/admin/veiculos", headers=auth(adm.id), json=corpo)


def test_criar_normaliza_placa_audita_e_aparece_para_solicitar(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    r = criar(client, auth, adm)
    assert r.status_code == 201
    v = r.get_json()
    assert v["placa"] == "ABC1D23" and v["ativo"] is True and v["requisicao_aberta"] is False
    assert v["acoes_permitidas"] == ["editar", "desativar", "rotacionar_codigo"]
    ev = repo.eventos_de("veiculo_criado")
    assert len(ev) == 1 and ev[0]["ator_id"] == adm.id and ev[0]["registro_id"] == v["id"]
    sol = repo.novo_perfil("solicitante")
    lista = client.get("/api/veiculos", headers=auth(sol.id)).get_json()["itens"]
    assert [i["id"] for i in lista] == [v["id"]] and lista[0]["disponivel"] is True


@pytest.mark.parametrize("placa", ["ABC12", "1234567", "AB-12345", "ABCD123", "abc 1d2x", 12])
def test_placa_invalida(client, auth, adm, repo, placa):  # type: ignore[no-untyped-def]
    r = criar(client, auth, adm, placa=placa)
    assert r.status_code == 400 and _cod(r) == "requisicao_invalida"
    assert not repo.veiculos


def test_placa_duplicada(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    assert criar(client, auth, adm).status_code == 201
    r = criar(client, auth, adm, placa="ABC 1D23", descricao="Outro")  # mesma placa, outra grafia
    assert r.status_code == 409 and _cod(r) == "placa_duplicada"
    assert "placa" in r.get_json()["erro"]["mensagem"] and len(repo.veiculos) == 1


def test_sem_placa_pode_repetir(client, auth, adm):  # type: ignore[no-untyped-def]
    for _ in range(2):
        assert criar(client, auth, adm, placa=None).status_code == 201
        assert criar(client, auth, adm, placa="").status_code == 201


@pytest.mark.parametrize(
    "extra",
    [{"descricao": ""}, {"descricao": "x" * 121}, {"descricao": 7}, {"ativo": False}, {"id": "x"}],
)
def test_criar_validacao(client, auth, adm, extra):  # type: ignore[no-untyped-def]
    assert criar(client, auth, adm, **extra).status_code == 400


def test_editar_descricao_e_placa(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    vid = criar(client, auth, adm).get_json()["id"]
    outro = criar(client, auth, adm, placa="XYZ9876", descricao="Outro").get_json()["id"]
    h = auth(adm.id)
    r = client.patch(
        f"/api/admin/veiculos/{vid}", headers=h, json={"descricao": "Argo 2", "placa": "qwe1a23"}
    )
    assert r.status_code == 200 and r.get_json()["placa"] == "QWE1A23"
    ev = repo.eventos_de("veiculo_alterado")
    assert ev[-1]["detalhes"]["campos"] == ["descricao", "placa"] and ev[-1]["ator_id"] == adm.id
    r = client.patch(f"/api/admin/veiculos/{vid}", headers=h, json={"placa": "XYZ9876"})
    assert r.status_code == 409 and _cod(r) == "placa_duplicada"
    assert (
        client.patch(f"/api/admin/veiculos/{outro}", headers=h, json={"placa": None}).status_code
        == 200
    )
    n = len(repo.eventos_de("veiculo_alterado"))
    assert (
        client.patch(
            f"/api/admin/veiculos/{vid}", headers=h, json={"descricao": "Argo 2"}
        ).status_code
        == 200
    )
    assert len(repo.eventos_de("veiculo_alterado")) == n  # sem mudança, sem auditoria


@pytest.mark.parametrize("corpo", [{}, {"ativo": False}, {"descricao": ""}, {"placa": "!!"}])
def test_editar_validacao(client, auth, adm, corpo):  # type: ignore[no-untyped-def]
    vid = criar(client, auth, adm).get_json()["id"]
    assert (
        client.patch(f"/api/admin/veiculos/{vid}", headers=auth(adm.id), json=corpo).status_code
        == 400
    )


def test_desativar_ativar_sem_apagar(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    vid = criar(client, auth, adm).get_json()["id"]
    h = auth(adm.id)
    sol = repo.novo_perfil("solicitante")
    r = client.post(f"/api/admin/veiculos/{vid}/desativar", headers=h)
    assert r.status_code == 200 and r.get_json()["ativo"] is False
    assert r.get_json()["acoes_permitidas"] == ["editar", "ativar", "rotacionar_codigo"]
    ev = repo.eventos_de("veiculo_alterado")[-1]
    assert (ev["de"], ev["para"], ev["ator_id"]) == ("true", "false", adm.id)
    # inativo: some da lista de solicitação e não aceita requisição, mas continua na admin
    assert client.get("/api/veiculos", headers=auth(sol.id)).get_json()["itens"] == []
    corpo = {
        "nome_solicitante": "Fulano",
        "unidade": "PU",
        "hora_saida": hora_saida_futura(),
        "trajeto": "A -> B",
        "veiculo_id": vid,
    }
    r = client.post("/api/requisicoes", headers=auth(sol.id), json=corpo)
    assert r.status_code == 409 and _cod(r) == "veiculo_indisponivel"
    assert (
        client.get("/api/admin/veiculos?ativo=false", headers=h).get_json()["itens"][0]["id"] == vid
    )
    n = len(repo.eventos_de("veiculo_alterado"))
    assert (
        client.post(f"/api/admin/veiculos/{vid}/desativar", headers=h).status_code == 200
    )  # idempotente
    assert len(repo.eventos_de("veiculo_alterado")) == n
    r = client.post(f"/api/admin/veiculos/{vid}/ativar", headers=h)
    assert r.status_code == 200 and r.get_json()["ativo"] is True
    assert client.post("/api/requisicoes", headers=auth(sol.id), json=corpo).status_code == 201
    # nunca apagar: não existe DELETE
    assert client.delete(f"/api/admin/veiculos/{vid}", headers=h).status_code == 405
    assert vid in repo.veiculos


def test_listar_mostra_requisicao_aberta(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    vid = criar(client, auth, adm).get_json()["id"]
    sol = repo.novo_perfil("solicitante")
    client.post(
        "/api/requisicoes",
        headers=auth(sol.id),
        json={
            "nome_solicitante": "F",
            "unidade": "PU",
            "hora_saida": hora_saida_futura(),
            "trajeto": "A -> B",
            "veiculo_id": vid,
        },
    )
    itens = client.get("/api/admin/veiculos", headers=auth(adm.id)).get_json()["itens"]
    assert itens[0]["requisicao_aberta"] is True
    assert client.get("/api/admin/veiculos?ativo=x", headers=auth(adm.id)).status_code == 400


def test_veiculo_inexistente_e_id_invalido(client, auth, adm):  # type: ignore[no-untyped-def]
    h = auth(adm.id)
    assert client.get(f"/api/admin/veiculos/{uuid.uuid4()}", headers=h).status_code == 404
    assert (
        client.patch(
            f"/api/admin/veiculos/{uuid.uuid4()}", headers=h, json={"descricao": "x"}
        ).status_code
        == 404
    )
    assert client.post(f"/api/admin/veiculos/{uuid.uuid4()}/ativar", headers=h).status_code == 404
    assert client.get("/api/admin/veiculos/xyz", headers=h).status_code == 400
