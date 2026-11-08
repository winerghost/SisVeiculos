"""Testes da migration 20261109120000 (relatorios: RPCs rel_*, envios_email, auditoria, configuracoes).

Somente banco local/dev DESCARTAVEL (TEST_DATABASE_URL + TEST_DATABASE_CONFIRM=descartavel + marcador
`comment on database ... is 'sisveiculos:descartavel'`; sem eles os testes sao pulados). Ver dbhelpers.py.

Dois tipos de fixture (sempre terminam em ROLLBACK, nada e gravado, salvo o teste de concorrencia):
  * `conn`  : session_replication_role = replica + DISABLE TRIGGER USER nas tabelas do dataset, para
              injetar carimbos controlados (criado_em, autorizado_em...) e perfis sem Auth. CHECKs e
              indices continuam valendo. Dataset em veiculo proprio + filtro p_veiculo.
  * `real`  : triggers e FKs ativos (envios_email, auditoria, configuracoes_validar). Usa contas reais
              (h.criar_usuario, persistentes como nos outros testes) e admin real como responsavel.
ATENCAO: o DISABLE TRIGGER pega lock forte durante o teste: nao rode em paralelo (-p no:xdist).

    pytest supabase/tests/test_relatorios.py -m db_local -p no:xdist
"""

from __future__ import annotations

import json
import threading
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
    Path(__file__).resolve().parents[1] / "migrations" / "20261109120000_relatorios.sql"
).read_text(encoding="utf-8")

JANELA = ("2026-03-01 00:00:00-03", "2026-04-01 00:00:00-03")  # 31 dias, fuso de Sao Paulo
ASSINATURAS = {
    "rel_movimentacao": "public.rel_movimentacao(timestamptz, timestamptz, text, public.unidade, uuid, uuid, boolean, boolean, integer)",
    "rel_resumo": "public.rel_resumo(timestamptz, timestamptz, text, public.unidade, uuid, uuid)",
    "rel_tempos": "public.rel_tempos(timestamptz, timestamptz, text, public.unidade, uuid, uuid)",
    "rel_pendencias": "public.rel_pendencias(public.unidade, uuid, integer, uuid, boolean)",
    "rel_auditoria": "public.rel_auditoria(timestamptz, timestamptz, text, integer)",
    "rel_opcionais_km": "public.rel_opcionais_km(timestamptz, timestamptz, public.unidade, uuid, uuid, integer)",
    "rel_opcionais_avarias": "public.rel_opcionais_avarias(timestamptz, timestamptz, public.unidade, uuid, uuid, boolean, integer)",
    "reservar_envio_email": "public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer, integer)",
    "marcar_envio_email": "public.marcar_envio_email(uuid, text, text)",
}
_JAN = "now() - interval '1 day', now()"
CHAMADAS = {
    "rel_movimentacao": f"select public.rel_movimentacao({_JAN})",
    "rel_resumo": f"select public.rel_resumo({_JAN})",
    "rel_tempos": f"select public.rel_tempos({_JAN})",
    "rel_pendencias": "select public.rel_pendencias()",
    "rel_auditoria": f"select public.rel_auditoria({_JAN})",
    "rel_opcionais_km": f"select public.rel_opcionais_km({_JAN})",
    "rel_opcionais_avarias": f"select public.rel_opcionais_avarias({_JAN})",
    "reservar_envio_email": "select public.reservar_envio_email(gen_random_uuid(), gen_random_uuid(), 'resumo', 'csv', '{}', 10)",
    "marcar_envio_email": "select public.marcar_envio_email(gen_random_uuid(), 'enviando', null)",
}
ACOES_ANTIGAS = [
    "criada", "transicao", "convite_criado", "convite_usado", "convite_revogado", "perfil_criado",
    "perfil_alterado", "perfil_unidade_alterada", "admin_semeado", "configuracao_alterada",
    "veiculo_criado", "veiculo_alterado", "acao_administrativa", "acesso_negado",
    "checklist_registrado", "foto_perfil_alterada", "foto_perfil_removida",
]
TABELAS_ANTIGAS = ["requisicoes", "convites", "perfis", "veiculos", "configuracoes", "checklists", "perfil_fotos"]


# --- fixtures ---------------------------------------------------------------------------------------
@pytest.fixture
def conn() -> Iterator["psycopg.Connection"]:
    assert h.DSN
    c = psycopg.connect(h.DSN)
    try:
        try:
            c.execute("set local session_replication_role = replica")
            for t in ("requisicoes", "veiculos", "checklists", "configuracoes", "perfis"):
                c.execute(f"alter table public.{t} disable trigger user")
        except psycopg.Error:
            c.rollback()
            pytest.skip("papel de conexao sem permissao para desligar gatilhos na transacao de teste")
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def real() -> Iterator["psycopg.Connection"]:
    assert h.DSN
    c = psycopg.connect(h.DSN)
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def chamar(c: "psycopg.Connection", sql: str, *args: object) -> dict:
    row = c.execute(sql, args).fetchone()
    assert row is not None
    return row[0]


def veiculo(c: "psycopg.Connection", placa: str | None = None, descricao: str | None = None) -> uuid.UUID:
    vid = uuid.uuid4()
    c.execute(
        "insert into public.veiculos (id, descricao, placa) values (%s, %s, %s)",
        (vid, descricao or f"rel-{vid.hex[:8]}", placa),
    )
    return vid


def perfil(c: "psycopg.Connection", nome: str, role: str = "autorizador", ativo: bool = True) -> uuid.UUID:
    pid = uuid.uuid4()
    c.execute(
        "insert into public.perfis (id, nome, unidade, role, ativo) values (%s, %s, 'UEA', %s, %s)",
        (pid, nome, role, ativo),
    )
    return pid


def req(c: "psycopg.Connection", vid: uuid.UUID, status: str, unidade: str = "UEA", *,
        dono: uuid.UUID | None = None, nome: str = "Teste", trajeto: str = "A-B",
        autor: uuid.UUID | None = None, **ts: object) -> uuid.UUID:
    rid = uuid.uuid4()
    ator = autor or uuid.uuid4()
    cols: dict[str, object] = {
        "id": rid, "usuario_id": dono or uuid.uuid4(), "nome_solicitante": nome, "unidade": unidade,
        "trajeto": trajeto, "veiculo_id": vid, "status": status,
        "hora_saida": ts.get("hora_saida") or ts["criado_em"], "criado_em": ts["criado_em"],
    }
    if ts.get("autorizado_em"):
        cols["autorizado_por"], cols["autorizado_em"] = ator, ts["autorizado_em"]
    if ts.get("saida_real_em"):
        cols["saida_registrada_por"], cols["saida_real_em"] = ator, ts["saida_real_em"]
    if ts.get("chegada_real_em"):
        cols["chegada_registrada_por"], cols["chegada_real_em"] = ator, ts["chegada_real_em"]
    if ts.get("cancelada_em"):
        cols["cancelada_por"], cols["cancelada_em"] = ator, ts["cancelada_em"]
    if "km_saida" in ts:
        cols["km_saida"] = ts["km_saida"]
    if "km_chegada" in ts:
        cols["km_chegada"] = ts["km_chegada"]
    c.execute(f"insert into public.requisicoes ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})",
              tuple(cols.values()))
    return rid


def flags(c: "psycopg.Connection", **kw: bool) -> None:
    """Liga/desliga flags de configuracoes direto (gatilhos desligados na fixture `conn`)."""
    sets = ", ".join(f"{k} = {str(v).lower()}" for k, v in kw.items())
    c.execute(f"update public.configuracoes set {sets}")


class Ds:
    """Dataset controlado (ver docstring de dataset())."""

    def __init__(self, v: uuid.UUID, dono: uuid.UUID, outro: uuid.UUID, autor: uuid.UUID, ids: dict[str, uuid.UUID]):
        self.v, self.dono, self.outro, self.autor, self.ids = v, dono, outro, autor, ids


def dataset(c: "psycopg.Connection") -> Ds:
    """Janela JANELA = [01/03 00:00-03, 01/04 00:00-03). Veiculo proprio com placa ABC1D23.

    R1 PU  concluida (dono):  cria 10/03 10:00, autoriza 10:10 (600s), prevista 11:00, saiu 11:05 (+300s),
                              voltou 13:05 (7200s); km 1000 -> 1100; trajeto 'Sede-Forum'; nome 'Ana'
    R2 PU  concluida (outro): cria 10/03 23:30 -03 (dia LOCAL 10), autoriza 23:50 (1200s), prevista 11/03 08:00,
                              saiu 07:50 (-600s), voltou 09:20 (5400s); km 1100 -> 1150
    R3 CJU negada    (outro): cria 11/03 09:00, decide 09:30
    R4 PF  cancelada (dono):  cria 12/03 09:00
    R6 PF  concluida (dono):  cria 31/03 23:30 -03 (dentro), autoriza 23:40 (600s), saiu 01/04 00:30 (FORA)
    R7 UEA concluida (outro): cria 28/02 23:30 (FORA), saiu 01/03 00:10 (DENTRO), prevista 00:00 (+600s), voltou 01:10 (3600s)
    """
    v = veiculo(c, placa="ABC1D23", descricao="Carro dos testes")
    dono, outro, autor = uuid.uuid4(), uuid.uuid4(), perfil(c, "Beto Autorizador")
    ids = {
        "R1": req(c, v, "concluida", "PU", dono=dono, nome="Ana", trajeto="Sede-Forum", autor=autor,
                  criado_em="2026-03-10 10:00-03", autorizado_em="2026-03-10 10:10-03",
                  hora_saida="2026-03-10 11:00-03", saida_real_em="2026-03-10 11:05-03",
                  chegada_real_em="2026-03-10 13:05-03", km_saida=1000, km_chegada=1100),
        "R2": req(c, v, "concluida", "PU", dono=outro, autor=autor,
                  criado_em="2026-03-10 23:30-03", autorizado_em="2026-03-10 23:50-03",
                  hora_saida="2026-03-11 08:00-03", saida_real_em="2026-03-11 07:50-03",
                  chegada_real_em="2026-03-11 09:20-03", km_saida=1100, km_chegada=1150),
        "R3": req(c, v, "negada", "CJU", dono=outro, autor=autor,
                  criado_em="2026-03-11 09:00-03", autorizado_em="2026-03-11 09:30-03"),
        "R4": req(c, v, "cancelada", "PF", dono=dono, criado_em="2026-03-12 09:00-03",
                  cancelada_em="2026-03-12 09:05-03"),
        "R6": req(c, v, "concluida", "PF", dono=dono, criado_em="2026-03-31 23:30-03",
                  autorizado_em="2026-03-31 23:40-03", hora_saida="2026-04-01 00:00-03",
                  saida_real_em="2026-04-01 00:30-03", chegada_real_em="2026-04-01 02:00-03"),
        "R7": req(c, v, "concluida", "UEA", dono=outro, criado_em="2026-02-28 23:30-03",
                  autorizado_em="2026-02-28 23:40-03", hora_saida="2026-03-01 00:00-03",
                  saida_real_em="2026-03-01 00:10-03", chegada_real_em="2026-03-01 01:10-03"),
    }
    return Ds(v, dono, outro, autor, ids)


def cod(rid: uuid.UUID) -> str:
    return str(rid)[:8]


MOV = ("select public.rel_movimentacao(%s::timestamptz, %s::timestamptz, %s, null, %s, %s, %s, %s, %s)")


def mov(c: "psycopg.Connection", d: Ds, campo: str = "criacao", usuario: uuid.UUID | None = None,
        nomes: bool = False, trajeto: bool = False, limite: int = 5001, janela: tuple[str, str] = JANELA) -> dict:
    return chamar(c, MOV, *janela, campo, d.v, usuario, nomes, trajeto, limite)


# --- estrutura e permissoes ---------------------------------------------------------------------
def test_indices_criados() -> None:
    with h.conectar() as c:
        nomes = {r[0] for r in c.execute(
            "select indexname from pg_indexes where schemaname='public' "
            "and tablename in ('requisicoes','auditoria','envios_email')").fetchall()}
    assert {"ix_requisicoes_hora_saida", "ix_auditoria_ocorrido_em", "ix_envios_email_criado_em",
            "ix_envios_email_solicitado_por_criado_em", "ix_envios_email_destinatario_id"} <= nomes


def test_migration_e_idempotente(real: "psycopg.Connection") -> None:
    real.execute(MIGRATION)
    real.execute(MIGRATION)


@pytest.mark.parametrize("papel", ["anon", "authenticated"])
@pytest.mark.parametrize("nome", sorted(CHAMADAS))
def test_anon_e_authenticated_sem_execute(papel: str, nome: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with h.como(papel, uuid.uuid4()) as c:
            c.execute(CHAMADAS[nome])


@pytest.mark.parametrize("nome", sorted(CHAMADAS))
def test_service_role_executa_e_public_nao(nome: str) -> None:
    f = ASSINATURAS[nome]
    with h.conectar() as c:
        assert c.execute("select has_function_privilege('service_role', %s::regprocedure, 'execute')", (f,)).fetchone() == (True,)
        assert c.execute("select has_function_privilege('anon', %s::regprocedure, 'execute')", (f,)).fetchone() == (False,)
        assert c.execute(
            "select exists (select 1 from pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
            "where p.oid = %s::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE')", (f,)
        ).fetchone() == (False,)
        assert c.execute(
            "select prosecdef and array_to_string(proconfig, ',') like '%%search_path=%%' "
            "from pg_proc where oid = %s::regprocedure", (f,)
        ).fetchone() == (True,)
    if nome.startswith("rel_"):
        with h.como("service_role") as c:
            assert c.execute(CHAMADAS[nome]).fetchone() is not None


def test_privado_nao_executavel_pela_api() -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with h.como("service_role") as c:
            c.execute("select privado.rel_validar_limite(10)")


def test_envios_email_rls_e_grants() -> None:
    with h.conectar() as c:
        assert c.execute("select relrowsecurity from pg_class where oid = 'public.envios_email'::regclass").fetchone() == (True,)
        for papel in ("anon", "authenticated"):
            for priv in ("select", "insert", "update", "delete"):
                assert c.execute("select has_table_privilege(%s, 'public.envios_email', %s)", (papel, priv)).fetchone() == (False,)
        for priv in ("insert", "update", "delete", "truncate"):
            assert c.execute("select has_table_privilege('service_role', 'public.envios_email', %s)", (priv,)).fetchone() == (False,)
        assert c.execute("select has_table_privilege('service_role', 'public.envios_email', 'select')").fetchone() == (True,)


# --- parametros invalidos (AG023) -------------------------------------------------------------------
JANELAS_INVALIDAS = [
    ("2026-03-01 00:00-03", "2026-03-01 00:00-03"),
    ("2026-03-02 00:00-03", "2026-03-01 00:00-03"),
    ("2026-01-01 00:00-03", "2027-01-03 00:00-03"),  # 367 dias
    (None, "2026-03-01 00:00-03"),
    ("2026-03-01 00:00-03", None),
]


@pytest.mark.parametrize("inicio,fim", JANELAS_INVALIDAS)
@pytest.mark.parametrize("nome", ["rel_movimentacao", "rel_resumo", "rel_tempos", "rel_auditoria",
                                  "rel_opcionais_km", "rel_opcionais_avarias"])
def test_janela_invalida(real: "psycopg.Connection", nome: str, inicio: str | None, fim: str | None) -> None:
    with pytest.raises(psycopg.Error) as e:
        real.execute(f"select public.{nome}(%s::timestamptz, %s::timestamptz)", (inicio, fim))
    assert h.sqlstate(e) == "AG023"


def test_janela_de_366_dias_e_aceita(real: "psycopg.Connection") -> None:
    r = chamar(real, "select public.rel_resumo(%s::timestamptz, %s::timestamptz, 'mes')",
               "2026-01-01 00:00-03", "2027-01-02 00:00-03")
    assert r["total_linhas"] >= 12


@pytest.mark.parametrize("sql", [
    "select public.rel_movimentacao(%s::timestamptz, %s::timestamptz, 'criacao', null, null, null, false, false, 0)",
    "select public.rel_movimentacao(%s::timestamptz, %s::timestamptz, 'criacao', null, null, null, false, false, 5002)",
    "select public.rel_movimentacao(%s::timestamptz, %s::timestamptz, 'criacao', null, null, null, false, false, null)",
    "select public.rel_movimentacao(%s::timestamptz, %s::timestamptz, 'outro')",
    "select public.rel_movimentacao(%s::timestamptz, %s::timestamptz, null)",
    "select public.rel_resumo(%s::timestamptz, %s::timestamptz, 'ano')",
    "select public.rel_resumo(%s::timestamptz, %s::timestamptz, null)",
    "select public.rel_tempos(%s::timestamptz, %s::timestamptz, 'dia')",
    "select public.rel_auditoria(%s::timestamptz, %s::timestamptz, 'x y')",
    "select public.rel_auditoria(%s::timestamptz, %s::timestamptz, null, 5002)",
    "select public.rel_opcionais_km(%s::timestamptz, %s::timestamptz, null, null, null, 5002)",
    "select public.rel_opcionais_avarias(%s::timestamptz, %s::timestamptz, null, null, null, false, 0)",
])
def test_parametros_invalidos(real: "psycopg.Connection", sql: str) -> None:
    with pytest.raises(psycopg.Error) as e:
        real.execute(sql, JANELA)
    assert h.sqlstate(e) == "AG023"


def test_pendencias_limite_invalido(real: "psycopg.Connection") -> None:
    for lim in (0, 5002):
        with pytest.raises(psycopg.Error) as e:
            real.execute("select public.rel_pendencias(null, null, %s)", (lim,))
        assert h.sqlstate(e) == "AG023"
        real.rollback()


def test_limite_5001_e_aceito(conn: "psycopg.Connection") -> None:
    d = dataset(conn)
    assert mov(conn, d, limite=5001)["total_linhas"] == 5


# --- movimentacao -------------------------------------------------------------------------------------
def test_movimentacao_por_criacao(conn: "psycopg.Connection") -> None:
    flags(conn, quilometragem=False)
    d = dataset(conn)
    r = mov(conn, d)
    assert r["total_linhas"] == 5 and r["truncado"] is False and r["campo_data"] == "criacao"
    assert [l["codigo"] for l in r["linhas"]] == [cod(d.ids[k]) for k in ("R1", "R2", "R3", "R4", "R6")]  # R7 fora
    l1 = r["linhas"][0]
    assert l1["unidade"] == "PU" and l1["status"] == "concluida" and l1["veiculo"] == "ABC1D23 - Carro dos testes"
    assert l1["veiculo_id"] == str(d.v)
    assert l1["espera_autorizacao_s"] == 600 and l1["duracao_uso_s"] == 7200
    assert l1["autorizada_em"] is not None and l1["hora_saida_prevista"] is not None
    l2, l3, l4 = r["linhas"][1], r["linhas"][2], r["linhas"][3]
    assert (l2["espera_autorizacao_s"], l2["duracao_uso_s"]) == (1200, 5400)
    assert l3["status"] == "negada" and l3["espera_autorizacao_s"] is None and l3["duracao_uso_s"] is None
    assert l4["status"] == "cancelada" and l4["autorizada_em"] is None and l4["saida_real_em"] is None
    assert set(l1) == set(r["colunas"])  # `colunas` descreve exatamente as chaves da linha


def test_movimentacao_por_saida_prevista_e_saida_real(conn: "psycopg.Connection") -> None:
    d = dataset(conn)
    prev = mov(conn, d, campo="saida_prevista")
    # previstas na janela: R1 10/03 11:00, R2 11/03 08:00, R3 11/03 09:00 (=criado), R4 12/03 09:00, R7 01/03 00:00; R6 (01/04) fora
    assert [l["codigo"] for l in prev["linhas"]] == [cod(d.ids[k]) for k in ("R7", "R1", "R2", "R3", "R4")]
    real_ = mov(conn, d, campo="saida_real")
    # saida real na janela: R7 (01/03 00:10), R1, R2; R6 saiu 01/04 00:30 (fora); sem saida real nao entra
    assert [l["codigo"] for l in real_["linhas"]] == [cod(d.ids[k]) for k in ("R7", "R1", "R2")]


def test_movimentacao_respeita_fuso_nas_bordas(conn: "psycopg.Connection") -> None:
    d = dataset(conn)
    # janela [10/03 23:30-03, 10/03 23:31-03): fim exclusivo, inicio inclusivo => so R2
    r = mov(conn, d, janela=("2026-03-10 23:30-03", "2026-03-10 23:31-03"))
    assert [l["codigo"] for l in r["linhas"]] == [cod(d.ids["R2"])]
    r2 = mov(conn, d, janela=("2026-03-10 23:00-03", "2026-03-10 23:30-03"))
    assert r2["linhas"] == []


def test_movimentacao_filtros_unidade_e_veiculo(conn: "psycopg.Connection") -> None:
    d = dataset(conn)
    pu = chamar(conn, MOV.replace("rel_movimentacao(%s::timestamptz, %s::timestamptz, %s, null,",
                                  "rel_movimentacao(%s::timestamptz, %s::timestamptz, %s, 'PU',"),
                *JANELA, "criacao", d.v, None, False, False, 5001)
    assert [l["codigo"] for l in pu["linhas"]] == [cod(d.ids["R1"]), cod(d.ids["R2"])]
    outro_v = veiculo(conn)
    vazio = chamar(conn, MOV, *JANELA, "criacao", outro_v, None, False, False, 5001)
    assert vazio["linhas"] == [] and vazio["total_linhas"] == 0 and vazio["truncado"] is False


def test_movimentacao_p_usuario_forca_so_do_dono(conn: "psycopg.Connection") -> None:
    d = dataset(conn)
    r = mov(conn, d, usuario=d.dono, nomes=True, trajeto=True)
    assert [l["codigo"] for l in r["linhas"]] == [cod(d.ids[k]) for k in ("R1", "R4", "R6")]
    assert all(l["solicitante"] in ("Ana", "Teste") for l in r["linhas"])
    ninguem = mov(conn, d, usuario=uuid.uuid4())
    assert ninguem["linhas"] == []
    # nenhum dado de outro usuario vaza no jsonb
    assert cod(d.ids["R2"]) not in json.dumps(r)


def test_movimentacao_flags_de_nomes_e_trajeto(conn: "psycopg.Connection") -> None:
    flags(conn, quilometragem=False)
    d = dataset(conn)
    sem = mov(conn, d)
    txt = json.dumps({"linhas": sem["linhas"], "colunas": sem["colunas"]})
    for proibido in ("Ana", "Sede-Forum", "Beto", "solicitante", "autorizado_por", "saida_por", "chegada_por", "trajeto"):
        assert proibido not in txt, proibido
    assert sem["colunas_opcionais"] == {"km": False, "nomes": False, "trajeto": False}
    com = mov(conn, d, nomes=True, trajeto=True)
    l1 = com["linhas"][0]
    assert l1["solicitante"] == "Ana" and l1["trajeto"] == "Sede-Forum"
    assert l1["autorizado_por"] == "Beto Autorizador" and l1["saida_por"] == "Beto Autorizador"
    assert l1["chegada_por"] == "Beto Autorizador"
    l4 = com["linhas"][3]  # cancelada: ninguem autorizou nem registrou
    assert l4["autorizado_por"] is None and l4["saida_por"] is None and l4["chegada_por"] is None
    assert com["colunas"][-5:] == ["solicitante", "autorizado_por", "saida_por", "chegada_por", "trajeto"]
    so_nomes = mov(conn, d, nomes=True)
    assert "trajeto" not in so_nomes["linhas"][0] and "solicitante" in so_nomes["linhas"][0]
    so_traj = mov(conn, d, trajeto=True)
    assert "trajeto" in so_traj["linhas"][0] and "solicitante" not in so_traj["linhas"][0]


def test_movimentacao_km_so_com_recurso_ligado(conn: "psycopg.Connection") -> None:
    d = dataset(conn)
    flags(conn, quilometragem=False)
    off = mov(conn, d)
    assert "km_saida" not in off["linhas"][0] and off["colunas_opcionais"]["km"] is False
    assert "km_saida" not in off["colunas"]
    flags(conn, quilometragem=True)
    on = mov(conn, d)
    assert on["colunas_opcionais"]["km"] is True
    assert (on["linhas"][0]["km_saida"], on["linhas"][0]["km_chegada"]) == (1000, 1100)
    assert on["linhas"][3]["km_saida"] is None  # cancelada
    assert set(on["linhas"][0]) == set(on["colunas"])


def test_movimentacao_truncamento_e_ordem_estavel(conn: "psycopg.Connection") -> None:
    d = dataset(conn)
    # empate de data escolhida: R3 tem hora_saida = criado_em; cria duas com o mesmo carimbo => desempate por id
    a = req(conn, d.v, "cancelada", "PU", criado_em="2026-03-20 10:00-03", cancelada_em="2026-03-20 10:05-03")
    b = req(conn, d.v, "cancelada", "PU", criado_em="2026-03-20 10:00-03", cancelada_em="2026-03-20 10:05-03")
    esperado = [cod(d.ids[k]) for k in ("R1", "R2", "R3", "R4")] + [cod(x) for x in sorted((a, b), key=str)] + [cod(d.ids["R6"])]
    cheio = mov(conn, d)
    assert cheio["total_linhas"] == 7 and cheio["truncado"] is False
    assert [l["codigo"] for l in cheio["linhas"]] == esperado
    t = mov(conn, d, limite=3)
    assert t["total_linhas"] == 3 and t["truncado"] is True
    assert [l["codigo"] for l in t["linhas"]] == esperado[:3]
    exato = mov(conn, d, limite=7)
    assert exato["total_linhas"] == 7 and exato["truncado"] is False
    seis = mov(conn, d, limite=6)
    assert seis["total_linhas"] == 6 and seis["truncado"] is True
    um = mov(conn, d, limite=1)
    assert um["total_linhas"] == 1 and um["truncado"] is True and um["linhas"][0]["codigo"] == esperado[0]


def test_movimentacao_veiculo_sem_placa_usa_descricao(conn: "psycopg.Connection") -> None:
    v = veiculo(conn, placa=None, descricao="Van sem placa")
    req(conn, v, "solicitada", "PU", criado_em="2026-03-05 10:00-03")
    r = chamar(conn, MOV, *JANELA, "criacao", v, None, False, False, 10)
    assert r["linhas"][0]["veiculo"] == "Van sem placa"


# --- resumo ---------------------------------------------------------------------------------------------
def resumo(c: "psycopg.Connection", d: Ds, agrupar: str, usuario: uuid.UUID | None = None, unidade: str | None = None) -> dict:
    return chamar(c, "select public.rel_resumo(%s::timestamptz, %s::timestamptz, %s, %s, %s, %s)",
                  *JANELA, agrupar, unidade, d.v, usuario)


def test_resumo_dia_semana_mes(conn: "psycopg.Connection") -> None:
    d = dataset(conn)
    dia = resumo(conn, d, "dia")
    assert dia["total_linhas"] == 31 and dia["truncado"] is False and dia["agrupar"] == "dia"
    por = {l["grupo"]: l for l in dia["linhas"]}
    assert por["2026-03-10"] == {"grupo": "2026-03-10", "rotulo": "2026-03-10", "requisicoes": 2, "autorizadas": 2,
                                  "negadas": 0, "canceladas": 0, "concluidas": 2, "horas_uso": 3.5}  # 23:30 -03 fica no dia 10
    assert por["2026-03-11"]["negadas"] == 1 and por["2026-03-12"]["canceladas"] == 1
    assert por["2026-03-20"]["requisicoes"] == 0 and por["2026-03-20"]["horas_uso"] == 0
    assert sum(l["requisicoes"] for l in dia["linhas"]) == 5
    sem = resumo(conn, d, "semana")
    assert [l["grupo"] for l in sem["linhas"]] == ["2026-02-23", "2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23", "2026-03-30"]
    mes = resumo(conn, d, "mes")
    # R6 e concluida criada na janela: conta (5 criadas); horas = 2 + 1.5 + 1.5 = 5.0
    assert mes["linhas"] == [{"grupo": "2026-03", "rotulo": "2026-03", "requisicoes": 5, "autorizadas": 3,
                              "negadas": 1, "canceladas": 1, "concluidas": 3, "horas_uso": 5.0}]


def test_resumo_unidade_e_veiculo(conn: "psycopg.Connection") -> None:
    d = dataset(conn)
    un = resumo(conn, d, "unidade")
    assert [l["grupo"] for l in un["linhas"]] == ["PU", "PF", "CJU", "UEA"]
    por = {l["grupo"]: l for l in un["linhas"]}
    assert (por["PU"]["requisicoes"], por["PU"]["concluidas"], por["PU"]["horas_uso"]) == (2, 2, 3.5)
    assert (por["PF"]["requisicoes"], por["PF"]["canceladas"], por["PF"]["concluidas"]) == (2, 1, 1)
    assert por["CJU"]["negadas"] == 1 and por["UEA"]["requisicoes"] == 0
    so_pf = resumo(conn, d, "unidade", unidade="PF")
    assert [l["grupo"] for l in so_pf["linhas"]] == ["PF"]
    ve = resumo(conn, d, "veiculo")
    (l,) = ve["linhas"]
    assert l["grupo"] == str(d.v) and l["rotulo"] == "ABC1D23 - Carro dos testes" and l["requisicoes"] == 5


def test_resumo_p_usuario(conn: "psycopg.Connection") -> None:
    d = dataset(conn)
    r = resumo(conn, d, "mes", usuario=d.dono)
    assert r["linhas"][0]["requisicoes"] == 3 and r["linhas"][0]["negadas"] == 0  # R1, R4, R6
    assert resumo(conn, d, "mes", usuario=uuid.uuid4())["linhas"][0]["requisicoes"] == 0


# --- tempos ---------------------------------------------------------------------------------------------
def tempos(c: "psycopg.Connection", d: Ds, agrupar: str, usuario: uuid.UUID | None = None) -> dict:
    return chamar(c, "select public.rel_tempos(%s::timestamptz, %s::timestamptz, %s, null, %s, %s)",
                  *JANELA, agrupar, d.v, usuario)


def test_tempos_por_mes(conn: "psycopg.Connection") -> None:
    d = dataset(conn)
    r = tempos(conn, d, "mes")
    (l,) = r["linhas"]
    assert l["grupo"] == "2026-03"
    assert (l["espera_n"], l["espera_media_s"], l["espera_mediana_s"], l["espera_max_s"]) == (3, 800, 600, 1200)  # negada excluida
    assert (l["uso_n"], l["uso_media_s"], l["uso_mediana_s"], l["uso_max_s"]) == (3, 5400, 5400, 7200)
    assert (l["saida_dif_n"], l["saida_dif_media_s"], l["saida_dif_mediana_s"], l["saida_dif_min_s"], l["saida_dif_max_s"]) == (3, 100, 300, -600, 600)


def test_tempos_por_unidade_e_veiculo(conn: "psycopg.Connection") -> None:
    d = dataset(conn)
    r = tempos(conn, d, "unidade")
    por = {l["grupo"]: l for l in r["linhas"]}
    assert [l["grupo"] for l in r["linhas"]] == ["PU", "PF", "UEA"]  # ordem do enum; CJU so teve negada (sem medida)
    assert por["PU"]["espera_n"] == 2 and por["PU"]["espera_media_s"] == 900 and por["PU"]["uso_media_s"] == 6300
    assert por["PF"]["espera_n"] == 1 and por["PF"]["uso_n"] == 0 and por["PF"]["uso_media_s"] is None  # R6 saiu fora
    assert por["UEA"]["espera_n"] == 0 and por["UEA"]["uso_n"] == 1 and por["UEA"]["saida_dif_media_s"] == 600
    v = tempos(conn, d, "veiculo")
    (lv,) = v["linhas"]
    assert lv["grupo"] == str(d.v) and lv["rotulo"] == "ABC1D23 - Carro dos testes" and lv["espera_n"] == 3


def test_tempos_vazio_e_p_usuario(conn: "psycopg.Connection") -> None:
    d = dataset(conn)
    assert tempos(conn, d, "mes", usuario=uuid.uuid4()) == {"agrupar": "mes", "total_linhas": 0, "truncado": False, "linhas": []}
    so_dono = tempos(conn, d, "mes", usuario=d.dono)
    assert so_dono["linhas"][0]["espera_n"] == 2 and so_dono["linhas"][0]["uso_n"] == 1  # R1 e R6; R6 saiu fora


# --- pendencias -----------------------------------------------------------------------------------------
def test_pendencias(conn: "psycopg.Connection") -> None:
    agora = conn.execute("select now()").fetchone()[0]  # type: ignore[index]
    from datetime import timedelta
    mins = lambda n: agora - timedelta(minutes=n)  # noqa: E731
    va, vb, vc, vd = (veiculo(conn) for _ in range(4))
    dono = uuid.uuid4()
    ra = req(conn, va, "solicitada", "PU", dono=dono, nome="Carla", criado_em=mins(30))
    rb = req(conn, vb, "autorizada", "PU", criado_em=mins(60), autorizado_em=mins(10))
    rc = req(conn, vc, "em_uso", "PF", criado_em=mins(180), autorizado_em=mins(150), saida_real_em=mins(120))
    req(conn, vd, "concluida", "PU", criado_em=mins(300), autorizado_em=mins(290), saida_real_em=mins(280), chegada_real_em=mins(200))
    for v, esperado in ((va, [ra]), (vb, [rb]), (vc, [rc]), (vd, [])):
        r = chamar(conn, "select public.rel_pendencias(null, %s)", v)
        assert [l["codigo"] for l in r["linhas"]] == [cod(x) for x in esperado]
    ra_l = chamar(conn, "select public.rel_pendencias(null, %s)", va)["linhas"][0]
    assert ra_l["status"] == "solicitada" and 1790 <= ra_l["decorrido_s"] <= 1810
    rb_l = chamar(conn, "select public.rel_pendencias(null, %s)", vb)["linhas"][0]
    assert 590 <= rb_l["decorrido_s"] <= 610  # desde a autorizacao
    rc_l = chamar(conn, "select public.rel_pendencias(null, %s)", vc)["linhas"][0]
    assert 7190 <= rc_l["decorrido_s"] <= 7210 and rc_l["unidade"] == "PF"  # desde a saida real
    assert "atrasado" not in json.dumps(rc_l) and "solicitante" not in rc_l
    # ordem global: solicitadas, autorizadas, em uso (contra o que ja existe, filtrando pelos 3 ids)
    todas = chamar(conn, "select public.rel_pendencias(null, null)")["linhas"]
    meus = [l["codigo"] for l in todas if l["codigo"] in {cod(ra), cod(rb), cod(rc)}]
    assert meus == [cod(ra), cod(rb), cod(rc)]
    # filtros: unidade, p_usuario, nomes
    assert chamar(conn, "select public.rel_pendencias('CJU', %s)", va)["linhas"] == []
    d_ = chamar(conn, "select public.rel_pendencias(null, %s, 5001, %s, true)", va, dono)
    assert d_["linhas"][0]["solicitante"] == "Carla" and "solicitante" in d_["colunas"]
    assert chamar(conn, "select public.rel_pendencias(null, %s, 5001, %s)", va, uuid.uuid4())["linhas"] == []


def test_pendencias_truncamento(conn: "psycopg.Connection") -> None:
    v = veiculo(conn)
    req(conn, v, "solicitada", "PU", criado_em="2026-03-05 10:00-03")
    # um unico aberto por veiculo na vida real; aqui so verificamos o limite global com a base atual
    total = chamar(conn, "select public.rel_pendencias(null, null)")["total_linhas"]
    um = chamar(conn, "select public.rel_pendencias(null, null, 1)")
    assert um["total_linhas"] == 1 and um["truncado"] is (total > 1)


# --- auditoria ------------------------------------------------------------------------------------------
def test_rel_auditoria_sem_detalhes_e_com_nome_do_ator(conn: "psycopg.Connection") -> None:
    ator = perfil(conn, "Admin Fulano", role="admin")
    conn.execute("insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes, ocorrido_em) "
                 "values ('perfis', %s, 'perfil_alterado', %s, 'a', 'b', '{\"segredo\":\"x\"}', '2026-03-05 10:00-03')",
                 (uuid.uuid4(), ator))
    conn.execute("insert into public.auditoria (tabela, registro_id, acao, ator_id, ocorrido_em) "
                 "values ('configuracoes', null, 'configuracao_alterada', null, '2026-03-05 10:00:01-03')")
    r = chamar(conn, "select public.rel_auditoria(%s::timestamptz, %s::timestamptz)", "2026-03-05 00:00-03", "2026-03-06 00:00-03")
    assert r["total_linhas"] == 2 and r["truncado"] is False
    assert r["colunas"] == ["ocorrido_em", "tabela", "acao", "de", "para", "ator_nome", "registro_id"]
    a, b = r["linhas"]
    assert set(a) == set(r["colunas"]) and a["ator_nome"] == "Admin Fulano" and (a["de"], a["para"]) == ("a", "b")
    assert b["ator_nome"] is None and b["acao"] == "configuracao_alterada"
    assert "segredo" not in json.dumps(r) and "detalhes" not in json.dumps(r)
    so = chamar(conn, "select public.rel_auditoria(%s::timestamptz, %s::timestamptz, 'perfil_alterado')", "2026-03-05 00:00-03", "2026-03-06 00:00-03")
    assert so["total_linhas"] == 1
    t = chamar(conn, "select public.rel_auditoria(%s::timestamptz, %s::timestamptz, null, 1)", "2026-03-05 00:00-03", "2026-03-06 00:00-03")
    assert t["total_linhas"] == 1 and t["truncado"] is True and t["linhas"][0]["acao"] == "perfil_alterado"


# --- opcionais ------------------------------------------------------------------------------------------
def test_opcionais_desligados_devolvem_so_habilitado_false(conn: "psycopg.Connection") -> None:
    flags(conn, quilometragem=False, checklist=False, abastecimento_manutencao=False)
    d = dataset(conn)
    km = chamar(conn, "select public.rel_opcionais_km(%s::timestamptz, %s::timestamptz, null, %s)", *JANELA, d.v)
    av = chamar(conn, "select public.rel_opcionais_avarias(%s::timestamptz, %s::timestamptz, null, %s)", *JANELA, d.v)
    assert km == {"habilitado": False} and av == {"habilitado": False}


def test_opcionais_km_ligado(conn: "psycopg.Connection") -> None:
    flags(conn, quilometragem=True)
    d = dataset(conn)
    r = chamar(conn, "select public.rel_opcionais_km(%s::timestamptz, %s::timestamptz, null, %s)", *JANELA, d.v)
    assert r["habilitado"] is True and r["totais"] == {"viagens": 2, "km_rodado": 150}
    assert [l["codigo"] for l in r["linhas"]] == [cod(d.ids["R1"]), cod(d.ids["R2"])]
    assert r["linhas"][0]["km_rodado"] == 100 and set(r["linhas"][0]) == set(r["colunas"])
    t = chamar(conn, "select public.rel_opcionais_km(%s::timestamptz, %s::timestamptz, null, %s, null, 1)", *JANELA, d.v)
    assert t["total_linhas"] == 1 and t["truncado"] is True and t["totais"]["viagens"] == 2  # totais cobrem tudo
    meu = chamar(conn, "select public.rel_opcionais_km(%s::timestamptz, %s::timestamptz, null, %s, %s)", *JANELA, d.v, d.outro)
    assert meu["totais"] == {"viagens": 1, "km_rodado": 50}


def test_opcionais_avarias_ligado(conn: "psycopg.Connection") -> None:
    flags(conn, checklist=True)
    d = dataset(conn)
    conn.execute(
        "insert into public.checklists (requisicao_id, momento, combustivel, avarias, descricao_avarias, "
        "registrado_por, registrado_em) values "
        "(%s, 'saida', 'cheio', false, null, %s, '2026-03-10 11:05-03'), "
        "(%s, 'chegada', 'meio', true, 'amassado na porta de Joao', %s, '2026-03-11 09:20-03')",
        (d.ids["R1"], d.autor, d.ids["R2"], d.autor))
    sql = ("select public.rel_opcionais_avarias(%s::timestamptz, %s::timestamptz, null, %s, %s, %s)")
    r = chamar(conn, sql, *JANELA, d.v, None, False)
    assert r["habilitado"] is True and r["totais"] == {"checklists": 2, "com_avaria": 1}
    (l,) = r["linhas"]
    assert l["codigo"] == cod(d.ids["R2"]) and l["momento"] == "chegada" and l["combustivel"] == "meio"
    assert "amassado" not in json.dumps(r) and "descricao_avarias" not in l  # texto livre so por flag
    com = chamar(conn, sql, *JANELA, d.v, None, True)
    assert com["linhas"][0]["descricao_avarias"] == "amassado na porta de Joao"
    assert com["colunas"][-1] == "descricao_avarias"
    assert chamar(conn, sql, *JANELA, d.v, d.dono, False)["linhas"] == []  # R2 e do outro usuario


# --- auditoria: novas acoes ------------------------------------------------------------------------------
@pytest.mark.parametrize("acao", ACOES_ANTIGAS + ["relatorio_gerado", "relatorio_enviado"])
def test_ck_auditoria_acao_aceita_antigas_e_novas(real: "psycopg.Connection", acao: str) -> None:
    real.execute("insert into public.auditoria (tabela, acao) values ('perfis', %s)", (acao,))


@pytest.mark.parametrize("tabela", TABELAS_ANTIGAS + ["relatorios", "envios_email"])
def test_ck_auditoria_tabela_aceita_antigas_e_novas(real: "psycopg.Connection", tabela: str) -> None:
    real.execute("insert into public.auditoria (tabela, acao) values (%s, 'acao_administrativa')", (tabela,))


def test_ck_auditoria_rejeita_desconhecidos(real: "psycopg.Connection") -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        real.execute("insert into public.auditoria (tabela, acao) values ('perfis', 'relatorio_apagado')")
    real.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        real.execute("insert into public.auditoria (tabela, acao) values ('segredos', 'acao_administrativa')")
    real.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):  # detalhes >= 2048 bytes continua barrado
        real.execute("insert into public.auditoria (tabela, acao, detalhes) values ('relatorios', 'relatorio_gerado', %s::jsonb)",
                     (json.dumps({"x": "a" * 2100}),))


def test_auditoria_continua_append_only(real: "psycopg.Connection") -> None:
    real.execute("insert into public.auditoria (tabela, acao) values ('relatorios', 'relatorio_gerado')")
    with pytest.raises(psycopg.Error) as e:
        real.execute("update public.auditoria set acao = 'transicao' where tabela = 'relatorios'")
    assert h.sqlstate(e) == "AG003"


DET_OK = {"tipo": "movimentacao", "filtros": {"unidade": "PU", "inicio": "2026-03-01"}, "formato": "csv",
          "via": "download", "total_linhas": 12}


def test_registrar_auditoria_relatorios(real: "psycopg.Connection") -> None:
    ator = h.criar_usuario("rel-aud", "autorizador")
    dest = str(uuid.uuid4())
    sql = "select public.registrar_auditoria(%s, %s, %s, %s, null, null, %s::jsonb)"
    i1 = real.execute(sql, ("relatorios", None, "relatorio_gerado", ator, json.dumps(DET_OK))).fetchone()
    assert i1 is not None and isinstance(i1[0], int)
    det_env = {**DET_OK, "via": "email", "destinatario_id": dest}
    assert real.execute(sql, ("envios_email", uuid.uuid4(), "relatorio_enviado", ator, json.dumps(det_env))).fetchone() is not None
    row = real.execute("select acao, tabela, ator_id, detalhes from public.auditoria where id = %s", (i1[0],)).fetchone()
    assert row == ("relatorio_gerado", "relatorios", ator, DET_OK)


def test_registrar_auditoria_antigas_continuam(real: "psycopg.Connection") -> None:
    ator = h.criar_usuario("rel-aud2", "autorizador")
    sql = "select public.registrar_auditoria('perfis', null, %s, %s, null, null, '{\"k\": 1}'::jsonb)"
    for acao in ("acao_administrativa", "acesso_negado"):
        assert real.execute(sql, (acao, ator)).fetchone() is not None
    for acao in ("criada", "transicao", "configuracao_alterada", "checklist_registrado", "desconhecida"):
        with pytest.raises(psycopg.Error) as e:
            real.execute(sql, (acao, ator))
        assert h.sqlstate(e) == "AG003"
        real.rollback()


@pytest.mark.parametrize("ator_ok,tabela,acao,det,esperado", [
    (False, "relatorios", "relatorio_gerado", DET_OK, "AG002"),                      # ator obrigatorio
    (True, "envios_email", "relatorio_gerado", DET_OK, "AG003"),                     # tabela trocada
    (True, "relatorios", "relatorio_enviado", DET_OK, "AG003"),
    (True, "relatorios", "relatorio_gerado", {**DET_OK, "conteudo": "linha 1"}, "AG003"),   # chave fora da lista
    (True, "relatorios", "relatorio_gerado", {**DET_OK, "email": "a"}, "AG003"),
    (True, "relatorios", "relatorio_gerado", {**DET_OK, "tipo": "fulano@gmail.com"}, "AG003"),  # '@' recusado
    (True, "relatorios", "relatorio_gerado", {"filtros": {"destino": "x@y.com"}}, "AG003"),
])
def test_registrar_auditoria_relatorio_rejeita(real: "psycopg.Connection", ator_ok: bool, tabela: str,
                                                acao: str, det: dict, esperado: str) -> None:
    ator = h.criar_usuario("rel-aud3", "autorizador") if ator_ok else None
    with pytest.raises(psycopg.Error) as e:
        real.execute("select public.registrar_auditoria(%s, null, %s, %s, null, null, %s::jsonb)",
                     (tabela, acao, ator, json.dumps(det)))
    assert h.sqlstate(e) == esperado


def test_registrar_auditoria_detalhes_nao_objeto(real: "psycopg.Connection") -> None:
    ator = h.criar_usuario("rel-aud4", "autorizador")
    with pytest.raises(psycopg.Error) as e:
        real.execute("select public.registrar_auditoria('relatorios', null, 'relatorio_gerado', %s, null, null, '[1]'::jsonb)", (ator,))
    assert h.sqlstate(e) == "AG003"


def test_registrar_auditoria_so_service_role() -> None:
    for papel in ("anon", "authenticated"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with h.como(papel, uuid.uuid4()) as c:
                c.execute("select public.registrar_auditoria('relatorios', null, 'relatorio_gerado', null)")


# --- configuracoes_validar ----------------------------------------------------------------------------------
def admin_real() -> uuid.UUID:
    return h.admin_ativo_qualquer() or h.criar_usuario("rel-admin", "admin")


def estado_flags(c: "psycopg.Connection") -> tuple:
    row = c.execute("select quilometragem, abastecimento_manutencao, checklist, mfa, relatorio_email, "
                    "relatorio_whatsapp from public.configuracoes").fetchone()
    assert row is not None
    return row


def test_configuracoes_novas_colunas_padrao_false() -> None:
    with h.conectar() as c:
        rows = c.execute("select column_name, data_type, is_nullable, column_default from information_schema.columns "
                         "where table_schema='public' and table_name='configuracoes' "
                         "and column_name in ('relatorio_email','relatorio_whatsapp') order by 1").fetchall()
    assert rows == [("relatorio_email", "boolean", "NO", "false"), ("relatorio_whatsapp", "boolean", "NO", "false")]


@pytest.mark.parametrize("chave", ["relatorio_email", "relatorio_whatsapp"])
def test_configuracoes_audita_chaves_novas(real: "psycopg.Connection", chave: str) -> None:
    adm = admin_real()
    antes = real.execute("select count(*) from public.auditoria where acao='configuracao_alterada' "
                         "and detalhes->>'recurso' = %s", (chave,)).fetchone()[0]  # type: ignore[index]
    atual = real.execute(f"select {chave} from public.configuracoes").fetchone()[0]  # type: ignore[index]
    real.execute(f"update public.configuracoes set {chave} = %s, alterado_por = %s", (not atual, adm))
    row = real.execute("select de, para, ator_id, tabela from public.auditoria where acao='configuracao_alterada' "
                       "and detalhes->>'recurso' = %s order by id desc limit 1", (chave,)).fetchone()
    assert row == (str(atual).lower(), str(not atual).lower(), adm, "configuracoes")
    depois = real.execute("select count(*) from public.auditoria where acao='configuracao_alterada' "
                          "and detalhes->>'recurso' = %s", (chave,)).fetchone()[0]  # type: ignore[index]
    assert depois == antes + 1
    assert real.execute("select alterado_por from public.configuracoes").fetchone() == (None,)  # nunca persiste


@pytest.mark.parametrize("chave", ["quilometragem", "checklist", "mfa"])
def test_configuracoes_continua_auditando_chaves_antigas(real: "psycopg.Connection", chave: str) -> None:
    adm = admin_real()
    atual = real.execute(f"select {chave} from public.configuracoes").fetchone()[0]  # type: ignore[index]
    real.execute(f"update public.configuracoes set {chave} = %s, alterado_por = %s", (not atual, adm))
    row = real.execute("select de, para from public.auditoria where acao='configuracao_alterada' "
                       "and detalhes->>'recurso' = %s order by id desc limit 1", (chave,)).fetchone()
    assert row == (str(atual).lower(), str(not atual).lower())


def test_configuracoes_abastecimento_audita_e_exige_km(real: "psycopg.Connection") -> None:
    adm = admin_real()
    real.execute("update public.configuracoes set abastecimento_manutencao = false, quilometragem = false, alterado_por = %s", (adm,))
    with pytest.raises(psycopg.Error) as e:
        real.execute("update public.configuracoes set abastecimento_manutencao = true, alterado_por = %s", (adm,))
    assert h.sqlstate(e) == "AG010"
    real.rollback()
    real.execute("update public.configuracoes set quilometragem = true, alterado_por = %s", (adm,))
    real.execute("update public.configuracoes set abastecimento_manutencao = true, alterado_por = %s", (adm,))
    row = real.execute("select para from public.auditoria where acao='configuracao_alterada' "
                       "and detalhes->>'recurso' = 'abastecimento_manutencao' order by id desc limit 1").fetchone()
    assert row == ("true",)


def test_configuracoes_chaves_novas_exigem_admin_ativo(real: "psycopg.Connection") -> None:
    nao_admin = h.criar_usuario("rel-sol", "solicitante")
    for ator in (None, nao_admin):
        with pytest.raises(psycopg.Error) as e:
            real.execute("update public.configuracoes set relatorio_email = not relatorio_email, alterado_por = %s", (ator,))
        assert h.sqlstate(e) == "AG002"
        real.rollback()


def test_configuracoes_update_sem_mudanca_nao_audita(real: "psycopg.Connection") -> None:
    antes = real.execute("select count(*) from public.auditoria where tabela = 'configuracoes'").fetchone()[0]  # type: ignore[index]
    real.execute("update public.configuracoes set relatorio_email = relatorio_email, alterado_por = null")
    depois = real.execute("select count(*) from public.auditoria where tabela = 'configuracoes'").fetchone()[0]  # type: ignore[index]
    assert depois == antes


def test_configuracoes_duas_chaves_geram_duas_linhas(real: "psycopg.Connection") -> None:
    adm = admin_real()
    real.execute("update public.configuracoes set relatorio_email = false, relatorio_whatsapp = false, alterado_por = %s", (adm,))
    antes = real.execute("select max(id) from public.auditoria").fetchone()[0]  # type: ignore[index]
    real.execute("update public.configuracoes set relatorio_email = true, relatorio_whatsapp = true, alterado_por = %s", (adm,))
    novos = real.execute("select detalhes->>'recurso' from public.auditoria where id > %s order by id", (antes,)).fetchall()
    assert novos == [("relatorio_email",), ("relatorio_whatsapp",)]


# --- envios_email -------------------------------------------------------------------------------------------
@pytest.fixture
def envio(real: "psycopg.Connection") -> Iterator[dict]:
    """Contas reais + recurso relatorio_email ligado (via admin, dentro da transacao de teste)."""
    adm = admin_real()
    sol = h.criar_usuario("rel-sol", "autorizador")
    dest = h.criar_usuario("rel-dest", "solicitante")
    # contas extras criadas ANTES de abrir a transacao com o UPDATE (insert em perfis de outra conexao bloquearia)
    dest_inativo = h.criar_usuario("rel-inativo", "solicitante", ativo=False)
    sol_inativo = h.criar_usuario("rel-sol-inativo", "autorizador", ativo=False)
    real.execute("update public.configuracoes set relatorio_email = true, alterado_por = %s", (adm,))
    yield {"c": real, "sol": sol, "dest": dest, "adm": adm, "dest_inativo": dest_inativo, "sol_inativo": sol_inativo}


def reservar(e: dict, max_h: int = 1000, dest: uuid.UUID | None = None, sol: uuid.UUID | None = None,
             tipo: str = "movimentacao", formato: str = "csv", filtros: dict | None = None) -> dict:
    return chamar(e["c"], "select public.reservar_envio_email(%s, %s, %s, %s, %s::jsonb, %s)",
                  sol or e["sol"], dest or e["dest"], tipo, formato, json.dumps(filtros or {"unidade": "PU"}), max_h)


def na_hora(c: "psycopg.Connection") -> int:
    return c.execute("select count(*) from public.envios_email where criado_em > clock_timestamp() - interval '1 hour'").fetchone()[0]  # type: ignore[index]


def test_reservar_cria_pendente(envio: dict) -> None:
    r = reservar(envio)
    assert set(r) == {"id"}
    row = envio["c"].execute("select status, tentativas, erro_codigo, enviado_em, solicitado_por, destinatario_id, "
                             "tipo_relatorio, formato, filtros from public.envios_email where id = %s", (r["id"],)).fetchone()
    assert row == ("pendente", 0, None, None, envio["sol"], envio["dest"], "movimentacao", "csv", {"unidade": "PU"})


def test_reservar_destinatario_inativo_ou_inexistente(envio: dict) -> None:
    for dest in (envio["dest_inativo"], uuid.uuid4()):
        with pytest.raises(psycopg.Error) as e:
            reservar(envio, dest=dest)
        assert h.sqlstate(e) == "AG024"
        envio["c"].rollback()
        # o rollback desfaz o recurso ligado: religa para o proximo caso
        envio["c"].execute("update public.configuracoes set relatorio_email = true, alterado_por = %s", (envio["adm"],))


def test_reservar_solicitante_inativo(envio: dict) -> None:
    with pytest.raises(psycopg.Error) as e:
        reservar(envio, sol=envio["sol_inativo"])
    assert h.sqlstate(e) == "AG002"


def test_reservar_recurso_desligado(envio: dict) -> None:
    envio["c"].execute("update public.configuracoes set relatorio_email = false, alterado_por = %s", (envio["adm"],))
    with pytest.raises(psycopg.Error) as e:
        reservar(envio)
    assert h.sqlstate(e) == "AG026"


def test_reservar_cota_por_hora(envio: dict) -> None:
    base = na_hora(envio["c"])
    reservar(envio, max_h=base + 2)
    reservar(envio, max_h=base + 2)
    with pytest.raises(psycopg.Error) as e:
        with envio["c"].transaction():  # savepoint: a transacao de teste segue utilizavel
            reservar(envio, max_h=base + 2)
    assert h.sqlstate(e) == "AG025"
    assert na_hora(envio["c"]) == base + 2  # a recusada nao grava


def test_reservar_cota_so_conta_ultima_hora(envio: dict) -> None:
    c = envio["c"]
    base = na_hora(c)
    r = reservar(envio, max_h=base + 1)
    with pytest.raises(psycopg.Error):
        reservar(envio, max_h=base + 1)
    c.rollback()
    c.execute("update public.configuracoes set relatorio_email = true, alterado_por = %s", (envio["adm"],))
    r = reservar(envio, max_h=base + 1)
    # empurra o registro para fora da janela: criado_em e imutavel por trigger, entao desliga so o gatilho de validacao
    c.execute("alter table public.envios_email disable trigger trg_envios_email_validar")
    c.execute("update public.envios_email set criado_em = clock_timestamp() - interval '61 minutes' where id = %s", (r["id"],))
    assert na_hora(c) == base
    reservar(envio, max_h=base + 1)  # volta a caber


@pytest.mark.parametrize("kw", [
    {"tipo": "Movimentacao"}, {"tipo": ""}, {"tipo": "a" * 41}, {"formato": "pdf"}, {"formato": "CSV"},
    {"max_h": 0}, {"max_h": 1001}, {"max_h": -1},
    {"filtros": {"para": "alguem@gmail.com"}}, {"filtros": {"x": "a" * 2100}},
])
def test_reservar_parametros_invalidos(envio: dict, kw: dict) -> None:
    with pytest.raises(psycopg.Error) as e:
        reservar(envio, **kw)
    assert h.sqlstate(e) == "AG027"


def test_reservar_filtros_nao_objeto_e_nulos(envio: dict) -> None:
    c = envio["c"]
    with pytest.raises(psycopg.Error) as e:
        c.execute("select public.reservar_envio_email(%s, %s, 'resumo', 'csv', '[1]'::jsonb, 10)", (envio["sol"], envio["dest"]))
    assert h.sqlstate(e) == "AG027"
    c.rollback()
    with pytest.raises(psycopg.Error) as e:
        c.execute("select public.reservar_envio_email(%s, %s, null, 'csv', '{}'::jsonb, 10)", (envio["sol"], envio["dest"]))
    assert h.sqlstate(e) == "AG027"
    c.rollback()
    c.execute("update public.configuracoes set relatorio_email = true, alterado_por = %s", (envio["adm"],))
    r = chamar(c, "select public.reservar_envio_email(%s, %s, 'resumo', 'xlsx', null, 10)", envio["sol"], envio["dest"])
    assert c.execute("select filtros, formato from public.envios_email where id = %s", (r["id"],)).fetchone() == ({}, "xlsx")


def marcar(e: dict, rid: str, status: str, erro: str | None = None) -> dict:
    return chamar(e["c"], "select public.marcar_envio_email(%s, %s, %s)", rid, status, erro)


def test_marcar_fluxo_feliz(envio: dict) -> None:
    rid = reservar(envio)["id"]
    assert marcar(envio, rid, "enviando") == {"id": rid, "status": "enviando", "tentativas": 1}
    assert marcar(envio, rid, "enviado") == {"id": rid, "status": "enviado", "tentativas": 1}
    row = envio["c"].execute("select status, erro_codigo, enviado_em is not null from public.envios_email where id = %s", (rid,)).fetchone()
    assert row == ("enviado", None, True)


def test_marcar_falha_e_nova_tentativa(envio: dict) -> None:
    rid = reservar(envio)["id"]
    marcar(envio, rid, "enviando")
    assert marcar(envio, rid, "falhou", "smtp_timeout")["status"] == "falhou"
    assert envio["c"].execute("select erro_codigo from public.envios_email where id = %s", (rid,)).fetchone() == ("smtp_timeout",)
    assert marcar(envio, rid, "enviando")["tentativas"] == 2
    assert envio["c"].execute("select erro_codigo from public.envios_email where id = %s", (rid,)).fetchone() == (None,)  # limpo na nova tentativa
    marcar(envio, rid, "enviado")
    # pendente -> falhou (nao conseguiu nem iniciar) e erro padrao
    r2 = reservar(envio)["id"]
    assert marcar(envio, r2, "falhou")["tentativas"] == 0
    assert envio["c"].execute("select erro_codigo from public.envios_email where id = %s", (r2,)).fetchone() == ("desconhecido",)


@pytest.mark.parametrize("caminho,destino", [
    (["enviando", "enviado"], "enviando"),   # enviado e terminal
    (["enviando", "enviado"], "falhou"),
    (["enviando"], "enviando"),               # mesmo estado
    ([], "enviado"),                          # pendente -> enviado
])
def test_marcar_transicao_invalida(envio: dict, caminho: list[str], destino: str) -> None:
    rid = reservar(envio)["id"]
    for s in caminho:
        marcar(envio, rid, s)
    with pytest.raises(psycopg.Error) as e:
        marcar(envio, rid, destino)
    assert h.sqlstate(e) == "AG001"


@pytest.mark.parametrize("status,erro,esperado", [
    ("pendente", None, "AG027"), ("xpto", None, "AG027"), (None, None, "AG027"),
    ("falhou", "tem espaco", "AG027"), ("falhou", "a" * 41, "AG027"), ("falhou", "x@y.com", "AG027"),
])
def test_marcar_parametros_invalidos(envio: dict, status: str | None, erro: str | None, esperado: str) -> None:
    rid = reservar(envio)["id"]
    with pytest.raises(psycopg.Error) as e:
        marcar(envio, rid, status, erro)  # type: ignore[arg-type]
    assert h.sqlstate(e) == esperado


def test_marcar_inexistente(envio: dict) -> None:
    with pytest.raises(psycopg.Error) as e:
        marcar(envio, str(uuid.uuid4()), "enviando")
    assert h.sqlstate(e) == "AG028"


def test_marcar_erro_ignorado_quando_nao_falhou(envio: dict) -> None:
    rid = reservar(envio)["id"]
    marcar(envio, rid, "enviando", "lixo")
    assert envio["c"].execute("select erro_codigo from public.envios_email where id = %s", (rid,)).fetchone() == (None,)


def test_envios_email_imutavel_sem_delete_e_insert_direto(envio: dict) -> None:
    c = envio["c"]
    rid = reservar(envio)["id"]
    for col, val in (("tipo_relatorio", "'outro'"), ("formato", "'xlsx'"), ("filtros", "'{}'::jsonb"),
                     ("destinatario_id", f"'{envio['sol']}'"), ("solicitado_por", f"'{envio['dest']}'"),
                     ("criado_em", "now() - interval '5 hours'"), ("id", "gen_random_uuid()")):
        with pytest.raises(psycopg.Error) as e:
            c.execute(f"update public.envios_email set {col} = {val} where id = %s", (rid,))
        assert h.sqlstate(e) == "AG004", col
        c.rollback()
        c.execute("update public.configuracoes set relatorio_email = true, alterado_por = %s", (envio["adm"],))
        rid = reservar(envio)["id"]
    with pytest.raises(psycopg.Error) as e:
        c.execute("delete from public.envios_email where id = %s", (rid,))
    assert h.sqlstate(e) == "AG003"
    c.rollback()
    with pytest.raises(psycopg.Error) as e:
        c.execute("truncate public.envios_email")
    assert h.sqlstate(e) == "AG003"
    c.rollback()
    # UPDATE sem mudar status (ex.: so tentativas) e recusado
    c.execute("update public.configuracoes set relatorio_email = true, alterado_por = %s", (envio["adm"],))
    rid = reservar(envio)["id"]
    with pytest.raises(psycopg.Error) as e:
        c.execute("update public.envios_email set tentativas = 5 where id = %s", (rid,))
    assert h.sqlstate(e) == "AG001"
    c.rollback()
    # INSERT direto so nasce pendente
    with pytest.raises(psycopg.Error) as e:
        c.execute("insert into public.envios_email (solicitado_por, destinatario_id, tipo_relatorio, formato, status, tentativas, enviado_em) "
                  "values (%s, %s, 'x', 'csv', 'enviado', 1, now())", (envio["sol"], envio["dest"]))
    assert h.sqlstate(e) == "AG001"


def test_envios_email_checks_de_tabela(envio: dict) -> None:
    c = envio["c"]
    base = "insert into public.envios_email (solicitado_por, destinatario_id, tipo_relatorio, formato, filtros) values (%s, %s, %s, %s, %s::jsonb)"
    for tipo, fmt, filtros in (("X", "csv", "{}"), ("ok", "pdf", "{}"), ("ok", "csv", "[]"),
                               ("ok", "csv", '{"a":"x@y.com"}'), ("ok", "csv", json.dumps({"a": "z" * 2100}))):
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute(base, (envio["sol"], envio["dest"], tipo, fmt, filtros))
        c.rollback()


def test_envios_email_fk_para_perfis(real: "psycopg.Connection") -> None:
    sol = h.criar_usuario("rel-fk", "autorizador")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        real.execute("insert into public.envios_email (solicitado_por, destinatario_id, tipo_relatorio, formato) "
                     "values (%s, %s, 'x', 'csv')", (sol, uuid.uuid4()))


def test_envios_email_inacessivel_pela_api() -> None:
    for papel in ("anon", "authenticated"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with h.como(papel, uuid.uuid4()) as c:
                c.execute("select count(*) from public.envios_email")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with h.como("service_role") as c:
            c.execute("delete from public.envios_email")


def test_cota_concorrente_so_uma_passa() -> None:
    """Duas reservas simultaneas com cota restante 1: uma passa, outra AG025 (advisory lock).

    Persiste 1 linha em envios_email (sem DELETE fisico, como nos outros testes de concorrencia) e
    restaura relatorio_email ao valor original.
    """
    adm = admin_real()
    sol = h.criar_usuario("rel-conc-s", "autorizador")
    dest = h.criar_usuario("rel-conc-d", "solicitante")
    with h.conectar() as c:
        original = c.execute("select relatorio_email from public.configuracoes").fetchone()[0]  # type: ignore[index]
        c.execute("update public.configuracoes set relatorio_email = true, alterado_por = %s", (adm,))
        base = c.execute("select count(*) from public.envios_email where criado_em > clock_timestamp() - interval '1 hour'").fetchone()[0]  # type: ignore[index]
    resultados: list[str] = []
    barreira = threading.Barrier(2, timeout=60)

    def tentar() -> None:
        with psycopg.connect(h.DSN, autocommit=True) as c2:  # type: ignore[arg-type]
            barreira.wait()
            try:
                c2.execute("select public.reservar_envio_email(%s, %s, 'resumo', 'csv', '{}'::jsonb, %s)", (sol, dest, base + 1))
                resultados.append("ok")
            except psycopg.Error as ex:
                resultados.append(getattr(ex, "sqlstate", "?") or "?")

    try:
        ts = [threading.Thread(target=tentar) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=90)
    finally:
        with h.conectar() as c:
            c.execute("update public.configuracoes set relatorio_email = %s, alterado_por = %s", (original, adm))
    assert sorted(resultados) == ["AG025", "ok"]
