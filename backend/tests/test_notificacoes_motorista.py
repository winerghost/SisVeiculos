"""Sino do motorista (somente sino): designação, troca, remoção, cancelamento, ator, recurso
desligado, e-mail nunca enfileirado, destino/texto sem PII e anti-IDOR.

A geração real é um trigger do banco (migration 20261113120000); aqui o fake emula o contrato
(`RepoMemoria.emular_*`) e o backend é testado de ponta a ponta (HTTP) em cima dele.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.config import Config
from app.modelos import Configuracoes
from app.notificacoes.despachante import Despachante
from app.services.notificacoes import (
    DESTINO_LISTA_MOTORISTA,
    DESTINO_VIAGEM_MOTORISTA,
    TEXTOS,
    TIPOS_SO_SINO,
    apresentar,
    montar_destino,
)
from tests.fakes_admin import AuthAdminMemoria
from tests.test_motorista import atribuir, get, viagem_autorizada
from tests.test_requisicoes import acao, criar

PII = ("João da Silva", "ABC1D23", "Rua das Flores", "@agu.gov.br", "Pessoa ")
TIPOS_MOT = ("motorista_designado", "motorista_removido", "viagem_cancelada_motorista")


@pytest.fixture
def m(repo, c):  # type: ignore[no-untyped-def]
    class M:
        a = repo.novo_perfil("motorista")
        b = repo.novo_perfil("motorista")

    repo.config = Configuracoes(motorista=True)
    return M


def do(repo: Any, quem: Any) -> list[dict[str, Any]]:
    return [n for n in repo.notificacoes if n["usuario_id"] == quem.id]


def tipos(repo: Any, quem: Any) -> list[str]:
    return [n["tipo"] for n in do(repo, quem)]


# -- designação ----------------------------------------------------------------------------------
def test_designado_na_autorizacao(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem_autorizada(client, auth, repo, c, m.a)
    assert tipos(repo, m.a) == ["motorista_designado"]
    n = do(repo, m.a)[0]
    assert n["requisicao_id"] == rid and n["critica"] is False and n["email_status"] is None
    assert tipos(repo, m.b) == []


def test_sem_motorista_na_autorizacao_ninguem_de_motorista_e_avisado(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    viagem_autorizada(client, auth, repo, c)
    assert tipos(repo, m.a) == tipos(repo, m.b) == []


def test_nova_requisicao_nao_notifica_motorista(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    assert criar(client, auth, c.sol, c.veiculo).status_code == 201
    assert do(repo, m.a) == do(repo, m.b) == []


def test_atribuicao_posterior_notifica(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem_autorizada(client, auth, repo, c)
    assert atribuir(client, auth, c.aut, rid, m.a.id).status_code == 200
    assert tipos(repo, m.a) == ["motorista_designado"]


def test_reatribuir_o_mesmo_motorista_e_idempotente(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem_autorizada(client, auth, repo, c, m.a)
    assert atribuir(client, auth, c.aut, rid, m.a.id).status_code == 200
    assert tipos(repo, m.a) == ["motorista_designado"]


def test_mesmo_evento_reprocessado_nao_duplica(repo, c, m):  # type: ignore[no-untyped-def]
    repo.emular_designacao("r1", None, m.a.id, c.aut.id, "ev-x")
    repo.emular_designacao("r1", None, m.a.id, c.aut.id, "ev-x")
    assert tipos(repo, m.a) == ["motorista_designado"]


# -- troca e remoção -----------------------------------------------------------------------------
def test_troca_avisa_novo_e_remove_do_antigo(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem_autorizada(client, auth, repo, c, m.a)
    assert atribuir(client, auth, c.aut, rid, m.b.id).status_code == 200
    assert tipos(repo, m.a) == ["motorista_designado", "motorista_removido"]
    assert tipos(repo, m.b) == ["motorista_designado"]


def test_remocao_avisa_o_antigo(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem_autorizada(client, auth, repo, c, m.a)
    assert atribuir(client, auth, c.aut, rid, None).status_code == 200
    assert tipos(repo, m.a) == ["motorista_designado", "motorista_removido"]
    assert tipos(repo, m.b) == []


# -- cancelamento --------------------------------------------------------------------------------
def test_cancelamento_avisa_o_motorista_designado(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem_autorizada(client, auth, repo, c, m.a)
    r = acao(client, auth, c.aut, rid, "cancelar")
    assert r.status_code == 200, r.get_json()
    assert tipos(repo, m.a) == ["motorista_designado", "viagem_cancelada_motorista"]
    assert tipos(repo, m.b) == []


def test_cancelamento_sem_motorista_nao_notifica_motorista(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem_autorizada(client, auth, repo, c)
    assert acao(client, auth, c.aut, rid, "cancelar").status_code == 200
    assert tipos(repo, m.a) == tipos(repo, m.b) == []


# -- ator e recurso ------------------------------------------------------------------------------
def test_ator_nao_e_notificado(repo, c, m):  # type: ignore[no-untyped-def]
    repo.emular_designacao("r1", None, m.a.id, m.a.id, "ev1")
    repo.emular_cancelamento("r1", m.a.id, m.a.id)
    assert do(repo, m.a) == []


def test_motorista_inativo_nao_recebe(repo, c):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True)
    inativo = repo.novo_perfil("motorista", ativo=False)
    repo.emular_designacao("r1", None, inativo.id, c.aut.id, "ev1")
    assert do(repo, inativo) == []


def test_recurso_desligado_nao_notifica(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem_autorizada(client, auth, repo, c, m.a)
    repo.config = Configuracoes(motorista=False)
    repo.notificacoes.clear()
    repo.emular_designacao(rid, m.a.id, m.b.id, c.aut.id, "ev9")
    repo.emular_cancelamento(rid, m.a.id, c.aut.id)
    assert repo.notificacoes == []


# -- e-mail nunca enfileirado ---------------------------------------------------------------------
def test_tipos_so_sino_sao_os_do_motorista():
    assert set(TIPOS_MOT) == TIPOS_SO_SINO


def test_email_nao_e_enfileirado_nem_enviado(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True, notificacao_email_criticos=True)
    rid = viagem_autorizada(client, auth, repo, c, m.a)
    atribuir(client, auth, c.aut, rid, m.b.id)
    acao(client, auth, c.aut, rid, "cancelar")
    mot = [n for n in repo.notificacoes if n["tipo"] in TIPOS_MOT]
    assert len(mot) == 4
    assert all(n["email_status"] is None and n["critica"] is False for n in mot)

    class Enviador:
        enviados: list[Any] = []

        def enviar(self, **kw: Any) -> None:
            self.enviados.append(kw)

    env = Enviador()
    cfg = Config(url_aceite_convite="https://app.exemplo.gov.br/convite")
    d = Despachante(repo, AuthAdminMemoria(repo), cfg, enviador=env)
    d.processar_uma_vez()
    assert all(e["destino"] not in {m.a.id, m.b.id} for e in env.enviados)  # nada para motoristas
    assert all(n["email_status"] is None for n in mot)


def test_despachante_ignora_tipo_so_sino_mesmo_se_chegasse_pendente(repo, m):  # type: ignore[no-untyped-def]
    n = repo.nova_notificacao(
        m.a.id,
        "motorista_designado",
        requisicao_id=str(uuid.uuid4()),
        critica=True,
        email_status="pendente",
    )
    repo.config = Configuracoes(notificacao_email_criticos=True)

    class Enviador:
        def __init__(self) -> None:
            self.enviados: list[Any] = []

        def enviar(self, **kw: Any) -> None:
            self.enviados.append(kw)

    env = Enviador()
    d = Despachante(repo, AuthAdminMemoria(repo), Config(), enviador=env)
    assert d.processar_uma_vez() == 0
    assert env.enviados == []
    assert n["email_status"] == "ignorado" and n["email_erro_codigo"] == "so_sino"


# -- API do sino: roles, IDOR, contrato ---------------------------------------------------------
def test_motorista_le_apenas_as_proprias(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    viagem_autorizada(client, auth, repo, c, m.a)
    r = get(client, auth, m.a, "/api/notificacoes")
    assert r.status_code == 200
    j = r.get_json()
    assert j["total"] == 1 and j["nao_lidas"] == 1
    assert j["itens"][0]["tipo"] == "motorista_designado"
    rb = get(client, auth, m.b, "/api/notificacoes").get_json()
    assert rb["total"] == 0 and rb["itens"] == []
    assert get(client, auth, m.a, "/api/notificacoes/contagem").get_json() == {"nao_lidas": 1}


def test_idor_motorista_nao_marca_lida_a_do_outro(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    viagem_autorizada(client, auth, repo, c, m.a)
    alheia = do(repo, m.a)[0]["id"]
    r = client.post(f"/api/notificacoes/{alheia}/lida", headers=auth(m.b.id))
    assert r.status_code == 404
    assert do(repo, m.a)[0]["lida_em"] is None
    ok = client.post(f"/api/notificacoes/{alheia}/lida", headers=auth(m.a.id))
    assert ok.status_code == 200


def test_destino_aponta_para_a_viagem_do_motorista(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    rid = viagem_autorizada(client, auth, repo, c, m.a)
    item = get(client, auth, m.a, "/api/notificacoes").get_json()["itens"][0]
    assert item["destino"] == f"{DESTINO_VIAGEM_MOTORISTA}{rid}"
    assert item["destino"].startswith("/motorista/detalhe?id=")


def test_destinos_allowlist():
    rid = str(uuid.uuid4())
    assert montar_destino("motorista_designado", rid) == f"/motorista/detalhe?id={rid}"
    assert montar_destino("viagem_cancelada_motorista", rid) == f"/motorista/detalhe?id={rid}"
    assert montar_destino("motorista_removido", rid) == DESTINO_LISTA_MOTORISTA == "/motorista"
    assert montar_destino("motorista_removido", None) == "/motorista"
    for t in ("motorista_designado", "viagem_cancelada_motorista"):
        assert montar_destino(t, None) is None
        assert montar_destino(t, "nao-uuid") is None
        assert montar_destino(t, f"{rid}&x=//evil.com") is None


@pytest.mark.parametrize("tipo", TIPOS_MOT)
def test_texto_fixo_sem_pii(tipo):  # type: ignore[no-untyped-def]
    assert tipo in TEXTOS
    base = {"id": str(uuid.uuid4()), "tipo": tipo, "criada_em": "2026-11-08T12:00:00+00:00"}
    v1 = apresentar({**base, "requisicao_id": str(uuid.uuid4()), "codigo": None})
    v2 = apresentar({**base, "requisicao_id": str(uuid.uuid4()), "codigo": "chegada"})
    assert (v1["titulo"], v1["texto"]) == (v2["titulo"], v2["texto"])  # código não altera o texto
    texto = f"{v1['titulo']} {v1['texto']}"
    assert "{" not in texto and "@" not in texto and not any(p in texto for p in PII)
