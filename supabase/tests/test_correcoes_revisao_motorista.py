"""Testes da migration 20261113120200 (correcoes da revisao do recurso Motorista).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). A auditoria e append-only: os testes gravam e
nao apagam.

    pytest supabase/tests/test_correcoes_revisao_motorista.py -m db_local -p no:xdist
"""

from __future__ import annotations

from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261113120200_correcoes_revisao_motorista.sql"
).read_text(encoding="utf-8")

ACOES = {
    "criada", "transicao", "convite_criado", "convite_usado", "convite_revogado",
    "perfil_criado", "perfil_alterado", "perfil_unidade_alterada", "admin_semeado",
    "configuracao_alterada", "veiculo_criado", "veiculo_alterado", "acao_administrativa",
    "acesso_negado", "checklist_registrado", "foto_perfil_alterada", "foto_perfil_removida",
    "relatorio_gerado", "relatorio_enviado", "motorista_designado", "localizacao_registrada",
    "localizacao_lida",
}  # fmt: skip


def test_migration_idempotente_e_sem_coordenadas() -> None:
    assert "drop constraint if exists ck_auditoria_acao" in MIGRATION
    assert "'localizacao_lida'" in MIGRATION
    assert "service_role" in MIGRATION


def test_check_aceita_todas_as_acoes_vigentes_e_a_nova() -> None:
    with h.conectar() as c:
        (d,) = c.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid = 'public.auditoria'::regclass and conname = 'ck_auditoria_acao'"
        ).fetchone() or (None,)
    assert d is not None
    for acao in ACOES:
        assert f"'{acao}'" in d, acao


def test_auditoria_aceita_localizacao_lida_sem_coordenadas() -> None:
    with h.conectar() as c:
        c.execute(
            "insert into public.auditoria (tabela, acao, detalhes) "
            "values ('requisicoes', 'localizacao_lida', %s::jsonb)",
            ('{"recurso": "motorista_localizacao"}',),
        )


def test_auditoria_continua_recusando_acao_desconhecida() -> None:
    with h.conectar() as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("insert into public.auditoria (tabela, acao) values ('requisicoes', 'localizacao_xyz')")


@pytest.mark.parametrize("papel", h.PAPEIS_API)
def test_notificacoes_gerar_nao_executavel_pela_api(papel: str) -> None:
    with h.conectar() as c:
        assert c.execute(
            "select has_function_privilege(%s, 'public.notificacoes_gerar()', 'execute')", (papel,)
        ).fetchone() == (False,)
