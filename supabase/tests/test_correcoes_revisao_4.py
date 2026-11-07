"""Testes da migration 13: ator na auditoria de unidade (perfis) e de veiculos (M5).

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.
A parte M2 (sem SELECT direto) esta em test_rls_idor.py e test_correcoes_revisao_2.py.

    pytest supabase/tests/test_correcoes_revisao_4.py -m db_local
"""

from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]


def _falha(sql: str, params: tuple, esperado: str) -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(sql, params)
    assert h.sqlstate(exc) == esperado


# --- unidade do perfil -----------------------------------------------------------------
def test_troca_de_unidade_grava_o_ator() -> None:
    admin = h.criar_usuario("adm", "admin")
    uid = h.criar_usuario("x")
    h.alterar_unidade(uid, admin, "PF")
    assert h.auditoria_de("perfis", uid)[-1] == ("perfil_unidade_alterada", admin, "UEA", "PF")


def test_troca_de_unidade_sem_ator_e_recusada() -> None:
    uid = h.criar_usuario("x")
    _falha("update public.perfis set unidade='PF' where id=%s", (uid,), "AG002")
    assert h.perfil_de(uid)[0] == "solicitante"
    with h.conectar() as c:
        assert c.execute("select unidade::text from public.perfis where id=%s", (uid,)).fetchone() == ("UEA",)


@pytest.mark.parametrize("papel", ["autorizador", "solicitante"])
def test_troca_de_unidade_por_nao_admin_e_recusada(papel: str) -> None:
    nao_admin = h.criar_usuario("na", papel)
    uid = h.criar_usuario("x")
    _falha(
        "update public.perfis set unidade='PF', alterado_por=%s where id=%s", (nao_admin, uid), "AG002"
    )


def test_troca_de_unidade_por_admin_inativo_e_recusada() -> None:
    ativo = h.criar_usuario("adm", "admin")
    outro = h.criar_usuario("adm2", "admin")
    h.alterar_perfil(outro, ativo, ativo=False)
    uid = h.criar_usuario("x")
    _falha("update public.perfis set unidade='PF', alterado_por=%s where id=%s", (outro, uid), "AG002")


def test_alterado_por_nao_persiste_em_perfis() -> None:
    admin = h.criar_usuario("adm", "admin")
    uid = h.criar_usuario("x")
    h.alterar_unidade(uid, admin, "CJU")
    with h.conectar() as c:
        assert c.execute("select alterado_por from public.perfis where id=%s", (uid,)).fetchone() == (None,)


def test_ator_da_unidade_nao_vaza_para_a_proxima_troca() -> None:
    admin = h.criar_usuario("adm", "admin")
    uid = h.criar_usuario("x")
    h.alterar_unidade(uid, admin, "PF")
    _falha("update public.perfis set unidade='PU' where id=%s", (uid,), "AG002")


def test_atualizar_so_o_nome_nao_exige_ator() -> None:
    uid = h.criar_usuario("x")
    with h.conectar() as c:
        c.execute("update public.perfis set nome='Outro Nome' where id=%s", (uid,))


def test_criacao_de_perfil_nao_exige_ator_nem_vira_troca_de_unidade() -> None:
    uid = h.criar_usuario("novo")  # INSERT de solicitante sem alterado_por
    assert [x[0] for x in h.auditoria_de("perfis", uid)] == ["perfil_criado"]


def test_unidade_e_papel_no_mesmo_update_geram_duas_linhas_com_ator() -> None:
    admin = h.criar_usuario("adm", "admin")
    uid = h.criar_usuario("x")
    with h.conectar() as c:
        c.execute(
            "update public.perfis set unidade='PF', role='autorizador', alterado_por=%s where id=%s",
            (admin, uid),
        )
    linhas = h.auditoria_de("perfis", uid)
    assert ("perfil_unidade_alterada", admin, "UEA", "PF") in linhas
    assert ("perfil_alterado", admin, "solicitante/ativo", "autorizador/ativo") in linhas


def test_admin_nao_troca_o_proprio_papel_junto_com_a_unidade() -> None:
    a = h.criar_usuario("adm", "admin")
    _falha(
        "update public.perfis set unidade='PF', role='solicitante', alterado_por=%s where id=%s",
        (a, a),
        "AG008",
    )


def test_consumir_convite_continua_funcionando() -> None:
    admin = h.criar_usuario("adm", "admin")
    email = h.email_unico()
    uid = h.criar_auth(email)
    _, th = h.novo_token()
    with h.conectar() as c:
        c.execute(
            "insert into public.convites (email, role, unidade, token_hash, criado_por) "
            "values (%s, 'solicitante', 'PF', %s, %s)",
            (email, th, admin),
        )
        c.execute("select public.consumir_convite(%s, %s, 'Convidado')", (th, uid))
    assert [x[0] for x in h.auditoria_de("perfis", uid)] == ["perfil_criado"]


# --- veiculos ---------------------------------------------------------------------------
def test_criacao_de_veiculo_grava_o_ator() -> None:
    admin = h.criar_usuario("adm", "admin")
    v = h.novo_veiculo(admin)
    assert h.auditoria_de("veiculos", v) == [("veiculo_criado", admin, None, None)]


def test_alteracao_de_veiculo_grava_o_ator() -> None:
    admin = h.criar_usuario("adm", "admin")
    outro = h.criar_usuario("adm2", "admin")
    v = h.novo_veiculo(admin)
    h.alterar_veiculo(v, outro, "ativo=false")
    assert h.auditoria_de("veiculos", v)[-1] == ("veiculo_alterado", outro, "true", "false")


def test_criacao_de_veiculo_sem_ator_e_recusada() -> None:
    vid = uuid.uuid4()
    _falha("insert into public.veiculos (id, descricao) values (%s, 'sem ator')", (vid,), "AG002")
    assert h.auditoria_de("veiculos", vid) == []


@pytest.mark.parametrize("papel", ["autorizador", "solicitante"])
def test_criacao_de_veiculo_por_nao_admin_e_recusada(papel: str) -> None:
    nao_admin = h.criar_usuario("na", papel)
    _falha(
        "insert into public.veiculos (descricao, alterado_por) values ('x', %s)", (nao_admin,), "AG002"
    )


def test_criacao_de_veiculo_com_ator_inexistente_e_recusada() -> None:
    _falha(
        "insert into public.veiculos (descricao, alterado_por) values ('x', %s)", (uuid.uuid4(),), "AG002"
    )


@pytest.mark.parametrize(
    "set_sql", ["descricao='novo'", "placa='BBB1B11'", "ativo=false"]
)
def test_alteracao_de_veiculo_sem_ator_e_recusada(set_sql: str) -> None:
    v = h.novo_veiculo()
    _falha(f"update public.veiculos set {set_sql} where id=%s", (v,), "AG002")
    assert [x[0] for x in h.auditoria_de("veiculos", v)] == ["veiculo_criado"]


def test_alterado_por_nao_persiste_em_veiculos() -> None:
    v = h.novo_veiculo()
    with h.conectar() as c:
        assert c.execute("select alterado_por from public.veiculos where id=%s", (v,)).fetchone() == (None,)


def test_ator_do_veiculo_nao_vaza_para_a_proxima_alteracao() -> None:
    admin = h.criar_usuario("adm", "admin")
    v = h.novo_veiculo(admin)
    h.alterar_veiculo(v, admin, "descricao='um'")
    _falha("update public.veiculos set descricao='dois' where id=%s", (v,), "AG002")


def test_update_sem_mudar_campos_auditaveis_nao_exige_ator() -> None:
    v = h.novo_veiculo()
    with h.conectar() as c:
        c.execute("update public.veiculos set descricao = descricao where id=%s", (v,))
    assert [x[0] for x in h.auditoria_de("veiculos", v)] == ["veiculo_criado"]


def test_service_role_cria_veiculo_com_admin() -> None:
    admin = h.criar_usuario("adm", "admin")
    vid = uuid.uuid4()
    with h.como("service_role") as c:
        c.execute(
            "insert into public.veiculos (id, descricao, alterado_por) values (%s, 'sr', %s)", (vid, admin)
        )
        linha = c.execute(
            "select ator_id from public.auditoria where tabela='veiculos' and registro_id=%s", (vid,)
        ).fetchone()
    assert linha == (admin,)
