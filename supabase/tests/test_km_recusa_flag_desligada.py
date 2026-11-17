"""Testes da migration 20261204120000 (B5): requisicoes_validar_km recusa km sem flag aplicavel (AG046).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). A fixture autouse desliga as flags de KM (e as que
dependem delas) antes de cada teste e restaura o estado original no fim. Rodar sem paralelismo.

    pytest supabase/tests/test_km_recusa_flag_desligada.py -m db_local
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261204120000_km_recusa_flag_desligada.sql"
)

# Dependentes primeiro (desligar nesta ordem; religar na ordem inversa).
FLAGS = (
    "motorista_km",
    "motorista_localizacao",
    "motorista_registra",
    "motorista",
    "autorizador_km",
    "vigilante_km",
    "abastecimento_manutencao",
    "quilometragem",
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
    por = h.admin_ativo_qualquer() or h.criar_usuario("krf-admin-cfg", "admin")
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
        self.admin = h.criar_usuario("krf-admin", "admin")
        self.dono = h.criar_usuario("krf-dono")
        self.aut = h.criar_usuario("krf-aut", "autorizador")
        self.vig = h.criar_usuario("krf-vig", "vigilante")
        self.mot = h.criar_usuario("krf-mot", "motorista")

    def cfg(self, flag: str, valor: bool) -> None:
        _cfg(flag, valor, self.admin)

    def autorizada(self, motorista: uuid.UUID | None = None) -> uuid.UUID:
        with h.conectar() as c:
            rid = h.inserir_requisicao(c, self.dono, h.novo_veiculo())
        h.autorizar(rid, self.aut)
        if motorista is not None:
            with h.conectar() as c:
                c.execute(
                    "update public.requisicoes set motorista_id = %s, motorista_alterado_por = %s where id = %s",
                    (motorista, self.aut, rid),
                )
        return rid


@pytest.fixture
def cena() -> Cena:
    return Cena()


def _saida(rid: uuid.UUID, por: uuid.UUID, km: int | None = None) -> None:
    with h.conectar() as c:
        c.execute(
            "update public.requisicoes set status='em_uso', saida_registrada_por=%s, km_saida=%s where id=%s",
            (por, km, rid),
        )


def _chegada(
    rid: uuid.UUID, por: uuid.UUID, km: int | None = None, km_saida: int | None = None
) -> None:
    with h.conectar() as c:
        c.execute(
            "update public.requisicoes set status='concluida', chegada_registrada_por=%s, km_chegada=%s, "
            "km_saida=coalesce(%s, km_saida) where id=%s",
            (por, km, km_saida, rid),
        )


def _km(rid: uuid.UUID) -> tuple[int | None, int | None]:
    with h.conectar() as c:
        row = c.execute("select km_saida, km_chegada from public.requisicoes where id=%s", (rid,)).fetchone()
    assert row is not None
    return row[0], row[1]


def _falha(fn, *args, codigo: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.Error) as exc:
        fn(*args)
    assert h.sqlstate(exc) == codigo


# --- estrutura ---------------------------------------------------------------------------------------------
def test_funcao_security_definer_search_path_vazio_e_sem_execute() -> None:
    with h.conectar() as c:
        row = c.execute(
            "select p.prosecdef, p.proconfig, "
            "has_function_privilege('authenticated', p.oid, 'execute'), "
            "has_function_privilege('anon', p.oid, 'execute'), "
            "has_function_privilege('service_role', p.oid, 'execute') "
            "from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
            "where n.nspname = 'public' and p.proname = 'requisicoes_validar_km'"
        ).fetchone()
    assert row is not None
    assert row[0] is True
    assert row[1] is not None and [x.replace('"', "") for x in row[1]] == ["search_path="]
    assert row[2:] == (False, False, False)


def test_migration_reaplica_sem_erro() -> None:
    with h.reaplicando_migration() as c:
        c.execute(MIGRATION.read_text(encoding="utf-8"))


# --- Quilometragem desligada: nenhum papel grava km ---------------------------------------------------------
@pytest.mark.parametrize("ator", ["aut", "vig", "admin"])
def test_quilometragem_desligada_recusa_km_saida(cena: Cena, ator: str) -> None:
    rid = cena.autorizada()
    _falha(_saida, rid, getattr(cena, ator), 100, codigo="AG046")
    assert h.status_de(rid) == "autorizada"
    _saida(rid, getattr(cena, ator))  # sem km: transicao segue valendo
    _falha(_chegada, rid, getattr(cena, ator), 150, codigo="AG046")
    _falha(_chegada, rid, getattr(cena, ator), 150, 100, codigo="AG046")
    _chegada(rid, getattr(cena, ator))
    assert h.status_de(rid) == "concluida" and _km(rid) == (None, None)


# --- Quilometragem ligada, flag do papel desligada ----------------------------------------------------------
def test_vigilante_sem_vigilante_km_recusa_km(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("autorizador_km", True)  # flag de OUTRO papel nao vale para o vigilante
    rid = cena.autorizada()
    _falha(_saida, rid, cena.vig, 500, codigo="AG046")
    _saida(rid, cena.vig)
    _falha(_chegada, rid, cena.vig, 520, 500, codigo="AG046")
    _chegada(rid, cena.vig)
    assert _km(rid) == (None, None)


def test_autorizador_sem_autorizador_km_recusa_km(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("vigilante_km", True)
    rid = cena.autorizada()
    _falha(_saida, rid, cena.aut, 10, codigo="AG046")
    _falha(_saida, rid, cena.admin, 10, codigo="AG046")
    _saida(rid, cena.aut)
    _chegada(rid, cena.aut)
    assert _km(rid) == (None, None)


def _rpc_motorista(rid: uuid.UUID, momento: str, ator: uuid.UUID, km_s: int | None, km_c: int | None) -> None:
    with h.conectar() as c:
        c.execute(
            "select public.registrar_transicao_motorista(%s, %s, %s, null, null, null, %s, %s)",
            (rid, momento, ator, km_s, km_c),
        )


def test_motorista_sem_motorista_km_recusa_km_pela_rpc(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("autorizador_km", True)
    cena.cfg("motorista", True)
    cena.cfg("motorista_registra", True)
    rid = cena.autorizada(cena.mot)
    _falha(_rpc_motorista, rid, "saida", cena.mot, 300, None, codigo="AG046")
    assert h.status_de(rid) == "autorizada"
    _rpc_motorista(rid, "saida", cena.mot, None, None)
    _falha(_rpc_motorista, rid, "chegada", cena.mot, 300, 350, codigo="AG046")
    _rpc_motorista(rid, "chegada", cena.mot, None, None)
    assert h.status_de(rid) == "concluida" and _km(rid) == (None, None)


# --- comportamento preservado -------------------------------------------------------------------------------
def test_flag_ligada_segue_obrigando_e_gravando(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("vigilante_km", True)
    rid = cena.autorizada()
    _falha(_saida, rid, cena.vig, codigo="AG012")
    _saida(rid, cena.vig, 1000)
    _falha(_chegada, rid, cena.vig, codigo="AG012")
    _chegada(rid, cena.vig, 1040)
    assert _km(rid) == (1000, 1040)


def test_motorista_km_ligada_grava_pela_rpc(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("motorista", True)
    cena.cfg("motorista_registra", True)
    cena.cfg("motorista_km", True)
    rid = cena.autorizada(cena.mot)
    _rpc_motorista(rid, "saida", cena.mot, 300, None)
    _rpc_motorista(rid, "chegada", cena.mot, None, 350)
    assert _km(rid) == (300, 350)


def test_km_gravado_com_flag_ligada_sobrevive_ao_desligar_e_chegada_sem_km_vale(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("autorizador_km", True)
    rid = cena.autorizada()
    _saida(rid, cena.aut, 700)
    cena.cfg("autorizador_km", False)
    _falha(_chegada, rid, cena.aut, 760, codigo="AG046")  # sem flag: nao grava km de chegada
    _chegada(rid, cena.aut)  # km_saida antigo nao muda: nao conta como gravar
    assert h.status_de(rid) == "concluida" and _km(rid) == (700, None)


def test_imutabilidade_continua_ag004_antes_do_ag046(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("autorizador_km", True)
    rid = cena.autorizada()
    _saida(rid, cena.aut, 700)
    cena.cfg("autorizador_km", False)
    _falha(_chegada, rid, cena.aut, 760, 701, codigo="AG004")


def test_sem_mudar_status_km_segue_ag004(cena: Cena) -> None:
    rid = cena.autorizada()
    _saida(rid, cena.aut)
    with pytest.raises(psycopg.Error) as exc, h.conectar() as c:
        c.execute("update public.requisicoes set km_saida = 5 where id = %s", (rid,))
    assert h.sqlstate(exc) == "AG004"


def test_cancelar_e_negar_sem_km_seguem_validos(cena: Cena) -> None:
    rid = cena.autorizada()
    h.cancelar(rid, cena.aut)
    assert h.status_de(rid) == "cancelada"
    with h.conectar() as c:
        rid2 = h.inserir_requisicao(c, cena.dono, h.novo_veiculo())
    h.negar(rid2, cena.aut)
    assert h.status_de(rid2) == "negada" and _km(rid2) == (None, None)
