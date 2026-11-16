"""Chegada com distância acima de KM_LIMITE_CONFIRMACAO (2000 km) exige `confirmar_km_alto: true`.

O backend decide: sem a confirmação a API devolve 409 `km_alto_confirmar` e nada é gravado.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modelos import Configuracoes
from app.services.validacao import KM_LIMITE_CONFIRMACAO
from tests.test_permissoes_arquivamento import levar_a
from tests.test_requisicoes import acao

EQUIPE = Configuracoes(quilometragem=True, autorizador_km=True)
MOTORISTA = Configuracoes(
    quilometragem=True, motorista=True, motorista_registra=True, motorista_km=True
)
MSG_2001 = (
    "A distância percorrida será de 2.001 km, acima do limite de 2.000 km. Confira a "
    "quilometragem de chegada. Se estiver correta, confirme para registrar."
)


@pytest.fixture
def m(repo):  # type: ignore[no-untyped-def]
    class M:
        a = repo.novo_perfil("motorista")

    return M


@pytest.fixture
def v(repo):  # type: ignore[no-untyped-def]
    class V:
        vig = repo.novo_perfil("vigilante")

    return V


def post(client, auth, quem, rid, nome, corpo: Any = None):  # type: ignore[no-untyped-def]
    return client.post(f"/api/requisicoes/{rid}/{nome}", headers=auth(quem.id), json=corpo)


def em_uso(client, auth, repo, c, km_saida: int = 1000) -> str:  # type: ignore[no-untyped-def]
    repo.config = EQUIPE
    rid = levar_a(client, auth, repo, c, "autorizada")
    assert post(client, auth, c.aut, rid, "saida", {"km_saida": km_saida}).status_code == 200
    return rid


def nada_gravado(repo, rid: str) -> None:  # type: ignore[no-untyped-def]
    assert repo.requisicoes[rid]["status"] == "em_uso"
    assert repo.requisicoes[rid]["km_chegada"] is None
    assert repo.requisicoes[rid]["chegada_real_em"] is None


def test_limite_e_2000():
    assert KM_LIMITE_CONFIRMACAO == 2000


def test_exatamente_2000_passa_sem_aviso(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = em_uso(client, auth, repo, c)
    r = post(client, auth, c.aut, rid, "chegada", {"km_chegada": 3000})
    assert r.status_code == 200 and r.get_json()["status"] == "concluida"
    assert repo.requisicoes[rid]["km_chegada"] == 3000


@pytest.mark.parametrize("flag", [{}, {"confirmar_km_alto": False}, {"confirmar_km_alto": None}])
def test_2001_sem_confirmacao_409_e_nada_gravado(client, auth, repo, c, flag):  # type: ignore[no-untyped-def]
    rid = em_uso(client, auth, repo, c)
    r = post(client, auth, c.aut, rid, "chegada", {"km_chegada": 3001, **flag})
    assert r.status_code == 409
    assert r.get_json() == {
        "erro": {
            "codigo": "km_alto_confirmar",
            "mensagem": MSG_2001,
            "distancia_km": 2001,
            "limite_km": 2000,
        }
    }
    nada_gravado(repo, rid)


def test_mensagem_formata_milhar_pt_br(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = em_uso(client, auth, repo, c, km_saida=0)
    r = post(client, auth, c.aut, rid, "chegada", {"km_chegada": 8_641_333})
    erro = r.get_json()["erro"]
    assert r.status_code == 409 and erro["distancia_km"] == 8_641_333
    assert erro["mensagem"].startswith("A distância percorrida será de 8.641.333 km,")


def test_2001_com_confirmacao_grava(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = em_uso(client, auth, repo, c)
    corpo = {"km_chegada": 3001, "confirmar_km_alto": True}
    r = post(client, auth, c.aut, rid, "chegada", corpo)
    assert r.status_code == 200 and r.get_json()["status"] == "concluida"
    assert repo.requisicoes[rid]["km_chegada"] == 3001
    assert "confirmar_km_alto" not in repo.requisicoes[rid]


@pytest.mark.parametrize("valor", ["true", 1, 0, "sim", [True], {"a": 1}])
def test_flag_de_outro_tipo_e_400(client, auth, repo, c, valor):  # type: ignore[no-untyped-def]
    rid = em_uso(client, auth, repo, c)
    r = post(client, auth, c.aut, rid, "chegada", {"km_chegada": 3001, "confirmar_km_alto": valor})
    assert r.status_code == 400
    assert r.get_json()["erro"]["codigo"] == "requisicao_invalida"
    nada_gravado(repo, rid)


def test_flag_ignorada_ate_o_limite(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = em_uso(client, auth, repo, c)
    corpo = {"km_chegada": 1500, "confirmar_km_alto": "qualquer"}
    assert post(client, auth, c.aut, rid, "chegada", corpo).status_code == 200


def test_chegada_menor_que_saida_continua_400_antes_do_aviso(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = em_uso(client, auth, repo, c, km_saida=5000)
    r = post(client, auth, c.aut, rid, "chegada", {"km_chegada": 10})
    assert r.status_code == 400 and r.get_json()["erro"]["codigo"] == "requisicao_invalida"
    assert "menor" in r.get_json()["erro"]["mensagem"]
    r = post(client, auth, c.aut, rid, "chegada", {"km_chegada": "9999"})
    assert r.status_code == 400  # tipo inválido também vem antes do aviso
    nada_gravado(repo, rid)


def test_saida_sem_km_enviando_os_dois_na_chegada(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "autorizada")
    assert acao(client, auth, c.aut, rid, "saida").status_code == 200  # recurso desligado
    repo.config = EQUIPE
    corpo = {"km_saida": 100, "km_chegada": 2101}
    r = post(client, auth, c.aut, rid, "chegada", corpo)
    assert r.status_code == 409 and r.get_json()["erro"]["distancia_km"] == 2001
    assert repo.requisicoes[rid]["km_saida"] is None
    nada_gravado(repo, rid)
    r = post(client, auth, c.aut, rid, "chegada", {**corpo, "confirmar_km_alto": True})
    assert r.status_code == 200
    assert (repo.requisicoes[rid]["km_saida"], repo.requisicoes[rid]["km_chegada"]) == (100, 2101)


def test_recurso_desligado_ignora(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "autorizada")
    assert acao(client, auth, c.aut, rid, "saida").status_code == 200
    corpo = {"km_saida": 0, "km_chegada": 9_999_999, "confirmar_km_alto": "x"}
    r = post(client, auth, c.aut, rid, "chegada", corpo)
    assert r.status_code == 200 and repo.requisicoes[rid]["km_chegada"] is None


def test_perfil_sem_flag_de_km_ignora(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(quilometragem=True, autorizador_km=True)  # vigilante_km desligado
    rid = levar_a(client, auth, repo, c, "autorizada")
    assert post(client, auth, c.aut, rid, "saida", {"km_saida": 0}).status_code == 200
    r = post(client, auth, v.vig, rid, "chegada", {"km_chegada": 9_999})
    assert r.status_code == 200 and repo.requisicoes[rid]["km_chegada"] is None


def test_caminho_do_motorista(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    repo.config = MOTORISTA
    rid = levar_a(client, auth, repo, c, "autorizada", motorista=m.a)
    assert post(client, auth, m.a, rid, "saida", {"km_saida": 50}).status_code == 200
    r = post(client, auth, m.a, rid, "chegada", {"km_chegada": 2051})
    assert r.status_code == 409 and r.get_json()["erro"]["codigo"] == "km_alto_confirmar"
    assert r.get_json()["erro"]["distancia_km"] == 2001
    nada_gravado(repo, rid)
    r = post(client, auth, m.a, rid, "chegada", {"km_chegada": 2051, "confirmar_km_alto": "true"})
    assert r.status_code == 400
    nada_gravado(repo, rid)
    r = post(client, auth, m.a, rid, "chegada", {"km_chegada": 2051, "confirmar_km_alto": True})
    assert r.status_code == 200
    assert repo.requisicoes[rid]["km_chegada"] == 2051
    assert repo.requisicoes[rid]["status"] == "concluida"


def test_outros_erros_sem_campos_extras(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = em_uso(client, auth, repo, c)
    erro = post(client, auth, c.aut, rid, "chegada", {}).get_json()["erro"]
    assert set(erro) <= {"codigo", "mensagem", "campo"}


MOTORISTA_LOC = Configuracoes(
    quilometragem=True,
    motorista=True,
    motorista_registra=True,
    motorista_km=True,
    motorista_localizacao=True,
)
COORD = {"latitude": -15.79, "longitude": -47.88}


def test_motorista_localizacao_invalida_vem_antes_do_aviso_de_km(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    """B3: com a localização ligada, coordenada ausente ou inválida dá 400 de localização já na
    primeira tentativa, sem passar pelo 409 de km alto."""
    repo.config = MOTORISTA_LOC
    rid = levar_a(client, auth, repo, c, "autorizada", motorista=m.a)
    assert post(client, auth, m.a, rid, "saida", {"km_saida": 50, **COORD}).status_code == 200
    for ruim in ({}, {"latitude": 200, "longitude": -47.88}):
        r = post(client, auth, m.a, rid, "chegada", {"km_chegada": 2051, **ruim})
        assert r.status_code == 400, r.get_json()
        assert r.get_json()["erro"]["codigo"] != "km_alto_confirmar"
        nada_gravado(repo, rid)
    r = post(client, auth, m.a, rid, "chegada", {"km_chegada": 2051, **COORD})
    assert r.status_code == 409 and r.get_json()["erro"]["codigo"] == "km_alto_confirmar"
    nada_gravado(repo, rid)
    corpo = {"km_chegada": 2051, "confirmar_km_alto": True, **COORD}
    assert post(client, auth, m.a, rid, "chegada", corpo).status_code == 200
    assert repo.requisicoes[rid]["status"] == "concluida"


def test_motorista_km_invalido_continua_antes_da_localizacao(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    """As validações de km (tipo, chegada < saída) seguem antes de tudo."""
    repo.config = MOTORISTA_LOC
    rid = levar_a(client, auth, repo, c, "autorizada", motorista=m.a)
    assert post(client, auth, m.a, rid, "saida", {"km_saida": 50, **COORD}).status_code == 200
    r = post(client, auth, m.a, rid, "chegada", {"km_chegada": 10})
    assert r.status_code == 400
    assert "menor" in r.get_json()["erro"]["mensagem"]
    nada_gravado(repo, rid)
