"""Testes da migration 5: transicoes de estado, permissoes de transicao e auditoria.

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.

    pytest supabase/tests/test_transicoes.py -m db_local
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]


@dataclass
class Atores:
    dono: uuid.UUID
    outro: uuid.UUID
    autorizador: uuid.UUID
    admin: uuid.UUID


@pytest.fixture
def atores() -> Atores:
    return Atores(
        dono=h.criar_usuario("dono"),
        outro=h.criar_usuario("outro"),
        autorizador=h.criar_usuario("autorizador", "autorizador"),
        admin=h.criar_usuario("admin", "admin"),
    )


def _nova(a: Atores) -> uuid.UUID:
    with h.conectar() as c:
        return h.inserir_requisicao(c, a.dono, h.novo_veiculo())


def _sqlstate(excinfo: pytest.ExceptionInfo[BaseException]) -> str | None:
    return getattr(excinfo.value, "sqlstate", None)


# --- pular status ------------------------------------------------------------
def test_nao_pula_de_solicitada_para_em_uso(atores: Atores) -> None:
    rid = _nova(atores)
    with pytest.raises(psycopg.Error) as exc:
        h.iniciar(rid, atores.autorizador)
    assert _sqlstate(exc) == "AG001"
    assert h.status_de(rid) == "solicitada"


def test_nao_pula_de_solicitada_para_concluida(atores: Atores) -> None:
    rid = _nova(atores)
    with pytest.raises(psycopg.Error) as exc:
        h.concluir(rid, atores.autorizador)
    assert _sqlstate(exc) == "AG001"


def test_nao_pula_de_autorizada_para_concluida(atores: Atores) -> None:
    rid = _nova(atores)
    h.autorizar(rid, atores.autorizador)
    with pytest.raises(psycopg.Error) as exc:
        h.concluir(rid, atores.autorizador)
    assert _sqlstate(exc) == "AG001"
    assert h.status_de(rid) == "autorizada"


@pytest.mark.parametrize("final", ["concluida", "negada", "cancelada"])
def test_estado_terminal_nao_volta(atores: Atores, final: str) -> None:
    rid = _nova(atores)
    if final == "negada":
        h.negar(rid, atores.autorizador)
    elif final == "cancelada":
        h.cancelar(rid, atores.dono)
    else:
        h.autorizar(rid, atores.autorizador)
        h.iniciar(rid, atores.autorizador)
        h.concluir(rid, atores.autorizador)
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                "update public.requisicoes set status='solicitada' where id=%s", (rid,)
            )
    assert _sqlstate(exc) == "AG001"
    assert h.status_de(rid) == final


def test_nova_requisicao_so_nasce_solicitada(atores: Atores) -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                """insert into public.requisicoes
                   (usuario_id, nome_solicitante, unidade, hora_saida, trajeto,
                    veiculo_id, status, autorizado_por, autorizado_em)
                   values (%s,'T','UEA',now(),'A-B',%s,'autorizada',%s,now())""",
                (atores.dono, h.novo_veiculo(), atores.autorizador),
            )
    assert _sqlstate(exc) == "AG001"


# --- quem pode fazer o que ---------------------------------------------------
def test_solicitante_nao_autoriza(atores: Atores) -> None:
    rid = _nova(atores)
    with pytest.raises(psycopg.Error) as exc:
        h.autorizar(rid, atores.dono)
    assert _sqlstate(exc) == "AG002"


def test_admin_ativo_percorre_o_fluxo_inteiro(atores: Atores) -> None:
    rid = _nova(atores)
    h.autorizar(rid, atores.admin)
    h.iniciar(rid, atores.admin)
    h.concluir(rid, atores.admin)
    assert h.status_de(rid) == "concluida"


def test_admin_nega(atores: Atores) -> None:
    rid = _nova(atores)
    h.negar(rid, atores.admin)
    assert h.status_de(rid) == "negada"


def test_admin_inativo_nao_autoriza(atores: Atores) -> None:
    inativo = h.criar_usuario("admin-inativo", "admin", ativo=False)
    rid = _nova(atores)
    with pytest.raises(psycopg.Error) as exc:
        h.autorizar(rid, inativo)
    assert _sqlstate(exc) == "AG002"


@pytest.mark.parametrize("acao", ["negar", "autorizar"])
def test_solicitante_continua_sem_poder_decidir(atores: Atores, acao: str) -> None:
    rid = _nova(atores)
    with pytest.raises(psycopg.Error) as exc:
        getattr(h, acao)(rid, atores.dono)
    assert _sqlstate(exc) == "AG002"


def test_solicitante_nao_registra_saida_nem_chegada(atores: Atores) -> None:
    rid = _nova(atores)
    h.autorizar(rid, atores.autorizador)
    with pytest.raises(psycopg.Error) as exc:
        h.iniciar(rid, atores.dono)
    assert _sqlstate(exc) == "AG002"
    h.iniciar(rid, atores.autorizador)
    with pytest.raises(psycopg.Error) as exc:
        h.concluir(rid, atores.dono)
    assert _sqlstate(exc) == "AG002"


def test_autorizar_exige_responsavel(atores: Atores) -> None:
    rid = _nova(atores)
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("update public.requisicoes set status='autorizada' where id=%s", (rid,))
    assert _sqlstate(exc) == "AG002"


def test_autorizador_inativo_nao_autoriza(atores: Atores) -> None:
    inativo = h.criar_usuario("inativo", "autorizador", ativo=False)
    rid = _nova(atores)
    with pytest.raises(psycopg.Error) as exc:
        h.autorizar(rid, inativo)
    assert _sqlstate(exc) == "AG002"


def test_solicitante_cancela_propria_enquanto_solicitada(atores: Atores) -> None:
    rid = _nova(atores)
    h.cancelar(rid, atores.dono)
    assert h.status_de(rid) == "cancelada"


def test_solicitante_nao_cancela_de_outro(atores: Atores) -> None:
    rid = _nova(atores)
    with pytest.raises(psycopg.Error) as exc:
        h.cancelar(rid, atores.outro)
    assert _sqlstate(exc) == "AG002"


def test_solicitante_nao_cancela_autorizada(atores: Atores) -> None:
    rid = _nova(atores)
    h.autorizar(rid, atores.autorizador)
    with pytest.raises(psycopg.Error) as exc:
        h.cancelar(rid, atores.dono)
    assert _sqlstate(exc) == "AG002"
    assert h.status_de(rid) == "autorizada"


@pytest.mark.parametrize("quem", ["autorizador", "admin"])
def test_autorizador_e_admin_cancelam_autorizada(atores: Atores, quem: str) -> None:
    rid = _nova(atores)
    h.autorizar(rid, atores.autorizador)
    h.cancelar(rid, getattr(atores, quem))
    assert h.status_de(rid) == "cancelada"


def test_nao_cancela_em_uso(atores: Atores) -> None:
    rid = _nova(atores)
    h.autorizar(rid, atores.autorizador)
    h.iniciar(rid, atores.autorizador)
    with pytest.raises(psycopg.Error) as exc:
        h.cancelar(rid, atores.admin)
    assert _sqlstate(exc) == "AG001"


def test_autorizacao_gravada_e_imutavel(atores: Atores) -> None:
    rid = _nova(atores)
    h.autorizar(rid, atores.autorizador)
    outro_aut = h.criar_usuario("aut2", "autorizador")
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                "update public.requisicoes set autorizado_por=%s where id=%s", (outro_aut, rid)
            )
    assert _sqlstate(exc) == "AG004"


# --- auditoria e sem DELETE --------------------------------------------------
def test_fluxo_completo_gera_uma_linha_por_transicao(atores: Atores) -> None:
    rid = _nova(atores)
    h.autorizar(rid, atores.autorizador)
    h.iniciar(rid, atores.autorizador)
    h.concluir(rid, atores.autorizador)
    with h.conectar() as c:
        linhas = c.execute(
            "select acao, de, para, ator_id from public.auditoria "
            "where tabela='requisicoes' and registro_id=%s order by id",
            (rid,),
        ).fetchall()
    assert [(a, d, p) for a, d, p, _ in linhas] == [
        ("criada", None, "solicitada"),
        ("transicao", "solicitada", "autorizada"),
        ("transicao", "autorizada", "em_uso"),
        ("transicao", "em_uso", "concluida"),
    ]
    assert linhas[0][3] == atores.dono
    assert all(r[3] == atores.autorizador for r in linhas[1:])


def test_transicao_rejeitada_nao_gera_auditoria(atores: Atores) -> None:
    rid = _nova(atores)
    with pytest.raises(psycopg.Error):
        h.iniciar(rid, atores.autorizador)
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.auditoria where registro_id=%s", (rid,)
        ).fetchone() or (0,)
    assert n == 1  # so a criacao


@pytest.mark.parametrize(
    "sql",
    [
        "update public.auditoria set acao='x' where tabela='requisicoes'",
        "delete from public.auditoria where tabela='requisicoes'",
        "truncate public.auditoria",
    ],
)
def test_auditoria_append_only(atores: Atores, sql: str) -> None:
    _nova(atores)  # garante ao menos uma linha
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(sql)
    assert _sqlstate(exc) == "AG003"


@pytest.mark.parametrize(
    "sql",
    [
        "delete from public.requisicoes",
        "delete from public.veiculos",
        "delete from public.perfis",
        # `requisicoes` e referenciada por FK (checklists -> requisicoes; checklist_fotos -> checklists).
        # TRUNCATE simples nela daria 0A000 do Postgres ANTES do gatilho; para provar o gatilho AG003
        # truncam-se as tabelas referenciadoras na mesma instrucao (o Postgres aceita) ou usa-se CASCADE.
        "truncate public.requisicoes, public.checklists, public.checklist_fotos",
        "truncate public.requisicoes cascade",
    ],
)
def test_sem_delete_fisico(atores: Atores, sql: str) -> None:
    rid = _nova(atores)
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(sql)
    assert _sqlstate(exc) == "AG003"
    assert h.status_de(rid) == "solicitada"  # nada foi apagado


def test_truncate_simples_em_requisicoes_e_bloqueado(atores: Atores) -> None:
    # Sem CASCADE e sem as referenciadoras, o Postgres recusa antes do gatilho (0A000); AG003 se um dia
    # a FK sair. Em qualquer caso o TRUNCATE nao pode ter efeito.
    rid = _nova(atores)
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("truncate public.requisicoes")
    assert _sqlstate(exc) in {"AG003", "0A000"}
    assert h.status_de(rid) == "solicitada"
