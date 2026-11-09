"""Testes das migrations 20261116120000 (DB-03) e 20261116120100 (S-03).

Parte estatica (sempre roda): confere o texto das migrations.
Parte de banco: somente banco local/dev DESCARTAVEL (ver dbhelpers.py).

    pytest supabase/tests/test_hardening_fase4.py -m db_local -p no:xdist
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h

MIG = Path(__file__).resolve().parents[1] / "migrations"
POLICY_SQL = (MIG / "20261116120000_ia_saudacoes_sem_acesso_api.sql").read_text(encoding="utf-8")
CRON_SQL = (MIG / "20261116120100_agenda_expurgo_localizacoes.sql").read_text(encoding="utf-8")

TABELAS = ["ia_configuracoes", "ia_uso", "saudacoes"]

# --- estatico -------------------------------------------------------------------------------------------
def test_estatico_policies_nas_3_tabelas() -> None:
    for t in TABELAS:
        assert f"create policy {t}_sem_acesso_api on public.{t}" in POLICY_SQL
        assert f"drop policy if exists {t}_sem_acesso_api on public.{t}" in POLICY_SQL
    assert POLICY_SQL.count("for all to anon, authenticated using (false) with check (false)") == 3


def test_estatico_cron_condicional_e_sem_falha() -> None:
    assert "pg_available_extensions" in CRON_SQL
    assert "exception when others then" in CRON_SQL
    assert "cron.schedule" in CRON_SQL
    assert "public.expurgar_localizacoes_vencidas()" in CRON_SQL
    assert "23 3 1 * *" in CRON_SQL


# --- DB-03 ----------------------------------------------------------------------------------------------
@pytest.mark.db_local
@pytest.mark.parametrize("tabela", TABELAS)
@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_policy_existe_e_nega(tabela: str, papel: str) -> None:
    if not h.DSN:
        pytest.skip("sem banco")
    with h.conectar() as c:
        row = c.execute(
            "select permissive, cmd, qual, with_check, roles::text[] from pg_policies "
            "where schemaname='public' and tablename=%s and policyname=%s",
            (tabela, f"{tabela}_sem_acesso_api"),
        ).fetchone()
    assert row is not None
    assert row[0] == "PERMISSIVE" and row[1] == "ALL" and row[2] == "false" and row[3] == "false"
    assert papel in row[4]


@pytest.mark.db_local
@pytest.mark.parametrize("tabela", TABELAS)
@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_api_nao_le_nem_escreve(tabela: str, papel: str) -> None:
    if not h.DSN:
        pytest.skip("sem banco")
    uid = h.criar_usuario(f"u-{tabela[:6]}", "solicitante") if papel == "authenticated" else None
    with pytest.raises(psycopg.Error) as exc, h.como(papel, uid) as c:
        c.execute(f"select * from public.{tabela}")  # type: ignore[arg-type]
    assert h.sqlstate(exc) == "42501"


@pytest.mark.db_local
def test_migration_de_policy_e_idempotente() -> None:
    if not h.DSN:
        pytest.skip("sem banco")
    with h.conectar() as c:
        c.execute(POLICY_SQL)  # type: ignore[arg-type]
        c.execute(POLICY_SQL)  # type: ignore[arg-type]
        (n,) = c.execute(
            "select count(*) from pg_policies where schemaname='public' and tablename = any(%s) "
            "and policyname like '%%_sem_acesso_api'",
            (TABELAS,),
        ).fetchone() or (0,)
    assert n == 3


# --- S-03 -----------------------------------------------------------------------------------------------
@pytest.mark.db_local
def test_migration_de_cron_roda_sem_falhar_e_e_idempotente() -> None:
    """Sem pg_cron (local/CI) so avisa; com pg_cron agenda um unico job."""
    if not h.DSN:
        pytest.skip("sem banco")
    with h.conectar() as c:
        c.execute(CRON_SQL)  # type: ignore[arg-type]
        c.execute(CRON_SQL)  # type: ignore[arg-type]
        instalada = c.execute("select 1 from pg_extension where extname='pg_cron'").fetchone()
        if not instalada:
            pytest.skip("pg_cron indisponivel neste banco: migration apenas avisou (comportamento esperado)")
        jobs = c.execute(
            "select schedule, command from cron.job where jobname = 'expurgar-localizacoes'"
        ).fetchall()
    assert jobs == [("23 3 1 * *", "select public.expurgar_localizacoes_vencidas()")]


@pytest.mark.db_local
def test_expurgo_grava_auditoria_e_nao_expoe_api() -> None:
    if not h.DSN:
        pytest.skip("sem banco")
    with h.conectar() as c:
        antes = c.execute("select count(*) from public.auditoria where acao='localizacao_expurgada'").fetchone()
        c.execute("select public.expurgar_localizacoes_vencidas()")
        depois = c.execute("select count(*) from public.auditoria where acao='localizacao_expurgada'").fetchone()
    assert depois[0] == antes[0] + 1  # type: ignore[index]
    for papel in ("anon", "authenticated"):
        with pytest.raises(psycopg.Error) as exc, h.como(
            papel, uuid.uuid4() if papel == "authenticated" else None
        ) as c:
            c.execute("select public.expurgar_localizacoes_vencidas()")
        assert h.sqlstate(exc) == "42501"

