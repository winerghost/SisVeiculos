"""Testes da migration 20261113120400 (registrar_auditoria: lista branca em 'localizacao_lida').

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). As chamadas rodam em transacao com rollback
(helper `como`); so a criacao dos perfis de apoio persiste.

    pytest supabase/tests/test_registrar_auditoria_localizacao_whitelist.py -m db_local -p no:xdist
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
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20261113120400_registrar_auditoria_localizacao_whitelist.sql"
).read_text(encoding="utf-8")

ASSINATURA = "public.registrar_auditoria(text, uuid, text, uuid, text, text, jsonb)"
CHAMADA = "select public.registrar_auditoria(%s, %s, %s, %s, null, null, %s::jsonb)"


def _registrar(
    ator: uuid.UUID | None,
    detalhes: dict | None = None,
    *,
    tabela: str = "requisicoes",
    acao: str = "localizacao_lida",
    registro_id: uuid.UUID | None | str = "novo",
) -> object:
    rid = uuid.uuid4() if registro_id == "novo" else registro_id
    with h.como("service_role") as c:
        return c.execute(CHAMADA, (tabela, rid, acao, ator, json.dumps(detalhes or {}))).fetchone()


def test_migration_estatica() -> None:
    assert "create or replace function public.registrar_auditoria(" in MIGRATION
    assert "array['requisicao_id', 'itens']" in MIGRATION
    assert "p_registro_id is null" in MIGRATION
    assert "foreach v_acao in array" in MIGRATION
    assert "foreach v_def" not in MIGRATION
    assert "~*" not in MIGRATION.split("create or replace function", 1)[1].split("$$;", 2)[0]
    assert "security definer" in MIGRATION
    assert "set search_path = ''" in MIGRATION
    assert "to service_role" in MIGRATION


@pytest.mark.parametrize(
    "detalhes",
    [
        {"lat": -15.79},
        {"lng": -47.88},
        {"coords": [-15.79, -47.88]},
        {"altitude": 1000},
        {"precisao2": 5},
        {"precisao-m": 5},
        {"Latitude": -15.79},
        {"requisicao_id": "x", "itens": 1, "lat": 1},
        {"itens": 1, "ponto": {"latitude": -15.79, "longitude": -47.88}},
        {"Itens": 1},
    ],
)
def test_chaves_fora_da_lista_recusadas(detalhes: dict) -> None:
    admin = h.criar_usuario("adm-wl-chave", role="admin")
    with pytest.raises(psycopg.errors.Error) as exc:
        _registrar(admin, detalhes)
    assert h.sqlstate(exc) == "AG003"


def test_detalhes_nao_objeto_recusado() -> None:
    admin = h.criar_usuario("adm-wl-arr", role="admin")
    with h.como("service_role") as c, pytest.raises(psycopg.errors.Error) as exc:
        c.execute(CHAMADA, ("requisicoes", uuid.uuid4(), "localizacao_lida", admin, "[1]"))
    assert h.sqlstate(exc) == "AG003"


@pytest.mark.parametrize(
    "detalhes",
    [{"requisicao_id": str(uuid.uuid4()), "itens": 3}, {"requisicao_id": "x"}, {"itens": 2}, {}],
)
def test_lista_branca_aceita(detalhes: dict) -> None:
    admin = h.criar_usuario("adm-wl-ok", role="admin")
    linha = _registrar(admin, detalhes)
    assert linha is not None and isinstance(linha[0], int)


def test_registro_id_nulo_recusado() -> None:
    admin = h.criar_usuario("adm-wl-nulo", role="admin")
    with pytest.raises(psycopg.errors.Error) as exc:
        _registrar(admin, {"itens": 1}, registro_id=None)
    assert h.sqlstate(exc) == "AG003"


def test_admin_inativo_recusado() -> None:
    admin = h.criar_usuario("adm-wl-inativo", role="admin", ativo=False)
    with pytest.raises(psycopg.errors.Error) as exc:
        _registrar(admin, {"itens": 1})
    assert h.sqlstate(exc) == "AG002"


@pytest.mark.parametrize("role", ["autorizador", "solicitante"])
def test_nao_admin_recusado(role: str) -> None:
    uid = h.criar_usuario(f"{role}-wl", role=role)
    with pytest.raises(psycopg.errors.Error) as exc:
        _registrar(uid, {"itens": 1})
    assert h.sqlstate(exc) == "AG002"


def test_acoes_antigas_seguem_aceitas() -> None:
    admin = h.criar_usuario("adm-wl-ant", role="admin")
    for acao in ("acao_administrativa", "acesso_negado"):
        assert _registrar(admin, {}, acao=acao) is not None
    for acao, tabela in (("relatorio_gerado", "relatorios"), ("relatorio_enviado", "envios_email")):
        assert _registrar(admin, {"tipo": "movimentacao"}, acao=acao, tabela=tabela) is not None


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
