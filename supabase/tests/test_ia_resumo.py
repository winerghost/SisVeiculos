"""Testes da migration 20261115120300 (IA Fase 2: ia_configuracoes.resumo e ck_ia_uso_funcao com 'resumo').

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). A configuracao e devolvida ao padrao ao final;
ia_uso e append-only (linhas criadas ficam, com usuario novo).

    pytest supabase/tests/test_ia_resumo.py -m db_local -p no:xdist
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261115120300_ia_resumo.sql"
).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _migration_aplicada_e_restaurar() -> Iterator[None]:
    # Idempotente: reaplica (test_ia_fundacao reaplica a migration antiga do CHECK, que o reduziria
    # a saudacao/teste se esta suite rodasse depois dela).
    with h.conectar() as c:
        c.execute(MIGRATION)
    yield
    admin = h.admin_ativo_qualquer() or h.criar_usuario("admin-resumo", "admin")
    with h.conectar() as c:
        c.execute("update public.ia_configuracoes set resumo = false, alterado_por = %s where id = true", (admin,))


def _resumo() -> bool:
    with h.conectar() as c:
        row = c.execute("select resumo from public.ia_configuracoes where id = true").fetchone()
    assert row is not None
    return bool(row[0])


def test_resumo_nasce_desligado_nao_nulo() -> None:
    assert _resumo() is False
    with h.conectar() as c:
        row = c.execute(
            "select is_nullable, column_default, data_type from information_schema.columns "
            "where table_schema = 'public' and table_name = 'ia_configuracoes' and column_name = 'resumo'"
        ).fetchone()
    assert row == ("NO", "false", "boolean")


def test_ligar_resumo_audita_com_recurso_ia_resumo() -> None:
    admin = h.criar_usuario("admin-resumo-aud", "admin")
    with h.conectar() as c:
        c.execute("update public.ia_configuracoes set resumo = true, alterado_por = %s where id = true", (admin,))
        linhas = c.execute(
            "select tabela, acao, ator_id, de, para from public.auditoria "
            "where detalhes->>'recurso' = 'ia.resumo' order by id desc limit 1"
        ).fetchall()
    assert linhas == [("configuracoes", "configuracao_alterada", admin, "false", "true")]
    assert _resumo() is True


def test_so_admin_ativo_altera_resumo() -> None:
    aut = h.criar_usuario("aut-resumo", "autorizador")
    inativo = h.criar_usuario("admin-resumo-inativo", "admin", ativo=False)
    for quem in (aut, inativo, None):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(
                    "update public.ia_configuracoes set resumo = true, alterado_por = %s where id = true", (quem,)
                )
        assert h.sqlstate(exc) == "AG002"
    assert _resumo() is False


def test_service_role_atualiza_resumo_e_api_nao_acessa() -> None:
    with h.conectar() as c:
        for papel, esperado in (("service_role", True), ("anon", False), ("authenticated", False)):
            (ok,) = c.execute(
                "select has_column_privilege(%s, 'public.ia_configuracoes', 'resumo', 'update')", (papel,)
            ).fetchone() or (None,)
            assert ok is esperado, papel


@pytest.mark.parametrize("funcao", ["saudacao", "teste", "resumo"])
def test_ia_uso_aceita_funcoes(funcao: str) -> None:
    u = h.criar_usuario("sol-ia-resumo")
    with h.conectar() as c:
        c.execute(
            "insert into public.ia_uso (funcao, provedor, modelo, usuario_id, status) "
            "values (%s, 'openai', 'm', %s, 'ok')",
            (funcao, u),
        )


def test_ia_uso_recusa_funcao_desconhecida() -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                "insert into public.ia_uso (funcao, provedor, modelo, status) values (%s, 'openai', 'm', 'ok')",
                (f"outra-{uuid.uuid4().hex[:4]}",),
            )
    assert h.sqlstate(exc) == "23514"


def test_migration_e_idempotente() -> None:
    with h.conectar() as c:
        c.execute(MIGRATION)
        c.execute(MIGRATION)
        (defn,) = c.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conname = 'ck_ia_uso_funcao' and conrelid = 'public.ia_uso'::regclass"
        ).fetchone() or ("",)
    assert all(v in defn for v in ("saudacao", "teste", "resumo"))
