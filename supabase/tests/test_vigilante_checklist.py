"""Testes das migrations 20261118120000/120100: checklist do vigilante (M1) e mensagem AG010 (B4).

Cobre: o vigilante registra checklist de saida/chegada de qualquer requisicao (sem flag e sem designacao),
com fotos, e completa saida/chegada com o recurso Checklist ligado; regressao dos demais papeis (solicitante
e inativo AG002, motorista AG034/AG035, janela AG014, autorizador/admin); mensagem do caminho inverso do AG010.

Somente banco local/dev DESCARTAVEL (TEST_DATABASE_URL). Ver dbhelpers.py. Fixture autouse isola as flags
globais; rodar sem paralelismo.

    pytest supabase/tests/test_vigilante_checklist.py -m db_local -p no:xdist
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.types.json import Jsonb

import dbhelpers as h

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(
        not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"
    ),
]

# Dependentes primeiro (desligar nesta ordem; religar na ordem inversa).
FLAGS = (
    "vigilante_km",
    "abastecimento_manutencao",
    "quilometragem",
    "motorista_localizacao",
    "motorista_registra",
    "motorista",
    "checklist",
)


def _estado() -> dict[str, bool]:
    with h.conectar() as c:
        row = c.execute(
            f"select {', '.join(FLAGS)} from public.configuracoes"
        ).fetchone()
    assert row is not None
    return dict(zip(FLAGS, (bool(v) for v in row), strict=True))


def _cfg(flag: str, valor: bool, por: uuid.UUID | None) -> None:
    with h.conectar() as c:
        c.execute(
            f"update public.configuracoes set {flag} = %s, alterado_por = %s where id = true",
            (valor, por),
        )


@pytest.fixture(autouse=True)
def _estado_recursos() -> Iterator[None]:
    orig = _estado()
    por = h.admin_ativo_qualquer() or h.criar_usuario("vc-admin-cfg", "admin")
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
        self.admin = h.criar_usuario("vc-admin", "admin")
        self.dono = h.criar_usuario("vc-dono")
        self.aut = h.criar_usuario("vc-aut", "autorizador")
        self.vig = h.criar_usuario("vc-vig", "vigilante")
        self.mot = h.criar_usuario("vc-mot", "motorista")
        self.mot2 = h.criar_usuario("vc-mot2", "motorista")

    def cfg(self, flag: str, valor: bool) -> None:
        _cfg(flag, valor, self.admin)

    def autorizada(self) -> uuid.UUID:
        with h.conectar() as c:
            rid = h.inserir_requisicao(c, self.dono, h.novo_veiculo())
        h.autorizar(rid, self.aut)
        return rid


@pytest.fixture
def cena() -> Cena:
    return Cena()


def _checklist(rid: uuid.UUID, momento: str, por: uuid.UUID, fotos: list | None = None) -> uuid.UUID:  # type: ignore[type-arg]
    with h.conectar() as c:
        row = c.execute(
            "select public.registrar_checklist(%s, %s, 'meio'::public.nivel_combustivel, false, null, %s, %s)",
            (rid, momento, por, Jsonb(fotos or [])),
        ).fetchone()
    assert row is not None
    return row[0]


def _erro(rid: uuid.UUID, momento: str, por: uuid.UUID, codigo: str) -> None:
    with pytest.raises(psycopg.Error) as exc:
        _checklist(rid, momento, por)
    assert h.sqlstate(exc) == codigo


def _n_checklists(rid: uuid.UUID) -> int:
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.checklists where requisicao_id=%s", (rid,)
        ).fetchone() or (0,)
    return int(n)


# --- M1: vigilante registra checklist ---------------------------------------------------------------
def test_vigilante_checklist_completa_saida_e_chegada_com_recurso_ligado(
    cena: Cena,
) -> None:
    cena.cfg("checklist", True)
    rid = cena.autorizada()  # sem motorista designado; recurso Motorista desligado
    # Sem checklist, a saida segue bloqueada (AG013): o vigilante nao burla a regra.
    with pytest.raises(psycopg.Error) as exc:
        h.iniciar(rid, cena.vig)
    assert h.sqlstate(exc) == "AG013"
    _checklist(rid, "saida", cena.vig)
    h.iniciar(rid, cena.vig)
    assert h.status_de(rid) == "em_uso"
    _checklist(rid, "chegada", cena.vig)
    h.concluir(rid, cena.vig)
    assert h.status_de(rid) == "concluida"
    assert _n_checklists(rid) == 2


def test_vigilante_checklist_com_fotos_e_requisicao_designada_a_outro(
    cena: Cena,
) -> None:
    cena.cfg("checklist", True)
    rid = cena.autorizada()
    with h.conectar() as c:
        c.execute(
            "update public.requisicoes set motorista_id=%s, motorista_alterado_por=%s where id=%s",
            (cena.mot, cena.aut, rid),
        )
    foto = {"id": str(uuid.uuid4()), "mime_type": "image/jpeg", "tamanho_bytes": 1000}
    _checklist(rid, "saida", cena.vig, [foto])
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.checklist_fotos where requisicao_id=%s", (rid,)
        ).fetchone() or (0,)
    assert n == 1


def test_vigilante_checklist_registrado_por_e_o_vigilante(cena: Cena) -> None:
    rid = cena.autorizada()
    cid = _checklist(rid, "saida", cena.vig)
    with h.conectar() as c:
        row = c.execute(
            "select registrado_por, registrado_em is not null from public.checklists where id=%s",
            (cid,),
        ).fetchone()
    assert row == (cena.vig, True)


def test_vigilante_respeita_a_janela_ag014(cena: Cena) -> None:
    with h.conectar() as c:
        solicitada = h.inserir_requisicao(c, cena.dono, h.novo_veiculo())
    _erro(solicitada, "saida", cena.vig, "AG014")  # ainda nao autorizada
    rid = cena.autorizada()
    _erro(rid, "chegada", cena.vig, "AG014")  # chegada so com em_uso
    h.iniciar(rid, cena.aut)
    _erro(rid, "saida", cena.vig, "AG014")  # saida so com autorizada
    h.concluir(rid, cena.aut)
    _erro(rid, "chegada", cena.vig, "AG014")  # concluida
    assert _n_checklists(rid) == 0


def test_vigilante_inativo_e_recusado_ag002(cena: Cena) -> None:
    rid = cena.autorizada()
    inativo = h.criar_usuario("vc-inativo", "vigilante", ativo=False)
    _erro(rid, "saida", inativo, "AG002")
    assert _n_checklists(rid) == 0


# --- regressao dos demais papeis -----------------------------------------------------------------------
def test_solicitante_continua_recusado_ag002(cena: Cena) -> None:
    rid = cena.autorizada()
    _erro(rid, "saida", cena.dono, "AG002")
    assert _n_checklists(rid) == 0


def test_staff_continua_registrando(cena: Cena) -> None:
    rid = cena.autorizada()
    _checklist(rid, "saida", cena.aut)
    rid2 = cena.autorizada()
    _checklist(rid2, "saida", cena.admin)
    assert (_n_checklists(rid), _n_checklists(rid2)) == (1, 1)


def test_motorista_continua_com_flag_e_designacao(cena: Cena) -> None:
    cena.cfg("motorista", True)
    rid = cena.autorizada()
    _erro(rid, "saida", cena.mot, "AG034")  # motorista_registra desligado
    cena.cfg("motorista_registra", True)
    _erro(rid, "saida", cena.mot, "AG035")  # nao designado
    with h.conectar() as c:
        c.execute(
            "update public.requisicoes set motorista_id=%s, motorista_alterado_por=%s where id=%s",
            (cena.mot, cena.aut, rid),
        )
    _erro(rid, "saida", cena.mot2, "AG035")
    _checklist(rid, "saida", cena.mot)
    assert _n_checklists(rid) == 1


def test_funcao_sem_execute_para_a_api_e_gatilho_ativo() -> None:
    with h.conectar() as c:
        for papel in ("anon", "authenticated", "service_role"):
            (ok,) = c.execute(
                "select has_function_privilege(%s, 'public.checklists_validar()'::regprocedure, 'execute')",
                (papel,),
            ).fetchone() or (True,)
            assert ok is False, papel
        row = c.execute(
            "select tgenabled from pg_trigger where tgrelid='public.checklists'::regclass "
            "and tgname='trg_checklists_validar' and not tgisinternal"
        ).fetchone()
    assert row is not None and row[0] == "A"  # ENABLE ALWAYS


# --- B4: mensagem do AG010 no caminho inverso ---------------------------------------------------------------
def _msg_ag010(sql: str, params: tuple) -> str:  # type: ignore[type-arg]
    with pytest.raises(psycopg.Error) as exc, h.conectar() as c:
        c.execute(sql, params)
    assert h.sqlstate(exc) == "AG010"
    return str(getattr(exc.value, "diag", None) and exc.value.diag.message_primary)


def test_desligar_quilometragem_com_vigilante_km_ligada_mensagem_correta(
    cena: Cena,
) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("vigilante_km", True)
    msg = _msg_ag010(
        "update public.configuracoes set quilometragem = false, alterado_por = %s where id = true",
        (cena.admin,),
    )
    assert "Desligue o KM do vigilante antes de desligar a Quilometragem" in msg
    estado = _estado()
    assert (
        estado["quilometragem"] is True and estado["vigilante_km"] is True
    )  # nada mudou


def test_ligar_vigilante_km_sem_quilometragem_mantem_a_mensagem_original(
    cena: Cena,
) -> None:
    msg = _msg_ag010(
        "update public.configuracoes set vigilante_km = true, alterado_por = %s where id = true",
        (cena.admin,),
    )
    assert "O KM do vigilante exige a Quilometragem ligada" in msg
    assert _estado()["vigilante_km"] is False


def test_ligar_as_duas_juntas_continua_AG010_e_desligar_as_duas_juntas_vale(
    cena: Cena,
) -> None:
    msg = _msg_ag010(
        "update public.configuracoes set vigilante_km = true, quilometragem = false, alterado_por = %s "
        "where id = true",
        (cena.admin,),
    )
    assert "exige a Quilometragem ligada" in msg
    cena.cfg("quilometragem", True)
    cena.cfg("vigilante_km", True)
    with h.conectar() as c:
        c.execute(
            "update public.configuracoes set vigilante_km = false, quilometragem = false, alterado_por = %s "
            "where id = true",
            (cena.admin,),
        )
    estado = _estado()
    assert estado["quilometragem"] is False and estado["vigilante_km"] is False
