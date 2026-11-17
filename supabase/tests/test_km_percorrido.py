"""Testes da migration 20261127120000_km_percorrido.

km_percorrido = km_chegada - km_saida, calculado so pelo banco (gatilho trg_requisicoes_zz_km_percorrido):
nulo com algum km nulo, valor do cliente ignorado, linha encerrada congelada, importacao recalcula e
compara sem a coluna derivada, relatorios so expoem com Quilometragem ligada.

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). A fixture autouse de recursos (mesma de
test_quilometragem) desliga quilometragem/abastecimento antes e restaura depois. Rodar sem paralelismo.

    pytest supabase/tests/test_km_percorrido.py -m db_local -p no:xdist
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h
from test_dados_exportar_importar import (
    arquivo,
    importar,
    placa_unica,
    requisicao_arq,
    veiculo_arq,
)
from test_quilometragem import (
    Atores,
    _autorizada,
    _concluir,
    _definir_abastecimento,
    _definir_autorizador_km,
    _definir_km,
    _dsn_local,
    _iniciar,
    _ler_autorizador_km,
    _ler_recursos,
)

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

ENCERRADA = "AG001"
GATILHO = "trg_requisicoes_zz_km_percorrido"
MOV = (
    "select public.rel_movimentacao(%s::timestamptz, %s::timestamptz, 'saida_real', null, %s, null, "
    "false, false, 5001, false)"
)
KM = "select public.rel_opcionais_km(%s::timestamptz, %s::timestamptz, null, %s, null, 5001, false)"


@pytest.fixture
def atores() -> Atores:
    return Atores(
        dono=h.criar_usuario("dono-kmp"),
        autorizador=h.criar_usuario("aut-kmp", "autorizador"),
        admin=h.criar_usuario("adm-kmp", "admin"),
    )


@pytest.fixture(autouse=True)
def _estado_recursos() -> Iterator[None]:
    """Desliga autorizador_km/quilometragem/abastecimento antes (dependentes primeiro, AG010) e restaura
    depois."""
    if not _dsn_local():
        pytest.skip("DSN nao parece banco local/descartavel (TEST_DATABASE_ALLOW_REMOTE=1 forca)")
    orig_km, orig_ab = _ler_recursos()
    orig_aut_km = _ler_autorizador_km()
    por = h.admin_ativo_qualquer() or h.criar_usuario("admin-cfg", "admin")
    if orig_ab:
        _definir_abastecimento(False, por)
    if orig_aut_km:
        _definir_autorizador_km(False, por)
    if orig_km:
        _definir_km(False, por)
    try:
        yield
    finally:
        atual_km, atual_ab = _ler_recursos()
        if atual_ab:
            _definir_abastecimento(False, por)
        if _ler_autorizador_km():
            _definir_autorizador_km(False, por)
        if atual_km != orig_km:
            _definir_km(orig_km, por)
        if orig_ab:
            _definir_abastecimento(True, por)
        if orig_aut_km:
            _definir_autorizador_km(True, por)


@contextmanager
def _km_gravavel(admin: uuid.UUID) -> Iterator[None]:
    """Desde 20261204120000 (B5) o autorizador so grava km com quilometragem E autorizador_km ligadas
    (desligado: AG046). Liga as duas e, ao sair, volta ao estado desligado da fixture (flag do papel
    antes da quilometragem, AG010). O km gravado continua (desligar nunca apaga)."""
    _definir_km(True, admin)
    _definir_autorizador_km(True, admin)
    try:
        yield
    finally:
        _definir_autorizador_km(False, admin)
        _definir_km(False, admin)


def _kmp(rid: uuid.UUID | str) -> int | None:
    with h.conectar() as c:
        row = c.execute(
            "select km_percorrido from public.requisicoes where id = %s", (rid,)
        ).fetchone()
    assert row is not None
    return row[0]


def _concluida(a: Atores, km_saida: int = 100, km_chegada: int = 150) -> uuid.UUID:
    rid = _autorizada(a)
    with _km_gravavel(a.admin):
        _iniciar(rid, a.autorizador, km_saida)
        _concluir(rid, a.autorizador, km_chegada)
    return rid


def _veiculo_de(rid: uuid.UUID) -> uuid.UUID:
    with h.conectar() as c:
        row = c.execute(
            "select veiculo_id from public.requisicoes where id = %s", (rid,)
        ).fetchone()
    assert row is not None
    return row[0]


def _janela() -> tuple[datetime, datetime]:
    agora = datetime.now(UTC)
    return agora - timedelta(hours=1), agora + timedelta(hours=1)


# --- estrutura ---------------------------------------------------------------------
def test_coluna_inteira_nula_nao_gerada() -> None:
    with h.conectar() as c:
        row = c.execute(
            "select data_type, is_nullable, column_default, is_generated from information_schema.columns "
            "where table_schema='public' and table_name='requisicoes' and column_name='km_percorrido'"
        ).fetchone()
    assert row == ("integer", "YES", None, "NEVER")


def test_gatilho_e_o_ultimo_before() -> None:
    with h.conectar() as c:
        nomes = [
            r[0]
            for r in c.execute(
                "select tgname from pg_trigger where tgrelid = 'public.requisicoes'::regclass "
                "and not tgisinternal and tgenabled = 'A' and (tgtype & 2) = 2 and (tgtype & 1) = 1 "
                "order by tgname"
            ).fetchall()
        ]
    assert nomes[-1] == GATILHO


def test_rls_continua_ativo() -> None:
    with h.conectar() as c:
        row = c.execute(
            "select relrowsecurity from pg_class where oid = 'public.requisicoes'::regclass"
        ).fetchone()
    assert row == (True,)


# --- calculo -----------------------------------------------------------------------
def test_nulo_sem_km_e_so_saida(atores: Atores) -> None:
    rid = _autorizada(atores)
    assert _kmp(rid) is None
    with _km_gravavel(atores.admin):
        _iniciar(rid, atores.autorizador, 100)
    assert _kmp(rid) is None


def test_calcula_na_chegada(atores: Atores) -> None:
    rid = _concluida(atores, 100, 175)
    assert _kmp(rid) == 75


def test_chegada_sem_km_fica_nulo(atores: Atores) -> None:
    rid = _autorizada(atores)
    _iniciar(rid, atores.autorizador)
    _concluir(rid, atores.autorizador)
    assert _kmp(rid) is None


def test_valor_do_cliente_e_ignorado_em_linha_aberta(atores: Atores) -> None:
    rid = _autorizada(atores)
    with _km_gravavel(atores.admin):
        _iniciar(rid, atores.autorizador, 100)
    with h.conectar() as c:
        c.execute("update public.requisicoes set km_percorrido = 999 where id = %s", (rid,))
    assert _kmp(rid) is None


def test_valor_do_cliente_em_linha_encerrada_e_recusado(atores: Atores) -> None:
    rid = _concluida(atores)
    with pytest.raises(psycopg.Error) as exc, h.conectar() as c:
        c.execute("update public.requisicoes set km_percorrido = 1 where id = %s", (rid,))
    assert h.sqlstate(exc) == ENCERRADA
    assert _kmp(rid) == 50


# --- importacao --------------------------------------------------------------------
def test_importacao_recalcula_e_reimportacao_e_existente(atores: Atores) -> None:
    v = veiculo_arq(placa_unica())
    r = requisicao_arq(atores.dono, v["id"], atores.admin)  # concluida, km 100 -> 150
    com_valor_falso = dict(r, km_percorrido=999)  # chave descartada pelo filtro; gatilho recalcula
    ok = importar(arquivo(veiculos=[v], requisicoes=[com_valor_falso]), atores.admin, True)
    assert ok["erros"] == [] and ok["aplicado"] is True
    assert ok["resumo"]["requisicoes"]["novos"] == 1
    assert _kmp(r["id"]) == 50

    for linha in (r, com_valor_falso):  # sem a chave (arquivo antigo) e com a chave
        de_novo = importar(arquivo(requisicoes=[linha]), atores.admin, True)
        assert de_novo["resumo"]["requisicoes"] == {"novos": 0, "existentes": 1, "conflitos": 0}
    assert _kmp(r["id"]) == 50


def test_importacao_nao_desliga_o_gatilho_de_calculo() -> None:
    with h.conectar() as c:
        row = c.execute(
            "select position('requisicoes_calcular_km_percorrido' in "
            "pg_get_functiondef('privado.dados_desligar_gatilhos()'::regprocedure)) > 0"
        ).fetchone()
    assert row == (True,)


# --- relatorios --------------------------------------------------------------------
def _relatorios(vid: uuid.UUID) -> tuple[dict, dict]:
    ini, fim = _janela()
    with h.conectar() as c:
        mov = c.execute(MOV, (ini, fim, vid)).fetchone()[0]
        km = c.execute(KM, (ini, fim, vid)).fetchone()[0]
    return (
        json.loads(mov) if isinstance(mov, str) else mov,
        json.loads(km) if isinstance(km, str) else km,
    )


def test_relatorios_sem_km_percorrido_com_recurso_desligado(atores: Atores) -> None:
    rid = _concluida(atores)
    mov, km = _relatorios(_veiculo_de(rid))
    assert "km_percorrido" not in mov["colunas"]
    assert mov["linhas"] and all("km_percorrido" not in ln for ln in mov["linhas"])
    assert km == {"habilitado": False}


def test_relatorios_com_km_percorrido_com_recurso_ligado(atores: Atores) -> None:
    rid = _concluida(atores, 200, 260)
    _definir_km(True, atores.admin)
    mov, km = _relatorios(_veiculo_de(rid))
    assert "km_percorrido" in mov["colunas"]
    assert [ln["km_percorrido"] for ln in mov["linhas"]] == [60]
    assert km["habilitado"] is True
    assert {"km_rodado", "km_percorrido"} <= set(km["colunas"])
    assert [(ln["km_rodado"], ln["km_percorrido"]) for ln in km["linhas"]] == [(60, 60)]
    assert km["totais"]["km_rodado"] == 60
