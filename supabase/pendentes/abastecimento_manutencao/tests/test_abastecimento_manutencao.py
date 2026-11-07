"""Testes das migrations 25-28: recurso Abastecimento e manutencao (+ alerta de revisao por km).

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.
Uma fixture autouse (_estado_recursos) le o estado de quilometragem, abastecimento_manutencao e
checklist, desliga os tres antes de cada teste (abastecimento -> km -> checklist) e restaura o
original no teardown (km -> abastecimento -> checklist). Cada alternancia gera linha
'configuracao_alterada' em auditoria (append-only, nao limpavel) e os registros criados sao
imutaveis e sem DELETE: rodar SO em banco descartavel. A fixture pula (skip) se o DSN nao parecer
local; TEST_DATABASE_ALLOW_REMOTE=1 forca a execucao. Sem paralelismo (nao usar pytest-xdist).

    pytest supabase/tests/test_abastecimento_manutencao.py -m db_local
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

psycopg = pytest.importorskip("psycopg")

from psycopg.conninfo import conninfo_to_dict

import dbhelpers as h

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

NEGADO = "42501"
CHECK = "23514"
FK = "23503"
UNICO = "23505"
SEGREDO = "SEGREDO-PESSOAL-Maria-99999"
TABELAS = ("abastecimentos", "manutencoes", "abastecimentos_anulacoes", "manutencoes_anulacoes")
VIEWS = (
    "abastecimentos_vigentes",
    "manutencoes_vigentes",
    "odometro_registros_vigentes",
    "revisao_situacao",
)
ACOES_AUDITORIA = (
    "criada", "transicao", "convite_criado", "convite_usado", "convite_revogado",
    "perfil_criado", "perfil_alterado", "perfil_unidade_alterada", "admin_semeado",
    "configuracao_alterada", "veiculo_criado", "veiculo_alterado",
    "acao_administrativa", "acesso_negado", "checklist_registrado",
    "abastecimento_registrado", "abastecimento_anulado",
    "manutencao_registrada", "manutencao_anulada", "revisao_regra_definida",
)  # fmt: skip
TABELAS_AUDITORIA = (
    "requisicoes", "convites", "perfis", "veiculos", "configuracoes", "checklists",
    "abastecimentos", "manutencoes", "revisao_regras",
)  # fmt: skip
SIG_REG_AB = (
    "public.registrar_abastecimento(uuid, uuid, timestamptz, numeric, numeric, integer, "
    "public.tipo_combustivel, uuid)"
)
SIG_REG_MAN = (
    "public.registrar_manutencao(uuid, public.tipo_manutencao, timestamptz, integer, numeric, text, uuid)"
)
SIG_ANU_AB = "public.anular_abastecimento(uuid, public.motivo_anulacao, uuid)"
SIG_ANU_MAN = "public.anular_manutencao(uuid, public.motivo_anulacao, uuid)"
SIG_REGRA = "public.definir_regra_revisao(uuid, integer, integer, uuid)"
RPCS = (SIG_REG_AB, SIG_REG_MAN, SIG_ANU_AB, SIG_ANU_MAN, SIG_REGRA)
GATILHOS = (
    "public.abastecimentos_validar()",
    "public.manutencoes_validar()",
    "public.anulacoes_validar()",
    "public.abastecimentos_auditar()",
    "public.manutencoes_auditar()",
    "public.abastecimentos_anulacoes_auditar()",
    "public.manutencoes_anulacoes_auditar()",
    "public.revisao_regras_validar()",
    "public.revisao_regras_auditar()",
    "privado.exigir_registrador_ativo(uuid)",
    "privado.exigir_odometro(uuid, integer)",
)


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


# --- fixture de flags ---------------------------------------------------------------------
def _ler_recursos() -> tuple[bool, bool, bool]:
    with h.conectar() as c:
        row = c.execute(
            "select quilometragem, abastecimento_manutencao, checklist from public.configuracoes"
        ).fetchone()
    assert row is not None
    return bool(row[0]), bool(row[1]), bool(row[2])


def _definir(coluna: str, ligado: bool, por: uuid.UUID) -> None:
    assert coluna in ("quilometragem", "abastecimento_manutencao", "checklist")
    with h.conectar() as c:
        c.execute(
            f"update public.configuracoes set {coluna} = %s, alterado_por = %s where id = true",
            (ligado, por),
        )


def _dsn_local() -> bool:
    if os.environ.get("TEST_DATABASE_ALLOW_REMOTE") == "1":
        return True
    host = conninfo_to_dict(h.DSN or "").get("host") or ""
    return host == "" or host.startswith("/") or host in ("localhost", "127.0.0.1", "::1")


@pytest.fixture(autouse=True)
def _estado_recursos() -> Iterator[None]:
    """Isola os flags globais: desliga antes (ab -> km -> checklist) e restaura depois."""
    if not _dsn_local():
        pytest.skip("DSN nao parece banco local/descartavel (TEST_DATABASE_ALLOW_REMOTE=1 forca)")
    orig_km, orig_ab, orig_chk = _ler_recursos()
    por = h.admin_ativo_qualquer() or h.criar_usuario("admin-cfg", "admin")
    if orig_ab:
        _definir("abastecimento_manutencao", False, por)
    if orig_km:
        _definir("quilometragem", False, por)
    if orig_chk:
        _definir("checklist", False, por)
    try:
        yield
    finally:
        atual_km, atual_ab, atual_chk = _ler_recursos()
        if atual_ab:  # nunca mexer em km com abastecimento ligado (AG010)
            _definir("abastecimento_manutencao", False, por)
        if atual_km != orig_km:
            _definir("quilometragem", orig_km, por)
        if orig_ab:
            _definir("abastecimento_manutencao", True, por)
        if atual_chk != orig_chk:
            _definir("checklist", orig_chk, por)


# --- helpers de dominio ----------------------------------------------------------------------
def _agora(**delta: float) -> datetime:
    return datetime.now(UTC) + timedelta(**delta)


def _reg_ab(
    veiculo: uuid.UUID,
    km: int,
    por: uuid.UUID,
    *,
    requisicao: uuid.UUID | None = None,
    quando: datetime | None = None,
    litros: Decimal | int | str = "40.50",
    valor: Decimal | int | str | None = "250.00",
    combustivel: str = "gasolina",
    conn: psycopg.Connection | None = None,
) -> uuid.UUID:
    sql = (
        "select public.registrar_abastecimento(%s::uuid, %s::uuid, %s::timestamptz, %s::numeric, "
        "%s::numeric, %s::integer, %s::public.tipo_combustivel, %s::uuid)"
    )
    params = (
        veiculo, requisicao, quando or _agora(hours=-1), str(litros),
        None if valor is None else str(valor), km, combustivel, por,
    )  # fmt: skip
    if conn is None:
        with h.conectar() as c:
            row = c.execute(sql, params).fetchone()
    else:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return row[0]


def _reg_man(
    veiculo: uuid.UUID,
    km: int,
    por: uuid.UUID,
    *,
    tipo: str = "troca_oleo",
    quando: datetime | None = None,
    valor: Decimal | int | str | None = "300.00",
    descricao: str | None = None,
    conn: psycopg.Connection | None = None,
) -> uuid.UUID:
    sql = (
        "select public.registrar_manutencao(%s::uuid, %s::public.tipo_manutencao, %s::timestamptz, "
        "%s::integer, %s::numeric, %s::text, %s::uuid)"
    )
    params = (
        veiculo, tipo, quando or _agora(hours=-1), km,
        None if valor is None else str(valor), descricao, por,
    )  # fmt: skip
    if conn is None:
        with h.conectar() as c:
            row = c.execute(sql, params).fetchone()
    else:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return row[0]


def _anular(tabela: str, rid: uuid.UUID, por: uuid.UUID, motivo: str = "erro_de_digitacao") -> None:
    fn = "anular_abastecimento" if tabela == "abastecimentos" else "anular_manutencao"
    with h.conectar() as c:
        c.execute(f"select public.{fn}(%s::uuid, %s::public.motivo_anulacao, %s::uuid)", (rid, motivo, por))


def _falha(esperado: str, fn, *args, **kw) -> pytest.ExceptionInfo[psycopg.Error]:  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.Error) as exc:
        fn(*args, **kw)
    assert h.sqlstate(exc) == esperado, str(exc.value)
    return exc


def _requisicao(a: Atores, veiculo: uuid.UUID) -> uuid.UUID:
    with h.conectar() as c:
        return h.inserir_requisicao(c, a.dono, veiculo)


def _vigentes_do_veiculo(veiculo: uuid.UUID) -> list[int]:
    with h.conectar() as c:
        rows = c.execute(
            "select km from public.odometro_registros_vigentes where veiculo_id=%s order by registrado_em",
            (veiculo,),
        ).fetchall()
    return [r[0] for r in rows]


def _auditoria(tabela: str, registro_id: uuid.UUID) -> list[tuple]:
    with h.conectar() as c:
        return c.execute(
            "select acao, ator_id, de, para, detalhes from public.auditoria "
            "where tabela=%s and registro_id=%s order by id",
            (tabela, registro_id),
        ).fetchall()


def _sem_conteudo(exc: pytest.ExceptionInfo[psycopg.Error]) -> None:
    """M1: nem a mensagem nem o DETAIL carregam a descricao; nao ha 'Failing row'."""
    diag = exc.value.diag
    assert SEGREDO not in str(exc.value)
    assert SEGREDO not in (diag.message_primary or "")
    assert SEGREDO not in (diag.message_detail or "")
    assert "Failing row" not in str(exc.value)
    assert not diag.message_detail


# --- flags / dependencia ------------------------------------------------------------------------
def test_flag_desligada_por_padrao_e_constraint_de_dependencia_existe() -> None:
    with h.conectar() as c:
        (padrao,) = c.execute(
            "select column_default from information_schema.columns where table_schema='public' "
            "and table_name='configuracoes' and column_name='abastecimento_manutencao'"
        ).fetchone() or (None,)
        (n,) = c.execute(
            "select count(*) from pg_constraint where conrelid='public.configuracoes'::regclass "
            "and conname='ck_configuracoes_abastecimento_exige_km'"
        ).fetchone() or (0,)
    assert padrao == "false"
    assert n == 1


def test_ligar_abastecimento_sem_quilometragem_e_recusado(atores: Atores) -> None:
    with pytest.raises(psycopg.Error) as exc:
        _definir("abastecimento_manutencao", True, atores.admin)
    assert h.sqlstate(exc) == "AG010"


def test_desligar_quilometragem_com_abastecimento_ligado_e_recusado(atores: Atores) -> None:
    _definir("quilometragem", True, atores.admin)
    _definir("abastecimento_manutencao", True, atores.admin)
    with pytest.raises(psycopg.Error) as exc:
        _definir("quilometragem", False, atores.admin)
    assert h.sqlstate(exc) == "AG010"


def test_desligado_o_banco_nao_exige_nada_e_ligado_tambem_aceita(atores: Atores) -> None:
    v = h.novo_veiculo()
    _reg_ab(v, 100, atores.autorizador)  # recurso desligado: o backend recusa, o banco nao
    _definir("quilometragem", True, atores.admin)
    _definir("abastecimento_manutencao", True, atores.admin)
    _reg_ab(v, 150, atores.autorizador)


def test_desligar_nao_apaga_registros(atores: Atores) -> None:
    v = h.novo_veiculo()
    _definir("quilometragem", True, atores.admin)
    _definir("abastecimento_manutencao", True, atores.admin)
    ab = _reg_ab(v, 100, atores.autorizador)
    man = _reg_man(v, 120, atores.autorizador)
    _definir("abastecimento_manutencao", False, atores.admin)
    _definir("quilometragem", False, atores.admin)
    assert _vigentes_do_veiculo(v) == [100, 120]
    with h.conectar() as c:
        for tabela, rid in (("abastecimentos", ab), ("manutencoes", man)):
            (n,) = c.execute(f"select count(*) from public.{tabela} where id=%s", (rid,)).fetchone() or (0,)
            assert n == 1


# --- estrutura ----------------------------------------------------------------------------------
def test_gatilhos_sempre_ativos() -> None:
    esperados = {
        "trg_abastecimentos_imutavel", "trg_abastecimentos_sem_truncate", "trg_abastecimentos_validar",
        "trg_abastecimentos_auditoria", "trg_manutencoes_imutavel", "trg_manutencoes_sem_truncate",
        "trg_manutencoes_validar", "trg_manutencoes_auditoria",
        "trg_abastecimentos_anulacoes_imutavel", "trg_abastecimentos_anulacoes_sem_truncate",
        "trg_abastecimentos_anulacoes_validar", "trg_abastecimentos_anulacoes_auditoria",
        "trg_manutencoes_anulacoes_imutavel", "trg_manutencoes_anulacoes_sem_truncate",
        "trg_manutencoes_anulacoes_validar", "trg_manutencoes_anulacoes_auditoria",
        "trg_revisao_regras_sem_delete", "trg_revisao_regras_sem_truncate",
        "trg_revisao_regras_validar", "trg_revisao_regras_auditoria",
    }  # fmt: skip
    with h.conectar() as c:
        rows = dict(
            c.execute(
                "select tgname, tgenabled from pg_trigger where not tgisinternal and tgrelid in "
                "('public.abastecimentos'::regclass, 'public.manutencoes'::regclass, "
                "'public.abastecimentos_anulacoes'::regclass, 'public.manutencoes_anulacoes'::regclass, "
                "'public.revisao_regras'::regclass)"
            ).fetchall()
        )
    assert esperados <= set(rows), esperados - set(rows)
    assert all(rows[n] == "A" for n in esperados)  # ENABLE ALWAYS


# --- valores invalidos --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("kw", "constraint"),
    [
        ({"litros": "0"}, "ck_abastecimentos_litros_positivo"),
        ({"litros": "-1"}, "ck_abastecimentos_litros_positivo"),
        ({"valor": "-0.01"}, "ck_abastecimentos_valor_nao_negativo"),
    ],
)
def test_abastecimento_valores_invalidos(atores: Atores, kw: dict, constraint: str) -> None:
    exc = _falha(CHECK, _reg_ab, h.novo_veiculo(), 100, atores.autorizador, **kw)
    assert exc.value.diag.constraint_name == constraint


def test_abastecimento_km_negativo(atores: Atores) -> None:
    exc = _falha(CHECK, _reg_ab, h.novo_veiculo(), -1, atores.autorizador)
    assert exc.value.diag.constraint_name == "ck_abastecimentos_km_nao_negativo"


def test_abastecimento_litros_estoura_precisao(atores: Atores) -> None:
    _falha("22003", _reg_ab, h.novo_veiculo(), 100, atores.autorizador, litros="10000")


def test_abastecimento_aceita_limites_e_valor_opcional(atores: Atores) -> None:
    v = h.novo_veiculo()
    _reg_ab(v, 0, atores.autorizador, litros="0.01", valor=None)
    _reg_ab(v, 0, atores.autorizador, litros="9999.99", valor="0")


def test_abastecimento_combustivel_fora_do_enum(atores: Atores) -> None:
    _falha("22P02", _reg_ab, h.novo_veiculo(), 100, atores.autorizador, combustivel="querosene")


def test_abastecimento_data_futura_recusada(atores: Atores) -> None:
    exc = _falha(CHECK, _reg_ab, h.novo_veiculo(), 100, atores.autorizador, quando=_agora(days=1))
    assert exc.value.diag.constraint_name == "ck_abastecimentos_data_nao_futura"


def test_abastecimento_veiculo_inexistente(atores: Atores) -> None:
    _falha(FK, _reg_ab, uuid.uuid4(), 100, atores.autorizador)


def test_abastecimento_requisicao_do_mesmo_veiculo_vale(atores: Atores) -> None:
    v = h.novo_veiculo()
    _reg_ab(v, 100, atores.autorizador, requisicao=_requisicao(atores, v))


def test_abastecimento_requisicao_de_outro_veiculo(atores: Atores) -> None:
    v, outro = h.novo_veiculo(), h.novo_veiculo()
    _falha("AG016", _reg_ab, v, 100, atores.autorizador, requisicao=_requisicao(atores, outro))


def test_abastecimento_requisicao_inexistente(atores: Atores) -> None:
    _falha(FK, _reg_ab, h.novo_veiculo(), 100, atores.autorizador, requisicao=uuid.uuid4())


@pytest.mark.parametrize("papel", ["dono", "inativo"])
def test_so_autorizador_ou_admin_ativo_registra(atores: Atores, papel: str) -> None:
    por = atores.dono if papel == "dono" else h.criar_usuario("inativo", "autorizador", ativo=False)
    v = h.novo_veiculo()
    _falha("AG002", _reg_ab, v, 100, por)
    _falha("AG002", _reg_man, v, 100, por)


def test_admin_registra(atores: Atores) -> None:
    v = h.novo_veiculo()
    _reg_ab(v, 100, atores.admin)
    _reg_man(v, 100, atores.admin)


@pytest.mark.parametrize("tipo", ["revisao", "troca_oleo", "pneus", "freios", "outro"])
def test_manutencao_tipos_do_enum(atores: Atores, tipo: str) -> None:
    _reg_man(h.novo_veiculo(), 100, atores.autorizador, tipo=tipo)


def test_manutencao_tipo_fora_do_enum(atores: Atores) -> None:
    _falha("22P02", _reg_man, h.novo_veiculo(), 100, atores.autorizador, tipo="lanternagem")


@pytest.mark.parametrize(
    ("kw", "constraint"),
    [
        ({"km": -1}, "ck_manutencoes_km_nao_negativo"),
        ({"valor": "-5"}, "ck_manutencoes_valor_nao_negativo"),
        ({"descricao": "x" * 201}, "ck_manutencoes_descricao"),
        ({"descricao": " \n\t "}, "ck_manutencoes_descricao"),
        ({"descricao": ""}, "ck_manutencoes_descricao"),
        ({"quando": _agora(days=2)}, "ck_manutencoes_data_nao_futura"),
    ],
)
def test_manutencao_valores_invalidos(atores: Atores, kw: dict, constraint: str) -> None:
    kw = {"km": 100, **kw}
    km = kw.pop("km")
    exc = _falha(CHECK, _reg_man, h.novo_veiculo(), km, atores.autorizador, **kw)
    assert exc.value.diag.constraint_name == constraint


def test_manutencao_descricao_no_limite_e_opcional(atores: Atores) -> None:
    v = h.novo_veiculo()
    _reg_man(v, 100, atores.autorizador, descricao="x" * 200, valor=None)
    _reg_man(v, 100, atores.autorizador, descricao=None)


@pytest.mark.parametrize("campo", ["veiculo_id", "tipo", "realizada_em", "km", "registrado_por", "id"])
def test_manutencao_campo_obrigatorio_nulo_sem_conteudo(atores: Atores, campo: str) -> None:
    v = h.novo_veiculo()
    cols = {
        "id": uuid.uuid4(), "veiculo_id": v, "tipo": "revisao", "realizada_em": _agora(hours=-1),
        "km": 10, "registrado_por": atores.autorizador, "descricao": SEGREDO,
    }  # fmt: skip
    cols[campo] = None  # type: ignore[assignment]
    nomes = ", ".join(cols)
    marcas = ", ".join("%s::public.tipo_manutencao" if k == "tipo" else "%s" for k in cols)
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(f"insert into public.manutencoes ({nomes}) values ({marcas})", tuple(cols.values()))
    assert h.sqlstate(exc) == "23502"
    assert exc.value.diag.column_name == campo
    assert exc.value.diag.table_name == "manutencoes"
    _sem_conteudo(exc)


@pytest.mark.parametrize(
    "kw",
    [
        {"km": -1, "descricao": SEGREDO},
        {"valor": "-1", "descricao": SEGREDO},
        {"quando": _agora(days=3), "descricao": SEGREDO},
        {"descricao": SEGREDO + "x" * 300},
        {"descricao": SEGREDO, "tipo": "outro"},
    ],
)
def test_manutencao_erros_nao_vazam_a_descricao(atores: Atores, kw: dict) -> None:
    km = kw.pop("km", 100)
    em_uso_valido = kw.get("quando") is None and kw.get("valor") is None and km >= 0 and len(kw["descricao"]) <= 200
    por = atores.dono if em_uso_valido else atores.autorizador  # ator invalido tambem nao vaza
    with pytest.raises(psycopg.Error) as exc:
        _reg_man(h.novo_veiculo(), km, por, **kw)
    assert h.sqlstate(exc) in (CHECK, "AG002")
    _sem_conteudo(exc)


def test_manutencao_insert_direto_tambem_sem_conteudo(atores: Atores) -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                "insert into public.manutencoes (veiculo_id, tipo, realizada_em, km, descricao, registrado_por) "
                "values (%s, 'outro', now(), -5, %s, %s)",
                (h.novo_veiculo(), SEGREDO, atores.autorizador),
            )
    assert h.sqlstate(exc) == CHECK
    _sem_conteudo(exc)


def test_carimbo_do_servidor_sobrescreve_registrado_em(atores: Atores) -> None:
    v = h.novo_veiculo()
    with h.conectar() as c:
        c.execute(
            "insert into public.abastecimentos (veiculo_id, abastecido_em, litros, km, combustivel, "
            "registrado_por, registrado_em) values (%s, now(), 1, 10, 'etanol', %s, '2000-01-01')",
            (v, atores.autorizador),
        )
        (ano,) = c.execute(
            "select extract(year from registrado_em)::int from public.abastecimentos where veiculo_id=%s", (v,)
        ).fetchone() or (0,)
    assert ano >= 2026


# --- imutabilidade --------------------------------------------------------------------------------
@pytest.mark.parametrize("tabela", ["abastecimentos", "manutencoes"])
def test_registros_imutaveis(atores: Atores, tabela: str) -> None:
    v = h.novo_veiculo()
    rid = _reg_ab(v, 100, atores.autorizador) if tabela == "abastecimentos" else _reg_man(v, 100, atores.autorizador)
    for sql in (f"update public.{tabela} set km = 1 where id = %s", f"delete from public.{tabela} where id = %s"):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(sql, (rid,))
        assert h.sqlstate(exc) == "AG003", sql


@pytest.mark.parametrize("tabela", ["abastecimentos", "manutencoes"])
def test_anulacoes_imutaveis(atores: Atores, tabela: str) -> None:
    v = h.novo_veiculo()
    rid = _reg_ab(v, 100, atores.autorizador) if tabela == "abastecimentos" else _reg_man(v, 100, atores.autorizador)
    _anular(tabela, rid, atores.autorizador)
    col = "abastecimento_id" if tabela == "abastecimentos" else "manutencao_id"
    for sql in (
        f"update public.{tabela}_anulacoes set motivo = 'duplicado' where {col} = %s",
        f"delete from public.{tabela}_anulacoes where {col} = %s",
    ):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(sql, (rid,))
        assert h.sqlstate(exc) == "AG003", sql


@pytest.mark.parametrize("tabela", [*TABELAS, "revisao_regras"])
def test_sem_truncate(tabela: str) -> None:
    # tabelas referenciadas por FK: o Postgres pode recusar (0A000) antes do gatilho.
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(f"truncate table public.{tabela}")
    assert h.sqlstate(exc) in ("AG003", "0A000")


def test_registro_com_abastecimento_nao_apaga_requisicao_nem_veiculo(atores: Atores) -> None:
    v = h.novo_veiculo()
    rid = _requisicao(atores, v)
    _reg_ab(v, 100, atores.autorizador, requisicao=rid)
    for sql in ("delete from public.requisicoes where id = %s", "delete from public.veiculos where id = %s"):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(sql, (rid if "requisicoes" in sql else v,))
        assert h.sqlstate(exc) == "AG003"


# --- odometro ----------------------------------------------------------------------------------------
def test_odometro_nao_retrocede_entre_abastecimentos(atores: Atores) -> None:
    v = h.novo_veiculo()
    _reg_ab(v, 1000, atores.autorizador)
    _falha("AG015", _reg_ab, v, 999, atores.autorizador)
    _reg_ab(v, 1000, atores.autorizador)  # igual e aceito
    _reg_ab(v, 1001, atores.autorizador)


def test_odometro_vale_entre_as_duas_tabelas(atores: Atores) -> None:
    v = h.novo_veiculo()
    _reg_man(v, 5000, atores.autorizador)
    _falha("AG015", _reg_ab, v, 4999, atores.autorizador)
    _reg_ab(v, 6000, atores.autorizador)
    _falha("AG015", _reg_man, v, 5999, atores.autorizador)


def test_odometro_e_por_veiculo(atores: Atores) -> None:
    a, b = h.novo_veiculo(), h.novo_veiculo()
    _reg_ab(a, 9000, atores.autorizador)
    _reg_ab(b, 10, atores.autorizador)


def test_odometro_primeiro_registro_de_veiculo_novo_aceita_qualquer_km(atores: Atores) -> None:
    _reg_ab(h.novo_veiculo(), 0, atores.autorizador)


def test_anular_o_ultimo_libera_km_menor(atores: Atores) -> None:
    v = h.novo_veiculo()
    _reg_ab(v, 1000, atores.autorizador)
    digitado_errado = _reg_ab(v, 99999, atores.autorizador)
    _falha("AG015", _reg_ab, v, 1500, atores.autorizador)
    _anular("abastecimentos", digitado_errado, atores.autorizador)
    _reg_ab(v, 1500, atores.autorizador)
    assert _vigentes_do_veiculo(v) == [1000, 1500]


def test_anular_no_meio_nunca_quebra_a_sequencia(atores: Atores) -> None:
    v = h.novo_veiculo()
    _reg_ab(v, 100, atores.autorizador)
    meio = _reg_man(v, 200, atores.autorizador)
    _reg_ab(v, 300, atores.autorizador)
    _anular("manutencoes", meio, atores.autorizador, "duplicado")
    ks = _vigentes_do_veiculo(v)
    assert ks == [100, 300] and ks == sorted(ks)
    _falha("AG015", _reg_ab, v, 299, atores.autorizador)


def test_anulacao_dupla_recusada(atores: Atores) -> None:
    v = h.novo_veiculo()
    rid = _reg_ab(v, 100, atores.autorizador)
    _anular("abastecimentos", rid, atores.autorizador)
    exc = _falha(UNICO, _anular, "abastecimentos", rid, atores.autorizador)
    assert exc.value.diag.constraint_name == "pk_abastecimentos_anulacoes"


def test_anular_registro_inexistente(atores: Atores) -> None:
    _falha(FK, _anular, "abastecimentos", uuid.uuid4(), atores.autorizador)
    _falha(FK, _anular, "manutencoes", uuid.uuid4(), atores.autorizador)


def test_anular_exige_autorizador_ou_admin_ativo(atores: Atores) -> None:
    v = h.novo_veiculo()
    rid = _reg_man(v, 100, atores.autorizador)
    _falha("AG002", _anular, "manutencoes", rid, atores.dono)
    _anular("manutencoes", rid, atores.admin, "registro_indevido")


def test_anular_motivo_fora_do_enum(atores: Atores) -> None:
    rid = _reg_ab(h.novo_veiculo(), 100, atores.autorizador)
    _falha("22P02", _anular, "abastecimentos", rid, atores.autorizador, "porque_sim")


def test_views_vigentes_excluem_anulados(atores: Atores) -> None:
    v = h.novo_veiculo()
    a1 = _reg_ab(v, 100, atores.autorizador)
    a2 = _reg_ab(v, 200, atores.autorizador)
    _anular("abastecimentos", a1, atores.autorizador)
    with h.conectar() as c:
        ids = {r[0] for r in c.execute("select id from public.abastecimentos_vigentes where veiculo_id=%s", (v,))}
        (todos,) = c.execute("select count(*) from public.abastecimentos where veiculo_id=%s", (v,)).fetchone() or (0,)
    assert ids == {a2} and todos == 2  # o original permanece


# --- concorrencia ---------------------------------------------------------------------------------------
def test_lock_do_odometro_serializa_o_mesmo_veiculo(atores: Atores) -> None:
    """Determinístico: a 1a transacao (nao commitada) segura o advisory; a 2a espera (55P03) e,
    depois do commit, enxerga o km da 1a e e recusada (AG015)."""
    v = h.novo_veiculo()
    _reg_ab(v, 1000, atores.autorizador)
    with h.conectar() as c1, h.conectar() as c2:
        with c1.transaction():
            _reg_ab(v, 3000, atores.autorizador, conn=c1)
            c2.execute("set lock_timeout = '800ms'")
            with pytest.raises(psycopg.Error) as exc:
                _reg_ab(v, 2000, atores.autorizador, conn=c2)
            assert h.sqlstate(exc) == "55P03"
        # c1 comitou
        c2.execute("set lock_timeout = '5s'")
        with pytest.raises(psycopg.Error) as exc2:
            _reg_ab(v, 2000, atores.autorizador, conn=c2)
        assert h.sqlstate(exc2) == "AG015"
    assert _vigentes_do_veiculo(v) == [1000, 3000]


def test_lock_do_odometro_nao_bloqueia_outro_veiculo(atores: Atores) -> None:
    a, b = h.novo_veiculo(), h.novo_veiculo()
    with h.conectar() as c1, h.conectar() as c2:
        with c1.transaction():
            _reg_ab(a, 3000, atores.autorizador, conn=c1)
            c2.execute("set lock_timeout = '800ms'")
            _reg_ab(b, 10, atores.autorizador, conn=c2)  # nao espera


def test_corrida_de_dois_registros_do_mesmo_veiculo_mantem_a_sequencia(atores: Atores) -> None:
    """Sem o lock, 2000 e 1500 poderiam entrar nesta ordem. Com ele, a sequencia por ordem de
    registro e sempre nao decrescente (ou o segundo e recusado com AG015)."""
    for _ in range(10):
        v = h.novo_veiculo()
        _reg_ab(v, 1000, atores.autorizador)
        barreira = threading.Barrier(2)
        conexoes = [h.conectar(), h.conectar()]  # abertas antes da barreira

        def tentar(
            par: tuple[psycopg.Connection, int], v: uuid.UUID = v, barreira: threading.Barrier = barreira
        ) -> tuple[str, object]:
            c, km = par
            barreira.wait(timeout=10)
            try:
                _reg_ab(v, km, atores.autorizador, conn=c)
                return "ok", km
            except psycopg.Error as e:
                return "erro", e.sqlstate

        try:
            with ThreadPoolExecutor(max_workers=2) as ex:
                resultados = list(ex.map(tentar, zip(conexoes, (2000, 1500))))
        finally:
            for c in conexoes:
                c.close()
        ks = _vigentes_do_veiculo(v)
        assert ks == sorted(ks), (ks, resultados)
        assert all(r[0] == "ok" or r[1] == "AG015" for r in resultados), resultados
        assert ks[0] == 1000 and len(ks) == 1 + sum(1 for r in resultados if r[0] == "ok")


# --- auditoria -------------------------------------------------------------------------------------------
def test_auditoria_do_abastecimento_sem_dado_pessoal(atores: Atores) -> None:
    v = h.novo_veiculo()
    rq = _requisicao(atores, v)
    rid = _reg_ab(v, 1234, atores.autorizador, requisicao=rq, combustivel="diesel", litros="50", valor="321.99")
    rows = _auditoria("abastecimentos", rid)
    assert len(rows) == 1
    acao, ator, de, para, det = rows[0]
    assert (acao, ator, de, para) == ("abastecimento_registrado", atores.autorizador, None, None)
    assert det == {"veiculo_id": str(v), "requisicao_id": str(rq), "km": 1234, "combustivel": "diesel"}
    assert "321" not in str(rows[0]) and "50" not in str(det.get("litros", ""))


def test_auditoria_da_manutencao_nao_leva_a_descricao(atores: Atores) -> None:
    v = h.novo_veiculo()
    rid = _reg_man(v, 777, atores.autorizador, tipo="freios", descricao=f"Pastilha, falar com {SEGREDO}", valor="10")
    rows = _auditoria("manutencoes", rid)
    assert len(rows) == 1
    acao, ator, de, para, det = rows[0]
    assert (acao, ator, de, para) == ("manutencao_registrada", atores.autorizador, None, "freios")
    assert det == {"veiculo_id": str(v), "tipo": "freios", "km": 777}
    assert SEGREDO not in str(rows[0]) and "Pastilha" not in str(rows[0])


def test_auditoria_da_anulacao(atores: Atores) -> None:
    v = h.novo_veiculo()
    ab, man = _reg_ab(v, 1, atores.autorizador), _reg_man(v, 2, atores.autorizador)
    _anular("abastecimentos", ab, atores.admin, "duplicado")
    _anular("manutencoes", man, atores.admin, "registro_indevido")
    assert _auditoria("abastecimentos", ab)[1][:4] == ("abastecimento_anulado", atores.admin, None, "duplicado")
    assert _auditoria("manutencoes", man)[1][:4] == ("manutencao_anulada", atores.admin, None, "registro_indevido")


def test_registro_recusado_nao_deixa_auditoria(atores: Atores) -> None:
    v = h.novo_veiculo()
    _reg_ab(v, 1000, atores.autorizador)
    _falha("AG015", _reg_ab, v, 1, atores.autorizador)
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.auditoria where tabela='abastecimentos' and detalhes->>'veiculo_id'=%s",
            (str(v),),
        ).fetchone() or (-1,)
    assert n == 1


def test_auditoria_preserva_todos_os_valores_antigos_e_acrescenta_os_novos() -> None:
    with h.conectar() as c:
        defs = dict(
            c.execute(
                "select conname, pg_get_constraintdef(oid) from pg_constraint "
                "where conrelid='public.auditoria'::regclass "
                "and conname in ('ck_auditoria_acao', 'ck_auditoria_tabela')"
            ).fetchall()
        )
    assert set(defs) == {"ck_auditoria_acao", "ck_auditoria_tabela"}
    assert len(ACOES_AUDITORIA) == 20 and len(TABELAS_AUDITORIA) == 9
    for v in ACOES_AUDITORIA:
        assert f"'{v}'" in defs["ck_auditoria_acao"], v
    for v in TABELAS_AUDITORIA:
        assert f"'{v}'" in defs["ck_auditoria_tabela"], v


@pytest.mark.parametrize(
    ("tabela", "acao", "constraint"),
    [
        ("requisicoes", "acao_desconhecida", "ck_auditoria_acao"),
        ("tabela_desconhecida", "criada", "ck_auditoria_tabela"),
    ],
)
def test_valor_desconhecido_de_auditoria_e_recusado(tabela: str, acao: str, constraint: str) -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("insert into public.auditoria (tabela, acao) values (%s, %s)", (tabela, acao))
    assert h.sqlstate(exc) == CHECK
    assert exc.value.diag.constraint_name == constraint


def test_registrar_auditoria_do_backend_continua_restrito() -> None:
    for acao in ("abastecimento_registrado", "revisao_regra_definida"):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute("select public.registrar_auditoria('abastecimentos', null, %s, null)", (acao,))
        assert h.sqlstate(exc) == "AG003"


def test_auditoria_continua_append_only() -> None:
    for sql in ("update public.auditoria set acao = acao", "delete from public.auditoria"):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(sql)
        assert h.sqlstate(exc) in ("AG003", NEGADO)


# --- RLS e grants --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tabela", [*TABELAS, "revisao_regras"])
def test_rls_ligado_e_sem_privilegios_diretos(tabela: str) -> None:
    with h.conectar() as c:
        (rls,) = c.execute(
            "select relrowsecurity from pg_class where oid = %s::regclass", (f"public.{tabela}",)
        ).fetchone() or (False,)
        assert rls is True
        for papel in ("anon", "authenticated"):
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                (ok,) = c.execute(
                    "select has_table_privilege(%s, %s, %s)", (papel, f"public.{tabela}", priv)
                ).fetchone() or (True,)
                assert ok is False, (papel, priv)
        for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            (ok,) = c.execute(
                "select has_table_privilege('service_role', %s, %s)", (f"public.{tabela}", priv)
            ).fetchone() or (True,)
            assert ok is False, priv
        (sel,) = c.execute(
            "select has_table_privilege('service_role', %s, 'SELECT')", (f"public.{tabela}",)
        ).fetchone() or (False,)
        assert sel is True


@pytest.mark.parametrize("objeto", [*TABELAS, "revisao_regras", *VIEWS])
@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_api_nao_le_nem_escreve(atores: Atores, objeto: str, papel: str) -> None:
    uid = atores.admin if papel == "authenticated" else None
    sqls = [f"select * from public.{objeto}"]
    if objeto not in VIEWS:
        sqls += [f"update public.{objeto} set veiculo_id = veiculo_id" if "veiculo_id" in _colunas(objeto) else
                 f"update public.{objeto} set motivo = motivo", f"delete from public.{objeto}"]
    for sql in sqls:
        with pytest.raises(psycopg.Error) as exc:
            with h.como(papel, uid) as c:
                c.execute(sql)
        assert h.sqlstate(exc) == NEGADO, sql


def _colunas(tabela: str) -> set[str]:
    with h.conectar() as c:
        return {
            r[0]
            for r in c.execute(
                "select column_name from information_schema.columns where table_schema='public' and table_name=%s",
                (tabela,),
            ).fetchall()
        }


@pytest.mark.parametrize("view", VIEWS)
def test_views_so_service_role_le_e_sao_security_invoker(view: str) -> None:
    with h.conectar() as c:
        (sel,) = c.execute("select has_table_privilege('service_role', %s, 'SELECT')", (f"public.{view}",)).fetchone() or (
            False,
        )
        (opts,) = c.execute("select reloptions from pg_class where oid=%s::regclass", (f"public.{view}",)).fetchone() or (
            None,
        )
    assert sel is True
    assert opts and "security_invoker=true" in opts
    with h.como("service_role") as c:
        c.execute(f"select * from public.{view} limit 1")


def test_service_role_nao_escreve_direto_mas_chama_as_rpcs(atores: Atores) -> None:
    v = h.novo_veiculo()
    with pytest.raises(psycopg.Error) as exc:
        with h.como("service_role") as c:
            c.execute(
                "insert into public.abastecimentos (veiculo_id, abastecido_em, litros, km, combustivel, "
                "registrado_por) values (%s, now(), 1, 1, 'etanol', %s)",
                (v, atores.autorizador),
            )
    assert h.sqlstate(exc) == NEGADO
    with pytest.raises(psycopg.Error) as exc2:
        with h.como("service_role") as c:
            c.execute(
                "insert into public.manutencoes_anulacoes (manutencao_id, motivo, anulado_por) "
                "values (%s, 'duplicado', %s)",
                (uuid.uuid4(), atores.autorizador),
            )
    assert h.sqlstate(exc2) == NEGADO
    with h.como("service_role") as c:  # RPCs funcionam (rollback ao sair)
        ab = _reg_ab(v, 1, atores.autorizador, conn=c)
        c.execute(
            "select public.anular_abastecimento(%s::uuid, 'duplicado'::public.motivo_anulacao, %s::uuid)",
            (ab, atores.autorizador),
        )
        _reg_man(v, 1, atores.autorizador, conn=c)


@pytest.mark.parametrize("assinatura", [*RPCS, *GATILHOS])
def test_funcoes_nao_executaveis_por_public_anon_authenticated(assinatura: str) -> None:
    with h.conectar() as c:
        for papel in ("anon", "authenticated"):
            (ok,) = c.execute(
                "select has_function_privilege(%s, %s::regprocedure, 'execute')", (papel, assinatura)
            ).fetchone() or (True,)
            assert ok is False, papel
        (publico,) = c.execute(
            "select exists (select 1 from pg_proc p, "
            "aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
            "where p.oid = %s::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE')",
            (assinatura,),
        ).fetchone() or (True,)
        assert publico is False
        (sr,) = c.execute(
            "select has_function_privilege('service_role', %s::regprocedure, 'execute')", (assinatura,)
        ).fetchone() or (None,)
        assert sr is (assinatura in RPCS), assinatura  # RPC sim; gatilhos/privadas nao


def test_funcoes_security_definer_com_search_path_vazio() -> None:
    with h.conectar() as c:
        for assinatura in [*RPCS, *GATILHOS]:
            row = c.execute(
                "select prosecdef, proconfig from pg_proc where oid = %s::regprocedure", (assinatura,)
            ).fetchone()
            assert row is not None
            secdef, config = row
            assert secdef is True, assinatura
            assert config and any(x.replace(" ", "") in ("search_path=", 'search_path=""') for x in config), assinatura


@pytest.mark.parametrize("tabela", ["perfis", "veiculos", "requisicoes", "auditoria", "configuracoes", "convites"])
def test_tabelas_antigas_continuam_fechadas(tabela: str) -> None:
    with h.conectar() as c:
        (rls,) = c.execute(
            "select relrowsecurity from pg_class where oid = %s::regclass", (f"public.{tabela}",)
        ).fetchone() or (False,)
        assert rls is True
        for papel in ("anon", "authenticated"):
            (ok,) = c.execute(
                "select has_table_privilege(%s, %s, 'select, insert, update, delete')",
                (papel, f"public.{tabela}"),
            ).fetchone() or (True,)
            assert ok is False, (papel, tabela)


# --- revisao por km -------------------------------------------------------------------------------------------
def _regra(v: uuid.UUID, intervalo: int, aviso: int | None, por: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute(
            "select public.definir_regra_revisao(%s::uuid, %s::integer, %s::integer, %s::uuid)",
            (v, intervalo, aviso, por),
        )


def _situacao(v: uuid.UUID) -> dict[str, object] | None:
    with h.conectar() as c:
        cur = c.execute("select * from public.revisao_situacao where veiculo_id=%s", (v,))
        row = cur.fetchone()
        if row is None:
            return None
        return dict(zip([d.name for d in cur.description or []], row))


def test_revisao_veiculo_sem_regra_nao_aparece(atores: Atores) -> None:
    assert _situacao(h.novo_veiculo()) is None


def test_revisao_so_admin_ativo_define_regra(atores: Atores) -> None:
    v = h.novo_veiculo()
    for quem in (atores.autorizador, atores.dono):
        with pytest.raises(psycopg.Error) as exc:
            _regra(v, 10000, 1000, quem)
        assert h.sqlstate(exc) == "AG002"
    inativo = h.criar_usuario("admin-inativo", "admin", ativo=False, por=atores.admin)
    with pytest.raises(psycopg.Error) as exc:
        _regra(v, 10000, 1000, inativo)
    assert h.sqlstate(exc) == "AG002"
    _regra(v, 10000, 1000, atores.admin)
    assert _situacao(v) is not None


@pytest.mark.parametrize(
    ("intervalo", "aviso", "constraint"),
    [(0, 0, "ck_revisao_regras_intervalo"), (1000001, 0, "ck_revisao_regras_intervalo"),
     (1000, -1, "ck_revisao_regras_aviso"), (1000, 1001, "ck_revisao_regras_aviso")],
)  # fmt: skip
def test_revisao_regra_valores_invalidos(atores: Atores, intervalo: int, aviso: int, constraint: str) -> None:
    with pytest.raises(psycopg.Error) as exc:
        _regra(h.novo_veiculo(), intervalo, aviso, atores.admin)
    assert h.sqlstate(exc) == CHECK
    assert exc.value.diag.constraint_name == constraint


def test_revisao_aviso_padrao_1000(atores: Atores) -> None:
    v = h.novo_veiculo()
    _regra(v, 10000, None, atores.admin)
    s = _situacao(v)
    assert s is not None and s["aviso_km"] == 1000


def test_revisao_situacao_sem_referencia_em_dia_proxima_vencida(atores: Atores) -> None:
    v = h.novo_veiculo()
    _regra(v, 10000, 1000, atores.admin)
    _reg_ab(v, 4000, atores.autorizador)
    s = _situacao(v)
    assert s is not None and s["situacao"] == "sem_referencia" and s["ultima_revisao_km"] is None
    assert s["km_atual"] == 4000 and s["proxima_revisao_km"] is None

    _reg_man(v, 5000, atores.autorizador, tipo="revisao")
    s = _situacao(v)
    assert s is not None
    assert (s["situacao"], s["ultima_revisao_km"], s["proxima_revisao_km"], s["km_restante"]) == (
        "em_dia", 5000, 15000, 10000)  # fmt: skip

    _reg_ab(v, 13999, atores.autorizador)
    s = _situacao(v)
    assert s is not None and s["situacao"] == "em_dia" and s["km_restante"] == 1001

    _reg_ab(v, 14000, atores.autorizador)  # entra na janela de aviso
    s = _situacao(v)
    assert s is not None and s["situacao"] == "proxima" and s["km_restante"] == 1000

    _reg_ab(v, 15000, atores.autorizador)  # alcancou o limite
    s = _situacao(v)
    assert s is not None and s["situacao"] == "vencida" and s["km_restante"] == 0

    _reg_ab(v, 15400, atores.autorizador)
    s = _situacao(v)
    assert s is not None and s["situacao"] == "vencida" and s["km_restante"] == -400


def test_revisao_nova_revisao_reinicia_e_anular_volta(atores: Atores) -> None:
    v = h.novo_veiculo()
    _regra(v, 10000, 1000, atores.admin)
    _reg_man(v, 1000, atores.autorizador, tipo="revisao")
    _reg_ab(v, 12000, atores.autorizador)
    s = _situacao(v)
    assert s is not None and s["situacao"] == "vencida"
    nova = _reg_man(v, 12000, atores.autorizador, tipo="revisao")
    s = _situacao(v)
    assert s is not None and s["situacao"] == "em_dia" and s["ultima_revisao_km"] == 12000
    _anular("manutencoes", nova, atores.autorizador)
    s = _situacao(v)
    assert s is not None and s["situacao"] == "vencida" and s["ultima_revisao_km"] == 1000


def test_revisao_outros_tipos_nao_contam_como_base(atores: Atores) -> None:
    v = h.novo_veiculo()
    _regra(v, 10000, 1000, atores.admin)
    _reg_man(v, 500, atores.autorizador, tipo="troca_oleo")
    s = _situacao(v)
    assert s is not None and s["situacao"] == "sem_referencia"


def test_revisao_auditoria_da_regra(atores: Atores) -> None:
    v = h.novo_veiculo()
    _regra(v, 10000, 1000, atores.admin)
    _regra(v, 10000, 1000, atores.admin)  # reenvio identico: sem nova linha
    _regra(v, 8000, 500, atores.admin)
    rows = _auditoria("revisao_regras", v)
    assert [r[0] for r in rows] == ["revisao_regra_definida", "revisao_regra_definida"]
    assert rows[0][1:4] == (atores.admin, None, "10000")
    assert rows[1][1:4] == (atores.admin, "10000", "8000")
    assert rows[1][4] == {"veiculo_id": str(v), "intervalo_km": 8000, "aviso_km": 500}


def test_revisao_regra_sem_delete_e_veiculo_imutavel(atores: Atores) -> None:
    v, outro = h.novo_veiculo(), h.novo_veiculo()
    _regra(v, 10000, 1000, atores.admin)
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("delete from public.revisao_regras where veiculo_id=%s", (v,))
    assert h.sqlstate(exc) == "AG003"
    with pytest.raises(psycopg.Error) as exc2:
        with h.conectar() as c:
            c.execute("update public.revisao_regras set veiculo_id=%s where veiculo_id=%s", (outro, v))
    assert h.sqlstate(exc2) == "AG004"


def test_revisao_service_role_nao_escreve_direto(atores: Atores) -> None:
    v = h.novo_veiculo()
    with pytest.raises(psycopg.Error) as exc:
        with h.como("service_role") as c:
            c.execute(
                "insert into public.revisao_regras (veiculo_id, intervalo_km, definido_por) values (%s, 1000, %s)",
                (v, atores.admin),
            )
    assert h.sqlstate(exc) == NEGADO
