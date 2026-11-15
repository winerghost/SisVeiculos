"""Regressao da migration 20261130120000_regex_push_passkeys.

O Postgres limita repeticoes {m,n} a 255; 20261120120000 (push) e 20261122120000 (passkeys) usavam
{16,256}/{16,1024}/{16,4096} e nenhuma inscricao/passkey era gravada. Aqui: limites aceitos e recusados
pelas RPCs e pelas constraints (INSERT direto), e uma varredura de todo o schema public para que nenhuma
repeticao acima de 255 volte (constraints e funcoes).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). Os testes gravam e nao apagam.

    pytest supabase/tests/test_regex_push_passkeys.py -m db_local -p no:xdist
"""

from __future__ import annotations

import re
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

AUTH = "a" * 22
# Repeticao acima de 255: {256}, {256,}, {16,256}, {1000,2000} etc. (mesma ideia da revisao B1).
_ACIMA_255 = r"(?:[0-9]{4,}|25[6-9]|2[6-9][0-9]|[3-9][0-9]{2})"
REPETICAO_INVALIDA = re.compile(r"\{" + _ACIMA_255 + r"(?:,[0-9]*)?\}|\{[0-9]+," + _ACIMA_255 + r"\}")


def _admin() -> uuid.UUID:
    return h.criar_usuario("rgx-push", "admin")


def _sqlstate(sql_: str, params: tuple) -> str | None:
    try:
        with h.conectar() as c:
            c.execute(sql_, params)
    except psycopg.Error as exc:
        return exc.sqlstate
    return None


def _push(uid: uuid.UUID, p256dh: str) -> str | None:
    return _sqlstate(
        "select public.registrar_inscricao_push(%s, %s, %s, %s)",
        (uid, f"https://fcm.googleapis.com/fcm/send/{uuid.uuid4().hex}", p256dh, AUTH),
    )


def _passkey(uid: uuid.UUID, credential_id: str, chave: str) -> str | None:
    return _sqlstate(
        "select public.registrar_credencial_webauthn(%s, %s, %s, 0, null, null)",
        (uid, credential_id, chave),
    )


def _cid(tamanho: int) -> str:
    base = uuid.uuid4().hex  # unico por chamada (credential_id e unico)
    return (base * (tamanho // len(base) + 1))[:tamanho]


@pytest.mark.parametrize("p256dh", ["B" * 16, "B" * 87 + "=", "B" * 256, "B" * 256 + "=="])
def test_push_aceita_p256dh_nos_limites(p256dh: str) -> None:
    assert _push(_admin(), p256dh) is None


@pytest.mark.parametrize("p256dh", ["B" * 15, "B" * 257, "B" * 40 + "=" + "B" * 40, "B" * 40 + "+", ""])
def test_push_recusa_p256dh_fora_do_formato(p256dh: str) -> None:
    assert _push(_admin(), p256dh) == "AG042"


@pytest.mark.parametrize(("cid", "chave"), [(16, 16), (1024, 300), (43, 4096)])
def test_passkey_aceita_nos_limites(cid: int, chave: int) -> None:
    assert _passkey(_admin(), _cid(cid), "k" * chave) is None


@pytest.mark.parametrize(
    ("cid", "chave"),
    [
        ("c" * 15, "k" * 300),
        (None, "k" * 300),
        ("c" * 43, "k" * 15),
        ("c" * 43, "k" * 4097),
        ("c" * 42 + "!", "k" * 300),
    ],
)
def test_passkey_recusa_fora_do_formato(cid: str | None, chave: str) -> None:
    assert _passkey(_admin(), cid if cid is not None else _cid(1025), chave) == "AG050"


@pytest.mark.parametrize(
    ("p256dh", "aceita"), [("B" * 16, True), ("B" * 256 + "=", True), ("B" * 15, False), ("B" * 257, False)]
)
def test_constraint_push_no_insert_direto(p256dh: str, aceita: bool) -> None:
    uid = _admin()
    endpoint = f"https://fcm.googleapis.com/fcm/send/{uuid.uuid4().hex}"
    estado = _sqlstate(
        "insert into public.push_inscricoes (usuario_id, endpoint, endpoint_hash, p256dh, auth) "
        "values (%s, %s, encode(sha256(convert_to(%s, 'UTF8')), 'hex'), %s, %s)",
        (uid, endpoint, endpoint, p256dh, AUTH),
    )
    assert estado is None if aceita else estado == "23514", estado  # 23514 = check_violation


@pytest.mark.parametrize(
    ("cid", "chave", "aceita"),
    [(1024, 4096, True), (16, 16, True), (1025, 300, False), (43, 4097, False)],
)
def test_constraints_passkey_no_insert_direto(cid: int, chave: int, aceita: bool) -> None:
    estado = _sqlstate(
        "insert into public.credenciais_webauthn (usuario_id, credential_id, chave_publica) values (%s, %s, %s)",
        (_admin(), _cid(cid), "k" * chave),
    )
    assert estado is None if aceita else estado == "23514", estado


def test_nenhuma_repeticao_acima_de_255_em_constraints_ou_funcoes_de_public() -> None:
    with h.conectar() as c:
        defs = [
            (r[0], r[1])
            for r in c.execute(
                "select conrelid::regclass::text || '.' || conname, pg_get_constraintdef(oid) "
                "from pg_constraint where contype = 'c' and connamespace = 'public'::regnamespace"
            ).fetchall()
        ]
        defs += [
            (r[0], r[1])
            for r in c.execute(
                "select p.oid::regprocedure::text, p.prosrc from pg_proc p "
                "where p.pronamespace = 'public'::regnamespace and p.prokind in ('f', 'p')"
            ).fetchall()
        ]
    assert defs, "nenhuma constraint/funcao lida"
    ruins = [nome for nome, corpo in defs if corpo and REPETICAO_INVALIDA.search(corpo)]
    assert not ruins, f"repeticao de regex acima de 255 (Postgres recusa): {ruins}"


def test_varredura_detecta_os_padroes_antigos() -> None:
    for antigo in ("{16,256}", "{16,1024}", "{16,4096}", "{256}", "{300,}"):
        assert REPETICAO_INVALIDA.search(antigo), antigo
    for valido in ("{2}", "{64}", "{8,128}", "{16,32}", "{255}", "{1,255}"):
        assert not REPETICAO_INVALIDA.search(valido), valido
