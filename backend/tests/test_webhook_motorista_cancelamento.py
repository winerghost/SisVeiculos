"""Webhook n8n: `requisicao.motorista_alterado` (M1, opção A1) e bloco `cancelamento` (B1, opção 2).

Aprovados pelo dono em 2026-11-16. Nenhum dos blocos novos leva dado pessoal: o motorista novo
sai só em `contatos` (exceção já aprovada) e o anterior nunca sai. Allowlist fixa por evento.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any

import httpx
import pytest

from app.notificacoes import EVENTOS
from app.notificacoes.n8n import BLOCOS_POR_EVENTO, montar_payload
from tests.fakes import RepoMemoria
from tests.test_notificacoes import Gravador, esperar, montar_app, req_exemplo, webhook
from tests.test_requisicoes import acao, criar

EVENTO = "requisicao.motorista_alterado"


@pytest.fixture
def grav() -> Gravador:
    return Gravador()


@pytest.fixture
def client_n(repo, auth_admin, storage, grav):  # type: ignore[no-untyped-def]
    return montar_app(repo, grav, auth_admin, storage).test_client()


@pytest.fixture
def cen(repo: RepoMemoria):  # type: ignore[no-untyped-def]
    class C:
        sol = repo.novo_perfil("solicitante")
        aut = repo.novo_perfil("autorizador")
        m1 = repo.novo_perfil("motorista")
        m2 = repo.novo_perfil("motorista")  # 2 ativos: sem motorista padrão na autorização
        veiculo = repo.novo_veiculo()

    repo.telefones[C.sol.id] = "5561911112222"
    repo.telefones[C.m1.id] = "5561933334444"
    repo.telefones[C.m2.id] = "556155556666"
    return C


def atribuir(client, auth, quem, rid, motorista_id):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/requisicoes/{rid}/motorista",
        headers=auth(quem.id),
        json={"motorista_id": motorista_id},
    )


def _ligar_motorista(repo: RepoMemoria) -> None:
    repo.config = replace(repo.ler_configuracoes(), motorista=True)


def _autorizada(client, auth, cen) -> str:  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cen.sol, cen.veiculo).get_json()["id"]
    assert acao(client, auth, cen.aut, rid, "autorizar").status_code == 200
    return rid


def _ultimo(grav: Gravador) -> tuple[str, dict[str, Any], Any, Any, Any]:
    evento, req = grav.chamadas[-1]
    return evento, req, grav.contatos[-1], grav.resumos[-1], grav.blocos[-1]


# ------------------------------------------------------------------ M1: motorista_alterado
def test_evento_novo_esta_no_conjunto_e_na_allowlist() -> None:
    assert EVENTO in EVENTOS
    assert BLOCOS_POR_EVENTO[EVENTO] == {"motorista_alteracao": ("tipo", "status")}


def test_designacao_troca_e_remocao_geram_evento(client_n, auth, grav, repo, cen):  # type: ignore[no-untyped-def]
    _ligar_motorista(repo)
    rid = _autorizada(client_n, auth, cen)
    assert grav.contatos[-1]["motorista"] is None  # autorizada sem motorista (2 ativos)

    assert atribuir(client_n, auth, cen.aut, rid, cen.m1.id).status_code == 200
    evento, req, contatos, resumo, blocos = _ultimo(grav)
    assert evento == EVENTO and req["id"] == rid
    assert blocos == {"motorista_alteracao": {"tipo": "designado", "status": "autorizada"}}
    assert contatos["motorista"]["id"] == cen.m1.id
    assert contatos["motorista"]["telefone"] == "5561933334444"
    assert contatos["solicitante"]["id"] == cen.sol.id
    assert resumo is not None and resumo["trajeto"]

    assert atribuir(client_n, auth, cen.aut, rid, cen.m2.id).status_code == 200
    _, _, contatos, _, blocos = _ultimo(grav)
    assert blocos == {"motorista_alteracao": {"tipo": "trocado", "status": "autorizada"}}
    assert contatos["motorista"]["id"] == cen.m2.id
    # A1: o motorista anterior não sai em lugar nenhum do evento.
    assert cen.m1.id not in json.dumps([contatos, blocos], default=str)
    assert "5561933334444" not in json.dumps([contatos, blocos], default=str)

    assert acao(client_n, auth, cen.aut, rid, "saida").status_code == 200
    assert atribuir(client_n, auth, cen.aut, rid, None).status_code == 200
    evento, _, contatos, resumo, blocos = _ultimo(grav)
    assert evento == EVENTO
    assert blocos == {"motorista_alteracao": {"tipo": "removido", "status": "em_uso"}}
    assert contatos["motorista"] is None
    assert resumo is not None

    assert grav.eventos == [
        "requisicao.criada",
        "requisicao.autorizada",
        EVENTO,
        EVENTO,
        "requisicao.saida",
        EVENTO,
    ]


def test_designacao_em_uso(client_n, auth, grav, repo, cen):  # type: ignore[no-untyped-def]
    _ligar_motorista(repo)
    rid = _autorizada(client_n, auth, cen)
    acao(client_n, auth, cen.aut, rid, "saida")
    assert atribuir(client_n, auth, cen.aut, rid, cen.m1.id).status_code == 200
    assert _ultimo(grav)[4] == {"motorista_alteracao": {"tipo": "designado", "status": "em_uso"}}


def test_sem_mudanca_e_recusas_nao_notificam(client_n, auth, grav, repo, cen):  # type: ignore[no-untyped-def]
    _ligar_motorista(repo)
    rid = _autorizada(client_n, auth, cen)
    atribuir(client_n, auth, cen.aut, rid, cen.m1.id)
    n = len(grav.chamadas)
    assert atribuir(client_n, auth, cen.aut, rid, cen.m1.id).status_code == 200  # sem mudança
    assert atribuir(client_n, auth, cen.sol, rid, cen.m2.id).status_code == 403  # sem papel
    assert atribuir(client_n, auth, cen.aut, rid, cen.sol.id).status_code == 400  # não motorista
    assert len(grav.chamadas) == n
    pendente = criar(client_n, auth, cen.aut, repo.novo_veiculo()).get_json()["id"]
    n = len(grav.chamadas)  # a criação acima notifica; dela em diante, nada
    assert atribuir(client_n, auth, cen.aut, pendente, cen.m1.id).status_code == 409  # solicitada
    repo.atribuir_motorista = lambda *a, **k: None  # type: ignore[method-assign]
    assert atribuir(client_n, auth, cen.aut, rid, cen.m2.id).status_code == 409  # concorrência
    assert len(grav.chamadas) == n


def test_recurso_desligado_recusa_sem_notificar(client_n, auth, grav, repo, cen):  # type: ignore[no-untyped-def]
    _ligar_motorista(repo)
    rid = _autorizada(client_n, auth, cen)
    repo.config = replace(repo.ler_configuracoes(), motorista=False)
    n = len(grav.chamadas)
    r = atribuir(client_n, auth, cen.aut, rid, cen.m1.id)
    assert r.status_code >= 400
    assert len(grav.chamadas) == n


def test_log_sem_pii_quando_contatos_falham(client_n, auth, grav, repo, cen, caplog):  # type: ignore[no-untyped-def]
    _ligar_motorista(repo)
    rid = _autorizada(client_n, auth, cen)

    def quebra(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(f"falha com {cen.m1.nome} 5561933334444")

    repo.contatos_perfis = quebra  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING):
        assert atribuir(client_n, auth, cen.aut, rid, cen.m1.id).status_code == 200
    assert grav.eventos[-1] == EVENTO and grav.contatos[-1] is None
    assert "5561933334444" not in caplog.text and cen.m1.nome not in caplog.text


# ------------------------------------------------------------------ B1: cancelamento
def test_cancelamento_origem_e_status_anterior(client_n, auth, grav, repo, cen):  # type: ignore[no-untyped-def]
    casos: list[tuple[Any, bool, dict[str, str]]] = [
        (cen.sol, False, {"origem": "solicitante", "status_anterior": "solicitada"}),
        (cen.aut, False, {"origem": "equipe", "status_anterior": "solicitada"}),
        (cen.aut, True, {"origem": "equipe", "status_anterior": "autorizada"}),
    ]
    for quem, autorizar, esperado in casos:
        rid = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]
        if autorizar:
            acao(client_n, auth, cen.aut, rid, "autorizar")
        assert acao(client_n, auth, quem, rid, "cancelar").status_code == 200
        evento, _, contatos, _, blocos = _ultimo(grav)
        assert evento == "requisicao.cancelada"
        assert blocos == {"cancelamento": esperado}
        assert contatos["solicitante"]["telefone"] == "5561911112222"


def test_equipe_cancelando_a_propria_conta_como_solicitante(client_n, auth, grav, cen):  # type: ignore[no-untyped-def]
    rid = criar(client_n, auth, cen.aut, cen.veiculo).get_json()["id"]
    assert acao(client_n, auth, cen.aut, rid, "cancelar").status_code == 200
    assert grav.blocos[-1] == {
        "cancelamento": {"origem": "solicitante", "status_anterior": "solicitada"}
    }


def test_demais_eventos_nao_levam_blocos(client_n, auth, grav, cen):  # type: ignore[no-untyped-def]
    rid = _autorizada(client_n, auth, cen)
    acao(client_n, auth, cen.aut, rid, "saida")
    acao(client_n, auth, cen.aut, rid, "chegada")
    r2 = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]
    acao(client_n, auth, cen.aut, r2, "negar")
    assert all(b is None for b in grav.blocos)


def test_blocos_nao_aparecem_na_resposta_da_api(client_n, auth, repo, cen):  # type: ignore[no-untyped-def]
    _ligar_motorista(repo)
    rid = _autorizada(client_n, auth, cen)
    r1 = atribuir(client_n, auth, cen.aut, rid, cen.m1.id)
    r2 = acao(client_n, auth, cen.aut, rid, "cancelar")
    for r in (r1, r2):
        texto = r.get_data(as_text=True)
        assert "motorista_alteracao" not in texto and '"cancelamento"' not in texto


# ------------------------------------------------------------------ montar_payload (allowlist)
def test_allowlist_descarta_campos_e_blocos_de_outro_evento() -> None:
    blocos = {
        "cancelamento": {"origem": "equipe", "status_anterior": "autorizada", "cancelada_por": "x"},
        "motorista_alteracao": {"tipo": "trocado", "status": "autorizada", "anterior": "y"},
        "intruso": {"a": 1},
    }
    p = montar_payload("requisicao.cancelada", req_exemplo(), blocos=blocos)
    assert p["cancelamento"] == {"origem": "equipe", "status_anterior": "autorizada"}
    assert "motorista_alteracao" not in p and "intruso" not in p and p["versao"] == 1
    p = montar_payload(EVENTO, req_exemplo(), blocos=blocos)
    assert p["motorista_alteracao"] == {"tipo": "trocado", "status": "autorizada"}
    assert "cancelamento" not in p and "intruso" not in p and p["versao"] == 1
    for evento in EVENTOS - set(BLOCOS_POR_EVENTO):
        p = montar_payload(evento, req_exemplo(), blocos=blocos)
        assert not {"cancelamento", "motorista_alteracao", "intruso"} & set(p)
    assert "cancelamento" not in montar_payload("requisicao.cancelada", req_exemplo())


def test_payload_http_assinado_leva_blocos() -> None:
    recebidos: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        recebidos.append(req)
        return httpx.Response(200)

    n = webhook(h)
    bloco = {"motorista_alteracao": {"tipo": "designado", "status": "em_uso"}}
    n.notificar(EVENTO, req_exemplo(), blocos=bloco)
    assert esperar(lambda: len(recebidos) == 1)
    n.fechar()
    corpo = json.loads(recebidos[0].content)
    assert (
        corpo["evento"] == EVENTO and corpo["motorista_alteracao"] == bloco["motorista_alteracao"]
    )
