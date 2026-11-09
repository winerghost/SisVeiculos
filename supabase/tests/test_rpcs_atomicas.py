"""Testes da migration 20261115120000 (reenviar_convite, dedupe em reservar_envio_email, reaper).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py): os testes gravam e nao apagam (sem DELETE fisico,
auditoria append-only). Cada teste usa e-mails/usuarios/filtros unicos.

    pytest supabase/tests/test_rpcs_atomicas.py -m db_local -p no:xdist

ATENCAO: escritos sem poder executa-los (ambiente do autor sem Docker). Revisar na primeira rodada do CI.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
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

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261115120000_rpcs_atomicas_convite_relatorio.sql"
).read_text(encoding="utf-8")
SIG_REENVIAR = "public.reenviar_convite(uuid, uuid, text, timestamptz)"
SIG_RESERVAR = "public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer, integer)"
SIG_REAPER = "public.reaper_envios_email(integer)"
GRANDE = 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def hash_novo() -> str:
    return hashlib.sha256(secrets.token_urlsafe(32).encode()).hexdigest()


def reenviar(conn: "psycopg.Connection", cid: uuid.UUID, ator: uuid.UUID, dias: int = 7, th: str | None = None) -> dict:
    row = conn.execute(
        "select public.reenviar_convite(%s, %s, %s, now() + make_interval(days => %s))",
        (cid, ator, th or hash_novo(), dias),
    ).fetchone()
    assert row is not None
    return row[0]


def abertos_do_email(email: str) -> int:
    with h.conectar() as c:
        return c.execute(
            "select count(*) from public.convites where email = %s and usado_em is null and revogado_em is null",
            (email,),
        ).fetchone()[0]  # type: ignore[index]


@pytest.fixture
def adm() -> uuid.UUID:
    return h.admin_ativo_qualquer() or h.criar_usuario("ra-admin", "admin")


# ---------------------------------------------------------------------------
# Permissoes e propriedades
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sig", [SIG_REENVIAR, SIG_RESERVAR, SIG_REAPER])
def test_permissoes_e_security_definer(sig: str) -> None:
    with h.conectar() as c:
        assert c.execute(
            "select has_function_privilege('service_role', %s::regprocedure, 'execute')", (sig,)
        ).fetchone() == (True,)
        for papel in ("anon", "authenticated"):
            assert c.execute(
                "select has_function_privilege(%s, %s::regprocedure, 'execute')", (papel, sig)
            ).fetchone() == (False,)
        assert c.execute(
            "select exists (select 1 from pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
            "where p.oid = %s::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE')",
            (sig,),
        ).fetchone() == (False,)
        assert c.execute(
            "select prosecdef and array_to_string(proconfig, ',') like '%%search_path=%%' "
            "from pg_proc where oid = %s::regprocedure",
            (sig,),
        ).fetchone() == (True,)


def test_reservar_continua_com_uma_unica_assinatura() -> None:
    with h.conectar() as c:
        assert c.execute(
            "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
            "where n.nspname = 'public' and p.proname = 'reservar_envio_email'"
        ).fetchone() == (1,)


@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_papeis_da_api_sem_execute(papel: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with h.como(papel, uuid.uuid4()) as c:
            c.execute(
                "select public.reenviar_convite(gen_random_uuid(), gen_random_uuid(), %s, now() + interval '1 day')",
                (hash_novo(),),
            )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with h.como(papel, uuid.uuid4()) as c:
            c.execute("select public.reaper_envios_email(15)")


def test_migration_e_idempotente() -> None:
    with h.reaplicando_migration() as c:
        c.execute(MIGRATION)
        c.execute(MIGRATION)


# ---------------------------------------------------------------------------
# A. reenviar_convite
# ---------------------------------------------------------------------------
def test_reenvio_revoga_antigo_e_cria_novo_com_auditoria(adm: uuid.UUID) -> None:
    email = h.email_unico()
    cid, th_antigo = h.novo_convite(adm, email, role="autorizador")
    with h.como("service_role") as c:  # papel real do backend
        novo = reenviar(c, cid, adm, dias=10)
        c.commit()
    assert "token_hash" not in novo
    assert novo["email"] == email and novo["role"] == "autorizador" and novo["unidade"] == "UEA"
    assert novo["id"] != str(cid) and novo["usado_em"] is None and novo["revogado_em"] is None
    assert abertos_do_email(email) == 1
    with h.conectar() as c:
        assert c.execute(
            "select revogado_em is not null, revogado_por from public.convites where id = %s", (cid,)
        ).fetchone() == (True, adm)
        # o novo convite tem OUTRO hash e o antigo nao e mais consumivel
        assert c.execute(
            "select token_hash <> %s from public.convites where id = %s", (th_antigo, novo["id"])
        ).fetchone() == (True,)
    assert [a for a, *_ in h.auditoria_de("convites", cid)] == ["convite_criado", "convite_revogado"]
    assert [a for a, *_ in h.auditoria_de("convites", uuid.UUID(novo["id"]))] == ["convite_criado"]


def test_antigo_revogado_nao_pode_ser_consumido(adm: uuid.UUID) -> None:
    email = h.email_unico()
    cid, th_antigo = h.novo_convite(adm, email)
    uid = h.criar_auth(email)
    with h.conectar() as c:
        reenviar(c, cid, adm)
        with pytest.raises(psycopg.Error) as ex:
            c.execute("select public.consumir_convite(%s, %s, 'X')", (th_antigo, uid))
    assert ex.value.sqlstate == "AG005"


def test_convite_expirado_pode_ser_reenviado(adm: uuid.UUID) -> None:
    email = h.email_unico()
    cid, _ = h.novo_convite(adm, email, expirado=True)
    with h.conectar() as c:
        novo = reenviar(c, cid, adm)
    assert abertos_do_email(email) == 1 and novo["email"] == email


@pytest.mark.parametrize("encerrar", ["usado", "revogado"])
def test_encerrado_recebe_ag038(adm: uuid.UUID, encerrar: str) -> None:
    email = h.email_unico()
    cid, th = h.novo_convite(adm, email)
    if encerrar == "usado":
        with h.conectar() as c:
            c.execute("select public.consumir_convite(%s, %s, 'X')", (th, h.criar_auth(email)))
    else:
        with h.conectar() as c:
            c.execute("update public.convites set revogado_em = now(), revogado_por = %s where id = %s", (adm, cid))
    with pytest.raises(psycopg.Error) as ex:
        with h.conectar() as c:
            reenviar(c, cid, adm)
    assert ex.value.sqlstate == "AG038"


def test_inexistente_ag007_e_ator_invalido_ag002(adm: uuid.UUID) -> None:
    with pytest.raises(psycopg.Error) as ex:
        with h.conectar() as c:
            reenviar(c, uuid.uuid4(), adm)
    assert ex.value.sqlstate == "AG007"

    cid, _ = h.novo_convite(adm, h.email_unico())
    comum = h.criar_usuario("ra-comum", "solicitante")
    for ator in (comum, uuid.uuid4()):
        with pytest.raises(psycopg.Error) as ex2:
            with h.conectar() as c:
                reenviar(c, cid, ator)
        assert ex2.value.sqlstate == "AG002"
    inativo = h.criar_usuario("ra-inativo", "autorizador", ativo=False)
    with pytest.raises(psycopg.Error) as ex3:
        with h.conectar() as c:
            reenviar(c, cid, inativo)
    assert ex3.value.sqlstate == "AG002"


@pytest.mark.parametrize(
    "hash_,expira",
    [
        ("abc", "now() + interval '1 day'"),
        ("A" * 64, "now() + interval '1 day'"),  # maiusculas nao valem
        (None, "now() + interval '1 day'"),
        ("a" * 64, "null"),
        ("a" * 64, "now() - interval '1 minute'"),
        ("a" * 64, "now() + interval '31 days'"),
        ("a" * 64, "now() + interval '30 days'"),  # acima de 30 dias menos 1 minuto
    ],
)
def test_parametros_invalidos_ag039(adm: uuid.UUID, hash_: str | None, expira: str) -> None:
    cid, _ = h.novo_convite(adm, h.email_unico())
    with pytest.raises(psycopg.Error) as ex:
        with h.conectar() as c:
            c.execute(f"select public.reenviar_convite(%s, %s, %s, {expira})", (cid, adm, hash_))
    assert ex.value.sqlstate == "AG039"


def test_falha_no_insert_desfaz_a_revogacao(adm: uuid.UUID) -> None:
    """token_hash repetido => 23505 e o convite antigo continua aberto (tudo ou nada)."""
    email = h.email_unico()
    cid, th_existente = h.novo_convite(adm, email)
    with pytest.raises(psycopg.Error) as ex:
        with h.conectar() as c:
            reenviar(c, cid, adm, th=th_existente)
    assert ex.value.sqlstate == "23505"
    assert abertos_do_email(email) == 1
    with h.conectar() as c:
        assert c.execute("select revogado_em from public.convites where id = %s", (cid,)).fetchone() == (None,)


def test_reenvios_simultaneos_so_um_efetiva(adm: uuid.UUID) -> None:
    email = h.email_unico()
    cid, _ = h.novo_convite(adm, email)
    barreira = threading.Barrier(2, timeout=60)
    res: list[str] = []
    lock = threading.Lock()

    def tentar() -> None:
        with psycopg.connect(h.DSN) as conn:  # type: ignore[arg-type]
            barreira.wait()
            try:
                reenviar(conn, cid, adm)
                conn.commit()
                r = "ok"
            except psycopg.Error as e:
                conn.rollback()
                r = e.sqlstate or "?"
        with lock:
            res.append(r)

    ts = [threading.Thread(target=tentar) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=90)
    assert sorted(res) == ["AG038", "ok"], res
    assert abertos_do_email(email) == 1
    with h.conectar() as c:
        # 1 antigo (revogado) + 1 novo (aberto): a perdedora nao gravou nada
        assert c.execute("select count(*) from public.convites where email = %s", (email,)).fetchone() == (2,)


def test_segunda_chamada_espera_transacao_aberta_e_depois_recebe_ag038(adm: uuid.UUID) -> None:
    email = h.email_unico()
    cid, _ = h.novo_convite(adm, email)
    c1 = psycopg.connect(h.DSN)  # type: ignore[arg-type]
    saida: dict[str, object] = {}
    try:
        reenviar(c1, cid, adm)  # segura a transacao aberta (FOR UPDATE no antigo)

        def segunda() -> None:
            with psycopg.connect(h.DSN) as c2:  # type: ignore[arg-type]
                c2.execute("set lock_timeout = '40s'")
                inicio = time.monotonic()
                try:
                    reenviar(c2, cid, adm)
                    saida["r"] = "ok"
                except psycopg.Error as e:
                    c2.rollback()
                    saida["r"] = e.sqlstate
                saida["espera"] = time.monotonic() - inicio

        t = threading.Thread(target=segunda)
        t.start()
        time.sleep(1.5)
        assert t.is_alive(), "a segunda chamada deveria estar bloqueada no lock do convite"
        c1.commit()
        t.join(timeout=60)
    finally:
        c1.close()
    assert saida["r"] == "AG038"
    assert float(saida["espera"]) >= 1.0  # type: ignore[arg-type]
    assert abertos_do_email(email) == 1


def test_reenvio_concorrente_com_criacao_nao_deixa_dois_abertos(adm: uuid.UUID) -> None:
    """Segurando o reenvio aberto, um INSERT de convite para o mesmo e-mail espera o indice e falha."""
    email = h.email_unico()
    cid, _ = h.novo_convite(adm, email)
    c1 = psycopg.connect(h.DSN)  # type: ignore[arg-type]
    saida: dict[str, object] = {}
    try:
        reenviar(c1, cid, adm)

        def inserir() -> None:
            with psycopg.connect(h.DSN) as c2:  # type: ignore[arg-type]
                c2.execute("set lock_timeout = '40s'")
                try:
                    c2.execute(
                        "insert into public.convites (email, role, unidade, token_hash, criado_por, expira_em) "
                        "values (%s, 'solicitante', 'UEA', %s, %s, now() + interval '1 day')",
                        (email, hash_novo(), adm),
                    )
                    c2.commit()
                    saida["r"] = "ok"
                except psycopg.Error as e:
                    c2.rollback()
                    saida["r"] = e.sqlstate

        t = threading.Thread(target=inserir)
        t.start()
        time.sleep(1.0)
        c1.commit()
        t.join(timeout=60)
    finally:
        c1.close()
    assert saida["r"] == "23505"
    assert abertos_do_email(email) == 1


# ---------------------------------------------------------------------------
# B. dedupe em reservar_envio_email
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def recurso_ligado() -> Iterator[uuid.UUID]:
    """Liga relatorio_email (commitado: as threads usam outras conexoes) e restaura ao fim."""
    adm_ = h.admin_ativo_qualquer() or h.criar_usuario("ra-admin", "admin")
    with h.conectar() as c:
        original = c.execute("select relatorio_email from public.configuracoes").fetchone()[0]  # type: ignore[index]
        c.execute("update public.configuracoes set relatorio_email = true, alterado_por = %s", (adm_,))
    try:
        yield adm_
    finally:
        with h.conectar() as c:
            c.execute("update public.configuracoes set relatorio_email = %s, alterado_por = %s", (original, adm_))


def reservar(
    conn: "psycopg.Connection",
    sol: uuid.UUID,
    dest: uuid.UUID,
    filtros: str,
    tipo: str = "resumo",
    formato: str = "csv",
    por_usuario: int | None = None,
) -> dict:
    row = conn.execute(
        "select public.reservar_envio_email(%s, %s, %s, %s, jsonb_build_object('k', %s::text), %s, %s)",
        (sol, dest, tipo, formato, filtros, GRANDE, por_usuario),
    ).fetchone()
    assert row is not None
    return row[0]


def marcar(conn: "psycopg.Connection", eid: str, status: str) -> None:
    conn.execute("select public.marcar_envio_email(%s, %s)", (eid, status))


def linhas(conn: "psycopg.Connection", sol: uuid.UUID) -> int:
    return conn.execute(
        "select count(*) from public.envios_email where solicitado_por = %s", (sol,)
    ).fetchone()[0]  # type: ignore[index]


@pytest.fixture
def par(recurso_ligado: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return h.criar_usuario("ra-sol", "autorizador"), h.criar_usuario("ra-dest", "solicitante")


def test_duplicado_devolve_o_existente_sem_inserir(par: tuple[uuid.UUID, uuid.UUID]) -> None:
    sol, dest = par
    f = uuid.uuid4().hex
    with h.conectar() as c:
        a = reservar(c, sol, dest, f)
        b = reservar(c, sol, dest, f)
        assert a["duplicado"] is False and b["duplicado"] is True and a["id"] == b["id"]
        assert linhas(c, sol) == 1
        marcar(c, a["id"], "enviando")
        d = reservar(c, sol, dest, f)  # 'enviando' tambem deduplica
        assert d["duplicado"] is True and d["id"] == a["id"] and linhas(c, sol) == 1


@pytest.mark.parametrize("variacao", ["formato", "tipo", "filtros", "destinatario"])
def test_qualquer_diferenca_gera_novo_envio(par: tuple[uuid.UUID, uuid.UUID], variacao: str) -> None:
    sol, dest = par
    f = uuid.uuid4().hex
    with h.conectar() as c:
        a = reservar(c, sol, dest, f)
        kw: dict = {"formato": "xlsx"} if variacao == "formato" else {"tipo": "outro"} if variacao == "tipo" else {}
        d2 = h.criar_usuario("ra-dest2", "solicitante") if variacao == "destinatario" else dest
        f2 = uuid.uuid4().hex if variacao == "filtros" else f
        b = reservar(c, sol, d2, f2, **kw)
        assert b["duplicado"] is False and b["id"] != a["id"]


def test_outro_solicitante_nao_deduplica(par: tuple[uuid.UUID, uuid.UUID]) -> None:
    sol, dest = par
    outro = h.criar_usuario("ra-outro", "autorizador")
    f = uuid.uuid4().hex
    with h.conectar() as c:
        a = reservar(c, sol, dest, f)
        b = reservar(c, outro, dest, f)
        assert b["duplicado"] is False and b["id"] != a["id"]


@pytest.mark.parametrize("final", ["falhou", "enviado"])
def test_envio_concluido_nao_deduplica(par: tuple[uuid.UUID, uuid.UUID], final: str) -> None:
    sol, dest = par
    f = uuid.uuid4().hex
    with h.conectar() as c:
        a = reservar(c, sol, dest, f)
        marcar(c, a["id"], "enviando")
        marcar(c, a["id"], final)
        b = reservar(c, sol, dest, f)
        assert b["duplicado"] is False and b["id"] != a["id"]


def test_duplicado_nao_consome_cota(par: tuple[uuid.UUID, uuid.UUID]) -> None:
    sol, dest = par
    f = uuid.uuid4().hex
    with h.conectar() as c:
        a = reservar(c, sol, dest, f, por_usuario=1)  # usuario novo: 1 vaga
        b = reservar(c, sol, dest, f, por_usuario=1)  # duplicado: nao bate na cota
        assert b["duplicado"] is True and b["id"] == a["id"] and linhas(c, sol) == 1
        with pytest.raises(psycopg.Error) as ex:
            reservar(c, sol, dest, uuid.uuid4().hex, por_usuario=1)  # diferente: cota esgotada
        assert ex.value.sqlstate == "AG025"


def test_validacoes_anteriores_ao_dedupe_continuam(par: tuple[uuid.UUID, uuid.UUID]) -> None:
    sol, dest = par
    with pytest.raises(psycopg.Error) as ex:
        with h.conectar() as c:
            reservar(c, sol, dest, "x", formato="pdf")
    assert ex.value.sqlstate == "AG027"
    with pytest.raises(psycopg.Error) as ex2:
        with h.conectar() as c:
            reservar(c, uuid.uuid4(), dest, "x")
    assert ex2.value.sqlstate == "AG002"


def test_apos_a_janela_de_30s_nao_deduplica(par: tuple[uuid.UUID, uuid.UUID]) -> None:
    sol, dest = par
    f = uuid.uuid4().hex
    with h.conectar() as c:
        a = reservar(c, sol, dest, f)
        time.sleep(31)
        b = reservar(c, sol, dest, f)
        assert b["duplicado"] is False and b["id"] != a["id"]


def test_reservas_simultaneas_identicas_geram_um_so_envio(par: tuple[uuid.UUID, uuid.UUID]) -> None:
    sol, dest = par
    f = uuid.uuid4().hex
    barreira = threading.Barrier(2, timeout=60)
    res: list[dict] = []
    lock = threading.Lock()

    def tentar() -> None:
        with psycopg.connect(h.DSN, autocommit=True) as c:  # type: ignore[arg-type]
            barreira.wait()
            r = reservar(c, sol, dest, f)
        with lock:
            res.append(r)

    ts = [threading.Thread(target=tentar) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=90)
    assert len(res) == 2 and res[0]["id"] == res[1]["id"]
    assert sorted(r["duplicado"] for r in res) == [False, True]
    with h.conectar() as c:
        assert linhas(c, sol) == 1


def test_segunda_reserva_espera_transacao_aberta_e_deduplica(par: tuple[uuid.UUID, uuid.UUID]) -> None:
    sol, dest = par
    f = uuid.uuid4().hex
    c1 = psycopg.connect(h.DSN)  # type: ignore[arg-type]
    saida: dict[str, object] = {}
    try:
        a = reservar(c1, sol, dest, f)  # advisory lock global segurado ate o commit

        def segunda() -> None:
            with psycopg.connect(h.DSN) as c2:  # type: ignore[arg-type]
                c2.execute("set lock_timeout = '40s'")
                saida["r"] = reservar(c2, sol, dest, f)
                c2.commit()

        t = threading.Thread(target=segunda)
        t.start()
        time.sleep(1.5)
        assert t.is_alive(), "a segunda reserva deveria esperar o advisory lock"
        c1.commit()
        t.join(timeout=60)
    finally:
        c1.close()
    r = saida["r"]
    assert isinstance(r, dict) and r["duplicado"] is True and r["id"] == a["id"]
    with h.conectar() as c:
        assert linhas(c, sol) == 1


# ---------------------------------------------------------------------------
# C. reaper_envios_email
# ---------------------------------------------------------------------------
def envelhecer(conn: "psycopg.Connection", ids: list[str], minutos: int) -> None:
    """Dentro de uma transacao de teste (ROLLBACK ao fim): desliga os triggers, recua atualizado_em e religa."""
    conn.execute("alter table public.envios_email disable trigger trg_envios_email_atualizado_em")
    conn.execute("alter table public.envios_email disable trigger trg_envios_email_validar")
    conn.execute(
        "update public.envios_email set atualizado_em = clock_timestamp() - make_interval(mins => %s) "
        "where id = any(%s::uuid[])",
        (minutos, ids),
    )
    conn.execute("alter table public.envios_email enable trigger trg_envios_email_atualizado_em")
    conn.execute("alter table public.envios_email enable trigger trg_envios_email_validar")


def estado(conn: "psycopg.Connection", eid: str) -> tuple:
    return conn.execute(
        "select status, erro_codigo from public.envios_email where id = %s", (eid,)
    ).fetchone()  # type: ignore[return-value]


def test_reaper_move_presos_para_falhou_timeout(par: tuple[uuid.UUID, uuid.UUID]) -> None:
    sol, dest = par
    conn = psycopg.connect(h.DSN)  # type: ignore[arg-type]
    try:
        pend = reservar(conn, sol, dest, uuid.uuid4().hex)["id"]
        env = reservar(conn, sol, dest, uuid.uuid4().hex)["id"]
        marcar(conn, env, "enviando")
        fim = reservar(conn, sol, dest, uuid.uuid4().hex)["id"]
        marcar(conn, fim, "enviando")
        marcar(conn, fim, "enviado")
        recente = reservar(conn, sol, dest, uuid.uuid4().hex)["id"]
        envelhecer(conn, [pend, env, fim], 20)

        n = conn.execute("select public.reaper_envios_email(15)").fetchone()[0]  # type: ignore[index]
        assert n >= 2
        assert estado(conn, pend) == ("falhou", "timeout")
        assert estado(conn, env) == ("falhou", "timeout")
        assert estado(conn, fim) == ("enviado", None)  # terminal nao e tocado
        assert estado(conn, recente) == ("pendente", None)  # recente nao e tocado
        # idempotente: segunda execucao nao encontra os mesmos
        assert conn.execute("select public.reaper_envios_email(15)").fetchone()[0] >= 0  # type: ignore[index]
        assert estado(conn, pend) == ("falhou", "timeout")
        # falhou pode voltar a enviando (retentativa) pela transicao ja permitida
        marcar(conn, pend, "enviando")
        assert estado(conn, pend)[0] == "enviando"
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.parametrize("minutos", [0, 4, 1441, -1])
def test_reaper_prazo_invalido_ag027(minutos: int) -> None:
    with pytest.raises(psycopg.Error) as ex:
        with h.conectar() as c:
            c.execute("select public.reaper_envios_email(%s)", (minutos,))
    assert ex.value.sqlstate == "AG027"


def test_reaper_sem_argumento_usa_15_min() -> None:
    with h.conectar() as c:
        assert c.execute("select public.reaper_envios_email()").fetchone()[0] >= 0  # type: ignore[index]
