"""Migration 20261206120000_push_reativar_apos_logout.sql: Web Push religa apos logout/login do MESMO usuario.

Cobre: coluna revogada_por_logout + CHECK; gatilho (linha ativa sempre sem marca; marca so na transicao
ativa -> revogada); revogar_inscricao_push_logout (so a propria, idempotente); reativar_inscricao_push (mesmo
usuario, so revogacao por logout, ate 30 dias, perfil ativo, limite de 10, nao revela nem reatribui a outro);
grants (so service_role), SECURITY DEFINER com search_path vazio e idempotencia.

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). Rodar sem paralelismo.

    pytest supabase/tests/test_push_reativar_apos_logout.py -m db_local -p no:xdist
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
    Path(__file__).resolve().parents[1] / "migrations" / "20261206120000_push_reativar_apos_logout.sql"
).read_text(encoding="utf-8")
RPCS = (
    "public.revogar_inscricao_push_logout(uuid, text)",
    "public.reativar_inscricao_push(uuid, text)",
)
P256 = "B" * 87
AUTH = "a" * 22


def _chamar(sql: str, params: tuple = ()):
    with h.conectar() as c:
        return c.execute(sql, params).fetchone()[0]  # type: ignore[index]


def _erro(sql: str, params: tuple = ()) -> str | None:
    with h.conectar() as c, pytest.raises(psycopg.Error) as exc:
        c.execute(sql, params)
    return h.sqlstate(exc)


def _ep() -> str:
    return f"https://fcm.googleapis.com/fcm/send/{uuid.uuid4().hex}"


def _inscrever(usuario: uuid.UUID, endpoint: str) -> str:
    return _chamar("select public.registrar_inscricao_push(%s, %s, %s, %s)",
                   (usuario, endpoint, P256, AUTH))["id"]


def _logout(usuario: uuid.UUID, endpoint: str) -> dict:
    return _chamar("select public.revogar_inscricao_push_logout(%s, %s)", (usuario, endpoint))


def _reativar(usuario: uuid.UUID, endpoint: str) -> bool:
    return _chamar("select public.reativar_inscricao_push(%s, %s)", (usuario, endpoint))["reativada"]


def _linha(iid: str) -> tuple:
    """(usuario_id, revogada_em is not null, revogada_por_logout, falhas)"""
    with h.conectar() as c:
        return c.execute("select usuario_id, revogada_em is not null, revogada_por_logout, falhas "
                         "from public.push_inscricoes where id = %s", (iid,)).fetchone()  # type: ignore[return-value]


# --- estrutura e permissoes ---------------------------------------------------------------------
def test_coluna_check_e_gatilho_sempre_ativo() -> None:
    with h.conectar() as c:
        assert c.execute("select is_nullable, column_default from information_schema.columns "
                         "where table_schema = 'public' and table_name = 'push_inscricoes' "
                         "and column_name = 'revogada_por_logout'").fetchone() == ("NO", "false")
        assert c.execute("select tgenabled from pg_trigger where tgname = 'trg_push_inscricoes_logout' "
                         "and tgrelid = 'public.push_inscricoes'::regclass").fetchone() == ("A",)


@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_rpcs_negadas_a_anon_e_authenticated(papel: str) -> None:
    with h.conectar() as c:
        for rpc in RPCS + ("public.push_inscricoes_marca_logout()",):
            assert c.execute("select has_function_privilege(%s, %s::regprocedure, 'execute')",
                             (papel, rpc)).fetchone() == (False,), rpc


def test_service_role_executa_so_as_rpcs() -> None:
    with h.conectar() as c:
        for rpc in RPCS:
            assert c.execute("select has_function_privilege('service_role', %s::regprocedure, 'execute')",
                             (rpc,)).fetchone() == (True,), rpc
        assert c.execute("select has_function_privilege('service_role', "
                         "'public.push_inscricoes_marca_logout()'::regprocedure, 'execute')").fetchone() == (False,)
        rows = c.execute("select proname, prosecdef, proconfig from pg_proc where pronamespace = "
                         "'public'::regnamespace and proname in ('revogar_inscricao_push_logout', "
                         "'reativar_inscricao_push', 'push_inscricoes_marca_logout')").fetchall()
    assert len(rows) == 3
    for nome, secdef, cfg in rows:
        # O gatilho e SECURITY INVOKER (so altera NEW); as RPCs, DEFINER.
        assert secdef is (nome != "push_inscricoes_marca_logout"), nome
        assert cfg and any(x.replace(" ", "") in ("search_path=", 'search_path=""') for x in cfg), nome


def test_parametros_invalidos() -> None:
    for rpc in ("revogar_inscricao_push_logout", "reativar_inscricao_push"):
        assert _erro(f"select public.{rpc}(%s, %s)", (None, _ep())) == "AG042"
        assert _erro(f"select public.{rpc}(%s, %s)", (uuid.uuid4(), None)) == "AG042"
        assert _erro(f"select public.{rpc}(%s, %s)", (uuid.uuid4(), "https://x/" + "a" * 2050)) == "AG042"


# --- fluxo ----------------------------------------------------------------------------------------
def test_logout_e_login_do_mesmo_usuario_religa() -> None:
    u, ep = h.criar_usuario("prl-mesmo"), _ep()
    iid = _inscrever(u, ep)
    assert _logout(u, ep) == {"revogadas": 1}
    assert _linha(iid) == (u, True, True, 0)
    assert _chamar("select public.listar_inscricoes_push(%s)", (u,)) == []
    assert _reativar(u, ep) is True
    assert _linha(iid) == (u, False, False, 0)  # marca limpa ao religar
    assert len(_chamar("select public.listar_inscricoes_push(%s)", (u,))) == 1
    assert _reativar(u, ep) is True  # idempotente


def test_logout_so_revoga_a_propria_e_e_idempotente() -> None:
    dono, outro, ep = h.criar_usuario("prl-dono"), h.criar_usuario("prl-outro"), _ep()
    iid = _inscrever(dono, ep)
    assert _logout(outro, ep) == {"revogadas": 0}
    assert _linha(iid)[1] is False
    assert _logout(dono, ep) == {"revogadas": 1}
    assert _logout(dono, ep) == {"revogadas": 0}


def test_outro_usuario_nao_religa_nem_reatribui() -> None:
    a, b, ep = h.criar_usuario("prl-a"), h.criar_usuario("prl-b"), _ep()
    iid = _inscrever(a, ep)
    assert _reativar(b, ep) is False  # ativa de A
    _logout(a, ep)
    assert _reativar(b, ep) is False  # revogada por logout de A
    assert _linha(iid) == (a, True, True, 0)
    assert _reativar(uuid.uuid4(), _ep()) is False  # inexistente: mesma resposta


def test_revogacao_manual_expirada_ou_por_falhas_nunca_religa() -> None:
    u = h.criar_usuario("prl-motivos")
    manual, exp, falhas = _ep(), _ep(), _ep()
    _inscrever(u, manual)
    _chamar("select public.revogar_inscricao_push(%s, %s)", (u, manual))
    i_exp = _inscrever(u, exp)
    _chamar("select public.marcar_resultado_inscricao_push(%s, false, true)", (i_exp,))
    i_f = _inscrever(u, falhas)
    for _ in range(5):
        _chamar("select public.marcar_resultado_inscricao_push(%s, false, false)", (i_f,))
    for ep in (manual, exp, falhas):
        assert _reativar(u, ep) is False, ep


def test_marca_nao_nasce_em_linha_ja_revogada_por_outro_motivo() -> None:
    u, ep = h.criar_usuario("prl-marca"), _ep()
    iid = _inscrever(u, ep)
    _chamar("select public.revogar_inscricao_push(%s, %s)", (u, ep))
    with h.conectar() as c:
        c.execute("update public.push_inscricoes set revogada_por_logout = true where id = %s", (iid,))
    assert _linha(iid)[2] is False
    assert _reativar(u, ep) is False


def test_check_impede_marca_em_linha_ativa() -> None:
    u, ep = h.criar_usuario("prl-check"), _ep()
    iid = _inscrever(u, ep)
    with h.conectar() as c:
        c.execute("update public.push_inscricoes set revogada_por_logout = true where id = %s", (iid,))
    assert _linha(iid) == (u, False, False, 0)  # gatilho zera em linha ativa


def test_reatribuir_pelo_botao_limpa_a_marca() -> None:
    a, b, ep = h.criar_usuario("prl-ra"), h.criar_usuario("prl-rb"), _ep()
    iid = _inscrever(a, ep)
    _logout(a, ep)
    assert _inscrever(b, ep) == iid  # opt-in explicito de B
    assert _linha(iid) == (b, False, False, 0)
    assert _reativar(a, ep) is False  # A nao toma de volta sem o botao


def test_prazo_de_30_dias() -> None:
    u, ep = h.criar_usuario("prl-prazo"), _ep()
    iid = _inscrever(u, ep)
    _logout(u, ep)
    with h.conectar() as c:
        c.execute("update public.push_inscricoes set revogada_em = now() - interval '31 days' where id = %s",
                  (iid,))
    assert _reativar(u, ep) is False
    assert _linha(iid)[1] is True


def test_perfil_inativo_nao_religa() -> None:
    adm = h.admin_ativo_qualquer() or h.criar_usuario("prl-admin", "admin")
    u, ep = h.criar_usuario("prl-inativo"), _ep()
    iid = _inscrever(u, ep)
    _logout(u, ep)
    with h.conectar() as c:
        c.execute("update public.perfis set ativo = false, alterado_por = %s where id = %s", (adm, u))
    assert _reativar(u, ep) is False
    assert _linha(iid)[1] is True


def test_limite_de_10_nao_religa_sem_erro() -> None:
    u, ep = h.criar_usuario("prl-limite"), _ep()
    iid = _inscrever(u, ep)
    _logout(u, ep)
    for _ in range(10):
        _inscrever(u, _ep())
    assert _reativar(u, ep) is False
    assert _linha(iid)[1] is True


def test_migration_e_idempotente() -> None:
    with h.reaplicando_migration() as c:
        c.execute(MIGRATION)
        c.execute(MIGRATION)


def test_migration_sem_segredos_nem_dml_perigoso() -> None:
    baixo = MIGRATION.lower()
    for proibido in ("vapid", "private key", "drop table", "disable row level security", "truncate",
                     "delete from"):
        assert proibido not in baixo, proibido
