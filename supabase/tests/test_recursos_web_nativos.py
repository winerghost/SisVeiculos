"""Testes da migration 20261121120000 (5 flags de recursos web nativos + configuracoes_validar com 19 chaves).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). A fixture autouse restaura as flags originais.
Rodar sem paralelismo.

    pytest supabase/tests/test_recursos_web_nativos.py -m db_local -p no:xdist
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

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261121120000_recursos_web_nativos.sql"
).read_text(encoding="utf-8")

NOVAS = ("indicador_conectividade", "feedback_tatil", "leitor_qr_veiculos", "voz_campos", "passkeys")


def _valores() -> dict[str, bool]:
    with h.conectar() as c:
        row = c.execute(f"select {', '.join(NOVAS)} from public.configuracoes").fetchone()
    return dict(zip(NOVAS, row, strict=True))  # type: ignore[arg-type]


def _definir(chave: str, valor: bool, por: uuid.UUID | None) -> None:
    with h.conectar() as c:
        c.execute(f"update public.configuracoes set {chave} = %s, alterado_por = %s", (valor, por))


@pytest.fixture(autouse=True)
def _isolado() -> Iterator[None]:
    por = h.admin_ativo_qualquer() or h.criar_usuario("rwn-admin-cfg", "admin")
    orig = _valores()
    try:
        yield
    finally:
        for k, v in orig.items():
            if _valores()[k] != v:
                _definir(k, v, por)


@pytest.fixture
def adm() -> uuid.UUID:
    return h.admin_ativo_qualquer() or h.criar_usuario("rwn-admin", "admin")


@pytest.mark.parametrize("chave", NOVAS)
def test_coluna_existe_boolean_not_null_default_false(chave: str) -> None:
    with h.conectar() as c:
        assert c.execute(
            "select data_type, is_nullable, column_default from information_schema.columns "
            "where table_schema = 'public' and table_name = 'configuracoes' and column_name = %s",
            (chave,),
        ).fetchone() == ("boolean", "NO", "false")


def test_padrao_desligado_em_banco_virgem(adm: uuid.UUID) -> None:
    # O default da coluna vale para novas linhas; aqui conferimos que nenhuma flag nasce ligada por default.
    with h.conectar() as c:
        defaults = c.execute(
            "select column_name, column_default from information_schema.columns "
            "where table_schema = 'public' and table_name = 'configuracoes' and column_name = any(%s)",
            (list(NOVAS),),
        ).fetchall()
    assert {r[0] for r in defaults} == set(NOVAS)
    assert all(r[1] == "false" for r in defaults)


@pytest.mark.parametrize("chave", NOVAS)
def test_cada_chave_e_auditada_uma_linha_com_ator(chave: str, adm: uuid.UUID) -> None:
    filtro = "acao = 'configuracao_alterada' and detalhes ->> 'recurso' = %s"
    with h.conectar() as c:
        antes = c.execute(f"select count(*) from public.auditoria where {filtro}", (chave,)).fetchone()[0]  # type: ignore[index]
    atual = _valores()[chave]
    _definir(chave, not atual, adm)
    _definir(chave, atual, adm)
    with h.conectar() as c:
        total = c.execute(f"select count(*) from public.auditoria where {filtro}", (chave,)).fetchone()[0]  # type: ignore[index]
        linhas = c.execute(
            f"select de, para, ator_id from public.auditoria where {filtro} order by criada_em desc, id desc limit 2",
            (chave,),
        ).fetchall()
    assert total == antes + 2
    assert {(r[0], r[1]) for r in linhas} == {("false", "true"), ("true", "false")}
    assert all(r[2] == adm for r in linhas)


def test_alterar_varias_chaves_gera_uma_linha_por_chave(adm: uuid.UUID) -> None:
    orig = _valores()
    with h.conectar() as c:
        antes = c.execute(
            "select count(*) from public.auditoria where acao = 'configuracao_alterada' "
            "and detalhes ->> 'recurso' = any(%s)", (list(NOVAS),)
        ).fetchone()[0]  # type: ignore[index]
        sets = ", ".join(f"{k} = %s" for k in NOVAS)
        c.execute(f"update public.configuracoes set {sets}, alterado_por = %s",
                  (*[not v for v in orig.values()], adm))
        depois = c.execute(
            "select count(*) from public.auditoria where acao = 'configuracao_alterada' "
            "and detalhes ->> 'recurso' = any(%s)", (list(NOVAS),)
        ).fetchone()[0]  # type: ignore[index]
    assert depois == antes + len(NOVAS)


@pytest.mark.parametrize("chave", NOVAS)
def test_so_admin_ativo_altera(chave: str) -> None:
    sol = h.criar_usuario("rwn-sol")
    atual = _valores()[chave]
    for por in (sol, None):
        with h.conectar() as c, pytest.raises(psycopg.Error) as exc:
            c.execute(f"update public.configuracoes set {chave} = %s, alterado_por = %s", (not atual, por))
        assert h.sqlstate(exc) == "AG002"
    assert _valores()[chave] is atual


def test_ator_nao_persiste(adm: uuid.UUID) -> None:
    atual = _valores()["passkeys"]
    _definir("passkeys", not atual, adm)
    with h.conectar() as c:
        assert c.execute("select alterado_por from public.configuracoes").fetchone() == (None,)


def test_flags_novas_sem_dependencia_de_outros_recursos(adm: uuid.UUID) -> None:
    """Ligar qualquer nova flag funciona com os demais recursos (ex.: quilometragem) desligados."""
    with h.conectar() as c:
        km = c.execute("select quilometragem from public.configuracoes").fetchone()[0]  # type: ignore[index]
    if km:
        pytest.skip("quilometragem ligada neste banco; o cenario exige desligada")
    for chave in NOVAS:
        _definir(chave, not _valores()[chave], adm)  # nao levanta AG010


def test_regras_antigas_preservadas(adm: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute("select 1")
        v = c.execute("select checklist from public.configuracoes").fetchone()[0]  # type: ignore[index]
        c.execute("update public.configuracoes set checklist = %s, alterado_por = %s", (not v, adm))
        n = c.execute("select count(*) from public.auditoria where acao = 'configuracao_alterada' "
                      "and detalhes ->> 'recurso' = 'checklist' and criada_em > now() - interval '1 minute'"
                      ).fetchone()[0]  # type: ignore[index]
        c.execute("update public.configuracoes set checklist = %s, alterado_por = %s", (v, adm))
    assert n >= 1
    # AG010 (dependencia) continua valendo: abastecimento_manutencao exige quilometragem.
    with h.conectar() as c:
        km = c.execute("select quilometragem from public.configuracoes").fetchone()[0]  # type: ignore[index]
    if not km:
        with h.conectar() as c, pytest.raises(psycopg.Error) as exc:
            c.execute("update public.configuracoes set abastecimento_manutencao = true, alterado_por = %s", (adm,))
        assert h.sqlstate(exc) == "AG010"


def test_validar_cobre_as_19_chaves_e_trigger_ativo() -> None:
    chaves = ("quilometragem", "abastecimento_manutencao", "checklist", "mfa", "relatorio_email",
              "relatorio_whatsapp", "notificacao_email_criticos", "motorista", "motorista_registra",
              "motorista_localizacao", "vigilante_km", "autorizador_decide_propria", "autorizador_km",
              "notificacao_push", *NOVAS)
    with h.conectar() as c:
        d = c.execute("select pg_get_functiondef('public.configuracoes_validar()'::regprocedure)").fetchone()[0]  # type: ignore[index]
        assert c.execute("select count(*) from pg_trigger where tgrelid = 'public.configuracoes'::regclass "
                         "and tgname = 'trg_configuracoes_validar' and tgenabled <> 'D' and not tgisinternal"
                         ).fetchone() == (1,)
    assert len(chaves) == 19
    for k in chaves:
        assert f"'{k}'" in d, k


@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_rls_da_configuracoes_intacto(papel: str) -> None:
    with h.conectar() as c:
        assert c.execute("select relrowsecurity from pg_class where oid = 'public.configuracoes'::regclass"
                         ).fetchone() == (True,)
        for p in ("insert", "update", "delete", "truncate"):
            assert c.execute("select has_table_privilege(%s, 'public.configuracoes', %s)",
                             (papel, p)).fetchone() == (False,), (papel, p)
    # nenhuma policy de escrita aberta a anon/authenticated
    with h.conectar() as c:
        assert c.execute(
            "select count(*) from pg_policy where polrelid = 'public.configuracoes'::regclass "
            "and polcmd in ('a', 'w', 'd', '*') and (polroles = '{0}' or "
            "exists (select 1 from pg_roles r where r.oid = any(polroles) and r.rolname = %s))", (papel,)
        ).fetchone() == (0,)


def test_migration_idempotente() -> None:
    with h.reaplicando_migration() as c:
        c.execute(MIGRATION)
        c.execute(MIGRATION)
    assert set(_valores()) == set(NOVAS)
