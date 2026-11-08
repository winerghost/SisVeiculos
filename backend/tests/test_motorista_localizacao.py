"""Localização do motorista (recurso opcional `motorista_localizacao`): obrigatoriedade, validação,
flag desligada, autorizador/admin não coletam, IDOR, leitura só do admin (auditada), ausência de
coordenadas em logs/auditoria/notificações/payloads e rollback da RPC."""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Any
from unittest.mock import MagicMock

import pytest
from postgrest.exceptions import APIError

from app.errors import ChecklistObrigatorio, ErroApi
from app.modelos import Configuracoes, Perfil
from app.repositorios.supabase_repo import SupabaseRepositorio
from app.services.requisicoes import ServicoRequisicoes
from tests.fakes_admin import erro_db
from tests.test_motorista import atribuir, autorizar, cod, get, viagem_autorizada
from tests.test_requisicoes import criar

LAT, LON, PREC = -15.793889, -47.882778, 12.5
TEXTOS_COORD = ("15.793889", "47.882778")  # como aparecem se vazarem (qualquer formatação)
LOC = Configuracoes(motorista=True, motorista_registra=True, motorista_localizacao=True)
SEM_LOC = Configuracoes(motorista=True, motorista_registra=True)
COORD = {"latitude": LAT, "longitude": LON, "precisao_m": PREC}


@pytest.fixture
def m(repo):  # type: ignore[no-untyped-def]
    class M:
        a = repo.novo_perfil("motorista")
        b = repo.novo_perfil("motorista")

    return M


def registrar(client, auth, quem, rid, momento="saida", corpo=None, bruto=None):  # type: ignore[no-untyped-def]
    if bruto is not None:
        return client.post(
            f"/api/requisicoes/{rid}/{momento}",
            headers=auth(quem.id),
            data=bruto,
            content_type="application/json",
        )
    return client.post(f"/api/requisicoes/{rid}/{momento}", headers=auth(quem.id), json=corpo)


def sem_coordenadas(texto: str) -> bool:
    return not any(t in texto for t in TEXTOS_COORD)


def viagem(client, auth, repo, c, m, config=LOC):  # type: ignore[no-untyped-def]
    repo.config = config
    return viagem_autorizada(client, auth, repo, c, motorista=m.a)


# ------------------------------------------------------------------ configuração (admin)
def test_flag_no_catalogo_e_em_eu(client, auth, c, repo):  # type: ignore[no-untyped-def]
    j = client.get("/api/admin/configuracoes", headers=auth(c.adm.id)).get_json()
    item = next(i for i in j["itens"] if i["chave"] == "motorista_localizacao")
    assert item["exige"] == ["motorista_registra"] and item["habilitado"] is False
    assert item["acoes_permitidas"] == []  # exige "Motorista registra" ligado antes
    assert "Ligue antes" in item["motivo_indisponibilidade"]
    assert (
        get(client, auth, c.sol, "/api/eu").get_json()["recursos"]["motorista_localizacao"] is False
    )


def test_dependencias_e_auditoria_da_flag(client, auth, c, repo):  # type: ignore[no-untyped-def]
    h = auth(c.adm.id)
    put = lambda corpo: client.put("/api/admin/configuracoes", headers=h, json=corpo)  # noqa: E731
    r = put({"motorista_localizacao": True})
    assert r.status_code == 409 and cod(r) == "dependencia_recurso"
    r = put({"motorista": True, "motorista_localizacao": True})
    assert r.status_code == 409 and cod(r) == "dependencia_recurso"
    r = put({"motorista": True, "motorista_registra": True, "motorista_localizacao": True})
    assert r.status_code == 200 and r.get_json()["recursos"]["motorista_localizacao"] is True
    assert any(
        e["acao"] == "configuracao_alterada"
        and e["detalhes"] == {"recurso": "motorista_localizacao"}
        for e in repo.eventos
    )
    # desligar a base com a localização ligada é recusado; na ordem certa passa
    assert put({"motorista_registra": False}).status_code == 409
    assert put({"motorista_localizacao": False, "motorista_registra": False}).status_code == 200


def test_so_admin_altera_a_flag(client, auth, c, m):  # type: ignore[no-untyped-def]
    for quem in (c.sol, c.aut, m.a):
        r = client.put(
            "/api/admin/configuracoes", headers=auth(quem.id), json={"motorista_localizacao": True}
        )
        assert r.status_code == 403


def test_erros_de_dependencia_do_banco_sao_amigaveis():
    assert (
        "Localização do motorista" in erro_db("AG010", "A localização do motorista exige").mensagem
    )
    assert (
        "Motorista registra" in erro_db("AG010", "registro pelo motorista exige o Perfil").mensagem
    )
    assert "Abastecimento" in erro_db("AG010", "Abastecimento e manutenção exigem").mensagem
    assert erro_db(
        "23514", 'violates check constraint "ck_configuracoes_localizacao_exige_registra"'
    )


# ------------------------------------------------------------------ contrato de campos_acao
def test_campos_acao_pede_latitude_e_longitude_so_ao_motorista(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem(client, auth, repo, c, m)
    j = get(client, auth, m.a, f"/api/motorista/viagens/{rid}").get_json()
    assert j["campos_acao"]["registrar_saida"]["campos_obrigatorios"] == ["latitude", "longitude"]
    assert j["campos_acao"]["registrar_saida"]["campos_opcionais"] == ["precisao_m"]
    for quem in (c.aut, c.adm):  # autorizador/admin: nunca pedem coordenadas
        j = get(client, auth, quem, f"/api/requisicoes/{rid}").get_json()
        assert "registrar_saida" not in j["campos_acao"]
    repo.config = SEM_LOC
    j = get(client, auth, m.a, f"/api/motorista/viagens/{rid}").get_json()
    assert j["campos_acao"] == {}


def test_campos_acao_soma_km_e_checklist(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem(
        client,
        auth,
        repo,
        c,
        m,
        Configuracoes(
            motorista=True,
            motorista_registra=True,
            motorista_localizacao=True,
            quilometragem=True,
            checklist=True,
        ),
    )
    j = get(client, auth, m.a, f"/api/motorista/viagens/{rid}").get_json()
    entrada = j["campos_acao"]["registrar_saida"]
    assert entrada["campos_obrigatorios"] == ["km_saida", "latitude", "longitude"]
    assert entrada["checklist_pendente"] == "saida"
    repo.requisicoes[rid]["status"] = "em_uso"
    repo.requisicoes[rid]["km_saida"] = 10
    j = get(client, auth, m.a, f"/api/motorista/viagens/{rid}").get_json()
    assert j["campos_acao"]["registrar_chegada"]["campos_obrigatorios"] == [
        "km_chegada",
        "latitude",
        "longitude",
    ]


# ------------------------------------------------------------------ obrigatoriedade
@pytest.mark.parametrize(
    "corpo",
    [None, {}, {"latitude": LAT}, {"longitude": LON}, {"latitude": None, "longitude": None}, []],
)
def test_motorista_sem_coordenadas_e_bloqueado(client, auth, repo, c, m, corpo):  # type: ignore[no-untyped-def]
    rid = viagem(client, auth, repo, c, m)
    r = registrar(client, auth, m.a, rid, corpo=corpo)
    assert r.status_code == 400 and cod(r) == "localizacao_obrigatoria"
    assert repo.requisicoes[rid]["status"] == "autorizada"
    assert repo.localizacoes == [] and repo.chamadas_rpc_motorista == []


@pytest.mark.parametrize(
    "corpo",
    [
        {"latitude": 91, "longitude": LON},
        {"latitude": -90.0001, "longitude": LON},
        {"latitude": LAT, "longitude": 180.5},
        {"latitude": LAT, "longitude": -181},
        {"latitude": "-15.79", "longitude": "-47.88"},
        {"latitude": True, "longitude": LON},
        {"latitude": LAT, "longitude": False},
        {"latitude": [LAT], "longitude": LON},
        {"latitude": {"v": LAT}, "longitude": LON},
        {"latitude": 1e999, "longitude": LON},
        {"latitude": 10**400, "longitude": LON},
        {**COORD, "precisao_m": -1},
        {**COORD, "precisao_m": "10"},
        {**COORD, "precisao_m": True},
        {**COORD, "precisao_m": 1_000_000},
    ],
)
def test_coordenadas_invalidas_sao_recusadas_sem_ecoar_valores(client, auth, repo, c, m, corpo):  # type: ignore[no-untyped-def]
    rid = viagem(client, auth, repo, c, m)
    r = registrar(client, auth, m.a, rid, corpo=corpo)
    assert r.status_code == 400 and cod(r) == "localizacao_invalida"
    mensagem = r.get_json()["erro"]["mensagem"]
    assert sem_coordenadas(mensagem) and "91" not in mensagem and "180.5" not in mensagem
    assert repo.requisicoes[rid]["status"] == "autorizada"
    assert repo.localizacoes == [] and repo.chamadas_rpc_motorista == []


@pytest.mark.parametrize(
    "bruto",
    [
        '{"latitude": NaN, "longitude": -47.88}',
        '{"latitude": -15.79, "longitude": Infinity}',
        '{"latitude": -Infinity, "longitude": -47.88}',
        '{"latitude": -15.79, "longitude": -47.88, "precisao_m": NaN}',
    ],
)
def test_nan_e_infinito_do_json_sao_recusados(client, auth, repo, c, m, bruto):  # type: ignore[no-untyped-def]
    rid = viagem(client, auth, repo, c, m)
    r = registrar(client, auth, m.a, rid, bruto=bruto)
    assert r.status_code == 400 and cod(r) == "localizacao_invalida"
    assert repo.chamadas_rpc_motorista == [] and repo.requisicoes[rid]["status"] == "autorizada"


def test_limites_da_faixa_sao_aceitos(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem(client, auth, repo, c, m)
    r = registrar(
        client, auth, m.a, rid, corpo={"latitude": -90, "longitude": 180, "precisao_m": 0}
    )
    assert r.status_code == 200


# ------------------------------------------------------------------ registro com sucesso
def test_ciclo_completo_grava_localizacao_pela_rpc_com_ator_do_jwt(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem(client, auth, repo, c, m)
    # o corpo tenta forjar o ator e a autoria: tudo é ignorado
    forjado = {**COORD, "ator_id": m.b.id, "registrado_por": m.b.id, "p_ator": m.b.id}
    r = registrar(client, auth, m.a, rid, corpo=forjado)
    assert r.status_code == 200 and r.get_json()["status"] == "em_uso"
    assert repo.chamadas_rpc_motorista == [
        {
            "requisicao_id": rid,
            "momento": "saida",
            "ator_id": m.a.id,
            "latitude": LAT,
            "longitude": LON,
            "precisao_m": PREC,
            "km_saida": None,
            "km_chegada": None,
        }
    ]
    assert repo.requisicoes[rid]["saida_registrada_por"] == m.a.id
    r = registrar(client, auth, m.a, rid, "chegada", {"latitude": -23.5, "longitude": -46.6})
    assert r.status_code == 200 and r.get_json()["status"] == "concluida"
    assert repo.chamadas_rpc_motorista[1]["momento"] == "chegada"
    assert repo.chamadas_rpc_motorista[1]["precisao_m"] is None
    assert [(x["momento"], x["registrado_por"]) for x in repo.localizacoes] == [
        ("saida", m.a.id),
        ("chegada", m.a.id),
    ]
    # auditoria do banco: uma linha por registro, sem coordenadas
    linhas = repo.eventos_de("localizacao_registrada")
    assert [e["detalhes"] for e in linhas] == [
        {"recurso": "motorista_localizacao", "momento": "saida"},
        {"recurso": "motorista_localizacao", "momento": "chegada"},
    ]
    assert all(sem_coordenadas(json.dumps(e)) for e in repo.eventos)


def test_km_e_checklist_continuam_valendo_antes_da_rpc(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    cfg = Configuracoes(
        motorista=True,
        motorista_registra=True,
        motorista_localizacao=True,
        quilometragem=True,
        checklist=True,
    )
    rid = viagem(client, auth, repo, c, m, cfg)
    r = registrar(client, auth, m.a, rid, corpo={**COORD, "km_saida": 100})
    assert r.status_code == 409 and cod(r) == "checklist_obrigatorio"
    corpo = {"combustivel": "meio", "avarias": False}
    url = f"/api/requisicoes/{rid}/checklist/saida"
    assert client.post(url, headers=auth(m.a.id), json=corpo).status_code == 201
    r = registrar(client, auth, m.a, rid, corpo=COORD)  # sem km
    assert r.status_code == 400 and cod(r) == "requisicao_invalida"
    assert repo.chamadas_rpc_motorista == []
    r = registrar(client, auth, m.a, rid, corpo={**COORD, "km_saida": 100})
    assert r.status_code == 200
    assert repo.chamadas_rpc_motorista[0]["km_saida"] == 100
    assert (
        client.post(
            f"/api/requisicoes/{rid}/checklist/chegada", headers=auth(m.a.id), json=corpo
        ).status_code
        == 201
    )
    r = registrar(client, auth, m.a, rid, "chegada", {**COORD, "km_chegada": 90})
    assert r.status_code == 400 and len(repo.chamadas_rpc_motorista) == 1
    r = registrar(client, auth, m.a, rid, "chegada", {**COORD, "km_chegada": 150})
    assert r.status_code == 200
    assert repo.chamadas_rpc_motorista[1]["km_chegada"] == 150
    assert repo.requisicoes[rid]["status"] == "concluida"


# ------------------------------------------------------------------ flag desligada
def test_flag_desligada_ignora_coordenadas_mas_usa_a_rpc(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem(client, auth, repo, c, m, SEM_LOC)
    r = registrar(client, auth, m.a, rid, corpo=COORD)  # enviadas por engano: descartadas
    assert r.status_code == 200 and r.get_json()["status"] == "em_uso"
    ch = repo.chamadas_rpc_motorista[0]
    assert ch["latitude"] is None and ch["longitude"] is None and ch["precisao_m"] is None
    assert repo.localizacoes == [] and repo.eventos_de("localizacao_registrada") == []
    # coordenadas inválidas também são ignoradas (nem lidas) com a flag desligada
    r = registrar(client, auth, m.a, rid, "chegada", {"latitude": 999, "longitude": "x"})
    assert r.status_code == 200


# ------------------------------------------------------------------ autorizador/admin não coletam
@pytest.mark.parametrize("papel", ["aut", "adm"])
def test_equipe_registrando_nao_coleta_e_ignora_coordenadas(client, auth, repo, c, m, papel):  # type: ignore[no-untyped-def]
    rid = viagem(client, auth, repo, c, m)
    quem = getattr(c, papel)
    r = registrar(client, auth, quem, rid, corpo=COORD)
    assert r.status_code == 200 and r.get_json()["status"] == "em_uso"
    # mesmo inválidas, não são lidas nem recusadas
    r = registrar(client, auth, quem, rid, "chegada", {"latitude": 999, "longitude": "x"})
    assert r.status_code == 200 and r.get_json()["status"] == "concluida"
    assert repo.chamadas_rpc_motorista == [] and repo.localizacoes == []
    assert repo.eventos_de("localizacao_registrada") == []
    assert repo.requisicoes[rid]["saida_registrada_por"] == quem.id


def test_equipe_sem_coordenadas_registra_normalmente(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem(client, auth, repo, c, m)
    assert registrar(client, auth, c.aut, rid).status_code == 200


# ------------------------------------------------------------------ IDOR e permissões
def test_motorista_b_nao_registra_a_viagem_de_a(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem(client, auth, repo, c, m)
    r = registrar(client, auth, m.b, rid, corpo=COORD)
    inexistente = registrar(client, auth, m.b, "00000000-0000-4000-8000-000000000000", corpo=COORD)
    assert r.status_code == inexistente.status_code == 404
    assert r.get_json() == inexistente.get_json()
    assert repo.chamadas_rpc_motorista == [] and repo.localizacoes == []
    assert repo.requisicoes[rid]["status"] == "autorizada"


def test_troca_de_motorista_corrida_no_banco_vira_404(client, auth, repo, c, m, monkeypatch):  # type: ignore[no-untyped-def]
    """Troca entre a leitura do service e a RPC: o banco recusa (AG035) e a API responde como
    viagem inexistente, sem gravar coordenadas."""
    rid = viagem(client, auth, repo, c, m)
    original = repo.registrar_transicao_motorista

    def trocar_antes(**kw: Any) -> dict[str, Any]:
        repo.requisicoes[rid]["motorista_id"] = m.b.id
        return original(**kw)

    monkeypatch.setattr(repo, "registrar_transicao_motorista", trocar_antes)
    r = registrar(client, auth, m.a, rid, corpo=COORD)
    assert r.status_code == 404 and sem_coordenadas(json.dumps(r.get_json()))
    assert repo.localizacoes == [] and repo.requisicoes[rid]["status"] == "autorizada"


def test_solicitante_nao_registra_nem_envia_coordenadas(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem(client, auth, repo, c, m)
    assert registrar(client, auth, c.sol, rid, corpo=COORD).status_code == 403
    assert repo.chamadas_rpc_motorista == []


def test_motorista_sem_registra_continua_so_leitura(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem(client, auth, repo, c, m, Configuracoes(motorista=True))
    r = registrar(client, auth, m.a, rid, corpo=COORD)
    assert r.status_code == 403 and repo.chamadas_rpc_motorista == []


def test_traducao_dos_erros_da_rpc():
    r = erro_db("AG034", "x")
    assert (r.status, r.codigo) == (404, "recurso_desligado")
    r = erro_db("AG035", "x")
    assert (r.status, r.codigo) == (404, "nao_encontrado")
    r = erro_db("AG036", "Latitude -15.793889 inválida")
    assert (r.status, r.codigo) == (400, "localizacao_obrigatoria")
    assert sem_coordenadas(r.mensagem)
    r = erro_db("AG037", "x")
    assert (r.status, r.codigo) == (400, "registro_invalido")
    r = erro_db("AG013", "Checklist de chegada é obrigatório")
    assert (r.status, r.codigo) == (409, "checklist_obrigatorio")


# ------------------------------------------------------------------ RPC: atomicidade
def test_rpc_desfaz_a_localizacao_se_a_transicao_falha(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    """Rollback simulado: a localização é inserida antes do UPDATE; AG013 no UPDATE desfaz tudo."""
    rid = viagem(
        client,
        auth,
        repo,
        c,
        m,
        Configuracoes(
            motorista=True, motorista_registra=True, motorista_localizacao=True, checklist=True
        ),
    )
    antes = len(repo.eventos)
    with pytest.raises(ChecklistObrigatorio):  # chamada direta: ignora a checagem do service
        repo.registrar_transicao_motorista(
            requisicao_id=rid,
            momento="saida",
            ator_id=m.a.id,
            latitude=LAT,
            longitude=LON,
            precisao_m=None,
            km_saida=None,
            km_chegada=None,
        )
    assert repo.localizacoes == [] and len(repo.eventos) == antes
    assert repo.requisicoes[rid]["status"] == "autorizada"


def test_rpc_do_fake_aplica_as_regras_do_banco(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem(client, auth, repo, c, m)
    base = {
        "requisicao_id": rid,
        "momento": "saida",
        "ator_id": m.a.id,
        "latitude": LAT,
        "longitude": LON,
        "precisao_m": None,
        "km_saida": None,
        "km_chegada": None,
    }
    casos = [
        ({"momento": "ida"}, "registro_invalido"),
        ({"latitude": None}, "localizacao_obrigatoria"),
        ({"latitude": float("nan")}, "localizacao_obrigatoria"),
        ({"longitude": 181.0}, "localizacao_obrigatoria"),
        ({"precisao_m": -1.0}, "localizacao_obrigatoria"),
        ({"ator_id": m.b.id}, "nao_encontrado"),  # AG035
        ({"ator_id": c.aut.id}, "sem_permissao"),  # AG002: só motorista ativo
        ({"momento": "chegada"}, "transicao_invalida"),  # AG001: ainda autorizada
    ]
    for extra, codigo in casos:
        with pytest.raises(Exception) as e:  # noqa: PT011
            repo.registrar_transicao_motorista(**{**base, **extra})
        assert getattr(e.value, "codigo", None) == codigo, extra
    assert repo.localizacoes == [] and repo.requisicoes[rid]["status"] == "autorizada"
    repo.config = Configuracoes(motorista=True, motorista_registra=True)
    # flag de localização desligada no banco: a RPC não grava (AG034 se o registro está desligado)
    repo.registrar_transicao_motorista(**base)
    assert repo.localizacoes == []
    repo.config = Configuracoes(motorista=True)
    with pytest.raises(Exception) as e:  # noqa: PT011
        repo.registrar_transicao_motorista(**{**base, "momento": "chegada"})
    assert e.value.codigo == "recurso_desligado"  # type: ignore[attr-defined]


# ------------------------------------------------------------------ leitura do admin
def rota(rid: str) -> str:
    return f"/api/admin/requisicoes/{rid}/localizacoes"


def viagem_com_registro(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem(client, auth, repo, c, m)
    assert registrar(client, auth, m.a, rid, corpo=COORD).status_code == 200
    assert registrar(client, auth, m.a, rid, "chegada", COORD).status_code == 200
    return rid


def test_admin_le_as_coordenadas_e_a_leitura_e_auditada(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem_com_registro(client, auth, repo, c, m)
    r = get(client, auth, c.adm, rota(rid))
    assert r.status_code == 200
    j = r.get_json()
    assert [i["momento"] for i in j["itens"]] == ["saida", "chegada"]
    item = j["itens"][0]
    assert set(item) == {
        "momento",
        "latitude",
        "longitude",
        "precisao_m",
        "registrado_em",
        "registrado_por_nome",
    }
    assert (item["latitude"], item["longitude"], item["precisao_m"]) == (LAT, LON, PREC)
    assert item["registrado_por_nome"] == m.a.nome
    assert r.headers["Cache-Control"] == "no-store"
    (leitura,) = repo.eventos_de("localizacao_lida")
    assert leitura["tabela"] == "requisicoes"
    assert leitura["ator_id"] == c.adm.id and leitura["registro_id"] == rid
    assert leitura["detalhes"] == {"requisicao_id": rid, "itens": 2}
    assert not {"latitude", "longitude"} & set(leitura["detalhes"])
    assert sem_coordenadas(json.dumps(leitura["detalhes"]))
    assert [e for e in repo.eventos_de("acao_administrativa") if e["tabela"] == "requisicoes"] == []
    assert sem_coordenadas(json.dumps(repo.eventos))


def test_leitura_sem_registros_devolve_lista_vazia(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem(client, auth, repo, c, m)
    assert get(client, auth, c.adm, rota(rid)).get_json() == {"itens": []}


def test_so_admin_le_coordenadas(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem_com_registro(client, auth, repo, c, m)
    for quem in (c.aut, c.sol, c.outro, m.a, m.b):
        r = get(client, auth, quem, rota(rid))
        assert r.status_code == 403 and cod(r) == "sem_permissao", quem.role
        assert sem_coordenadas(r.get_data(as_text=True))
    assert client.get(rota(rid)).status_code == 401
    assert repo.eventos_de("localizacao_lida") == []


def test_leitura_admin_valida_id_e_existencia(client, auth, repo, c):  # type: ignore[no-untyped-def]
    assert get(client, auth, c.adm, rota("nao-e-uuid")).status_code == 400
    r = get(client, auth, c.adm, rota("00000000-0000-4000-8000-000000000000"))
    assert r.status_code == 404


def test_sem_trilha_de_auditoria_nao_ha_dados(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    from app.errors import ErroInterno

    rid = viagem_com_registro(client, auth, repo, c, m)
    repo.falha_auditoria = ErroInterno("falha")
    r = get(client, auth, c.adm, rota(rid))
    assert r.status_code == 503 and sem_coordenadas(r.get_data(as_text=True))
    assert "itens" not in r.get_json()
    assert repo.eventos_de("localizacao_lida") == []


def test_dados_gravados_continuam_legiveis_com_a_flag_desligada(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem_com_registro(client, auth, repo, c, m)
    repo.config = Configuracoes()
    assert len(get(client, auth, c.adm, rota(rid)).get_json()["itens"]) == 2


# ------------------------------------------------------------------ LGPD: nada vaza
def test_coordenadas_nao_aparecem_em_payloads_de_nenhum_outro_perfil(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem_com_registro(client, auth, repo, c, m)
    caminhos = [
        f"/api/requisicoes/{rid}",
        "/api/requisicoes",
        f"/api/requisicoes/{rid}/checklist",
        "/api/eu",
        "/api/notificacoes",
    ]
    for quem in (c.sol, c.aut, c.adm, m.a):
        for caminho in caminhos:
            texto = get(client, auth, quem, caminho).get_data(as_text=True)
            assert sem_coordenadas(texto), (quem.role, caminho)
    for caminho in (f"/api/motorista/viagens/{rid}", "/api/motorista/viagens"):
        assert sem_coordenadas(get(client, auth, m.a, caminho).get_data(as_text=True))
    # a resposta do próprio registro também não repete as coordenadas
    rid2 = viagem_autorizada(client, auth, repo, c, motorista=m.a, veiculo=repo.novo_veiculo())
    r = registrar(client, auth, m.a, rid2, corpo=COORD)
    assert r.status_code == 200 and sem_coordenadas(r.get_data(as_text=True))


def test_linha_do_tempo_historico_e_notificacoes_toleram_a_auditoria_nova(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem_com_registro(client, auth, repo, c, m)
    assert repo.eventos_de("localizacao_registrada")
    j = get(client, auth, c.adm, f"/api/requisicoes/{rid}").get_json()
    assert j["linha_do_tempo"]["desfecho"] == "concluida"
    assert [h["rotulo"] for h in j["historico"]][-2:] == ["Saída registrada", "Chegada registrada"]
    assert sem_coordenadas(json.dumps(j))


def test_relatorio_de_auditoria_aceita_a_acao_nova(client, auth, repo, c):  # type: ignore[no-untyped-def]
    from app.services.relatorios import ACOES_AUDITORIA

    assert dict(ACOES_AUDITORIA)["localizacao_registrada"] == "Localização registrada"
    r = get(client, auth, c.adm, "/api/relatorios/auditoria/previa?acao=localizacao_registrada")
    assert r.status_code == 200 and sem_coordenadas(r.get_data(as_text=True))


def repo_real() -> SupabaseRepositorio:
    r = SupabaseRepositorio.__new__(SupabaseRepositorio)
    r._c = MagicMock()
    return r


def api_error(codigo: str, mensagem: str) -> APIError:
    return APIError({"message": mensagem, "code": codigo, "details": mensagem, "hint": ""})


def test_repositorio_chama_a_rpc_com_os_parametros_certos():
    r = repo_real()
    r._c.rpc.return_value.execute.return_value.data = {
        "status": "em_uso",
        "localizacao_gravada": True,
    }
    out = r.registrar_transicao_motorista(
        requisicao_id="R",
        momento="saida",
        ator_id="A",
        latitude=LAT,
        longitude=LON,
        precisao_m=PREC,
        km_saida=100,
        km_chegada=None,
    )
    assert out == {"status": "em_uso", "localizacao_gravada": True}
    nome, params = r._c.rpc.call_args.args
    assert nome == "registrar_transicao_motorista"
    assert params == {
        "p_requisicao_id": "R",
        "p_momento": "saida",
        "p_ator": "A",
        "p_latitude": LAT,
        "p_longitude": LON,
        "p_precisao_m": PREC,
        "p_km_saida": 100,
        "p_km_chegada": None,
    }


def test_repositorio_nao_ecoa_nem_loga_coordenadas_nos_erros(caplog):  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    r = repo_real()
    chamada = dict(
        requisicao_id="R",
        momento="saida",
        ator_id="A",
        latitude=LAT,
        longitude=LON,
        precisao_m=None,
        km_saida=None,
        km_chegada=None,
    )
    vazamento = f"coordenadas {LAT} {LON} invalidas"
    esperado = {
        "AG034": "recurso_desligado",
        "AG035": "nao_encontrado",
        "AG036": "localizacao_obrigatoria",
        "AG037": "registro_invalido",
        "AG013": "checklist_obrigatorio",
        "XX999": "indisponivel",  # desconhecido: só o código vai ao log
    }
    for codigo, resposta in esperado.items():
        r._c.rpc.return_value.execute.side_effect = api_error(codigo, vazamento)
        with pytest.raises(ErroApi) as e:
            r.registrar_transicao_motorista(**chamada)
        assert e.value.codigo == resposta and sem_coordenadas(e.value.mensagem), codigo
    r._c.rpc.return_value.execute.side_effect = RuntimeError(vazamento)  # rede, timeout etc.
    with pytest.raises(ErroApi) as e:
        r.registrar_transicao_motorista(**chamada)
    assert e.value.codigo == "indisponivel"
    assert caplog.records, "o teste precisa observar logs"
    assert sem_coordenadas(caplog.text)


def test_repositorio_envia_o_ator_junto_do_motorista_no_mesmo_update():
    r = repo_real()
    consulta = r._c.table.return_value.update.return_value.eq.return_value.in_.return_value
    consulta.execute.return_value.data = []
    r.atribuir_motorista("R", "M", ["autorizada", "em_uso"], ator_id="A")
    r._c.table.assert_called_with("requisicoes")
    assert r._c.table.return_value.update.call_args.args[0] == {
        "motorista_id": "M",
        "motorista_alterado_por": "A",
    }


def test_repositorio_le_coordenadas_so_da_tabela_propria():
    r = repo_real()
    linha = {
        "momento": "saida",
        "latitude": LAT,
        "longitude": LON,
        "precisao_m": None,
        "registrado_em": "2026-11-08T12:00:00+00:00",
        "registrado_por": "A",
        "id": "x",  # coluna extra: descartada
    }
    q = r._c.table.return_value.select.return_value.eq.return_value.order.return_value
    q.execute.return_value.data = [linha]
    (item,) = r.listar_localizacoes("R")
    assert "id" not in item and item["latitude"] == LAT
    r._c.table.assert_called_with("localizacoes_viagem")


def test_fluxo_da_api_nao_gera_logs_com_coordenadas(client, auth, repo, c, m, caplog):  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    rid = viagem(client, auth, repo, c, m)
    registrar(client, auth, m.a, rid, corpo={"latitude": 95.793889, "longitude": -47.882778})
    registrar(client, auth, m.a, rid, bruto='{"latitude": NaN, "longitude": -47.882778}')
    registrar(client, auth, m.a, rid, corpo=COORD)
    registrar(client, auth, m.b, rid, "chegada", COORD)  # IDOR
    registrar(client, auth, m.a, rid, "chegada", COORD)
    get(client, auth, c.adm, rota(rid))
    assert sem_coordenadas(caplog.text)


class Gravador:
    def __init__(self) -> None:
        self.eventos: list[tuple[str, dict[str, Any]]] = []

    def notificar(self, evento: str, requisicao: dict[str, Any]) -> None:
        self.eventos.append((evento, dict(requisicao)))

    def fechar(self) -> None:
        return None


def test_notificador_externo_n8n_nao_recebe_coordenadas(repo, c, m):  # type: ignore[no-untyped-def]
    repo.config = LOC
    grav = Gravador()
    svc = ServicoRequisicoes(repo, notificador=grav)
    motorista: Perfil = m.a
    rid = svc.criar(c.sol, _corpo_requisicao(c.veiculo))["id"]
    svc.executar("autorizar", c.aut, rid, {"motorista_id": motorista.id})
    svc.executar("registrar_saida", motorista, rid, COORD)
    svc.executar("registrar_chegada", motorista, rid, COORD)
    assert [e for e, _ in grav.eventos][-2:] == ["requisicao.saida", "requisicao.chegada"]
    assert sem_coordenadas(json.dumps(grav.eventos, default=str))
    assert not any("latitude" in r or "longitude" in r for _, r in grav.eventos)


def _corpo_requisicao(veiculo_id: str) -> dict[str, Any]:
    from tests.test_requisicoes import corpo

    return corpo(veiculo_id)


def test_nao_ha_coordenadas_em_ia_relatorios_dashboards_e_notificacoes():
    """Guarda estática: estes módulos nunca devem tocar em latitude/longitude."""
    raiz = pathlib.Path(__file__).resolve().parents[1] / "app"
    alvos = [
        *raiz.joinpath("ia").rglob("*.py"),
        *raiz.joinpath("notificacoes").rglob("*.py"),
        raiz / "services" / "relatorios.py",
        raiz / "services" / "exportacao.py",
        raiz / "services" / "dashboards.py",
        raiz / "services" / "notificacoes.py",
        raiz / "rotas" / "relatorios.py",
        raiz / "rotas" / "dashboards.py",
        raiz / "rotas" / "notificacoes.py",
    ]
    assert len(alvos) > 9
    for arq in alvos:
        texto = arq.read_text(encoding="utf-8").lower()
        assert "latitude" not in texto and "longitude" not in texto, arq.name


def test_designacao_e_localizacao_convivem(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    """Troca de motorista antes da saída: só o novo registra e a coordenada é dele."""
    rid = viagem(client, auth, repo, c, m)
    assert atribuir(client, auth, c.aut, rid, m.b.id).status_code == 200
    assert registrar(client, auth, m.a, rid, corpo=COORD).status_code == 404
    assert registrar(client, auth, m.b, rid, corpo=COORD).status_code == 200
    assert [x["registrado_por"] for x in repo.localizacoes] == [m.b.id]
    assert criar and autorizar  # helpers reutilizados
