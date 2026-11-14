"""Testes da migration 20261121120100 (veiculos.codigo_publico opaco + rotacionar_codigo_veiculo).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). Rodar sem paralelismo.

    pytest supabase/tests/test_veiculos_codigo_publico.py -m db_local -p no:xdist
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(
        not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"
    ),
]

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20261121120100_veiculos_codigo_publico.sql"
).read_text(encoding="utf-8")

FORMATO = re.compile(r"^[a-z0-9]{16,32}$")


@pytest.fixture
def adm() -> uuid.UUID:
    return h.admin_ativo_qualquer() or h.criar_usuario("vcp-admin", "admin")


def _codigo(vid: uuid.UUID) -> str:
    with h.conectar() as c:
        row = c.execute(
            "select codigo_publico from public.veiculos where id = %s", (vid,)
        ).fetchone()
    assert row is not None
    return str(row[0])


def _rotacionar(vid: uuid.UUID | None, ator: uuid.UUID | None) -> str:
    with h.conectar() as c:
        return str(c.execute("select public.rotacionar_codigo_veiculo(%s, %s)", (vid, ator)).fetchone()[0])  # type: ignore[index]


def _auditorias_rotacao(vid: uuid.UUID) -> list[tuple]:
    with h.conectar() as c:
        return c.execute(
            "select ator_id, de, para, detalhes::text from public.auditoria "
            "where tabela = 'veiculos' and registro_id = %s and acao = 'veiculo_alterado' "
            "and detalhes ? 'rotacao' order by id",
            (vid,),
        ).fetchall()


# --- coluna, formato, unicidade, backfill ---------------------------------------------------
def test_coluna_text_not_null_com_default() -> None:
    with h.conectar() as c:
        row = c.execute(
            "select data_type, is_nullable, column_default from information_schema.columns "
            "where table_schema = 'public' and table_name = 'veiculos' and column_name = 'codigo_publico'"
        ).fetchone()
    assert row is not None and row[0] == "text" and row[1] == "NO" and row[2]


def test_novo_veiculo_recebe_codigo_aleatorio_com_formato() -> None:
    a, b = h.novo_veiculo(), h.novo_veiculo()
    ca, cb = _codigo(a), _codigo(b)
    assert FORMATO.match(ca) and FORMATO.match(cb)
    assert ca != cb
    assert str(a).replace("-", "") != ca  # opaco: nao e o id do veiculo


def test_backfill_todos_os_veiculos_tem_codigo_valido_e_unico() -> None:
    with h.conectar() as c:
        total, validos, distintos = c.execute(
            "select count(*), count(*) filter (where codigo_publico ~ '^[a-z0-9]{16,32}$'), "
            "count(distinct codigo_publico) from public.veiculos"
        ).fetchone()  # type: ignore[misc]
    assert total == validos == distintos


def test_formato_invalido_recusado_por_check(adm: uuid.UUID) -> None:
    for ruim in ("curto", "ABCDEFGHIJKLMNOP", "a" * 33, "abcdefghijklmnop-"):
        with pytest.raises(psycopg.errors.CheckViolation), h.conectar() as c:
            c.execute(
                "insert into public.veiculos (descricao, codigo_publico, alterado_por) values ('x', %s, %s)",
                (ruim, adm),
            )


def test_codigo_nulo_recusado(adm: uuid.UUID) -> None:
    with pytest.raises(psycopg.errors.NotNullViolation), h.conectar() as c:
        c.execute(
            "insert into public.veiculos (descricao, codigo_publico, alterado_por) values ('x', null, %s)",
            (adm,),
        )


def test_codigo_unico(adm: uuid.UUID) -> None:
    existente = _codigo(h.novo_veiculo())
    with pytest.raises(psycopg.errors.UniqueViolation), h.conectar() as c:
        c.execute(
            "insert into public.veiculos (descricao, codigo_publico, alterado_por) values ('dup', %s, %s)",
            (existente, adm),
        )


# --- imutavel por UPDATE comum --------------------------------------------------------------
def test_update_comum_do_codigo_e_recusado(adm: uuid.UUID) -> None:
    vid = h.novo_veiculo()
    antes = _codigo(vid)
    with pytest.raises(psycopg.errors.Error) as e, h.conectar() as c:
        c.execute(
            "update public.veiculos set codigo_publico = %s, alterado_por = %s where id = %s",
            ("a" * 20, adm, vid),
        )
    assert h.sqlstate(e) == "AG003"
    assert _codigo(vid) == antes


def test_update_de_outros_campos_nao_mexe_no_codigo(adm: uuid.UUID) -> None:
    vid = h.novo_veiculo()
    antes = _codigo(vid)
    h.alterar_veiculo(vid, adm, "descricao = 'outra descricao'")
    assert _codigo(vid) == antes


def test_marca_de_rotacao_forjada_nao_vale_em_outra_transacao(adm: uuid.UUID) -> None:
    vid = h.novo_veiculo()
    with h.conectar() as c:  # autocommit: a marca local expira no proprio comando
        c.execute(
            "select set_config(%s, '1', true)",
            ("sisveiculos.rotacao_codigo_" + vid.hex,),
        )
    with pytest.raises(psycopg.errors.Error) as e, h.conectar() as c:
        c.execute(
            "update public.veiculos set codigo_publico = %s, alterado_por = %s where id = %s",
            ("b" * 20, adm, vid),
        )
    assert h.sqlstate(e) == "AG003"


# --- rotacao --------------------------------------------------------------------------------
def test_rotacao_gera_novo_codigo_e_audita_sem_o_valor(adm: uuid.UUID) -> None:
    vid = h.novo_veiculo()
    antigo = _codigo(vid)
    novo = _rotacionar(vid, adm)
    assert novo != antigo and FORMATO.match(novo)
    assert _codigo(vid) == novo

    linhas = _auditorias_rotacao(vid)
    assert len(linhas) == 1
    ator, de, para, detalhes = linhas[0]
    assert ator == adm and de is None and para is None
    assert "codigo_publico" in detalhes
    assert antigo not in detalhes and novo not in detalhes
    with h.conectar() as c:  # nem em nenhuma coluna de texto da linha de auditoria
        achou = c.execute(
            "select count(*) from public.auditoria a where a.registro_id = %s and (a::text like %s or a::text like %s)",
            (vid, f"%{antigo}%", f"%{novo}%"),
        ).fetchone()[
            0
        ]  # type: ignore[index]
    assert achou == 0


def test_rotacoes_sucessivas_geram_codigos_distintos(adm: uuid.UUID) -> None:
    vid = h.novo_veiculo()
    c1, c2 = _rotacionar(vid, adm), _rotacionar(vid, adm)
    assert c1 != c2 and _codigo(vid) == c2
    assert len(_auditorias_rotacao(vid)) == 2


def test_rotacao_exige_admin_ativo() -> None:
    vid = h.novo_veiculo()
    antes = _codigo(vid)
    nao_admin = h.criar_usuario("vcp-solic", "solicitante")
    for ator in (nao_admin, uuid.uuid4(), None):
        with pytest.raises(psycopg.errors.Error) as e:
            _rotacionar(vid, ator)
        assert h.sqlstate(e) == "AG002"
    assert _codigo(vid) == antes
    assert _auditorias_rotacao(vid) == []


def test_rotacao_veiculo_inexistente_e_argumento_nulo(adm: uuid.UUID) -> None:
    with pytest.raises(psycopg.errors.Error) as e:
        _rotacionar(uuid.uuid4(), adm)
    assert h.sqlstate(e) == "P0002"
    with pytest.raises(psycopg.errors.NotNullViolation):
        _rotacionar(None, adm)


def test_rotacao_nao_deixa_a_marca_valendo_depois(adm: uuid.UUID) -> None:
    vid = h.novo_veiculo()
    with h.conectar() as c, c.transaction():
        c.execute("select public.rotacionar_codigo_veiculo(%s, %s)", (vid, adm))
        with pytest.raises(psycopg.errors.Error) as e, c.transaction():
            c.execute(
                "update public.veiculos set codigo_publico = %s where id = %s",
                ("c" * 20, vid),
            )
        assert h.sqlstate(e) == "AG003"  # a marca foi consumida pelo update da RPC


# --- acesso por papel -----------------------------------------------------------------------
@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_papeis_da_api_nao_executam_nem_leem(papel: str, adm: uuid.UUID) -> None:
    vid = h.novo_veiculo()
    with pytest.raises(psycopg.errors.InsufficientPrivilege), h.como(papel, adm) as c:
        c.execute("select public.rotacionar_codigo_veiculo(%s, %s)", (vid, adm))
    with pytest.raises(psycopg.errors.InsufficientPrivilege), h.como(papel, adm) as c:
        c.execute("select codigo_publico from public.veiculos")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), h.como(papel, adm) as c:
        c.execute(
            "update public.veiculos set codigo_publico = %s where id = %s",
            ("d" * 20, vid),
        )


def test_service_role_rotaciona_pela_rpc_mas_nao_por_update_direto(
    adm: uuid.UUID,
) -> None:
    vid = h.novo_veiculo()
    with h.como("service_role") as c:
        novo = c.execute("select public.rotacionar_codigo_veiculo(%s, %s)", (vid, adm)).fetchone()[0]  # type: ignore[index]
        assert FORMATO.match(novo)
    with pytest.raises(psycopg.errors.Error), h.como("service_role") as c:
        c.execute(
            "update public.veiculos set codigo_publico = %s where id = %s",
            ("e" * 20, vid),
        )


def test_funcoes_internas_sem_execute_para_papeis_da_api() -> None:
    with h.conectar() as c:
        for papel in h.PAPEIS_API:
            assert not c.execute(
                "select has_function_privilege(%s, 'public.veiculos_codigo_publico_guarda()', 'execute')",
                (papel,),
            ).fetchone()[
                0
            ]  # type: ignore[index]
        assert c.execute(
            "select prosecdef and array_to_string(proconfig, ',') like '%%search_path=%%' "
            "from pg_proc where oid = 'public.rotacionar_codigo_veiculo(uuid, uuid)'::regprocedure"
        ).fetchone()[
            0
        ]  # type: ignore[index]


def test_rls_continua_ligado_e_sem_delete_fisico(adm: uuid.UUID) -> None:
    with h.conectar() as c:
        assert c.execute("select relrowsecurity from pg_class where oid = 'public.veiculos'::regclass").fetchone()[0]  # type: ignore[index]
    vid = h.novo_veiculo()
    with pytest.raises(psycopg.errors.Error) as e, h.conectar() as c:
        c.execute("delete from public.veiculos where id = %s", (vid,))
    assert h.sqlstate(e) == "AG003"


# --- idempotencia ---------------------------------------------------------------------------
def test_migration_reaplicavel_sem_mudar_codigos() -> None:
    with h.conectar() as c:
        antes = c.execute(
            "select id, codigo_publico from public.veiculos order by id"
        ).fetchall()
    with h.reaplicando_migration() as conn:
        conn.execute(MIGRATION)
        depois = conn.execute(
            "select id, codigo_publico from public.veiculos order by id"
        ).fetchall()
    assert antes == depois
