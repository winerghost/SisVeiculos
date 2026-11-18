"""Testes da migration 20261208120000 (dash_movimentacao_km: painel "Movimentacao e km").

Somente banco local/dev DESCARTAVEL (TEST_DATABASE_URL + TEST_DATABASE_CONFIRM=descartavel + marcador
`comment on database ... is 'sisveiculos:descartavel'`; sem eles os testes sao pulados). Ver dbhelpers.py.

Mesmo esquema de test_dashboards.py: dataset controlado numa transacao com session_replication_role =
replica + DISABLE TRIGGER USER (CHECKs e indices continuam valendo), SEMPRE terminada em ROLLBACK.
Com os gatilhos desligados o km_percorrido nao e calculado: o dataset grava o valor coerente
(km_chegada - km_saida) junto. Cada dataset usa veiculo proprio e o filtro p_veiculo.
ATENCAO: o DISABLE TRIGGER pega lock forte nas tabelas durante o teste: nao rode em paralelo.

    pytest supabase/tests/test_dash_movimentacao_km.py -m db_local -p no:xdist
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
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261208120000_dash_movimentacao_km.sql"
).read_text(encoding="utf-8")

ASSINATURA = "public.dash_movimentacao_km(timestamptz, timestamptz, public.unidade, uuid, text)"
JANELA = ("2026-03-01 00:00:00-03", "2026-04-01 00:00:00-03")  # 31 dias, fuso de Sao Paulo
SQL = "select public.dash_movimentacao_km(%s::timestamptz, %s::timestamptz, %s::public.unidade, %s, %s)"
CHAVES = {"serie", "por_veiculo", "quilometragem_habilitada", "granularidade", "fuso"}
# Flags que dependem de Quilometragem (CHECK em configuracoes): desligadas junto.
FLAGS_KM = ("abastecimento_manutencao", "vigilante_km", "autorizador_km", "motorista_km")


# --- fixture: transacao descartavel com triggers desligados -----------------------------------
@pytest.fixture
def conn() -> Iterator["psycopg.Connection"]:
    assert h.DSN
    c = psycopg.connect(h.DSN)  # sem autocommit: tudo termina em rollback
    try:
        try:
            c.execute("set local session_replication_role = replica")
            for t in ("requisicoes", "veiculos", "configuracoes"):
                c.execute(f"alter table public.{t} disable trigger user")
        except psycopg.Error:
            c.rollback()
            pytest.skip("papel de conexao sem permissao para desligar gatilhos na transacao de teste")
        yield c
    finally:
        c.rollback()
        c.close()


def km_ligado(c: "psycopg.Connection", ligado: bool) -> None:
    if ligado:
        c.execute("update public.configuracoes set quilometragem = true")
    else:
        extras = ", ".join(f"{f} = false" for f in FLAGS_KM)
        c.execute(f"update public.configuracoes set {extras}, quilometragem = false")


def veiculo(c: "psycopg.Connection", ativo: bool = True) -> uuid.UUID:
    vid = uuid.uuid4()
    c.execute(
        "insert into public.veiculos (id, descricao, ativo) values (%s, %s, %s)",
        (vid, f"mov-{vid.hex[:8]}", ativo),
    )
    return vid


def viagem(
    c: "psycopg.Connection",
    vid: uuid.UUID,
    status: str,
    saida: str | None,
    chegada: str | None = None,
    km: int | None = None,
    unidade: str = "UEA",
) -> uuid.UUID:
    """Requisicao com saida/chegada reais controladas. km = km percorrido (km_saida 1000)."""
    rid, ator = uuid.uuid4(), uuid.uuid4()
    base = saida or "2026-03-20 10:00-03"
    cols: dict[str, object] = {
        "id": rid,
        "usuario_id": uuid.uuid4(),
        "nome_solicitante": "Teste",
        "unidade": unidade,
        "trajeto": "A-B",
        "veiculo_id": vid,
        "status": status,
        "hora_saida": base,
        "criado_em": base,
    }
    if status != "solicitada":
        cols["autorizado_por"], cols["autorizado_em"] = ator, base
    if saida:
        cols["saida_registrada_por"], cols["saida_real_em"] = ator, saida
    if chegada:
        cols["chegada_registrada_por"], cols["chegada_real_em"] = ator, chegada
    if km is not None:
        cols["km_saida"], cols["km_chegada"], cols["km_percorrido"] = 1000, 1000 + km, km
    nomes = ", ".join(cols)
    marcas = ", ".join(["%s"] * len(cols))
    c.execute(f"insert into public.requisicoes ({nomes}) values ({marcas})", tuple(cols.values()))
    return rid


def dataset(c: "psycopg.Connection") -> uuid.UUID:
    """Veiculo novo; janela JANELA = [01/03 00:00-03, 01/04 00:00-03).

    A UEA concluida: sai 10/03 11:05, chega 10/03 13:05, 100 km     -> dia 10: s, c, 100
    B UEA concluida: sai 10/03 23:30 -03 (11/03 UTC: dia LOCAL 10), chega 11/03 00:30, 50 km
                                                                     -> dia 10: s; dia 11: c, 50
    C UEA em_uso:    sai 12/03 09:00, sem chegada                   -> dia 12: s
    D UEA concluida: sai 28/02 23:00 (FORA), chega 01/03 01:00, 30 km -> dia 01: c, 30
    E UEA concluida: sai 31/03 22:00, chega 01/04 00:30 (FORA), 70 km -> dia 31: s (km nao entra)
    F PU  concluida: sai 15/03 08:00, chega 15/03 09:00, sem km     -> dia 15: s, c, 0
    G UEA negada:    sem saida/chegada                              -> nada
    Totais na janela: 5 saidas, 4 chegadas, 180 km.
    """
    v = veiculo(c)
    viagem(c, v, "concluida", "2026-03-10 11:05-03", "2026-03-10 13:05-03", 100)
    viagem(c, v, "concluida", "2026-03-10 23:30-03", "2026-03-11 00:30-03", 50)
    viagem(c, v, "em_uso", "2026-03-12 09:00-03")
    viagem(c, v, "concluida", "2026-02-28 23:00-03", "2026-03-01 01:00-03", 30)
    viagem(c, v, "concluida", "2026-03-31 22:00-03", "2026-04-01 00:30-03", 70)
    viagem(c, v, "concluida", "2026-03-15 08:00-03", "2026-03-15 09:00-03", None, unidade="PU")
    viagem(c, v, "negada", None)
    return v


def chamar(c: "psycopg.Connection", *args: object) -> dict:
    row = c.execute(SQL, args).fetchone()
    assert row is not None
    return row[0]


def por_periodo(r: dict) -> dict[str, dict]:
    return {p["periodo"]: p for p in r["serie"]}


# --- estrutura e permissoes ---------------------------------------------------------------------
def test_indice_chegada_criado() -> None:
    with h.conectar() as c:
        row = c.execute(
            "select indexdef from pg_indexes where schemaname='public' and tablename='requisicoes' "
            "and indexname='ix_requisicoes_chegada_real_em'"
        ).fetchone()
    assert row is not None and "chegada_real_em IS NOT NULL" in row[0]


def test_migration_e_idempotente(conn: "psycopg.Connection") -> None:
    conn.execute(MIGRATION)
    conn.execute(MIGRATION)


@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_anon_e_authenticated_sem_execute(papel: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with h.como(papel, uuid.uuid4()) as c:
            c.execute("select public.dash_movimentacao_km(now() - interval '1 day', now(), null, null, 'dia')")


def test_service_role_executa_e_public_nao() -> None:
    with h.como("service_role") as c:
        assert c.execute(
            "select public.dash_movimentacao_km(now() - interval '1 day', now(), null, null, 'dia')"
        ).fetchone() is not None
    with h.conectar() as c:
        assert c.execute(
            "select exists (select 1 from pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
            "where p.oid = %s::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE')", (ASSINATURA,)
        ).fetchone() == (False,)
        assert c.execute(
            "select prosecdef and provolatile = 's' and array_to_string(proconfig, ',') like '%%search_path=%%' "
            "from pg_proc where oid = %s::regprocedure", (ASSINATURA,)
        ).fetchone() == (True,)


# --- parametros invalidos (AG023) ---------------------------------------------------------------
@pytest.mark.parametrize(
    "inicio,fim,gran",
    [
        ("2026-03-01 00:00-03", "2026-03-01 00:00-03", "dia"),  # fim = inicio
        ("2026-03-02 00:00-03", "2026-03-01 00:00-03", "dia"),  # fim < inicio
        ("2026-01-01 00:00-03", "2027-01-03 00:00-03", "mes"),  # 367 dias
        (None, "2026-03-01 00:00-03", "dia"),
        ("2026-03-01 00:00-03", None, "dia"),
        (*JANELA, "ano"),
        (*JANELA, ""),
        (*JANELA, None),
        (*JANELA, "DIA"),
    ],
)
def test_parametros_invalidos(conn: "psycopg.Connection", inicio: str | None, fim: str | None,
                              gran: str | None) -> None:
    with pytest.raises(psycopg.Error) as e:
        conn.execute(SQL, (inicio, fim, None, None, gran))
    assert h.sqlstate(e) == "AG023"


# --- contagens, fuso e km -----------------------------------------------------------------------
def test_dia_com_limite_de_fuso_e_km(conn: "psycopg.Connection") -> None:
    km_ligado(conn, True)
    v = dataset(conn)
    r = chamar(conn, *JANELA, None, v, "dia")
    assert set(r) == CHAVES
    assert r["granularidade"] == "dia" and r["fuso"] == "America/Sao_Paulo"
    assert r["quilometragem_habilitada"] is True
    assert len(r["serie"]) == 31 and r["serie"][0]["periodo"] == "2026-03-01"
    assert r["serie"][-1]["periodo"] == "2026-03-31"
    p = por_periodo(r)
    assert p["2026-03-01"] == {"periodo": "2026-03-01", "saidas": 0, "chegadas": 1, "km_rodado": 30}
    assert p["2026-03-10"] == {"periodo": "2026-03-10", "saidas": 2, "chegadas": 1, "km_rodado": 100}
    assert p["2026-03-11"] == {"periodo": "2026-03-11", "saidas": 0, "chegadas": 1, "km_rodado": 50}
    assert p["2026-03-12"] == {"periodo": "2026-03-12", "saidas": 1, "chegadas": 0, "km_rodado": 0}
    assert p["2026-03-15"] == {"periodo": "2026-03-15", "saidas": 1, "chegadas": 1, "km_rodado": 0}
    assert p["2026-03-31"] == {"periodo": "2026-03-31", "saidas": 1, "chegadas": 0, "km_rodado": 0}
    assert sum(x["saidas"] for x in r["serie"]) == 5
    assert sum(x["chegadas"] for x in r["serie"]) == 4
    assert sum(x["km_rodado"] for x in r["serie"]) == 180
    assert r["por_veiculo"] == [{"veiculo_id": str(v), "saidas": 5, "chegadas": 4, "km_rodado": 180}]


def test_semana_e_mes(conn: "psycopg.Connection") -> None:
    km_ligado(conn, True)
    v = dataset(conn)
    sem = chamar(conn, *JANELA, None, v, "semana")
    # 01/03/2026 e domingo: a primeira semana comeca na segunda 23/02.
    assert [x["periodo"] for x in sem["serie"]] == [
        "2026-02-23", "2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23", "2026-03-30"]
    p = por_periodo(sem)
    assert p["2026-02-23"] == {"periodo": "2026-02-23", "saidas": 0, "chegadas": 1, "km_rodado": 30}
    assert p["2026-03-09"] == {"periodo": "2026-03-09", "saidas": 4, "chegadas": 3, "km_rodado": 150}
    assert p["2026-03-30"] == {"periodo": "2026-03-30", "saidas": 1, "chegadas": 0, "km_rodado": 0}
    mes = chamar(conn, *JANELA, None, v, "mes")
    assert mes["serie"] == [{"periodo": "2026-03-01", "saidas": 5, "chegadas": 4, "km_rodado": 180}]


def test_filtro_de_unidade(conn: "psycopg.Connection") -> None:
    km_ligado(conn, True)
    v = dataset(conn)
    r = chamar(conn, *JANELA, "PU", v, "mes")
    assert r["serie"] == [{"periodo": "2026-03-01", "saidas": 1, "chegadas": 1, "km_rodado": 0}]
    assert r["por_veiculo"] == [{"veiculo_id": str(v), "saidas": 1, "chegadas": 1, "km_rodado": 0}]


def test_quilometragem_desligada_omite_km(conn: "psycopg.Connection") -> None:
    km_ligado(conn, False)
    v = dataset(conn)
    r = chamar(conn, *JANELA, None, v, "dia")
    assert set(r) == CHAVES and r["quilometragem_habilitada"] is False
    assert all("km_rodado" not in x and set(x) == {"periodo", "saidas", "chegadas"} for x in r["serie"])
    assert r["por_veiculo"] == [{"veiculo_id": str(v), "saidas": 5, "chegadas": 4}]


def test_estado_vazio_e_veiculo_inativo(conn: "psycopg.Connection") -> None:
    km_ligado(conn, True)
    ativo = veiculo(conn)
    r = chamar(conn, *JANELA, None, ativo, "dia")
    assert len(r["serie"]) == 31
    assert all(x["saidas"] == 0 and x["chegadas"] == 0 and x["km_rodado"] == 0 for x in r["serie"])
    assert r["por_veiculo"] == [{"veiculo_id": str(ativo), "saidas": 0, "chegadas": 0, "km_rodado": 0}]
    # Inativo sem movimento nao aparece; inativo com movimento na janela aparece.
    inativo = veiculo(conn, ativo=False)
    assert chamar(conn, *JANELA, None, inativo, "dia")["por_veiculo"] == []
    viagem(conn, inativo, "concluida", "2026-03-05 08:00-03", "2026-03-05 10:00-03", 12)
    assert chamar(conn, *JANELA, None, inativo, "dia")["por_veiculo"] == [
        {"veiculo_id": str(inativo), "saidas": 1, "chegadas": 1, "km_rodado": 12}]


def test_sem_dado_pessoal(conn: "psycopg.Connection") -> None:
    km_ligado(conn, True)
    v = dataset(conn)
    texto = str(chamar(conn, *JANELA, None, v, "dia"))
    for proibido in ("Teste", "A-B", "nome", "trajeto", "observacao", "email", "telefone"):
        assert proibido not in texto
