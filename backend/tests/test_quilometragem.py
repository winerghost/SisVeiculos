"""Recurso opcional Quilometragem: o backend decide; o banco (fake fiel) repete as garantias."""

from __future__ import annotations

import json
from typing import Any

import pytest
from postgrest.exceptions import APIError

from app.modelos import Configuracoes
from app.repositorios.supabase_repo import traduzir_erro_banco
from tests.fakes import RepoMemoria
from tests.test_requisicoes import acao, criar

LIGADO = Configuracoes(quilometragem=True)


@pytest.fixture
def c(repo: RepoMemoria):  # type: ignore[no-untyped-def]
    class C:
        sol = repo.novo_perfil("solicitante")
        outro = repo.novo_perfil("solicitante")
        aut = repo.novo_perfil("autorizador")
        veiculo = repo.novo_veiculo()

    return C


def preparar(client, auth, c, estado: str = "autorizada") -> str:  # type: ignore[no-untyped-def]
    """Leva uma requisição nova até `autorizada` ou `em_uso` (saída sem km se desligado)."""
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    assert acao(client, auth, c.aut, rid, "autorizar").status_code == 200
    return rid


def post(client, auth, perfil, rid, nome, corpo: Any = None):  # type: ignore[no-untyped-def]
    return client.post(f"/api/requisicoes/{rid}/{nome}", headers=auth(perfil.id), json=corpo)


def codigo(r: Any) -> str:
    return r.get_json()["erro"]["codigo"]


# ------------------------------------------------------------------ desligado
def test_desligado_ignora_km_sem_erro_e_nao_grava(client, auth, c, repo):  # type: ignore[no-untyped-def]
    rid = preparar(client, auth, c)
    r = post(client, auth, c.aut, rid, "saida", {"km_saida": 100, "km_chegada": "lixo"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["km_saida"] is None and j["km_chegada"] is None and j["campos_acao"] == {}
    r = post(client, auth, c.aut, rid, "chegada", {"km_saida": -5, "km_chegada": 1.5})
    assert r.status_code == 200
    assert repo.requisicoes[rid]["km_saida"] is None
    assert repo.requisicoes[rid]["km_chegada"] is None


def test_desligado_corpo_nao_objeto_e_ignorado(client, auth, c):  # type: ignore[no-untyped-def]
    rid = preparar(client, auth, c)
    assert post(client, auth, c.aut, rid, "saida", [1, 2]).status_code == 200


# ------------------------------------------------------------------ ligado
def test_ligado_ciclo_completo_com_km_e_contrato(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = preparar(client, auth, c)
    j = client.get(f"/api/requisicoes/{rid}", headers=auth(c.aut.id)).get_json()
    assert j["acoes_permitidas"] == ["registrar_saida", "cancelar"]
    assert j["campos_acao"] == {"registrar_saida": {"campos_obrigatorios": ["km_saida"]}}
    # solicitante dono não recebe campos de ações que não pode executar
    assert (
        client.get(f"/api/requisicoes/{rid}", headers=auth(c.sol.id)).get_json()["campos_acao"]
        == {}
    )

    j = post(client, auth, c.aut, rid, "saida", {"km_saida": 1000}).get_json()
    assert j["km_saida"] == 1000 and j["status"] == "em_uso"
    assert j["campos_acao"] == {"registrar_chegada": {"campos_obrigatorios": ["km_chegada"]}}
    j = post(client, auth, c.aut, rid, "chegada", {"km_chegada": 1000}).get_json()  # igual vale
    assert (j["km_saida"], j["km_chegada"], j["status"]) == (1000, 1000, "concluida")
    km = [h.get("km") for h in j["historico"]]
    assert km == [None, None, 1000, 1000]


def test_ligado_exige_km_saida(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = preparar(client, auth, c)
    for corpo in (None, {}, {"km_saida": None}, {"outro": 1}):
        r = post(client, auth, c.aut, rid, "saida", corpo)
        assert r.status_code == 400 and codigo(r) == "requisicao_invalida"
        assert "km_saida" in r.get_json()["erro"]["mensagem"]
    assert repo.requisicoes[rid]["status"] == "autorizada"


def test_ligado_corpo_nao_objeto(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = preparar(client, auth, c)
    assert post(client, auth, c.aut, rid, "saida", [1]).status_code == 400


@pytest.mark.parametrize(
    "valor",
    [True, False, 1.5, 10.0, "100", "", -1, 2_147_483_648, 10**30, [1], {"a": 1}],
)
def test_ligado_tipos_invalidos_e_overflow(client, auth, c, repo, valor):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = preparar(client, auth, c)
    r = post(client, auth, c.aut, rid, "saida", {"km_saida": valor})
    assert r.status_code == 400 and codigo(r) == "requisicao_invalida"
    assert r.get_json()["erro"]["mensagem"]
    assert repo.requisicoes[rid]["status"] == "autorizada"


def test_limite_do_integer_e_aceito(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = preparar(client, auth, c)
    r = post(client, auth, c.aut, rid, "saida", {"km_saida": 2_147_483_647})
    assert r.status_code == 200


def test_chegada_exige_km_chegada_e_nao_menor_que_saida(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = preparar(client, auth, c)
    post(client, auth, c.aut, rid, "saida", {"km_saida": 500})
    r = post(client, auth, c.aut, rid, "chegada", {})
    assert r.status_code == 400 and "km_chegada" in r.get_json()["erro"]["mensagem"]
    r = post(client, auth, c.aut, rid, "chegada", {"km_chegada": 499})
    assert r.status_code == 400 and "menor" in r.get_json()["erro"]["mensagem"]
    r = post(client, auth, c.aut, rid, "chegada", {"km_chegada": "600"})
    assert r.status_code == 400
    assert repo.requisicoes[rid]["status"] == "em_uso"
    # km_saida enviado na chegada é ignorado (já gravado, imutável)
    r = post(client, auth, c.aut, rid, "chegada", {"km_saida": 1, "km_chegada": 600})
    assert r.status_code == 200 and r.get_json()["km_saida"] == 500


def test_viagem_em_uso_sem_km_saida_exige_os_dois_na_chegada(client, auth, c, repo):  # type: ignore[no-untyped-def]
    rid = preparar(client, auth, c)
    post(client, auth, c.aut, rid, "saida")  # recurso desligado: sai sem km
    repo.config = LIGADO  # admin liga com a viagem em curso
    j = client.get(f"/api/requisicoes/{rid}", headers=auth(c.aut.id)).get_json()
    assert j["campos_acao"]["registrar_chegada"]["campos_obrigatorios"] == [
        "km_saida",
        "km_chegada",
    ]
    r = post(client, auth, c.aut, rid, "chegada", {"km_chegada": 900})
    assert r.status_code == 400
    msg = r.get_json()["erro"]["mensagem"]
    assert "km_saida e km_chegada juntos" in msg
    r = post(client, auth, c.aut, rid, "chegada", {"km_saida": 900, "km_chegada": 800})
    assert r.status_code == 400 and "menor" in r.get_json()["erro"]["mensagem"]
    r = post(client, auth, c.aut, rid, "chegada", {"km_saida": 800, "km_chegada": 900})
    assert r.status_code == 200
    assert (r.get_json()["km_saida"], r.get_json()["km_chegada"]) == (800, 900)


# ------------------------------------------------------------------ ligar/desligar entre chamadas
def test_ligar_e_desligar_entre_chamadas(client, auth, c, repo):  # type: ignore[no-untyped-def]
    rid = preparar(client, auth, c)
    repo.config = LIGADO
    assert post(client, auth, c.aut, rid, "saida", {}).status_code == 400
    assert post(client, auth, c.aut, rid, "saida", {"km_saida": 10}).status_code == 200
    repo.config = Configuracoes()  # desligou: ignora, nada novo gravado
    j = post(client, auth, c.aut, rid, "chegada", {"km_chegada": 5}).get_json()
    assert j["status"] == "concluida" and j["km_chegada"] is None
    # dado já gravado continua visível com o recurso desligado, para quem pode ver
    assert j["km_saida"] == 10 and j["campos_acao"] == {}
    for perfil in (c.sol, c.aut):
        v = client.get(f"/api/requisicoes/{rid}", headers=auth(perfil.id)).get_json()
        assert v["km_saida"] == 10
        assert [h.get("km") for h in v["historico"]][2] == 10
    lista = client.get("/api/requisicoes", headers=auth(c.sol.id)).get_json()["itens"]
    assert lista[0]["km_saida"] == 10


def test_corrida_backend_desligado_banco_ligado_traduz_ag012(client, auth, c, repo):  # type: ignore[no-untyped-def]
    """O backend leu 'desligado', mas o admin ligou antes da escrita: o banco recusa (AG012)."""
    rid = preparar(client, auth, c)
    transicionar = repo.transicionar

    def ligar_antes_da_escrita(*a: Any, **k: Any) -> Any:
        repo.config = LIGADO  # o admin liga entre a leitura do backend e o UPDATE
        return transicionar(*a, **k)

    repo.transicionar = ligar_antes_da_escrita  # type: ignore[method-assign]
    r = post(client, auth, c.aut, rid, "saida", {"km_saida": 1})
    assert r.status_code == 400 and codigo(r) == "km_obrigatorio"
    assert repo.requisicoes[rid]["status"] == "autorizada"


# ------------------------------------------------------------------ permissões / IDOR
def test_km_nao_vaza_nem_permite_acao_a_quem_nao_pode(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = preparar(client, auth, c)
    post(client, auth, c.aut, rid, "saida", {"km_saida": 777})
    r = client.get(f"/api/requisicoes/{rid}", headers=auth(c.outro.id))
    assert r.status_code == 404 and "777" not in r.get_data(as_text=True)
    lista = client.get("/api/requisicoes", headers=auth(c.outro.id)).get_json()
    assert lista["itens"] == [] and "777" not in json.dumps(lista)
    # solicitante não registra chegada (nem com km); estado inalterado
    r = post(client, auth, c.sol, rid, "chegada", {"km_chegada": 900})
    assert r.status_code == 403
    r = post(client, auth, c.outro, rid, "chegada", {"km_chegada": 900})
    assert r.status_code == 403  # a rota já exige autorizador/admin
    assert repo.requisicoes[rid]["km_chegada"] is None


def test_erros_de_km_nao_vazam_dados_internos(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = preparar(client, auth, c)
    r = post(client, auth, c.aut, rid, "saida", {"km_saida": "DROP TABLE"})
    texto = r.get_data(as_text=True)
    assert "DROP" not in texto and "Traceback" not in texto and "postgres" not in texto.lower()


# ------------------------------------------------------------------ tradução de erros do banco
def api_erro(codigo: str, message: str = "", details: str = "") -> APIError:
    return APIError({"message": message, "code": codigo, "details": details, "hint": ""})


@pytest.mark.parametrize(
    ("codigo_pg", "msg", "status", "codigo_api"),
    [
        ("AG012", "Quilometragem de saída é obrigatória.", 400, "km_obrigatorio"),
        ("AG004", "Quilometragem já gravada não pode mudar.", 409, "km_ja_registrado"),
        (
            "AG004",
            "Quilometragem só é registrada junto da saída ou da chegada.",
            409,
            "km_ja_registrado",
        ),
        (
            "23514",
            'violates check constraint "ck_requisicoes_km_chegada_maior_igual_saida"',
            400,
            "km_invalido",
        ),
        (
            "23514",
            'violates check constraint "ck_requisicoes_km_saida_nao_negativo"',
            400,
            "km_invalido",
        ),
        (
            "23514",
            'violates check constraint "ck_requisicoes_km_chegada_exige_saida"',
            400,
            "km_invalido",
        ),
        (
            "23514",
            'violates check constraint "ck_requisicoes_km_coerente_com_real"',
            400,
            "km_invalido",
        ),
        ("22003", "integer out of range", 400, "km_invalido"),
    ],
)
def test_traducao_km(codigo_pg, msg, status, codigo_api):  # type: ignore[no-untyped-def]
    e = traduzir_erro_banco(api_erro(codigo_pg, msg, "Failing row contains (segredo-123)"))
    assert (e.status, e.codigo) == (status, codigo_api)
    assert e.mensagem and "segredo" not in e.mensagem and "constraint" not in e.mensagem


def test_ag004_de_outras_regras_continua_generico():  # type: ignore[no-untyped-def]
    e = traduzir_erro_banco(api_erro("AG004", "Campo imutável."))
    assert e.codigo == "requisicao_invalida"
