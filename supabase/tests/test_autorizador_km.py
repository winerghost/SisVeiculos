"""Testes da migration 20261119120200: flag autorizador_km e KM obrigatorio POR PAPEL (AG012).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). A fixture autouse desliga as flags de KM (e as que
dependem delas) antes de cada teste e restaura o estado original no fim. Rodar sem paralelismo.

    pytest supabase/tests/test_autorizador_km.py -m db_local
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

# Dependentes primeiro (desligar nesta ordem; religar na ordem inversa).
FLAGS = (
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


def _cfg(flag: str, valor: bool, por: uuid.UUID | None) -> None:
    with h.conectar() as c:
        c.execute(
            f"update public.configuracoes set {flag} = %s, alterado_por = %s where id = true",
            (valor, por),
        )


@pytest.fixture(autouse=True)
def _estado_recursos() -> Iterator[None]:
    orig = _estado()
    por = h.admin_ativo_qualquer() or h.criar_usuario("akm-admin-cfg", "admin")
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
        self.admin = h.criar_usuario("akm-admin", "admin")
        self.dono = h.criar_usuario("akm-dono")
        self.aut = h.criar_usuario("akm-aut", "autorizador")
        self.vig = h.criar_usuario("akm-vig", "vigilante")
        self.mot = h.criar_usuario("akm-mot", "motorista")

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


def _msg_ag010(sql: str, params: tuple) -> str:  # type: ignore[type-arg]
    with pytest.raises(psycopg.Error) as exc, h.conectar() as c:
        c.execute(sql, params)
    assert h.sqlstate(exc) == "AG010"
    return str(getattr(exc.value, "diag", None) and exc.value.diag.message_primary)


# --- coluna, CHECK, AG010, auditoria ----------------------------------------------------------------------
def test_coluna_boolean_not_null_default_false() -> None:
    with h.conectar() as c:
        row = c.execute(
            "select data_type, is_nullable, column_default from information_schema.columns "
            "where table_schema='public' and table_name='configuracoes' and column_name='autorizador_km'"
        ).fetchone()
    assert row == ("boolean", "NO", "false")


def test_check_de_tabela_existe() -> None:
    with h.conectar() as c:
        row = c.execute(
            "select 1 from pg_constraint where conrelid='public.configuracoes'::regclass "
            "and conname='ck_configuracoes_autorizador_km_exige_quilometragem' and contype='c'"
        ).fetchone()
    assert row is not None


def test_ligar_sem_quilometragem_ag010(cena: Cena) -> None:
    msg = _msg_ag010(
        "update public.configuracoes set autorizador_km = true, alterado_por = %s where id = true",
        (cena.admin,),
    )
    assert "O KM do autorizador exige a Quilometragem ligada" in msg
    assert _estado()["autorizador_km"] is False


def test_desligar_quilometragem_com_autorizador_km_ligada_ag010(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("autorizador_km", True)
    msg = _msg_ag010(
        "update public.configuracoes set quilometragem = false, alterado_por = %s where id = true",
        (cena.admin,),
    )
    assert "Desligue o KM do autorizador antes de desligar a Quilometragem" in msg
    assert _estado()["quilometragem"] is True


def test_desligar_as_duas_juntas_vale(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("autorizador_km", True)
    with h.conectar() as c:
        c.execute(
            "update public.configuracoes set autorizador_km = false, quilometragem = false, alterado_por = %s "
            "where id = true",
            (cena.admin,),
        )
    assert _estado()["quilometragem"] is False and _estado()["autorizador_km"] is False


def test_alteracao_e_auditada_e_so_admin_altera(cena: Cena) -> None:
    cena.cfg("quilometragem", True)

    def n() -> int:
        with h.conectar() as c:
            (v,) = c.execute(
                "select count(*) from public.auditoria where tabela='configuracoes' "
                "and acao='configuracao_alterada' and detalhes->>'recurso'='autorizador_km'"
            ).fetchone() or (0,)
        return int(v)

    antes = n()
    cena.cfg("autorizador_km", True)
    cena.cfg("autorizador_km", False)
    assert n() == antes + 2
    with pytest.raises(psycopg.Error) as exc:
        _cfg("autorizador_km", True, cena.aut)
    assert h.sqlstate(exc) == "AG002"


# --- KM por papel -------------------------------------------------------------------------------------------
def test_quilometragem_desligada_ninguem_e_obrigado(cena: Cena) -> None:
    rid = cena.autorizada()
    _saida(rid, cena.aut)
    _chegada(rid, cena.admin)
    assert _km(rid) == (None, None)


@pytest.mark.parametrize("papel", ["aut", "admin"])
def test_autorizador_km_desligada_staff_registra_sem_km(cena: Cena, papel: str) -> None:
    cena.cfg("quilometragem", True)
    ator = getattr(cena, papel)
    rid = cena.autorizada()
    _saida(rid, ator)
    _chegada(rid, ator)
    assert h.status_de(rid) == "concluida"
    assert _km(rid) == (None, None)


@pytest.mark.parametrize("papel", ["aut", "admin"])
def test_autorizador_km_ligada_staff_e_obrigado(cena: Cena, papel: str) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("autorizador_km", True)
    ator = getattr(cena, papel)
    rid = cena.autorizada()
    _falha(_saida, rid, ator, codigo="AG012")
    _saida(rid, ator, 100)
    _falha(_chegada, rid, ator, codigo="AG012")
    _falha(_chegada, rid, ator, 99, codigo="23514")  # chegada < saida (CHECK)
    _chegada(rid, ator, 130)
    assert _km(rid) == (100, 130)


def test_motorista_nunca_e_obrigado_mesmo_com_todas_as_flags(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("autorizador_km", True)
    cena.cfg("vigilante_km", True)
    cena.cfg("motorista", True)
    cena.cfg("motorista_registra", True)
    rid = cena.autorizada()
    with h.conectar() as c:
        c.execute(
            "update public.requisicoes set motorista_id = %s, motorista_alterado_por = %s where id = %s",
            (cena.mot, cena.aut, rid),
        )
        for momento in ("saida", "chegada"):
            c.execute(
                "select public.registrar_transicao_motorista(%s, %s, %s, null::numeric, null::numeric, "
                "null::numeric, null::integer, null::integer)",
                (rid, momento, cena.mot),
            )
    assert h.status_de(rid) == "concluida"
    assert _km(rid) == (None, None)


def test_vigilante_segue_vigilante_km(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    rid = cena.autorizada()
    _saida(rid, cena.vig)  # vigilante_km desligada: dispensado
    _chegada(rid, cena.vig)
    assert _km(rid) == (None, None)
    cena.cfg("vigilante_km", True)
    rid2 = cena.autorizada()
    _falha(_saida, rid2, cena.vig, codigo="AG012")
    _saida(rid2, cena.vig, 10)
    _chegada(rid2, cena.vig, 20)
    assert _km(rid2) == (10, 20)


def test_autorizador_km_ligada_nao_obriga_o_vigilante(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("autorizador_km", True)
    rid = cena.autorizada()
    _saida(rid, cena.vig)
    _chegada(rid, cena.vig)
    assert _km(rid) == (None, None)


def test_saida_do_staff_sem_km_e_chegada_do_vigilante_obrigado_exige_os_dois_km(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("vigilante_km", True)
    rid = cena.autorizada()
    _saida(rid, cena.aut)  # autorizador_km desligada: sem KM
    _falha(_chegada, rid, cena.vig, 50, codigo="AG012")  # falta km_saida
    _chegada(rid, cena.vig, 50, 40)
    assert _km(rid) == (40, 50)


def test_desligar_autorizador_km_nao_apaga_km_gravado(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("autorizador_km", True)
    rid = cena.autorizada()
    _saida(rid, cena.aut, 700)
    cena.cfg("autorizador_km", False)
    assert _km(rid) == (700, None)
    _chegada(rid, cena.aut)  # agora dispensado; km_saida intacto
    assert _km(rid) == (700, None)


def test_km_gravado_continua_imutavel(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    rid = cena.autorizada()
    _saida(rid, cena.aut, 10)
    with pytest.raises(psycopg.Error) as exc:
        _chegada(rid, cena.aut, 20, 11)
    assert h.sqlstate(exc) == "AG004"


def test_funcoes_nao_executaveis_pela_api() -> None:
    with h.conectar() as c:
        for fn in ("public.requisicoes_validar_km()", "public.configuracoes_validar()"):
            for papel in ("anon", "authenticated", "service_role"):
                (ok,) = c.execute(
                    "select has_function_privilege(%s, %s::regprocedure, 'execute')", (papel, fn)
                ).fetchone() or (True,)
                assert ok is False, (fn, papel)
