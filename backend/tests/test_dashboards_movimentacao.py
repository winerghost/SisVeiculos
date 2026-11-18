"""Painel `movimentacao` (Movimentação e km): acesso, filtros repassados à RPC e km removido
com o recurso Quilometragem desligado (o backend é a fonte da verdade)."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

import pytest

URL = "/api/dashboards/movimentacao"


def get(client, auth, perfil, **params: Any):  # type: ignore[no-untyped-def]
    return client.get(URL, headers=auth(perfil.id), query_string=params)


def test_solicitante_403_e_sem_token_401(client, auth, c) -> None:  # type: ignore[no-untyped-def]
    assert client.get(URL).status_code == 401
    r = get(client, auth, c.sol)
    assert r.status_code == 403 and r.get_json()["erro"]["codigo"] == "sem_permissao"


@pytest.mark.parametrize("quem", ["aut", "adm"])
def test_listado_com_rotulo(client, auth, c, quem) -> None:  # type: ignore[no-untyped-def]
    paineis = client.get("/api/dashboards", headers=auth(getattr(c, quem).id)).get_json()
    assert {"chave": "movimentacao", "rotulo": "Movimentação e km"} in paineis["paineis"]


def test_km_ligado_mantem_km_rodado(client, auth, c, repo) -> None:  # type: ignore[no-untyped-def]
    repo.config = replace(repo.config, quilometragem=True)
    r = get(client, auth, c.aut)
    assert r.status_code == 200
    d = r.get_json()["dados"]
    assert d["quilometragem_habilitada"] is True
    assert d["serie"][0]["km_rodado"] == 120.5 and d["por_veiculo"][0]["km_rodado"] == 120.5
    assert d["granularidade"] == "dia" and d["fuso"] == "America/Sao_Paulo"


@pytest.mark.parametrize("rpc_devolve_km", [False, True])
def test_km_desligado_remove_km_rodado(  # type: ignore[no-untyped-def]
    client, auth, c, repo, rpc_devolve_km
) -> None:
    repo.movimentacao_km_sempre = rpc_devolve_km  # defesa: mesmo se a RPC mandar, sai fora
    d = get(client, auth, c.adm).get_json()["dados"]
    assert d["quilometragem_habilitada"] is False
    assert d["serie"] and all("km_rodado" not in x for x in d["serie"])
    assert d["por_veiculo"] and all("km_rodado" not in x for x in d["por_veiculo"])
    assert d["serie"][0]["saidas"] == 3 and d["serie"][0]["chegadas"] == 2


def test_filtros_repassados_a_rpc(client, auth, c, repo) -> None:  # type: ignore[no-untyped-def]
    vid = str(uuid.uuid4())
    r = get(
        client,
        auth,
        c.aut,
        inicio="2026-11-01",
        fim="2026-11-30",
        granularidade="semana",
        unidade="UEA",
        veiculo=vid,
    )
    assert r.status_code == 200
    assert r.get_json()["filtros"] == {
        "inicio": "2026-11-01",
        "fim": "2026-11-30",
        "fuso": "America/Sao_Paulo",
        "granularidade": "semana",
        "unidade": "UEA",
        "veiculo": vid,
    }
    nome, params = repo.chamadas_dash[-1]
    assert nome == "movimentacao"
    assert params == {
        "inicio": "2026-11-01T03:00:00+00:00",
        "fim": "2026-12-01T03:00:00+00:00",
        "unidade": "UEA",
        "veiculo": vid,
        "granularidade": "semana",
    }


@pytest.mark.parametrize(
    "params",
    [{"granularidade": "ano"}, {"unidade": "XX"}, {"veiculo": "abc"}, {"inicio": "2026-13-01"}],
)
def test_filtro_invalido_400(client, auth, c, params) -> None:  # type: ignore[no-untyped-def]
    assert get(client, auth, c.aut, **params).status_code == 400
