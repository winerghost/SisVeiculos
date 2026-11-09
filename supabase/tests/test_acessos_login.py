"""Testes das migrations 20261107122430 (historico de acessos) e 122440 (endurecimento).

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.
Nao depende de flags de recursos. Os testes de expurgo apagam linhas com mais de 12 meses de
qualquer origem: por isso so rodam em banco local (a fixture pula se o DSN nao parecer local;
TEST_DATABASE_ALLOW_REMOTE=1 forca - nunca use em banco real). Sem paralelismo (sem xdist).

    pytest supabase/tests/test_acessos_login.py -m db_local
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from psycopg.conninfo import conninfo_to_dict  # noqa: E402

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

NEGADO = "42501"
CHECK = "23514"
_MIG = Path(__file__).resolve().parents[1] / "migrations"
MIGRATION = (_MIG / "20261107122430_acessos_login.sql").read_text(encoding="utf-8")
MIGRATION_2 = (_MIG / "20261107122440_acessos_login_endurecimento.sql").read_text(encoding="utf-8")

SQL_REGISTRAR = (
    "select public.registrar_acesso_login(%s::uuid, %s::uuid, %s::timestamptz, "
    "%s::text, %s::text, %s::text, %s::text)"
)
SQL_LISTAR = (
    "select public.listar_acessos_login(%s::uuid, %s::timestamptz, %s::timestamptz, "
    "%s::integer, %s::integer)"
)
FUNCOES_INTERNAS = (
    "public.acessos_login_proteger()",
    "public.acessos_login_ua_valida(text)",
    "public.acessos_login_ip_valido(cidr)",
)
NAVEGADORES = ["Chrome", "Firefox", "Safari", "Edge", "Opera", "Samsung Internet", "Mobile Safari", "Outro"]
SISTEMAS = ["Windows", "macOS", "Linux", "Android", "iOS", "ChromeOS", "Desconhecido"]
FUNCOES = {
    "registrar_acesso_login": "public.registrar_acesso_login(uuid, uuid, timestamptz, text, text, text, text)",
    "listar_acessos_login": "public.listar_acessos_login(uuid, timestamptz, timestamptz, integer, integer)",
    "ultimo_acesso_por_usuario": "public.ultimo_acesso_por_usuario(uuid[])",
    "purgar_acessos_login": "public.purgar_acessos_login(integer)",
}


def _dsn_local() -> bool:
    if os.environ.get("TEST_DATABASE_ALLOW_REMOTE") == "1":
        return True
    host = conninfo_to_dict(h.DSN or "").get("host") or ""
    return host == "" or host.startswith("/") or host in ("localhost", "127.0.0.1", "::1")


@pytest.fixture(autouse=True)
def _somente_local() -> Iterator[None]:
    if not _dsn_local():
        pytest.skip("DSN nao parece banco local/descartavel (TEST_DATABASE_ALLOW_REMOTE=1 forca)")
    yield


# --- helpers ---------------------------------------------------------------------------------
def _agora() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=1)


def _usuario(nome: str = "acesso", ativo: bool = True) -> uuid.UUID:
    return h.criar_usuario(nome, ativo=ativo)


def _sessao_auth(
    usuario: uuid.UUID, sessao: uuid.UUID, criada_em: datetime | str | None = "agora"
) -> None:
    """Cria a linha em auth.sessions (so id, user_id, created_at; as demais colunas sao anulaveis).

    Acoplamento ao schema interno do GoTrue: se uma versao futura mudar essas colunas, falha aqui.
    criada_em=None grava created_at NULL (a coluna do GoTrue e anulavel).
    """
    quando = _agora() if criada_em == "agora" else criada_em
    with h.conectar() as c:
        c.execute(
            "insert into auth.sessions (id, user_id, created_at) values (%s, %s, %s) "
            "on conflict (id) do nothing",
            (sessao, usuario, quando),
        )


def _registrar(
    usuario: uuid.UUID,
    sessao: uuid.UUID | None = None,
    *,
    ocorrido: datetime | None = None,
    com_sessao: bool = True,
    metodo: str | None = "password",
    aal: str | None = "aal1",
    ip: str | None = "203.0.113.0/24",
    ua: str | None = "Chrome/Windows",
) -> bool:
    sessao = sessao or uuid.uuid4()
    if com_sessao:
        _sessao_auth(usuario, sessao)
    with h.conectar() as c:
        row = c.execute(
            SQL_REGISTRAR, (usuario, sessao, ocorrido or _agora(), metodo, aal, ip, ua)
        ).fetchone()
    assert row is not None
    return bool(row[0])


def _falha(esperado: str, nao_contem: tuple[str, ...] = (), **kw: object) -> None:
    with pytest.raises(psycopg.Error) as exc:
        _registrar(**kw)  # type: ignore[arg-type]
    assert h.sqlstate(exc) == esperado, str(exc.value)
    for texto in nao_contem:
        assert texto not in str(exc.value), "mensagem repete o valor recebido"
    assert "DETAIL" not in str(exc.value).upper().replace("DETAILS", "")


def _inserir_direto(
    usuario: uuid.UUID,
    quando: datetime | str,
    sessao: uuid.UUID | None = None,
) -> uuid.UUID:
    """INSERT como dono (postgres): serve para criar historico antigo nos testes."""
    sid = sessao or uuid.uuid4()
    with h.conectar() as c:
        if isinstance(quando, str):  # expressao SQL, ex.: "now() - interval '13 months'"
            c.execute(
                f"insert into public.acessos_login (usuario_id, session_id, ocorrido_em) "
                f"values (%s, %s, {quando})",
                (usuario, sid),
            )
        else:
            c.execute(
                "insert into public.acessos_login (usuario_id, session_id, ocorrido_em) "
                "values (%s, %s, %s)",
                (usuario, sid, quando),
            )
    return sid


def _existe(sessao: uuid.UUID) -> bool:
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.acessos_login where session_id = %s", (sessao,)
        ).fetchone() or (0,)
    return n == 1


def _listar(
    usuario: uuid.UUID | None = None,
    de: datetime | None = None,
    ate: datetime | None = None,
    limite: int | None = 50,
    offset: int | None = 0,
) -> dict:
    with h.como("service_role") as c:
        row = c.execute(SQL_LISTAR, (usuario, de, ate, limite, offset)).fetchone()
    assert row is not None
    return row[0]


def _sessao_ref(sessao: uuid.UUID) -> str:
    return hashlib.sha256(str(sessao).encode()).hexdigest()[:8]


# --- estrutura -------------------------------------------------------------------------------
def test_estrutura_da_tabela() -> None:
    with h.conectar() as c:
        cols = {
            r[0]: (r[1], r[2])
            for r in c.execute(
                "select column_name, data_type, is_nullable from information_schema.columns "
                "where table_schema='public' and table_name='acessos_login'"
            ).fetchall()
        }
        fk = c.execute(
            "select confdeltype from pg_constraint where conrelid='public.acessos_login'::regclass "
            "and contype='f' and confrelid='public.perfis'::regclass"
        ).fetchall()
        unicos = c.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid='public.acessos_login'::regclass and contype='u'"
        ).fetchall()
        indices = {
            r[0]
            for r in c.execute(
                "select indexdef from pg_indexes where schemaname='public' and tablename='acessos_login'"
            ).fetchall()
        }
        (rls, forcado) = c.execute(
            "select relrowsecurity, relforcerowsecurity from pg_class where oid='public.acessos_login'::regclass"
        ).fetchone() or (None, None)
        politicas = c.execute(
            "select policyname, roles::text, qual, with_check from pg_policies "
            "where schemaname='public' and tablename='acessos_login'"
        ).fetchall()
        gatilhos = dict(
            c.execute(
                "select tgname, tgenabled from pg_trigger "
                "where tgrelid='public.acessos_login'::regclass and not tgisinternal"
            ).fetchall()
        )
    assert cols["id"] == ("uuid", "NO")
    assert cols["usuario_id"] == ("uuid", "NO")
    assert cols["session_id"] == ("uuid", "NO")
    assert cols["ocorrido_em"] == ("timestamp with time zone", "NO")
    assert cols["registrado_em"] == ("timestamp with time zone", "NO")
    assert cols["ip_mascarado"][0] == "cidr"
    assert cols["metodo"][0] == "text" and cols["aal"][0] == "text" and cols["ua_familia"][0] == "text"
    assert "session_id" in unicos[0][0]
    assert fk == [("r",)]  # ON DELETE RESTRICT
    assert any("(usuario_id, ocorrido_em DESC)" in d for d in indices)
    assert any("(ocorrido_em DESC)" in d for d in indices)
    assert rls is True
    assert forcado is False
    assert len(politicas) == 1
    assert politicas[0][2] == "false" and politicas[0][3] == "false"
    assert gatilhos["trg_acessos_login_imutavel"] == "A"  # ENABLE ALWAYS
    assert gatilhos["trg_acessos_login_sem_truncate"] == "A"


def test_migrations_sao_idempotentes_e_nao_falham_sem_pg_cron() -> None:
    with h.reaplicando_migration() as c:
        # Reexecuta na ordem; o bloco pg_cron so avisa se a extensao faltar. O estado final tem
        # de ser o endurecido (a 122430 reconcede SELECT; a 122440 revoga).
        for _ in range(2):
            c.execute(MIGRATION)
            c.execute(MIGRATION_2)
        (sel,) = c.execute(
            "select has_table_privilege('service_role', 'public.acessos_login', 'select')"
        ).fetchone() or (None,)
        assert sel is False


# --- RLS / grants / search_path -----------------------------------------------------------------
@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_anon_e_authenticated_sem_acesso_a_tabela(papel: str) -> None:
    for sql in (
        "select * from public.acessos_login",
        "insert into public.acessos_login (usuario_id, session_id, ocorrido_em) values (gen_random_uuid(), gen_random_uuid(), now())",
        "update public.acessos_login set metodo = 'outro'",
        "delete from public.acessos_login",
        "truncate public.acessos_login",
    ):
        with pytest.raises(psycopg.Error) as exc:
            with h.como(papel) as c:
                c.execute(sql)
        assert h.sqlstate(exc) == NEGADO, sql


@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_anon_e_authenticated_sem_execute_nas_rpcs(papel: str) -> None:
    chamadas = {
        "registrar_acesso_login": (SQL_REGISTRAR, (uuid.uuid4(), uuid.uuid4(), _agora(), None, None, None, None)),
        "listar_acessos_login": (SQL_LISTAR, (None, None, None, 10, 0)),
        "ultimo_acesso_por_usuario": ("select * from public.ultimo_acesso_por_usuario(%s::uuid[])", ([],)),
        "purgar_acessos_login": ("select public.purgar_acessos_login(12)", ()),
    }
    for nome, (sql, params) in chamadas.items():
        with pytest.raises(psycopg.Error) as exc:
            with h.como(papel) as c:
                c.execute(sql, params)
        assert h.sqlstate(exc) == NEGADO, nome


def test_service_role_sem_acesso_direto_e_executa_as_rpcs() -> None:
    for sql in (
        "select count(*) from public.acessos_login",
        "select id from public.acessos_login",
        "insert into public.acessos_login (usuario_id, session_id, ocorrido_em) values (gen_random_uuid(), gen_random_uuid(), now())",
        "update public.acessos_login set metodo = 'outro'",
        "delete from public.acessos_login",
        "truncate public.acessos_login",
    ):
        with pytest.raises(psycopg.Error) as exc:
            with h.como("service_role") as c:
                c.execute(sql)
        assert h.sqlstate(exc) == NEGADO, sql
    with h.conectar() as c:
        for p in ("select", "insert", "update", "delete", "truncate", "trigger", "references"):
            (tem,) = c.execute(
                "select has_table_privilege('service_role', 'public.acessos_login', %s)", (p,)
            ).fetchone() or (None,)
            assert tem is False, p
        (col,) = c.execute(
            "select has_any_column_privilege('service_role', 'public.acessos_login', 'select, insert, update, references')"
        ).fetchone() or (None,)
        assert col is False
        for assinatura in FUNCOES.values():
            (ok,) = c.execute(
                "select has_function_privilege('service_role', %s::regprocedure, 'execute')",
                (assinatura,),
            ).fetchone() or (None,)
            assert ok is True, assinatura
        # o dono (postgres) tambem executa o expurgo, para o pg_cron
        (dono,) = c.execute(
            "select has_function_privilege('postgres', %s::regprocedure, 'execute')",
            (FUNCOES["purgar_acessos_login"],),
        ).fetchone() or (None,)
        assert dono is True


def test_rpcs_funcionam_como_service_role_sem_select_na_tabela() -> None:
    """As RPCs SECURITY DEFINER leem/escrevem a tabela e auth.sessions em nome do dono."""
    u = _usuario()
    sessao = uuid.uuid4()
    _sessao_auth(u, sessao)
    with h.como("service_role") as c:  # rollback no fim: nada persiste
        row = c.execute(
            SQL_REGISTRAR, (u, sessao, _agora(), "password", "aal1", "203.0.113.0/24", "Chrome/Windows")
        ).fetchone()
        assert row is not None and row[0] is True
        lista = c.execute(SQL_LISTAR, (u, None, None, 10, 0)).fetchone()
        assert lista is not None and lista[0]["total"] == 1
        ult = c.execute("select * from public.ultimo_acesso_por_usuario(%s::uuid[])", ([u],)).fetchall()
        assert [r[0] for r in ult] == [u]
        (n,) = c.execute("select public.purgar_acessos_login(12)").fetchone() or (None,)
        assert isinstance(n, int)


def test_funcoes_sem_public_e_search_path_vazio() -> None:
    with h.conectar() as c:
        for assinatura in FUNCOES.values():
            (publico,) = c.execute(
                "select exists (select 1 from pg_proc p, "
                "aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
                "where p.oid = %s::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE')",
                (assinatura,),
            ).fetchone() or (None,)
            assert publico is False, assinatura
            (definer, config) = c.execute(
                "select prosecdef, proconfig from pg_proc where oid = %s::regprocedure", (assinatura,)
            ).fetchone() or (None, None)
            assert definer is True, assinatura
            assert 'search_path=""' in (config or []), assinatura
        for interna in FUNCOES_INTERNAS:
            for papel in ("anon", "authenticated", "service_role"):
                (tem,) = c.execute(
                    "select has_function_privilege(%s, %s::regprocedure, 'execute')", (papel, interna)
                ).fetchone() or (None,)
                assert tem is False, (papel, interna)
            (publico,) = c.execute(
                "select exists (select 1 from pg_proc p, "
                "aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
                "where p.oid = %s::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE')",
                (interna,),
            ).fetchone() or (None,)
            assert publico is False, interna
            (cfg,) = c.execute(
                "select proconfig from pg_proc where oid = %s::regprocedure", (interna,)
            ).fetchone() or (None,)
            assert 'search_path=""' in (cfg or []), interna


# --- registrar: idempotencia e concorrencia ---------------------------------------------------------
def test_registrar_insere_e_e_idempotente() -> None:
    u = _usuario()
    sessao = uuid.uuid4()
    assert _registrar(u, sessao, metodo="totp", aal="aal2", ip="2001:db8:1::/48", ua="Edge/Android") is True
    assert _registrar(u, sessao) is False  # mesma sessao: nao duplica, nao altera
    with h.conectar() as c:
        rows = c.execute(
            "select usuario_id, metodo, aal, ip_mascarado::text, ua_familia, registrado_em is not null "
            "from public.acessos_login where session_id = %s",
            (sessao,),
        ).fetchall()
    assert rows == [(u, "totp", "aal2", "2001:db8:1::/48", "Edge/Android", True)]


def test_registrar_aceita_campos_opcionais_nulos() -> None:
    u = _usuario()
    assert _registrar(u, metodo=None, aal=None, ip=None, ua=None) is True


def test_session_id_e_unico_na_tabela() -> None:
    u = _usuario()
    sessao = _inserir_direto(u, _agora())
    with pytest.raises(psycopg.Error) as exc:
        _inserir_direto(u, _agora(), sessao)
    assert h.sqlstate(exc) == "23505"


def test_duas_chamadas_simultaneas_da_mesma_sessao_geram_uma_linha() -> None:
    u = _usuario()
    sessao = uuid.uuid4()
    _sessao_auth(u, sessao)
    # Conexoes abertas ANTES da barreira.
    conexoes = [h.conectar(), h.conectar()]
    barreira = threading.Barrier(2, timeout=60)
    resultados: list[bool] = []
    erros: list[BaseException] = []

    def alvo(c: "psycopg.Connection") -> None:
        try:
            barreira.wait()
            row = c.execute(SQL_REGISTRAR, (u, sessao, _agora(), "password", "aal1", None, None)).fetchone()
            assert row is not None
            resultados.append(bool(row[0]))
        except BaseException as e:  # noqa: BLE001
            erros.append(e)

    threads = [threading.Thread(target=alvo, args=(c,)) for c in conexoes]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=90)
        assert not any(t.is_alive() for t in threads), "chamada concorrente travada"
    finally:
        for c in conexoes:
            c.close()
    assert not erros, erros
    assert sorted(resultados) == [False, True]
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.acessos_login where session_id = %s", (sessao,)
        ).fetchone() or (0,)
    assert n == 1


# --- registrar: IP mascarado ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ip",
    ["203.0.113.0/24", "203.0.0.0/16", "10.0.0.0/8", "2001:db8:1::/48", "2001:db8::/32", "2001::/16"],
)
def test_ip_mascarado_aceito(ip: str) -> None:
    sessao = uuid.uuid4()
    assert _registrar(_usuario(), sessao, ip=ip) is True
    with h.conectar() as c:
        (guardado,) = c.execute(
            "select ip_mascarado::text from public.acessos_login where session_id = %s", (sessao,)
        ).fetchone() or (None,)
    assert guardado == ip


@pytest.mark.parametrize(
    "ip",
    [
        "10",  # atalho classful do cast (viraria 10.0.0.0/8)
        "203.0.113.5",  # IPv4 completo (/32)
        "203.0.113.5/32",
        "203.0.113.5/24",  # bits de host ligados
        "203.0.113.0/25",  # mais fino que /24
        "2001:db8:1:2::/64",
        "2001:db8:1:2::1",
        "0.0.0.0/0",  # piso de prefixo: v4 >= /8
        "10.0.0.0/7",
        "128.0.0.0/1",
        "::/0",  # piso de prefixo: v6 >= /16
        "8000::/1",
        "2000::/15",
        "::ffff:203.0.0.0/104",
        "::1",
        "::ffff:203.0.113.5",
        "nao-e-ip",
        "x" * 100,
    ],
)
def test_ip_nao_mascarado_recusado_sem_vazar_o_valor(ip: str) -> None:
    sessao = uuid.uuid4()
    _falha("AG020", nao_contem=("203.0.113", "2001:db8", "nao-e-ip"), usuario=_usuario(), sessao=sessao, ip=ip)
    assert not _existe(sessao)


def test_check_da_tabela_recusa_ip_completo_mesmo_para_o_dono() -> None:
    u = _usuario()
    for ip in ("203.0.113.5", "2001:db8:1:2::/64", "0.0.0.0/0", "::/0", "10.0.0.0/7", "2000::/15", "::ffff:203.0.0.0/104"):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(
                    "insert into public.acessos_login (usuario_id, session_id, ocorrido_em, ip_mascarado) "
                    "values (%s, gen_random_uuid(), now(), %s)",
                    (u, ip),
                )
        assert h.sqlstate(exc) == CHECK
        assert "ck_acessos_login_ip_mascarado" in str(exc.value)


# --- registrar: ua_familia, metodo, aal -----------------------------------------------------------------
@pytest.mark.parametrize("ua", ["Chrome/Windows", "Mobile Safari/iOS", "Firefox/Linux", "Outro/Desconhecido"])
def test_ua_familia_aceita(ua: str) -> None:
    assert _registrar(_usuario(), ua=ua) is True


def test_lista_fechada_de_ua_na_funcao_e_no_check() -> None:
    """Todas as 56 combinacoes Familia/Sistema passam; qualquer outra coisa e recusada."""
    validas = [f"{n}/{o}" for n in NAVEGADORES for o in SISTEMAS]
    invalidas = [
        "Joao Silva/Maria Souza", "Brave/Windows", "Chrome/Windows 11", "chrome/windows", "Chrome/windows",
        "Chrome/Windows/x", " Chrome/Windows", "Chrome/Windows ", "Chrome//Windows", "Chrome/Plan9", "/", "",
    ]
    with h.conectar() as c:
        for ua in validas:
            assert c.execute("select public.acessos_login_ua_valida(%s)", (ua,)).fetchone() == (True,), ua
        for ua in invalidas:
            assert c.execute("select public.acessos_login_ua_valida(%s)", (ua,)).fetchone() == (False,), ua
        assert c.execute("select public.acessos_login_ua_valida(null)").fetchone() == (True,)
        u = _usuario()
        for ua in validas:  # o CHECK da tabela aceita todas
            c.execute(
                "insert into public.acessos_login (usuario_id, session_id, ocorrido_em, ua_familia) "
                "values (%s, gen_random_uuid(), now(), %s)",
                (u, ua),
            )


@pytest.mark.parametrize(
    "ua",
    [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "curl/8.4.0",
        "Chrome/120.0.0.0",
        "Chrome",
        "/Windows",
        "Chrome/",
        "x" * 60,
        "Chrome/Windows\n",
        "<script>/alert",
        "",
        "Joao Silva/Maria Souza",  # nome de pessoa no formato antigo (regex): agora recusado
        "Brave/Windows",
        "Chrome/Windows/x",
    ],
)
def test_ua_bruto_ou_fora_do_formato_recusado(ua: str) -> None:
    sessao = uuid.uuid4()
    _falha("AG020", nao_contem=("Mozilla", "curl", "script", "Joao", "Maria"), usuario=_usuario(), sessao=sessao, ua=ua)
    assert not _existe(sessao)


@pytest.mark.parametrize(
    "ua", ["Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101", "Joao Silva/Maria Souza", "Brave/Windows", ""]
)
def test_check_da_tabela_recusa_ua_fora_da_lista_mesmo_para_o_dono(ua: str) -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                "insert into public.acessos_login (usuario_id, session_id, ocorrido_em, ua_familia) "
                "values (%s, gen_random_uuid(), now(), %s)",
                (_usuario(), ua),
            )
    assert h.sqlstate(exc) == CHECK
    assert "ck_acessos_login_ua_familia" in str(exc.value)


@pytest.mark.parametrize(
    "coluna, valor, restricao",
    [("metodo", "sms", "ck_acessos_login_metodo"), ("aal", "aal3", "ck_acessos_login_aal")],
)
def test_check_da_tabela_recusa_metodo_e_aal_invalidos_mesmo_para_o_dono(
    coluna: str, valor: str, restricao: str
) -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                f"insert into public.acessos_login (usuario_id, session_id, ocorrido_em, {coluna}) "
                "values (%s, gen_random_uuid(), now(), %s)",
                (_usuario(), valor),
            )
    assert h.sqlstate(exc) == CHECK
    assert restricao in str(exc.value)


def test_metodo_e_aal_validos_e_invalidos() -> None:
    for metodo in ("password", "totp", "outro"):
        assert _registrar(_usuario(), metodo=metodo) is True
    for aal in ("aal1", "aal2"):
        assert _registrar(_usuario(), aal=aal) is True
    _falha("AG020", usuario=_usuario(), metodo="sms")
    _falha("AG020", usuario=_usuario(), aal="aal3")


# --- registrar: usuario, obrigatorios e janela -----------------------------------------------------------
def test_usuario_inativo_ou_inexistente() -> None:
    sessao = uuid.uuid4()
    _falha("AG019", usuario=_usuario("inativo", ativo=False), sessao=sessao)
    _falha("AG019", usuario=uuid.uuid4(), sessao=sessao)
    assert not _existe(sessao)


def test_campos_obrigatorios() -> None:
    u = _usuario()
    for params in (
        (None, uuid.uuid4(), _agora()),
        (u, None, _agora()),
        (u, uuid.uuid4(), None),
    ):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(SQL_REGISTRAR, (*params, None, None, None, None))
        assert h.sqlstate(exc) == "AG020"


def test_janela_do_momento() -> None:
    u = _usuario()
    _falha("AG020", usuario=u, ocorrido=datetime.now(timezone.utc) + timedelta(minutes=30))
    _falha("AG020", usuario=u, ocorrido=datetime.now(timezone.utc) - timedelta(days=8))
    assert _registrar(u, ocorrido=datetime.now(timezone.utc) - timedelta(days=6)) is True


def test_reenvio_tardio_de_sessao_ja_registrada_e_idempotente() -> None:
    u = _usuario()
    sessao = _inserir_direto(u, datetime.now(timezone.utc) - timedelta(days=30))
    assert _registrar(u, sessao, ocorrido=datetime.now(timezone.utc) - timedelta(days=30)) is False


# --- registrar: sessao do Auth (B1) -----------------------------------------------------------------------
def test_sessao_inexistente_no_auth_e_recusada() -> None:
    sessao = uuid.uuid4()
    _falha("AG020", usuario=_usuario(), sessao=sessao, com_sessao=False)
    assert not _existe(sessao)


def test_sessao_de_outro_usuario_e_recusada_sem_confirmar_a_existencia() -> None:
    dono, outro = _usuario(), _usuario()
    sessao = uuid.uuid4()
    _sessao_auth(dono, sessao)
    with pytest.raises(psycopg.Error) as e1:
        _registrar(outro, sessao, com_sessao=False)
    with pytest.raises(psycopg.Error) as e2:
        _registrar(outro, uuid.uuid4(), com_sessao=False)
    assert h.sqlstate(e1) == "AG020" and h.sqlstate(e2) == "AG020"
    assert str(e1.value) == str(e2.value)  # mesma mensagem: nao distingue os casos
    assert not _existe(sessao)
    assert _registrar(dono, sessao, com_sessao=False) is True  # o dono registra normalmente


def test_sessao_ja_registrada_de_outro_usuario_da_a_mesma_mensagem_uniforme() -> None:
    dono, outro = _usuario(), _usuario()
    sessao = uuid.uuid4()
    assert _registrar(dono, sessao) is True
    with pytest.raises(psycopg.Error) as e1:  # historico ja tem o session_id, mas e do dono
        _registrar(outro, sessao, com_sessao=False)
    with pytest.raises(psycopg.Error) as e2:  # session_id que nao existe em lugar nenhum
        _registrar(outro, uuid.uuid4(), com_sessao=False)
    assert h.sqlstate(e1) == "AG020" and str(e1.value) == str(e2.value)
    assert "Sessão de autenticação inválida." in str(e1.value)
    assert _registrar(dono, sessao, com_sessao=False) is False  # o dono segue idempotente


def test_trava_final_da_migration_continua_valida() -> None:
    """O bloco DO final (premissas do dono, grants, CHECKs) passa no banco atual."""
    bloco = MIGRATION_2[MIGRATION_2.index("-- 8. Trava"):]
    bloco = bloco[bloco.index("do $$"):bloco.index("notify pgrst")]
    with h.conectar() as c:
        c.execute(bloco)


def test_ocorrido_em_vem_do_created_at_da_sessao_do_auth() -> None:
    u = _usuario()
    sessao = uuid.uuid4()
    criada = (datetime.now(timezone.utc) - timedelta(days=2)).replace(microsecond=0)
    _sessao_auth(u, sessao, criada)
    # p_ocorrido_em valido na janela, mas diferente: o gravado e o do Auth
    assert _registrar(u, sessao, ocorrido=_agora(), com_sessao=False) is True
    with h.conectar() as c:
        (gravado,) = c.execute(
            "select ocorrido_em from public.acessos_login where session_id = %s", (sessao,)
        ).fetchone() or (None,)
    assert gravado == criada


def test_created_at_nulo_no_auth_usa_o_parametro_validado() -> None:
    u = _usuario()
    sessao = uuid.uuid4()
    _sessao_auth(u, sessao, None)
    quando = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(microsecond=0)
    assert _registrar(u, sessao, ocorrido=quando, com_sessao=False) is True
    with h.conectar() as c:
        (gravado,) = c.execute(
            "select ocorrido_em from public.acessos_login where session_id = %s", (sessao,)
        ).fetchone() or (None,)
    assert gravado == quando


def test_sessao_apagada_do_auth_depois_nao_quebra_reenvio_idempotente() -> None:
    u = _usuario()
    sessao = uuid.uuid4()
    assert _registrar(u, sessao) is True
    with h.conectar() as c:
        c.execute("delete from auth.sessions where id = %s", (sessao,))
    assert _registrar(u, sessao, com_sessao=False) is False


# --- nao toca em perfis nem auditoria -----------------------------------------------------------------------
def test_registrar_nao_escreve_em_perfis_nem_auditoria() -> None:
    u = _usuario()
    with h.conectar() as c:
        antes_p = c.execute("select xmin::text from public.perfis where id = %s", (u,)).fetchone()
        (antes_a,) = c.execute("select count(*) from public.auditoria").fetchone() or (0,)
    assert _registrar(u) is True
    with h.conectar() as c:
        depois_p = c.execute("select xmin::text from public.perfis where id = %s", (u,)).fetchone()
        (depois_a,) = c.execute("select count(*) from public.auditoria").fetchone() or (0,)
    assert antes_p == depois_p
    assert antes_a == depois_a


def test_registrar_nao_toma_perfis_admin() -> None:
    """Outra sessao segura o advisory 'sisveiculos.perfis_admin': registrar nao pode esperar."""
    u = _usuario()
    sessao = uuid.uuid4()
    _sessao_auth(u, sessao)
    segura = h.conectar()
    try:
        with segura.transaction():
            segura.execute("select pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0))")
            with h.conectar() as c:
                c.execute("set lock_timeout = '3s'")
                inicio = time.monotonic()
                row = c.execute(
                    SQL_REGISTRAR, (u, sessao, _agora(), "password", "aal1", "203.0.113.0/24", "Chrome/Windows")
                ).fetchone()
                assert row is not None and row[0] is True
                assert time.monotonic() - inicio < 2.5
                # ultimo_acesso e listar tambem nao esperam
                c.execute("select * from public.ultimo_acesso_por_usuario(%s::uuid[])", ([u],))
                c.execute(SQL_LISTAR, (u, None, None, 10, 0))
            # prova de que o lock estava mesmo seguro: um terceiro nao o obtem
            with h.conectar() as c:
                (livre,) = c.execute(
                    "select pg_try_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0))"
                ).fetchone() or (None,)
                assert livre is False
    finally:
        segura.close()


# --- imutabilidade ---------------------------------------------------------------------------------------
def test_update_delete_truncate_bloqueados_com_ag003_ate_para_o_dono() -> None:
    u = _usuario()
    recente = _inserir_direto(u, _agora())
    antiga = _inserir_direto(u, "now() - interval '13 months'")
    for sql, params in (
        ("update public.acessos_login set metodo = 'outro' where session_id = %s", (recente,)),
        ("update public.acessos_login set ocorrido_em = now() where session_id = %s", (antiga,)),
        ("delete from public.acessos_login where session_id = %s", (recente,)),
        ("delete from public.acessos_login where session_id = %s", (antiga,)),  # antiga, mas sem expurgo
        ("truncate public.acessos_login", ()),
    ):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(sql, params)
        assert h.sqlstate(exc) == "AG003", sql
        assert "DETAIL" not in str(exc.value)
    assert _existe(recente) and _existe(antiga)


def test_piso_de_6_meses_vale_ate_com_a_variavel_de_expurgo_ligada() -> None:
    u = _usuario()
    recente = _inserir_direto(u, "now() - interval '5 months'")
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            with c.transaction():
                c.execute("select set_config('sisveiculos.expurgo_acessos', '1', true)")
                c.execute("delete from public.acessos_login where session_id = %s", (recente,))
    assert h.sqlstate(exc) == "AG003"
    assert _existe(recente)


def test_update_e_truncate_bloqueados_mesmo_com_a_variavel_de_expurgo() -> None:
    u = _usuario()
    antiga = _inserir_direto(u, "now() - interval '13 months'")
    for sql, params in (
        ("update public.acessos_login set metodo = 'outro' where session_id = %s", (antiga,)),
        ("truncate public.acessos_login", ()),
    ):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                with c.transaction():
                    c.execute("select set_config('sisveiculos.expurgo_acessos', '1', true)")
                    c.execute(sql, params)
        assert h.sqlstate(exc) == "AG003", sql
    assert _existe(antiga)


def test_papeis_da_api_nao_deletam_nem_forjando_a_variavel() -> None:
    antiga = _inserir_direto(_usuario(), "now() - interval '13 months'")
    for papel in ("anon", "authenticated", "service_role"):
        with pytest.raises(psycopg.Error) as exc:
            with h.como(papel) as c:
                c.execute("select set_config('sisveiculos.expurgo_acessos', '1', true)")
                c.execute("delete from public.acessos_login where session_id = %s", (antiga,))
        assert h.sqlstate(exc) == NEGADO, papel
    assert _existe(antiga)


# --- expurgo -----------------------------------------------------------------------------------------------
def test_expurgo_apaga_so_o_que_passou_de_12_meses() -> None:
    u = _usuario()
    velha = _inserir_direto(u, "now() - interval '13 months'")
    muito_velha = _inserir_direto(u, "now() - interval '40 months'")
    quase = _inserir_direto(u, "now() - interval '11 months'")
    sete = _inserir_direto(u, "now() - interval '7 months'")
    recente = _inserir_direto(u, _agora())
    with h.como("service_role") as c:  # so prova a permissao; rollback
        c.execute("select public.purgar_acessos_login(12)")
    assert _existe(velha) and _existe(muito_velha)  # nada persistiu no rollback
    with h.conectar() as c:
        (n,) = c.execute("select public.purgar_acessos_login()").fetchone() or (0,)
    assert n >= 2
    assert not _existe(velha) and not _existe(muito_velha)
    assert _existe(quase) and _existe(sete) and _existe(recente)


def test_expurgo_nao_aceita_prazo_abaixo_de_12_meses_mas_o_piso_do_gatilho_segue_em_6() -> None:
    u = _usuario()
    oito = _inserir_direto(u, "now() - interval '8 months'")
    treze = _inserir_direto(u, "now() - interval '13 months'")
    for meses in (6, 11):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute("select public.purgar_acessos_login(%s::integer)", (meses,))
        assert h.sqlstate(exc) == "AG020", meses
        assert "Prazo de retenção fora do intervalo permitido (12 a 1200 meses)." in str(exc.value)
    assert _existe(oito) and _existe(treze)
    with h.conectar() as c:
        c.execute("select public.purgar_acessos_login(12)")
        c.execute("select public.purgar_acessos_login(1200)")
    assert _existe(oito) and not _existe(treze)


@pytest.mark.parametrize("meses", [11, 6, 5, 1, 0, -3, None, 1201])
def test_expurgo_recusa_prazo_invalido(meses: int | None) -> None:
    antiga = _inserir_direto(_usuario(), "now() - interval '30 months'")
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("select public.purgar_acessos_login(%s::integer)", (meses,))
    assert h.sqlstate(exc) == "AG020"
    assert _existe(antiga)


def test_variavel_de_expurgo_nao_fica_ligada_apos_a_funcao() -> None:
    u = _usuario()
    antiga = _inserir_direto(u, "now() - interval '14 months'")
    with h.conectar() as c:
        with c.transaction():
            c.execute("select public.purgar_acessos_login(12)")
            (valor,) = c.execute("select current_setting('sisveiculos.expurgo_acessos', true)").fetchone() or (None,)
            assert valor in (None, "")
            # a antiga ja foi apagada; uma nova antiga nao pode ser apagada fora da funcao
            c.execute(
                "insert into public.acessos_login (usuario_id, session_id, ocorrido_em) "
                "values (%s, gen_random_uuid(), now() - interval '14 months')",
                (u,),
            )
            c.execute("savepoint s")
            with pytest.raises(psycopg.Error) as exc:
                c.execute("delete from public.acessos_login where ocorrido_em < now() - interval '13 months'")
            assert h.sqlstate(exc) == "AG003"
            c.execute("rollback to savepoint s")
    assert not _existe(antiga)


# --- listar ---------------------------------------------------------------------------------------------------
def _massa() -> tuple[uuid.UUID, uuid.UUID, str, list[tuple[uuid.UUID, datetime]]]:
    """5 acessos para u1 (a cada 2 dias) e 2 para u2. Devolve (u1, u2, nome_u1, [(sessao, quando)])."""
    u1 = _usuario("alice")
    u2 = _usuario("bruno")
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=30)
    itens = []
    for i in range(5):
        quando = base + timedelta(days=2 * i)
        itens.append((_inserir_direto(u1, quando), quando))
    _inserir_direto(u2, base)
    _inserir_direto(u2, base + timedelta(days=1))
    with h.conectar() as c:
        (nome,) = c.execute("select nome from public.perfis where id = %s", (u1,)).fetchone() or ("",)
    return u1, u2, nome, itens


def test_listar_paginacao_ordem_total_e_nome() -> None:
    u1, _, nome, itens = _massa()
    esperado = [s for s, _ in sorted(itens, key=lambda x: x[1], reverse=True)]
    p1 = _listar(u1, limite=2, offset=0)
    p2 = _listar(u1, limite=2, offset=2)
    p3 = _listar(u1, limite=2, offset=4)
    p4 = _listar(u1, limite=2, offset=6)
    for p in (p1, p2, p3, p4):
        assert p["total"] == 5
        assert p["limite"] == 2
    assert [len(p["itens"]) for p in (p1, p2, p3, p4)] == [2, 2, 1, 0]
    refs = [i["sessao_ref"] for p in (p1, p2, p3) for i in p["itens"]]
    assert refs == [_sessao_ref(s) for s in esperado]
    assert all(i["nome"] == nome and i["usuario_id"] == str(u1) for i in p1["itens"])
    datas = [i["ocorrido_em"] for p in (p1, p2, p3) for i in p["itens"]]
    assert datas == sorted(datas, reverse=True)


def test_listar_limites() -> None:
    u1, _, _, _ = _massa()
    assert _listar(u1, limite=1000)["limite"] == 100
    assert _listar(u1, limite=0)["limite"] == 1
    assert _listar(u1, limite=-5)["limite"] == 1
    assert _listar(u1, limite=None)["limite"] == 50
    r = _listar(u1, limite=10, offset=-3)
    assert r["offset"] == 0 and len(r["itens"]) == 5
    assert _listar(u1, limite=10, offset=None)["offset"] == 0
    # sem filtro: nunca mais que 100 itens, total >= o que ha do usuario
    geral = _listar(None, limite=1000)
    assert len(geral["itens"]) <= 100 and geral["total"] >= 7


def test_listar_teto_de_offset() -> None:
    u1, _, _, _ = _massa()
    assert _listar(u1, limite=10, offset=100000)["offset"] == 100000  # no teto: aceito
    for off in (100001, 2_000_000_000):
        with pytest.raises(psycopg.Error) as exc:
            _listar(u1, limite=10, offset=off)
        assert h.sqlstate(exc) == "AG020", off


def test_listar_filtro_por_usuario_e_periodo() -> None:
    u1, u2, _, itens = _massa()
    assert _listar(u2)["total"] == 2
    quandos = sorted(q for _, q in itens)
    # p_de inclusivo, p_ate exclusivo
    r = _listar(u1, de=quandos[1], ate=quandos[3])
    assert r["total"] == 2
    assert {i["sessao_ref"] for i in r["itens"]} == {
        _sessao_ref(s) for s, q in itens if quandos[1] <= q < quandos[3]
    }
    assert _listar(u1, de=quandos[4] + timedelta(seconds=1))["total"] == 0
    assert _listar(u1, ate=quandos[0])["total"] == 0
    assert _listar(u1, de=quandos[0], ate=quandos[4] + timedelta(seconds=1))["total"] == 5
    assert _listar(uuid.uuid4())["total"] == 0


def test_listar_nunca_devolve_session_id_completo() -> None:
    u = _usuario()
    sessao = uuid.uuid4()
    assert _registrar(u, sessao) is True
    r = _listar(u)
    texto = json.dumps(r)
    assert str(sessao) not in texto
    assert sessao.hex not in texto
    item = r["itens"][0]
    assert "session_id" not in item
    assert item["sessao_ref"] == _sessao_ref(sessao)
    assert len(item["sessao_ref"]) == 8
    assert item["ip_mascarado"] == "203.0.113.0/24"
    assert item["ua_familia"] == "Chrome/Windows"
    assert set(item) == {
        "id", "usuario_id", "nome", "ocorrido_em", "metodo", "aal", "ip_mascarado", "ua_familia", "sessao_ref",
    }


# --- ultimo acesso em lote ------------------------------------------------------------------------------------------
def _ultimos(ids: list[uuid.UUID] | None) -> dict[uuid.UUID, datetime]:
    with h.como("service_role") as c:
        rows = c.execute("select * from public.ultimo_acesso_por_usuario(%s::uuid[])", (ids,)).fetchall()
    return {r[0]: r[1] for r in rows}


def test_ultimo_acesso_em_lote() -> None:
    u1, u2, u3 = _usuario(), _usuario(), _usuario()
    agora = datetime.now(timezone.utc).replace(microsecond=0)
    _inserir_direto(u1, agora - timedelta(days=10))
    ultimo_u1 = agora - timedelta(days=1)
    _inserir_direto(u1, ultimo_u1)
    _inserir_direto(u1, agora - timedelta(days=5))
    ultimo_u2 = agora - timedelta(days=3)
    _inserir_direto(u2, ultimo_u2)
    r = _ultimos([u1, u2, u3, uuid.uuid4(), u1])  # u3 sem acesso, id inexistente, duplicado
    assert r == {u1: ultimo_u1, u2: ultimo_u2}
    assert _ultimos([]) == {}
    assert _ultimos(None) == {}


def test_ultimo_acesso_limite_de_100_ids() -> None:
    assert _ultimos([uuid.uuid4() for _ in range(100)]) == {}
    with pytest.raises(psycopg.Error) as exc:
        _ultimos([uuid.uuid4() for _ in range(101)])
    assert h.sqlstate(exc) == "AG020"
