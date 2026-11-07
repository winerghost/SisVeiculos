"""Testes da migration 8: configuracoes (recursos opcionais).

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.
Os testes que ligam recursos desfazem a alteracao ao final; o teste de estado
inicial presume banco recem-criado (`supabase db reset`).

    pytest supabase/tests/test_configuracoes.py -m db_local
"""

from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

RECURSOS = ["quilometragem", "abastecimento_manutencao", "checklist", "mfa"]


def _definir(valores: dict[str, bool], por: uuid.UUID | None) -> None:
    sets = ", ".join(f"{k} = %s" for k in valores)
    with h.conectar() as c:
        c.execute(
            f"update public.configuracoes set {sets}, alterado_por = %s where id = true",
            (*valores.values(), por),
        )


def _linha() -> dict[str, bool]:
    with h.conectar() as c:
        row = c.execute(
            "select quilometragem, abastecimento_manutencao, checklist, mfa "
            "from public.configuracoes where id = true"
        ).fetchone()
    assert row is not None
    return dict(zip(RECURSOS, row, strict=True))


def test_defaults_das_colunas_sao_desligado() -> None:
    with h.conectar() as c:
        rows = c.execute(
            "select column_name, column_default from information_schema.columns "
            "where table_schema='public' and table_name='configuracoes' "
            "and column_name = any(%s)",
            (RECURSOS,),
        ).fetchall()
    assert {n: d for n, d in rows} == {r: "false" for r in RECURSOS}


def test_recursos_vem_desligados_em_banco_novo() -> None:
    assert _linha() == {r: False for r in RECURSOS}


def test_so_existe_uma_linha() -> None:
    with pytest.raises(psycopg.Error):
        with h.conectar() as c:
            c.execute("insert into public.configuracoes (id) values (false)")
    with h.conectar() as c:
        (n,) = c.execute("select count(*) from public.configuracoes").fetchone() or (0,)
    assert n == 1


def test_so_admin_ativo_altera_e_gera_auditoria() -> None:
    admin = h.criar_usuario("admin", "admin")
    autorizador = h.criar_usuario("aut", "autorizador")
    inativo = h.criar_usuario("admin-inativo", "admin", ativo=False)
    for quem in (autorizador, inativo, None):
        with pytest.raises(psycopg.Error) as exc:
            _definir({"checklist": True}, quem)
        assert h.sqlstate(exc) == "AG002"
    with h.conectar() as c:
        (antes,) = c.execute(
            "select count(*) from public.auditoria where tabela='configuracoes' and ator_id=%s", (admin,)
        ).fetchone() or (0,)
    try:
        _definir({"checklist": True}, admin)
        linhas = [
            r
            for r in h.auditoria_de("configuracoes", None)
            if r[1] == admin and r[0] == "configuracao_alterada"
        ]
        assert ("configuracao_alterada", admin, "false", "true") in linhas
        assert len(linhas) == antes + 1
    finally:
        _definir({"checklist": False}, admin)


def test_abastecimento_exige_quilometragem() -> None:
    admin = h.criar_usuario("admin", "admin")
    with pytest.raises(psycopg.Error) as exc:
        _definir({"abastecimento_manutencao": True}, admin)
    assert h.sqlstate(exc) == "AG010"
    try:
        _definir({"quilometragem": True, "abastecimento_manutencao": True}, admin)
        # nao pode desligar a dependencia enquanto o dependente estiver ligado
        with pytest.raises(psycopg.Error) as exc:
            _definir({"quilometragem": False}, admin)
        assert h.sqlstate(exc) == "AG010"
    finally:
        _definir({"abastecimento_manutencao": False}, admin)
        _definir({"quilometragem": False}, admin)
    assert _linha() == {r: False for r in RECURSOS}


def test_alteracao_recusada_nao_deixa_auditoria() -> None:
    admin = h.criar_usuario("admin", "admin")
    antes = len(h.auditoria_de("configuracoes", None))
    with pytest.raises(psycopg.Error):
        _definir({"abastecimento_manutencao": True}, admin)
    assert len(h.auditoria_de("configuracoes", None)) == antes


def test_sem_delete_nem_truncate() -> None:
    for sql in ("delete from public.configuracoes", "truncate public.configuracoes"):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(sql)
        assert h.sqlstate(exc) == "AG003"
