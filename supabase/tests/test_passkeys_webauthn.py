"""Testes da migration 20261122120000 (Passkeys/WebAuthn: credenciais, desafios, reautenticacoes, RPCs, auditoria).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). Os testes gravam e nao apagam (auditoria e append-only);
cada teste cria os proprios usuarios. Rodar sem paralelismo.

    pytest supabase/tests/test_passkeys_webauthn.py -m db_local -p no:xdist
"""

from __future__ import annotations

import re
import threading
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
    Path(__file__).resolve().parents[1] / "migrations" / "20261122120000_passkeys_webauthn.sql"
).read_text(encoding="utf-8")

TABELAS = ("credenciais_webauthn", "desafios_webauthn", "reautenticacoes_webauthn")
RPCS = (
    "public.criar_desafio_webauthn(uuid, text)",
    "public.consumir_desafio_webauthn(uuid, uuid, text)",
    "public.registrar_credencial_webauthn(uuid, text, text, bigint, text[], text)",
    "public.credenciais_ativas_webauthn(uuid)",
    "public.buscar_credencial_webauthn(uuid, text)",
    "public.atualizar_contador_webauthn(uuid, bigint)",
    "public.revogar_credencial_webauthn(uuid, uuid)",
    "public.registrar_reautenticacao_webauthn(uuid, text)",
    "public.reautenticacao_valida_webauthn(uuid, text)",
    "public.registrar_evento_webauthn(uuid, text, text)",
    "public.purgar_webauthn()",
)
ACOES = (
    "passkey_cadastrada",
    "passkey_revogada",
    "passkey_autenticada",
    "passkey_falha",
    "passkey_regressao_contador",
    "passkey_desafio_invalido",
)


# --- infraestrutura -----------------------------------------------------------------------------
def _um(sql: str, params: tuple = ()):
    with h.conectar() as c:
        return c.execute(sql, params).fetchone()[0]  # type: ignore[index]


def _linhas(sql: str, params: tuple = ()) -> list[tuple]:
    with h.conectar() as c:
        return c.execute(sql, params).fetchall()


def _erro(sql: str, params: tuple = ()) -> str | None:
    with h.conectar() as c, pytest.raises(psycopg.Error) as exc:
        c.execute(sql, params)
    return h.sqlstate(exc)


def _cid() -> str:
    return "C" + uuid.uuid4().hex + uuid.uuid4().hex[:7]  # base64url plausivel (40 chars)


CHAVE = "pQECAyYgASFYIK" + "A" * 60


def _cadastrar(
    usuario: uuid.UUID, cred: str | None = None, apelido: str | None = "Notebook"
) -> uuid.UUID:
    return _um(
        "select public.registrar_credencial_webauthn(%s, %s, %s, 0, %s, %s)",
        (usuario, cred or _cid(), CHAVE, ["internal", "hybrid"], apelido),
    )


def _desafio(usuario: uuid.UUID, tipo: str = "autenticacao") -> tuple[uuid.UUID, str]:
    with h.conectar() as c:
        return c.execute("select id, desafio from public.criar_desafio_webauthn(%s, %s)", (usuario, tipo)).fetchone()  # type: ignore[return-value]


def _auditorias(usuario: uuid.UUID, acao: str) -> list[tuple]:
    return _linhas(
        "select detalhes::text, de, para, ator_id from public.auditoria "
        "where tabela = 'perfis' and registro_id = %s and acao = %s order by id",
        (usuario, acao),
    )


_UPD_REAUT = (
    "update public.reautenticacoes_webauthn set valido_ate = now() - interval '{}' "
    "where usuario_id=%s and sessao=%s"
)


def _forcar(tabela: str, gatilho: str, comandos: list[tuple[str, tuple]]) -> None:
    """Altera dados que o gatilho de imutabilidade protege (so em teste, banco descartavel).

    ENABLE ALWAYS vale ate em session_replication_role=replica; por isso desabilita o gatilho DENTRO da
    transacao e o reabilita ALWAYS antes do commit (DDL transacional: nada vaza para outras conexoes).
    """
    with h.conectar() as c, c.transaction():
        c.execute(f"alter table public.{tabela} disable trigger {gatilho}")
        for sql, params in comandos:
            c.execute(sql, params)
        c.execute(f"alter table public.{tabela} enable always trigger {gatilho}")


@pytest.fixture
def u() -> uuid.UUID:
    return h.criar_usuario("pk-user")


# --- RLS e acesso indevido ----------------------------------------------------------------------
@pytest.mark.parametrize("papel", ["anon", "authenticated", "service_role"])
@pytest.mark.parametrize("tabela", TABELAS)
def test_tabelas_negadas_a_papeis_da_api(papel: str, tabela: str, u: uuid.UUID) -> None:
    for sql in (f"select * from public.{tabela}", f"delete from public.{tabela}"):
        with h.como(papel, u if papel == "authenticated" else None) as c, pytest.raises(
            psycopg.Error
        ) as exc:
            c.execute(sql)
        assert h.sqlstate(exc) == "42501"


@pytest.mark.parametrize("tabela", TABELAS)
def test_rls_ligado_sem_policy(tabela: str) -> None:
    assert _um(
        "select relrowsecurity from pg_class where oid = %s::regclass", (f"public.{tabela}",)
    )
    assert (
        _um(
            "select count(*) from pg_policies where schemaname='public' and tablename=%s", (tabela,)
        )
        == 0
    )


@pytest.mark.parametrize("rpc", RPCS)
def test_rpcs_so_service_role(rpc: str) -> None:
    for papel in ("anon", "authenticated"):
        assert _um("select has_function_privilege(%s, %s, 'execute')", (papel, rpc)) is False
    assert _um("select has_function_privilege('service_role', %s, 'execute')", (rpc,)) is True


def test_rpc_negada_a_authenticated_na_pratica(u: uuid.UUID) -> None:
    with h.como("authenticated", u) as c, pytest.raises(psycopg.Error) as exc:
        c.execute("select public.criar_desafio_webauthn(%s, 'registro')", (u,))
    assert h.sqlstate(exc) == "42501"


def test_rpcs_security_definer_com_search_path_vazio() -> None:
    nomes = re.findall(
        r"create or replace function public\.(\w+_webauthn|purgar_webauthn)\(", MIGRATION
    )
    assert len(nomes) >= len(RPCS)
    rows = _linhas(
        "select p.proname, p.prosecdef, p.proconfig from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
        "where n.nspname = 'public' and p.proname = any(%s)",
        (nomes,),
    )
    assert rows
    for nome, secdef, cfg in rows:
        assert secdef, nome
        assert cfg and any(
            x.replace(" ", "") in ("search_path=", 'search_path=""') for x in cfg
        ), nome


# --- desafios -----------------------------------------------------------------------------------
def test_desafio_32_bytes_base64url_e_validade_5_min(u: uuid.UUID) -> None:
    did, des = _desafio(u, "registro")
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", des)
    seg = _um(
        "select extract(epoch from expira_em - criado_em) from public.desafios_webauthn where id=%s",
        (did,),
    )
    assert float(seg) == 300.0
    assert _desafio(u, "registro")[1] != des


def test_desafio_uso_unico(u: uuid.UUID) -> None:
    did, des = _desafio(u)
    assert _um("select public.consumir_desafio_webauthn(%s, %s, 'autenticacao')", (did, u)) == des
    assert _um("select public.consumir_desafio_webauthn(%s, %s, 'autenticacao')", (did, u)) is None


def test_desafio_uso_unico_concorrente(u: uuid.UUID) -> None:
    did, des = _desafio(u)
    barreira = threading.Barrier(6, timeout=60)
    res: list[str | None] = []

    def worker() -> None:
        with h.conectar() as c:
            barreira.wait()
            res.append(c.execute("select public.consumir_desafio_webauthn(%s, %s, 'autenticacao')", (did, u)).fetchone()[0])  # type: ignore[index]

    ts = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(r for r in res if r) == [des] and res.count(None) == 5


def test_desafio_expirado_devolve_nulo(u: uuid.UUID) -> None:
    did, _ = _desafio(u)
    _forcar(
        "desafios_webauthn",
        "trg_desafios_webauthn_proteger",
        [
            (
                "update public.desafios_webauthn set expira_em = now() - interval '1 second' where id=%s",
                (did,),
            )
        ],
    )
    assert _um("select public.consumir_desafio_webauthn(%s, %s, 'autenticacao')", (did, u)) is None


def test_desafio_de_outro_usuario_ou_tipo_mesma_resposta(u: uuid.UUID) -> None:
    outro = h.criar_usuario("pk-outro")
    did, des = _desafio(u, "autenticacao")
    assert (
        _um("select public.consumir_desafio_webauthn(%s, %s, 'autenticacao')", (did, outro)) is None
    )
    assert _um("select public.consumir_desafio_webauthn(%s, %s, 'registro')", (did, u)) is None
    assert (
        _um("select public.consumir_desafio_webauthn(%s, %s, 'autenticacao')", (uuid.uuid4(), u))
        is None
    )
    # as tentativas invalidas nao queimaram o desafio
    assert _um("select public.consumir_desafio_webauthn(%s, %s, 'autenticacao')", (did, u)) == des


def test_novo_desafio_invalida_pendente_do_mesmo_tipo(u: uuid.UUID) -> None:
    a, _ = _desafio(u, "registro")
    b, des_b = _desafio(u, "registro")
    c_, des_c = _desafio(u, "autenticacao")
    assert _um("select public.consumir_desafio_webauthn(%s, %s, 'registro')", (a, u)) is None
    assert _um("select public.consumir_desafio_webauthn(%s, %s, 'registro')", (b, u)) == des_b
    assert _um("select public.consumir_desafio_webauthn(%s, %s, 'autenticacao')", (c_, u)) == des_c


def test_criar_desafio_exige_perfil_ativo_e_tipo_valido(u: uuid.UUID) -> None:
    inativo = h.criar_usuario("pk-inativo", ativo=False)
    assert _erro("select public.criar_desafio_webauthn(%s, 'registro')", (inativo,)) == "AG051"
    assert _erro("select public.criar_desafio_webauthn(%s, 'registro')", (uuid.uuid4(),)) == "AG051"
    assert _erro("select public.criar_desafio_webauthn(%s, 'outro')", (u,)) == "AG050"
    assert _erro("select public.criar_desafio_webauthn(null, 'registro')") == "AG050"


def test_limite_de_desafios_por_hora(u: uuid.UUID) -> None:
    for _ in range(30):
        _desafio(u)
    assert _erro("select public.criar_desafio_webauthn(%s, 'autenticacao')", (u,)) == "AG053"


def test_desafio_imutavel_e_sem_delete(u: uuid.UUID) -> None:
    did, _ = _desafio(u)
    assert (
        _erro("update public.desafios_webauthn set desafio = %s where id=%s", ("A" * 43, did))
        == "AG054"
    )
    assert (
        _erro(
            "update public.desafios_webauthn set expira_em = now() + interval '1 day' where id=%s",
            (did,),
        )
        == "AG054"
    )
    assert (
        _erro("update public.desafios_webauthn set usado_em = null where id=%s", (did,)) == "AG054"
    )
    assert _erro("delete from public.desafios_webauthn where id=%s", (did,)) == "AG003"
    assert _erro("truncate public.desafios_webauthn") == "AG003"


# --- credenciais --------------------------------------------------------------------------------
def test_registrar_credencial_audita_sem_material_criptografico(u: uuid.UUID) -> None:
    cred = _cid()
    rid = _cadastrar(u, cred)
    linhas = _auditorias(u, "passkey_cadastrada")
    assert len(linhas) == 1
    det, de, para, ator = linhas[0]
    assert ator == u and de is None and para is None
    assert str(rid) in det
    assert (
        cred not in det and CHAVE not in det and "credential_id" not in det and "chave" not in det
    )


def test_credencial_duplicada_recusada(u: uuid.UUID) -> None:
    cred = _cid()
    _cadastrar(u, cred)
    assert (
        _erro(
            "select public.registrar_credencial_webauthn(%s, %s, %s, 0, null, null)",
            (h.criar_usuario("pk-dup"), cred, CHAVE),
        )
        == "AG055"
    )


def test_registrar_credencial_valida_parametros(u: uuid.UUID) -> None:
    base = "select public.registrar_credencial_webauthn(%s, %s, %s, %s, %s, %s)"
    assert _erro(base, (u, "curto", CHAVE, 0, None, None)) == "AG050"
    assert _erro(base, (u, _cid(), "tem espaco!!!!!!!!!!!!", 0, None, None)) == "AG050"
    assert _erro(base, (u, _cid(), CHAVE, -1, None, None)) == "AG050"
    assert _erro(base, (u, _cid(), CHAVE, 0, ["usb", "bluetooth"], None)) == "AG050"
    assert _erro(base, (u, _cid(), CHAVE, 0, None, "")) == "AG050"
    assert _erro(base, (u, _cid(), CHAVE, 0, None, "x" * 41)) == "AG050"
    assert _erro(base, (u, _cid(), CHAVE, 0, None, " espaco ")) == "AG050"
    assert _um(base, (u, _cid(), CHAVE, 0, ["usb", "nfc", "ble", "internal", "hybrid"], "x" * 40))


def test_registrar_credencial_exige_perfil_ativo() -> None:
    inativo = h.criar_usuario("pk-inativo2", ativo=False)
    assert (
        _erro(
            "select public.registrar_credencial_webauthn(%s, %s, %s, 0, null, null)",
            (inativo, _cid(), CHAVE),
        )
        == "AG051"
    )


def test_limite_de_10_credenciais_ativas(u: uuid.UUID) -> None:
    ids = [_cadastrar(u) for _ in range(10)]
    assert (
        _erro(
            "select public.registrar_credencial_webauthn(%s, %s, %s, 0, null, null)",
            (u, _cid(), CHAVE),
        )
        == "AG052"
    )
    # revogar libera uma vaga
    assert _um("select public.revogar_credencial_webauthn(%s, %s)", (u, ids[0])) is True
    _cadastrar(u)


def test_limite_de_10_em_concorrencia(u: uuid.UUID) -> None:
    for _ in range(8):
        _cadastrar(u)
    barreira = threading.Barrier(5, timeout=60)
    ok: list[bool] = []

    def worker() -> None:
        with h.conectar() as c:
            barreira.wait()
            try:
                c.execute(
                    "select public.registrar_credencial_webauthn(%s, %s, %s, 0, null, null)",
                    (u, _cid(), CHAVE),
                )
                ok.append(True)
            except psycopg.Error:
                ok.append(False)

    ts = [threading.Thread(target=worker) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert ok.count(True) == 2
    assert (
        _um(
            "select count(*) from public.credenciais_webauthn where usuario_id=%s and revogada_em is null",
            (u,),
        )
        == 10
    )


def test_listagem_so_ativas_do_usuario_sem_chave_publica(u: uuid.UUID) -> None:
    outro = h.criar_usuario("pk-outro2")
    a, b = _cadastrar(u, apelido="A"), _cadastrar(u, apelido="B")
    _cadastrar(outro)
    _um("select public.revogar_credencial_webauthn(%s, %s)", (u, b))
    with h.conectar() as c:
        cur = c.execute("select * from public.credenciais_ativas_webauthn(%s)", (u,))
        cols = [d.name for d in cur.description]  # type: ignore[union-attr]
        rows = cur.fetchall()
    assert cols == ["id", "credential_id", "transportes", "apelido", "criada_em", "ultimo_uso_em"]
    assert [r[0] for r in rows] == [a]
    assert not any(CHAVE in str(v) for r in rows for v in r)


def test_buscar_credencial_so_ativa_e_do_usuario(u: uuid.UUID) -> None:
    outro = h.criar_usuario("pk-outro3")
    cred = _cid()
    rid = _cadastrar(u, cred)
    q = "select id, chave_publica, contador from public.buscar_credencial_webauthn(%s, %s)"
    assert _linhas(q, (u, cred)) == [(rid, CHAVE, 0)]
    assert _linhas(q, (outro, cred)) == []
    _um("select public.revogar_credencial_webauthn(%s, %s)", (u, rid))
    assert _linhas(q, (u, cred)) == []


def test_contador_monotonico(u: uuid.UUID) -> None:
    rid = _cadastrar(u)
    f = "select public.atualizar_contador_webauthn(%s, %s)"
    assert _um(f, (rid, 5)) is True
    assert _um(f, (rid, 5)) is False  # igual
    assert _um(f, (rid, 4)) is False  # regressao
    assert _um(f, (rid, 0)) is False
    assert _um(f, (rid, 6)) is True
    assert _um("select contador from public.credenciais_webauthn where id=%s", (rid,)) == 6
    assert (
        _um("select ultimo_uso_em is not null from public.credenciais_webauthn where id=%s", (rid,))
        is True
    )


def test_contador_zero_para_autenticador_sem_contador(u: uuid.UUID) -> None:
    rid = _cadastrar(u)
    assert _um("select public.atualizar_contador_webauthn(%s, 0)", (rid,)) is True
    assert (
        _um("select ultimo_uso_em is not null from public.credenciais_webauthn where id=%s", (rid,))
        is True
    )


def test_contador_de_revogada_ou_inexistente_recusado(u: uuid.UUID) -> None:
    rid = _cadastrar(u)
    _um("select public.revogar_credencial_webauthn(%s, %s)", (u, rid))
    assert _um("select public.atualizar_contador_webauthn(%s, 9)", (rid,)) is False
    assert _um("select public.atualizar_contador_webauthn(%s, 9)", (uuid.uuid4(),)) is False


def test_revogacao_so_do_dono_e_auditada(u: uuid.UUID) -> None:
    outro = h.criar_usuario("pk-intruso")
    rid = _cadastrar(u)
    assert _um("select public.revogar_credencial_webauthn(%s, %s)", (outro, rid)) is False
    assert (
        _um("select revogada_em is null from public.credenciais_webauthn where id=%s", (rid,))
        is True
    )
    assert _auditorias(u, "passkey_revogada") == []
    assert _um("select public.revogar_credencial_webauthn(%s, %s)", (u, rid)) is True
    assert (
        _um("select public.revogar_credencial_webauthn(%s, %s)", (u, rid)) is False
    )  # idempotente
    linhas = _auditorias(u, "passkey_revogada")
    assert len(linhas) == 1 and str(rid) in linhas[0][0]


def test_credencial_imutavel_sem_delete_sem_truncate(u: uuid.UUID) -> None:
    rid = _cadastrar(u)
    for col, val in (
        ("credential_id", _cid()),
        ("chave_publica", CHAVE + "B"),
        ("usuario_id", h.criar_usuario("pk-troca")),
        ("criada_em", "2000-01-01"),
        ("transportes", ["usb"]),
    ):
        assert (
            _erro(f"update public.credenciais_webauthn set {col} = %s where id=%s", (val, rid))
            == "AG054"
        ), col
    assert _erro("delete from public.credenciais_webauthn where id=%s", (rid,)) == "AG003"
    assert _erro("truncate public.credenciais_webauthn") == "AG003"


def test_contador_nao_regride_nem_por_update_direto(u: uuid.UUID) -> None:
    rid = _cadastrar(u)
    _um("select public.atualizar_contador_webauthn(%s, 7)", (rid,))
    assert (
        _erro("update public.credenciais_webauthn set contador = 3 where id=%s", (rid,)) == "AG054"
    )


def test_revogada_nao_reativa(u: uuid.UUID) -> None:
    rid = _cadastrar(u)
    _um("select public.revogar_credencial_webauthn(%s, %s)", (u, rid))
    assert (
        _erro("update public.credenciais_webauthn set revogada_em = null where id=%s", (rid,))
        == "AG054"
    )


def test_check_constraints_da_tabela(u: uuid.UUID) -> None:
    rid = _cadastrar(u)
    assert (
        _erro("update public.credenciais_webauthn set contador = -1 where id=%s", (rid,)) == "AG054"
    )
    assert (
        _erro("update public.credenciais_webauthn set apelido = '' where id=%s", (rid,)) == "23514"
    )
    assert (
        _erro("update public.credenciais_webauthn set apelido = %s where id=%s", ("x" * 41, rid))
        == "23514"
    )


# --- reautenticacao -----------------------------------------------------------------------------
def test_reautenticacao_valida_e_por_sessao(u: uuid.UUID) -> None:
    sess = "sess-" + uuid.uuid4().hex
    assert _um("select public.reautenticacao_valida_webauthn(%s, %s)", (u, sess)) is False
    ate = _um("select public.registrar_reautenticacao_webauthn(%s, %s)", (u, sess))
    assert ate is not None
    seg = _um("select extract(epoch from %s::timestamptz - now())", (ate,))
    assert 290 < float(seg) <= 300
    assert _um("select public.reautenticacao_valida_webauthn(%s, %s)", (u, sess)) is True
    assert _um("select public.reautenticacao_valida_webauthn(%s, %s)", (u, "outra-sessao")) is False
    assert (
        _um(
            "select public.reautenticacao_valida_webauthn(%s, %s)", (h.criar_usuario("pk-o4"), sess)
        )
        is False
    )
    linhas = _auditorias(u, "passkey_autenticada")
    assert len(linhas) == 1 and sess not in linhas[0][0]


def test_reautenticacao_expirada_nao_vale(u: uuid.UUID) -> None:
    sess = "sess-" + uuid.uuid4().hex
    _um("select public.registrar_reautenticacao_webauthn(%s, %s)", (u, sess))
    _forcar(
        "reautenticacoes_webauthn",
        "trg_reautenticacoes_webauthn_proteger",
        [(_UPD_REAUT.format("1 second"), (u, sess))],
    )
    assert _um("select public.reautenticacao_valida_webauthn(%s, %s)", (u, sess)) is False


def test_reautenticacao_valida_parametros_e_perfil(u: uuid.UUID) -> None:
    inativo = h.criar_usuario("pk-inativo3", ativo=False)
    f = "select public.registrar_reautenticacao_webauthn(%s, %s)"
    assert _erro(f, (inativo, "s")) == "AG051"
    assert _erro(f, (u, "")) == "AG050"
    assert _erro(f, (u, "s" * 129)) == "AG050"
    assert _erro(f, (None, "s")) == "AG050"
    assert _um(f, (u, "s" * 128)) is not None


def test_reautenticacao_imutavel_e_sem_delete(u: uuid.UUID) -> None:
    sess = "sess-" + uuid.uuid4().hex
    _um("select public.registrar_reautenticacao_webauthn(%s, %s)", (u, sess))
    assert (
        _erro(
            "update public.reautenticacoes_webauthn set valido_ate = now() + interval '1 day' where sessao=%s",
            (sess,),
        )
        == "AG003"
    )
    assert _erro("delete from public.reautenticacoes_webauthn where sessao=%s", (sess,)) == "AG003"
    assert _erro("truncate public.reautenticacoes_webauthn") == "AG003"


# --- eventos e auditoria ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "evento", ["passkey_falha", "passkey_regressao_contador", "passkey_desafio_invalido"]
)
def test_registrar_evento_allowlist(evento: str, u: uuid.UUID) -> None:
    _um("select public.registrar_evento_webauthn(%s, %s, 'assinatura_invalida')", (u, evento))
    linhas = _auditorias(u, evento)
    assert len(linhas) == 1
    assert "assinatura_invalida" in linhas[0][0]


@pytest.mark.parametrize(
    "evento,codigo",
    [
        ("passkey_cadastrada", "ok"),  # evento fora da allowlist
        ("transicao", "ok"),
        ("passkey_falha", "Maiuscula"),
        ("passkey_falha", "tem-hifen"),
        ("passkey_falha", "x" * 41),
        ("passkey_falha", ""),
        ("passkey_falha", "origem_https://x"),
        (None, "ok"),
        ("passkey_falha", None),
    ],
)
def test_registrar_evento_rejeita_fora_da_allowlist(
    evento: str | None, codigo: str | None, u: uuid.UUID
) -> None:
    assert (
        _erro("select public.registrar_evento_webauthn(%s, %s, %s)", (u, evento, codigo)) == "AG050"
    )


def test_registrar_evento_perfil_inexistente() -> None:
    assert (
        _erro("select public.registrar_evento_webauthn(%s, 'passkey_falha', 'x')", (uuid.uuid4(),))
        == "AG051"
    )


def test_auditoria_nunca_guarda_material_criptografico(u: uuid.UUID) -> None:
    cred = _cid()
    rid = _cadastrar(u, cred)
    did, des = _desafio(u)
    _um("select public.consumir_desafio_webauthn(%s, %s, 'autenticacao')", (did, u))
    _um("select public.atualizar_contador_webauthn(%s, 3)", (rid,))
    _um("select public.registrar_reautenticacao_webauthn(%s, %s)", (u, "sess-" + uuid.uuid4().hex))
    _um("select public.registrar_evento_webauthn(%s, 'passkey_falha', 'origem_invalida')", (u,))
    _um("select public.revogar_credencial_webauthn(%s, %s)", (u, rid))
    linhas = _linhas(
        "select detalhes::text || coalesce(de, '') || coalesce(para, '') from public.auditoria "
        "where tabela='perfis' and registro_id=%s and acao like 'passkey\\_%%'",
        (u,),
    )
    assert len(linhas) == 4
    texto = " ".join(r[0] for r in linhas)
    for segredo in (cred, CHAVE, des):
        assert segredo not in texto


def test_acoes_de_auditoria_permitidas_e_anteriores_preservadas() -> None:
    d = _um(
        "select pg_get_constraintdef(oid) from pg_constraint where conrelid='public.auditoria'::regclass and conname='ck_auditoria_acao'"
    )
    for a in ACOES + ("criada", "transicao", "saudacao_excluida", "localizacao_expurgada"):
        assert f"'{a}'" in d, a


def test_auditoria_continua_append_only(u: uuid.UUID) -> None:
    _cadastrar(u)
    assert (
        _erro(
            "update public.auditoria set acao='criada' where acao='passkey_cadastrada' and registro_id=%s",
            (u,),
        )
        == "AG003"
    )
    assert _erro("delete from public.auditoria where registro_id=%s", (u,)) == "AG003"


# --- expurgo ------------------------------------------------------------------------------------
def test_purgar_remove_so_vencidos_ha_mais_de_um_dia(u: uuid.UUID) -> None:
    velho, _ = _desafio(u, "registro")
    recente, _ = _desafio(u, "autenticacao")
    sess_velha, sess_nova = "v-" + uuid.uuid4().hex, "n-" + uuid.uuid4().hex
    _um("select public.registrar_reautenticacao_webauthn(%s, %s)", (u, sess_velha))
    _um("select public.registrar_reautenticacao_webauthn(%s, %s)", (u, sess_nova))
    _forcar(
        "desafios_webauthn",
        "trg_desafios_webauthn_proteger",
        [
            (
                "update public.desafios_webauthn set expira_em = now() - interval '2 days' where id=%s",
                (velho,),
            )
        ],
    )
    _forcar(
        "reautenticacoes_webauthn",
        "trg_reautenticacoes_webauthn_proteger",
        [(_UPD_REAUT.format("2 days"), (u, sess_velha))],
    )
    assert _um("select public.purgar_webauthn()") >= 2
    assert _um("select count(*) from public.desafios_webauthn where id=%s", (velho,)) == 0
    assert _um("select count(*) from public.desafios_webauthn where id=%s", (recente,)) == 1
    assert (
        _um("select count(*) from public.reautenticacoes_webauthn where sessao=%s", (sess_velha,))
        == 0
    )
    assert (
        _um("select count(*) from public.reautenticacoes_webauthn where sessao=%s", (sess_nova,))
        == 1
    )
    # o expurgo nao deixa o DELETE liberado fora dele
    assert _erro("delete from public.desafios_webauthn where id=%s", (recente,)) == "AG003"


# --- migration idempotente ----------------------------------------------------------------------
def test_migration_idempotente() -> None:
    with h.reaplicando_migration() as c:
        c.execute(MIGRATION)
        c.execute(MIGRATION)
