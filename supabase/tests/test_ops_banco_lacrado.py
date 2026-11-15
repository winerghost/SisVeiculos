"""Testes da migration 20261129120000_ops_banco_lacrado (public.ops_banco_lacrado()).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). Os testes trocam o comentario do banco e SEMPRE
restauram o marcador 'sisveiculos:descartavel' no fim. Sem paralelismo.

    pytest supabase/tests/test_ops_banco_lacrado.py -m db_local -p no:xdist
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

psycopg = pytest.importorskip("psycopg")

from psycopg import sql  # noqa: E402

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

FUNCAO = "public.ops_banco_lacrado()"
PADRAO = "default administrative connection database"


def _definir_comentario(valor: str | None) -> None:
    with h.conectar() as c:
        banco = c.execute("select current_database()").fetchone()[0]  # type: ignore[index]
        c.execute(
            sql.SQL("comment on database {} is {}").format(
                sql.Identifier(banco), sql.Literal(valor) if valor is not None else sql.SQL("NULL")
            )
        )


@pytest.fixture
def marcador() -> Iterator[None]:
    """Garante que o marcador de banco descartavel volte ao fim, aconteca o que acontecer."""
    with h.conectar() as c:
        original = c.execute(
            "select shobj_description(oid, 'pg_database') from pg_database where datname = current_database()"
        ).fetchone()[0]  # type: ignore[index]
    assert original == h.MARCADOR_BANCO
    try:
        yield
    finally:
        _definir_comentario(h.MARCADOR_BANCO)


def _lacrado_como_service_role() -> bool:
    with h.como("service_role") as c:
        return bool(c.execute(f"select {FUNCAO}").fetchone()[0])  # type: ignore[index]


@pytest.mark.parametrize(
    ("comentario", "esperado"),
    [
        (None, False),
        ("", False),
        (PADRAO, False),
        ("sisveiculos:descartavel", False),
        ("sisveiculos:lacrad", False),
        ("SISVEICULOS:LACRADO", False),  # sensivel a maiusculas, como o SQL de ops
        ("sisveiculos:lacrado", True),
        ("sisveiculos:lacrado em 2026-12-01", True),
        ("antes sisveiculos:lacrado depois", True),  # "contem", nao so "comeca com"
    ],
)
def test_devolve_true_so_quando_o_comentario_contem_o_lacre(
    marcador: None, comentario: str | None, esperado: bool
) -> None:
    _definir_comentario(comentario)
    assert _lacrado_como_service_role() is esperado


def test_marcador_de_teste_nao_conta_como_lacre(marcador: None) -> None:
    assert _lacrado_como_service_role() is False


@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_anon_e_authenticated_nao_executam(marcador: None, papel: str) -> None:
    _definir_comentario("sisveiculos:lacrado")  # nem com o lacre gravado a resposta vaza
    uid = uuid.uuid4() if papel == "authenticated" else None
    with pytest.raises(psycopg.errors.InsufficientPrivilege), h.como(papel, uid) as c:
        c.execute(f"select {FUNCAO}")


def test_grants_e_configuracao() -> None:
    with h.conectar() as c:
        row = c.execute(
            "select has_function_privilege('anon', %(f)s, 'execute'), "
            "has_function_privilege('authenticated', %(f)s, 'execute'), "
            "has_function_privilege('public', %(f)s, 'execute'), "
            "has_function_privilege('service_role', %(f)s, 'execute'), "
            "p.prosecdef, p.proconfig, p.provolatile, pg_get_function_result(p.oid) "
            "from pg_proc p where p.oid = %(f)s::regprocedure",
            {"f": FUNCAO},
        ).fetchone()
    assert row is not None
    anon, auth, publico, service, secdef, config, volatil, retorno = row
    assert (anon, auth, publico, service) == (False, False, False, True)
    assert secdef is True and config == ['search_path=""']
    assert volatil == "s" and retorno == "boolean"


def test_so_devolve_booleano_sem_o_texto_do_comentario(marcador: None) -> None:
    _definir_comentario("sisveiculos:lacrado segredo-nao-deve-sair")
    with h.como("service_role") as c:
        cur = c.execute(f"select * from {FUNCAO}")
        assert [d.name for d in cur.description or []] == ["ops_banco_lacrado"]
        assert cur.fetchall() == [(True,)]
