"""Testes da migration 20261108130000 (IA opcional: ia_configuracoes, ia_uso e RPC ia_consumo).

Somente banco local/dev DESCARTAVEL (TEST_DATABASE_URL + TEST_DATABASE_CONFIRM=descartavel + marcador
`comment on database ... is 'sisveiculos:descartavel'`; sem eles os testes sao pulados). Ver dbhelpers.py.
Os testes que alteram a configuracao desfazem ao final; ia_uso e append-only, entao as linhas criadas
ficam (usam usuario novo e unidade propria do usuario, sem colisao).

    pytest supabase/tests/test_ia_fundacao.py -m db_local -p no:xdist
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

COLUNAS = "mestre, saudacao, provedor, modelo, provedor_reserva, teto_tokens_mes"


def _definir(sets: str, valores: tuple, por: uuid.UUID | None) -> None:
    with h.conectar() as c:
        c.execute(
            f"update public.ia_configuracoes set {sets}, alterado_por = %s where id = true",
            (*valores, por),
        )


def _linha() -> tuple:
    with h.conectar() as c:
        row = c.execute(f"select {COLUNAS} from public.ia_configuracoes where id = true").fetchone()
    assert row is not None
    return row


@pytest.fixture(autouse=True)
def _restaurar() -> Iterator[None]:
    """Devolve a configuracao ao padrao (tudo desligado) depois de cada teste."""
    yield
    admin = h.admin_ativo_qualquer() or h.criar_usuario("admin-ia", "admin")
    _definir(
        "mestre = false, saudacao = false, provedor = null, modelo = null, "
        "provedor_reserva = null, teto_tokens_mes = null",
        (),
        admin,
    )


def test_padrao_tudo_desligado_e_sem_provedor() -> None:
    assert _linha() == (False, False, None, None, None, None)


def test_so_existe_uma_linha() -> None:
    with pytest.raises(psycopg.Error):
        with h.conectar() as c:
            c.execute("insert into public.ia_configuracoes (id) values (false)")
    with h.conectar() as c:
        (n,) = c.execute("select count(*) from public.ia_configuracoes").fetchone() or (0,)
    assert n == 1


def test_so_admin_ativo_altera() -> None:
    aut = h.criar_usuario("aut-ia", "autorizador")
    inativo = h.criar_usuario("admin-ia-inativo", "admin", ativo=False)
    for quem in (aut, inativo, None):
        with pytest.raises(psycopg.Error) as exc:
            _definir("mestre = %s", (True,), quem)
        assert h.sqlstate(exc) == "AG002"
    assert _linha()[0] is False


def test_alteracao_gera_auditoria_por_coluna_e_zera_o_ator() -> None:
    admin = h.criar_usuario("admin-ia-aud", "admin")
    with h.conectar() as c:
        (antes,) = c.execute(
            "select count(*) from public.auditoria where acao = 'configuracao_alterada' "
            "and detalhes->>'recurso' like 'ia.%%'"
        ).fetchone() or (0,)
    _definir("provedor = %s, modelo = %s", ("openai", "gpt-5.4-mini"), admin)
    with h.conectar() as c:
        linhas = c.execute(
            "select detalhes->>'recurso', de, para, ator_id from public.auditoria "
            "where acao = 'configuracao_alterada' and detalhes->>'recurso' like 'ia.%%' "
            "order by id desc limit 2"
        ).fetchall()
        (alterado_por,) = c.execute(
            "select alterado_por from public.ia_configuracoes where id = true"
        ).fetchone() or (None,)
        (depois,) = c.execute(
            "select count(*) from public.auditoria where acao = 'configuracao_alterada' "
            "and detalhes->>'recurso' like 'ia.%%'"
        ).fetchone() or (0,)
    assert depois == antes + 2
    assert sorted((r[0], r[1], r[2], r[3]) for r in linhas) == [
        ("ia.modelo", None, "gpt-5.4-mini", admin),
        ("ia.provedor", None, "openai", admin),
    ]
    assert alterado_por is None  # transporte: nunca persiste


def test_sem_mudanca_nao_audita() -> None:
    admin = h.criar_usuario("admin-ia-igual", "admin")
    with h.conectar() as c:
        (antes,) = c.execute("select count(*) from public.auditoria").fetchone() or (0,)
    _definir("mestre = false", (), admin)  # ja era false
    with h.conectar() as c:
        (depois,) = c.execute("select count(*) from public.auditoria").fetchone() or (0,)
    assert depois == antes


@pytest.mark.parametrize(
    "sets, valores",
    [
        ("provedor = %s", ("deepseek",)),
        ("provedor_reserva = %s", ("gemini",)),
        ("provedor = %s, provedor_reserva = %s", ("openai", "openai")),
        ("modelo = %s", ("modelo com espaco",)),
        ("modelo = %s", ("x" * 81,)),
        ("teto_tokens_mes = %s", (0,)),
        ("teto_tokens_mes = %s", (-5,)),
    ],
)
def test_checks_recusam_valores_invalidos(sets: str, valores: tuple) -> None:
    admin = h.criar_usuario("admin-ia-chk", "admin")
    with pytest.raises(psycopg.Error) as exc:
        _definir(sets, valores, admin)
    assert h.sqlstate(exc) == "23514"


def test_sem_delete_nem_truncate_na_configuracao() -> None:
    for sql in ("delete from public.ia_configuracoes", "truncate public.ia_configuracoes"):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(sql)
        assert h.sqlstate(exc) == "AG003"


def _uso(usuario: uuid.UUID, provedor: str, status: str, tin: int = 0, tout: int = 0) -> None:
    with h.conectar() as c:
        c.execute(
            "insert into public.ia_uso (funcao, provedor, modelo, usuario_id, unidade, "
            "tokens_entrada, tokens_saida, status, latencia_ms) "
            "values ('saudacao', %s, 'm', %s, 'UEA', %s, %s, %s, 10)",
            (provedor, usuario, tin, tout, status),
        )


def test_ia_uso_e_append_only() -> None:
    u = h.criar_usuario("sol-ia-imut")
    _uso(u, "openai", "ok", 1, 1)
    for sql in (
        "update public.ia_uso set tokens_saida = 0",
        "delete from public.ia_uso",
        "truncate public.ia_uso",
    ):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(sql)
        assert h.sqlstate(exc) == "AG003"


@pytest.mark.parametrize(
    "campo, valor",
    [("funcao", "outra"), ("provedor", "deepseek"), ("status", "talvez"), ("tokens_entrada", -1)],
)
def test_ia_uso_checks(campo: str, valor: object) -> None:
    u = h.criar_usuario("sol-ia-chk")
    base = {
        "funcao": "saudacao",
        "provedor": "openai",
        "modelo": "m",
        "usuario_id": u,
        "status": "ok",
        "tokens_entrada": 0,
    }
    base[campo] = valor
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                "insert into public.ia_uso (funcao, provedor, modelo, usuario_id, status, "
                "tokens_entrada) values (%(funcao)s, %(provedor)s, %(modelo)s, %(usuario_id)s, "
                "%(status)s, %(tokens_entrada)s)",
                base,
            )
    assert h.sqlstate(exc) == "23514"


def test_ia_uso_aceita_funcao_teste() -> None:
    u = h.criar_usuario("sol-ia-teste")
    with h.conectar() as c:
        c.execute(
            "insert into public.ia_uso (funcao, provedor, modelo, usuario_id, status, "
            "tokens_entrada) values ('teste', 'openai', 'm', %s, 'ok', 0)",
            (u,),
        )


def test_migration_funcao_teste_e_idempotente() -> None:
    sql = (
        Path(__file__).resolve().parents[1] / "migrations" / "20261111120200_ia_uso_funcao_teste.sql"
    ).read_text(encoding="utf-8")
    with h.reaplicando_migration() as c:
        c.execute(sql)
        c.execute(sql)
        (defn,) = c.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conname = 'ck_ia_uso_funcao' and conrelid = 'public.ia_uso'::regclass"
        ).fetchone() or ("",)
    assert "saudacao" in defn and "teste" in defn


def test_ia_consumo_conta_tentativas_e_soma_tokens_do_mes() -> None:
    u = h.criar_usuario("sol-ia-consumo")
    _uso(u, "openai", "ok", 100, 50)
    _uso(u, "openai", "erro")
    _uso(u, "anthropic", "ok", 10, 5)
    _uso(u, "openai", "cota")  # barrada: nao conta como tentativa
    _uso(u, "openai", "circuito")  # barrada: nao conta como tentativa
    with h.conectar() as c:
        (r,) = c.execute(
            "select public.ia_consumo(%s, 'UEA', now() - interval '1 hour', "
            "now() - interval '30 days')",
            (u,),
        ).fetchone() or (None,)
    assert r["usuario_hora"] == 3
    assert r["tokens_mes"]["openai"] >= 150
    assert r["tokens_mes"]["anthropic"] >= 15


def test_ia_consumo_respeita_a_janela() -> None:
    u = h.criar_usuario("sol-ia-janela")
    _uso(u, "openai", "ok", 7, 3)
    with h.conectar() as c:
        (futuro,) = c.execute(
            "select public.ia_consumo(%s, 'UEA', now() + interval '1 hour', "
            "now() + interval '1 hour')",
            (u,),
        ).fetchone() or (None,)
    assert futuro["usuario_hora"] == 0 and futuro["tokens_mes"] == {"openai": 0, "anthropic": 0}


def test_rls_e_grants_negam_papeis_da_api() -> None:
    with h.conectar() as c:
        rls = dict(
            c.execute(
                "select relname, relrowsecurity from pg_class where relname in "
                "('ia_configuracoes', 'ia_uso')"
            ).fetchall()
        )
        assert rls == {"ia_configuracoes": True, "ia_uso": True}
        for papel in ("anon", "authenticated"):
            for tabela in ("ia_configuracoes", "ia_uso"):
                (ok,) = c.execute(
                    "select has_table_privilege(%s, %s, 'select, insert, update, delete')",
                    (papel, f"public.{tabela}"),
                ).fetchone() or (True,)
                assert ok is False
            (exec_,) = c.execute(
                "select has_function_privilege(%s, "
                "'public.ia_consumo(uuid, public.unidade, timestamptz, timestamptz)', 'execute')",
                (papel,),
            ).fetchone() or (True,)
            assert exec_ is False
        (svc,) = c.execute(
            "select has_function_privilege('service_role', "
            "'public.ia_consumo(uuid, public.unidade, timestamptz, timestamptz)', 'execute')"
        ).fetchone() or (False,)
        assert svc is True
        (muta,) = c.execute(
            "select has_table_privilege('service_role', 'public.ia_uso', 'update') "
            "or has_table_privilege('service_role', 'public.ia_uso', 'delete')"
        ).fetchone() or (True,)
        assert muta is False
