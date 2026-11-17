"""Migration 20261205120000_compartilhar_imagem.sql: flag configuracoes.compartilhar_imagem.

Cobre: coluna boolean not null default false; auditoria 'configuracao_alterada' com ator (uma linha por
mudanca); so admin ativo altera (AG002); ator nao persiste; sem dependencia de outros recursos; a flag fica
fora da exportacao de dados (decisao LGPD: importar nunca liga o recurso sozinho).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py).

    pytest supabase/tests/test_compartilhar_imagem.py -m db_local -p no:xdist
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

CHAVE = "compartilhar_imagem"
MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261205120000_compartilhar_imagem.sql"
).read_text(encoding="utf-8")
FILTRO = "acao = 'configuracao_alterada' and detalhes ->> 'recurso' = %s"


def _valor() -> bool:
    with h.conectar() as c:
        return bool(c.execute(f"select {CHAVE} from public.configuracoes").fetchone()[0])  # type: ignore[index]


def _definir(valor: bool, por: uuid.UUID | None) -> None:
    with h.conectar() as c:
        c.execute(f"update public.configuracoes set {CHAVE} = %s, alterado_por = %s", (valor, por))


@pytest.fixture
def adm() -> uuid.UUID:
    return h.admin_ativo_qualquer() or h.criar_usuario("cimg-admin", "admin")


@pytest.fixture(autouse=True)
def _isolado() -> Iterator[None]:
    por = h.admin_ativo_qualquer() or h.criar_usuario("cimg-admin-cfg", "admin")
    orig = _valor()
    try:
        yield
    finally:
        if _valor() != orig:
            _definir(orig, por)


def test_migration_e_idempotente_e_nao_mexe_em_exportacao() -> None:
    assert "add column if not exists compartilhar_imagem" in MIGRATION
    assert "create or replace function privado.dados_colunas" not in MIGRATION
    assert "function public.importar_dados" not in MIGRATION
    assert "notify pgrst, 'reload schema';" in MIGRATION


def test_coluna_boolean_not_null_default_false() -> None:
    with h.conectar() as c:
        assert c.execute(
            "select data_type, is_nullable, column_default from information_schema.columns "
            "where table_schema = 'public' and table_name = 'configuracoes' and column_name = %s",
            (CHAVE,),
        ).fetchone() == ("boolean", "NO", "false")


def test_alteracao_auditada_com_ator(adm: uuid.UUID) -> None:
    with h.conectar() as c:
        antes = c.execute(f"select count(*) from public.auditoria where {FILTRO}", (CHAVE,)).fetchone()[0]  # type: ignore[index]
    atual = _valor()
    _definir(not atual, adm)
    _definir(atual, adm)
    with h.conectar() as c:
        total = c.execute(f"select count(*) from public.auditoria where {FILTRO}", (CHAVE,)).fetchone()[0]  # type: ignore[index]
        linhas = c.execute(
            f"select de, para, ator_id from public.auditoria where {FILTRO} "
            "order by ocorrido_em desc, id desc limit 2",
            (CHAVE,),
        ).fetchall()
    assert total == antes + 2
    assert {(r[0], r[1]) for r in linhas} == {("false", "true"), ("true", "false")}
    assert all(r[2] == adm for r in linhas)


def test_update_sem_mudanca_nao_audita(adm: uuid.UUID) -> None:
    with h.conectar() as c:
        antes = c.execute(f"select count(*) from public.auditoria where {FILTRO}", (CHAVE,)).fetchone()[0]  # type: ignore[index]
    _definir(_valor(), adm)
    with h.conectar() as c:
        depois = c.execute(f"select count(*) from public.auditoria where {FILTRO}", (CHAVE,)).fetchone()[0]  # type: ignore[index]
    assert depois == antes


def test_so_admin_ativo_altera() -> None:
    sol = h.criar_usuario("cimg-sol")
    atual = _valor()
    for por in (sol, None):
        with h.conectar() as c, pytest.raises(psycopg.Error) as exc:
            c.execute(
                f"update public.configuracoes set {CHAVE} = %s, alterado_por = %s", (not atual, por)
            )
        assert h.sqlstate(exc) == "AG002"
    assert _valor() is atual


def test_ator_nao_persiste(adm: uuid.UUID) -> None:
    _definir(not _valor(), adm)
    with h.conectar() as c:
        assert c.execute("select alterado_por from public.configuracoes").fetchone() == (None,)


def test_sem_dependencia_de_outros_recursos(adm: uuid.UUID) -> None:
    """Liga com quilometragem, checklist e motorista desligados (nao exige nenhum recurso)."""
    with h.conectar() as c:
        km, ck, mot = c.execute(
            "select quilometragem, checklist, motorista from public.configuracoes"
        ).fetchone()  # type: ignore[misc]
    if km or ck or mot:
        pytest.skip("banco local com recursos ligados; o teste exige todos desligados")
    _definir(True, adm)
    assert _valor() is True


def test_fora_da_lista_de_exportacao() -> None:
    with h.conectar() as c:
        (cols,) = c.execute("select privado.dados_colunas('configuracoes')").fetchone()  # type: ignore[misc]
    assert CHAVE not in cols
