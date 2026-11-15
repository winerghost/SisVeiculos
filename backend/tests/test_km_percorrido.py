# ruff: noqa: F811
"""km_percorrido: calculado só no banco (trigger), exposto na API, no webhook e no relatório
apenas com a Quilometragem ligada; nunca aceito do cliente."""

from __future__ import annotations

from dataclasses import replace

from app.modelos import Configuracoes
from tests.test_notificacoes import cen, client_n, grav  # noqa: F401  (fixtures)
from tests.test_relatorios import BASE, app, get, mk, nova_req  # noqa: F401  (fixtures)
from tests.test_requisicoes import acao, criar

LIGADO = Configuracoes(quilometragem=True, autorizador_km=True)


def _post(client, auth, perfil, rid, nome, corpo):  # type: ignore[no-untyped-def]
    return client.post(f"/api/requisicoes/{rid}/{nome}", headers=auth(perfil.id), json=corpo)


def _viagem_com_km(client, auth, cen):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cen.sol, cen.veiculo).get_json()["id"]
    assert acao(client, auth, cen.aut, rid, "autorizar").status_code == 200
    r = _post(client, auth, cen.aut, rid, "saida", {"km_saida": 95000, "km_percorrido": 7})
    assert r.status_code == 200 and r.get_json()["km_percorrido"] is None  # cliente ignorado
    r = _post(client, auth, cen.aut, rid, "chegada", {"km_chegada": 95030, "km_percorrido": 1})
    assert r.status_code == 200
    return rid, r.get_json()


def test_api_devolve_km_percorrido_e_mantem_com_recurso_desligado(client_n, auth, cen, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid, j = _viagem_com_km(client_n, auth, cen)
    assert (j["km_saida"], j["km_chegada"], j["km_percorrido"]) == (95000, 95030, 30)
    assert repo.requisicoes[rid]["km_percorrido"] == 30
    dono = client_n.get(f"/api/requisicoes/{rid}", headers=auth(cen.sol.id)).get_json()
    assert dono["km_percorrido"] == 30

    repo.config = replace(LIGADO, quilometragem=False)
    for perfil in (cen.aut, cen.sol):
        j = client_n.get(f"/api/requisicoes/{rid}", headers=auth(perfil.id)).get_json()
        # Igual a km_saida/km_chegada: desligar não esconde o que já foi gravado.
        assert (j["km_saida"], j["km_chegada"], j["km_percorrido"]) == (95000, 95030, 30)
        itens = client_n.get("/api/requisicoes", headers=auth(perfil.id)).get_json()["itens"]
        assert [x["km_percorrido"] for x in itens if x["id"] == rid] == [30]
    assert repo.requisicoes[rid]["km_percorrido"] == 30  # desligar não apaga


def test_api_sem_km_devolve_km_percorrido_nulo(client_n, auth, cen):  # type: ignore[no-untyped-def]
    # Valor do cliente nunca é aceito: a criação recusa o campo (lista branca).
    assert criar(client_n, auth, cen.sol, cen.veiculo, km_percorrido=5).status_code == 400
    assert criar(client_n, auth, cen.sol, cen.veiculo).get_json()["km_percorrido"] is None


def test_webhook_de_chegada_leva_km_percorrido(client_n, auth, cen, grav, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    _viagem_com_km(client_n, auth, cen)
    d = dict(grav.detalhes)
    assert d["requisicao.chegada"]["km_percorrido"] == 30
    assert d["requisicao.chegada"]["requisicao"]["km_percorrido"] == 30
    assert d["requisicao.saida"]["km_percorrido"] is None


def test_webhook_sem_km_percorrido_com_recurso_desligado(client_n, auth, cen, grav, repo):  # type: ignore[no-untyped-def]
    rid = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]
    acao(client_n, auth, cen.aut, rid, "autorizar")
    acao(client_n, auth, cen.aut, rid, "saida")
    acao(client_n, auth, cen.aut, rid, "chegada")
    d = dict(grav.detalhes)["requisicao.chegada"]
    assert "km_percorrido" not in d and "km_percorrido" not in d["requisicao"]


def test_relatorio_movimentacao_inclui_km_percorrido(client, auth, c, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol, km_saida=95000, km_chegada=95030, km_percorrido=999)
    url = f"{BASE}/movimentacao/previa"
    assert get(client, auth, c.adm, url, colunas="km_percorrido").status_code == 400
    cat = get(client, auth, c.adm, BASE).get_json()["relatorios"]
    mov = next(r for r in cat if r["tipo"] == "movimentacao")
    assert "km_percorrido" not in {x["chave"] for x in mov["colunas_disponiveis"]}
    j = get(client, auth, c.adm, url).get_json()
    assert "km_percorrido" not in {x["chave"] for x in j["colunas"]}

    repo.config = Configuracoes(quilometragem=True)
    cat = get(client, auth, c.adm, BASE).get_json()["relatorios"]
    mov = next(r for r in cat if r["tipo"] == "movimentacao")
    disp = {x["chave"]: x["rotulo"] for x in mov["colunas_disponiveis"]}
    assert disp["km_percorrido"] == "Km percorrido"
    j = get(client, auth, c.adm, url, colunas="codigo,km_percorrido").get_json()
    assert [x["chave"] for x in j["colunas"]] == ["codigo", "km_percorrido"]
    assert j["linhas"][0]["km_percorrido"] == "30"  # do trigger, não do valor enviado
