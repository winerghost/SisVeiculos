"""Testes da migration 20261204120200 (registrar_auditoria: lista branca em 'acao_administrativa'
e 'acesso_negado', achado B3).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). As chamadas rodam em transacao com rollback
(helper `como`); so a criacao dos perfis de apoio persiste.

    pytest supabase/tests/test_registrar_auditoria_whitelist_admin.py -m db_local -p no:xdist
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20261204120200_registrar_auditoria_whitelist_admin.sql"
).read_text(encoding="utf-8")

ASSINATURA = "public.registrar_auditoria(text, uuid, text, uuid, text, text, jsonb)"
CHAMADA = "select public.registrar_auditoria(%s, %s, %s, %s, null, null, %s::jsonb)"


def _registrar(
    ator: uuid.UUID | None,
    detalhes: object,
    *,
    acao: str = "acao_administrativa",
    tabela: str = "perfis",
    registro_id: uuid.UUID | None = None,
) -> object:
    with h.como("service_role") as c:
        return c.execute(CHAMADA, (tabela, registro_id, acao, ator, json.dumps(detalhes))).fetchone()


def _recusa(ator: uuid.UUID, detalhes: object, acao: str) -> None:
    with pytest.raises(psycopg.errors.Error) as exc:
        _registrar(ator, detalhes, acao=acao)
    assert h.sqlstate(exc) == "AG003"


@pytest.fixture(scope="module")
def admin() -> uuid.UUID:
    return h.criar_usuario("adm-wl-b3", role="admin")


def test_migration_estatica() -> None:
    corpo = MIGRATION.split("create or replace function", 1)[1].split("$$;", 1)[0]
    assert "array['recurso', 'campos']" in corpo
    assert "else array['recurso'] end" in corpo
    assert "array['requisicao_id', 'itens']" in corpo  # localizacao_lida mantida
    assert "'destinatario_id'" in corpo  # relatorio mantido
    assert "security definer" in MIGRATION
    assert "set search_path = ''" in MIGRATION
    assert "to service_role" in MIGRATION


# --- chamadas reais do backend (levantadas por grep em backend/app) -------------------------
def test_chamada_acessos_do_backend_aceita(admin: uuid.UUID) -> None:
    # services/acessos.py: DETALHES_AUDITORIA = {"recurso": "acessos"}, tabela perfis, registro nulo
    assert _registrar(admin, {"recurso": "acessos"}) is not None


def test_chamada_admin_usuarios_nome_aceita(admin: uuid.UUID) -> None:
    # services/admin_usuarios.py: detalhes={"campos": ["nome"]}, tabela perfis, registro = alvo
    assert _registrar(admin, {"campos": ["nome"]}, registro_id=admin) is not None


@pytest.mark.parametrize("acao", ["acao_administrativa", "acesso_negado"])
def test_detalhes_vazio_e_nulo_aceitos(admin: uuid.UUID, acao: str) -> None:
    assert _registrar(admin, {}, acao=acao) is not None
    with h.como("service_role") as c:  # p_detalhes omitido (default '{}')
        assert c.execute(
            "select public.registrar_auditoria('perfis', null, %s, %s)", (acao, admin)
        ).fetchone() is not None
    with h.como("service_role") as c:  # SQL NULL (PostgREST com "p_detalhes": null)
        assert c.execute(
            "select public.registrar_auditoria('perfis', null, %s, %s, null, null, null)", (acao, admin)
        ).fetchone() is not None


@pytest.mark.parametrize("acao", ["acao_administrativa", "acesso_negado"])
def test_detalhes_json_null_recusado(admin: uuid.UUID, acao: str) -> None:
    _recusa(admin, None, acao)  # json.dumps(None) -> 'null'::jsonb (nao e objeto)


def test_acesso_negado_com_recurso_aceito(admin: uuid.UUID) -> None:
    assert _registrar(admin, {"recurso": "acessos"}, acao="acesso_negado") is not None


# --- recusas --------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "detalhes",
    [
        {"k": 1},
        {"email": "x@teste.invalid"},
        {"nome": "Fulano"},
        {"telefone": "61999999999"},
        {"filtros": {"usuario_id": "x"}},
        {"Recurso": "acessos"},
        {"recurso": "acessos", "extra": 1},
        {"campos": ["nome"], "valor": "Fulano"},
        [1],
        "texto",
    ],
)
def test_acao_administrativa_chave_fora_da_lista(admin: uuid.UUID, detalhes: object) -> None:
    _recusa(admin, detalhes, "acao_administrativa")


@pytest.mark.parametrize(
    "detalhes",
    [
        {"campos": ["nome"]},  # 'campos' so vale em acao_administrativa
        {"motivo": "rota"},
        {"email": "x@teste.invalid"},
        [1],
    ],
)
def test_acesso_negado_chave_fora_da_lista(admin: uuid.UUID, detalhes: object) -> None:
    _recusa(admin, detalhes, "acesso_negado")


@pytest.mark.parametrize(
    "recurso",
    ["x@teste.invalid", "Fulano de Tal", "Acessos", "1acessos", "", "a" * 65, 1, ["acessos"], None,
     {"a": 1}],
)
@pytest.mark.parametrize("acao", ["acao_administrativa", "acesso_negado"])
def test_valor_de_recurso_so_identificador(admin: uuid.UUID, acao: str, recurso: object) -> None:
    _recusa(admin, {"recurso": recurso}, acao)


@pytest.mark.parametrize(
    "campos",
    [
        [],
        "nome",
        ["Fulano"],
        ["x@teste.invalid"],
        ["nome", 1],
        [["nome"]],
        [{"nome": "Fulano"}],
        ["a" * 65],
        ["c"] * 33,
        None,
    ],
)
def test_valor_de_campos_so_lista_de_identificadores(admin: uuid.UUID, campos: object) -> None:
    _recusa(admin, {"campos": campos}, "acao_administrativa")


def test_campos_no_limite_aceito(admin: uuid.UUID) -> None:
    assert _registrar(admin, {"campos": [f"c{i}" for i in range(32)]}) is not None


@pytest.mark.parametrize("acao", ["acao_administrativa", "acesso_negado"])
def test_recurso_no_limite_de_64_aceito(admin: uuid.UUID, acao: str) -> None:
    assert _registrar(admin, {"recurso": "a" * 64}, acao=acao) is not None


def test_item_de_campos_no_limite_de_64_aceito(admin: uuid.UUID) -> None:
    assert _registrar(admin, {"campos": ["a" * 64]}) is not None


def test_recusa_nao_grava_linha(admin: uuid.UUID) -> None:
    """Em autocommit (sem o rollback do helper `como`): a recusa nao deixa linha."""
    marcador = f"b3_{uuid.uuid4().hex[:12]}"
    with h.conectar() as c:
        with pytest.raises(psycopg.errors.Error) as exc:
            c.execute(CHAMADA, ("perfis", None, "acao_administrativa", admin,
                                json.dumps({"recurso": marcador, "email": "x@teste.invalid"})))
        assert h.sqlstate(exc) == "AG003"
        (n,) = c.execute(
            "select count(*) from public.auditoria where detalhes ->> 'recurso' = %s", (marcador,)
        ).fetchone() or (None,)
    assert n == 0


# --- regras anteriores mantidas -------------------------------------------------------------
def test_outras_acoes_inalteradas(admin: uuid.UUID) -> None:
    assert _registrar(admin, {"tipo": "movimentacao"}, acao="relatorio_gerado",
                      tabela="relatorios") is not None
    assert _registrar(admin, {"requisicao_id": "x", "itens": 1}, acao="localizacao_lida",
                      tabela="requisicoes", registro_id=uuid.uuid4()) is not None
    _recusa(admin, {}, "transicao")
    with pytest.raises(psycopg.errors.Error) as exc:
        _registrar(admin, {"lat": 1}, acao="localizacao_lida", tabela="requisicoes",
                   registro_id=uuid.uuid4())
    assert h.sqlstate(exc) == "AG003"


def test_ator_inexistente_segue_ag002() -> None:
    with pytest.raises(psycopg.errors.Error) as exc:
        _registrar(uuid.uuid4(), {"recurso": "acessos"})
    assert h.sqlstate(exc) == "AG002"


def test_grants_e_definer_inalterados() -> None:
    with h.conectar() as c:
        (sec, cfg) = c.execute(
            "select prosecdef, proconfig from pg_proc where oid = to_regprocedure(%s)", (ASSINATURA,)
        ).fetchone() or (None, None)
        assert sec is True
        assert cfg is not None and 'search_path=""' in cfg
        for papel, esperado in (("anon", False), ("authenticated", False), ("service_role", True)):
            (pode,) = c.execute(
                "select has_function_privilege(%s, to_regprocedure(%s), 'execute')", (papel, ASSINATURA)
            ).fetchone() or (None,)
            assert pode is esperado, papel
