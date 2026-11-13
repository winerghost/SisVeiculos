"""Testes das migrations 20261117120000/120100/120200: role vigilante, flag vigilante_km e KM do vigilante.

Cobre: enum, flag + CHECK + AG010 + auditoria, ramo do vigilante em requisicoes_validar (so saida/chegada, de
qualquer requisicao, sem flag e sem designacao; autorizar/negar/cancelar => AG002), matriz de KM
(quilometragem x vigilante_km x ator), imutabilidade do KM e RLS deny-by-default.

Somente banco local/dev DESCARTAVEL (TEST_DATABASE_URL). Ver dbhelpers.py. Fixture autouse isola as flags
globais; rodar sem paralelismo.

    pytest supabase/tests/test_vigilante.py -m db_local -p no:xdist
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(
        not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"
    ),
]

NEGADO = "42501"
# Dependentes primeiro (desligar nesta ordem; religar na ordem inversa).
FLAGS = ("autorizador_km", "vigilante_km", "abastecimento_manutencao", "quilometragem", "checklist")


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
    por = h.admin_ativo_qualquer() or h.criar_usuario("vg-admin-cfg", "admin")
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
        self.admin = h.criar_usuario("vg-admin", "admin")
        self.dono = h.criar_usuario("vg-dono")
        self.aut = h.criar_usuario("vg-aut", "autorizador")
        self.vig = h.criar_usuario("vg-vig", "vigilante")
        self.mot = h.criar_usuario("vg-mot", "motorista")

    def cfg(self, flag: str, valor: bool) -> None:
        _cfg(flag, valor, self.admin)

    def solicitada(self, dono: uuid.UUID | None = None) -> uuid.UUID:
        with h.conectar() as c:
            return h.inserir_requisicao(c, dono or self.dono, h.novo_veiculo())

    def autorizada(self) -> uuid.UUID:
        rid = self.solicitada()
        h.autorizar(rid, self.aut)
        return rid


@pytest.fixture
def cena() -> Cena:
    return Cena()


def _falha(sql: str, params: tuple, codigo: str) -> None:  # type: ignore[type-arg]
    with pytest.raises(psycopg.Error) as exc, h.conectar() as c:
        c.execute(sql, params)
    assert h.sqlstate(exc) == codigo


def _saida(rid: uuid.UUID, por: uuid.UUID, km: int | None = None) -> None:
    with h.conectar() as c:
        c.execute(
            "update public.requisicoes set status='em_uso', saida_registrada_por=%s, km_saida=%s where id=%s",
            (por, km, rid),
        )


def _chegada(rid: uuid.UUID, por: uuid.UUID, km: int | None = None) -> None:
    with h.conectar() as c:
        c.execute(
            "update public.requisicoes set status='concluida', chegada_registrada_por=%s, km_chegada=%s where id=%s",
            (por, km, rid),
        )


def _km(rid: uuid.UUID) -> tuple[int | None, int | None]:
    with h.conectar() as c:
        row = c.execute(
            "select km_saida, km_chegada from public.requisicoes where id=%s", (rid,)
        ).fetchone()
    assert row is not None
    return row[0], row[1]


def _n_cfg_auditoria(chave: str) -> int:
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.auditoria where tabela='configuracoes' "
            "and acao='configuracao_alterada' and detalhes->>'recurso' = %s",
            (chave,),
        ).fetchone() or (0,)
    return int(n)


# --- enum e flag -----------------------------------------------------------------------------------
def test_enum_tem_vigilante() -> None:
    with h.conectar() as c:
        rows = c.execute(
            "select unnest(enum_range(null::public.role_usuario))::text"
        ).fetchall()
    assert "vigilante" in {r[0] for r in rows}


def test_perfil_vigilante_e_criado(cena: Cena) -> None:
    assert h.perfil_de(cena.vig) == ("vigilante", True)


def test_vigilante_km_nasce_desligada_not_null() -> None:
    with h.conectar() as c:
        rows = c.execute(
            "select data_type, is_nullable, column_default from information_schema.columns "
            "where table_schema='public' and table_name='configuracoes' and column_name='vigilante_km'"
        ).fetchall()
    assert rows == [("boolean", "NO", "false")]


def test_vigilante_km_exige_quilometragem_ag010(cena: Cena) -> None:
    _falha(
        "update public.configuracoes set vigilante_km = true, alterado_por = %s where id = true",
        (cena.admin,),
        "AG010",
    )
    assert _estado()["vigilante_km"] is False


def test_desligar_quilometragem_com_vigilante_km_ligada_ag010(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("vigilante_km", True)
    _falha(
        "update public.configuracoes set quilometragem = false, alterado_por = %s where id = true",
        (cena.admin,),
        "AG010",
    )
    # Desligando as duas na mesma alteracao, vale.
    with h.conectar() as c:
        c.execute(
            "update public.configuracoes set vigilante_km = false, quilometragem = false, alterado_por = %s "
            "where id = true",
            (cena.admin,),
        )
    assert _estado()["quilometragem"] is False


def test_check_de_tabela_vigilante_km_exige_quilometragem(cena: Cena) -> None:
    """O CHECK existe e valida (rede de seguranca por tras do AG010 do gatilho)."""
    with h.conectar() as c:
        row = c.execute(
            "select convalidated from pg_constraint where conrelid='public.configuracoes'::regclass "
            "and conname='ck_configuracoes_vigilante_km_exige_quilometragem' and contype='c'"
        ).fetchone()
    assert row == (True,)


def test_alteracao_da_flag_e_auditada_e_ator_zerado(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    antes = _n_cfg_auditoria("vigilante_km")
    cena.cfg("vigilante_km", True)
    cena.cfg("vigilante_km", False)
    assert _n_cfg_auditoria("vigilante_km") == antes + 2
    with h.conectar() as c:
        row = c.execute(
            "select ator_id, de, para from public.auditoria where tabela='configuracoes' "
            "and detalhes->>'recurso'='vigilante_km' order by id desc limit 1"
        ).fetchone()
        (alterado,) = c.execute(
            "select alterado_por from public.configuracoes"
        ).fetchone() or (None,)
    assert row == (cena.admin, "true", "false")
    assert alterado is None  # alterado_por nunca persiste


def test_flag_so_por_admin_ativo_ag002(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    for ator in (cena.dono, cena.aut, cena.vig, None):
        _falha(
            "update public.configuracoes set vigilante_km = true, alterado_por = %s where id = true",
            (ator,),
            "AG002",
        )
    assert _estado()["vigilante_km"] is False


def test_chaves_anteriores_continuam_auditadas(cena: Cena) -> None:
    """Regressao: a versao nova de configuracoes_validar preserva as 10 chaves anteriores."""
    antes = _n_cfg_auditoria("checklist")
    cena.cfg("checklist", True)
    cena.cfg("checklist", False)
    assert _n_cfg_auditoria("checklist") == antes + 2
    antes_q = _n_cfg_auditoria("quilometragem")
    cena.cfg("quilometragem", True)
    assert _n_cfg_auditoria("quilometragem") == antes_q + 1
    with h.conectar() as c:
        (d,) = c.execute(
            "select pg_get_functiondef('public.configuracoes_validar()'::regprocedure)"
        ).fetchone() or ("",)
    for chave in (
        "quilometragem", "abastecimento_manutencao", "checklist", "mfa", "relatorio_email",
        "relatorio_whatsapp", "notificacao_email_criticos", "motorista", "motorista_registra",
        "motorista_localizacao", "vigilante_km", "autorizador_decide_propria", "autorizador_km",
    ):  # fmt: skip
        assert f"'{chave}'" in d, chave


# --- ramo do vigilante: transicoes -----------------------------------------------------------------
def test_vigilante_registra_saida_e_chegada_sem_flag_e_sem_designacao(
    cena: Cena,
) -> None:
    rid = (
        cena.autorizada()
    )  # sem motorista designado, recurso Motorista nao precisa estar ligado
    _saida(rid, cena.vig)
    assert h.status_de(rid) == "em_uso"
    _chegada(rid, cena.vig)
    assert h.status_de(rid) == "concluida"
    with h.conectar() as c:
        row = c.execute(
            "select saida_registrada_por, chegada_registrada_por, saida_real_em is not null, "
            "chegada_real_em is not null from public.requisicoes where id=%s",
            (rid,),
        ).fetchone()
    assert row == (cena.vig, cena.vig, True, True)


def test_vigilante_registra_requisicao_designada_a_outro_motorista(cena: Cena) -> None:
    rid = cena.autorizada()
    with h.conectar() as c:
        c.execute(
            "update public.requisicoes set motorista_id=%s, motorista_alterado_por=%s where id=%s",
            (cena.mot, cena.aut, rid),
        )
    _saida(rid, cena.vig)
    assert h.status_de(rid) == "em_uso"


def test_vigilante_nao_autoriza_nem_nega_ag002(cena: Cena) -> None:
    rid = cena.solicitada()
    _falha(
        "update public.requisicoes set status='autorizada', autorizado_por=%s where id=%s",
        (cena.vig, rid),
        "AG002",
    )
    _falha(
        "update public.requisicoes set status='negada', autorizado_por=%s where id=%s",
        (cena.vig, rid),
        "AG002",
    )
    assert h.status_de(rid) == "solicitada"


def test_vigilante_nao_cancela_nem_a_propria_ag002(cena: Cena) -> None:
    propria = cena.solicitada(cena.vig)
    autorizada = cena.autorizada()
    _falha(
        "update public.requisicoes set status='cancelada', cancelada_por=%s where id=%s",
        (cena.vig, propria),
        "AG002",
    )
    _falha(
        "update public.requisicoes set status='cancelada', cancelada_por=%s where id=%s",
        (cena.vig, autorizada),
        "AG002",
    )
    assert (h.status_de(propria), h.status_de(autorizada)) == (
        "solicitada",
        "autorizada",
    )


def test_vigilante_nao_pula_estados(cena: Cena) -> None:
    rid = cena.solicitada()
    _falha(
        "update public.requisicoes set status='em_uso', saida_registrada_por=%s where id=%s",
        (cena.vig, rid),
        "AG001",
    )
    assert h.status_de(rid) == "solicitada"


def test_vigilante_inativo_e_recusado(cena: Cena) -> None:
    rid = cena.autorizada()
    inativo = h.criar_usuario("vg-inativo", "vigilante", ativo=False)
    _falha(
        "update public.requisicoes set status='em_uso', saida_registrada_por=%s where id=%s",
        (inativo, rid),
        "AG002",
    )


def test_outros_papeis_seguem_iguais(cena: Cena) -> None:
    """Regressao: autorizador e admin registram; solicitante e motorista (recurso desligado) nao."""
    rid = cena.autorizada()
    _falha(
        "update public.requisicoes set status='em_uso', saida_registrada_por=%s where id=%s",
        (cena.dono, rid),
        "AG002",
    )
    _falha(
        "update public.requisicoes set status='em_uso', saida_registrada_por=%s where id=%s",
        (cena.mot, rid),
        "AG034",
    )
    _saida(rid, cena.aut)
    assert h.status_de(rid) == "em_uso"
    _chegada(rid, cena.admin)
    assert h.status_de(rid) == "concluida"


def test_requisicao_encerrada_segue_congelada(cena: Cena) -> None:
    rid = cena.autorizada()
    _saida(rid, cena.vig)
    _chegada(rid, cena.vig)
    _falha(
        "update public.requisicoes set trajeto = 'X-Y' where id = %s", (rid,), "AG001"
    )


# --- KM: quilometragem x vigilante_km x ator -------------------------------------------------------
def test_quilometragem_desligada_vigilante_sem_km(cena: Cena) -> None:
    rid = cena.autorizada()
    _saida(rid, cena.vig)
    _chegada(rid, cena.vig)
    assert _km(rid) == (None, None)


def test_quilometragem_ligada_vigilante_km_desligada_vigilante_registra_sem_km(
    cena: Cena,
) -> None:
    cena.cfg("quilometragem", True)
    rid = cena.autorizada()
    _saida(rid, cena.vig)  # sem AG012
    assert h.status_de(rid) == "em_uso"
    _chegada(rid, cena.vig)
    assert h.status_de(rid) == "concluida"
    assert _km(rid) == (None, None)


def test_quilometragem_ligada_autorizador_km_desligada_autorizador_e_admin_registram_sem_km(
    cena: Cena,
) -> None:
    # Migration 20261119120200: autorizador/admin so sao obrigados com autorizador_km ligada.
    cena.cfg("quilometragem", True)
    rid = cena.autorizada()
    _saida(rid, cena.aut)
    _chegada(rid, cena.admin)
    assert h.status_de(rid) == "concluida"
    assert _km(rid) == (None, None)


def test_quilometragem_ligada_autorizador_km_ligada_demais_atores_seguem_obrigando(
    cena: Cena,
) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("autorizador_km", True)
    rid = cena.autorizada()
    for ator in (cena.aut, cena.admin):
        _falha(
            "update public.requisicoes set status='em_uso', saida_registrada_por=%s where id=%s",
            (ator, rid),
            "AG012",
        )
    _saida(rid, cena.aut, 100)
    _falha(
        "update public.requisicoes set status='concluida', chegada_registrada_por=%s where id=%s",
        (cena.admin, rid),
        "AG012",
    )
    _chegada(rid, cena.admin, 130)
    assert _km(rid) == (100, 130)


def test_vigilante_km_desligada_aceita_km_enviado_e_valida_coerencia(
    cena: Cena,
) -> None:
    cena.cfg("quilometragem", True)
    rid = cena.autorizada()
    _saida(rid, cena.vig, 500)
    assert _km(rid) == (500, None)
    _falha(
        "update public.requisicoes set status='concluida', chegada_registrada_por=%s, km_chegada=499 where id=%s",
        (cena.vig, rid),
        "23514",  # CHECK km_chegada >= km_saida segue valendo
    )
    _chegada(rid, cena.vig, 520)
    assert _km(rid) == (500, 520)


def test_vigilante_km_ligada_vigilante_precisa_informar_km(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("vigilante_km", True)
    rid = cena.autorizada()
    _falha(
        "update public.requisicoes set status='em_uso', saida_registrada_por=%s where id=%s",
        (cena.vig, rid),
        "AG012",
    )
    _saida(rid, cena.vig, 1000)
    _falha(
        "update public.requisicoes set status='concluida', chegada_registrada_por=%s where id=%s",
        (cena.vig, rid),
        "AG012",
    )
    _falha(
        "update public.requisicoes set status='concluida', chegada_registrada_por=%s, km_chegada=999 where id=%s",
        (cena.vig, rid),
        "23514",
    )
    _chegada(rid, cena.vig, 1042)
    assert _km(rid) == (1000, 1042)


def test_misto_saida_autorizador_chegada_vigilante_sem_km_com_flag_desligada(
    cena: Cena,
) -> None:
    cena.cfg("quilometragem", True)
    rid = cena.autorizada()
    _saida(rid, cena.aut, 300)
    _chegada(rid, cena.vig)  # vigilante_km desligada: dispensado
    assert h.status_de(rid) == "concluida"
    assert _km(rid) == (300, None)


def test_desligar_vigilante_km_nao_apaga_km_gravado(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    cena.cfg("vigilante_km", True)
    rid = cena.autorizada()
    _saida(rid, cena.vig, 700)
    cena.cfg("vigilante_km", False)
    assert _km(rid) == (700, None)
    _chegada(rid, cena.vig)  # agora dispensado, e o km_saida segue intacto
    assert _km(rid) == (700, None)


def test_km_gravado_continua_imutavel(cena: Cena) -> None:
    cena.cfg("quilometragem", True)
    rid = cena.autorizada()
    _saida(rid, cena.vig, 10)
    _falha(
        "update public.requisicoes set status='concluida', chegada_registrada_por=%s, km_saida=11 where id=%s",
        (cena.vig, rid),
        "AG004",
    )


# --- RLS / permissoes ------------------------------------------------------------------------------
def test_api_nao_acessa_configuracoes_nem_funcoes(cena: Cena) -> None:
    for papel in ("anon", "authenticated"):
        with (
            pytest.raises(psycopg.Error) as exc,
            h.como(papel, cena.vig if papel == "authenticated" else None) as c,
        ):
            c.execute("select vigilante_km from public.configuracoes")
        assert h.sqlstate(exc) == NEGADO
    with h.conectar() as c:
        for fn in (
            "public.requisicoes_validar()",
            "public.requisicoes_validar_km()",
            "public.configuracoes_validar()",
        ):
            for papel in ("anon", "authenticated", "service_role"):
                (ok,) = c.execute(
                    "select has_function_privilege(%s, %s::regprocedure, 'execute')",
                    (papel, fn),
                ).fetchone() or (True,)
                assert ok is False, (papel, fn)


def test_vigilante_nao_le_tabelas_via_api(cena: Cena) -> None:
    for tabela in ("perfis", "requisicoes", "auditoria", "configuracoes"):
        with (
            pytest.raises(psycopg.Error) as exc,
            h.como("authenticated", cena.vig) as c,
        ):
            c.execute(f"select 1 from public.{tabela} limit 1")
        assert h.sqlstate(exc) == NEGADO, tabela
