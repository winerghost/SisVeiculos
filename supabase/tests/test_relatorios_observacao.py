"""Testes da migration 20261119120000 (observacao opt-in nos relatorios).

Mesmas regras de test_relatorios.py (banco DESCARTAVEL; tudo em ROLLBACK):
    pytest supabase/tests/test_relatorios_observacao.py -m db_local -p no:xdist
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402
from test_relatorios import (  # noqa: E402,F401  (fixtures reaproveitadas)
    JANELA, chamar, cod, conn, dataset, flags, real, veiculo, req,
)

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261119120000_relatorios_observacao.sql"
).read_text(encoding="utf-8")

SEGREDO = "Obs com dado pessoal: Fulano 123"
MOV = ("select public.rel_movimentacao(%s::timestamptz, %s::timestamptz, 'criacao', null, %s, null,"
       " false, false, 5001, %s)")
KM = "select public.rel_opcionais_km(%s::timestamptz, %s::timestamptz, null, %s, null, 5001, %s)"
PEND = "select public.rel_pendencias(null, %s, 5001, null, false, %s)"


def _com_obs(c: "psycopg.Connection") -> "tuple":
    d = dataset(c)
    c.execute("update public.requisicoes set observacao = %s where id = %s", (SEGREDO, d.ids["R1"]))
    return d, d.ids["R1"]


def test_migration_e_idempotente(real: "psycopg.Connection") -> None:
    real.execute(MIGRATION)
    real.execute(MIGRATION)


def test_sem_sobrecarga_antiga() -> None:
    with h.conectar() as c:
        n = c.execute(
            "select count(*) from pg_proc p join pg_namespace s on s.oid = p.pronamespace "
            "where s.nspname = 'public' and p.proname in ('rel_movimentacao','rel_pendencias','rel_opcionais_km')"
        ).fetchone()
    assert n == (3,)


@pytest.mark.parametrize("sql", [MOV, KM, PEND])
def test_padrao_nao_expoe_observacao(conn: "psycopg.Connection", sql: str) -> None:
    flags(conn, quilometragem=True)
    d, _ = _com_obs(conn)
    r = chamar(conn, sql, *( (*JANELA, d.v, False) if sql is not PEND else (d.v, False) ))
    assert "observacao" not in r["colunas"]
    assert SEGREDO not in json.dumps(r) and all("observacao" not in l for l in r["linhas"])


def test_movimentacao_com_flag(conn: "psycopg.Connection") -> None:
    flags(conn, quilometragem=False)
    d, r1 = _com_obs(conn)
    r = chamar(conn, MOV, *JANELA, d.v, True)
    assert r["colunas"][-1] == "observacao" and r["colunas_opcionais"]["observacao"] is True
    por = {l["codigo"]: l for l in r["linhas"]}
    assert por[cod(r1)]["observacao"] == SEGREDO
    assert por[cod(d.ids["R2"])]["observacao"] is None  # sem observacao => null, chave presente
    assert all(set(l) == set(r["colunas"]) for l in r["linhas"])


def test_km_com_flag_e_recurso_desligado(conn: "psycopg.Connection") -> None:
    flags(conn, quilometragem=True)
    d, r1 = _com_obs(conn)
    r = chamar(conn, KM, *JANELA, d.v, True)
    assert r["colunas"][-1] == "observacao"
    assert {l["codigo"]: l["observacao"] for l in r["linhas"]}[cod(r1)] == SEGREDO
    assert set(r["linhas"][0]) == set(r["colunas"])
    flags(conn, quilometragem=False)  # recurso desligado vence a flag: nada vaza
    assert chamar(conn, KM, *JANELA, d.v, True) == {"habilitado": False}


def test_pendencias_com_flag(conn: "psycopg.Connection") -> None:
    v = veiculo(conn)
    rid = req(conn, v, "solicitada", "PU", criado_em="2026-03-05 10:00-03")
    conn.execute("update public.requisicoes set observacao = %s where id = %s", (SEGREDO, rid))
    r = chamar(conn, PEND, v, True)
    assert r["colunas"][-1] == "observacao" and r["linhas"][0]["observacao"] == SEGREDO
    nomes = chamar(conn, "select public.rel_pendencias(null, %s, 5001, null, true, true)", v)
    assert nomes["colunas"][-2:] == ["solicitante", "observacao"]


def test_chamadas_antigas_posicionais_continuam(conn: "psycopg.Connection") -> None:
    d, _ = _com_obs(conn)
    r = chamar(conn, "select public.rel_movimentacao(%s::timestamptz, %s::timestamptz, 'criacao', null, %s, null, true, true, 5001)",
               *JANELA, d.v)
    assert "observacao" not in r["colunas"] and SEGREDO not in json.dumps(r)


def test_resumo_tempos_auditoria_e_avarias_nao_mudaram() -> None:
    with h.conectar() as c:
        for f in ("rel_resumo", "rel_tempos", "rel_auditoria", "rel_opcionais_avarias"):
            args = c.execute(
                "select pg_get_function_identity_arguments(p.oid) from pg_proc p "
                "join pg_namespace n on n.oid = p.pronamespace where n.nspname='public' and p.proname=%s", (f,)
            ).fetchone()
            assert args is not None and "observacao" not in args[0]
