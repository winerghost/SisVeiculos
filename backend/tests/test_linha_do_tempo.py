"""Linha do tempo coerente com a máquina de estados, em todos os status e nas duas visões."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.requisicoes import montar_linha_do_tempo
from tests.test_requisicoes import acao, criar

T0 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
DONO, STAFF_A, STAFF_B = "dono", "staff-a", "staff-b"
NOMES = {DONO: "Dona Maria", STAFF_A: "Ana Autorizadora", STAFF_B: "Beto Admin"}


def iso(segundos: int) -> str:
    return (T0 + timedelta(seconds=segundos)).isoformat()


def req(status: str, **campos: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": status,
        "usuario_id": DONO,
        "criado_em": iso(0),
        "autorizado_por": None,
        "autorizado_em": None,
        "saida_real_em": None,
        "saida_registrada_por": None,
        "chegada_real_em": None,
        "chegada_registrada_por": None,
        "cancelada_em": None,
        "cancelada_por": None,
    }
    return {**base, **campos}


AUTORIZADA = {"autorizado_em": iso(300), "autorizado_por": STAFF_A}
EM_USO = {**AUTORIZADA, "saida_real_em": iso(900), "saida_registrada_por": STAFF_B}
CONCLUIDA = {**EM_USO, "chegada_real_em": iso(4500), "chegada_registrada_por": STAFF_B}
AGORA = T0 + timedelta(seconds=7200)


def linha(status: str, ocultar: bool = False, **campos: Any) -> dict[str, Any]:
    return montar_linha_do_tempo(req(status, **campos), NOMES, ocultar_nomes=ocultar, agora=AGORA)


def situacoes(lt: dict[str, Any]) -> list[tuple[str, str]]:
    return [(e["chave"], e["situacao"]) for e in lt["etapas"]]


def test_solicitada() -> None:
    lt = linha("solicitada")
    assert lt["desfecho"] == "em_andamento"
    assert (lt["etapa_atual"], lt["total_etapas"]) == (1, 4)
    assert lt["texto_equivalente"] == "Etapa 1 de 4: solicitada"
    assert situacoes(lt) == [
        ("solicitada", "atual"),
        ("autorizada", "pendente"),
        ("em_uso", "pendente"),
        ("concluida", "pendente"),
    ]
    atual, *futuras = lt["etapas"]
    assert atual["em"] == iso(0) and atual["decorrido_s"] == 7200
    assert atual["duracao_desde_anterior_s"] is None
    assert atual["por"] == {"nome": "Dona Maria"}
    for f in futuras:
        assert f["em"] is None and f["por"] is None
        assert f["duracao_desde_anterior_s"] is None and f["decorrido_s"] is None
    assert lt["gerado_em"] == AGORA.isoformat()


def test_autorizada() -> None:
    lt = linha("autorizada", **AUTORIZADA)
    assert (lt["etapa_atual"], lt["texto_equivalente"]) == (2, "Etapa 2 de 4: autorizada")
    assert situacoes(lt)[:3] == [
        ("solicitada", "concluida"),
        ("autorizada", "atual"),
        ("em_uso", "pendente"),
    ]
    sol, aut = lt["etapas"][:2]
    assert sol["decorrido_s"] is None  # só a etapa atual tem decorrido
    assert aut["duracao_desde_anterior_s"] == 300
    assert aut["decorrido_s"] == 7200 - 300
    assert aut["por"] == {"nome": "Ana Autorizadora"}


def test_em_uso() -> None:
    lt = linha("em_uso", **EM_USO)
    assert (lt["etapa_atual"], lt["texto_equivalente"]) == (3, "Etapa 3 de 4: em uso")
    assert [e["situacao"] for e in lt["etapas"]] == ["concluida", "concluida", "atual", "pendente"]
    uso = lt["etapas"][2]
    assert uso["duracao_desde_anterior_s"] == 600 and uso["decorrido_s"] == 7200 - 900


def test_concluida_sem_etapa_atual() -> None:
    lt = linha("concluida", **CONCLUIDA)
    assert lt["desfecho"] == "concluida"
    assert (lt["etapa_atual"], lt["total_etapas"]) == (4, 4)
    assert lt["texto_equivalente"] == "Etapa 4 de 4: concluída"
    assert {e["situacao"] for e in lt["etapas"]} == {"concluida"}
    assert all(e["decorrido_s"] is None for e in lt["etapas"])
    assert [e["duracao_desde_anterior_s"] for e in lt["etapas"]] == [None, 300, 600, 3600]
    assert [e["chave"] for e in lt["etapas"]] == ["solicitada", "autorizada", "em_uso", "concluida"]


def test_negada_encerra_a_linha() -> None:
    lt = linha("negada", **AUTORIZADA)
    assert lt["desfecho"] == "negada"
    assert situacoes(lt) == [("solicitada", "concluida"), ("negada", "negada")]
    assert (lt["etapa_atual"], lt["total_etapas"]) == (2, 2)
    assert lt["texto_equivalente"] == "Etapa 2 de 2: negada"
    negada = lt["etapas"][1]
    assert negada["em"] == iso(300) and negada["duracao_desde_anterior_s"] == 300
    assert negada["por"] == {"nome": "Ana Autorizadora"} and negada["decorrido_s"] is None


def test_cancelada_antes_de_autorizar() -> None:
    lt = linha("cancelada", cancelada_em=iso(120), cancelada_por=DONO)
    assert lt["desfecho"] == "cancelada"
    assert situacoes(lt) == [("solicitada", "concluida"), ("cancelada", "cancelada")]
    assert lt["etapas"][1]["duracao_desde_anterior_s"] == 120
    assert lt["etapas"][1]["por"] == {"nome": "Dona Maria"}
    assert lt["texto_equivalente"] == "Etapa 2 de 2: cancelada"


def test_cancelada_depois_de_autorizar() -> None:
    lt = linha("cancelada", cancelada_em=iso(1000), cancelada_por=STAFF_B, **AUTORIZADA)
    assert situacoes(lt) == [
        ("solicitada", "concluida"),
        ("autorizada", "concluida"),
        ("cancelada", "cancelada"),
    ]
    assert (lt["etapa_atual"], lt["total_etapas"]) == (3, 3)
    assert [e["duracao_desde_anterior_s"] for e in lt["etapas"]] == [None, 300, 700]
    assert all(e["decorrido_s"] is None for e in lt["etapas"])


def test_visao_solicitante_so_papel_para_a_equipe() -> None:
    lt = linha("concluida", ocultar=True, **CONCLUIDA)
    assert [e["por"] for e in lt["etapas"]] == [
        {"nome": "Dona Maria"},
        {"papel": "Autorizador"},
        {"papel": "Autorizador"},
        {"papel": "Autorizador"},
    ]
    texto = str(lt)
    assert "Ana Autorizadora" not in texto and "Beto Admin" not in texto


def test_visao_solicitante_cancelada_ou_negada_pela_equipe_mostra_papel() -> None:
    lt = linha("cancelada", ocultar=True, cancelada_em=iso(50), cancelada_por=STAFF_A)
    assert lt["etapas"][1]["por"] == {"papel": "Autorizador"}
    lt = linha("negada", ocultar=True, **AUTORIZADA)
    assert lt["etapas"][1]["por"] == {"papel": "Autorizador"}


def test_visao_staff_mostra_nomes() -> None:
    lt = linha("em_uso", **EM_USO)
    assert [e["por"] for e in lt["etapas"][:3]] == [
        {"nome": "Dona Maria"},
        {"nome": "Ana Autorizadora"},
        {"nome": "Beto Admin"},
    ]


def test_autor_desconhecido_vira_null() -> None:
    lt = montar_linha_do_tempo(req("solicitada"), {}, agora=AGORA)
    assert lt["etapas"][0]["por"] is None


# ---------------------------------------------------------------- pela API, fluxo real
def lt_de(resp: Any) -> dict[str, Any]:
    assert resp.status_code in (200, 201), resp.get_json()
    return resp.get_json()["linha_do_tempo"]


def test_fluxo_completo_pela_api_nas_duas_visoes(client, auth, c):  # type: ignore[no-untyped-def]
    r = criar(client, auth, c.sol, c.veiculo)
    rid = r.get_json()["id"]
    assert lt_de(r)["etapa_atual"] == 1
    for etapa, nome in [(2, "autorizar"), (3, "saida"), (4, "chegada")]:
        assert lt_de(acao(client, auth, c.aut, rid, nome))["etapa_atual"] == etapa
    dono = client.get(f"/api/requisicoes/{rid}", headers=auth(c.sol.id)).get_json()
    staff = client.get(f"/api/requisicoes/{rid}", headers=auth(c.adm.id)).get_json()
    assert dono["linha_do_tempo"]["desfecho"] == staff["linha_do_tempo"]["desfecho"] == "concluida"
    assert "historico" in dono and "historico" in staff  # compatibilidade
    assert dono["linha_do_tempo"]["etapas"][1]["por"] == {"papel": "Autorizador"}
    assert staff["linha_do_tempo"]["etapas"][1]["por"] == {"nome": c.aut.nome}
    assert c.aut.nome not in str(dono["linha_do_tempo"])


def test_negar_e_cancelar_pela_api(client, auth, c):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    lt = lt_de(acao(client, auth, c.aut, rid, "negar"))
    assert lt["desfecho"] == "negada" and lt["etapa_atual"] == 2
    rid2 = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    lt = lt_de(acao(client, auth, c.sol, rid2, "cancelar"))
    assert lt["desfecho"] == "cancelada"
    assert lt["etapas"][-1]["por"] == {"nome": c.sol.nome}


def test_listagem_inclui_linha_do_tempo_e_idor_continua(client, auth, c):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    itens = client.get("/api/requisicoes", headers=auth(c.sol.id)).get_json()["itens"]
    assert itens[0]["linha_do_tempo"]["texto_equivalente"] == "Etapa 1 de 4: solicitada"
    assert client.get("/api/requisicoes", headers=auth(c.outro.id)).get_json()["itens"] == []
    assert client.get(f"/api/requisicoes/{rid}", headers=auth(c.outro.id)).status_code == 404
