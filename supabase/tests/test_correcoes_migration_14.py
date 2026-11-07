"""Testes da migration 14: lock exclusivo unico (M-A), gatilho de comando (M-B) e baixos 1/3/4.

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.

    pytest supabase/tests/test_correcoes_migration_14.py -m db_local
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


# --- M-A: modo unico exclusivo --------------------------------------------------------------
def test_exigir_admin_ativo_toma_lock_exclusivo() -> None:
    admin = h.criar_usuario("admin", "admin")
    with psycopg.connect(h.DSN) as c:
        c.execute("select privado.exigir_admin_ativo(%s)", (admin,))
        modos = {
            r[0]
            for r in c.execute(
                "select mode from pg_locks where locktype = 'advisory' and pid = pg_backend_pid()"
            ).fetchall()
        }
        c.rollback()
    assert modos == {"ExclusiveLock"}


def _fluxo_convite_e_desativacao(
    admin: uuid.UUID, alvo: uuid.UUID, pausa: threading.Event
) -> Callable[[], None]:
    """Uma transacao: revoga convite do admin e depois desativa um perfil (S -> X na migration 12)."""

    def fn() -> None:
        cid, _ = h.novo_convite(admin, h.email_unico())
        with psycopg.connect(h.DSN) as c:  # fecha com commit se nao houver excecao
            c.execute(
                "update public.convites set revogado_em = now(), revogado_por = %s where id = %s",
                (admin, cid),
            )
            pausa.wait(timeout=1.5)  # abre a janela para a outra transacao entrar
            c.execute(
                "update public.perfis set ativo = false, alterado_por = %s where id = %s", (admin, alvo)
            )

    return fn


def test_convite_e_desativacao_concorrentes_nao_dao_deadlock() -> None:
    a = h.criar_usuario("admin-a", "admin")
    b = h.criar_usuario("admin-b", "admin")
    alvo_a = h.criar_usuario("alvo-a")
    alvo_b = h.criar_usuario("alvo-b")
    pausa = threading.Event()  # nunca sinalizado: cada fluxo espera o timeout
    t1, r1, f1 = _em_thread(_fluxo_convite_e_desativacao(a, alvo_a, pausa))
    t2, r2, f2 = _em_thread(_fluxo_convite_e_desativacao(b, alvo_b, pausa))
    t1.join(timeout=90)
    t2.join(timeout=90)
    assert f1.is_set() and f2.is_set()
    assert r1["erro"] is None and r2["erro"] is None, (r1, r2)
    assert h.perfil_de(alvo_a)[1] is False and h.perfil_de(alvo_b)[1] is False


# --- M-B: gatilho de comando ---------------------------------------------------------------------
def _bloqueia_enquanto_lock_externo(sql: str, params: tuple) -> None:
    """Com o advisory segurado por outra sessao, o comando espera (mesmo sem tocar em linha)."""
    with psycopg.connect(h.DSN) as segura:
        segura.execute(f"select pg_advisory_xact_lock({CHAVE_LOCK})")

        def comando() -> None:
            with h.conectar() as c:
                c.execute(sql, params)

        t, res, fim = _em_thread(comando)
        time.sleep(1.0)
        assert not fim.is_set(), "o gatilho de comando deveria esperar o advisory"
        segura.commit()
    t.join(timeout=15)
    assert fim.is_set() and res["erro"] is None, res


def test_update_de_perfil_sem_mudar_papel_espera_o_advisory() -> None:
    uid = h.criar_usuario("x")
    _bloqueia_enquanto_lock_externo("update public.perfis set nome = 'Novo' where id = %s", (uid,))


def test_update_de_perfil_com_zero_linhas_espera_o_advisory() -> None:
    _bloqueia_enquanto_lock_externo("update public.perfis set nome = 'Z' where id = %s", (uuid.uuid4(),))


def test_update_de_veiculo_espera_o_advisory() -> None:
    v = h.novo_veiculo()
    _bloqueia_enquanto_lock_externo("update public.veiculos set descricao = descricao where id = %s", (v,))


def test_update_multilinha_nao_trava_linha_antes_do_advisory() -> None:
    """Cenario do M-B: T1 segura o advisory e depois quer uma linha que T2 (multilinha) travaria
    antes de pedir o advisory. Com o gatilho de comando, T2 espera de inicio e nao ha 40P01."""
    a = h.criar_usuario("admin-a", "admin")
    b = h.criar_usuario("admin-b", "admin")
    p1 = h.criar_usuario("p1")
    p2 = h.criar_usuario("p2")
    p3 = h.criar_usuario("p3")
    t1 = psycopg.connect(h.DSN)
    try:
        t1.execute("update public.perfis set role='autorizador', alterado_por=%s where id=%s", (a, p1))

        def multilinha() -> None:
            with h.conectar() as c:
                c.execute(
                    "update public.perfis set role='autorizador', alterado_por=%s where id = any(%s)",
                    (b, [p2, p3]),
                )

        t, res, fim = _em_thread(multilinha)
        time.sleep(1.0)
        assert not fim.is_set(), "a multilinha deveria esperar o advisory antes de travar linhas"
        t1.execute("update public.perfis set ativo=false, alterado_por=%s where id=%s", (a, p2))
        t1.commit()
    finally:
        t1.close()
    t.join(timeout=15)
    assert fim.is_set() and res["erro"] is None, res
    assert h.perfil_de(p2) == ("autorizador", False)
    assert h.perfil_de(p3) == ("autorizador", True)


def test_gatilhos_de_comando_existem_em_enable_always() -> None:
    with h.conectar() as c:
        rows = c.execute(
            "select c.relname, t.tgenabled, (t.tgtype & 1) = 1 as por_linha, (t.tgtype & 4) <> 0 as ins, "
            "(t.tgtype & 16) <> 0 as upd from pg_trigger t join pg_class c on c.oid = t.tgrelid "
            "where c.relnamespace = 'public'::regnamespace and not t.tgisinternal "
            "and t.tgname in ('trg_perfis_trava_comando', 'trg_veiculos_trava_comando')"
        ).fetchall()
    assert sorted(r[0] for r in rows) == ["perfis", "veiculos"]
    for _, enabled, por_linha, ins, upd in rows:
        assert enabled == "A" and not por_linha and ins and upd


def test_funcao_do_gatilho_de_comando_sem_acesso_da_api() -> None:
    with h.conectar() as c:
        for papel in ("anon", "authenticated", "public"):
            (ok,) = c.execute(
                "select has_function_privilege(%s, 'public.travar_admin_comando()', 'execute')", (papel,)
            ).fetchone() or (True,)
            assert not ok, papel


def test_seed_com_admin_existente_retorna_ja_existe_admin() -> None:
    """Cobre so o ramo 'ja_existe_admin' de semear_primeiro_admin (retorna sem inserir).
    O caminho do seed com gatilho de comando em banco virgem fica em test_seed_primeiro_admin.py."""
    h.criar_usuario("admin", "admin")
    with h.conectar() as c:
        (r,) = c.execute("select privado.semear_primeiro_admin('nao-existe@teste.invalid')").fetchone() or ("",)
    assert r == "ja_existe_admin"


# --- Baixo 1: ator_criacao so para admin ---------------------------------------------------------
def test_variavel_ator_criacao_forjada_para_nao_admin_e_ignorada() -> None:
    admin = h.criar_usuario("admin", "admin")
    uid = h.criar_auth(h.email_unico())
    with h.conectar() as c, c.transaction():
        c.execute(
            "select set_config('sisveiculos.ator_criacao_' || replace(%s::text, '-', ''), %s::text, true)",
            (uid, admin),
        )
        c.execute("insert into public.perfis (id, nome, unidade, role) values (%s, 'X', 'UEA', 'solicitante')", (uid,))
        (resto,) = c.execute(
            "select current_setting('sisveiculos.ator_criacao_' || replace(%s::text, '-', ''), true)", (uid,)
        ).fetchone() or ("",)
    assert h.auditoria_de("perfis", uid) == [("perfil_criado", None, None, "solicitante/ativo")]
    assert not resto


def test_admin_criado_por_admin_continua_com_ator() -> None:
    admin = h.criar_usuario("admin", "admin")
    novo = h.criar_usuario("admin2", "admin", por=admin)  # ator explicito: sem depender de outros admins
    assert h.auditoria_de("perfis", novo) == [("perfil_criado", admin, None, "admin/ativo")]


# --- Baixo 3: auditoria de veiculos intacta --------------------------------------------------------
def test_auditoria_de_veiculos_continua_com_ator_e_consome_a_variavel() -> None:
    admin = h.criar_usuario("admin", "admin")
    v = h.novo_veiculo(admin)
    h.alterar_veiculo(v, admin, "descricao='novo'")
    assert [(x[0], x[1]) for x in h.auditoria_de("veiculos", v)] == [
        ("veiculo_criado", admin),
        ("veiculo_alterado", admin),
    ]
    with pytest.raises(psycopg.Error) as exc:  # a variavel nao vaza para a proxima alteracao
        with h.conectar() as c:
            c.execute("update public.veiculos set descricao='outro' where id=%s", (v,))
    assert h.sqlstate(exc) == "AG002"


# --- Baixo 4: ON CONFLICT DO UPDATE ------------------------------------------------------------------
def test_on_conflict_do_update_sem_repassar_responsavel_e_recusado() -> None:
    admin = h.criar_usuario("admin", "admin")
    v = h.novo_veiculo(admin)
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                "insert into public.veiculos (id, descricao, alterado_por) values (%s, 'x', %s) "
                "on conflict (id) do update set descricao = excluded.descricao",
                (v, admin),
            )
    assert h.sqlstate(exc) == "AG002"


def test_on_conflict_do_update_repassando_responsavel_literal_funciona() -> None:
    admin = h.criar_usuario("admin", "admin")
    v = h.novo_veiculo(admin)
    with h.conectar() as c:
        c.execute(
            "insert into public.veiculos (id, descricao, alterado_por) values (%s, 'y', %s) "
            "on conflict (id) do update set descricao = excluded.descricao, alterado_por = %s",
            (v, admin, admin),
        )
    assert h.auditoria_de("veiculos", v)[-1][:2] == ("veiculo_alterado", admin)
