"""Reforco dos testes da RPC registrar_transicao_motorista (migration 20261112120200).

Complementa test_motorista_localizacao.py (que cobre o caminho feliz, coordenadas, atomicidade e
concorrencia). Aqui: IDOR entre requisicoes e apos troca de motorista, recursos desligados,
transicoes de status invalidas (estados terminais), localizacao obrigatoria, auditoria e execucao
pela API.

Somente banco local/dev DESCARTAVEL (TEST_DATABASE_URL). Ver dbhelpers.py. Fixture autouse isola as
flags globais; rodar sem paralelismo.

    pytest supabase/tests/test_motorista_transicao.py -m db_local -p no:xdist
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
FLAGS = ("motorista_localizacao", "motorista_registra", "motorista")  # dependentes primeiro
LAT, LON = -15.793889, -47.882778


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
    por = h.admin_ativo_qualquer() or h.criar_usuario("mt-admin-cfg", "admin")
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
        self.admin = h.criar_usuario("mt-admin", "admin")
        self.dono = h.criar_usuario("mt-dono")
        self.aut = h.criar_usuario("mt-aut", "autorizador")
        self.mot = h.criar_usuario("mt-mot", "motorista")
        self.mot2 = h.criar_usuario("mt-mot2", "motorista")

    def ligar(self, *, localizacao: bool = True) -> None:
        _cfg("motorista", True, self.admin)
        _cfg("motorista_registra", True, self.admin)
        if localizacao:
            _cfg("motorista_localizacao", True, self.admin)

    def desligar_tudo(self) -> None:
        for f in FLAGS:
            if _estado()[f]:
                _cfg(f, False, self.admin)

    def autorizada(self, motorista: uuid.UUID | None = None) -> uuid.UUID:
        with h.conectar() as c:
            rid = h.inserir_requisicao(c, self.dono, h.novo_veiculo())
        h.autorizar(rid, self.aut)
        if motorista is not None:
            self.designar(rid, motorista)
        return rid

    def designar(self, rid: uuid.UUID, motorista: uuid.UUID | None) -> None:
        with h.conectar() as c:
            c.execute(
                "update public.requisicoes set motorista_id = %s, motorista_alterado_por = %s where id = %s",
                (motorista, self.aut, rid),
            )


@pytest.fixture
def cena() -> Cena:
    return Cena()


def _rpc(rid, momento, ator, lat=LAT, lon=LON, prec=None):  # type: ignore[no-untyped-def]
    with h.conectar() as c:
        row = c.execute(
            "select public.registrar_transicao_motorista(%s, %s, %s, %s::numeric, %s::numeric, %s::numeric)",
            (rid, momento, ator, lat, lon, prec),
        ).fetchone()
    assert row is not None
    return row[0]


def _erro(rid, momento, ator, codigo, **kw):  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.Error) as exc:
        _rpc(rid, momento, ator, **kw)
    assert h.sqlstate(exc) == codigo


def _n_loc(rid: uuid.UUID) -> int:
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.localizacoes_viagem where requisicao_id = %s", (rid,)
        ).fetchone() or (0,)
    return int(n)


def _acoes(rid: uuid.UUID) -> list[str]:
    return [a[0] for a in h.auditoria_de("requisicoes", rid)]


def _sem_efeito(rid: uuid.UUID, status: str, acoes_antes: list[str]) -> None:
    """Chamada recusada: status intacto, nenhuma localizacao e nenhuma linha nova de auditoria."""
    assert h.status_de(rid) == status
    assert _n_loc(rid) == 0
    assert _acoes(rid) == acoes_antes


# --- IDOR ------------------------------------------------------------------------------------------
def test_motorista_nao_designado_e_recusado_sem_efeitos(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    antes = _acoes(rid)
    _erro(rid, "saida", cena.mot2, "AG035")
    _sem_efeito(rid, "autorizada", antes)


def test_motorista_nao_age_na_requisicao_do_outro_nem_sem_designacao(cena: Cena) -> None:
    cena.ligar()
    rid_a = cena.autorizada(cena.mot)
    rid_b = cena.autorizada(cena.mot2)
    sem = cena.autorizada()  # autorizada sem motorista
    _erro(rid_a, "saida", cena.mot2, "AG035")
    _erro(rid_b, "saida", cena.mot, "AG035")
    _erro(sem, "saida", cena.mot, "AG035")
    assert [h.status_de(r) for r in (rid_a, rid_b, sem)] == ["autorizada"] * 3


def test_apos_troca_so_o_novo_motorista_registra(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    cena.designar(rid, cena.mot2)
    _erro(rid, "saida", cena.mot, "AG035")  # o antigo perdeu o direito
    assert _rpc(rid, "saida", cena.mot2)["status"] == "em_uso"


def test_troca_em_uso_o_antigo_nao_registra_chegada(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    _rpc(rid, "saida", cena.mot)
    cena.designar(rid, cena.mot2)
    _erro(rid, "chegada", cena.mot, "AG035")
    assert _rpc(rid, "chegada", cena.mot2)["status"] == "concluida"


def test_ator_com_outro_papel_e_recusado_mesmo_sendo_dono_ou_staff(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    antes = _acoes(rid)
    for ator in (cena.dono, cena.aut, cena.admin):
        _erro(rid, "saida", ator, "AG002")
    _sem_efeito(rid, "autorizada", antes)


def test_ator_nulo_ou_parametros_invalidos(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    _erro(rid, "saida", None, "AG037")
    _erro(None, "saida", cena.mot, "AG037")
    _erro(rid, None, cena.mot, "AG037")
    _erro(rid, "SAIDA", cena.mot, "AG037")  # sem normalizacao: so 'saida' ou 'chegada'


# --- recurso desligado -----------------------------------------------------------------------------
def test_recurso_motorista_desligado_recusa_a_rpc(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    cena.desligar_tudo()  # designacao existente permanece, mas o recurso esta off
    antes = _acoes(rid)
    _erro(rid, "saida", cena.mot, "AG034")
    _sem_efeito(rid, "autorizada", antes)


def test_apenas_motorista_ligado_sem_registra_recusa(cena: Cena) -> None:
    _cfg("motorista", True, cena.admin)
    rid = cena.autorizada(cena.mot)
    _erro(rid, "saida", cena.mot, "AG034")
    # o gatilho tambem recusa a gravacao direta do motorista (nao so a RPC)
    with pytest.raises(psycopg.Error) as exc:
        h.iniciar(rid, cena.mot)
    assert h.sqlstate(exc) == "AG034"


def test_religar_o_recurso_volta_a_funcionar_e_nada_foi_apagado(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    cena.desligar_tudo()
    _erro(rid, "saida", cena.mot, "AG034")
    cena.ligar()
    assert _rpc(rid, "saida", cena.mot)["status"] == "em_uso"


# --- transicao de status invalida ------------------------------------------------------------------
def test_chegada_antes_da_saida_e_saida_repetida(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    antes = _acoes(rid)
    _erro(rid, "chegada", cena.mot, "AG001")
    _sem_efeito(rid, "autorizada", antes)
    _rpc(rid, "saida", cena.mot)
    _erro(rid, "saida", cena.mot, "AG001")
    assert h.status_de(rid) == "em_uso"


def test_requisicao_concluida_nao_aceita_nada(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    _rpc(rid, "saida", cena.mot)
    _rpc(rid, "chegada", cena.mot)
    for momento in ("saida", "chegada"):
        _erro(rid, momento, cena.mot, "AG001")
    assert _n_loc(rid) == 2  # nenhuma localizacao extra


def test_requisicao_cancelada_nao_aceita_saida(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    h.cancelar(rid, cena.aut)
    _erro(rid, "saida", cena.mot, "AG001")
    assert h.status_de(rid) == "cancelada" and _n_loc(rid) == 0


def test_requisicao_solicitada_sem_designacao_e_recusada(cena: Cena) -> None:
    """Solicitada nao admite motorista (AG032); a RPC recusa por AG035 (nao designado)."""
    cena.ligar()
    with h.conectar() as c:
        rid = h.inserir_requisicao(c, cena.dono, h.novo_veiculo())
    _erro(rid, "saida", cena.mot, "AG035")
    assert h.status_de(rid) == "solicitada" and _n_loc(rid) == 0


def test_motorista_nao_consegue_outras_transicoes_por_gravacao_direta(cena: Cena) -> None:
    cena.ligar(localizacao=False)
    with h.conectar() as c:
        rid = h.inserir_requisicao(c, cena.dono, h.novo_veiculo())
    for fn in (h.autorizar, h.negar, h.cancelar):
        with pytest.raises(psycopg.Error) as exc:
            fn(rid, cena.mot)
        assert h.sqlstate(exc) == "AG002", fn.__name__
    assert h.status_de(rid) == "solicitada"


# --- localizacao obrigatoria -----------------------------------------------------------------------
def test_localizacao_obrigatoria_na_saida_e_na_chegada(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    _erro(rid, "saida", cena.mot, "AG036", lat=None, lon=None)
    _rpc(rid, "saida", cena.mot)
    antes = _acoes(rid)
    _erro(rid, "chegada", cena.mot, "AG036", lat=None, lon=None)
    _erro(rid, "chegada", cena.mot, "AG036", lat=LAT, lon=None)
    assert h.status_de(rid) == "em_uso" and _n_loc(rid) == 1 and _acoes(rid) == antes


def test_precisao_invalida_recusada(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    for prec in (-0.1, 1000000, "NaN"):
        _erro(rid, "saida", cena.mot, "AG036", prec=prec)
    assert h.status_de(rid) == "autorizada" and _n_loc(rid) == 0


def test_localizacao_desligada_nao_exige_nem_grava(cena: Cena) -> None:
    cena.ligar(localizacao=False)
    rid = cena.autorizada(cena.mot)
    r = _rpc(rid, "saida", cena.mot, lat=None, lon=None)
    assert r == {"status": "em_uso", "localizacao_gravada": False}
    assert _n_loc(rid) == 0


# --- auditoria -------------------------------------------------------------------------------------
def test_transicoes_geram_auditoria_com_o_motorista_como_ator(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    _rpc(rid, "saida", cena.mot)
    _rpc(rid, "chegada", cena.mot)
    linhas = h.auditoria_de("requisicoes", rid)
    assert ("transicao", cena.mot, "autorizada", "em_uso") in linhas
    assert ("transicao", cena.mot, "em_uso", "concluida") in linhas
    assert [a for a in _acoes(rid) if a == "localizacao_registrada"] == ["localizacao_registrada"] * 2


def test_auditoria_nao_vaza_coordenadas(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    _rpc(rid, "saida", cena.mot)
    with h.conectar() as c:
        textos = c.execute(
            "select coalesce(de,'') || coalesce(para,'') || coalesce(detalhes::text,'') "
            "from public.auditoria where tabela = 'requisicoes' and registro_id = %s",
            (rid,),
        ).fetchall()
    blob = " ".join(t[0] for t in textos)
    assert "15.79" not in blob and "47.88" not in blob


def test_auditoria_continua_append_only(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    _rpc(rid, "saida", cena.mot)
    for sql in (
        "update public.auditoria set acao = 'criada' where registro_id = %s",
        "delete from public.auditoria where registro_id = %s",
    ):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(sql, (rid,))
        assert h.sqlstate(exc) == "AG003"


def test_carimbos_e_responsaveis_gravados_pelo_servidor(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    _rpc(rid, "saida", cena.mot)
    _rpc(rid, "chegada", cena.mot)
    with h.conectar() as c:
        row = c.execute(
            "select saida_registrada_por, chegada_registrada_por, motorista_id, "
            "saida_real_em is not null, chegada_real_em > saida_real_em "
            "from public.requisicoes where id = %s",
            (rid,),
        ).fetchone()
    assert row == (cena.mot, cena.mot, cena.mot, True, True)


# --- execucao pela API -----------------------------------------------------------------------------
def test_rpc_nao_executavel_por_anon_e_authenticated(cena: Cena) -> None:
    cena.ligar()
    rid = cena.autorizada(cena.mot)
    sql = "select public.registrar_transicao_motorista(%s, 'saida', %s, 0, 0, null, null, null)"
    for papel, uid in (("anon", None), ("authenticated", cena.mot)):
        with pytest.raises(psycopg.Error) as exc:
            with h.como(papel, uid) as c:
                c.execute(sql, (rid, cena.mot))
        assert h.sqlstate(exc) == NEGADO, papel
    assert h.status_de(rid) == "autorizada"
