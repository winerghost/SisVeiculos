"""Testes da migration 20261115120400 (exclusao fisica de saudacoes SOMENTE rejeitadas).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py; sem as variaveis os testes sao pulados).

    pytest supabase/tests/test_saudacoes_exclusao.py -m db_local -p no:xdist
"""

from __future__ import annotations

import json
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]


def _admin() -> uuid.UUID:
    return h.admin_ativo_qualquer() or h.criar_usuario("admin-sau-ex", "admin")


def _criar(status: str = "pendente") -> uuid.UUID:
    adm = _admin()
    sid = uuid.uuid4()
    with h.conectar() as c:
        c.execute(
            "insert into public.saudacoes (id, ocasiao, texto, origem, criado_por) "
            "values (%s, 'manha', %s, 'manual', %s)",
            (sid, f"Bom dia {uuid.uuid4().hex[:8]}", adm),
        )
        if status != "pendente":
            c.execute(
                "update public.saudacoes set status = %s, decidido_por = %s where id = %s",
                (status, adm, sid),
            )
    return sid


def _existe(sid: uuid.UUID) -> bool:
    with h.conectar() as c:
        return c.execute("select 1 from public.saudacoes where id = %s", (sid,)).fetchone() is not None


def _excluir(sid: uuid.UUID, por: uuid.UUID | None, papel: str = "service_role") -> bool | None:
    with h.como(papel) as c:
        row = c.execute("select public.excluir_saudacao(%s, %s)", (sid, por)).fetchone()
        return row[0] if row else None


def test_service_role_tem_delete_sem_truncate_e_anon_authenticated_nao() -> None:
    with h.conectar() as c:
        def priv(papel: str, p: str) -> bool:
            return c.execute("select has_table_privilege(%s, 'public.saudacoes', %s)", (papel, p)).fetchone()[0]  # type: ignore[index]

        assert priv("service_role", "delete") is True
        assert priv("service_role", "truncate") is False
        for papel in ("anon", "authenticated"):
            assert priv(papel, "delete") is False
            assert priv(papel, "select") is False


@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_anon_e_authenticated_nao_deletam_nem_executam_rpc(papel: str) -> None:
    uid = h.criar_usuario("sol-sau-ex") if papel == "authenticated" else None
    sid = _criar("rejeitada")
    for sql, params in (
        ("delete from public.saudacoes where id = %s", (sid,)),
        ("select public.excluir_saudacao(%s, %s)", (sid, _admin())),
    ):
        with pytest.raises(psycopg.Error) as exc:
            with h.como(papel, uid) as c:
                c.execute(sql, params)  # type: ignore[arg-type]
        assert h.sqlstate(exc) == "42501", sql
    assert _existe(sid)


def test_rejeitada_e_excluida_pelo_rpc_com_auditoria() -> None:
    adm = _admin()
    sid = _criar("rejeitada")
    # `como` termina em rollback; aqui (conexao do dono, autocommit) o commit permite conferir a auditoria.
    with h.conectar() as c:
        (ok,) = c.execute("select public.excluir_saudacao(%s, %s)", (sid, adm)).fetchone() or (None,)
    assert ok is True
    assert not _existe(sid)
    with h.conectar() as c:
        linhas = c.execute(
            "select tabela, acao, ator_id, de, para, detalhes from public.auditoria "
            "where registro_id = %s order by id",
            (sid,),
        ).fetchall()
    # auditoria preservada: criada + rejeitada (anteriores) + saudacao_excluida
    assert [l[1] for l in linhas] == ["configuracao_alterada", "configuracao_alterada", "saudacao_excluida"]
    tabela, acao, ator, de, para, det = linhas[-1]
    assert (tabela, acao, ator, de, para) == ("configuracoes", "saudacao_excluida", adm, "rejeitada", None)
    assert set(det) == {"recurso", "evento", "ocasiao", "origem"}
    assert det["evento"] == "excluida"
    assert "Bom dia" not in json.dumps(det)


def test_service_role_executa_rpc() -> None:
    sid = _criar("rejeitada")
    # `como` faz rollback ao final: a linha continua existindo, mas o RPC retornou true.
    assert _excluir(sid, _admin()) is True
    assert _existe(sid)


def test_rpc_id_inexistente_devolve_false() -> None:
    assert _excluir(uuid.uuid4(), _admin()) is False


@pytest.mark.parametrize("status", ["pendente", "aprovada"])
def test_pendente_e_aprovada_nao_podem_ser_excluidas(status: str) -> None:
    sid = _criar(status)
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("select public.excluir_saudacao(%s, %s)", (sid, _admin()))
    assert h.sqlstate(exc) == "AG003"
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("delete from public.saudacoes where id = %s", (sid,))
    assert h.sqlstate(exc) == "AG003"
    assert _existe(sid)


def test_delete_direto_de_rejeitada_sem_ator_e_barrado() -> None:
    sid = _criar("rejeitada")
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("delete from public.saudacoes where id = %s", (sid,))
    assert h.sqlstate(exc) == "AG002"
    assert _existe(sid)


def test_rpc_exige_admin_ativo() -> None:
    sid = _criar("rejeitada")
    aut = h.criar_usuario("aut-sau-ex", "autorizador")
    inativo = h.criar_usuario("admin-sau-ex-inativo", "admin", ativo=False)
    for quem in (aut, inativo, None, uuid.uuid4()):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute("select public.excluir_saudacao(%s, %s)", (sid, quem))
        assert h.sqlstate(exc) == "AG002", quem
    assert _existe(sid)


def test_truncate_continua_bloqueado_e_auditoria_continua_append_only() -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("truncate public.saudacoes")
    assert h.sqlstate(exc) == "AG003"
    sid = _criar("rejeitada")
    with h.conectar() as c:
        c.execute("select public.excluir_saudacao(%s, %s)", (sid, _admin()))
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("delete from public.auditoria where registro_id = %s", (sid,))
    assert h.sqlstate(exc) == "AG003"
    with h.conectar() as c:
        (n,) = c.execute("select count(*) from public.auditoria where registro_id = %s", (sid,)).fetchone() or (0,)
    assert n == 3
