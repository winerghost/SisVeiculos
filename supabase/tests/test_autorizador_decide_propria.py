"""Testes da migration 20261119120100: flag autorizador_decide_propria e defesa no banco (AG040).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). A fixture autouse deixa a flag desligada antes de cada
teste e a restaura no fim (cada alteracao gera linha 'configuracao_alterada' em auditoria, append-only).

    pytest supabase/tests/test_autorizador_decide_propria.py -m db_local
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

FLAG = "autorizador_decide_propria"


def _valor() -> bool:
    with h.conectar() as c:
        row = c.execute(f"select {FLAG} from public.configuracoes").fetchone()
    assert row is not None
    return bool(row[0])


def _definir(valor: bool, por: uuid.UUID | None) -> None:
    with h.conectar() as c:
        c.execute(
            f"update public.configuracoes set {FLAG} = %s, alterado_por = %s where id = true",
            (valor, por),
        )


def _n_auditoria() -> int:
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.auditoria where tabela='configuracoes' "
            "and acao='configuracao_alterada' and detalhes->>'recurso' = %s",
            (FLAG,),
        ).fetchone() or (0,)
    return int(n)


@pytest.fixture(autouse=True)
def _estado_flag() -> Iterator[None]:
    orig = _valor()
    por = h.admin_ativo_qualquer() or h.criar_usuario("adp-admin-cfg", "admin")
    if orig:
        _definir(False, por)
    try:
        yield
    finally:
        if _valor() != orig:
            _definir(orig, por)


class Cena:
    def __init__(self) -> None:
        self.admin = h.criar_usuario("adp-admin", "admin")
        self.admin2 = h.criar_usuario("adp-admin2", "admin")
        self.aut = h.criar_usuario("adp-aut", "autorizador")
        self.aut2 = h.criar_usuario("adp-aut2", "autorizador")
        self.sol = h.criar_usuario("adp-sol")

    def req(self, dono: uuid.UUID) -> uuid.UUID:
        with h.conectar() as c:
            return h.inserir_requisicao(c, dono, h.novo_veiculo())

    def flag(self, valor: bool) -> None:
        _definir(valor, self.admin)


@pytest.fixture
def cena() -> Cena:
    return Cena()


def _recusa(fn, *args) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.Error) as exc:
        fn(*args)
    assert h.sqlstate(exc) == "AG040"


# --- coluna e auditoria --------------------------------------------------------------------------------
def test_coluna_boolean_not_null_default_false() -> None:
    with h.conectar() as c:
        row = c.execute(
            "select data_type, is_nullable, column_default from information_schema.columns "
            "where table_schema='public' and table_name='configuracoes' and column_name = %s",
            (FLAG,),
        ).fetchone()
    assert row == ("boolean", "NO", "false")


def test_ligar_e_desligar_geram_duas_linhas_de_auditoria(cena: Cena) -> None:
    antes = _n_auditoria()
    cena.flag(True)
    cena.flag(False)
    assert _n_auditoria() == antes + 2
    cena.flag(False)  # sem mudanca: sem auditoria
    assert _n_auditoria() == antes + 2


@pytest.mark.parametrize("quem", ["aut", "sol", None])
def test_so_admin_ativo_altera(cena: Cena, quem: str | None) -> None:
    ator = getattr(cena, quem) if quem else None
    with pytest.raises(psycopg.Error) as exc:
        _definir(True, ator)
    assert h.sqlstate(exc) == "AG002"
    assert _valor() is False


def test_funcao_valida_as_12_chaves_e_nao_e_executavel_pela_api() -> None:
    with h.conectar() as c:
        (d,) = c.execute(
            "select pg_get_functiondef('public.configuracoes_validar()'::regprocedure)"
        ).fetchone() or ("",)
        for papel in ("anon", "authenticated", "service_role"):
            (ok,) = c.execute(
                "select has_function_privilege(%s, 'public.configuracoes_validar()'::regprocedure, 'execute')",
                (papel,),
            ).fetchone() or (True,)
            assert ok is False, papel
    for chave in (
        "quilometragem", "abastecimento_manutencao", "checklist", "mfa", "relatorio_email",
        "relatorio_whatsapp", "notificacao_email_criticos", "motorista", "motorista_registra",
        "motorista_localizacao", "vigilante_km", FLAG,
    ):  # fmt: skip
        assert f"'{chave}'" in d, chave


# --- AG040 -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("papel", ["aut", "admin"])
def test_desligada_nega_autorizar_a_propria(cena: Cena, papel: str) -> None:
    ator = getattr(cena, papel)
    rid = cena.req(ator)
    _recusa(h.autorizar, rid, ator)
    assert h.status_de(rid) == "solicitada"


@pytest.mark.parametrize("papel", ["aut", "admin"])
def test_desligada_nega_negar_a_propria(cena: Cena, papel: str) -> None:
    ator = getattr(cena, papel)
    rid = cena.req(ator)
    _recusa(h.negar, rid, ator)
    assert h.status_de(rid) == "solicitada"


@pytest.mark.parametrize("papel", ["aut", "admin"])
def test_ligada_permite_autorizar_e_negar_a_propria(cena: Cena, papel: str) -> None:
    ator = getattr(cena, papel)
    cena.flag(True)
    r1, r2 = cena.req(ator), cena.req(ator)
    h.autorizar(r1, ator)
    h.negar(r2, ator)
    assert (h.status_de(r1), h.status_de(r2)) == ("autorizada", "negada")


def test_requisicao_alheia_segue_permitida_com_a_flag_desligada(cena: Cena) -> None:
    r1, r2, r3 = cena.req(cena.sol), cena.req(cena.aut), cena.req(cena.admin)
    h.autorizar(r1, cena.aut)
    h.autorizar(r2, cena.admin)  # staff decidindo a requisicao de OUTRO staff
    h.negar(r3, cena.aut2)
    assert (h.status_de(r1), h.status_de(r2), h.status_de(r3)) == ("autorizada", "autorizada", "negada")


def test_cancelar_a_propria_nao_depende_da_flag(cena: Cena) -> None:
    rid = cena.req(cena.aut)
    h.cancelar(rid, cena.aut)
    assert h.status_de(rid) == "cancelada"


def test_solicitante_continua_sem_decidir_ag002(cena: Cena) -> None:
    cena.flag(True)
    rid = cena.req(cena.sol)
    with pytest.raises(psycopg.Error) as exc:
        h.autorizar(rid, cena.sol)
    assert h.sqlstate(exc) == "AG002"  # papel, antes da regra da propria


def test_flag_religada_depois_de_negada_volta_a_negar(cena: Cena) -> None:
    cena.flag(True)
    cena.flag(False)
    rid = cena.req(cena.aut)
    _recusa(h.autorizar, rid, cena.aut)


def test_regras_anteriores_continuam_valendo(cena: Cena) -> None:
    """Regressao da reescrita: encerrada congelada (AG001), transicao invalida (AG001), vigilante (AG002)."""
    vig = h.criar_usuario("adp-vig", "vigilante")
    rid = cena.req(cena.sol)
    h.autorizar(rid, cena.aut)
    with pytest.raises(psycopg.Error) as exc:
        h.autorizar(rid, cena.aut2)  # autorizada -> autorizada
    assert h.sqlstate(exc) is not None
    rid2 = cena.req(cena.sol)
    with pytest.raises(psycopg.Error) as exc2:
        h.autorizar(rid2, vig)
    assert h.sqlstate(exc2) == "AG002"
    h.cancelar(rid2, cena.sol)
    with pytest.raises(psycopg.Error) as exc3:
        h.autorizar(rid2, cena.aut)
    assert h.sqlstate(exc3) == "AG001"


def test_requisicoes_validar_nao_e_executavel_pela_api() -> None:
    with h.conectar() as c:
        for papel in ("anon", "authenticated", "service_role"):
            (ok,) = c.execute(
                "select has_function_privilege(%s, 'public.requisicoes_validar()'::regprocedure, 'execute')",
                (papel,),
            ).fetchone() or (True,)
            assert ok is False, papel
