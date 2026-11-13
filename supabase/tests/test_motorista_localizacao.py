"""Testes das migrations 20261112120000..0300: localizacao obrigatoria do motorista, registro de
saida/chegada pelo motorista e checklist do motorista designado.

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.
Uma fixture autouse isola as flags globais (desliga todas antes de cada teste e restaura no fim).
Cada alternancia gera linha 'configuracao_alterada' em auditoria (append-only): rodar SO em banco
descartavel e sem paralelismo.

    pytest supabase/tests/test_motorista_localizacao.py -m db_local
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

psycopg = pytest.importorskip("psycopg")

from psycopg.types.json import Jsonb  # noqa: E402

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

NEGADO = "42501"
# Ordem de desligamento (dependentes primeiro); o religamento usa a ordem inversa.
FLAGS = (
    "motorista_localizacao",
    "motorista_registra",
    "motorista",
    "checklist",
    "abastecimento_manutencao",
    "quilometragem",
)
LAT, LON = -15.793889, -47.882778  # Brasilia


def _estado() -> dict[str, bool]:
    with h.conectar() as c:
        row = c.execute(f"select {', '.join(FLAGS)} from public.configuracoes").fetchone()
    assert row is not None
    return dict(zip(FLAGS, (bool(v) for v in row), strict=True))


def _cfg(valores: dict[str, bool], por: uuid.UUID | None) -> None:
    sets = ", ".join(f"{k} = %s" for k in valores)
    with h.conectar() as c:
        c.execute(
            f"update public.configuracoes set {sets}, alterado_por = %s where id = true",
            (*valores.values(), por),
        )


@pytest.fixture(autouse=True)
def _estado_recursos() -> Iterator[None]:
    orig = _estado()
    por = h.admin_ativo_qualquer() or h.criar_usuario("admin-cfg", "admin")
    for f in FLAGS:
        if _estado()[f]:
            _cfg({f: False}, por)
    try:
        yield
    finally:
        for f in FLAGS:
            if _estado()[f]:
                _cfg({f: False}, por)
        for f in reversed(FLAGS):
            if orig[f]:
                _cfg({f: True}, por)


class Cena:
    def __init__(self) -> None:
        self.admin = h.criar_usuario("admin", "admin")
        self.dono = h.criar_usuario("dono")
        self.autorizador = h.criar_usuario("autorizador", "autorizador")
        self.motorista = h.criar_usuario("motorista", "motorista")
        self.outro = h.criar_usuario("motorista2", "motorista")

    def ligar(self, *, localizacao: bool = True) -> None:
        _cfg({"motorista": True}, self.admin)
        _cfg({"motorista_registra": True}, self.admin)
        if localizacao:
            _cfg({"motorista_localizacao": True}, self.admin)

    def autorizada(self, motorista: uuid.UUID | None = None) -> uuid.UUID:
        """Requisicao autorizada com o motorista designado (padrao: self.motorista)."""
        with h.conectar() as c:
            rid = h.inserir_requisicao(c, self.dono, h.novo_veiculo())
        h.autorizar(rid, self.autorizador)
        with h.conectar() as c:
            c.execute(
                "update public.requisicoes set motorista_id = %s, motorista_alterado_por = %s where id = %s",
                (motorista or self.motorista, self.autorizador, rid),
            )
        return rid


@pytest.fixture
def cena() -> Cena:
    return Cena()


def _rpc(rid: uuid.UUID, momento: str, ator: uuid.UUID, lat=LAT, lon=LON, prec=None, **km) -> dict:  # type: ignore[no-untyped-def]
    with h.conectar() as c:
        row = c.execute(
            "select public.registrar_transicao_motorista(%s, %s, %s, %s::numeric, %s::numeric, %s::numeric, %s::integer, %s::integer)",
            (rid, momento, ator, lat, lon, prec, km.get("km_saida"), km.get("km_chegada")),
        ).fetchone()
    assert row is not None
    return row[0]


def _localizacoes(rid: uuid.UUID) -> list[tuple]:
    with h.conectar() as c:
        return c.execute(
            "select momento, latitude, longitude, registrado_por from public.localizacoes_viagem "
            "where requisicao_id = %s order by momento",
            (rid,),
        ).fetchall()


def _erro(rid: uuid.UUID, momento: str, ator: uuid.UUID, codigo: str, **kw) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.Error) as exc:
        _rpc(rid, momento, ator, **kw)
    assert h.sqlstate(exc) == codigo


# --- flag ------------------------------------------------------------------------------------------
def test_flag_nasce_desligada() -> None:
    with h.conectar() as c:
        row = c.execute(
            "select column_default, is_nullable from information_schema.columns where table_schema='public' "
            "and table_name='configuracoes' and column_name='motorista_localizacao'"
        ).fetchone()
    assert row == ("false", "NO")


def test_flag_exige_registra_e_e_auditada(cena: Cena) -> None:
    with pytest.raises(psycopg.Error) as exc:
        _cfg({"motorista_localizacao": True}, cena.admin)
    assert h.sqlstate(exc) == "AG010"
    _cfg({"motorista": True}, cena.admin)
    with pytest.raises(psycopg.Error) as exc2:
        _cfg({"motorista_localizacao": True}, cena.admin)
    assert h.sqlstate(exc2) == "AG010"
    _cfg({"motorista_registra": True}, cena.admin)
    _cfg({"motorista_localizacao": True}, cena.admin)
    with pytest.raises(psycopg.Error) as exc3:  # nao desliga o pai com a localizacao ligada
        _cfg({"motorista_registra": False}, cena.admin)
    assert h.sqlstate(exc3) == "AG010"
    with h.conectar() as c:
        rows = c.execute(
            "select de, para from public.auditoria where tabela='configuracoes' and ator_id=%s "
            "and detalhes ->> 'recurso' = 'motorista_localizacao'",
            (cena.admin,),
        ).fetchall()
    assert ("false", "true") in rows
    with pytest.raises(psycopg.Error) as exc4:  # so admin ativo altera
        _cfg({"motorista_localizacao": False}, cena.autorizador)
    assert h.sqlstate(exc4) == "AG002"


# --- RPC: caminho feliz ----------------------------------------------------------------------------
def test_saida_e_chegada_com_localizacao(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada()
    r = _rpc(rid, "saida", cena.motorista, prec=12.5)
    assert r == {"status": "em_uso", "localizacao_gravada": True}
    assert h.status_de(rid) == "em_uso"
    r2 = _rpc(rid, "chegada", cena.motorista)
    assert r2["status"] == "concluida"
    locs = _localizacoes(rid)
    assert [(m, float(la), float(lo), p) for m, la, lo, p in locs] == [
        ("chegada", LAT, LON, cena.motorista),
        ("saida", LAT, LON, cena.motorista),
    ]
    with h.conectar() as c:
        row = c.execute(
            "select saida_registrada_por, chegada_registrada_por from public.requisicoes where id=%s", (rid,)
        ).fetchone()
    assert row == (cena.motorista, cena.motorista)


def test_auditoria_da_localizacao_nao_grava_coordenadas(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada()
    _rpc(rid, "saida", cena.motorista)
    with h.conectar() as c:
        rows = c.execute(
            "select ator_id, de, para, detalhes::text from public.auditoria "
            "where tabela='requisicoes' and registro_id=%s and acao='localizacao_registrada'",
            (rid,),
        ).fetchall()
    assert len(rows) == 1
    ator, de, para, det = rows[0]
    assert ator == cena.motorista and de is None and para is None
    assert "15.79" not in det and "47.88" not in det and "latitude" not in det and "longitude" not in det
    assert '"momento": "saida"' in det
    # a transicao tambem foi auditada, com o motorista como ator
    assert ("transicao", cena.motorista, "autorizada", "em_uso") in h.auditoria_de("requisicoes", rid)


# --- RPC: bloqueios --------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kw",
    [
        {"lat": None, "lon": None},
        {"lat": LAT, "lon": None},
        {"lat": None, "lon": LON},
        {"lat": "NaN", "lon": LON},
    ],
)
def test_motorista_sem_coordenadas_e_bloqueado(cena: Cena, kw: dict) -> None:  # type: ignore[type-arg]
    cena.ligar()
    rid = cena.autorizada()
    _erro(rid, "saida", cena.motorista, "AG036", **kw)
    assert h.status_de(rid) == "autorizada" and _localizacoes(rid) == []


@pytest.mark.parametrize("kw", [{"lat": 90.5, "lon": 0}, {"lat": -91, "lon": 0}, {"lat": 0, "lon": 180.5},
                                {"lat": 0, "lon": -181}, {"lat": 99999, "lon": 0}, {"lat": 0, "lon": 0, "prec": -1}])
def test_coordenada_fora_da_faixa_recusada(cena: Cena, kw: dict) -> None:  # type: ignore[type-arg]
    cena.ligar()
    rid = cena.autorizada()
    _erro(rid, "saida", cena.motorista, "AG036", **kw)
    assert h.status_de(rid) == "autorizada" and _localizacoes(rid) == []


def test_limites_exatos_sao_aceitos(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada()
    _rpc(rid, "saida", cena.motorista, lat=90, lon=-180)
    assert h.status_de(rid) == "em_uso"


def test_check_da_tabela_recusa_fora_da_faixa(cena: Cena) -> None:
    """Defesa em profundidade: os CHECKs existem e sao validados."""
    with h.conectar() as c:
        rows = c.execute(
            "select conname, pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid='public.localizacoes_viagem'::regclass and contype='c' and convalidated"
        ).fetchall()
    defs = {n: d for n, d in rows}
    assert "90" in defs["ck_localizacoes_viagem_latitude"] and "180" in defs["ck_localizacoes_viagem_longitude"]
    assert "precisao" in defs["ck_localizacoes_viagem_precisao"]


def test_outro_motorista_bloqueado(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada()
    _erro(rid, "saida", cena.outro, "AG035")
    assert h.status_de(rid) == "autorizada" and _localizacoes(rid) == []
    sem_designado = cena.autorizada()
    with h.conectar() as c:
        c.execute(
            "update public.requisicoes set motorista_id=null, motorista_alterado_por=%s where id=%s",
            (cena.autorizador, sem_designado),
        )  # motorista_id aceita NULL de volta (item 3)
    _erro(sem_designado, "saida", cena.motorista, "AG035")


def test_ator_que_nao_e_motorista_ou_inativo_recusado_na_rpc(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada()
    inativo = h.criar_usuario("mot-inativo", "motorista", ativo=False)
    for ator in (cena.autorizador, cena.admin, cena.dono, inativo, uuid.uuid4()):
        _erro(rid, "saida", ator, "AG002")


def test_momento_e_status_incompativeis(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada()
    _erro(rid, "chegada", cena.motorista, "AG001")  # autorizada nao tem chegada
    _erro(rid, "meio", cena.motorista, "AG037")
    _erro(uuid.uuid4(), "saida", cena.motorista, "AG037")
    _rpc(rid, "saida", cena.motorista)
    _erro(rid, "saida", cena.motorista, "AG001")  # nao repete


def test_recursos_desligados_bloqueiam_a_rpc(cena: Cena) -> None:
    _cfg({"motorista": True}, cena.admin)
    rid2 = cena.autorizada()
    _erro(rid2, "saida", cena.motorista, "AG034")  # motorista_registra desligado
    _cfg({"motorista_registra": True}, cena.admin)
    assert _rpc(rid2, "saida", cena.motorista, lat=None, lon=None)["localizacao_gravada"] is False


def test_flag_localizacao_desligada_ignora_coordenadas(cena: Cena) -> None:
    cena.ligar(localizacao=False)
    rid = cena.autorizada()
    r = _rpc(rid, "saida", cena.motorista)  # com coordenadas validas: ignoradas
    assert r == {"status": "em_uso", "localizacao_gravada": False}
    assert _localizacoes(rid) == []
    _rpc(rid, "chegada", cena.motorista, lat=None, lon=None)  # sem coordenadas passa
    assert h.status_de(rid) == "concluida"


def test_autorizador_e_admin_nao_geram_localizacao(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada()
    h.iniciar(rid, cena.autorizador)  # nao coleta, mesmo com motorista designado e localizacao ligada
    h.concluir(rid, cena.admin)
    assert h.status_de(rid) == "concluida" and _localizacoes(rid) == []
    acoes = [a[0] for a in h.auditoria_de("requisicoes", rid)]
    assert "localizacao_registrada" not in acoes


# --- gravacao direta do motorista (fora da RPC) ----------------------------------------------------
def test_update_direto_do_motorista_com_localizacao_ligada_e_bloqueado(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada()
    with pytest.raises(psycopg.Error) as exc:
        h.iniciar(rid, cena.motorista)
    assert h.sqlstate(exc) == "AG036"
    assert h.status_de(rid) == "autorizada"


def test_update_direto_do_motorista_sem_localizacao_ligada_funciona(cena: Cena) -> None:
    cena.ligar(localizacao=False)
    rid = cena.autorizada()
    h.iniciar(rid, cena.motorista)
    h.concluir(rid, cena.motorista)
    assert h.status_de(rid) == "concluida"


def test_update_direto_de_outro_motorista_e_do_nao_designado_bloqueado(cena: Cena) -> None:
    cena.ligar(localizacao=False)
    rid = cena.autorizada()
    with pytest.raises(psycopg.Error) as exc:
        h.iniciar(rid, cena.outro)
    assert h.sqlstate(exc) == "AG035"


def test_motorista_nao_autoriza_nem_cancela(cena: Cena) -> None:
    cena.ligar(localizacao=False)
    with h.conectar() as c:
        rid = h.inserir_requisicao(c, cena.dono, h.novo_veiculo())
    with pytest.raises(psycopg.Error) as exc:
        h.autorizar(rid, cena.motorista)
    assert h.sqlstate(exc) == "AG002"
    rid2 = cena.autorizada()
    with pytest.raises(psycopg.Error) as exc2:
        h.cancelar(rid2, cena.motorista)
    assert h.sqlstate(exc2) == "AG002"


def test_update_direto_sem_flags_continua_recusado_ao_motorista(cena: Cena) -> None:
    _cfg({"motorista": True}, cena.admin)
    rid = cena.autorizada()
    with pytest.raises(psycopg.Error) as exc:
        h.iniciar(rid, cena.motorista)  # motorista_registra desligado
    assert h.sqlstate(exc) == "AG034"


def test_insert_direto_de_localizacao_recusado(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada()
    with pytest.raises(psycopg.Error) as exc:  # sem o marcador da RPC (mesmo como dono do banco)
        with h.conectar() as c:
            c.execute(
                "insert into public.localizacoes_viagem (requisicao_id, momento, latitude, longitude, registrado_por) "
                "values (%s, 'saida', 0, 0, %s)",
                (rid, cena.motorista),
            )
    assert h.sqlstate(exc) == "AG037"
    with pytest.raises(psycopg.Error) as exc2:
        with h.como("service_role") as c:
            c.execute(
                "insert into public.localizacoes_viagem (requisicao_id, momento, latitude, longitude, registrado_por) "
                "values (%s, 'saida', 0, 0, %s)",
                (rid, cena.motorista),
            )
    assert h.sqlstate(exc2) == NEGADO


# --- atomicidade -----------------------------------------------------------------------------------
def test_falha_na_transicao_desfaz_a_localizacao(cena: Cena) -> None:
    """Checklist ligado e ausente (AG013): a RPC falha e NAO sobra localizacao orfa."""
    cena.ligar()
    _cfg({"checklist": True}, cena.admin)
    rid = cena.autorizada()
    _erro(rid, "saida", cena.motorista, "AG013")
    assert _localizacoes(rid) == [] and h.status_de(rid) == "autorizada"


def test_motorista_nunca_e_obrigado_a_lancar_km(cena: Cena) -> None:
    # Migration 20261119120200: motorista nunca exige KM (AG012 nao vale para ele), mesmo com Quilometragem ligada.
    cena.ligar()
    _cfg({"quilometragem": True}, cena.admin)
    rid = cena.autorizada()
    _rpc(rid, "saida", cena.motorista)
    _rpc(rid, "chegada", cena.motorista)
    assert h.status_de(rid) == "concluida"


def test_duas_chamadas_simultaneas_so_uma_persiste(cena: Cena) -> None:
    import threading

    cena.ligar()
    rid = cena.autorizada()
    resultados: list[str] = []

    def chamar() -> None:
        try:
            _rpc(rid, "saida", cena.motorista)
            resultados.append("ok")
        except psycopg.Error as e:
            resultados.append(str(e.sqlstate))

    ts = [threading.Thread(target=chamar) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(resultados) == ["AG001", "ok"]
    assert len(_localizacoes(rid)) == 1


# --- append-only, RLS e grants ---------------------------------------------------------------------
def test_localizacao_e_append_only(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada()
    _rpc(rid, "saida", cena.motorista)
    for sql in (
        "update public.localizacoes_viagem set latitude = 0 where requisicao_id = %s",
        "delete from public.localizacoes_viagem where requisicao_id = %s",
    ):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(sql, (rid,))
        assert h.sqlstate(exc) == "AG003"
    with pytest.raises(psycopg.Error) as exc2:
        with h.conectar() as c:
            c.execute("truncate public.localizacoes_viagem")
    assert h.sqlstate(exc2) == "AG003"
    # Desligar o recurso nao apaga o que foi gravado.
    _cfg({"motorista_localizacao": False}, cena.admin)
    assert len(_localizacoes(rid)) == 1


def test_leitura_so_service_role(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada()
    _rpc(rid, "saida", cena.motorista)
    sql = "select latitude, longitude from public.localizacoes_viagem where requisicao_id = %s"
    for papel, uid in (("anon", None), ("authenticated", cena.admin), ("authenticated", cena.motorista)):
        with pytest.raises(psycopg.Error) as exc:
            with h.como(papel, uid) as c:
                c.execute(sql, (rid,))
        assert h.sqlstate(exc) == NEGADO, papel
    with h.como("service_role") as c:
        assert len(c.execute(sql, (rid,)).fetchall()) == 1
    for dml in ("insert into public.localizacoes_viagem (requisicao_id) values (%s)",
                "update public.localizacoes_viagem set latitude = 0 where requisicao_id = %s",
                "delete from public.localizacoes_viagem where requisicao_id = %s"):
        with pytest.raises(psycopg.Error) as exc2:
            with h.como("service_role") as c:
                c.execute(dml, (rid,))
        assert h.sqlstate(exc2) == NEGADO


def test_rpc_so_service_role_e_gatilhos_sem_execute() -> None:
    with h.conectar() as c:
        (rpc,) = c.execute(
            "select has_function_privilege('service_role', p, 'execute'), "
            "has_function_privilege('anon', p, 'execute') or has_function_privilege('authenticated', p, 'execute') "
            "from to_regprocedure('public.registrar_transicao_motorista(uuid,text,uuid,numeric,numeric,numeric,integer,integer)') p"
        ).fetchall()
        (trg,) = c.execute(
            "select bool_or(has_function_privilege('anon', p, 'execute') or has_function_privilege('authenticated', p, 'execute') "
            "or has_function_privilege('service_role', p, 'execute')) from unnest(array["
            "to_regprocedure('public.localizacoes_viagem_validar()'), to_regprocedure('public.localizacoes_viagem_auditar()'), "
            "to_regprocedure('public.requisicoes_validar()'), to_regprocedure('public.checklists_validar()')]) p"
        ).fetchall()
    assert rpc == (True, False)
    assert trg == (False,)


# --- checklist do motorista designado ---------------------------------------------------------------
def _checklist(rid: uuid.UUID, momento: str, por: uuid.UUID, fotos: list | None = None) -> uuid.UUID:
    with h.conectar() as c:
        row = c.execute(
            "select public.registrar_checklist(%s, %s, 'meio'::public.nivel_combustivel, false, null, %s, %s)",
            (rid, momento, por, Jsonb(fotos or [])),
        ).fetchone()
    assert row is not None
    return row[0]


def test_motorista_designado_grava_o_proprio_checklist_com_fotos(cena: Cena) -> None:
    cena.ligar()
    _cfg({"checklist": True}, cena.admin)
    rid = cena.autorizada()
    foto = {"id": str(uuid.uuid4()), "mime_type": "image/jpeg", "tamanho_bytes": 1000}
    _checklist(rid, "saida", cena.motorista, [foto])
    with h.conectar() as c:
        (n,) = c.execute("select count(*) from public.checklist_fotos where requisicao_id=%s", (rid,)).fetchone() or (0,)
    assert n == 1
    _rpc(rid, "saida", cena.motorista)
    _checklist(rid, "chegada", cena.motorista)
    _rpc(rid, "chegada", cena.motorista)
    assert h.status_de(rid) == "concluida"


def test_checklist_de_outro_motorista_ou_recurso_desligado_recusado(cena: Cena) -> None:
    _cfg({"motorista": True}, cena.admin)
    rid = cena.autorizada()
    with pytest.raises(psycopg.Error) as exc:
        _checklist(rid, "saida", cena.motorista)  # motorista_registra desligado
    assert h.sqlstate(exc) == "AG034"
    _cfg({"motorista_registra": True}, cena.admin)
    with pytest.raises(psycopg.Error) as exc2:
        _checklist(rid, "saida", cena.outro)
    assert h.sqlstate(exc2) == "AG035"
    with pytest.raises(psycopg.Error) as exc3:
        _checklist(rid, "saida", cena.dono)  # solicitante continua recusado
    assert h.sqlstate(exc3) == "AG002"
    _checklist(rid, "saida", cena.autorizador)  # staff segue igual


# --- designacao: NULL de volta e auditoria vinda do backend -----------------------------------------
def test_motorista_id_aceita_null_de_volta_e_audita(cena: Cena) -> None:
    cena.ligar(localizacao=False)
    rid = cena.autorizada()
    with h.conectar() as c:
        c.execute(
            "update public.requisicoes set motorista_id=null, motorista_alterado_por=%s where id=%s",
            (cena.autorizador, rid),
        )
        row = c.execute("select motorista_id, motorista_alterado_por from public.requisicoes where id=%s", (rid,)).fetchone()
    assert row == (None, None)
    linhas = [r for r in h.auditoria_de("requisicoes", rid) if r[0] == "motorista_designado"]
    assert [(r[2], r[3]) for r in linhas] == [(None, str(cena.motorista)), (str(cena.motorista), None)]


def test_regras_anteriores_preservadas(cena: Cena) -> None:
    """Imutabilidade, carimbo do servidor e encerrada congelada seguem valendo."""
    cena.ligar(localizacao=False)
    rid = cena.autorizada()
    h.iniciar(rid, cena.autorizador)
    with pytest.raises(psycopg.Error) as exc:  # carimbo vindo do chamador
        with h.conectar() as c:
            c.execute("update public.requisicoes set chegada_real_em = now() where id=%s", (rid,))
    assert h.sqlstate(exc) == "AG004"
    h.concluir(rid, cena.autorizador)
    with pytest.raises(psycopg.Error) as exc2:
        with h.conectar() as c:
            c.execute("update public.requisicoes set trajeto = 'x' where id=%s", (rid,))
    assert h.sqlstate(exc2) == "AG001"
