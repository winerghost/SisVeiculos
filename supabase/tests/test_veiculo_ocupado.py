"""Testes da regra "veiculo ocupado" (migration 4: ux_requisicoes_veiculo_aberta).

Rodam SOMENTE contra banco local/dev descartavel, nunca producao. A conexao vem de
TEST_DATABASE_URL (ex.: a URL do `supabase start`); sem ela, tudo e ignorado.
Requer o papel postgres e as migrations 1-5. Nao ha limpeza: DELETE fisico e
proibido (migration 5); use `supabase db reset` para zerar.

    pytest supabase/tests/test_veiculo_ocupado.py -m db_local
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import errors  # noqa: E402

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

INDICE = "ux_requisicoes_veiculo_aberta"


@dataclass
class Ctx:
    usuario_id: uuid.UUID
    autorizador_id: uuid.UUID


@pytest.fixture
def ctx() -> Ctx:
    return Ctx(
        usuario_id=h.criar_usuario("solicitante"),
        autorizador_id=h.criar_usuario("autorizador", "autorizador"),
    )


def _abertas(veiculo_id: uuid.UUID) -> int:
    with h.conectar() as c:
        row = c.execute(
            """select count(*) from public.requisicoes
               where veiculo_id = %s and status in ('solicitada','autorizada','em_uso')""",
            (veiculo_id,),
        ).fetchone()
    assert row is not None
    return int(row[0])


def _fechar(rid: uuid.UUID, destino: str, ctx: Ctx) -> None:
    """Leva a requisicao a um estado que libera o veiculo, seguindo as transicoes."""
    if destino == "cancelada":
        h.cancelar(rid, ctx.usuario_id)
    elif destino == "negada":
        h.negar(rid, ctx.autorizador_id)
    elif destino == "concluida":
        h.autorizar(rid, ctx.autorizador_id)
        h.iniciar(rid, ctx.autorizador_id)
        h.concluir(rid, ctx.autorizador_id)
    else:
        raise ValueError(destino)


def test_concorrencia_duas_insercoes_so_uma_persiste(ctx: Ctx) -> None:
    veiculo = h.novo_veiculo()
    barreira = threading.Barrier(2)
    resultados: list[str] = []
    lock = threading.Lock()

    def tentar() -> None:
        # Cada thread: conexao propria, transacao aberta; a barreira alinha o INSERT
        # e o commit so ocorre depois, forcando a disputa no indice unico.
        with psycopg.connect(h.DSN) as conn:
            barreira.wait(timeout=60)
            try:
                h.inserir_requisicao(conn, ctx.usuario_id, veiculo)
                conn.commit()
                r = "ok"
            except errors.UniqueViolation as e:
                conn.rollback()
                r = f"conflito:{e.diag.constraint_name}"
        with lock:
            resultados.append(r)

    threads = [threading.Thread(target=tentar) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)

    assert sorted(r.split(":")[0] for r in resultados) == ["conflito", "ok"], resultados
    assert f"conflito:{INDICE}" in resultados
    assert _abertas(veiculo) == 1


def test_segunda_requisicao_aberta_no_mesmo_veiculo_falha(ctx: Ctx) -> None:
    veiculo = h.novo_veiculo()
    with h.conectar() as c:
        h.inserir_requisicao(c, ctx.usuario_id, veiculo)
        with pytest.raises(errors.UniqueViolation) as exc:
            h.inserir_requisicao(c, ctx.usuario_id, veiculo)
    assert exc.value.diag.constraint_name == INDICE


@pytest.mark.parametrize("destino", ["negada", "cancelada", "concluida"])
def test_encerrar_libera_o_veiculo(ctx: Ctx, destino: str) -> None:
    veiculo = h.novo_veiculo()
    with h.conectar() as c:
        primeira = h.inserir_requisicao(c, ctx.usuario_id, veiculo)
    _fechar(primeira, destino, ctx)
    with h.conectar() as c:
        h.inserir_requisicao(c, ctx.usuario_id, veiculo)  # nao deve levantar
    assert _abertas(veiculo) == 1


def test_veiculos_diferentes_nao_conflitam(ctx: Ctx) -> None:
    v1, v2 = h.novo_veiculo(), h.novo_veiculo()
    with h.conectar() as c:
        h.inserir_requisicao(c, ctx.usuario_id, v1)
        h.inserir_requisicao(c, ctx.usuario_id, v2)
    assert _abertas(v1) == 1
    assert _abertas(v2) == 1
