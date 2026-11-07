"""Testes da migration 15: advisory antes de linhas em convites (consumir_convite + gatilho de comando).

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.
Cobre tambem os cenarios multilinha da migration 14 (INSERT ... SELECT, ON CONFLICT, UPDATE
multilinha concorrente em veiculos).

    pytest supabase/tests/test_correcoes_revisao_5.py -m db_local
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

CHAVE_LOCK = "hashtextextended('sisveiculos.perfis_admin', 0)"


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


def _revogar(c: "psycopg.Connection", cid: uuid.UUID, admin: uuid.UUID) -> None:
    c.execute("update public.convites set revogado_em = now(), revogado_por = %s where id = %s", (admin, cid))


def _consumir(th: str, uid: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute("select public.consumir_convite(%s, %s, 'Convidado')", (th, uid))


# --- migration 15 (a)/(b): ACL e gatilho --------------------------------------------------------
def test_gatilho_de_comando_em_convites_enable_always() -> None:
    with h.conectar() as c:
        row = c.execute(
            "select t.tgenabled, (t.tgtype & 1) = 1, (t.tgtype & 4) <> 0, (t.tgtype & 16) <> 0 "
            "from pg_trigger t where t.tgrelid = 'public.convites'::regclass "
            "and t.tgname = 'trg_convites_trava_comando' and not t.tgisinternal"
        ).fetchone()
    assert row is not None
    enabled, por_linha, ins, upd = row
    assert enabled == "A" and not por_linha and ins and upd


def test_consumir_convite_acl_somente_service_role() -> None:
    with h.conectar() as c:
        for papel, esperado in (("anon", False), ("authenticated", False), ("service_role", True)):
            (ok,) = c.execute(
                "select has_function_privilege(%s, 'public.consumir_convite(text, uuid, text)', 'execute')",
                (papel,),
            ).fetchone() or (None,)
            assert ok is esperado, papel


def test_consumir_convite_espera_o_advisory_mesmo_de_outro_convite() -> None:
    """Prova que o advisory vem ANTES do FOR UPDATE: com o advisory segurado por outra sessao,
    consumir_convite espera mesmo sem nenhuma linha em disputa."""
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    _, th = h.novo_convite(admin, email)
    uid = h.criar_auth(email)
    with psycopg.connect(h.DSN) as segura:
        segura.execute(f"select pg_advisory_xact_lock({CHAVE_LOCK})")
        t, res, fim = _em_thread(lambda: _consumir(th, uid))
        time.sleep(1.0)
        assert not fim.is_set(), "consumir_convite deveria esperar o advisory antes de travar o convite"
        segura.commit()
    t.join(timeout=15)
    assert fim.is_set() and res["erro"] is None, res
    assert h.perfil_de(uid) == ("solicitante", True)


def test_revogar_convite_espera_o_advisory_gatilho_de_comando() -> None:
    admin = h.criar_usuario("admin", "admin")
    cid, _ = h.novo_convite(admin, h.email_unico())

    def revoga() -> None:
        with h.conectar() as c:
            _revogar(c, cid, admin)

    with psycopg.connect(h.DSN) as segura:
        segura.execute(f"select pg_advisory_xact_lock({CHAVE_LOCK})")
        t, res, fim = _em_thread(revoga)
        time.sleep(1.0)
        assert not fim.is_set(), "o gatilho de comando em convites deveria esperar o advisory"
        segura.commit()
    t.join(timeout=15)
    assert fim.is_set() and res["erro"] is None, res


# --- dois fluxos concorrentes de revogacao -----------------------------------------------------
def test_dois_fluxos_de_revogar_convites_nao_dao_deadlock() -> None:
    a = h.criar_usuario("admin-a", "admin")
    b = h.criar_usuario("admin-b", "admin")
    conv_a = [h.novo_convite(a, h.email_unico())[0] for _ in range(3)]
    conv_b = [h.novo_convite(b, h.email_unico())[0] for _ in range(3)]
    largada = threading.Barrier(2)

    def fluxo(admin: uuid.UUID, ids: list[uuid.UUID]) -> Callable[[], None]:
        def fn() -> None:
            largada.wait(timeout=60)
            with psycopg.connect(h.DSN) as c:  # uma transacao, varios comandos, commit ao sair
                for cid in ids:
                    _revogar(c, cid, admin)
                    time.sleep(0.2)  # abre janela para o outro fluxo

        return fn

    t1, r1, f1 = _em_thread(fluxo(a, conv_a))
    t2, r2, f2 = _em_thread(fluxo(b, conv_b[::-1]))
    t1.join(timeout=90)
    t2.join(timeout=90)
    assert f1.is_set() and f2.is_set()
    assert r1["erro"] is None and r2["erro"] is None, (r1, r2)
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.convites where id = any(%s) and revogado_em is not null",
            (conv_a + conv_b,),
        ).fetchone() or (0,)
    assert n == 6


def test_revogar_e_consumir_convite_concorrentes_nao_dao_deadlock() -> None:
    admin = h.criar_usuario("admin", "admin")
    for _ in range(5):
        email = h.email_unico()
        cid, th = h.novo_convite(admin, email)
        uid = h.criar_auth(email)
        largada = threading.Barrier(2)

        def revoga() -> None:
            largada.wait(timeout=60)
            with h.conectar() as c:
                _revogar(c, cid, admin)

        def consome() -> None:
            largada.wait(timeout=60)
            _consumir(th, uid)

        t1, r1, f1 = _em_thread(revoga)
        t2, r2, f2 = _em_thread(consome)
        t1.join(timeout=90)
        t2.join(timeout=90)
        assert f1.is_set() and f2.is_set()
        assert r1["erro"] != "40P01" and r2["erro"] != "40P01", (r1, r2)
        assert r2["erro"] in (None, "AG005"), r2  # consumiu ou perdeu para a revogacao


# --- multilinha: INSERT ... SELECT -------------------------------------------------------------
def test_insert_select_multilinha_em_perfis() -> None:
    uids = [h.criar_auth(h.email_unico()) for _ in range(3)]
    with h.conectar() as c:
        n = c.execute(
            "insert into public.perfis (id, nome, unidade) "
            "select id, 'Multi', 'UEA' from auth.users where id = any(%s)",
            (uids,),
        ).rowcount
    assert n == 3
    for u in uids:
        assert h.auditoria_de("perfis", u) == [("perfil_criado", None, None, "solicitante/ativo")]


def test_insert_select_multilinha_em_perfis_espera_o_advisory() -> None:
    uids = [h.criar_auth(h.email_unico()) for _ in range(2)]

    def insere() -> None:
        with h.conectar() as c:
            c.execute(
                "insert into public.perfis (id, nome, unidade) "
                "select id, 'Multi', 'UEA' from auth.users where id = any(%s)",
                (uids,),
            )

    with psycopg.connect(h.DSN) as segura:
        segura.execute(f"select pg_advisory_xact_lock({CHAVE_LOCK})")
        t, res, fim = _em_thread(insere)
        time.sleep(1.0)
        assert not fim.is_set()
        segura.commit()
    t.join(timeout=15)
    assert fim.is_set() and res["erro"] is None, res


def test_insert_select_multilinha_em_veiculos() -> None:
    admin = h.criar_usuario("admin", "admin")
    with h.conectar() as c:
        ids = [
            r[0]
            for r in c.execute(
                "insert into public.veiculos (descricao, alterado_por) "
                "select 'multi-' || g || '-' || left(md5(random()::text), 6), %s "
                "from generate_series(1, 3) g returning id",
                (admin,),
            ).fetchall()
        ]
    assert len(ids) == 3
    for vid in ids:
        assert [(x[0], x[1]) for x in h.auditoria_de("veiculos", vid)] == [("veiculo_criado", admin)]


# --- ON CONFLICT ---------------------------------------------------------------------------------
def test_on_conflict_do_nothing_em_perfis_e_veiculos() -> None:
    admin = h.criar_usuario("admin", "admin")
    uid = h.criar_usuario("comum")
    vid = h.novo_veiculo(admin)
    antes_p = len(h.auditoria_de("perfis", uid))
    antes_v = len(h.auditoria_de("veiculos", vid))
    with h.conectar() as c:
        n1 = c.execute(
            "insert into public.perfis (id, nome, unidade) values (%s, 'Outro', 'UEA') on conflict (id) do nothing",
            (uid,),
        ).rowcount
        n2 = c.execute(
            "insert into public.veiculos (id, descricao, alterado_por) values (%s, 'x', %s) "
            "on conflict (id) do nothing",
            (vid, admin),
        ).rowcount
    assert (n1, n2) == (0, 0)
    assert len(h.auditoria_de("perfis", uid)) == antes_p
    assert len(h.auditoria_de("veiculos", vid)) == antes_v


def test_on_conflict_do_update_em_perfis_so_nome_e_permitido() -> None:
    uid = h.criar_usuario("comum")
    with h.conectar() as c:
        c.execute(
            "insert into public.perfis (id, nome, unidade) values (%s, 'Renomeado', 'UEA') "
            "on conflict (id) do update set nome = excluded.nome",
            (uid,),
        )
        (nome,) = c.execute("select nome from public.perfis where id = %s", (uid,)).fetchone() or ("",)
    assert nome == "Renomeado"


def test_on_conflict_do_update_em_perfis_mudando_papel_sem_responsavel_e_recusado() -> None:
    admin = h.criar_usuario("admin", "admin")
    uid = h.criar_usuario("comum")
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                "insert into public.perfis (id, nome, unidade, role) values (%s, 'X', 'UEA', 'autorizador') "
                "on conflict (id) do update set role = excluded.role",
                (uid,),
            )
    assert h.sqlstate(exc) == "AG002"
    assert h.perfil_de(uid) == ("solicitante", True)
    assert admin  # admin existe: o erro vem da falta de responsavel, nao da falta de admin


# --- UPDATE multilinha concorrente em veiculos -----------------------------------------------------
def test_dois_updates_multilinha_concorrentes_em_veiculos_nao_dao_deadlock() -> None:
    a = h.criar_usuario("admin-a", "admin")
    b = h.criar_usuario("admin-b", "admin")
    ids = [h.novo_veiculo(a) for _ in range(6)]
    largada = threading.Barrier(2)

    def fluxo(admin: uuid.UUID, ordem: list[uuid.UUID]) -> Callable[[], None]:
        def fn() -> None:
            largada.wait(timeout=60)
            for _ in range(5):
                with h.conectar() as c:
                    c.execute(
                        "update public.veiculos set descricao = 'u-' || left(md5(random()::text), 8), "
                        "alterado_por = %s where id = any(%s)",
                        (admin, ordem),
                    )

        return fn

    t1, r1, f1 = _em_thread(fluxo(a, ids))
    t2, r2, f2 = _em_thread(fluxo(b, ids[::-1]))
    t1.join(timeout=60)
    t2.join(timeout=60)
    assert f1.is_set() and f2.is_set()
    assert r1["erro"] is None and r2["erro"] is None, (r1, r2)
