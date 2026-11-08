"""Testes da migration 20261108120000 (dashboards: indices e RPCs dash_*).

Somente banco local/dev DESCARTAVEL (TEST_DATABASE_URL + TEST_DATABASE_CONFIRM=descartavel + marcador
`comment on database ... is 'sisveiculos:descartavel'`; sem eles os testes sao pulados). Ver dbhelpers.py.

Como os carimbos (criado_em, autorizado_em, saida_real_em...) sao do servidor e imutaveis, o dataset
controlado e inserido numa transacao com session_replication_role = replica + DISABLE TRIGGER USER nas
tabelas tocadas (CHECKs e indices continuam valendo) e SEMPRE terminada em ROLLBACK: nada e gravado, a
auditoria append-only nao e tocada. Se o papel de conexao nao puder, os testes de dados sao pulados.
ATENCAO: o DISABLE TRIGGER pega lock forte nas tabelas durante o teste: nao rode em paralelo.
Cada dataset usa veiculo proprio e o filtro p_veiculo, entao dados de outros testes nao interferem.

    pytest supabase/tests/test_dashboards.py -m db_local -p no:xdist
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261108120000_dashboards.sql"
).read_text(encoding="utf-8")

JANELA = ("2026-03-01 00:00:00-03", "2026-04-01 00:00:00-03")  # 31 dias, fuso de Sao Paulo
ASSINATURAS = {
    "dash_operacao_agora": "public.dash_operacao_agora(public.unidade, uuid)",
    "dash_volume_desfecho": "public.dash_volume_desfecho(timestamptz, timestamptz, public.unidade, uuid, text)",
    "dash_tempos_fluxo": "public.dash_tempos_fluxo(timestamptz, timestamptz, public.unidade, uuid)",
    "dash_uso_unidade": "public.dash_uso_unidade(timestamptz, timestamptz, public.unidade, uuid)",
    "dash_uso_veiculo": "public.dash_uso_veiculo(timestamptz, timestamptz, public.unidade, uuid)",
    "dash_governanca": "public.dash_governanca()",
    "dash_opcionais": "public.dash_opcionais(timestamptz, timestamptz, public.unidade, uuid)",
}
CHAMADAS = {
    "dash_operacao_agora": "select public.dash_operacao_agora(null, null)",
    "dash_volume_desfecho": "select public.dash_volume_desfecho(now() - interval '1 day', now(), null, null, 'dia')",
    "dash_tempos_fluxo": "select public.dash_tempos_fluxo(now() - interval '1 day', now(), null, null)",
    "dash_uso_unidade": "select public.dash_uso_unidade(now() - interval '1 day', now(), null, null)",
    "dash_uso_veiculo": "select public.dash_uso_veiculo(now() - interval '1 day', now(), null, null)",
    "dash_governanca": "select public.dash_governanca()",
    "dash_opcionais": "select public.dash_opcionais(now() - interval '1 day', now(), null, null)",
}


# --- fixture: transacao descartavel com triggers desligados -----------------------------------
@pytest.fixture
def conn() -> Iterator["psycopg.Connection"]:
    assert h.DSN
    c = psycopg.connect(h.DSN)  # sem autocommit: tudo termina em rollback
    try:
        try:
            c.execute("set local session_replication_role = replica")
            # Os gatilhos das migrations sao ENABLE ALWAYS (replica nao os desliga): desliga-os so nesta
            # transacao (DDL transacional; o rollback restaura). Exige ser dono das tabelas.
            for t in ("requisicoes", "veiculos", "checklists", "configuracoes"):
                c.execute(f"alter table public.{t} disable trigger user")
        except psycopg.Error:
            c.rollback()
            pytest.skip("papel de conexao sem permissao para desligar gatilhos na transacao de teste")
        yield c
    finally:
        c.rollback()
        c.close()


def veiculo(c: "psycopg.Connection", ativo: bool = True) -> uuid.UUID:
    vid = uuid.uuid4()
    c.execute(
        "insert into public.veiculos (id, descricao, ativo) values (%s, %s, %s)",
        (vid, f"dash-{vid.hex[:8]}", ativo),
    )
    return vid


def req(c: "psycopg.Connection", vid: uuid.UUID, status: str, unidade: str = "UEA", **ts: object) -> uuid.UUID:
    """Insere requisicao com carimbos controlados. ts: criado_em, hora_saida, autorizado_em,
    saida_real_em, chegada_real_em, cancelada_em (strings de timestamptz ou SQL via ts['..._sql'])."""
    rid, ator = uuid.uuid4(), uuid.uuid4()
    cols = {
        "id": rid,
        "usuario_id": uuid.uuid4(),
        "nome_solicitante": "Teste",
        "unidade": unidade,
        "trajeto": "A-B",
        "veiculo_id": vid,
        "status": status,
        "hora_saida": ts.get("hora_saida") or ts["criado_em"],
        "criado_em": ts["criado_em"],
    }
    if ts.get("autorizado_em"):
        cols["autorizado_por"], cols["autorizado_em"] = ator, ts["autorizado_em"]
    if ts.get("saida_real_em"):
        cols["saida_registrada_por"], cols["saida_real_em"] = ator, ts["saida_real_em"]
    if ts.get("chegada_real_em"):
        cols["chegada_registrada_por"], cols["chegada_real_em"] = ator, ts["chegada_real_em"]
    if ts.get("cancelada_em"):
        cols["cancelada_por"], cols["cancelada_em"] = ator, ts["cancelada_em"]
    nomes = ", ".join(cols)
    marcas = ", ".join(["%s"] * len(cols))
    c.execute(f"insert into public.requisicoes ({nomes}) values ({marcas})", tuple(cols.values()))
    return rid


def chamar(c: "psycopg.Connection", sql: str, *args: object) -> dict:
    row = c.execute(sql, args).fetchone()
    assert row is not None
    return row[0]


def dataset(c: "psycopg.Connection") -> uuid.UUID:
    """Dataset controlado (veiculo novo). Janela JANELA = [01/03 00:00-03, 01/04 00:00-03).

    R1 PU  concluida: cria 10/03 10:00, autoriza 10:10 (600s), prevista 11:00, saiu 11:05 (+300s), voltou 13:05 (7200s)
    R2 PU  concluida: cria 10/03 23:30 -03 (11/03 02:30 UTC: dia LOCAL 10), autoriza 23:50 (1200s),
                      prevista 11/03 08:00, saiu 07:50 (-600s), voltou 09:20 (5400s)
    R3 CJU negada:    cria 11/03 09:00, decide 09:30
    R4 PF  cancelada: cria 12/03 09:00
    R6 PF  concluida: cria 31/03 23:30 -03 (dentro), autoriza 23:40 (600s), saiu 01/04 00:30 (FORA da janela)
    R7 UEA concluida: cria 28/02 23:30 (FORA), saiu 01/03 00:10 (DENTRO), prevista 00:00 (+600s), voltou 01:10 (3600s)
    """
    v = veiculo(c)
    req(c, v, "concluida", "PU", criado_em="2026-03-10 10:00-03", autorizado_em="2026-03-10 10:10-03",
        hora_saida="2026-03-10 11:00-03", saida_real_em="2026-03-10 11:05-03", chegada_real_em="2026-03-10 13:05-03")
    req(c, v, "concluida", "PU", criado_em="2026-03-10 23:30-03", autorizado_em="2026-03-10 23:50-03",
        hora_saida="2026-03-11 08:00-03", saida_real_em="2026-03-11 07:50-03", chegada_real_em="2026-03-11 09:20-03")
    req(c, v, "negada", "CJU", criado_em="2026-03-11 09:00-03", autorizado_em="2026-03-11 09:30-03")
    req(c, v, "cancelada", "PF", criado_em="2026-03-12 09:00-03", cancelada_em="2026-03-12 09:05-03")
    req(c, v, "concluida", "PF", criado_em="2026-03-31 23:30-03", autorizado_em="2026-03-31 23:40-03",
        hora_saida="2026-04-01 00:00-03", saida_real_em="2026-04-01 00:30-03", chegada_real_em="2026-04-01 02:00-03")
    req(c, v, "concluida", "UEA", criado_em="2026-02-28 23:30-03", autorizado_em="2026-02-28 23:40-03",
        hora_saida="2026-03-01 00:00-03", saida_real_em="2026-03-01 00:10-03", chegada_real_em="2026-03-01 01:10-03")
    return v


# --- estrutura e permissoes ---------------------------------------------------------------------
def test_indices_criados() -> None:
    with h.conectar() as c:
        nomes = {r[0] for r in c.execute(
            "select indexname from pg_indexes where schemaname='public' and tablename='requisicoes'"
        ).fetchall()}
    assert {"ix_requisicoes_criado_em", "ix_requisicoes_unidade_criado_em",
            "ix_requisicoes_saida_real_em", "ix_requisicoes_abertas_criado_em"} <= nomes


def test_migration_e_idempotente(conn: "psycopg.Connection") -> None:
    conn.execute(MIGRATION)
    conn.execute(MIGRATION)


@pytest.mark.parametrize("papel", ["anon", "authenticated"])
@pytest.mark.parametrize("nome", sorted(CHAMADAS))
def test_anon_e_authenticated_sem_execute(papel: str, nome: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with h.como(papel, uuid.uuid4()) as c:
            c.execute(CHAMADAS[nome])


@pytest.mark.parametrize("nome", sorted(CHAMADAS))
def test_service_role_executa_e_public_nao(nome: str) -> None:
    with h.como("service_role") as c:
        assert c.execute(CHAMADAS[nome]).fetchone() is not None
    with h.conectar() as c:
        f = ASSINATURAS[nome]
        assert c.execute("select has_function_privilege('anon', %s::regprocedure, 'execute')", (f,)).fetchone() == (False,)
        assert c.execute(
            "select exists (select 1 from pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
            "where p.oid = %s::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE')", (f,)
        ).fetchone() == (False,)
        assert c.execute(
            "select prosecdef and array_to_string(proconfig, ',') like '%%search_path=%%' "
            "from pg_proc where oid = %s::regprocedure", (f,)
        ).fetchone() == (True,)


# --- janela e parametros invalidos (AG023) -------------------------------------------------------
@pytest.mark.parametrize(
    "inicio,fim",
    [
        ("2026-03-01 00:00-03", "2026-03-01 00:00-03"),  # fim = inicio
        ("2026-03-02 00:00-03", "2026-03-01 00:00-03"),  # fim < inicio
        ("2026-01-01 00:00-03", "2027-01-03 00:00-03"),  # 367 dias
        (None, "2026-03-01 00:00-03"),
        ("2026-03-01 00:00-03", None),
    ],
)
@pytest.mark.parametrize("nome", ["dash_volume_desfecho", "dash_tempos_fluxo", "dash_uso_unidade",
                                  "dash_uso_veiculo", "dash_opcionais"])
def test_janela_invalida(conn: "psycopg.Connection", nome: str, inicio: str | None, fim: str | None) -> None:
    extra = ", 'dia'" if nome == "dash_volume_desfecho" else ""
    with pytest.raises(psycopg.Error) as e:
        conn.execute(
            f"select public.{nome}(%s::timestamptz, %s::timestamptz, null, null{extra})", (inicio, fim)
        )
    assert h.sqlstate(e) == "AG023"


def test_janela_de_366_dias_e_aceita(conn: "psycopg.Connection") -> None:
    r = chamar(conn, "select public.dash_volume_desfecho(%s::timestamptz, %s::timestamptz, null, null, 'mes')",
               "2026-01-01 00:00-03", "2027-01-02 00:00-03")
    assert r["totais"]["total"] >= 0


def test_granularidade_invalida(conn: "psycopg.Connection") -> None:
    for g in ("ano", "", None, "DIA"):
        with pytest.raises(psycopg.Error) as e:
            conn.execute("select public.dash_volume_desfecho(%s::timestamptz, %s::timestamptz, null, null, %s)",
                         (*JANELA, g))
        assert h.sqlstate(e) == "AG023"
        conn.rollback()
        conn.execute("set local session_replication_role = replica")  # nova transacao (so p/ proximo caso)


# --- volume e desfecho + fuso -----------------------------------------------------------------
def test_volume_desfecho_dia_com_limite_de_fuso(conn: "psycopg.Connection") -> None:
    v = dataset(conn)
    r = chamar(conn, "select public.dash_volume_desfecho(%s::timestamptz, %s::timestamptz, null, %s, 'dia')", *JANELA, v)
    t = r["totais"]
    assert (t["total"], t["autorizadas"], t["negadas"], t["canceladas"], t["solicitadas_pendentes"]) == (5, 3, 1, 1, 0)
    assert (t["pct_autorizadas"], t["pct_negadas"], t["pct_canceladas"]) == (60.0, 20.0, 20.0)
    serie = {p["periodo"]: p for p in r["serie"]}
    assert len(r["serie"]) == 31 and r["fuso"] == "America/Sao_Paulo"
    assert serie["2026-03-10"]["total"] == 2 and serie["2026-03-10"]["autorizadas"] == 2  # 23:30 -03 fica no dia 10
    assert "2026-03-11" in serie and serie["2026-03-11"]["total"] == 1 and serie["2026-03-11"]["negadas"] == 1
    assert serie["2026-03-12"]["canceladas"] == 1
    assert serie["2026-03-31"]["total"] == 1 and serie["2026-03-31"]["autorizadas"] == 1
    assert sum(p["total"] for p in r["serie"]) == 5
    assert serie["2026-03-20"]["total"] == 0  # periodo sem ocorrencia preenchido com zero


def test_volume_semana_e_mes(conn: "psycopg.Connection") -> None:
    v = dataset(conn)
    sem = chamar(conn, "select public.dash_volume_desfecho(%s::timestamptz, %s::timestamptz, null, %s, 'semana')", *JANELA, v)
    por = {p["periodo"]: p["total"] for p in sem["serie"]}
    # 01/03/2026 e domingo: a semana (segunda) comeca em 23/02.
    assert list(por) == ["2026-02-23", "2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23", "2026-03-30"]
    assert por["2026-03-09"] == 4 and por["2026-03-30"] == 1
    mes = chamar(conn, "select public.dash_volume_desfecho(%s::timestamptz, %s::timestamptz, null, %s, 'mes')", *JANELA, v)
    assert mes["serie"] == [{"periodo": "2026-03-01", "total": 5, "autorizadas": 3, "negadas": 1, "canceladas": 1}]


def test_volume_filtro_de_unidade(conn: "psycopg.Connection") -> None:
    v = dataset(conn)
    r = chamar(conn, "select public.dash_volume_desfecho(%s::timestamptz, %s::timestamptz, 'PU', %s, 'dia')", *JANELA, v)
    assert r["totais"]["total"] == 2 and r["totais"]["pct_autorizadas"] == 100.0


# --- tempos ----------------------------------------------------------------------------------------
def test_tempos_fluxo(conn: "psycopg.Connection") -> None:
    v = dataset(conn)
    r = chamar(conn, "select public.dash_tempos_fluxo(%s::timestamptz, %s::timestamptz, null, %s)", *JANELA, v)
    assert r["espera_autorizacao"] == {"n": 3, "media_s": 800, "mediana_s": 600, "max_s": 1200}  # negada excluida
    assert r["duracao_uso"] == {"n": 3, "media_s": 5400, "mediana_s": 5400, "max_s": 7200}
    assert r["saida_vs_prevista"] == {"n": 3, "media_s": 100, "mediana_s": 300, "min_s": -600, "max_s": 600}


# --- uso por unidade e por veiculo -------------------------------------------------------------
def test_uso_unidade(conn: "psycopg.Connection") -> None:
    v = dataset(conn)
    r = chamar(conn, "select public.dash_uso_unidade(%s::timestamptz, %s::timestamptz, null, %s)", *JANELA, v)
    assert [i["unidade"] for i in r["itens"]] == ["PU", "PF", "CJU", "UEA"]
    por = {i["unidade"]: i for i in r["itens"]}
    assert por["PU"] == {"unidade": "PU", "requisicoes": 2, "usos": 2, "horas_uso": 3.5}
    assert por["PF"] == {"unidade": "PF", "requisicoes": 2, "usos": 0, "horas_uso": 0}  # R6 saiu fora da janela
    assert por["CJU"]["requisicoes"] == 1 and por["CJU"]["usos"] == 0
    assert por["UEA"] == {"unidade": "UEA", "requisicoes": 0, "usos": 1, "horas_uso": 1.0}  # R7 criada fora, saiu dentro
    so_pf = chamar(conn, "select public.dash_uso_unidade(%s::timestamptz, %s::timestamptz, 'PF', %s)", *JANELA, v)
    assert [i["unidade"] for i in so_pf["itens"]] == ["PF"]


def test_uso_veiculo_e_ocupacao(conn: "psycopg.Connection") -> None:
    v = dataset(conn)
    r = chamar(conn, "select public.dash_uso_veiculo(%s::timestamptz, %s::timestamptz, null, %s)", *JANELA, v)
    assert r["horas_periodo"] == 744.0
    (i,) = r["itens"]
    assert i["veiculo_id"] == str(v) and i["usos"] == 3
    assert i["horas_uso"] == 4.5 and i["horas_ocupadas"] == 4.5
    assert i["taxa_ocupacao"] == pytest.approx(4.5 / 744, abs=1e-4)


def test_ocupacao_corta_ao_periodo(conn: "psycopg.Connection") -> None:
    v = dataset(conn)
    # Janela comeca 00:30: R7 (00:10 a 01:10) conta so 40 min; nao e "uso" da janela (saiu antes).
    r = chamar(conn, "select public.dash_uso_veiculo(%s::timestamptz, %s::timestamptz, null, %s)",
               "2026-03-01 00:30-03", "2026-03-02 00:00-03", v)
    (i,) = r["itens"]
    assert i["usos"] == 0 and i["horas_ocupadas"] == pytest.approx(0.67, abs=0.01)
    assert i["taxa_ocupacao"] == pytest.approx(2400 / 84600, abs=1e-4)


# --- operacao agora --------------------------------------------------------------------------------
def test_operacao_agora(conn: "psycopg.Connection") -> None:
    antes = chamar(conn, "select public.dash_operacao_agora(null, null)")
    agora = conn.execute("select now()").fetchone()[0]  # now() e fixo na transacao: igual ao da RPC
    mins = lambda n: agora - timedelta(minutes=n)  # noqa: E731
    va, vb, vc, vd, vf = (veiculo(conn) for _ in range(5))
    veiculo(conn, ativo=False)
    ra = req(conn, va, "solicitada", "PU", criado_em=mins(30))
    rf = req(conn, vf, "solicitada", "PU", criado_em=mins(50))
    rb = req(conn, vb, "autorizada", "PU", criado_em=mins(60), autorizado_em=mins(10))
    rc = req(conn, vc, "em_uso", "PU", criado_em=mins(180), autorizado_em=mins(150), saida_real_em=mins(120))
    assert vd  # livre
    depois = chamar(conn, "select public.dash_operacao_agora(null, null)")
    for chave, delta in (("ativos", 5), ("livres", 1), ("reservados", 3), ("em_uso", 1)):
        assert depois["veiculos"][chave] - antes["veiculos"][chave] == delta, chave
    assert depois["solicitadas"]["total"] - antes["solicitadas"]["total"] == 2
    assert depois["autorizadas_sem_saida"]["total"] - antes["autorizadas_sem_saida"]["total"] == 1
    assert depois["em_uso"]["total"] - antes["em_uso"]["total"] == 1
    meus = [a for a in depois["abertas"] if a["id"] in {str(x) for x in (ra, rf, rb, rc)}]
    assert [a["id"] for a in meus] == [str(x) for x in (rf, ra, rb, rc)]  # solicitadas mais antigas primeiro
    por = {a["id"]: a for a in meus}
    assert 2990 <= por[str(rf)]["decorrido_s"] <= 3010
    assert 590 <= por[str(rb)]["decorrido_s"] <= 610  # desde a autorizacao, nao desde a criacao
    assert 7190 <= por[str(rc)]["decorrido_s"] <= 7210  # desde a saida real
    assert por[str(ra)]["status"] == "solicitada" and "atrasado" not in str(depois)
    # Filtros: p_veiculo isola a frota; p_unidade so filtra requisicoes (frota continua real).
    um = chamar(conn, "select public.dash_operacao_agora(null, %s)", va)
    assert um["veiculos"] == {"ativos": 1, "livres": 0, "reservados": 1, "em_uso": 0}
    assert um["solicitadas"]["total"] == 1 and um["solicitadas"]["mais_antiga"]["id"] == str(ra)
    outra = chamar(conn, "select public.dash_operacao_agora('CJU', %s)", va)
    assert outra["solicitadas"]["total"] == 0 and outra["abertas"] == []
    assert outra["veiculos"]["reservados"] == 1


# --- estado vazio -------------------------------------------------------------------------------
def test_estado_vazio(conn: "psycopg.Connection") -> None:
    v = veiculo(conn)
    vol = chamar(conn, "select public.dash_volume_desfecho(%s::timestamptz, %s::timestamptz, null, %s, 'dia')", *JANELA, v)
    assert vol["totais"] == {"total": 0, "autorizadas": 0, "negadas": 0, "canceladas": 0,
                             "solicitadas_pendentes": 0, "pct_autorizadas": None, "pct_negadas": None,
                             "pct_canceladas": None}
    assert len(vol["serie"]) == 31 and all(p["total"] == 0 for p in vol["serie"])
    tem = chamar(conn, "select public.dash_tempos_fluxo(%s::timestamptz, %s::timestamptz, null, %s)", *JANELA, v)
    assert tem["espera_autorizacao"] == {"n": 0, "media_s": None, "mediana_s": None, "max_s": None}
    assert tem["saida_vs_prevista"]["n"] == 0 and tem["saida_vs_prevista"]["min_s"] is None
    uni = chamar(conn, "select public.dash_uso_unidade(%s::timestamptz, %s::timestamptz, null, %s)", *JANELA, v)
    assert len(uni["itens"]) == 4 and all(i["requisicoes"] == 0 and i["horas_uso"] == 0 for i in uni["itens"])
    vei = chamar(conn, "select public.dash_uso_veiculo(%s::timestamptz, %s::timestamptz, null, %s)", *JANELA, v)
    assert [(i["usos"], i["taxa_ocupacao"]) for i in vei["itens"]] == [(0, 0)]
    inexistente = chamar(conn, "select public.dash_uso_veiculo(%s::timestamptz, %s::timestamptz, null, %s)",
                         *JANELA, uuid.uuid4())
    assert inexistente["itens"] == []
    op = chamar(conn, "select public.dash_operacao_agora(null, %s)", uuid.uuid4())
    assert op["veiculos"] == {"ativos": 0, "livres": 0, "reservados": 0, "em_uso": 0}
    assert op["solicitadas"] == {"total": 0, "mais_antiga": None} and op["abertas"] == []
    assert op["em_uso"] == {"total": 0, "decorrido_medio_s": None, "decorrido_max_s": None}


# --- governanca e opcionais ------------------------------------------------------------------------
def test_governanca_estrutura_e_sem_pii(conn: "psycopg.Connection") -> None:
    r = chamar(conn, "select public.dash_governanca()")
    assert set(r) == {"gerado_em", "perfis", "convites", "auditoria_admin", "configuracoes"}
    assert set(r["perfis"]) == {"admin", "autorizador", "solicitante"}
    assert set(r["convites"]) == {"pendentes", "expirados", "usados", "revogados"}
    assert set(r["configuracoes"]) == {"quilometragem", "abastecimento_manutencao", "checklist", "mfa"}
    assert len(r["auditoria_admin"]) <= 20
    for a in r["auditoria_admin"]:
        assert set(a) == {"id", "ocorrido_em", "tabela", "registro_id", "acao", "ator_id"}
        assert a["tabela"] in {"perfis", "veiculos", "convites", "configuracoes", "perfil_fotos"}
    ids = [a["id"] for a in r["auditoria_admin"]]
    assert ids == sorted(ids, reverse=True)
    esperado = conn.execute("select count(*) filter (where role = 'admin' and ativo) from public.perfis").fetchone()
    assert r["perfis"]["admin"]["ativos"] == esperado[0]  # type: ignore[index]


def test_opcionais_desligados_nao_expoem_dados(conn: "psycopg.Connection") -> None:
    conn.execute("update public.configuracoes set quilometragem = false, checklist = false, "
                 "abastecimento_manutencao = false")
    v = dataset(conn)
    r = chamar(conn, "select public.dash_opcionais(%s::timestamptz, %s::timestamptz, null, %s)", *JANELA, v)
    assert r["quilometragem"] == {"habilitado": False} and r["checklist"] == {"habilitado": False}


def test_opcionais_ligados(conn: "psycopg.Connection") -> None:
    conn.execute("update public.configuracoes set quilometragem = true, checklist = true")
    v = dataset(conn)
    # km: R1 100 km e R2 50 km (dentro); R7 sem km; R6 fora da janela.
    conn.execute("update public.requisicoes set km_saida = 1000, km_chegada = 1100 "
                 "where veiculo_id = %s and criado_em = '2026-03-10 10:00-03'", (v,))
    conn.execute("update public.requisicoes set km_saida = 1100, km_chegada = 1150 "
                 "where veiculo_id = %s and criado_em = '2026-03-10 23:30-03'", (v,))
    r1, r2 = [x[0] for x in conn.execute(
        "select id from public.requisicoes where veiculo_id = %s and criado_em in "
        "('2026-03-10 10:00-03', '2026-03-10 23:30-03') order by criado_em", (v,)).fetchall()]
    conn.execute(
        "insert into public.checklists (requisicao_id, momento, combustivel, avarias, descricao_avarias, "
        "registrado_por, registrado_em) values "
        "(%s, 'saida', 'cheio', false, null, %s, '2026-03-10 11:05-03'), "
        "(%s, 'chegada', 'meio', true, 'amassado na porta', %s, '2026-03-11 09:20-03')",
        (r1, uuid.uuid4(), r2, uuid.uuid4()),
    )
    r = chamar(conn, "select public.dash_opcionais(%s::timestamptz, %s::timestamptz, null, %s)", *JANELA, v)
    assert r["quilometragem"] == {"habilitado": True, "km_rodado": 150, "viagens": 2}
    ck = r["checklist"]
    assert ck["habilitado"] is True and ck["total"] == 2 and ck["com_avaria"] == 1
    assert ck["requisicoes_com_avaria"] == [
        {"requisicao_id": str(r2), "momento": "chegada", "registrado_em": ck["requisicoes_com_avaria"][0]["registrado_em"]}
    ]
    assert "amassado" not in str(r)  # texto livre nunca sai
