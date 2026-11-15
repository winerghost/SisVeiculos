"""Testes das migrations 20261128120000 (perfis.telefone), 20261128120100 (convites.telefone) e
20261128120200 (expurgo do telefone em convites encerrados + telefone_informado na auditoria).

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.

    pytest supabase/tests/test_telefone.py -m db_local
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
TEL_13 = "5561999998888"
TEL_12 = "556133334444"


def _set_telefone(alvo: uuid.UUID, por: uuid.UUID | None, telefone: str | None) -> None:
    """UPDATE como o backend faz: telefone + alterado_por no MESMO comando."""
    with h.conectar() as c:
        c.execute(
            "update public.perfis set telefone = %s, alterado_por = %s where id = %s",
            (telefone, por, alvo),
        )


def _telefone_de(pid: uuid.UUID) -> str | None:
    with h.conectar() as c:
        row = c.execute("select telefone from public.perfis where id = %s", (pid,)).fetchone()
    assert row is not None
    return row[0]


def _auditoria_telefone(pid: uuid.UUID) -> list[tuple]:
    with h.conectar() as c:
        return c.execute(
            "select ator_id, de, para, detalhes from public.auditoria "
            "where tabela = 'perfis' and registro_id = %s and acao = 'perfil_telefone_alterado' order by id",
            (pid,),
        ).fetchall()


def _auditoria_cita(valor: str) -> bool:
    """Alguma linha de auditoria contem o valor (em qualquer coluna textual/jsonb)?"""
    with h.conectar() as c:
        row = c.execute(
            "select exists (select 1 from public.auditoria a where a::text like %s)",
            (f"%{valor}%",),
        ).fetchone()
    return bool(row and row[0])


def _novo_convite_tel(
    admin: uuid.UUID, email: str, telefone: str | None, role: str = "solicitante"
) -> tuple[uuid.UUID, str]:
    _, th = h.novo_token()
    cid = uuid.uuid4()
    with h.conectar() as c:
        c.execute(
            "insert into public.convites (id, email, role, unidade, telefone, token_hash, criado_por) "
            "values (%s, %s, %s::public.role_usuario, 'UEA', %s, %s, %s)",
            (cid, email, role, telefone, th, admin),
        )
    return cid, th


def _telefone_convite(cid: uuid.UUID) -> str | None:
    with h.conectar() as c:
        row = c.execute("select telefone from public.convites where id = %s", (cid,)).fetchone()
    assert row is not None
    return row[0]


def _detalhes_convite_criado(cid: uuid.UUID) -> dict:
    with h.conectar() as c:
        row = c.execute(
            "select detalhes from public.auditoria "
            "where tabela = 'convites' and registro_id = %s and acao = 'convite_criado'",
            (cid,),
        ).fetchone()
    assert row is not None
    return row[0]


ACOES_AUDITORIA_33 = (
    "criada", "transicao", "convite_criado", "convite_usado", "convite_revogado",
    "perfil_criado", "perfil_alterado", "perfil_unidade_alterada", "admin_semeado",
    "configuracao_alterada", "veiculo_criado", "veiculo_alterado", "acao_administrativa", "acesso_negado",
    "checklist_registrado", "foto_perfil_alterada", "foto_perfil_removida", "relatorio_gerado",
    "relatorio_enviado", "motorista_designado", "localizacao_registrada", "localizacao_lida",
    "localizacao_expurgada", "saudacao_excluida", "passkey_cadastrada", "passkey_revogada",
    "passkey_autenticada", "passkey_falha", "passkey_regressao_contador", "passkey_desafio_invalido",
    "dados_exportados", "dados_importados", "perfil_telefone_alterado",
)  # fmt: skip


def _acoes_ck_auditoria(c: psycopg.Connection) -> set[str]:
    (d,) = c.execute(
        "select pg_get_constraintdef(oid) from pg_constraint "
        "where conrelid = 'public.auditoria'::regclass and conname = 'ck_auditoria_acao'"
    ).fetchone()
    return set(re.findall(r"'([a-z_]+)'::text", d))


# --- formato ------------------------------------------------------------------
@pytest.mark.parametrize("tel", [TEL_13, TEL_12, None])
def test_formatos_aceitos(tel: str | None) -> None:
    admin = h.criar_usuario("admin-tel", "admin")
    alvo = h.criar_usuario("alvo-tel")
    _set_telefone(alvo, admin, TEL_13 if tel is None else None)  # garante mudanca real
    _set_telefone(alvo, admin, tel)
    assert _telefone_de(alvo) == tel


@pytest.mark.parametrize(
    "tel",
    [
        "",  # vazio: o backend converte em NULL
        "61999998888",  # sem DDI
        "5501999998888",  # DDD com zero
        "5561899998888",  # 13 digitos sem o 9 na posicao do celular
        "+5561999998888",  # com '+'
        "55 61 99999-8888",  # com espacos/hifen
        "55619999988889",  # 14 digitos
        "55619999988",  # curto
        "5461999998888",  # outro DDI
        "556199999888a",
    ],
)
def test_formatos_recusados(tel: str) -> None:
    admin = h.criar_usuario("admin-tel", "admin")
    alvo = h.criar_usuario("alvo-tel")
    with pytest.raises(psycopg.errors.CheckViolation):
        _set_telefone(alvo, admin, tel)
    assert _telefone_de(alvo) is None


# --- so admin ativo altera -----------------------------------------------------
def test_alterar_telefone_exige_admin_ativo() -> None:
    alvo = h.criar_usuario("alvo-tel")
    autorizador = h.criar_usuario("aut-tel", "autorizador")
    admin_inativo = h.criar_usuario("admin-inat-tel", "admin", ativo=False)
    for ator in (None, alvo, autorizador, admin_inativo):
        with pytest.raises(psycopg.Error) as exc:
            _set_telefone(alvo, ator, TEL_13)
        assert h.sqlstate(exc) == "AG002", ator
    assert _telefone_de(alvo) is None
    assert _auditoria_telefone(alvo) == []


def test_admin_pode_alterar_o_proprio_telefone() -> None:
    admin = h.criar_usuario("admin-tel", "admin")
    _set_telefone(admin, admin, TEL_13)
    assert _telefone_de(admin) == TEL_13
    assert [a for a, *_ in _auditoria_telefone(admin)] == [admin]


def test_api_nao_le_nem_escreve_telefone() -> None:
    alvo = h.criar_usuario("alvo-tel")
    for papel in ("anon", "authenticated"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), h.como(papel, alvo) as c:
            c.execute("select telefone from public.perfis where id = %s", (alvo,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege), h.como(papel, alvo) as c:
            c.execute("update public.perfis set telefone = %s where id = %s", (TEL_13, alvo))


# --- auditoria sem o valor ------------------------------------------------------
def test_auditoria_registra_so_o_fato_sem_o_valor() -> None:
    admin = h.criar_usuario("admin-tel", "admin")
    alvo = h.criar_usuario("alvo-tel")
    tel_unico = "55619" + str(uuid.uuid4().int)[:8]  # 13 digitos, unico nesta execucao

    _set_telefone(alvo, admin, tel_unico)  # cadastra
    _set_telefone(alvo, admin, TEL_12)  # troca
    _set_telefone(alvo, admin, None)  # remove

    linhas = _auditoria_telefone(alvo)
    assert len(linhas) == 3
    for ator, de, para, detalhes in linhas:
        assert ator == admin
        assert de is None and para is None
        assert detalhes == {"telefone_alterado": True}
    assert not _auditoria_cita(tel_unico)
    assert not _auditoria_cita(TEL_12)


def test_sem_mudanca_de_telefone_nao_audita() -> None:
    admin = h.criar_usuario("admin-tel", "admin")
    alvo = h.criar_usuario("alvo-tel")
    _set_telefone(alvo, admin, TEL_13)
    # Mesmo valor (com ou sem ator) e troca so do nome: nao e acao administrativa de telefone.
    _set_telefone(alvo, admin, TEL_13)
    _set_telefone(alvo, None, TEL_13)
    with h.conectar() as c:
        c.execute("update public.perfis set nome = 'Outro nome' where id = %s", (alvo,))
    assert len(_auditoria_telefone(alvo)) == 1


def test_telefone_nao_dispara_aviso_de_perfil_alterado() -> None:
    admin = h.criar_usuario("admin-tel", "admin")
    alvo = h.criar_usuario("alvo-tel")
    _set_telefone(alvo, admin, TEL_13)
    acoes = [a for a, *_ in h.auditoria_de("perfis", alvo)]
    assert "perfil_alterado" not in acoes
    assert acoes.count("perfil_telefone_alterado") == 1


def test_guarda_segue_valendo_para_papel() -> None:
    """A reescrita do guarda preserva AG008 (auto-rebaixamento) mesmo junto com o telefone."""
    admin = h.criar_usuario("admin-tel", "admin")
    with pytest.raises(psycopg.Error) as exc, h.conectar() as c:
        c.execute(
            "update public.perfis set role = 'solicitante', telefone = %s, alterado_por = %s where id = %s",
            (TEL_13, admin, admin),
        )
    assert h.sqlstate(exc) == "AG008"
    assert _telefone_de(admin) is None


# --- convites -------------------------------------------------------------------
def test_convite_leva_telefone_ao_perfil() -> None:
    admin = h.criar_usuario("admin-tel", "admin")
    email = h.email_unico()
    cid, th = _novo_convite_tel(admin, email, TEL_13)
    uid = h.criar_auth(email)
    with h.conectar() as c:
        c.execute("select public.consumir_convite(%s, %s, 'Convidado')", (th, uid))
    assert _telefone_de(uid) == TEL_13
    # Criacao nao gera 'perfil_telefone_alterado' e a auditoria do convite nao cita o numero.
    assert _auditoria_telefone(uid) == []
    assert [a for a, *_ in h.auditoria_de("convites", cid)] == ["convite_criado", "convite_usado"]


def test_convite_sem_telefone_cria_perfil_sem_telefone() -> None:
    admin = h.criar_usuario("admin-tel", "admin")
    email = h.email_unico()
    _, th = _novo_convite_tel(admin, email, None)
    uid = h.criar_auth(email)
    with h.conectar() as c:
        c.execute("select public.consumir_convite(%s, %s, 'Convidado')", (th, uid))
    assert _telefone_de(uid) is None


@pytest.mark.parametrize(
    "tel",
    [
        "",  # vazio: o backend converte em NULL
        "61999998888",  # sem DDI
        "+5561999998888",  # com '+'
        "55619999988889",  # 14 digitos
    ],
)
def test_convite_telefone_formato_recusado(tel: str) -> None:
    admin = h.criar_usuario("admin-tel", "admin")
    with pytest.raises(psycopg.errors.CheckViolation):
        _novo_convite_tel(admin, h.email_unico(), tel)


def test_convite_telefone_imutavel_exceto_para_null() -> None:
    admin = h.criar_usuario("admin-tel", "admin")
    cid, _ = _novo_convite_tel(admin, h.email_unico(), TEL_13)
    with pytest.raises(psycopg.Error) as exc, h.conectar() as c:
        c.execute("update public.convites set telefone = %s where id = %s", (TEL_12, cid))
    assert h.sqlstate(exc) == "AG004"
    # Unica transicao aceita: telefone -> NULL (LGPD: eliminacao). E sem volta.
    with h.conectar() as c:
        c.execute("update public.convites set telefone = null where id = %s", (cid,))
    assert _telefone_convite(cid) is None
    with pytest.raises(psycopg.Error) as exc, h.conectar() as c:
        c.execute("update public.convites set telefone = %s where id = %s", (TEL_13, cid))
    assert h.sqlstate(exc) == "AG004"


def test_convite_encerrado_so_aceita_telefone_para_null() -> None:
    """Convite encerrado segue congelado (AG005/AG004), mas o expurgo do telefone passa."""
    admin = h.criar_usuario("admin-tel", "admin")
    cid, _ = _novo_convite_tel(admin, h.email_unico(), TEL_13)
    with h.conectar() as c:
        c.execute(
            "update public.convites set revogado_em = now(), revogado_por = %s where id = %s",
            (admin, cid),
        )
        c.execute("update public.convites set telefone = null where id = %s", (cid,))
    with pytest.raises(psycopg.Error) as exc, h.conectar() as c:
        c.execute("update public.convites set telefone = %s where id = %s", (TEL_13, cid))
    assert h.sqlstate(exc) == "AG004"
    with pytest.raises(psycopg.Error) as exc, h.conectar() as c:
        c.execute(
            "update public.convites set telefone = null, revogado_em = null, revogado_por = null "
            "where id = %s",
            (cid,),
        )
    assert h.sqlstate(exc) == "AG005"


def test_aceite_zera_telefone_do_convite() -> None:
    admin = h.criar_usuario("admin-tel", "admin")
    email = h.email_unico()
    cid, th = _novo_convite_tel(admin, email, TEL_13)
    uid = h.criar_auth(email)
    with h.conectar() as c:
        c.execute("select public.consumir_convite(%s, %s, 'Convidado')", (th, uid))
    assert _telefone_de(uid) == TEL_13
    assert _telefone_convite(cid) is None


def test_convite_admin_com_telefone() -> None:
    """Ramo alterado_por = criado_por de consumir_convite: perfil admin nasce com o telefone."""
    admin = h.criar_usuario("admin-tel", "admin")
    email = h.email_unico()
    cid, th = _novo_convite_tel(admin, email, TEL_12, role="admin")
    uid = h.criar_auth(email)
    with h.conectar() as c:
        c.execute("select public.consumir_convite(%s, %s, 'Novo admin')", (th, uid))
    assert h.perfil_de(uid) == ("admin", True)
    assert _telefone_de(uid) == TEL_12
    assert _telefone_convite(cid) is None
    assert _auditoria_telefone(uid) == []


def test_revogacao_zera_telefone() -> None:
    """Revogacao direta (como o backend faz): o gatilho zera o numero sem o chamador pedir."""
    admin = h.criar_usuario("admin-tel", "admin")
    cid, _ = _novo_convite_tel(admin, h.email_unico(), TEL_13)
    with h.conectar() as c:
        c.execute(
            "update public.convites set revogado_em = now(), revogado_por = %s where id = %s",
            (admin, cid),
        )
    assert _telefone_convite(cid) is None
    assert [a for a, *_ in h.auditoria_de("convites", cid)] == ["convite_criado", "convite_revogado"]


def test_reenviar_convite_herda_telefone_e_antigo_fica_sem() -> None:
    admin = h.criar_usuario("admin-tel", "admin")
    cid, _ = _novo_convite_tel(admin, h.email_unico(), TEL_13)
    _, novo_hash = h.novo_token()
    with h.conectar() as c:
        (ret,) = c.execute(
            "select public.reenviar_convite(%s, %s, %s, now() + interval '7 days')",
            (cid, admin, novo_hash),
        ).fetchone()
        (tel_novo,) = c.execute(
            "select telefone from public.convites where token_hash = %s", (novo_hash,)
        ).fetchone()
    assert ret["telefone"] == TEL_13
    assert "token_hash" not in ret
    assert tel_novo == TEL_13
    assert _telefone_convite(cid) is None  # convite antigo (revogado) sem o numero


def test_nenhum_convite_encerrado_guarda_telefone() -> None:
    admin = h.criar_usuario("admin-tel", "admin")
    cid, _ = _novo_convite_tel(admin, h.email_unico(), TEL_13)
    for _ in range(2):  # dois reenvios seguidos
        _, novo_hash = h.novo_token()
        with h.conectar() as c:
            (ret,) = c.execute(
                "select public.reenviar_convite(%s, %s, %s, now() + interval '7 days')",
                (cid, admin, novo_hash),
            ).fetchone()
        cid = uuid.UUID(ret["id"])
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.convites "
            "where telefone is not null and (usado_em is not null or revogado_em is not null)"
        ).fetchone()
    assert n == 0
    assert _telefone_convite(cid) == TEL_13  # so o convite aberto guarda o numero


# --- auditoria do convite: so o fato ---------------------------------------------
@pytest.mark.parametrize("tel, esperado", [(TEL_13, True), (None, False)])
def test_convite_criado_registra_telefone_informado(tel: str | None, esperado: bool) -> None:
    admin = h.criar_usuario("admin-tel", "admin")
    cid, _ = _novo_convite_tel(admin, h.email_unico(), tel)
    detalhes = _detalhes_convite_criado(cid)
    assert detalhes["telefone_informado"] is esperado
    assert set(detalhes) == {"role", "unidade", "expira_em", "telefone_informado"}
    if tel is not None:
        assert tel not in str(detalhes)


def test_reenvio_registra_telefone_informado_sem_o_valor() -> None:
    admin = h.criar_usuario("admin-tel", "admin")
    tel_unico = "55619" + str(uuid.uuid4().int)[:8]
    cid, _ = _novo_convite_tel(admin, h.email_unico(), tel_unico)
    _, novo_hash = h.novo_token()
    with h.conectar() as c:
        (ret,) = c.execute(
            "select public.reenviar_convite(%s, %s, %s, now() + interval '7 days')",
            (cid, admin, novo_hash),
        ).fetchone()
    assert _detalhes_convite_criado(uuid.UUID(ret["id"]))["telefone_informado"] is True
    assert not _auditoria_cita(tel_unico)


def test_ck_auditoria_acao_tem_as_33_acoes() -> None:
    with h.conectar() as c:
        acoes = _acoes_ck_auditoria(c)
    assert len(ACOES_AUDITORIA_33) == 33
    assert set(ACOES_AUDITORIA_33) <= acoes


# --- idempotencia ---------------------------------------------------------------
MIGRATIONS_TELEFONE = (
    "20261128120000_perfis_telefone.sql",
    "20261128120100_convites_telefone.sql",
    "20261128120200_convites_telefone_expurgo.sql",
)


def test_migrations_sao_idempotentes() -> None:
    sqls = [(MIGRATIONS / nome).read_text(encoding="utf-8") for nome in MIGRATIONS_TELEFONE]
    admin = h.criar_usuario("admin-tel", "admin")
    alvo = h.criar_usuario("alvo-tel")
    with h.reaplicando_migration() as c:
        for _ in range(2):
            for sql in sqls:
                c.execute(sql)
        (n,) = c.execute(
            "select count(*) from pg_constraint where conname in ('ck_perfis_telefone', 'ck_convites_telefone')"
        ).fetchone()
        assert n == 2
        # ck_auditoria_acao segue com as 33 acoes (32 anteriores + perfil_telefone_alterado).
        assert set(ACOES_AUDITORIA_33) <= _acoes_ck_auditoria(c)
        # O guarda ainda audita a troca de telefone (sem o valor).
        c.execute(
            "update public.perfis set telefone = %s, alterado_por = %s where id = %s",
            (TEL_13, admin, alvo),
        )
        (det,) = c.execute(
            "select detalhes from public.auditoria where tabela = 'perfis' and registro_id = %s "
            "and acao = 'perfil_telefone_alterado'",
            (alvo,),
        ).fetchone()
        assert det == {"telefone_alterado": True}
        # A versao final de convites_validar e a da 20261128120200 (telefone -> NULL aceito).
        (src,) = c.execute(
            "select prosrc from pg_proc where oid = 'public.convites_validar()'::regprocedure"
        ).fetchone()
        assert "new.telefone is not null" in src
