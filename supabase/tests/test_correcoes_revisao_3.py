"""Testes da migration 12: correcoes da terceira revisao.

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.
Cobre H1 (lock consultivo compartilhado x exclusivo, sem deadlock), M1 (bootstrap so com
seed em curso), B2 (ator na auditoria de admin criado), B1 (perfil do usuario do convite
criado na mesma transacao), reconvite apos revogar convite expirado e os limites
documentados (SQL livre pode forjar as variaveis de sessao).

    pytest supabase/tests/test_correcoes_revisao_3.py -m db_local
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]


def _revogar(cid: uuid.UUID, admin: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute(
            "update public.convites set revogado_em = now(), revogado_por = %s where id = %s", (admin, cid)
        )


# --- reconvite apos revogar convite expirado ---------------------------------------------
def test_reconvite_apos_revogar_convite_expirado() -> None:
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    cid, _ = h.novo_convite(admin, email, expirado=True)
    # expirado e nao usado continua ocupando o e-mail (indice nao inclui expira_em)...
    with pytest.raises(psycopg.errors.UniqueViolation):
        h.novo_convite(admin, email)
    # ...ate ser revogado (revogar convite expirado e permitido).
    _revogar(cid, admin)
    novo_id, th = h.novo_convite(admin, email)
    uid = h.criar_auth(email)
    with h.conectar() as c:
        c.execute("select public.consumir_convite(%s, %s, 'Convidado')", (th, uid))
    assert h.perfil_de(uid) == ("solicitante", True)
    assert novo_id != cid


# --- B2: ator na auditoria do admin criado ----------------------------------------------------
def test_admin_criado_por_admin_audita_com_o_ator_sem_duplicar() -> None:
    admin = h.criar_usuario("admin", "admin")
    uid = h.criar_auth(h.email_unico())
    with h.como("service_role") as c:
        c.execute(
            "insert into public.perfis (id, nome, unidade, role, alterado_por) "
            "values (%s, 'X', 'UEA', 'admin', %s)",
            (uid, admin),
        )
        linhas = c.execute(
            "select acao, ator_id, para from public.auditoria where tabela='perfis' and registro_id=%s order by id",
            (uid,),
        ).fetchall()
    assert linhas == [("perfil_criado", admin, "admin/ativo")]


def test_admin_criado_por_convite_audita_com_o_criador_do_convite() -> None:
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    _, th = h.novo_convite(admin, email, role="admin")
    uid = h.criar_auth(email)
    with h.conectar() as c:
        c.execute("select public.consumir_convite(%s, %s, 'Convidado')", (th, uid))
    assert h.auditoria_de("perfis", uid) == [("perfil_criado", admin, None, "admin/ativo")]


def test_perfil_nao_admin_continua_auditado_sem_ator() -> None:
    uid = h.criar_usuario("comum", "solicitante")
    assert h.auditoria_de("perfis", uid) == [("perfil_criado", None, None, "solicitante/ativo")]


# --- M1: bootstrap so com seed em curso ----------------------------------------------------------
def test_marca_de_seed_nao_dispensa_ator_havendo_admin_ativo() -> None:
    """Forjar seed_em_curso nao ajuda quando ja existe admin ativo: o ator continua exigido."""
    h.criar_usuario("admin", "admin")
    uid = h.criar_auth(h.email_unico())
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c, c.transaction():
            c.execute("select set_config('sisveiculos.seed_em_curso', '1', true)")
            c.execute("insert into public.perfis (id, nome, unidade, role) values (%s, 'X', 'UEA', 'admin')", (uid,))
    assert h.sqlstate(exc) == "AG002"


def test_bootstrap_exige_seed_em_curso() -> None:
    """So reproduzivel em banco SEM admin ativo (primeiro teste da suite num banco novo)."""
    if h.admin_ativo_qualquer() is not None:
        pytest.skip("ja existe admin ativo: bootstrap nao reproduzivel nesta base")
    uid = h.criar_auth(h.email_unico())
    with pytest.raises(psycopg.Error) as exc:  # sem a marca: recusado
        with h.conectar() as c:
            c.execute("insert into public.perfis (id, nome, unidade, role) values (%s, 'X', 'UEA', 'admin')", (uid,))
    assert h.sqlstate(exc) == "AG002"
    assert h.criar_usuario("admin", "admin")  # helper informa a marca: bootstrap passa


# --- B1: perfil do usuario do convite criado na mesma transacao ------------------------------------
def test_variavel_exata_com_perfil_antigo_e_recusada() -> None:
    admin = h.criar_usuario("admin", "admin")
    cid, _ = h.novo_convite(admin, h.email_unico())
    antigo = h.criar_usuario("antigo")  # perfil de outra transacao
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c, c.transaction():
            c.execute("select set_config('sisveiculos.convite_em_consumo', %s, true)", (f"{cid}:{antigo}",))
            c.execute("update public.convites set usado_em = now(), usado_por = %s where id = %s", (antigo, cid))
    assert h.sqlstate(exc) == "AG004"


def test_limite_documentado_sql_livre_forja_variavel_e_perfil_na_mesma_transacao() -> None:
    """LIMITE (documentado, nao e falha do teste): com SQL livre, inserir o perfil e forjar o
    par exato '<convite>:<usuario>' na mesma transacao marca o convite como usado. O trigger
    nao tem como provar o chamador; a barreira real e quem recebe credencial de service_role."""
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    cid, _ = h.novo_convite(admin, email)
    uid = h.criar_auth(email)
    with h.conectar() as c, c.transaction():
        c.execute("insert into public.perfis (id, nome, unidade) values (%s, 'Forjado', 'UEA')", (uid,))
        c.execute("select set_config('sisveiculos.convite_em_consumo', %s, true)", (f"{cid}:{uid}",))
        c.execute("update public.convites set usado_em = now(), usado_por = %s where id = %s", (uid, cid))
    with h.conectar() as c:
        (usado,) = c.execute("select usado_em is not null from public.convites where id=%s", (cid,)).fetchone() or (
            False,
        )
    assert usado


# --- H1: advisory lock compartilhado x exclusivo -----------------------------------------------------
def _segurar_advisory_admin(admin: uuid.UUID) -> "psycopg.Connection":
    """Transacao aberta que executou exigir_admin_ativo (segura o advisory exclusivo desde a migration 14)."""
    conn = psycopg.connect(h.DSN)
    conn.execute("select privado.exigir_admin_ativo(%s)", (admin,))
    return conn


def _em_thread(fn: Callable[[], None]) -> tuple[threading.Thread, dict, threading.Event]:
    res: dict = {}
    fim = threading.Event()

    def alvo() -> None:
        try:
            fn()
            res["erro"] = None
        except psycopg.Error as e:
            res["erro"] = e.sqlstate or repr(e)
        finally:
            fim.set()

    t = threading.Thread(target=alvo)
    t.start()
    return t, res, fim


def test_exclusivo_espera_compartilhado_sem_deadlock() -> None:
    """Acao de convite em curso (compartilhado) faz o UPDATE de role esperar, sem 40P01."""
    admin_a = h.criar_usuario("admin-a", "admin")
    admin_b = h.criar_usuario("admin-b", "admin")
    alvo = h.criar_usuario("alvo")
    segura = _segurar_advisory_admin(admin_b)
    try:
        t, res, fim = _em_thread(lambda: h.alterar_perfil(alvo, admin_a, role="autorizador"))
        time.sleep(1.0)
        assert not fim.is_set(), "o lock exclusivo deveria esperar o compartilhado"
        segura.commit()
    finally:
        segura.close()
    t.join(timeout=15)
    assert fim.is_set() and res["erro"] is None, res
    assert h.perfil_de(alvo) == ("autorizador", True)


def test_compartilhado_espera_exclusivo_sem_deadlock() -> None:
    """UPDATE de role em curso (exclusivo) faz a acao de convite esperar, sem 40P01."""
    admin_a = h.criar_usuario("admin-a", "admin")
    admin_b = h.criar_usuario("admin-b", "admin")
    alvo = h.criar_usuario("alvo")
    segura = psycopg.connect(h.DSN)
    try:
        segura.execute(
            "update public.perfis set role='autorizador', alterado_por=%s where id=%s", (admin_a, alvo)
        )
        t, res, fim = _em_thread(lambda: h.novo_convite(admin_b, h.email_unico()))
        time.sleep(1.0)
        assert not fim.is_set(), "o compartilhado deveria esperar o exclusivo"
        segura.commit()
    finally:
        segura.close()
    t.join(timeout=15)
    assert fim.is_set() and res["erro"] is None, res


def test_duas_acoes_de_convite_se_serializam_sem_deadlock() -> None:
    """Ajustado na migration 14 (M-A): o lock de exigir_admin_ativo passou a ser EXCLUSIVO,
    entao a segunda acao de convite espera a primeira terminar (antes conviviam)."""
    admin = h.criar_usuario("admin", "admin")
    segura = _segurar_advisory_admin(admin)
    try:
        t, res, fim = _em_thread(lambda: h.novo_convite(admin, h.email_unico()))
        time.sleep(1.0)
        assert not fim.is_set(), "o segundo deveria esperar o primeiro (lock exclusivo)"
        segura.commit()
    finally:
        segura.close()
    t.join(timeout=15)
    assert fim.is_set() and res["erro"] is None, res
