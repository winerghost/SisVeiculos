"""Contrato entregue ao frontend: unidades, rótulo, histórico, nomes e total."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.fakes import RepoMemoria
from tests.test_requisicoes import acao, criar


@pytest.fixture
def cenario(repo: RepoMemoria):  # type: ignore[no-untyped-def]
    class C:
        sol = repo.novo_perfil("solicitante")
        outro = repo.novo_perfil("solicitante")
        aut = repo.novo_perfil("autorizador")
        adm = repo.novo_perfil("admin")
        veiculo = repo.novo_veiculo()

    return C


def test_eu_traz_unidades_e_rotulo(client, auth, repo):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil("autorizador")
    j = client.get("/api/eu", headers=auth(p.id)).get_json()
    assert j["unidades"] == ["PU", "PF", "CJU", "UEA"]
    assert j["rotulo_perfil"] == "PU · Autorizador"


@pytest.mark.parametrize(
    ("role", "texto"),
    [("solicitante", "PU · Solicitante"), ("admin", "PU · Administrador")],
)
def test_rotulo_por_papel(client, auth, repo, role, texto):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil(role)
    assert client.get("/api/eu", headers=auth(p.id)).get_json()["rotulo_perfil"] == texto


def test_historico_ciclo_completo_e_nomes(client, auth, cenario):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cenario.sol, cenario.veiculo).get_json()["id"]
    j = client.get(f"/api/requisicoes/{rid}", headers=auth(cenario.adm.id)).get_json()
    assert [h["rotulo"] for h in j["historico"]] == ["Solicitada"]
    assert j["autorizado_por_nome"] is None
    assert j["veiculo_descricao"] == "Carro"
    for passo in ("autorizar", "saida", "chegada"):
        assert acao(client, auth, cenario.aut, rid, passo).status_code == 200
    j = client.get(f"/api/requisicoes/{rid}", headers=auth(cenario.adm.id)).get_json()
    assert [h["rotulo"] for h in j["historico"]] == [
        "Solicitada",
        "Autorizada",
        "Saída registrada",
        "Chegada registrada",
    ]
    assert all(h["quando"] for h in j["historico"])
    assert j["autorizado_por_nome"] == cenario.aut.nome
    assert j["saida_registrada_por_nome"] == cenario.aut.nome
    assert j["chegada_registrada_por_nome"] == cenario.aut.nome


def test_historico_negada_e_cancelada(client, auth, cenario, repo):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cenario.sol, cenario.veiculo).get_json()["id"]
    j = acao(client, auth, cenario.aut, rid, "negar").get_json()
    assert [h["rotulo"] for h in j["historico"]] == ["Solicitada", "Negada"]
    v2 = repo.novo_veiculo()
    rid2 = criar(client, auth, cenario.sol, v2).get_json()["id"]
    j = acao(client, auth, cenario.sol, rid2, "cancelar").get_json()
    assert [h["rotulo"] for h in j["historico"]] == ["Solicitada", "Cancelada"]


def test_listagem_devolve_total_com_limite(client, auth, cenario, repo):  # type: ignore[no-untyped-def]
    for _ in range(3):
        criar(client, auth, cenario.sol, repo.novo_veiculo())
    criar(client, auth, cenario.outro, repo.novo_veiculo())
    r = client.get("/api/requisicoes?limite=2", headers=auth(cenario.sol.id)).get_json()
    assert len(r["itens"]) == 2 and r["total"] == 3  # total só das próprias
    r = client.get("/api/requisicoes?limite=2", headers=auth(cenario.aut.id)).get_json()
    assert len(r["itens"]) == 2 and r["total"] == 4
    r = client.get("/api/requisicoes?status=autorizada", headers=auth(cenario.aut.id)).get_json()
    assert r == {"itens": [], "total": 0}


def test_nenhum_dado_pessoal_extra_para_solicitante(client, auth, cenario):  # type: ignore[no-untyped-def]
    criar(client, auth, cenario.sol, cenario.veiculo)
    rid = criar(client, auth, cenario.outro, cenario.veiculo)  # ocupado: não cria
    assert rid.status_code == 409
    lista: Any = client.get("/api/requisicoes", headers=auth(cenario.sol.id)).get_json()
    bruto = json.dumps(lista)
    assert cenario.outro.nome not in bruto and cenario.outro.id not in bruto
    assert "@" not in bruto and "email" not in bruto
    # solicitante não recebe nome, foto nem id de quem agiu (só a linha_do_tempo, com papel)
    assert not {k for k in lista["itens"][0] if k.endswith(("_nome", "_foto_url"))} - {
        "solicitante_foto_url"
    }


def test_solicitante_nao_ve_nome_de_requisicao_alheia(client, auth, cenario):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cenario.sol, cenario.veiculo).get_json()["id"]
    acao(client, auth, cenario.aut, rid, "autorizar")
    r = client.get(f"/api/requisicoes/{rid}", headers=auth(cenario.outro.id))
    assert r.status_code == 404 and cenario.aut.nome not in r.get_data(as_text=True)


CAMPOS_EQUIPE = (
    "autorizado_por",
    "saida_registrada_por",
    "chegada_registrada_por",
    "cancelada_por",
    "autorizado_por_nome",
    "saida_registrada_por_nome",
    "chegada_registrada_por_nome",
    "autorizado_por_foto_url",
    "saida_registrada_por_foto_url",
    "chegada_registrada_por_foto_url",
)


def test_solicitante_nao_recebe_dados_da_equipe_em_lista_e_detalhe(client, auth, cenario, repo):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cenario.sol, cenario.veiculo).get_json()["id"]
    for passo in ("autorizar", "saida", "chegada"):
        acao(client, auth, cenario.aut, rid, passo)
    v2 = repo.novo_veiculo()
    rid2 = criar(client, auth, cenario.sol, v2).get_json()["id"]
    acao(client, auth, cenario.adm, rid2, "cancelar")
    cab = auth(cenario.sol.id)
    lista = client.get("/api/requisicoes", headers=cab).get_json()["itens"]
    detalhe = client.get(f"/api/requisicoes/{rid}", headers=cab).get_json()
    for item in [*lista, detalhe]:
        assert not [k for k in CAMPOS_EQUIPE if k in item]
        assert all("por" not in h and {"rotulo", "quando"} <= set(h) for h in item["historico"])
        assert "solicitante_foto_url" in item
        texto = json.dumps(item)
        assert cenario.aut.id not in texto and cenario.aut.nome not in texto
        assert cenario.adm.id not in texto and cenario.adm.nome not in texto


def test_solicitante_nao_consulta_nomes_da_equipe(client, auth, cenario, repo, monkeypatch):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cenario.sol, cenario.veiculo).get_json()["id"]
    acao(client, auth, cenario.aut, rid, "autorizar")
    pedidos: list[set[str]] = []
    original = repo.nomes_perfis
    monkeypatch.setattr(
        repo, "nomes_perfis", lambda ids: (pedidos.append(set(ids)), original(ids))[1]
    )
    client.get(f"/api/requisicoes/{rid}", headers=auth(cenario.sol.id))
    assert pedidos == [{cenario.sol.id}]


def test_staff_continua_recebendo_dados_da_equipe(client, auth, cenario):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cenario.sol, cenario.veiculo).get_json()["id"]
    acao(client, auth, cenario.aut, rid, "autorizar")
    for quem in (cenario.aut, cenario.adm):
        j = client.get(f"/api/requisicoes/{rid}", headers=auth(quem.id)).get_json()
        assert j["autorizado_por"] == cenario.aut.id
        assert j["autorizado_por_nome"] == cenario.aut.nome and "autorizado_por_foto_url" in j
        assert all("por" in h for h in j["historico"])
