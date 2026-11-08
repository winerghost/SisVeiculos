"""Testes da migration 20261113120000 (notificacoes do motorista: somente sino).

Somente banco local/dev DESCARTAVEL (TEST_DATABASE_URL). Ver dbhelpers.py. Os testes gravam e nao
apagam (auditoria e notificacoes sao imutaveis): cada teste cria os proprios usuarios/requisicoes.
Uma fixture autouse isola as flags globais (desliga antes de cada teste e restaura no fim); cada
alternancia gera linha 'configuracao_alterada' em auditoria: rodar SO em banco descartavel e sem
paralelismo.

    pytest supabase/tests/test_notificacoes_motorista.py -m db_local -p no:xdist
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

NEGADO = "42501"
TIPOS_MOTORISTA = ("motorista_designado", "motorista_removido", "viagem_cancelada_motorista")
# Ordem de desligamento (dependentes primeiro); o religamento usa a ordem inversa.
FLAGS = (
    "motorista_localizacao",
    "motorista_registra",
    "motorista",
    "notificacao_email_criticos",
)


def _estado() -> dict[str, bool]:
    with h.conectar() as c:
        row = c.execute(f"select {', '.join(FLAGS)} from public.configuracoes").fetchone()
    assert row is not None
    return dict(zip(FLAGS, (bool(v) for v in row), strict=True))


def _cfg(flag: str, valor: bool, por: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute(
            f"update public.configuracoes set {flag} = %s, alterado_por = %s where id = true",
            (valor, por),
        )


@pytest.fixture(autouse=True)
def _estado_recursos() -> Iterator[None]:
    orig = _estado()
    por = h.admin_ativo_qualquer() or h.criar_usuario("nm-admin-cfg", "admin")
    for f in FLAGS:
        if _estado()[f]:
            _cfg(f, False, por)
    try:
        yield
    finally:
        for f in FLAGS:
            if _estado()[f]:
                _cfg(f, False, por)
        for f in reversed(FLAGS):
            if orig[f]:
                _cfg(f, True, por)


class Cena:
    def __init__(self) -> None:
        self.admin = h.criar_usuario("nm-admin", "admin")
        self.dono = h.criar_usuario("nm-dono")
        self.aut = h.criar_usuario("nm-aut", "autorizador")
        self.mot = h.criar_usuario("nm-mot", "motorista")
        self.mot2 = h.criar_usuario("nm-mot2", "motorista")

    def ligar(self) -> None:
        _cfg("motorista", True, self.admin)

    def desligar(self) -> None:
        for f in ("motorista_localizacao", "motorista_registra", "motorista"):
            if _estado()[f]:
                _cfg(f, False, self.admin)

    def autorizada(self, motorista: uuid.UUID | None = None) -> uuid.UUID:
        """Requisicao autorizada; com `motorista`, ja designado (exige o recurso ligado)."""
        with h.conectar() as c:
            rid = h.inserir_requisicao(c, self.dono, h.novo_veiculo())
        h.autorizar(rid, self.aut)
        if motorista is not None:
            self.designar(rid, motorista)
        return rid

    def designar(self, rid: uuid.UUID, motorista: uuid.UUID | None, por: uuid.UUID | None = None) -> None:
        with h.conectar() as c:
            c.execute(
                "update public.requisicoes set motorista_id = %s, motorista_alterado_por = %s where id = %s",
                (motorista, por or self.aut, rid),
            )


@pytest.fixture
def cena() -> Cena:
    return Cena()


def _notifs(rid: uuid.UUID, tipo: str | None = None) -> list[tuple]:
    """[(usuario_id, tipo, critica, email_status, requisicao_id, referencia_id)] da requisicao."""
    with h.conectar() as c:
        return c.execute(
            "select usuario_id, tipo, critica, email_status, requisicao_id, referencia_id "
            "from public.notificacoes where requisicao_id = %s and (%s::text is null or tipo = %s) "
            "order by criada_em, id",
            (rid, tipo, tipo),
        ).fetchall()


def _dest(rid: uuid.UUID, tipo: str) -> list[uuid.UUID]:
    return [r[0] for r in _notifs(rid, tipo)]


# --- designacao ------------------------------------------------------------------------------------
def test_designado_recebe_aviso_somente_sino(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    linhas = _notifs(rid, "motorista_designado")
    assert [r[0] for r in linhas] == [cena.mot]
    _, _, critica, email_status, req, ref = linhas[0]
    assert critica is False and email_status is None  # nunca entra na fila de e-mail
    assert req == rid and ref is not None  # requisicao_id obrigatorio; referencia deterministica


def test_nao_vaza_para_outro_motorista_nem_solicitante_nem_ator(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    for outro in (cena.mot2, cena.dono, cena.aut, cena.admin):
        assert not [r for r in _notifs(rid) if r[0] == outro and r[1] in TIPOS_MOTORISTA], outro


def test_requisicao_nova_sem_designacao_nao_avisa_motorista(cena: Cena) -> None:
    cena.ligar()
    with h.conectar() as c:
        rid = h.inserir_requisicao(c, cena.dono, h.novo_veiculo())
    h.autorizar(rid, cena.aut)
    h.cancelar(rid, cena.aut)  # sem motorista designado: nada para motorista
    assert [r for r in _notifs(rid) if r[1] in TIPOS_MOTORISTA] == []
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.notificacoes where usuario_id in (%s, %s) and requisicao_id = %s",
            (cena.mot, cena.mot2, rid),
        ).fetchone() or (0,)
    assert n == 0


def test_troca_avisa_o_novo_designado_e_o_antigo_removido(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    cena.designar(rid, cena.mot2)
    assert _dest(rid, "motorista_designado") == [cena.mot, cena.mot2]
    assert _dest(rid, "motorista_removido") == [cena.mot]
    # o novo nao recebe "removido"; o antigo nao recebe um segundo "designado"
    assert cena.mot2 not in _dest(rid, "motorista_removido")
    assert _dest(rid, "motorista_designado").count(cena.mot) == 1


def test_remocao_avisa_so_o_removido_e_redesignar_gera_novo_aviso(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    cena.designar(rid, None)
    assert _dest(rid, "motorista_removido") == [cena.mot]
    assert _dest(rid, "motorista_designado") == [cena.mot]  # remocao nao cria "designado"
    cena.designar(rid, cena.mot)  # referencia vem do id da auditoria: evento novo, aviso novo
    assert _dest(rid, "motorista_designado") == [cena.mot, cena.mot]


def test_motorista_inativo_nao_recebe(cena: Cena) -> None:
    cena.ligar()
    inativo = h.criar_usuario("nm-mot-off", "motorista")
    rid = cena.autorizada(inativo)
    h.alterar_perfil(inativo, cena.admin, ativo=False)
    h.cancelar(rid, cena.aut)
    assert _notifs(rid, "viagem_cancelada_motorista") == []
    assert _dest(rid, "requisicao_cancelada_terceiros") == [cena.dono]  # o fluxo principal segue


# --- cancelamento ----------------------------------------------------------------------------------
def test_cancelamento_avisa_o_motorista_designado_e_so_ele(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    h.cancelar(rid, cena.aut)
    linhas = _notifs(rid, "viagem_cancelada_motorista")
    assert [r[0] for r in linhas] == [cena.mot]
    assert linhas[0][2] is False and linhas[0][3] is None
    assert _dest(rid, "requisicao_cancelada_terceiros") == [cena.dono]  # fluxo anterior preservado
    assert cena.mot2 not in {r[0] for r in _notifs(rid)}


def test_cancelamento_por_admin_tambem_avisa_e_ator_nunca_recebe(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    h.cancelar(rid, cena.admin)
    assert _dest(rid, "viagem_cancelada_motorista") == [cena.mot]
    assert cena.admin not in {r[0] for r in _notifs(rid) if r[1] in TIPOS_MOTORISTA}


def test_email_criticos_ligado_nao_enfileira_avisos_do_motorista(cena: Cena) -> None:
    cena.ligar()
    _cfg("notificacao_email_criticos", True, cena.admin)
    rid = cena.autorizada(cena.mot)
    cena.designar(rid, cena.mot2)
    h.cancelar(rid, cena.aut)
    linhas = [r for r in _notifs(rid) if r[1] in TIPOS_MOTORISTA]
    assert {r[1] for r in linhas} == set(TIPOS_MOTORISTA)
    assert all(r[2] is False and r[3] is None for r in linhas)  # nao critica, sem e-mail
    # controle: o aviso critico ao solicitante continua entrando na fila
    assert [r[3] for r in _notifs(rid, "requisicao_cancelada_terceiros")] == ["pendente"]


# --- recurso desligado -----------------------------------------------------------------------------
def test_recurso_desligado_nao_gera_aviso_de_cancelamento(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    cena.desligar()
    h.cancelar(rid, cena.aut)
    assert _notifs(rid, "viagem_cancelada_motorista") == []
    assert _dest(rid, "requisicao_cancelada_terceiros") == [cena.dono]  # o resto segue normal


def test_recurso_desligado_remocao_nao_avisa(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    cena.desligar()
    cena.designar(rid, None)  # remover e permitido com o recurso desligado (so desfaz)
    assert _notifs(rid, "motorista_removido") == []
    assert _dest(rid, "motorista_designado") == [cena.mot]  # so o aviso de quando estava ligado


def test_recurso_desligado_nao_permite_designar(cena: Cena) -> None:
    rid = cena.autorizada()
    with pytest.raises(psycopg.Error) as exc:
        cena.designar(rid, cena.mot)
    assert h.sqlstate(exc) == "AG033"
    assert all(r[1] not in TIPOS_MOTORISTA for r in _notifs(rid))


# --- integridade: CHECKs, deduplicacao, gatilho ------------------------------------------------------
def _inserir(usuario: uuid.UUID, tipo: str, rid: uuid.UUID | None, ref: uuid.UUID | None) -> None:
    with h.conectar() as c:
        c.execute(
            "insert into public.notificacoes (usuario_id, tipo, requisicao_id, referencia_id, critica) "
            "values (%s, %s, %s, %s, false)",
            (usuario, tipo, rid, ref),
        )


@pytest.mark.parametrize("tipo", TIPOS_MOTORISTA)
def test_tipo_do_motorista_exige_requisicao_id(cena: Cena, tipo: str) -> None:
    with pytest.raises(psycopg.Error) as exc:
        _inserir(cena.mot, tipo, None, uuid.uuid4())
    assert h.sqlstate(exc) == "23514"  # ck_notificacoes_requisicao_coerente


def test_tipo_desconhecido_continua_recusado(cena: Cena) -> None:
    with pytest.raises(psycopg.Error) as exc:
        _inserir(cena.mot, "motorista_qualquer", None, None)
    assert h.sqlstate(exc) == "23514"


@pytest.mark.parametrize("tipo", TIPOS_MOTORISTA)
def test_indice_de_deduplicacao_cobre_os_tipos_do_motorista(cena: Cena, tipo: str) -> None:
    cena.ligar()
    rid = cena.autorizada()
    ref = uuid.uuid4()
    _inserir(cena.mot, tipo, rid, ref)
    with pytest.raises(psycopg.Error) as exc:
        _inserir(cena.mot, tipo, rid, ref)  # reprocessar o mesmo evento nao duplica
    assert h.sqlstate(exc) == "23505"
    _inserir(cena.mot, tipo, rid, uuid.uuid4())  # outro evento: permitido


def test_gatilho_inclui_acao_e_segue_sempre_ativo() -> None:
    with h.conectar() as c:
        row = c.execute(
            "select tgenabled, pg_get_triggerdef(oid) from pg_trigger "
            "where tgrelid = 'public.auditoria'::regclass and tgname = 'trg_auditoria_notificar' "
            "and not tgisinternal"
        ).fetchone()
    assert row is not None
    assert row[0] == "A" and "motorista_designado" in row[1]


# --- privilegios e RLS -----------------------------------------------------------------------------
def test_notificacoes_deny_by_default_para_a_api(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    sql = "select usuario_id from public.notificacoes where requisicao_id = %s"
    for papel, uid in (("anon", None), ("authenticated", cena.mot), ("authenticated", cena.admin)):
        with pytest.raises(psycopg.Error) as exc:
            with h.como(papel, uid) as c:
                c.execute(sql, (rid,))
        assert h.sqlstate(exc) == NEGADO, papel
    with h.como("service_role") as c:  # leitura so via service_role (backend)
        assert [r[0] for r in c.execute(sql + " and tipo = 'motorista_designado'", (rid,)).fetchall()] == [cena.mot]
    for dml in (
        "update public.notificacoes set lida_em = now() where requisicao_id = %s",
        "delete from public.notificacoes where requisicao_id = %s",
    ):
        with pytest.raises(psycopg.Error) as exc2:
            with h.como("service_role") as c:
                c.execute(dml, (rid,))
        assert h.sqlstate(exc2) == NEGADO


def test_rls_ativo_e_politica_sem_acesso() -> None:
    with h.conectar() as c:
        (rls,) = c.execute(
            "select relrowsecurity from pg_class where oid = 'public.notificacoes'::regclass"
        ).fetchone() or (False,)
        pol = c.execute("select count(*) from pg_policies where tablename = 'notificacoes'").fetchone()
    assert rls is True and pol is not None and pol[0] >= 1


def test_gerador_e_notificar_sem_execute_para_a_api() -> None:
    with h.conectar() as c:
        row = c.execute(
            "select bool_or(has_function_privilege(r, p, 'execute')) from unnest(array["
            "to_regprocedure('public.notificacoes_gerar()'), "
            "to_regprocedure('privado.notificar(text,uuid,uuid,boolean,text,uuid,text,uuid,uuid)')]) p, "
            "unnest(array['anon','authenticated','service_role']) r"
        ).fetchone()
    assert row == (False,)
