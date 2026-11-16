"""Bloco `resumo` do webhook n8n (exceção LGPD aprovada em 2026-11-15).

Todo evento `requisicao.*` leva {nome_solicitante, hora_saida (UTC), hora_saida_fmt (Brasília),
trajeto}; `checklist.registrado` não leva; `observacao` nunca entra; falha omite o bloco; nunca
aparece em resposta da API nem em log. Não altera `versao`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.fuso import FUSO
from app.modelos import Configuracoes
from app.notificacoes.n8n import CAMPOS_RESUMO, montar_payload
from app.services.requisicoes import ServicoRequisicoes, montar_resumo_webhook
from tests.fakes import RepoMemoria
from tests.test_notificacoes import Gravador, esperar, montar_app, req_exemplo, webhook
from tests.test_requisicoes import acao, corpo, criar

OBS = "obs-sensivel-resumo"
NOME = "Fulano de Tal"
TRAJETO = "AGU -> Fórum"


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
        veiculo = repo.novo_veiculo()

    return C


def _hora_virada() -> tuple[str, str, str]:
    """Amanhã às 22:30 em Brasília = 01:30 UTC do dia seguinte (virada de dia UTC)."""
    amanha = (datetime.now(UTC) + timedelta(days=2)).astimezone(FUSO).date()
    local = datetime(amanha.year, amanha.month, amanha.day, 22, 30, tzinfo=FUSO)
    utc = local.astimezone(UTC)
    assert utc.date() != local.date()
    return local.isoformat(), utc.isoformat(), f"{local:%d/%m/%Y} 22:30"


def _esperado(hora_utc: str, fmt: str) -> dict[str, Any]:
    return {
        "nome_solicitante": NOME,
        "hora_saida": hora_utc,
        "hora_saida_fmt": fmt,
        "trajeto": TRAJETO,
    }


# ------------------------------------------------------------------ função pura
def test_formata_no_fuso_de_brasilia_com_virada_de_dia_utc() -> None:
    r = montar_resumo_webhook(
        {
            "nome_solicitante": NOME,
            "hora_saida": "2026-11-17T01:30:00+00:00",
            "trajeto": TRAJETO,
            "observacao": OBS,
        }
    )
    assert r == _esperado("2026-11-17T01:30:00+00:00", "16/11/2026 22:30")


def test_aceita_datetime_e_offset_local_e_normaliza_para_utc() -> None:
    r1 = montar_resumo_webhook(
        {"nome_solicitante": NOME, "hora_saida": "2026-11-16T14:30:00-03:00", "trajeto": TRAJETO}
    )
    r2 = montar_resumo_webhook(
        {
            "nome_solicitante": NOME,
            "hora_saida": datetime(2026, 11, 16, 17, 30, tzinfo=UTC),
            "trajeto": TRAJETO,
        }
    )
    assert r1 == r2 == _esperado("2026-11-16T17:30:00+00:00", "16/11/2026 14:30")


@pytest.mark.parametrize(
    "req",
    [
        {"nome_solicitante": NOME, "trajeto": TRAJETO},
        {"nome_solicitante": NOME, "hora_saida": "lixo", "trajeto": TRAJETO},
        {"nome_solicitante": NOME, "hora_saida": "2026-11-16T14:30:00", "trajeto": TRAJETO},
    ],
)
def test_dados_invalidos_lancam(req: dict[str, Any]) -> None:
    with pytest.raises((KeyError, ValueError)):
        montar_resumo_webhook(req)


def test_payload_usa_allowlist_e_nao_altera_versao() -> None:
    resumo = {**_esperado("x", "y"), "observacao": OBS, "extra": 1}
    p = montar_payload("requisicao.autorizada", req_exemplo(), resumo=resumo)
    assert p["versao"] == 1 and list(p["resumo"]) == list(CAMPOS_RESUMO)
    assert OBS not in json.dumps(p) and "extra" not in p["resumo"]
    p2 = montar_payload("requisicao.saida", req_exemplo(), detalhes={"a": 1}, resumo=resumo)
    assert p2["versao"] == 2 and "resumo" in p2
    assert "resumo" not in montar_payload("requisicao.criada", req_exemplo())


def test_checklist_registrado_nunca_leva_resumo_no_payload() -> None:
    p = montar_payload("checklist.registrado", req_exemplo(), resumo=_esperado("x", "y"))
    assert "resumo" not in p


# ------------------------------------------------------------------ fluxo da API
def test_resumo_em_todos_os_seis_eventos(client_n, auth, grav, cen):  # type: ignore[no-untyped-def]
    local, utc, fmt = _hora_virada()
    r1 = criar(client_n, auth, cen.sol, cen.veiculo, hora_saida=local, observacao=OBS)
    rid = r1.get_json()["id"]
    acao(client_n, auth, cen.aut, rid, "autorizar")
    acao(client_n, auth, cen.aut, rid, "saida")
    acao(client_n, auth, cen.aut, rid, "chegada")
    r2 = criar(client_n, auth, cen.sol, cen.veiculo, hora_saida=local, observacao=OBS)
    acao(client_n, auth, cen.aut, r2.get_json()["id"], "negar")
    r3 = criar(client_n, auth, cen.sol, cen.veiculo, hora_saida=local, observacao=OBS)
    acao(client_n, auth, cen.sol, r3.get_json()["id"], "cancelar")
    assert set(grav.eventos) == {
        "requisicao.criada",
        "requisicao.autorizada",
        "requisicao.saida",
        "requisicao.chegada",
        "requisicao.negada",
        "requisicao.cancelada",
    }
    assert len(grav.resumos) == len(grav.eventos) == 8
    for resumo in grav.resumos:
        assert resumo == _esperado(utc, fmt)
        assert OBS not in json.dumps(resumo)


def test_motorista_via_rpc_tambem_leva_resumo(repo):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True, motorista_registra=True)
    sol, aut = repo.novo_perfil("solicitante"), repo.novo_perfil("autorizador")
    mot = repo.novo_perfil("motorista")
    veiculo = repo.novo_veiculo()
    grav = Gravador()
    svc = ServicoRequisicoes(repo, notificador=grav)
    local, utc, fmt = _hora_virada()
    rid = svc.criar(sol, corpo(veiculo, hora_saida=local, observacao=OBS))["id"]
    svc.executar("autorizar", aut, rid, {"motorista_id": mot.id})
    svc.executar("registrar_saida", mot, rid, {})
    svc.executar("registrar_chegada", mot, rid, {})
    assert grav.eventos[-2:] == ["requisicao.saida", "requisicao.chegada"]
    assert grav.resumos[-2:] == [_esperado(utc, fmt)] * 2


def test_visao_reduzida_rele_a_linha_completa(repo, cen):  # type: ignore[no-untyped-def]
    svc = ServicoRequisicoes(repo, notificador=Gravador())
    local, utc, fmt = _hora_virada()
    rid = svc.criar(cen.sol, corpo(cen.veiculo, hora_saida=local))["id"]
    reduzida = {"id": rid, "status": "em_uso", "veiculo_id": cen.veiculo}
    assert svc._resumo_webhook(reduzida, "requisicao.saida") == _esperado(utc, fmt)


def test_falha_de_leitura_omite_o_bloco_e_loga_sem_pii(  # type: ignore[no-untyped-def]
    client_n, auth, grav, repo, cen, caplog, monkeypatch
):
    caplog.set_level(logging.DEBUG)
    rid = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]

    def quebra(requisicao_id: str) -> dict[str, Any]:
        raise RuntimeError(f"{NOME} {TRAJETO}")

    monkeypatch.setattr(repo, "obter_requisicao_completa", quebra)
    original = repo.transicionar

    def sem_trajeto(*a, **kw):  # type: ignore[no-untyped-def]
        r = original(*a, **kw)
        return {k: v for k, v in r.items() if k != "trajeto"} if r else r

    monkeypatch.setattr(repo, "transicionar", sem_trajeto)
    r = acao(client_n, auth, cen.aut, rid, "autorizar")
    assert r.status_code == 200
    assert grav.eventos[-1] == "requisicao.autorizada" and grav.resumos[-1] is None
    assert grav.contatos[-1] is not None  # o resto do evento segue
    assert "n8n: resumo indisponivel evento=requisicao.autorizada (RuntimeError)" in caplog.text
    assert NOME not in caplog.text and TRAJETO not in caplog.text


def test_checklist_registrado_sem_resumo_no_fluxo(client_n, auth, grav, repo, cen):  # type: ignore[no-untyped-def]
    repo.config = replace(repo.ler_configuracoes(), checklist=True)
    rid = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]
    acao(client_n, auth, cen.aut, rid, "autorizar")
    r = client_n.post(
        f"/api/requisicoes/{rid}/checklist/saida",
        headers=auth(cen.aut.id),
        json={"combustivel": "cheio", "avarias": False, "fotos": []},
    )
    assert r.status_code == 201, r.get_json()
    assert grav.eventos[-1] == "checklist.registrado" and grav.resumos[-1] is None


def test_resumo_nunca_na_resposta_da_api_nem_em_log(  # type: ignore[no-untyped-def]
    client_n, auth, grav, cen, caplog
):
    caplog.set_level(logging.DEBUG)
    local, _utc, fmt = _hora_virada()
    respostas = [criar(client_n, auth, cen.sol, cen.veiculo, hora_saida=local)]
    rid = respostas[0].get_json()["id"]
    for nome in ("autorizar", "saida", "chegada"):
        respostas.append(acao(client_n, auth, cen.aut, rid, nome))
    respostas.append(client_n.get(f"/api/requisicoes/{rid}", headers=auth(cen.aut.id)))
    respostas.append(client_n.get("/api/requisicoes", headers=auth(cen.aut.id)))
    assert all(g is not None for g in grav.resumos)
    for r in respostas:
        assert r.status_code < 300
        texto = r.get_data(as_text=True)
        assert '"resumo"' not in texto and "hora_saida_fmt" not in texto and fmt not in texto
    assert "resumo" not in caplog.text and fmt not in caplog.text


def test_payload_http_assinado_leva_resumo() -> None:
    recebidos: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        recebidos.append(req)
        return httpx.Response(200)

    n = webhook(h)
    resumo = _esperado("2026-11-17T01:30:00+00:00", "16/11/2026 22:30")
    n.notificar("requisicao.criada", req_exemplo(), resumo=resumo)
    assert esperar(lambda: len(recebidos) == 1)
    n.fechar()
    corpo_http = json.loads(recebidos[0].content)
    assert corpo_http["resumo"] == resumo and corpo_http["versao"] == 1
