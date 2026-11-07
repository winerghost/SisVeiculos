"""Testes da migration 18: recurso Quilometragem em requisicoes.

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.
Uma fixture autouse (_estado_recursos) le o estado inicial de quilometragem e
abastecimento_manutencao, desliga ambos antes de cada teste (abastecimento primeiro, por
ck_configuracoes_abastecimento_exige_km/AG010) e restaura o original no teardown
(quilometragem primeiro). Cada alternancia gera linha 'configuracao_alterada' em auditoria
(append-only, nao limpavel): rodar SO em banco descartavel. A fixture pula (skip) se o DSN nao
parecer local (localhost/127.0.0.1/::1/socket); TEST_DATABASE_ALLOW_REMOTE=1 forca a execucao.
Rodar sem paralelismo (nao usar pytest-xdist): enquanto ligado, outros testes que registram
saida/chegada sem km falhariam com AG012.

    pytest supabase/tests/test_quilometragem.py -m db_local
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
import os
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass

import pytest

psycopg = pytest.importorskip("psycopg")

from psycopg.conninfo import conninfo_to_dict  # noqa: E402

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

NEGADO = "42501"
CHECK = "23514"


@dataclass
class Atores:
    dono: uuid.UUID
    autorizador: uuid.UUID
    admin: uuid.UUID


@pytest.fixture
def atores() -> Atores:
    return Atores(
        dono=h.criar_usuario("dono"),
        autorizador=h.criar_usuario("autorizador", "autorizador"),
        admin=h.criar_usuario("admin", "admin"),
    )


def _definir_km(ligado: bool, por: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute(
            "update public.configuracoes set quilometragem = %s, alterado_por = %s where id = true",
            (ligado, por),
        )


def _definir_abastecimento(ligado: bool, por: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute(
            "update public.configuracoes set abastecimento_manutencao = %s, alterado_por = %s where id = true",
            (ligado, por),
        )


def _ler_recursos() -> tuple[bool, bool]:
    with h.conectar() as c:
        row = c.execute("select quilometragem, abastecimento_manutencao from public.configuracoes").fetchone()
    assert row is not None
    return bool(row[0]), bool(row[1])


def _dsn_local() -> bool:
    if os.environ.get("TEST_DATABASE_ALLOW_REMOTE") == "1":
        return True
    host = conninfo_to_dict(h.DSN or "").get("host") or ""
    return host == "" or host.startswith("/") or host in ("localhost", "127.0.0.1", "::1")


@pytest.fixture(autouse=True)
def _estado_recursos() -> Iterator[None]:
    """Isola o flag global: desliga antes (abastecimento -> km) e restaura depois (km -> abastecimento)."""
    if not _dsn_local():
        pytest.skip("DSN nao parece banco local/descartavel (TEST_DATABASE_ALLOW_REMOTE=1 forca)")
    orig_km, orig_ab = _ler_recursos()
    por = h.admin_ativo_qualquer() or h.criar_usuario("admin-cfg", "admin")
    if orig_ab:
        _definir_abastecimento(False, por)
    if orig_km:
        _definir_km(False, por)
    try:
        yield
    finally:
        atual_km, atual_ab = _ler_recursos()
        if atual_ab:  # nunca mexer em km com abastecimento ligado (AG010)
            _definir_abastecimento(False, por)
        if atual_km != orig_km:
            _definir_km(orig_km, por)
        if orig_ab:
            _definir_abastecimento(True, por)


@contextmanager
def km_ligada(admin: uuid.UUID) -> Iterator[None]:
    """Liga o recurso; desligar/restaurar e da fixture autouse."""
    _definir_km(True, admin)
    yield


def _autorizada(a: Atores) -> uuid.UUID:
    with h.conectar() as c:
        rid = h.inserir_requisicao(c, a.dono, h.novo_veiculo())
    h.autorizar(rid, a.autorizador)
    return rid


def _iniciar(rid: uuid.UUID, por: uuid.UUID, km: int | None = None) -> None:
    with h.conectar() as c:
        c.execute(
            "update public.requisicoes set status='em_uso', saida_registrada_por=%s, km_saida=%s where id=%s",
            (por, km, rid),
        )


def _concluir(
    rid: uuid.UUID, por: uuid.UUID, km_chegada: int | None = None, km_saida: int | None = None
) -> None:
    with h.conectar() as c:
        c.execute(
            "update public.requisicoes set status='concluida', chegada_registrada_por=%s, "
            "km_chegada=%s, km_saida=coalesce(%s, km_saida) where id=%s",
            (por, km_chegada, km_saida, rid),
        )


def _kms(rid: uuid.UUID) -> tuple[int | None, int | None]:
    with h.conectar() as c:
        row = c.execute("select km_saida, km_chegada from public.requisicoes where id=%s", (rid,)).fetchone()
    assert row is not None
    return row[0], row[1]


def _falha(sql: str, params: tuple, esperado: str) -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(sql, params)
    assert h.sqlstate(exc) == esperado


# --- colunas ---------------------------------------------------------------------
def test_colunas_inteiras_nulas_por_padrao() -> None:
    with h.conectar() as c:
        rows = c.execute(
            "select column_name, data_type, is_nullable, column_default from information_schema.columns "
            "where table_schema='public' and table_name='requisicoes' "
            "and column_name in ('km_saida', 'km_chegada') order by column_name"
        ).fetchall()
    assert rows == [("km_chegada", "integer", "YES", None), ("km_saida", "integer", "YES", None)]


def test_requisicao_nova_nasce_sem_km(atores: Atores) -> None:
    with h.conectar() as c:
        rid = h.inserir_requisicao(c, atores.dono, h.novo_veiculo())
    assert _kms(rid) == (None, None)


def test_insert_com_km_e_recusado(atores: Atores) -> None:
    _falha(
        "insert into public.requisicoes (usuario_id, nome_solicitante, unidade, hora_saida, trajeto, "
        "veiculo_id, km_saida) values (%s, 'T', 'UEA', now(), 'A-B', %s, 10)",
        (atores.dono, h.novo_veiculo()),
        CHECK,
    )


# --- recurso DESLIGADO -------------------------------------------------------------
def test_desligado_km_nao_e_obrigatorio(atores: Atores) -> None:
    rid = _autorizada(atores)
    _iniciar(rid, atores.autorizador)
    _concluir(rid, atores.autorizador)
    assert h.status_de(rid) == "concluida"
    assert _kms(rid) == (None, None)


def test_desligado_banco_nao_descarta_km_valido(atores: Atores) -> None:
    """Fronteira: ignorar a entrada com recurso desligado e do BACKEND; o banco so guarda dado valido."""
    rid = _autorizada(atores)
    _iniciar(rid, atores.autorizador, 100)
    _concluir(rid, atores.autorizador, 150)
    assert _kms(rid) == (100, 150)


# --- recurso LIGADO ----------------------------------------------------------------
def test_ligado_saida_exige_km_saida(atores: Atores) -> None:
    rid = _autorizada(atores)
    with km_ligada(atores.admin):
        with pytest.raises(psycopg.Error) as exc:
            _iniciar(rid, atores.autorizador)
        assert h.sqlstate(exc) == "AG012"
        assert h.status_de(rid) == "autorizada"
        _iniciar(rid, atores.autorizador, 1000)
    assert h.status_de(rid) == "em_uso"
    assert _kms(rid) == (1000, None)


def test_ligado_chegada_exige_km_chegada(atores: Atores) -> None:
    rid = _autorizada(atores)
    with km_ligada(atores.admin):
        _iniciar(rid, atores.autorizador, 1000)
        with pytest.raises(psycopg.Error) as exc:
            _concluir(rid, atores.autorizador)
        assert h.sqlstate(exc) == "AG012"
        assert h.status_de(rid) == "em_uso"
        _concluir(rid, atores.autorizador, 1042)
    assert _kms(rid) == (1000, 1042)


def test_ligado_chegada_igual_a_saida_vale(atores: Atores) -> None:
    rid = _autorizada(atores)
    with km_ligada(atores.admin):
        _iniciar(rid, atores.autorizador, 500)
        _concluir(rid, atores.autorizador, 500)
    assert _kms(rid) == (500, 500)


def test_ligado_com_viagem_em_curso_sem_km_saida(atores: Atores) -> None:
    """Recurso ligado com a viagem ja em uso: a chegada precisa trazer os dois km."""
    rid = _autorizada(atores)
    _iniciar(rid, atores.autorizador)  # desligado
    with km_ligada(atores.admin):
        with pytest.raises(psycopg.Error) as exc:
            _concluir(rid, atores.autorizador, 200)
        assert h.sqlstate(exc) == "AG012"
        _concluir(rid, atores.autorizador, 230, km_saida=200)
    assert _kms(rid) == (200, 230)


# --- invariantes sempre ----------------------------------------------------------------
@pytest.mark.parametrize("ligado", [False, True])
def test_chegada_menor_que_saida_recusada(atores: Atores, ligado: bool) -> None:
    rid = _autorizada(atores)
    ctx: AbstractContextManager[None] = km_ligada(atores.admin) if ligado else nullcontext()
    with ctx:
        _iniciar(rid, atores.autorizador, 1000)
        with pytest.raises(psycopg.Error) as exc:
            _concluir(rid, atores.autorizador, 999)
        assert h.sqlstate(exc) == CHECK
        assert "ck_requisicoes_km_chegada_maior_igual_saida" in str(exc.value)
        assert h.status_de(rid) == "em_uso"


def test_km_negativo_recusado(atores: Atores) -> None:
    rid = _autorizada(atores)
    with pytest.raises(psycopg.Error) as exc:
        _iniciar(rid, atores.autorizador, -1)
    assert h.sqlstate(exc) == CHECK
    assert h.status_de(rid) == "autorizada"


def test_km_chegada_sem_km_saida_recusado(atores: Atores) -> None:
    rid = _autorizada(atores)
    _iniciar(rid, atores.autorizador)
    with pytest.raises(psycopg.Error) as exc:
        _concluir(rid, atores.autorizador, 10)
    assert h.sqlstate(exc) == CHECK
    assert h.status_de(rid) == "em_uso"


def test_km_saida_fora_de_em_uso_recusado(atores: Atores) -> None:
    rid = _autorizada(atores)
    _falha("update public.requisicoes set km_saida = 10 where id = %s", (rid,), "AG004")


def test_km_imutavel_e_so_na_transicao(atores: Atores) -> None:
    rid = _autorizada(atores)
    _iniciar(rid, atores.autorizador)
    # sem transicao: nao pode preencher km depois
    _falha("update public.requisicoes set km_saida = 10 where id = %s", (rid,), "AG004")
    rid2 = _autorizada(atores)
    _iniciar(rid2, atores.autorizador, 10)
    _falha("update public.requisicoes set km_saida = 11 where id = %s", (rid2,), "AG004")
    _concluir(rid2, atores.autorizador, 20)
    # concluida congelada
    _falha("update public.requisicoes set km_chegada = 21 where id = %s", (rid2,), "AG001")


# --- desligar nunca apaga ---------------------------------------------------------------
def test_concluida_nao_altera_km_saida_ja_gravado(atores: Atores) -> None:
    """Na propria transicao para concluida, km_saida ja gravado nao muda (AG004)."""
    rid = _autorizada(atores)
    _iniciar(rid, atores.autorizador, 10)
    with pytest.raises(psycopg.Error) as exc:
        _concluir(rid, atores.autorizador, 20, km_saida=11)
    assert h.sqlstate(exc) == "AG004"
    assert h.status_de(rid) == "em_uso"
    assert _kms(rid) == (10, None)


# --- prioridade de erros entre gatilhos ----------------------------------------------------
def test_ator_invalido_tem_prioridade_sobre_km_obrigatorio(atores: Atores) -> None:
    """trg_requisicoes_validar (AG002) roda antes de trg_requisicoes_validar_km (AG012)."""
    rid = _autorizada(atores)
    inativo = h.criar_usuario("inativo", "autorizador", ativo=False)
    with km_ligada(atores.admin):
        with pytest.raises(psycopg.Error) as exc:
            _iniciar(rid, inativo)  # sem km e com ator inativo
        assert h.sqlstate(exc) == "AG002"
    assert h.status_de(rid) == "autorizada"


def test_ligado_chegada_so_com_km_chegada_da_ag012(atores: Atores) -> None:
    """Mesma transicao, so km_chegada, recurso ligado: AG012 (gatilho) antes do CHECK 23514."""
    rid = _autorizada(atores)
    _iniciar(rid, atores.autorizador)  # desligado: em_uso sem km
    with km_ligada(atores.admin):
        with pytest.raises(psycopg.Error) as exc:
            _concluir(rid, atores.autorizador, 50)
        assert h.sqlstate(exc) == "AG012"
    assert h.status_de(rid) == "em_uso"


# --- desligar nunca apaga ---------------------------------------------------------------
def test_desligar_nao_apaga_dados(atores: Atores) -> None:
    rid = _autorizada(atores)
    with km_ligada(atores.admin):
        _iniciar(rid, atores.autorizador, 300)
        _concluir(rid, atores.autorizador, 345)
    _definir_km(False, atores.admin)
    assert _kms(rid) == (300, 345)
    assert _ler_recursos()[0] is False
    with h.conectar() as c:
        det = c.execute(
            "select detalhes from public.auditoria where tabela='requisicoes' and registro_id=%s "
            "and para='concluida'",
            (rid,),
        ).fetchone()
    assert det is not None and det[0] == {"km_saida": 300, "km_chegada": 345}
    # desligado, uma segunda operacao sem km continua possivel
    rid2 = _autorizada(atores)
    _iniciar(rid2, atores.autorizador)
    _concluir(rid2, atores.autorizador)
    assert h.status_de(rid2) == "concluida"
    assert _kms(rid) == (300, 345)


# --- auditoria --------------------------------------------------------------------------
def test_auditoria_leva_km_sem_dado_pessoal(atores: Atores) -> None:
    rid = _autorizada(atores)
    with km_ligada(atores.admin):
        _iniciar(rid, atores.autorizador, 700)
        _concluir(rid, atores.autorizador, 760)
    with h.conectar() as c:
        rows = c.execute(
            "select para, detalhes from public.auditoria "
            "where tabela='requisicoes' and registro_id=%s and acao='transicao' order by id",
            (rid,),
        ).fetchall()
    por_status = dict(rows)
    assert por_status["em_uso"] == {"km_saida": 700}
    assert por_status["concluida"] == {"km_saida": 700, "km_chegada": 760}
    assert por_status["autorizada"] == {}
    for _, det in rows:
        assert set(det) <= {"km_saida", "km_chegada"}  # nenhum nome/e-mail/placa


def test_auditoria_continua_append_only() -> None:
    for sql in ("update public.auditoria set acao = acao", "delete from public.auditoria"):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(sql)
        assert h.sqlstate(exc) in ("AG003", NEGADO)


# --- RLS / grants inalterados -----------------------------------------------------------------
def test_rls_ligado_e_gatilho_sempre_ativo() -> None:
    with h.conectar() as c:
        (rls,) = c.execute("select relrowsecurity from pg_class where oid = 'public.requisicoes'::regclass").fetchone() or (
            False,
        )
        (tg,) = c.execute(
            "select tgenabled from pg_trigger where tgrelid = 'public.requisicoes'::regclass "
            "and tgname = 'trg_requisicoes_validar_km'"
        ).fetchone() or ("",)
    assert rls is True
    assert tg == "A"  # ENABLE ALWAYS


@pytest.mark.parametrize("papel", ["anon", "authenticated"])
@pytest.mark.parametrize("coluna", ["km_saida", "km_chegada"])
def test_api_nao_le_nem_escreve_km(atores: Atores, papel: str, coluna: str) -> None:
    uid = atores.admin if papel == "authenticated" else None
    for sql in (
        f"select {coluna} from public.requisicoes",
        f"update public.requisicoes set {coluna} = 1",
    ):
        with pytest.raises(psycopg.Error) as exc:
            with h.como(papel, uid) as c:
                c.execute(sql)
        assert h.sqlstate(exc) == NEGADO


def test_service_role_sem_delete_nem_trigger() -> None:
    with h.conectar() as c:
        privs = {
            p: c.execute("select has_table_privilege('service_role', 'public.requisicoes', %s)", (p,)).fetchone()
            for p in ("DELETE", "TRUNCATE", "TRIGGER", "REFERENCES")
        }
    assert all(v == (False,) for v in privs.values()), privs


def test_funcao_de_auditoria_nao_executavel_por_public_anon_authenticated() -> None:
    with h.conectar() as c:
        for papel in ("anon", "authenticated"):
            (ok,) = c.execute(
                "select has_function_privilege(%s, 'public.requisicoes_auditar()', 'execute')", (papel,)
            ).fetchone() or (True,)
            assert ok is False, papel
        (publico,) = c.execute(
            "select exists (select 1 from pg_proc p, "
            "aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
            "where p.oid = 'public.requisicoes_auditar()'::regprocedure "
            "and a.grantee = 0 and a.privilege_type = 'EXECUTE')"
        ).fetchone() or (True,)
        assert publico is False


def test_funcao_do_gatilho_nao_executavel_pela_api() -> None:
    for papel in ("anon", "authenticated"):
        with h.conectar() as c:
            (ok,) = c.execute(
                "select has_function_privilege(%s, 'public.requisicoes_validar_km()', 'execute')", (papel,)
            ).fetchone() or (True,)
        assert ok is False
