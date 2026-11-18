"""Migration 20261207120000_push_marca_logout_desativacao.sql: desativar o perfil apaga a marca de logout.

Cobre: usuario sai, e desativado e reativado por admin => o aparelho NAO religa sozinho (exige o botao);
sem desativacao o fluxo normal segue religando; outro usuario nao e afetado; funcao do gatilho SECURITY DEFINER
com search_path vazio e sem EXECUTE para os papeis da API; gatilho sempre ativo; idempotencia.

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). Rodar sem paralelismo.

    pytest supabase/tests/test_push_marca_logout_desativacao.py -m db_local -p no:xdist
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261207120000_push_marca_logout_desativacao.sql"
).read_text(encoding="utf-8")
FUNCAO = "public.perfis_limpa_marca_push_logout()"
P256 = "B" * 87
AUTH = "a" * 22


def _chamar(sql: str, params: tuple = ()):
    with h.conectar() as c:
        return c.execute(sql, params).fetchone()[0]  # type: ignore[index]


def _ep() -> str:
    return f"https://fcm.googleapis.com/fcm/send/{uuid.uuid4().hex}"


def _inscrever(usuario: uuid.UUID, endpoint: str) -> str:
    return _chamar("select public.registrar_inscricao_push(%s, %s, %s, %s)",
                   (usuario, endpoint, P256, AUTH))["id"]


def _logout(usuario: uuid.UUID, endpoint: str) -> None:
    assert _chamar("select public.revogar_inscricao_push_logout(%s, %s)", (usuario, endpoint)) == {"revogadas": 1}


def _reativar(usuario: uuid.UUID, endpoint: str) -> bool:
    return _chamar("select public.reativar_inscricao_push(%s, %s)", (usuario, endpoint))["reativada"]


def _marca(iid: str) -> tuple[bool, bool]:
    """(revogada, revogada_por_logout)"""
    with h.conectar() as c:
        return c.execute("select revogada_em is not null, revogada_por_logout from public.push_inscricoes "
                         "where id = %s", (iid,)).fetchone()  # type: ignore[return-value]


def _admin() -> uuid.UUID:
    return h.admin_ativo_qualquer() or h.criar_usuario("pmd-admin", "admin")


def test_desativar_e_reativar_nao_religa_sozinho() -> None:
    adm = _admin()
    u, ep = h.criar_usuario("pmd-desat"), _ep()
    iid = _inscrever(u, ep)
    _logout(u, ep)
    assert _marca(iid) == (True, True)
    h.alterar_perfil(u, adm, ativo=False)
    assert _marca(iid) == (True, False)  # a desativacao apaga a marca
    h.alterar_perfil(u, adm, ativo=True)
    assert _reativar(u, ep) is False
    assert _marca(iid) == (True, False)
    # O opt-in explicito (botao) continua funcionando.
    assert _inscrever(u, ep) == iid
    assert _marca(iid) == (False, False)


def test_sem_desativacao_segue_religando() -> None:
    adm = _admin()
    u, ep = h.criar_usuario("pmd-normal"), _ep()
    iid = _inscrever(u, ep)
    _logout(u, ep)
    h.alterar_perfil(u, adm, role="autorizador")  # muda papel, nao o ativo: marca preservada
    assert _marca(iid) == (True, True)
    assert _reativar(u, ep) is True


def test_desativar_um_nao_afeta_outro() -> None:
    adm = _admin()
    a, b = h.criar_usuario("pmd-a"), h.criar_usuario("pmd-b")
    ep_a, ep_b = _ep(), _ep()
    ia, ib = _inscrever(a, ep_a), _inscrever(b, ep_b)
    _logout(a, ep_a)
    _logout(b, ep_b)
    h.alterar_perfil(a, adm, ativo=False)
    assert _marca(ia) == (True, False)
    assert _marca(ib) == (True, True)
    assert _reativar(b, ep_b) is True


def test_funcao_e_gatilho_protegidos() -> None:
    with h.conectar() as c:
        for papel in ("anon", "authenticated", "service_role"):
            assert c.execute("select has_function_privilege(%s, %s::regprocedure, 'execute')",
                             (papel, FUNCAO)).fetchone() == (False,), papel
        secdef, cfg = c.execute("select prosecdef, proconfig from pg_proc where oid = %s::regprocedure",
                                (FUNCAO,)).fetchone()  # type: ignore[misc]
        assert secdef is True
        assert cfg and any(x.replace(" ", "") in ("search_path=", 'search_path=""') for x in cfg)
        assert c.execute("select tgenabled from pg_trigger where tgname = 'trg_perfis_limpa_marca_push_logout' "
                         "and tgrelid = 'public.perfis'::regclass").fetchone() == ("A",)


def test_migration_e_idempotente() -> None:
    with h.reaplicando_migration() as c:
        c.execute(MIGRATION)
        c.execute(MIGRATION)


def test_migration_sem_segredos_nem_dml_perigoso() -> None:
    baixo = MIGRATION.lower()
    for proibido in ("vapid", "private key", "drop table", "disable row level security", "truncate",
                     "delete from"):
        assert proibido not in baixo, proibido
