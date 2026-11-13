"""Flags `autorizador_decide_propria` e `autorizador_km`, KM por perfil, cancelar/negar pela equipe,
arquivamento pessoal (estados, permissões, IDOR, concorrência), seletor de motoristas e
`grupos_acao`/`ver_*` em `acoes_permitidas`."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from app.errors import SemPermissao, TransicaoInvalida
from app.modelos import Configuracoes
from app.repositorios.supabase_repo import MSG_ARQUIVAR_ABERTA, MSG_DECIDIR_PROPRIA
from app.services.requisicoes import (
    CATEGORIA_ACAO,
    ESTADOS_ARQUIVAVEIS,
    ORDEM_CATEGORIAS,
    TRANSICOES,
    ServicoRequisicoes,
)
from tests.fakes_admin import erro_db
from tests.test_requisicoes import acao, criar

MSG_PROPRIA = "Você não pode decidir sobre a sua própria requisição."
TERMINAIS = ("concluida", "negada", "cancelada")
ABERTOS = ("solicitada", "autorizada", "em_uso")


def cod(r: Any) -> str:
    return str(r.get_json()["erro"]["codigo"])


def get(client, auth, quem, caminho):  # type: ignore[no-untyped-def]
    return client.get(caminho, headers=auth(quem.id))


def post(client, auth, quem, caminho, **corpo):  # type: ignore[no-untyped-def]
    return client.post(caminho, headers=auth(quem.id), json=corpo or None)


@pytest.fixture
def m(repo):  # type: ignore[no-untyped-def]
    class M:
        a = repo.novo_perfil("motorista")
        b = repo.novo_perfil("motorista")

    return M


@pytest.fixture
def v(repo):  # type: ignore[no-untyped-def]
    class V:
        vig = repo.novo_perfil("vigilante")

    return V


def levar_a(client, auth, repo, c, estado, *, dono=None, veiculo=None, motorista=None):  # type: ignore[no-untyped-def]
    """Cria uma requisição e a leva ao estado pedido pela API (nunca mexendo no banco)."""
    dono = dono or c.sol
    j = criar(client, auth, dono, veiculo or c.veiculo).get_json()
    rid = j["id"]
    if estado == "solicitada":
        return rid
    if estado == "cancelada":
        assert acao(client, auth, dono, rid, "cancelar").status_code == 200
        return rid
    if estado == "negada":
        assert acao(client, auth, c.aut, rid, "negar").status_code == 200
        return rid
    corpo = {"motorista_id": motorista.id} if motorista else None
    r = client.post(f"/api/requisicoes/{rid}/autorizar", headers=auth(c.aut.id), json=corpo)
    assert r.status_code == 200, r.get_json()
    for nome in {"autorizada": [], "em_uso": ["saida"], "concluida": ["saida", "chegada"]}[estado]:
        assert acao(client, auth, c.aut, rid, nome).status_code == 200
    return rid


# ===================================================================== autorizador_decide_propria
@pytest.mark.parametrize("papel", ["aut", "adm"])
@pytest.mark.parametrize("nome", ["autorizar", "negar"])
def test_decidir_a_propria_desligada_403_e_sem_a_acao(client, auth, repo, c, papel, nome):  # type: ignore[no-untyped-def]
    quem = getattr(c, papel)
    j = criar(client, auth, quem, c.veiculo).get_json()
    assert j["acoes_permitidas"] == ["cancelar"]  # só cancelar a própria
    r = acao(client, auth, quem, j["id"], nome)
    assert r.status_code == 403 and cod(r) == "sem_permissao"
    assert r.get_json()["erro"]["mensagem"] == MSG_PROPRIA
    assert repo.requisicoes[j["id"]]["status"] == "solicitada"


@pytest.mark.parametrize("papel", ["aut", "adm"])
@pytest.mark.parametrize(("nome", "estado"), [("autorizar", "autorizada"), ("negar", "negada")])
def test_decidir_a_propria_ligada_funciona(client, auth, repo, c, papel, nome, estado):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(autorizador_decide_propria=True)
    quem = getattr(c, papel)
    j = criar(client, auth, quem, c.veiculo).get_json()
    assert {"autorizar", "negar", "cancelar"} <= set(j["acoes_permitidas"])
    r = acao(client, auth, quem, j["id"], nome)
    assert r.status_code == 200 and r.get_json()["status"] == estado


def test_flag_e_lida_a_cada_chamada_sem_reiniciar(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, c.aut, c.veiculo).get_json()["id"]
    antes = get(client, auth, c.aut, f"/api/requisicoes/{rid}").get_json()["acoes_permitidas"]
    repo.config = Configuracoes(autorizador_decide_propria=True)
    ligada = get(client, auth, c.aut, f"/api/requisicoes/{rid}").get_json()["acoes_permitidas"]
    repo.config = Configuracoes()
    desligada = get(client, auth, c.aut, f"/api/requisicoes/{rid}").get_json()["acoes_permitidas"]
    assert "autorizar" not in antes and "autorizar" in ligada and "autorizar" not in desligada


def test_flag_nao_muda_requisicao_alheia_nem_da_poder_ao_solicitante(client, auth, repo, c):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(autorizador_decide_propria=True)
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    assert acao(client, auth, c.outro, rid, "autorizar").status_code == 403
    assert acao(client, auth, c.sol, rid, "autorizar").status_code == 403
    assert acao(client, auth, c.aut, rid, "autorizar").status_code == 200


def test_ag040_do_banco_vira_403_sem_permissao(client, auth, repo, c, monkeypatch):  # type: ignore[no-untyped-def]
    """Corrida: o service ainda achava a flag ligada, mas o banco (gatilho) a viu desligada."""
    rid = criar(client, auth, c.aut, c.veiculo).get_json()["id"]
    monkeypatch.setattr(ServicoRequisicoes, "_pode", lambda *_a, **_k: True)
    r = acao(client, auth, c.aut, rid, "autorizar")
    assert r.status_code == 403 and cod(r) == "sem_permissao"
    assert r.get_json()["erro"]["mensagem"] == MSG_DECIDIR_PROPRIA
    assert repo.requisicoes[rid]["status"] == "solicitada"


def test_ag041_vira_transicao_invalida_clara(repo, c):  # type: ignore[no-untyped-def]
    e = erro_db("AG041", "texto bruto do banco")
    assert isinstance(e, TransicaoInvalida) and e.status == 409
    assert str(e.mensagem) == MSG_ARQUIVAR_ABERTA and "bruto" not in str(e.mensagem)
    assert isinstance(erro_db("AG040"), SemPermissao)


# ===================================================================== configuração (admin)
def put(client, auth, quem, **corpo):  # type: ignore[no-untyped-def]
    return client.put("/api/admin/configuracoes", headers=auth(quem.id), json=corpo)


def test_admin_liga_decide_propria_com_auditoria(client, auth, repo, c):  # type: ignore[no-untyped-def]
    r = put(client, auth, c.adm, autorizador_decide_propria=True)
    assert r.status_code == 200 and r.get_json()["recursos"]["autorizador_decide_propria"] is True
    item = {i["chave"]: i for i in r.get_json()["itens"]}["autorizador_decide_propria"]
    assert item["habilitado"] is True and item["acoes_permitidas"] == ["desligar"]
    assert item["exige"] == [] and item["rotulo"]
    ev = repo.eventos_de("configuracao_alterada")
    assert [(e["detalhes"]["recurso"], e["de"], e["para"]) for e in ev] == [
        ("autorizador_decide_propria", "false", "true")
    ]
    put(client, auth, c.adm, autorizador_decide_propria=False)
    assert len(repo.eventos_de("configuracao_alterada")) == 2


def test_autorizador_km_exige_quilometragem_nos_dois_sentidos(client, auth, repo, c):  # type: ignore[no-untyped-def]
    r = put(client, auth, c.adm, autorizador_km=True)
    assert r.status_code == 409 and cod(r) == "dependencia_recurso"
    itens = {
        i["chave"]: i
        for i in get(client, auth, c.adm, "/api/admin/configuracoes").get_json()["itens"]
    }
    assert itens["autorizador_km"]["exige"] == ["quilometragem"]
    assert itens["autorizador_km"]["acoes_permitidas"] == []  # falta ligar a Quilometragem
    assert put(client, auth, c.adm, quilometragem=True, autorizador_km=True).status_code == 200
    assert repo.config.autorizador_km is True
    r = put(client, auth, c.adm, quilometragem=False)  # caminho inverso
    assert r.status_code == 409 and cod(r) == "dependencia_recurso"
    assert repo.config.quilometragem is True


@pytest.mark.parametrize("chave", ["autorizador_decide_propria", "autorizador_km"])
def test_so_admin_altera_as_flags_novas(client, auth, repo, c, chave):  # type: ignore[no-untyped-def]
    for quem in (c.aut, c.sol):
        assert put(client, auth, quem, **{chave: True}).status_code == 403
    assert getattr(repo.config, chave) is False


def test_eu_informa_as_flags_novas(client, auth, repo, c):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(quilometragem=True, autorizador_km=True)
    rec = get(client, auth, c.sol, "/api/eu").get_json()["recursos"]
    assert rec["autorizador_km"] is True and rec["autorizador_decide_propria"] is False


def test_dependencia_do_banco_e_traduzida_para_a_mensagem_clara():
    e = erro_db("AG010", "O KM do autorizador exige a Quilometragem ligada.")
    assert e.codigo == "dependencia_recurso" and "autorizador" in e.mensagem
    e = erro_db("AG010", "Desligue o KM do autorizador antes de desligar a Quilometragem.")
    assert e.codigo == "dependencia_recurso" and e.mensagem.startswith("Desligue")
    e = erro_db(
        "23514", 'violates check constraint "ck_configuracoes_autorizador_km_exige_quilometragem"'
    )
    assert e.codigo == "dependencia_recurso" and e.mensagem.startswith("Desligue")


# ===================================================================== KM por perfil
@pytest.mark.parametrize(
    ("role", "cfg", "esperado"),
    [
        ("vigilante", Configuracoes(quilometragem=True, vigilante_km=True), True),
        ("vigilante", Configuracoes(quilometragem=True, autorizador_km=True), False),
        ("vigilante", Configuracoes(vigilante_km=True), False),  # sem Quilometragem
        ("autorizador", Configuracoes(quilometragem=True, autorizador_km=True), True),
        ("admin", Configuracoes(quilometragem=True, autorizador_km=True), True),
        ("autorizador", Configuracoes(quilometragem=True, vigilante_km=True), False),
        ("admin", Configuracoes(quilometragem=True), False),
        ("autorizador", Configuracoes(autorizador_km=True), False),  # sem Quilometragem
        (
            "motorista",
            Configuracoes(quilometragem=True, vigilante_km=True, autorizador_km=True),
            False,
        ),
        (
            "solicitante",
            Configuracoes(quilometragem=True, vigilante_km=True, autorizador_km=True),
            False,
        ),
    ],
)
def test_pede_km_por_perfil(repo, role, cfg, esperado):  # type: ignore[no-untyped-def]
    assert ServicoRequisicoes._pede_km(repo.novo_perfil(role), cfg) is esperado


def test_staff_sem_autorizador_km_registra_sem_km_e_o_corpo_e_ignorado(client, auth, repo, c):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(quilometragem=True)
    rid = levar_a(client, auth, repo, c, "autorizada")
    j = get(client, auth, c.aut, f"/api/requisicoes/{rid}").get_json()
    assert "registrar_saida" not in j["campos_acao"]  # nem o campo vem
    r = client.post(f"/api/requisicoes/{rid}/saida", headers=auth(c.aut.id), json={"km_saida": 9})
    assert r.status_code == 200 and repo.requisicoes[rid]["km_saida"] is None


def test_staff_com_autorizador_km_pede_e_valida_km(client, auth, repo, c):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(quilometragem=True, autorizador_km=True)
    rid = levar_a(client, auth, repo, c, "autorizada")
    j = get(client, auth, c.adm, f"/api/requisicoes/{rid}").get_json()
    assert j["campos_acao"]["registrar_saida"]["campos_obrigatorios"] == ["km_saida"]
    assert acao(client, auth, c.adm, rid, "saida").status_code == 400
    h = auth(c.adm.id)
    assert (
        client.post(f"/api/requisicoes/{rid}/saida", headers=h, json={"km_saida": 50}).status_code
        == 200
    )
    r = client.post(f"/api/requisicoes/{rid}/chegada", headers=h, json={"km_chegada": 10})
    assert r.status_code == 400
    r = client.post(f"/api/requisicoes/{rid}/chegada", headers=h, json={"km_chegada": 80})
    assert r.status_code == 200 and repo.requisicoes[rid]["km_chegada"] == 80


def test_saida_sem_km_pelo_vigilante_e_chegada_pelo_staff_exige_os_dois(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(quilometragem=True, autorizador_km=True)  # vigilante_km desligada
    rid = levar_a(client, auth, repo, c, "autorizada")
    assert acao(client, auth, v.vig, rid, "saida").status_code == 200  # vigilante sem km
    assert repo.requisicoes[rid]["km_saida"] is None
    j = get(client, auth, c.aut, f"/api/requisicoes/{rid}").get_json()
    assert j["campos_acao"]["registrar_chegada"]["campos_obrigatorios"] == [
        "km_saida",
        "km_chegada",
    ]
    h = auth(c.aut.id)
    assert (
        client.post(
            f"/api/requisicoes/{rid}/chegada", headers=h, json={"km_chegada": 9}
        ).status_code
        == 400
    )
    corpo = {"km_saida": 5, "km_chegada": 9}
    assert client.post(f"/api/requisicoes/{rid}/chegada", headers=h, json=corpo).status_code == 200


def test_vigilante_segue_vigilante_km_e_motorista_nunca(client, auth, repo, c, v, m):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(
        quilometragem=True, vigilante_km=True, motorista=True, motorista_registra=True
    )
    rid = levar_a(client, auth, repo, c, "autorizada", motorista=m.a)
    jv = get(client, auth, v.vig, f"/api/requisicoes/{rid}").get_json()
    assert jv["campos_acao"]["registrar_saida"]["campos_obrigatorios"] == ["km_saida"]
    jm = get(client, auth, m.a, f"/api/motorista/viagens/{rid}").get_json()
    assert "registrar_saida" not in jm["campos_acao"]


# ===================================================================== cancelar / negar pela equipe
@pytest.mark.parametrize("papel", ["aut", "adm"])
@pytest.mark.parametrize("estado", ["solicitada", "autorizada"])
def test_equipe_cancela_requisicao_de_qualquer_dono(client, auth, repo, c, papel, estado):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, estado)
    j = get(client, auth, getattr(c, papel), f"/api/requisicoes/{rid}").get_json()
    assert "cancelar" in j["acoes_permitidas"]
    r = acao(client, auth, getattr(c, papel), rid, "cancelar")
    assert r.status_code == 200 and r.get_json()["status"] == "cancelada"


@pytest.mark.parametrize("papel", ["aut", "adm"])
def test_equipe_nega_requisicao_de_qualquer_dono(client, auth, repo, c, papel):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "solicitada")
    r = acao(client, auth, getattr(c, papel), rid, "negar")
    assert r.status_code == 200 and r.get_json()["status"] == "negada"


def test_solicitante_continua_so_cancelando_a_propria_solicitada(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "autorizada")
    assert (
        "cancelar"
        not in get(client, auth, c.sol, f"/api/requisicoes/{rid}").get_json()["acoes_permitidas"]
    )
    assert acao(client, auth, c.sol, rid, "cancelar").status_code == 403
    assert acao(client, auth, c.outro, rid, "cancelar").status_code == 404  # alheia: invisível
    rid2 = levar_a(client, auth, repo, c, "solicitada", veiculo=repo.novo_veiculo())
    assert acao(client, auth, c.sol, rid2, "cancelar").status_code == 200


# ===================================================================== arquivar: estados
@pytest.mark.parametrize("estado", ABERTOS)
def test_nunca_arquiva_requisicao_aberta(client, auth, repo, c, estado):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, estado)
    j = get(client, auth, c.aut, f"/api/requisicoes/{rid}").get_json()
    assert "arquivar" not in j["acoes_permitidas"] and "desarquivar" not in j["acoes_permitidas"]
    for quem in (c.sol, c.aut, c.adm):
        r = post(client, auth, quem, f"/api/requisicoes/{rid}/arquivar")
        assert r.status_code == 409 and cod(r) == "transicao_invalida"
    assert repo.arquivadas == {}


@pytest.mark.parametrize("estado", TERMINAIS)
def test_arquiva_e_desarquiva_estados_terminais(client, auth, repo, c, estado):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, estado)
    j = get(client, auth, c.sol, f"/api/requisicoes/{rid}").get_json()
    assert "arquivar" in j["acoes_permitidas"] and j["arquivada"] is False
    r = post(client, auth, c.sol, f"/api/requisicoes/{rid}/arquivar")
    assert r.status_code == 200 and r.get_json()["arquivada"] is True
    assert "desarquivar" in r.get_json()["acoes_permitidas"]
    assert "arquivar" not in r.get_json()["acoes_permitidas"]
    r = post(client, auth, c.sol, f"/api/requisicoes/{rid}/desarquivar")
    assert r.status_code == 200 and r.get_json()["arquivada"] is False
    assert "arquivar" in r.get_json()["acoes_permitidas"] and repo.arquivadas == {}


def test_arquivar_nao_toca_na_requisicao_nem_gera_auditoria(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "concluida")
    antes = (dict(repo.requisicoes[rid]), list(repo.auditoria), list(repo.eventos))
    post(client, auth, c.sol, f"/api/requisicoes/{rid}/arquivar")
    post(client, auth, c.sol, f"/api/requisicoes/{rid}/desarquivar")
    assert (repo.requisicoes[rid], repo.auditoria, repo.eventos) == antes


def test_arquivar_e_desarquivar_sao_idempotentes(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "concluida")
    for _ in range(3):
        assert post(client, auth, c.sol, f"/api/requisicoes/{rid}/arquivar").status_code == 200
    assert len(repo.arquivadas) == 1
    for _ in range(3):
        assert post(client, auth, c.sol, f"/api/requisicoes/{rid}/desarquivar").status_code == 200
    assert repo.arquivadas == {}


def test_estados_arquivaveis_sao_exatamente_os_terminais():
    assert frozenset(TERMINAIS) == ESTADOS_ARQUIVAVEIS


# ===================================================================== arquivar: permissões e IDOR
def test_solicitante_so_arquiva_a_propria(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "concluida")
    r = post(client, auth, c.outro, f"/api/requisicoes/{rid}/arquivar")
    assert r.status_code == 404 and cod(r) == "nao_encontrado"  # não revela a existência
    assert post(client, auth, c.outro, f"/api/requisicoes/{rid}/desarquivar").status_code == 404
    assert repo.arquivadas == {}


@pytest.mark.parametrize("papel", ["aut", "adm"])
def test_equipe_arquiva_qualquer_requisicao_encerrada(client, auth, repo, c, papel):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "negada")
    quem = getattr(c, papel)
    j = get(client, auth, quem, f"/api/requisicoes/{rid}").get_json()
    assert "arquivar" in j["acoes_permitidas"]
    assert post(client, auth, quem, f"/api/requisicoes/{rid}/arquivar").status_code == 200


def test_vigilante_nunca_arquiva(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    j = criar(client, auth, v.vig, c.veiculo).get_json()  # própria
    assert acao(client, auth, c.aut, j["id"], "cancelar").status_code == 200  # encerrada
    r = post(client, auth, v.vig, f"/api/requisicoes/{j['id']}/arquivar")
    assert r.status_code == 403 and cod(r) == "sem_permissao"
    assert (
        "arquivar"
        not in get(client, auth, v.vig, f"/api/requisicoes/{j['id']}").get_json()[
            "acoes_permitidas"
        ]
    )
    assert repo.arquivadas == {}


def test_arquivar_exige_autenticacao_e_rejeita_id_invalido(client, auth, c):  # type: ignore[no-untyped-def]
    assert client.post("/api/requisicoes/x/arquivar").status_code == 401
    r = post(client, auth, c.sol, "/api/requisicoes/nao-e-uuid/arquivar")
    assert r.status_code in {400, 404}
    r = post(client, auth, c.sol, "/api/requisicoes/00000000-0000-4000-8000-000000000000/arquivar")
    assert r.status_code == 404


def test_o_dono_do_arquivamento_e_sempre_o_jwt(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "concluida")
    post(client, auth, c.sol, f"/api/requisicoes/{rid}/arquivar", usuario_id=c.aut.id)
    assert list(repo.arquivadas) == [(c.sol.id, rid)]


# ===================================================================== arquivar: motorista
def test_motorista_arquiva_so_viagem_atribuida_com_recurso_ligado(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True)
    rid = levar_a(client, auth, repo, c, "concluida", motorista=m.a)
    j = get(client, auth, m.a, f"/api/motorista/viagens/{rid}").get_json()
    assert j["acoes_permitidas"] == ["arquivar"]
    r = post(client, auth, m.a, f"/api/motorista/viagens/{rid}/arquivar")
    assert r.status_code == 200 and r.get_json()["arquivada"] is True
    assert post(client, auth, m.b, f"/api/motorista/viagens/{rid}/arquivar").status_code == 404
    assert post(client, auth, m.b, f"/api/requisicoes/{rid}/arquivar").status_code == 404
    assert post(client, auth, m.b, f"/api/requisicoes/{rid}/desarquivar").status_code == 404
    assert list(repo.arquivadas) == [(m.a.id, rid)]
    r = post(client, auth, m.a, f"/api/motorista/viagens/{rid}/desarquivar")
    assert r.status_code == 200 and r.get_json()["arquivada"] is False


def test_motorista_nao_arquiva_viagem_nao_encerrada(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True)
    rid = levar_a(client, auth, repo, c, "autorizada", motorista=m.a)
    r = post(client, auth, m.a, f"/api/motorista/viagens/{rid}/arquivar")
    assert r.status_code == 409 and cod(r) == "transicao_invalida"


def test_motorista_com_recurso_desligado_e_recusado_e_dados_ficam(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True)
    rid = levar_a(client, auth, repo, c, "concluida", motorista=m.a)
    assert post(client, auth, m.a, f"/api/motorista/viagens/{rid}/arquivar").status_code == 200
    repo.config = Configuracoes()  # recurso desligado: nada é apagado, mas nada é aceito
    r = post(client, auth, m.a, f"/api/motorista/viagens/{rid}/desarquivar")
    assert r.status_code == 404 and cod(r) == "recurso_desligado"
    assert get(client, auth, m.a, "/api/motorista/viagens").status_code == 404
    assert list(repo.arquivadas) == [(m.a.id, rid)]


def test_so_motorista_usa_as_rotas_de_arquivar_viagem(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True)
    rid = levar_a(client, auth, repo, c, "concluida", motorista=m.a)
    assert post(client, auth, c.aut, f"/api/motorista/viagens/{rid}/arquivar").status_code == 403


def test_atribuicao_ao_arquivar_nao_vaza_dados_de_equipe_ao_motorista(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True)
    rid = levar_a(client, auth, repo, c, "concluida", motorista=m.a)
    j = post(client, auth, m.a, f"/api/motorista/viagens/{rid}/arquivar").get_json()
    assert "historico" in j and all("por" not in e for e in j["historico"])


# ===================================================================== lista: filtro e total
def ids(resp: Any) -> list[str]:
    return [i["id"] for i in resp.get_json()["itens"]]


def test_filtro_arquivadas_pessoal_total_coerente(client, auth, repo, c):  # type: ignore[no-untyped-def]
    r1 = levar_a(client, auth, repo, c, "concluida")
    r2 = levar_a(client, auth, repo, c, "cancelada")
    r3 = levar_a(client, auth, repo, c, "negada")
    post(client, auth, c.sol, f"/api/requisicoes/{r1}/arquivar")
    post(client, auth, c.aut, f"/api/requisicoes/{r2}/arquivar")  # outro usuário, outra lista

    padrao = get(client, auth, c.sol, "/api/requisicoes")  # padrão: sem arquivadas
    assert set(ids(padrao)) == {r2, r3} and padrao.get_json()["total"] == 2
    so = get(client, auth, c.sol, "/api/requisicoes?arquivadas=true")
    assert ids(so) == [r1] and so.get_json()["total"] == 1
    assert get(client, auth, c.sol, "/api/requisicoes?arquivadas=false").get_json()["total"] == 2
    assert all(i["arquivada"] for i in so.get_json()["itens"])

    aut = get(client, auth, c.aut, "/api/requisicoes")  # a lista da equipe é independente
    assert set(ids(aut)) == {r1, r3} and aut.get_json()["total"] == 2
    assert ids(get(client, auth, c.aut, "/api/requisicoes?arquivadas=true")) == [r2]
    # arquivar pelo solicitante não altera a requisição que a equipe e os relatórios enxergam
    assert repo.requisicoes[r1]["status"] == "concluida"


def test_filtro_combina_com_status_e_valor_invalido_e_400(client, auth, repo, c):  # type: ignore[no-untyped-def]
    r1 = levar_a(client, auth, repo, c, "concluida")
    post(client, auth, c.sol, f"/api/requisicoes/{r1}/arquivar")
    r = get(client, auth, c.sol, "/api/requisicoes?arquivadas=true&status=cancelada")
    assert ids(r) == [] and r.get_json()["total"] == 0
    r = get(client, auth, c.sol, "/api/requisicoes?arquivadas=talvez")
    assert r.status_code == 400 and cod(r) == "requisicao_invalida"


def test_arquivadas_nao_expoe_requisicao_alheia(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "concluida")
    post(client, auth, c.aut, f"/api/requisicoes/{rid}/arquivar")
    r = get(client, auth, c.outro, "/api/requisicoes?arquivadas=true")
    assert ids(r) == [] and r.get_json()["total"] == 0


def test_viagens_do_motorista_filtram_arquivadas(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True)
    r1 = levar_a(client, auth, repo, c, "concluida", motorista=m.a)
    r2 = levar_a(client, auth, repo, c, "autorizada", motorista=m.a)
    assert acao(client, auth, c.aut, r2, "cancelar").status_code == 200
    post(client, auth, m.a, f"/api/motorista/viagens/{r1}/arquivar")
    ativa = get(client, auth, m.a, "/api/motorista/viagens")
    assert ids(ativa) == [r2] and ativa.get_json()["total"] == 1
    arq = get(client, auth, m.a, "/api/motorista/viagens?arquivadas=true")
    assert ids(arq) == [r1] and arq.get_json()["total"] == 1
    assert get(client, auth, m.a, "/api/motorista/viagens?arquivadas=x").status_code == 400
    assert get(client, auth, c.aut, "/api/requisicoes").get_json()["total"] == 2  # equipe intacta


def test_vigilante_lista_sem_juncao_de_arquivo(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "autorizada")
    assert ids(get(client, auth, v.vig, "/api/requisicoes")) == [rid]
    assert ids(get(client, auth, v.vig, "/api/requisicoes?arquivadas=true")) == [
        rid
    ]  # nunca arquiva


# ===================================================================== concorrência
def test_arquivar_concorrente_so_uma_linha_e_sem_erro(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "concluida")
    barreira = threading.Barrier(8)
    status: list[int] = []

    def tentar() -> None:
        barreira.wait()
        status.append(post(client, auth, c.sol, f"/api/requisicoes/{rid}/arquivar").status_code)

    ts = [threading.Thread(target=tentar) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert status == [200] * 8 and len(repo.arquivadas) == 1


def test_arquivar_e_desarquivar_simultaneos_deixam_estado_consistente(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "concluida")
    barreira = threading.Barrier(8)
    status: list[int] = []

    def tentar(nome: str) -> None:
        barreira.wait()
        status.append(post(client, auth, c.sol, f"/api/requisicoes/{rid}/{nome}").status_code)

    ts = [
        threading.Thread(target=tentar, args=(("arquivar", "desarquivar")[i % 2],))
        for i in range(8)
    ]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert status == [200] * 8 and len(repo.arquivadas) <= 1


# ===================================================================== grupos_acao e ver_*
def test_toda_acao_tem_categoria():
    extras = {"atribuir_motorista", "ver_checklist", "ver_localizacao", "arquivar", "desarquivar"}
    assert set(TRANSICOES) | extras <= set(CATEGORIA_ACAO)
    assert set(CATEGORIA_ACAO.values()) <= set(ORDEM_CATEGORIAS)


def test_grupos_acao_agrupa_e_ordena_sem_perder_acoes(client, auth, repo, c):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True)
    rid = levar_a(client, auth, repo, c, "autorizada")
    j = get(client, auth, c.aut, f"/api/requisicoes/{rid}").get_json()
    assert j["grupos_acao"] == {
        "decidir": ["cancelar"],
        "operar": ["registrar_saida"],
        "designar": ["atribuir_motorista"],
    }
    # A ordem de exibição é ORDEM_CATEGORIAS (o JSON do Flask ordena as chaves alfabeticamente).
    assert set(j["grupos_acao"]) <= set(ORDEM_CATEGORIAS)
    assert sorted(a for grupo in j["grupos_acao"].values() for a in grupo) == sorted(
        j["acoes_permitidas"]
    )


def test_cancelar_do_solicitante_e_categoria_solicitar(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "solicitada")
    assert get(client, auth, c.sol, f"/api/requisicoes/{rid}").get_json()["grupos_acao"] == {
        "solicitar": ["cancelar"]
    }
    lista = get(client, auth, c.sol, "/api/requisicoes").get_json()["itens"]
    assert all("grupos_acao" in i for i in lista)


def test_requisicao_sem_acoes_tem_grupos_vazios(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    j = criar(client, auth, v.vig, c.veiculo).get_json()
    assert j["acoes_permitidas"] == [] and j["grupos_acao"] == {}


def test_ver_localizacao_so_para_admin_com_saida_registrada(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "autorizada")
    assert (
        "ver_localizacao"
        not in get(client, auth, c.adm, f"/api/requisicoes/{rid}").get_json()["acoes_permitidas"]
    )
    acao(client, auth, c.aut, rid, "saida")
    for quem, esperado in ((c.adm, True), (c.aut, False), (c.sol, False)):
        j = get(client, auth, quem, f"/api/requisicoes/{rid}").get_json()
        assert ("ver_localizacao" in j["acoes_permitidas"]) is esperado
    assert get(client, auth, c.adm, f"/api/requisicoes/{rid}").get_json()["grupos_acao"][
        "consultar"
    ] == ["ver_localizacao"]


def test_ver_checklist_depende_do_recurso_e_do_papel(client, auth, repo, c, m, v):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "autorizada", motorista=None)
    assert (
        "ver_checklist"
        not in get(client, auth, c.aut, f"/api/requisicoes/{rid}").get_json()["acoes_permitidas"]
    )
    repo.config = Configuracoes(checklist=True, motorista=True)
    repo.requisicoes[rid]["motorista_id"] = m.a.id
    for quem, esperado in ((c.aut, True), (c.adm, True), (c.sol, False), (v.vig, False)):
        j = get(client, auth, quem, f"/api/requisicoes/{rid}").get_json()
        assert ("ver_checklist" in j["acoes_permitidas"]) is esperado, quem.role
    jm = get(client, auth, m.a, f"/api/motorista/viagens/{rid}").get_json()
    assert "ver_checklist" in jm["acoes_permitidas"]
    jb = get(client, auth, m.b, f"/api/motorista/viagens/{rid}")
    assert jb.status_code == 404
    # solicitada ainda não tem checklist possível
    r2 = levar_a(client, auth, repo, c, "solicitada", veiculo=repo.novo_veiculo())
    assert (
        "ver_checklist"
        not in get(client, auth, c.aut, f"/api/requisicoes/{r2}").get_json()["acoes_permitidas"]
    )


# ===================================================================== motorista padrão e lista
def test_motorista_unico_ativo_e_atribuido_sozinho_e_lista_vem_da_api(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True)
    repo.perfis[m.b.id] = type(m.b)(m.b.id, m.b.nome, m.b.unidade, "motorista", False)  # inativo
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    j = get(client, auth, c.aut, f"/api/requisicoes/{rid}").get_json()
    autorizar = j["campos_acao"]["autorizar"]
    assert autorizar["motoristas"] == [{"id": m.a.id, "nome": m.a.nome}]
    assert (
        autorizar["motorista_padrao"] == m.a.id and "motorista_id" in autorizar["campos_opcionais"]
    )
    r = acao(client, auth, c.aut, rid, "autorizar")  # sem motorista_id no corpo
    assert r.status_code == 200 and r.get_json()["motorista"] == {"id": m.a.id, "nome": m.a.nome}
    assert repo.requisicoes[rid]["motorista_id"] == m.a.id


def test_com_mais_de_um_motorista_o_usuario_escolhe(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True)
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    a = get(client, auth, c.aut, f"/api/requisicoes/{rid}").get_json()["campos_acao"]["autorizar"]
    assert {x["id"] for x in a["motoristas"]} == {m.a.id, m.b.id} and a["motorista_padrao"] is None
    r = acao(client, auth, c.aut, rid, "autorizar")
    assert r.status_code == 200 and r.get_json()["motorista"] is None  # nada presumido
    lista = get(client, auth, c.aut, "/api/motoristas").get_json()
    assert lista["padrao"] is None and {x["id"] for x in lista["itens"]} == {m.a.id, m.b.id}


def test_seletor_so_para_quem_pode_autorizar_e_sem_dados_pessoais(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True)
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    j = get(client, auth, c.sol, f"/api/requisicoes/{rid}").get_json()
    assert "autorizar" not in j["campos_acao"]  # o solicitante não recebe a lista de motoristas
    a = get(client, auth, c.aut, f"/api/requisicoes/{rid}").get_json()["campos_acao"]["autorizar"]
    assert all(set(x) == {"id", "nome"} for x in a["motoristas"])


def test_recurso_motorista_desligado_nao_oferece_nem_atribui(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    j = get(client, auth, c.aut, f"/api/requisicoes/{rid}").get_json()
    assert "autorizar" not in j["campos_acao"]
    r = acao(client, auth, c.aut, rid, "autorizar")
    assert r.status_code == 200 and repo.requisicoes[rid]["motorista_id"] is None
