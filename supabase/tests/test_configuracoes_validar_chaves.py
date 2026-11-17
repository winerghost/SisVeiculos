"""Guarda de regressao (achado M4): toda coluna boolean de public.configuracoes (exceto id) precisa
constar nas DUAS listas manuais de configuracoes_validar() (versao vigente em
20261201120000_motorista_km.sql):
  1. o early-return (`new.<col> is not distinct from old.<col>`): sem ela, mudar so o flag novo
     pularia a exigencia de admin ativo;
  2. o array de chaves da auditoria: sem ela, a mudanca do flag nao gera linha em `auditoria`.

Sem migration. Somente banco local/dev DESCARTAVEL (ver dbhelpers.py); so leitura de catalogo.

    pytest supabase/tests/test_configuracoes_validar_chaves.py -m db_local -p no:xdist
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
VIGENTE = "20261201120000_motorista_km.sql"

_EARLY = re.compile(r"new\.([a-z0-9_]+)\s+is not distinct from\s+old\.([a-z0-9_]+)")
_ARRAY = re.compile(r"where\s+chave\s*=\s*any\s*\(\s*array\[(.*?)\]\s*\)", re.DOTALL)
_LITERAL = re.compile(r"'([a-z0-9_]+)'")


def _colunas_boolean() -> set[str]:
    with h.conectar() as c:
        linhas = c.execute(
            """
            select column_name
              from information_schema.columns
             where table_schema = 'public' and table_name = 'configuracoes'
               and data_type = 'boolean' and column_name <> 'id'
            """
        ).fetchall()
    cols = {r[0] for r in linhas}
    assert cols, "nenhuma coluna boolean em public.configuracoes: catalogo inesperado"
    return cols


def _listas(fonte: str) -> tuple[set[str], set[str]]:
    early = set()
    for novo, velho in _EARLY.findall(fonte):
        assert novo == velho, f"early-return compara colunas diferentes: {novo} x {velho}"
        early.add(novo)
    m = _ARRAY.search(fonte)
    assert m, "array de chaves da auditoria nao encontrado em configuracoes_validar"
    return early, set(_LITERAL.findall(m.group(1)))


def _definicao_viva() -> str:
    with h.conectar() as c:
        (fonte,) = c.execute(
            "select pg_get_functiondef('public.configuracoes_validar()'::regprocedure)"
        ).fetchone() or ("",)
    return fonte


def _ultima_migration_que_define() -> Path:
    alvo = "create or replace function public.configuracoes_validar()"
    arquivos = [p for p in sorted(MIGRATIONS.glob("*.sql")) if alvo in p.read_text(encoding="utf-8")]
    assert arquivos, "nenhuma migration define configuracoes_validar"
    return arquivos[-1]


def test_funcao_viva_cobre_todas_as_colunas_boolean() -> None:
    cols = _colunas_boolean()
    early, auditadas = _listas(_definicao_viva())
    assert not (cols - early), f"faltam no early-return de configuracoes_validar: {sorted(cols - early)}"
    assert not (cols - auditadas), f"faltam na lista de auditoria: {sorted(cols - auditadas)}"


def test_early_return_e_auditoria_sao_a_mesma_lista() -> None:
    early, auditadas = _listas(_definicao_viva())
    assert early == auditadas, (
        f"so no early-return: {sorted(early - auditadas)}; so na auditoria: {sorted(auditadas - early)}"
    )


def test_listas_nao_citam_coluna_inexistente() -> None:
    cols = _colunas_boolean()
    early, auditadas = _listas(_definicao_viva())
    assert not (early - cols), f"early-return cita colunas inexistentes: {sorted(early - cols)}"
    assert not (auditadas - cols), f"auditoria cita colunas inexistentes: {sorted(auditadas - cols)}"


def test_ultima_migration_cobre_todas_as_colunas_boolean() -> None:
    """A migration mais nova que redefine a funcao tambem precisa da lista completa (pega o caso de
    a funcao viva estar certa por acaso, mas a fonte versionada nao)."""
    arquivo = _ultima_migration_que_define()
    assert arquivo.name >= VIGENTE, f"versao vigente regrediu para {arquivo.name}"
    cols = _colunas_boolean()
    fonte = arquivo.read_text(encoding="utf-8")
    # So o corpo de configuracoes_validar (a migration define outras funcoes com o mesmo padrao).
    corpo = fonte.split("create or replace function public.configuracoes_validar()", 1)[1]
    corpo = corpo.split("$$;", 1)[0]
    early, auditadas = _listas(corpo)
    assert not (cols - early), f"{arquivo.name}: faltam no early-return: {sorted(cols - early)}"
    assert not (cols - auditadas), f"{arquivo.name}: faltam na auditoria: {sorted(cols - auditadas)}"
