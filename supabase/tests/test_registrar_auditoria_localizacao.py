"""Testes da migration 20261113120300 (registrar_auditoria aceita 'localizacao_lida').

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). As chamadas rodam em transacao com rollback
(helper `como`); so a criacao dos perfis de apoio persiste.

    pytest supabase/tests/test_registrar_auditoria_localizacao.py -m db_local -p no:xdist
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261113120300_registrar_auditoria_localizacao_lida.sql"
).read_text(encoding="utf-8")

ASSINATURA = "public.registrar_auditoria(text, uuid, text, uuid, text, text, jsonb)"
CHAMADA = "select public.registrar_auditoria(%s, %s, %s, %s, null, null, %s::jsonb)"


def _registrar(
    ator: uuid.UUID | None,
    detalhes: dict | None = None,
    *,
    tabela: str = "requisicoes",
    acao: str = "localizacao_lida",
) -> object:
    with h.como("service_role") as c:
        return c.execute(
            CHAMADA, (tabela, uuid.uuid4(), acao, ator, json.dumps(detalhes or {}))
        ).fetchone()


def test_migration_estatica() -> None:
    assert "create or replace function public.registrar_auditoria(" in MIGRATION
    assert "'localizacao_lida'" in MIGRATION
    assert "security definer" in MIGRATION
    assert "set search_path = ''" in MIGRATION
    assert "to service_role" in MIGRATION


def test_admin_registra_localizacao_lida() -> None:
    admin = h.criar_usuario("adm-loc", role="admin")
    linha = _registrar(admin, {"requisicao_id": str(uuid.uuid4()), "itens": 3})
    assert linha is not None and isinstance(linha[0], int)


@pytest.mark.parametrize("role", ["autorizador", "solicitante"])
def test_nao_admin_recusado(role: str) -> None:
    uid = h.criar_usuario(f"{role}-loc", role=role)
    with pytest.raises(psycopg.errors.Error) as exc:
        _registrar(uid, {"itens": 1})
    assert h.sqlstate(exc) == "AG002"


def test_sem_ator_recusado() -> None:
    with pytest.raises(psycopg.errors.Error) as exc:
        _registrar(None, {"itens": 1})
    assert h.sqlstate(exc) == "AG002"


def test_tabela_diferente_de_requisicoes_recusada() -> None:
    admin = h.criar_usuario("adm-loc-tab", role="admin")
    with pytest.raises(psycopg.errors.Error) as exc:
        _registrar(admin, {"itens": 1}, tabela="perfis")
    assert h.sqlstate(exc) == "AG003"


@pytest.mark.parametrize(
    "detalhes",
    [
        {"latitude": -15.79},
        {"longitude": -47.88},
        {"precisao": 5},
        {"precisao_m": 5},
        {"itens": 1, "ponto": {"latitude": -15.79, "longitude": -47.88}},
    ],
)
def test_detalhes_com_coordenadas_recusado(detalhes: dict) -> None:
    admin = h.criar_usuario("adm-loc-coord", role="admin")
    with pytest.raises(psycopg.errors.Error) as exc:
        _registrar(admin, detalhes)
    assert h.sqlstate(exc) == "AG003"


def test_detalhes_nao_objeto_recusado() -> None:
    admin = h.criar_usuario("adm-loc-arr", role="admin")
    with h.como("service_role") as c, pytest.raises(psycopg.errors.Error) as exc:
        c.execute(CHAMADA, ("requisicoes", uuid.uuid4(), "localizacao_lida", admin, "[1]"))
    assert h.sqlstate(exc) == "AG003"


def test_acao_desconhecida_continua_recusada() -> None:
    admin = h.criar_usuario("adm-loc-desc", role="admin")
    with pytest.raises(psycopg.errors.Error) as exc:
        _registrar(admin, {}, acao="localizacao_xyz")
    assert h.sqlstate(exc) == "AG003"


def test_acoes_anteriores_seguem_aceitas() -> None:
    admin = h.criar_usuario("adm-loc-ant", role="admin")
    for acao in ("acao_administrativa", "acesso_negado"):
        assert _registrar(admin, {}, acao=acao) is not None


def test_grants_inalterados() -> None:
    with h.conectar() as c:
        (sec, cfg) = c.execute(
            "select prosecdef, proconfig from pg_proc where oid = to_regprocedure(%s)", (ASSINATURA,)
        ).fetchone() or (None, None)
        assert sec is True
        assert cfg is not None and 'search_path=""' in cfg
        for papel, esperado in (("anon", False), ("authenticated", False), ("service_role", True)):
            assert c.execute(
                "select has_function_privilege(%s, %s::regprocedure, 'execute')", (papel, ASSINATURA)
            ).fetchone() == (esperado,), papel
