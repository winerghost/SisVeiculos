"""Testes das migrations 20261111120000/20261111120100: recurso opcional Perfil Motorista.

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.
Uma fixture autouse (_estado_recurso) le o estado inicial de motorista/motorista_registra, desliga
ambos antes de cada teste (registra primeiro, por AG010) e restaura o original no teardown.
Cada alternancia gera linha 'configuracao_alterada' em auditoria (append-only, nao limpavel): rodar
SO em banco descartavel. Rodar sem paralelismo (flag global de configuracoes).

    pytest supabase/tests/test_motorista.py -m db_local
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

NEGADO = "42501"


def _cfg(valores: dict[str, bool], por: uuid.UUID | None) -> None:
    sets = ", ".join(f"{k} = %s" for k in valores)
    with h.conectar() as c:
        c.execute(
            f"update public.configuracoes set {sets}, alterado_por = %s where id = true",
            (*valores.values(), por),
        )


def _ler() -> tuple[bool, bool]:
    with h.conectar() as c:
        row = c.execute("select motorista, motorista_registra from public.configuracoes").fetchone()
    assert row is not None
    return bool(row[0]), bool(row[1])


@pytest.fixture(autouse=True)
def _estado_recurso() -> Iterator[None]:
    """Isola o flag global: desliga antes (registra -> motorista) e restaura depois (motorista -> registra)."""
    orig_mot, orig_reg = _ler()
    por = h.admin_ativo_qualquer() or h.criar_usuario("admin-cfg", "admin")
    if orig_reg:
        _cfg({"motorista_registra": False}, por)
    if orig_mot:
        _cfg({"motorista": False}, por)
    try:
        yield
    finally:
        mot, reg = _ler()
        if reg:
            _cfg({"motorista_registra": False}, por)
        if mot != orig_mot:
            _cfg({"motorista": orig_mot}, por)
        if orig_reg:
            _cfg({"motorista_registra": True}, por)


@contextmanager
def recurso_ligado(admin: uuid.UUID) -> Iterator[None]:
    _cfg({"motorista": True}, admin)
    yield


class Cena:
    def __init__(self) -> None:
        self.admin = h.criar_usuario("admin", "admin")
        self.dono = h.criar_usuario("dono")
        self.autorizador = h.criar_usuario("autorizador", "autorizador")
        self.motorista = h.criar_usuario("motorista", "motorista")

    def solicitada(self) -> uuid.UUID:
        with h.conectar() as c:
            return h.inserir_requisicao(c, self.dono, h.novo_veiculo())

    def autorizada(self) -> uuid.UUID:
        rid = self.solicitada()
        h.autorizar(rid, self.autorizador)
        return rid

    def designar(self, rid: uuid.UUID, motorista: uuid.UUID | None, por: uuid.UUID | None) -> None:
        with h.conectar() as c:
            c.execute(
                "update public.requisicoes set motorista_id = %s, motorista_alterado_por = %s where id = %s",
                (motorista, por, rid),
            )


@pytest.fixture
def cena() -> Cena:
    return Cena()


def _motorista_de(rid: uuid.UUID) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    with h.conectar() as c:
        row = c.execute(
            "select motorista_id, motorista_alterado_por from public.requisicoes where id = %s", (rid,)
        ).fetchone()
    assert row is not None
    return row[0], row[1]


# --- role e flags ----------------------------------------------------------------------------------
def test_role_motorista_existe_e_perfil_pode_te_lo(cena: Cena) -> None:
    assert h.perfil_de(cena.motorista) == ("motorista", True)


def test_flags_nascem_desligadas_com_default_false() -> None:
    with h.conectar() as c:
        rows = c.execute(
            "select column_name, column_default, is_nullable from information_schema.columns "
            "where table_schema='public' and table_name='configuracoes' "
            "and column_name in ('motorista','motorista_registra')"
        ).fetchall()
    assert {r[0]: (r[1], r[2]) for r in rows} == {
        "motorista": ("false", "NO"),
        "motorista_registra": ("false", "NO"),
    }
    assert _ler() == (False, False)  # a fixture autouse desligou; o default e conferido acima


def test_registra_exige_motorista_ligado(cena: Cena) -> None:
    with pytest.raises(psycopg.Error) as exc:
        _cfg({"motorista_registra": True}, cena.admin)
    assert h.sqlstate(exc) == "AG010"
    with recurso_ligado(cena.admin):
        _cfg({"motorista_registra": True}, cena.admin)
        with pytest.raises(psycopg.Error) as exc2:  # nao da para desligar o pai com o filho ligado
            _cfg({"motorista": False}, cena.admin)
        assert h.sqlstate(exc2) == "AG010"
        _cfg({"motorista_registra": False}, cena.admin)


def test_check_do_banco_vale_sem_o_trigger(cena: Cena) -> None:
    """Defesa em profundidade: o CHECK existe e e validado."""
    with h.conectar() as c:
        row = c.execute(
            "select convalidated from pg_constraint where conname = "
            "'ck_configuracoes_motorista_registra_exige_motorista'"
        ).fetchone()
    assert row is not None and row[0] is True


def test_alteracao_das_flags_so_por_admin_ativo_e_e_auditada(cena: Cena) -> None:
    for quem in (cena.autorizador, None):
        with pytest.raises(psycopg.Error) as exc:
            _cfg({"motorista": True}, quem)
        assert h.sqlstate(exc) == "AG002"
    _cfg({"motorista": True}, cena.admin)
    with h.conectar() as c:
        rows = c.execute(
            "select de, para, detalhes ->> 'recurso' from public.auditoria "
            "where tabela='configuracoes' and acao='configuracao_alterada' and ator_id=%s "
            "and detalhes ->> 'recurso' = 'motorista' order by id",
            (cena.admin,),
        ).fetchall()
    assert ("false", "true", "motorista") in rows
    # alterado_por nunca persiste
    with h.conectar() as c:
        (alt,) = c.execute("select alterado_por from public.configuracoes").fetchone() or (None,)
    assert alt is None


# --- requisicoes.motorista_id ----------------------------------------------------------------------
def test_motorista_nunca_em_solicitada(cena: Cena) -> None:
    with recurso_ligado(cena.admin):
        rid = cena.solicitada()
        with pytest.raises(psycopg.Error) as exc:
            cena.designar(rid, cena.motorista, cena.autorizador)
        assert h.sqlstate(exc) == "AG032"
        assert _motorista_de(rid) == (None, None)


def test_insert_com_motorista_e_recusado(cena: Cena) -> None:
    with recurso_ligado(cena.admin):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(
                    """insert into public.requisicoes
                       (id, usuario_id, nome_solicitante, unidade, hora_saida, trajeto, veiculo_id, motorista_id)
                       values (%s, %s, 'T', 'UEA', now() + interval '1 day', 'A-B', %s, %s)""",
                    (uuid.uuid4(), cena.dono, h.novo_veiculo(), cena.motorista),
                )
        assert h.sqlstate(exc) == "AG032"


def test_check_do_banco_recusa_motorista_em_solicitada_sem_trigger(cena: Cena) -> None:
    """O CHECK existe independentemente do trigger (nome estavel para o backend traduzir)."""
    with h.conectar() as c:
        row = c.execute(
            "select pg_get_constraintdef(oid) from pg_constraint where conname = 'ck_requisicoes_motorista_status'"
        ).fetchone()
    assert row is not None
    assert "solicitada" not in row[0] and "negada" not in row[0] and "autorizada" in row[0]


def test_designar_em_autorizada_audita_e_zera_transporte(cena: Cena) -> None:
    with recurso_ligado(cena.admin):
        rid = cena.autorizada()
        cena.designar(rid, cena.motorista, cena.autorizador)
        assert _motorista_de(rid) == (cena.motorista, None)  # transporte zerado
        linhas = [r for r in h.auditoria_de("requisicoes", rid) if r[0] == "motorista_designado"]
        assert linhas == [("motorista_designado", cena.autorizador, None, str(cena.motorista))]
        with h.conectar() as c:
            (det,) = c.execute(
                "select detalhes from public.auditoria where tabela='requisicoes' and registro_id=%s "
                "and acao='motorista_designado'",
                (rid,),
            ).fetchone() or ({},)
        assert det == {"recurso": "motorista", "status": "autorizada"}
        assert "@" not in str(det)


def test_designar_junto_da_autorizacao(cena: Cena) -> None:
    with recurso_ligado(cena.admin):
        rid = cena.solicitada()
        with h.conectar() as c:
            c.execute(
                "update public.requisicoes set status='autorizada', autorizado_por=%s, "
                "motorista_id=%s, motorista_alterado_por=%s where id=%s",
                (cena.autorizador, cena.motorista, cena.autorizador, rid),
            )
        assert _motorista_de(rid)[0] == cena.motorista
        acoes = [r[0] for r in h.auditoria_de("requisicoes", rid)]
        assert "motorista_designado" in acoes and "transicao" in acoes


def test_motorista_precisa_ser_role_motorista_ativo_e_existente(cena: Cena) -> None:
    inativo = h.criar_usuario("mot-inativo", "motorista", ativo=False)
    with recurso_ligado(cena.admin):
        rid = cena.autorizada()
        for alvo in (cena.dono, cena.autorizador, cena.admin, inativo, uuid.uuid4()):
            with pytest.raises(psycopg.Error) as exc:
                cena.designar(rid, alvo, cena.autorizador)
            assert h.sqlstate(exc) == "AG031", alvo
        assert _motorista_de(rid) == (None, None)


def test_recurso_desligado_recusa_nova_designacao_mas_permite_remover(cena: Cena) -> None:
    rid = cena.autorizada()
    with pytest.raises(psycopg.Error) as exc:  # fixture deixou desligado
        cena.designar(rid, cena.motorista, cena.autorizador)
    assert h.sqlstate(exc) == "AG033"
    with recurso_ligado(cena.admin):
        cena.designar(rid, cena.motorista, cena.autorizador)
    _cfg({"motorista": False}, cena.admin)
    # Desligar nao apaga o dado gravado...
    assert _motorista_de(rid)[0] == cena.motorista
    # ...e remover continua possivel (so desfaz), com auditoria.
    cena.designar(rid, None, cena.autorizador)
    assert _motorista_de(rid)[0] is None
    ultimas = [r for r in h.auditoria_de("requisicoes", rid) if r[0] == "motorista_designado"]
    assert ultimas[-1][2:] == (str(cena.motorista), None)


def test_ator_da_designacao_precisa_ser_autorizador_ou_admin_ativo(cena: Cena) -> None:
    inativo = h.criar_usuario("aut-inativo", "autorizador", ativo=False)
    with recurso_ligado(cena.admin):
        rid = cena.autorizada()
        for quem in (None, cena.dono, cena.motorista, inativo, uuid.uuid4()):
            with pytest.raises(psycopg.Error) as exc:
                cena.designar(rid, cena.motorista, quem)
            assert h.sqlstate(exc) == "AG002", quem
        cena.designar(rid, cena.motorista, cena.admin)  # admin pode


def test_responsavel_sem_mudanca_de_motorista_e_recusado(cena: Cena) -> None:
    with recurso_ligado(cena.admin):
        rid = cena.autorizada()
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(
                    "update public.requisicoes set motorista_alterado_por=%s where id=%s",
                    (cena.autorizador, rid),
                )
        assert h.sqlstate(exc) == "AG004"


def test_trocar_em_uso_e_auditado_e_encerrada_fica_congelada(cena: Cena) -> None:
    outro = h.criar_usuario("motorista2", "motorista")
    with recurso_ligado(cena.admin):
        rid = cena.autorizada()
        cena.designar(rid, cena.motorista, cena.autorizador)
        h.iniciar(rid, cena.autorizador)
        cena.designar(rid, outro, cena.autorizador)
        assert _motorista_de(rid)[0] == outro
        h.concluir(rid, cena.autorizador)
        with pytest.raises(psycopg.Error) as exc:
            cena.designar(rid, cena.motorista, cena.autorizador)
        assert h.sqlstate(exc) == "AG001"  # requisicao encerrada
        linhas = [r for r in h.auditoria_de("requisicoes", rid) if r[0] == "motorista_designado"]
        assert [(r[2], r[3]) for r in linhas] == [
            (None, str(cena.motorista)),
            (str(cena.motorista), str(outro)),
        ]


def test_negada_nao_aceita_motorista(cena: Cena) -> None:
    with recurso_ligado(cena.admin):
        rid = cena.solicitada()
        h.negar(rid, cena.autorizador)
        with pytest.raises(psycopg.Error):
            cena.designar(rid, cena.motorista, cena.autorizador)


def test_cancelada_mantem_o_motorista_ja_designado(cena: Cena) -> None:
    with recurso_ligado(cena.admin):
        rid = cena.autorizada()
        cena.designar(rid, cena.motorista, cena.autorizador)
        h.cancelar(rid, cena.autorizador)
        assert _motorista_de(rid)[0] == cena.motorista


def test_motorista_nao_autoriza_e_so_registra_com_motorista_registra(cena: Cena) -> None:
    """Atualizado pela migration 20261112120200: o motorista designado registra saida/chegada, mas so com
    motorista_registra ligado (AG034 antes disso); autorizar continua proibido (AG002)."""
    with recurso_ligado(cena.admin):
        rid = cena.solicitada()
        with pytest.raises(psycopg.Error) as exc:
            h.autorizar(rid, cena.motorista)
        assert h.sqlstate(exc) == "AG002"
        rid2 = cena.autorizada()
        cena.designar(rid2, cena.motorista, cena.autorizador)
        with pytest.raises(psycopg.Error) as exc2:
            h.iniciar(rid2, cena.motorista)
        assert h.sqlstate(exc2) == "AG034"
        _cfg({"motorista_registra": True}, cena.admin)
        h.iniciar(rid2, cena.motorista)
        assert h.status_de(rid2) == "em_uso"


# --- RLS / grants / append-only --------------------------------------------------------------------
def test_api_nao_acessa_as_colunas_novas_e_sem_delete(cena: Cena) -> None:
    rid = cena.solicitada()
    for papel, uid in (("anon", None), ("authenticated", cena.dono)):
        with pytest.raises(psycopg.Error) as exc:
            with h.como(papel, uid) as c:
                c.execute("select motorista_id from public.requisicoes where id = %s", (rid,))
        assert h.sqlstate(exc) == NEGADO
    with pytest.raises(psycopg.Error) as exc2:
        with h.como("service_role") as c:
            c.execute("delete from public.requisicoes where id = %s", (rid,))
    assert h.sqlstate(exc2) == NEGADO


def test_funcao_do_trigger_nao_e_executavel_pela_api() -> None:
    with h.conectar() as c:
        (ok,) = c.execute(
            "select not (has_function_privilege('anon', p, 'execute') "
            "or has_function_privilege('authenticated', p, 'execute') "
            "or has_function_privilege('service_role', p, 'execute')) "
            "from to_regprocedure('public.requisicoes_validar_motorista()') p"
        ).fetchone() or (False,)
    assert ok


def test_auditoria_continua_append_only(cena: Cena) -> None:
    with recurso_ligado(cena.admin):
        rid = cena.autorizada()
        cena.designar(rid, cena.motorista, cena.autorizador)
    with pytest.raises(psycopg.Error):
        with h.conectar() as c:
            c.execute("delete from public.auditoria where acao = 'motorista_designado' and registro_id = %s", (rid,))
    with pytest.raises(psycopg.Error):
        with h.conectar() as c:
            c.execute("update public.auditoria set para = null where acao = 'motorista_designado' and registro_id = %s", (rid,))
