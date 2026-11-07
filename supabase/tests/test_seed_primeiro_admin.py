"""Seed do primeiro admin contra BANCO VIRGEM (sem nenhum admin ativo).

DEVE RODAR PRIMEIRO, em job de CI proprio com banco virgem (`supabase db reset`, so migrations,
sem outros testes antes): qualquer outro teste que crie admin (dbhelpers.criar_usuario) tira o
banco do estado virgem. Se ja existir admin ativo, todos os testes deste arquivo sao pulados
automaticamente. Marker proprio: db_virgem (registre-o no pytest.ini/pyproject para evitar aviso).

    pytest supabase/tests/test_seed_primeiro_admin.py -m db_virgem

Afirma: (1) INSERT de admin sem a marca de seed falha com AG002; (2) privado.semear_primeiro_admin
retorna 'criado' para conta de auth confirmada; (3) a auditoria tem 'admin_semeado' e
'perfil_criado', ambos com ator nulo.

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.
"""

from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_virgem,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]


@pytest.fixture(scope="module")
def semeado() -> dict:
    """Executa a sequencia uma unica vez e na ordem correta (recusa sem marca, depois seed)."""
    if h.admin_ativo_qualquer() is not None:
        pytest.skip("banco nao esta virgem: ja existe admin ativo")

    # (1) conta qualquer tentando virar admin SEM a marca de seed.
    intruso = h.criar_auth(h.email_unico())
    sem_marca: str | None = None
    try:
        with h.conectar() as c:
            c.execute(
                "insert into public.perfis (id, nome, unidade, role) values (%s, 'Intruso', 'UEA', 'admin')",
                (intruso,),
            )
    except psycopg.Error as e:
        sem_marca = e.sqlstate

    # (2) seed com conta de auth confirmada.
    email = h.email_unico()
    uid = h.criar_auth(email)
    with h.conectar() as c:
        (retorno,) = c.execute("select privado.semear_primeiro_admin(%s)", (email,)).fetchone() or (None,)
    return {"intruso": intruso, "sem_marca": sem_marca, "retorno": retorno, "uid": uid}


def test_insert_de_admin_sem_a_marca_de_seed_falha_com_ag002(semeado: dict) -> None:
    assert semeado["sem_marca"] == "AG002"
    assert h.auditoria_de("perfis", semeado["intruso"]) == []


def test_semear_primeiro_admin_retorna_criado(semeado: dict) -> None:
    assert semeado["retorno"] == "criado"
    assert h.perfil_de(semeado["uid"]) == ("admin", True)


def test_auditoria_tem_admin_semeado_e_perfil_criado_com_ator_nulo(semeado: dict) -> None:
    linhas = h.auditoria_de("perfis", semeado["uid"])
    assert {acao for acao, _, _, _ in linhas} == {"admin_semeado", "perfil_criado"}
    assert all(ator is None for _, ator, _, _ in linhas)
    assert all(para == "admin/ativo" for _, _, _, para in linhas)


def test_seed_repetido_nao_cria_outro(semeado: dict) -> None:
    email = h.email_unico()
    h.criar_auth(email)
    with h.conectar() as c:
        (r,) = c.execute("select privado.semear_primeiro_admin(%s)", (email,)).fetchone() or (None,)
    assert r == "ja_existe_admin"
    assert isinstance(semeado["uid"], uuid.UUID)
