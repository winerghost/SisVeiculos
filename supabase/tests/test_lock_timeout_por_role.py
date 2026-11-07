"""Testes das migrations 16 (lock_timeout por role da API) e 17 (reload do PostgREST).

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.
Requer role de conexao capaz de ALTER ROLE (ex.: postgres do `supabase start`).

    pytest supabase/tests/test_lock_timeout_por_role.py -m db_local
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL_16 = (MIGRATIONS / "20261107121600_lock_timeout_por_role.sql").read_text(encoding="utf-8")
SQL_17 = (MIGRATIONS / "20261107121700_recarrega_postgrest.sql").read_text(encoding="utf-8")

ROLES_API = ("service_role", "authenticated", "anon")
CHAVE_LOCK = "hashtextextended('sisveiculos.perfis_admin', 0)"


def _config(role: str) -> list[str]:
    with h.conectar() as c:
        row = c.execute("select rolconfig from pg_roles where rolname = %s", (role,)).fetchone()
    assert row is not None, role
    return list(row[0] or [])


def _executar(sql: str) -> None:
    with h.conectar() as c:
        c.execute(sql)


def _snapshot() -> dict:
    """Estado que as migrations 16/17 NAO podem alterar (exceto lock_timeout das roles da API)."""
    with h.conectar() as c:
        roles = c.execute("select rolname, rolconfig from pg_roles order by rolname").fetchall()
        configs = {}
        for nome, cfg in roles:
            itens = sorted(cfg or [])
            if nome in ROLES_API:
                itens = [i for i in itens if not i.startswith("lock_timeout=")]
            configs[nome] = itens
        policies = c.execute(
            "select schemaname, tablename, policyname, permissive, roles::text, cmd, qual, with_check "
            "from pg_policies order by 1, 2, 3"
        ).fetchall()
        rls = c.execute(
            "select c.oid::regclass::text, c.relrowsecurity, c.relforcerowsecurity from pg_class c "
            "join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relkind in ('r', 'p') order by 1"
        ).fetchall()
        privs = c.execute(
            "select c.oid::regclass::text, r, p, has_table_privilege(r, c.oid, p) "
            "from pg_class c join pg_namespace n on n.oid = c.relnamespace, "
            "unnest(array['anon','authenticated','service_role']) r, "
            "unnest(array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) p "
            "where n.nspname = 'public' and c.relkind in ('r', 'p') order by 1, 2, 3"
        ).fetchall()
    return {"configs": configs, "policies": policies, "rls": rls, "privs": privs}


@pytest.mark.parametrize("role", ROLES_API)
def test_roles_da_api_tem_lock_timeout_5s(role: str) -> None:
    assert "lock_timeout=5s" in _config(role)


@pytest.mark.parametrize("role", ["authenticator", "postgres"])
def test_roles_administrativas_nao_ganham_lock_timeout_desta_migration(role: str) -> None:
    """Tolerante a configuracao propria do ambiente: so falha se vier o valor desta migration."""
    assert "lock_timeout=5s" not in _config(role)


def test_reexecutar_migration_16_nao_duplica_entrada() -> None:
    _executar(SQL_16)
    _executar(SQL_16)
    for role in ROLES_API:
        entradas = [i for i in _config(role) if i.startswith("lock_timeout=")]
        assert entradas == ["lock_timeout=5s"], (role, entradas)


def test_migrations_16_e_17_nao_mudam_rolconfig_nao_relacionado_rls_nem_grants() -> None:
    antes = _snapshot()
    _executar(SQL_16)
    _executar(SQL_16)
    _executar(SQL_17)
    _executar(SQL_17)
    depois = _snapshot()
    assert depois["configs"] == antes["configs"]
    assert depois["policies"] == antes["policies"]
    assert depois["rls"] == antes["rls"]
    assert depois["privs"] == antes["privs"]
    # Deny-by-default segue valendo: toda tabela do schema public com RLS ligado.
    assert all(ligado for _, ligado, _ in depois["rls"]), depois["rls"]


def test_migration_17_so_tem_notify() -> None:
    """Guarda contra mudanca de escopo: a 17 nao contem DDL/DML alem de NOTIFY."""
    comandos = [
        linha.strip().lower()
        for linha in SQL_17.splitlines()
        if linha.strip() and not linha.strip().startswith("--")
    ]
    assert comandos, "migration 17 vazia"
    assert all(c.startswith("notify pgrst,") for c in comandos), comandos


def test_lock_timeout_expira_com_55p03_quando_advisory_esta_seguro() -> None:
    """Comportamento: com o advisory de perfis seguro por outra sessao, uma escrita em perfis
    que aplica o lock_timeout de 5s recebe LockNotAvailable (55P03).

    SET ROLE nao aplica rolconfig (so login e PostgREST), entao o valor e simulado com set_config,
    equivalente ao que o PostgREST faz ao assumir a role.
    """
    with psycopg.connect(h.DSN) as segura:
        segura.execute(f"select pg_advisory_xact_lock({CHAVE_LOCK})")
        try:
            with psycopg.connect(h.DSN) as outra:
                outra.execute("select set_config('lock_timeout', '5s', false)")
                inicio = time.monotonic()
                with pytest.raises(psycopg.errors.LockNotAvailable) as exc:
                    # Update de zero linhas: o gatilho de comando ja pede o advisory (migration 14).
                    outra.execute(
                        "update public.perfis set nome = 'Z' where id = %s", (uuid.uuid4(),)
                    )
                espera = time.monotonic() - inicio
                outra.rollback()
            assert h.sqlstate(exc) == "55P03"
            # Limite superior folgado: a VM do Docker no Windows chega a congelar ~50 s; o que importa
            # e que expirou (nao ficou preso para sempre) e nao antes dos 5 s configurados.
            assert 4.0 <= espera <= 90.0, espera
        finally:
            segura.rollback()
