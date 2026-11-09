"""Testes da migration 20261109130000 (cota de envios por usuario em reservar_envio_email).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). Transacoes de teste terminam em ROLLBACK,
exceto o teste de concorrencia (persiste linhas em envios_email, sem DELETE fisico).

    pytest supabase/tests/test_relatorios_cota_usuario.py -m db_local -p no:xdist
"""

from __future__ import annotations

import json
import threading
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
    Path(__file__).resolve().parents[1] / "migrations" / "20261109130000_envios_email_cota_usuario.sql"
).read_text(encoding="utf-8")
ASSINATURA = "public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer, integer)"
GRANDE = 1000  # cota global folgada: so a cota por usuario deve barrar


@pytest.fixture
def e() -> Iterator[dict]:
    assert h.DSN
    adm = h.admin_ativo_qualquer() or h.criar_usuario("cu-admin", "admin")
    a = h.criar_usuario("cu-a", "autorizador")
    b = h.criar_usuario("cu-b", "autorizador")
    dest = h.criar_usuario("cu-dest", "solicitante")
    c = psycopg.connect(h.DSN)
    try:
        c.execute("update public.configuracoes set relatorio_email = true, alterado_por = %s", (adm,))
        yield {"c": c, "a": a, "b": b, "dest": dest, "adm": adm}
    finally:
        c.rollback()
        c.close()


def reservar(e: dict, sol: uuid.UUID, por_usuario: int | None = None, glob: int = GRANDE) -> dict:
    # Filtros unicos por chamada: desde a migration 20261115120000 um envio pendente/enviando
    # identico nos ultimos 30 s e deduplicado (nao consome cota). A dedupe e testada em
    # test_rpcs_atomicas.py; aqui cada chamada precisa contar como um envio novo.
    c = e["c"]
    filtros = json.dumps({"n": uuid.uuid4().hex})
    if por_usuario is None:
        return c.execute(
            "select public.reservar_envio_email(%s, %s, 'resumo', 'csv', %s::jsonb, %s)",
            (sol, e["dest"], filtros, glob),
        ).fetchone()[0]
    return c.execute(
        "select public.reservar_envio_email(%s, %s, 'resumo', 'csv', %s::jsonb, %s, %s)",
        (sol, e["dest"], filtros, glob, por_usuario),
    ).fetchone()[0]


def recusa(e: dict, sol: uuid.UUID, **kw: int) -> "psycopg.Error":
    with pytest.raises(psycopg.Error) as ex:
        with e["c"].transaction():  # savepoint: a transacao de teste segue utilizavel
            reservar(e, sol, **kw)
    return ex.value


def do_usuario(c: "psycopg.Connection", sol: uuid.UUID) -> int:
    return c.execute(
        "select count(*) from public.envios_email where solicitado_por = %s "
        "and criado_em > clock_timestamp() - interval '1 hour'",
        (sol,),
    ).fetchone()[0]  # type: ignore[index]


def test_so_uma_assinatura_e_permissoes() -> None:
    with h.conectar() as c:
        assert c.execute(
            "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
            "where n.nspname = 'public' and p.proname = 'reservar_envio_email'"
        ).fetchone() == (1,)
        assert c.execute(
            "select has_function_privilege('service_role', %s::regprocedure, 'execute')", (ASSINATURA,)
        ).fetchone() == (True,)
        for papel in ("anon", "authenticated"):
            assert c.execute(
                "select has_function_privilege(%s, %s::regprocedure, 'execute')", (papel, ASSINATURA)
            ).fetchone() == (False,)
        assert c.execute(
            "select exists (select 1 from pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
            "where p.oid = %s::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE')",
            (ASSINATURA,),
        ).fetchone() == (False,)
        assert c.execute(
            "select prosecdef and array_to_string(proconfig, ',') like '%%search_path=%%' "
            "from pg_proc where oid = %s::regprocedure",
            (ASSINATURA,),
        ).fetchone() == (True,)


@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_anon_e_authenticated_sem_execute(papel: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with h.como(papel, uuid.uuid4()) as c:
            c.execute(
                "select public.reservar_envio_email(gen_random_uuid(), gen_random_uuid(), 'resumo', 'csv', '{}', 10, 5)"
            )


@pytest.mark.parametrize("args", ["10", "10, 5"])
def test_service_role_chama_com_6_e_7_argumentos(args: str) -> None:
    # Chega na validacao de negocio (nunca "funcao nao existe"/sem permissao): 6 args continua valido.
    with pytest.raises(psycopg.Error) as ex:
        with h.como("service_role") as c:
            c.execute(
                "select public.reservar_envio_email(gen_random_uuid(), gen_random_uuid(), 'resumo', 'csv', '{}', "
                + args
                + ")"
            )
    assert h.sqlstate(ex) in {"AG026", "AG002"}


def test_migration_e_idempotente(e: dict) -> None:
    e["c"].execute(MIGRATION)
    e["c"].execute(MIGRATION)
    assert reservar(e, e["a"], por_usuario=5)["id"]


def test_sexto_envio_do_mesmo_usuario_bloqueia_sem_afetar_outro(e: dict) -> None:
    c = e["c"]
    base_a = do_usuario(c, e["a"])
    limite = base_a + 5
    for _ in range(5):
        reservar(e, e["a"], por_usuario=limite)
    erro = recusa(e, e["a"], por_usuario=limite)
    assert erro.sqlstate == "AG025" and "do usuário" in str(erro)
    assert do_usuario(c, e["a"]) == limite  # a recusada nao grava
    assert reservar(e, e["b"], por_usuario=do_usuario(c, e["b"]) + 5)["id"]  # outro usuario nao e afetado


def test_mensagens_global_e_usuario_sao_distintas(e: dict) -> None:
    c = e["c"]
    glob = c.execute(
        "select count(*) from public.envios_email where criado_em > clock_timestamp() - interval '1 hour'"
    ).fetchone()[0]  # type: ignore[index]
    reservar(e, e["a"], glob=glob + 1)
    g = recusa(e, e["a"], glob=glob + 1)
    assert g.sqlstate == "AG025" and "Limite de envios por hora atingido" in str(g)
    # global folgada + usuario no limite => mensagem do usuario
    u = recusa(e, e["a"], por_usuario=do_usuario(c, e["a"]))
    assert u.sqlstate == "AG025" and "do usuário" in str(u)
    # ambas estouradas: vale a global (verificada primeiro)
    ambas = recusa(e, e["a"], por_usuario=do_usuario(c, e["a"]), glob=glob + 1)
    assert "do usuário" not in str(ambas)


def test_null_mantem_comportamento_anterior(e: dict) -> None:
    antes = do_usuario(e["c"], e["a"])
    for _ in range(8):
        reservar(e, e["a"])  # sem limite por usuario
    assert do_usuario(e["c"], e["a"]) == antes + 8


@pytest.mark.parametrize("valor", [0, -1, 1001])
def test_limite_por_usuario_invalido(e: dict, valor: int) -> None:
    assert recusa(e, e["a"], por_usuario=valor).sqlstate == "AG027"


def test_ordem_das_validacoes(e: dict) -> None:
    c = e["c"]
    c.execute("update public.configuracoes set relatorio_email = false, alterado_por = %s", (e["adm"],))
    assert recusa(e, e["a"], por_usuario=1).sqlstate == "AG026"  # recurso vence cota por usuario
    c.execute("update public.configuracoes set relatorio_email = true, alterado_por = %s", (e["adm"],))
    assert recusa(e, uuid.uuid4(), por_usuario=1).sqlstate == "AG002"  # solicitante vence cota por usuario


def test_cota_por_usuario_concorrente_so_uma_passa() -> None:
    """Duas reservas simultaneas do mesmo usuario com 1 vaga restante: uma passa, outra AG025."""
    adm = h.admin_ativo_qualquer() or h.criar_usuario("cu-admin", "admin")
    sol = h.criar_usuario("cu-conc-s", "autorizador")
    dest = h.criar_usuario("cu-conc-d", "solicitante")
    with h.conectar() as c:
        original = c.execute("select relatorio_email from public.configuracoes").fetchone()[0]  # type: ignore[index]
        c.execute("update public.configuracoes set relatorio_email = true, alterado_por = %s", (adm,))
        base = do_usuario(c, sol)
    res: list[str] = []
    barreira = threading.Barrier(2, timeout=60)

    def tentar(i: int) -> None:
        # filtros distintos por thread: envios identicos seriam deduplicados (migration 20261115120000)
        with psycopg.connect(h.DSN, autocommit=True) as c2:  # type: ignore[arg-type]
            barreira.wait()
            try:
                c2.execute(
                    "select public.reservar_envio_email(%s, %s, 'resumo', 'csv', %s::jsonb, 1000, %s)",
                    (sol, dest, json.dumps({"thread": i}), base + 1),
                )
                res.append("ok")
            except psycopg.Error as ex:
                res.append(getattr(ex, "sqlstate", "?") or "?")

    try:
        ts = [threading.Thread(target=tentar, args=(i,)) for i in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=90)
    finally:
        with h.conectar() as c:
            c.execute("update public.configuracoes set relatorio_email = %s, alterado_por = %s", (original, adm))
    assert sorted(res) == ["AG025", "ok"]
