"""Testes da migration 20261204120100 (B4): reservar_envio_email recusa relatorio com observacao (AG027).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). Cada teste roda numa transacao desfeita no fim.

    pytest supabase/tests/test_email_relatorio_sem_observacao.py -m db_local
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261204120100_email_relatorio_sem_observacao.sql"
).read_text(encoding="utf-8")
SIG = "public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer, integer)"


@pytest.fixture
def e() -> Iterator[dict[str, Any]]:
    assert h.DSN
    adm = h.admin_ativo_qualquer() or h.criar_usuario("eso-admin", "admin")
    aut = h.criar_usuario("eso-aut", "autorizador")
    sol = h.criar_usuario("eso-sol", "solicitante")
    c = psycopg.connect(h.DSN)
    try:
        c.execute("update public.configuracoes set relatorio_email = true, alterado_por = %s", (adm,))
        yield {"c": c, "adm": adm, "aut": aut, "sol": sol}
    finally:
        c.rollback()
        c.close()


def _reservar(e: dict[str, Any], sol: uuid.UUID, dest: uuid.UUID, filtros: dict[str, Any]) -> dict[str, Any]:
    filtros = {**filtros, "n": uuid.uuid4().hex}  # sem dedupe entre chamadas
    return e["c"].execute(
        f"select {SIG.split('(')[0]}(%s, %s, 'movimentacao', 'csv', %s::jsonb, 1000, null)",
        (sol, dest, json.dumps(filtros)),
    ).fetchone()[0]


def _recusa(e: dict[str, Any], sol: uuid.UUID, dest: uuid.UUID, filtros: dict[str, Any]) -> "psycopg.Error":
    with pytest.raises(psycopg.Error) as ex:
        with e["c"].transaction():  # savepoint: a transacao segue utilizavel
            _reservar(e, sol, dest, filtros)
    return ex.value


def _n_envios(e: dict[str, Any], sol: uuid.UUID) -> int:
    return e["c"].execute(
        "select count(*) from public.envios_email where solicitado_por = %s", (sol,)
    ).fetchone()[0]


# --- estrutura ---------------------------------------------------------------------------------------------
def test_assinatura_unica_e_permissoes_mantidas() -> None:
    with h.conectar() as c:
        assert c.execute(
            "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
            "where n.nspname = 'public' and p.proname = 'reservar_envio_email'"
        ).fetchone() == (1,)
        for papel, esperado in (("anon", False), ("authenticated", False), ("service_role", True)):
            assert c.execute(
                "select has_function_privilege(%s, %s::regprocedure, 'execute')", (papel, SIG)
            ).fetchone() == (esperado,)
        assert c.execute(
            "select prosecdef and array_to_string(proconfig, ',') like '%%search_path=%%' "
            "from pg_proc where oid = %s::regprocedure",
            (SIG,),
        ).fetchone() == (True,)


def test_migration_e_idempotente() -> None:
    with h.reaplicando_migration() as c:
        c.execute(MIGRATION)
        c.execute(MIGRATION)


# --- recusa ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "filtros",
    [
        {"incluir_observacao": True},
        {"incluir_observacao": "true"},
        {"incluir_observacao": 1},
        {"incluir_observacao": True, "colunas": ["status"]},
        {"incluir_observacao": False, "colunas": ["status", "observacao"]},
        {"colunas": ["observacao"]},
    ],
)
@pytest.mark.parametrize("papel", ["adm", "aut", "sol"])
def test_recusa_observacao_para_qualquer_papel(e: dict[str, Any], filtros: dict[str, Any], papel: str) -> None:
    sol = e[papel]
    antes = _n_envios(e, sol)
    erro = _recusa(e, sol, sol, filtros)
    assert erro.sqlstate == "AG027"
    assert "observação" in str(erro)
    assert _n_envios(e, sol) == antes  # nada reservado, nenhuma cota gasta


def test_recusa_vem_antes_do_recurso_desligado(e: dict[str, Any]) -> None:
    e["c"].execute(
        "update public.configuracoes set relatorio_email = false, alterado_por = %s", (e["adm"],)
    )
    assert _recusa(e, e["aut"], e["aut"], {"incluir_observacao": True}).sqlstate == "AG027"


# --- aceito ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "filtros",
    [
        {},
        {"incluir_observacao": False},
        {"incluir_observacao": None},
        {"incluir_nomes": True, "incluir_trajeto": True, "colunas": ["status", "trajeto"]},
        {"incluir_observacao": False, "colunas": ["status"]},
    ],
)
def test_sem_observacao_reserva_normalmente(e: dict[str, Any], filtros: dict[str, Any]) -> None:
    r = _reservar(e, e["aut"], e["adm"], filtros)
    assert r["duplicado"] is False and uuid.UUID(r["id"])


def test_validacoes_anteriores_seguem(e: dict[str, Any]) -> None:
    # destinatario inexistente segue AG024; formato invalido segue AG027 (mensagem generica)
    assert _recusa(e, e["aut"], uuid.uuid4(), {}).sqlstate == "AG024"
    with pytest.raises(psycopg.Error) as ex:
        with e["c"].transaction():
            e["c"].execute(
                f"select {SIG.split('(')[0]}(%s, %s, 'movimentacao', 'pdf', '{{}}'::jsonb, 10, null)",
                (e["aut"], e["aut"]),
            )
    assert ex.value.sqlstate == "AG027" and "observação" not in str(ex.value)
