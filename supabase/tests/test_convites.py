"""Testes da migration 6: convites (hash, uso unico, prazo, dominio de e-mail).

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.

    pytest supabase/tests/test_convites.py -m db_local
"""

from __future__ import annotations

import threading

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]


def _consumir(token_hash: str, usuario, nome: str = "Convidado") -> None:
    with h.conectar() as c:
        c.execute("select public.consumir_convite(%s, %s, %s)", (token_hash, usuario, nome))


def test_convite_valido_cria_perfil_e_marca_usado() -> None:
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    cid, th = h.novo_convite(admin, email, role="autorizador")
    uid = h.criar_auth(email)

    _consumir(th, uid)

    assert h.perfil_de(uid) == ("autorizador", True)
    acoes = [a for a, *_ in h.auditoria_de("convites", cid)]
    assert acoes == ["convite_criado", "convite_usado"]


def test_convite_so_pode_ser_usado_uma_vez() -> None:
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    _, th = h.novo_convite(admin, email)
    uid = h.criar_auth(email)
    _consumir(th, uid)
    with pytest.raises(psycopg.Error) as exc:
        _consumir(th, uid)
    assert h.sqlstate(exc) == "AG005"


def test_uso_simultaneo_do_mesmo_token_so_um_vence() -> None:
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    _, th = h.novo_convite(admin, email)
    uid = h.criar_auth(email)
    barreira = threading.Barrier(2)
    resultados: list[str | None] = []
    lock = threading.Lock()

    def tentar() -> None:
        with psycopg.connect(h.DSN) as conn:
            barreira.wait(timeout=60)
            try:
                conn.execute("select public.consumir_convite(%s, %s, 'Convidado')", (th, uid))
                conn.commit()
                r: str | None = "ok"
            except psycopg.Error as e:
                conn.rollback()
                r = e.sqlstate
        with lock:
            resultados.append(r)

    ts = [threading.Thread(target=tentar) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=90)
    assert sorted(str(r) for r in resultados) == ["AG005", "ok"], resultados


def test_convite_expirado_e_recusado() -> None:
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    _, th = h.novo_convite(admin, email, expirado=True)
    uid = h.criar_auth(email)
    with pytest.raises(psycopg.Error) as exc:
        _consumir(th, uid)
    assert h.sqlstate(exc) == "AG006"
    assert h.auditoria_de("perfis", uid) == []


def test_update_direto_nao_contorna_a_expiracao() -> None:
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    cid, _ = h.novo_convite(admin, email, expirado=True)
    uid = h.criar_usuario("qualquer")
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                "update public.convites set usado_em = now(), usado_por = %s where id = %s",
                (uid, cid),
            )
    assert h.sqlstate(exc) == "AG006"


def test_convite_revogado_nao_vale() -> None:
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    cid, th = h.novo_convite(admin, email)
    with h.conectar() as c:
        c.execute(
            "update public.convites set revogado_em = now(), revogado_por = %s where id = %s",
            (admin, cid),
        )
    uid = h.criar_auth(email)
    with pytest.raises(psycopg.Error) as exc:
        _consumir(th, uid)
    assert h.sqlstate(exc) == "AG005"


def test_token_desconhecido_e_email_divergente() -> None:
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    _, th = h.novo_convite(admin, email)
    _, hash_inexistente = h.novo_token()
    outra_conta = h.criar_auth(h.email_unico())
    with pytest.raises(psycopg.Error) as exc:
        _consumir(hash_inexistente, outra_conta)
    assert h.sqlstate(exc) == "AG007"
    with pytest.raises(psycopg.Error) as exc:
        _consumir(th, outra_conta)  # token certo, conta de outro e-mail
    assert h.sqlstate(exc) == "AG007"


def test_so_admin_ativo_cria_convite() -> None:
    comum = h.criar_usuario("comum", "autorizador")
    admin_inativo = h.criar_usuario("admin-inativo", "admin", ativo=False)
    for quem in (comum, admin_inativo):
        with pytest.raises(psycopg.Error) as exc:
            h.novo_convite(quem, h.email_unico())
        assert h.sqlstate(exc) == "AG002"


@pytest.mark.parametrize(
    "email",
    ["pessoa@hotmail.com", "pessoa@gmail.com.br", "pessoa@agu.gov.br.evil.com", "Pessoa@gmail.com", "x@evilagu.gov.br"],
)
def test_dominio_de_email_restrito(email: str) -> None:
    admin = h.criar_usuario("admin", "admin")
    with pytest.raises(psycopg.errors.CheckViolation):
        h.novo_convite(admin, email)


@pytest.mark.parametrize("email", ["pessoa@agu.gov.br", "pessoa@sub.agu.gov.br", "pessoa.x+1@gmail.com"])
def test_dominio_de_email_aceito(email: str) -> None:
    admin = h.criar_usuario("admin", "admin")
    cid, _ = h.novo_convite(admin, email)
    # M2: e-mails fixos: revoga no fim para a suite poder rodar de novo (indice de convite
    # aberto por e-mail, migration 11) sem reset do banco.
    with h.conectar() as c:
        c.execute(
            "update public.convites set revogado_em = now(), revogado_por = %s where id = %s",
            (admin, cid),
        )


def test_hash_malformado_e_prazo_maximo() -> None:
    admin = h.criar_usuario("admin", "admin")
    with pytest.raises(psycopg.errors.CheckViolation):
        with h.conectar() as c:
            c.execute(
                "insert into public.convites (email, unidade, token_hash, criado_por) "
                "values (%s, 'UEA', 'token-em-claro', %s)",
                (h.email_unico(), admin),
            )
    with pytest.raises(psycopg.errors.CheckViolation):
        with h.conectar() as c:
            c.execute(
                "insert into public.convites (email, unidade, token_hash, criado_por, expira_em) "
                "values (%s, 'UEA', %s, %s, now() + interval '31 days')",
                (h.email_unico(), h.novo_token()[1], admin),
            )


def test_dados_do_convite_sao_imutaveis_e_sem_delete() -> None:
    admin = h.criar_usuario("admin", "admin")
    cid, _ = h.novo_convite(admin, h.email_unico())
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("update public.convites set role='admin' where id=%s", (cid,))
    assert h.sqlstate(exc) == "AG004"
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("delete from public.convites where id=%s", (cid,))
    assert h.sqlstate(exc) == "AG003"
