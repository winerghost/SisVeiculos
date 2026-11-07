"""Testes das migrations 9, 10 e 13: RLS, grants e leitura direta negada a authenticated/anon.

Somente banco local/dev descartavel (TEST_DATABASE_URL). Assume papeis do Supabase
(anon, authenticated, service_role) via `set local role` + claims de JWT simulados.
Ver dbhelpers.como().

    pytest supabase/tests/test_rls_idor.py -m db_local
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

NEGADO = "42501"  # insufficient_privilege
TABELAS = ["perfis", "veiculos", "requisicoes", "auditoria", "configuracoes", "convites"]


@dataclass
class Cenario:
    a: uuid.UUID
    b: uuid.UUID
    autorizador: uuid.UUID
    admin: uuid.UUID
    inativo: uuid.UUID
    req_a: uuid.UUID
    req_b: uuid.UUID


@pytest.fixture
def cen() -> Cenario:
    a, b = h.criar_usuario("a"), h.criar_usuario("b")
    with h.conectar() as c:
        ra = h.inserir_requisicao(c, a, h.novo_veiculo())
        rb = h.inserir_requisicao(c, b, h.novo_veiculo())
    return Cenario(
        a=a,
        b=b,
        autorizador=h.criar_usuario("aut", "autorizador"),
        admin=h.criar_usuario("adm", "admin"),
        inativo=h.criar_usuario("inativo", "solicitante", ativo=False),
        req_a=ra,
        req_b=rb,
    )


# --- leitura direta negada (migration 13): so o Flask (service_role) le dados -----------
# O anti-IDOR por dono agora e do backend; no banco nenhum papel da API le tabela alguma.
def _leitura_negada(papel: str, uid: uuid.UUID | None, sql: str) -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.como(papel, uid) as c:
            c.execute(sql)
    assert h.sqlstate(exc) == NEGADO


@pytest.mark.parametrize("tabela", TABELAS)
@pytest.mark.parametrize("quem", ["a", "autorizador", "admin", "inativo"])
def test_authenticated_nao_le_nenhuma_tabela(cen: Cenario, quem: str, tabela: str) -> None:
    _leitura_negada("authenticated", getattr(cen, quem), f"select count(*) from public.{tabela}")


def test_solicitante_nao_acessa_requisicao_alheia_por_id(cen: Cenario) -> None:
    _leitura_negada("authenticated", cen.a, f"select id from public.requisicoes where id = '{cen.req_b}'")


def test_sem_claims_nao_le_nada() -> None:
    _leitura_negada("authenticated", None, "select count(*) from public.requisicoes")


def test_service_role_continua_lendo(cen: Cenario) -> None:
    with h.como("service_role") as c:
        ids = {r[0] for r in c.execute("select id from public.requisicoes").fetchall()}
    assert {cen.req_a, cen.req_b} <= ids


def test_sem_politicas_de_leitura_por_papel() -> None:
    with h.conectar() as c:
        rows = c.execute(
            "select policyname from pg_policies where schemaname = 'public' "
            "and policyname in ('perfis_leitura', 'veiculos_leitura', 'requisicoes_leitura', "
            "'auditoria_leitura', 'configuracoes_leitura')"
        ).fetchall()
    assert rows == []


def test_authenticated_sem_acesso_ao_schema_privado(cen: Cenario) -> None:
    with h.conectar() as c:
        (usage,) = c.execute("select has_schema_privilege('authenticated', 'privado', 'usage')").fetchone() or (True,)
        (exe,) = c.execute(
            "select has_function_privilege('authenticated', 'privado.papel_atual()', 'execute')"
        ).fetchone() or (True,)
    assert not usage and not exe
    _leitura_negada("authenticated", cen.admin, "select privado.papel_atual()")


# --- escrita negada ------------------------------------------------------------
@pytest.mark.parametrize(
    "sql",
    [
        "insert into public.veiculos (descricao) values ('x')",
        "update public.perfis set role = 'admin'",
        "update public.requisicoes set status = 'cancelada'",
        "delete from public.requisicoes",
        "insert into public.auditoria (tabela, acao) values ('perfis', 'perfil_alterado')",
        "update public.configuracoes set mfa = true",
    ],
)
@pytest.mark.parametrize("papel", ["authenticated", "anon"])
def test_authenticated_e_anon_nao_escrevem(cen: Cenario, papel: str, sql: str) -> None:
    uid = cen.admin if papel == "authenticated" else None  # nem admin acessa a API de dados
    with pytest.raises(psycopg.Error) as exc:
        with h.como(papel, uid) as c:
            c.execute(sql)
    assert h.sqlstate(exc) == NEGADO


@pytest.mark.parametrize("tabela", TABELAS)
def test_anon_nao_le_nada(tabela: str) -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.como("anon") as c:
            c.execute(f"select * from public.{tabela}")
    assert h.sqlstate(exc) == NEGADO


# --- funcoes internas ------------------------------------------------------------
@pytest.mark.parametrize("papel", ["anon", "authenticated"])
@pytest.mark.parametrize(
    "sql",
    [
        "select public.consumir_convite(%s, gen_random_uuid(), 'x')",
        "select public.registrar_auditoria('perfis', null, 'acesso_negado', null)",
        "select privado.semear_primeiro_admin('x@gmail.com')",
    ],
)
def test_funcoes_sensiveis_nao_executaveis_pela_api(papel: str, sql: str) -> None:
    params = (h.novo_token()[1],) if "%s" in sql else ()
    with pytest.raises(psycopg.Error) as exc:
        with h.como(papel) as c:
            c.execute(sql, params)
    assert h.sqlstate(exc) == NEGADO


def test_seed_recusa_quando_ja_existe_admin_ativo() -> None:
    h.criar_usuario("admin", "admin")
    with h.conectar() as c:
        assert c.execute("select privado.semear_primeiro_admin(%s)", (h.email_unico(),)).fetchone() == (
            "ja_existe_admin",
        )
        assert c.execute("select privado.semear_primeiro_admin(null)").fetchone() == ("sem_email",)


def test_rls_ligado_em_todas_as_tabelas() -> None:
    with h.conectar() as c:
        rows = c.execute(
            "select relname, relrowsecurity from pg_class "
            "where relnamespace = 'public'::regnamespace and relname = any(%s)",
            (TABELAS,),
        ).fetchall()
    assert len(rows) == len(TABELAS)
    assert all(ativo for _, ativo in rows), rows
