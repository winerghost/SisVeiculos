"""GET /api/requisicoes com filtros opcionais q/ordenar/veiculo_id/unidade.

Validação por allowlist (400 filtro_invalido), injeção no `q` (o termo nunca muda a estrutura
do filtro PostgREST), visibilidade inalterada (IDOR) e retrocompatibilidade sem parâmetros.
"""

from __future__ import annotations

from typing import Any

import pytest
from postgrest import SyncPostgrestClient

from app.repositorios.supabase_repo import _filtros_lista
from app.services.filtros_listagem import neutralizar_busca
from tests.semear import semear

URL = "/api/requisicoes"


def listar(client, auth, perfil, **params: Any):  # type: ignore[no-untyped-def]
    return client.get(URL, headers=auth(perfil.id), query_string=params)


def ids(resp: Any) -> list[str]:
    return [i["id"] for i in resp.get_json()["itens"]]


@pytest.fixture
def base(repo, c):  # type: ignore[no-untyped-def]
    """Três requisições de donos, veículos, unidades e horários diferentes."""
    v2 = repo.novo_veiculo()
    a = semear(
        repo,
        c.sol,
        c.veiculo,
        nome_solicitante="Maria Souza",
        trajeto="Sede -> TRF, 1ª Região",
        unidade="PU",
        hora_saida="2026-11-10T15:00:00+00:00",
        criado_em="2026-11-01T10:00:00+00:00",
    )
    b = semear(
        repo,
        c.sol,
        v2,
        nome_solicitante="João Lima",
        trajeto="Sede -> Fórum",
        unidade="CJU",
        status="concluida",
        hora_saida="2026-11-09T15:00:00+00:00",
        saida_real_em="2026-11-09T15:00:00+00:00",
        chegada_real_em="2026-11-09T17:00:00+00:00",
        criado_em="2026-11-02T10:00:00+00:00",
    )
    o = semear(
        repo,
        c.outro,
        repo.novo_veiculo(),
        nome_solicitante="Maria Outra",
        trajeto="Sede -> Aeroporto",
        unidade="UEA",
        status="negada",
        hora_saida="2026-11-11T15:00:00+00:00",
        criado_em="2026-11-03T10:00:00+00:00",
    )

    class B:
        pass

    B.a, B.b, B.o, B.v2 = a, b, o, v2  # type: ignore[attr-defined]
    return B


# ---------------------------------------------------------------- retrocompatibilidade
def test_sem_parametros_igual_ao_de_antes(client, auth, c, base, repo):  # type: ignore[no-untyped-def]
    r = listar(client, auth, c.aut)
    assert r.status_code == 200
    j = r.get_json()
    assert set(j) == {"itens", "total"} and j["total"] == 3
    assert repo.chamadas_listagem[-1] == {"busca": None, "ordem": ("criado_em", True)}
    # Solicitante sem filtros continua vendo só as próprias.
    assert set(ids(listar(client, auth, c.sol))) == {base.a, base.b}


@pytest.mark.parametrize("vazio", ["", "   "])
def test_parametro_vazio_e_ignorado(client, auth, c, base, vazio):  # type: ignore[no-untyped-def]
    r = listar(client, auth, c.aut, q=vazio, ordenar=vazio, veiculo_id=vazio, unidade=vazio)
    assert r.status_code == 200 and r.get_json()["total"] == 3


# ---------------------------------------------------------------- q
def test_busca_por_nome_ou_trajeto_sem_maiusculas(client, auth, c, base):  # type: ignore[no-untyped-def]
    j = listar(client, auth, c.aut, q="maria").get_json()
    assert set(i["id"] for i in j["itens"]) == {base.a, base.o} and j["total"] == 2
    j = listar(client, auth, c.aut, q="FÓRUM").get_json()
    assert [i["id"] for i in j["itens"]] == [base.b] and j["total"] == 1


def test_busca_com_virgula_e_parenteses_e_neutralizada(client, auth, c, base):  # type: ignore[no-untyped-def]
    j = listar(client, auth, c.aut, q="TRF, 1ª").get_json()
    assert [i["id"] for i in j["itens"]] == [base.a]


@pytest.mark.parametrize("q", ["a", "x" * 81, "ab\x00cd", "%%", "**", "(,)", "_ _"])
def test_busca_invalida_400(client, auth, c, base, q):  # type: ignore[no-untyped-def]
    r = listar(client, auth, c.aut, q=q)
    assert r.status_code == 400
    assert r.get_json()["erro"]["codigo"] == "filtro_invalido"


@pytest.mark.parametrize(
    "q",
    [
        "x,usuario_id.neq.0",
        "a),or(usuario_id.not.is.null",
        'Maria",status.eq.negada',
        "Maria\\*",
        "Maria%",
        "Ma_ia",
        "*Maria*",
    ],
)
def test_injecao_no_q_nao_amplia_visibilidade(client, auth, c, base, q):  # type: ignore[no-untyped-def]
    r = listar(client, auth, c.sol, q=q)
    assert r.status_code == 200
    assert set(ids(r)) <= {base.a, base.b}  # nunca a requisição de outro dono
    assert base.o not in ids(r)


@pytest.mark.parametrize(
    "bruto",
    [
        "x),usuario_id.neq.(1",
        'a",status.in.(negada)',
        "a\\),or(id.not.is.null",
        "a:b.c%d_e*f",
    ],
)
def test_busca_neutralizada_nao_altera_estrutura_do_filtro(bruto: str) -> None:
    busca = neutralizar_busca(bruto)
    assert busca is not None
    assert not set(busca) & set(',()"\\:%_')
    q = SyncPostgrestClient("http://localhost").from_("requisicoes").select("id")
    q = _filtros_lista(
        q,
        usuario_id="u1",
        status=None,
        motorista_id=None,
        ou_status=("autorizada", "em_uso"),
        ou_registrada_por="u1",
        busca=busca,
    )
    params = list(q.params.multi_items())
    # Um ÚNICO parâmetro `or`: dono/vigilante E busca, nunca um OR solto que amplie.
    assert [k for k, _ in params].count("or") == 1
    valor = dict(params)["or"]
    assert valor.startswith("(and(or(usuario_id.eq.u1,") and valor.count("(") == valor.count(")")
    assert f"nome_solicitante.ilike.*{busca}*,trajeto.ilike.*{busca}*" in valor


def test_busca_dono_simples_mantem_eq(repo) -> None:  # type: ignore[no-untyped-def]
    q = SyncPostgrestClient("http://localhost").from_("requisicoes").select("id")
    q = _filtros_lista(
        q,
        usuario_id="u1",
        status="solicitada",
        motorista_id=None,
        ou_status=None,
        ou_registrada_por=None,
        busca="abc",
        veiculo_id="v",
        unidade="PU",
    )
    p = dict(q.params.multi_items())
    assert p["usuario_id"] == "eq.u1" and p["status"] == "eq.solicitada"
    assert p["veiculo_id"] == "eq.v" and p["unidade"] == "eq.PU"
    assert p["or"] == "(nome_solicitante.ilike.*abc*,trajeto.ilike.*abc*)"


def test_busca_vigilante_respeita_visibilidade(client, auth, c, base, repo):  # type: ignore[no-untyped-def]
    vig = repo.novo_perfil("vigilante")
    vis = semear(repo, c.outro, repo.novo_veiculo(), status="autorizada", trajeto="Sede -> Porto")
    r = listar(client, auth, vig, q="Sede")
    assert r.status_code == 200
    assert ids(r) == [vis] and r.get_json()["total"] == 1  # negada/concluída alheias fora


def test_busca_motorista_so_as_atribuidas(client, auth, c, base, repo):  # type: ignore[no-untyped-def]
    from dataclasses import replace

    repo.config = replace(repo.config, motorista=True)
    mot = repo.novo_perfil("motorista")
    repo.requisicoes[base.b]["motorista_id"] = mot.id
    r = listar(client, auth, mot, q="Sede")
    assert ids(r) == [base.b] and r.get_json()["total"] == 1


# ---------------------------------------------------------------- ordenar
@pytest.mark.parametrize("ordenar", ["nome_solicitante", "criado_em;drop", "--criado_em", "id"])
def test_ordenar_fora_da_allowlist_400(client, auth, c, base, ordenar):  # type: ignore[no-untyped-def]
    r = listar(client, auth, c.aut, ordenar=ordenar)
    assert r.status_code == 400 and r.get_json()["erro"]["codigo"] == "filtro_invalido"


def test_ordenar_por_hora_saida_e_status(client, auth, c, base, repo):  # type: ignore[no-untyped-def]
    assert ids(listar(client, auth, c.aut, ordenar="hora_saida")) == [base.b, base.a, base.o]
    assert ids(listar(client, auth, c.aut, ordenar="-hora_saida")) == [base.o, base.a, base.b]
    assert ids(listar(client, auth, c.aut, ordenar="status")) == [base.a, base.b, base.o]
    assert ids(listar(client, auth, c.aut, ordenar="criado_em")) == [base.a, base.b, base.o]
    assert repo.chamadas_listagem[-1]["ordem"] == ("criado_em", False)


# ---------------------------------------------------------------- veiculo_id e unidade
@pytest.mark.parametrize("valor", ["abc", "1", "' or 1=1 --"])
def test_veiculo_id_invalido_400(client, auth, c, base, valor):  # type: ignore[no-untyped-def]
    r = listar(client, auth, c.aut, veiculo_id=valor)
    assert r.status_code == 400 and r.get_json()["erro"]["codigo"] == "filtro_invalido"


@pytest.mark.parametrize("valor", ["pu", "XX", "PU,PF"])
def test_unidade_invalida_400(client, auth, c, base, valor):  # type: ignore[no-untyped-def]
    r = listar(client, auth, c.aut, unidade=valor)
    assert r.status_code == 400 and r.get_json()["erro"]["codigo"] == "filtro_invalido"


def test_filtra_por_veiculo_e_unidade_com_total(client, auth, c, base):  # type: ignore[no-untyped-def]
    j = listar(client, auth, c.aut, veiculo_id=base.v2).get_json()
    assert [i["id"] for i in j["itens"]] == [base.b] and j["total"] == 1
    j = listar(client, auth, c.aut, unidade="UEA").get_json()
    assert [i["id"] for i in j["itens"]] == [base.o] and j["total"] == 1
    j = listar(client, auth, c.aut, unidade="PU", q="maria", status="solicitada").get_json()
    assert [i["id"] for i in j["itens"]] == [base.a] and j["total"] == 1


def test_filtro_por_unidade_nao_revela_alheias(client, auth, c, base):  # type: ignore[no-untyped-def]
    j = listar(client, auth, c.sol, unidade="UEA").get_json()
    assert j == {"itens": [], "total": 0}


def test_limite_maximo_inalterado(client, auth, c, base, repo):  # type: ignore[no-untyped-def]
    for _ in range(105):
        semear(repo, c.sol, repo.novo_veiculo(), trajeto="Sede -> Lote")
    j = listar(client, auth, c.aut, q="lote", limite=500).get_json()
    assert len(j["itens"]) == 100 and j["total"] == 105
