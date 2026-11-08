"""Testes da migration 20261114120000 (retencao de 12 meses de localizacoes_viagem).

Parte estatica (sempre roda): confere o texto da migration.
Parte de banco: somente banco local/dev DESCARTAVEL (ver dbhelpers.py). Todo o cenario roda em UMA
transacao com rollback (nada de coordenada persiste); so a criacao dos perfis/veiculo de apoio persiste.
Para criar linhas antigas, a transacao desliga o gatilho de VALIDACAO (que carimba registrado_em com
o relogio) e insere registrado_em explicito; o gatilho de imutabilidade permanece ativo.

    pytest supabase/tests/test_localizacao_retencao.py -m db_local -p no:xdist
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261114120000_localizacao_retencao_12_meses.sql"
).read_text(encoding="utf-8")

NEGADO = "42501"
AG003 = "AG003"
LAT, LON = -15.793889, -47.882778

db = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]


# --- estatico -------------------------------------------------------------------------------------------
def test_migration_estatica() -> None:
    assert "create or replace function public.expurgar_localizacoes_vencidas()" in MIGRATION
    assert "security definer" in MIGRATION
    assert "set search_path = ''" in MIGRATION
    assert "grant execute on function public.expurgar_localizacoes_vencidas() to service_role" in MIGRATION
    assert "from public, anon, authenticated;" in MIGRATION
    assert "interval '12 months'" in MIGRATION
    assert "registrado_em < v_corte" in MIGRATION
    assert "'localizacao_expurgada'" in MIGRATION
    assert "sisveiculos.expurgo_localizacoes" in MIGRATION
    assert "enable always trigger trg_localizacoes_viagem_imutavel" in MIGRATION
    # Agendamento fora do escopo: nada de pg_cron.
    assert "cron.schedule" not in MIGRATION
    # A linha de auditoria nao pode citar coordenadas.
    trecho = MIGRATION.split("insert into public.auditoria", 1)[1].split("return v_n", 1)[0]
    for proibido in ("latitude", "longitude", "precisao"):
        assert proibido not in trecho


def test_migration_nao_cria_extensao_nem_agenda() -> None:
    assert "create extension" not in MIGRATION.lower()
    assert "cron." not in MIGRATION


# --- banco ----------------------------------------------------------------------------------------------
class Cena:
    """Dados de apoio e a transacao do teste (rollback ao sair)."""

    def __init__(self, conn: "psycopg.Connection") -> None:
        self.conn = conn
        self.motorista = h.criar_usuario("motorista-ret", "motorista")
        dono = h.criar_usuario("dono-ret")
        self.veiculo = h.novo_veiculo()
        self.req = h.inserir_requisicao(conn, dono, self.veiculo)
        conn.execute("alter table public.localizacoes_viagem disable trigger trg_localizacoes_viagem_validar")

    def inserir(self, momento: str, idade_sql: str, req: uuid.UUID | None = None) -> None:
        self.conn.execute(
            "insert into public.localizacoes_viagem "
            "(requisicao_id, momento, latitude, longitude, registrado_por, registrado_em) "
            f"values (%s, %s, %s, %s, %s, now() - interval '{idade_sql}')",
            (req or self.req, momento, LAT, LON, self.motorista),
        )

    def total(self) -> int:
        return self.conn.execute("select count(*) from public.localizacoes_viagem where requisicao_id = %s", (self.req,)).fetchone()[0]  # type: ignore[index]


@contextmanager
def cena() -> Iterator[Cena]:
    assert h.DSN
    with psycopg.connect(h.DSN) as conn:
        try:
            yield Cena(conn)
        finally:
            conn.rollback()


def _contagens(conn: "psycopg.Connection") -> tuple[int, int, int]:
    return conn.execute(
        "select (select count(*) from public.requisicoes), (select count(*) from public.perfis), "
        "(select count(*) from public.veiculos)"
    ).fetchone()  # type: ignore[return-value]


@pytest.mark.db_local
@pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado")
def test_expurgo_apaga_so_vencidas_e_preserva_recentes() -> None:
    with cena() as c:
        c.inserir("saida", "12 months 1 day")  # vencida
        c.inserir("chegada", "11 months")  # recente
        outras_antes = _contagens(c.conn)
        c.conn.execute("set local role service_role")
        apagadas = c.conn.execute("select public.expurgar_localizacoes_vencidas()").fetchone()[0]  # type: ignore[index]
        c.conn.execute("reset role")
        assert apagadas >= 1
        restantes = c.conn.execute(
            "select momento from public.localizacoes_viagem where requisicao_id = %s", (c.req,)
        ).fetchall()
        assert restantes == [("chegada",)]
        assert c.conn.execute(
            "select count(*) from public.localizacoes_viagem where registrado_em < now() - interval '12 months'"
        ).fetchone()[0] == 0  # type: ignore[index]
        # Outras tabelas intactas.
        assert _contagens(c.conn) == outras_antes


@pytest.mark.db_local
@pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado")
def test_expurgo_audita_contagem_e_corte_sem_coordenadas() -> None:
    with cena() as c:
        c.inserir("saida", "13 months")
        n = c.conn.execute("select public.expurgar_localizacoes_vencidas()").fetchone()[0]  # type: ignore[index]
        acao, tabela, registro, ator, detalhes = c.conn.execute(
            "select acao, tabela, registro_id, ator_id, detalhes from public.auditoria "
            "where acao = 'localizacao_expurgada' order by id desc limit 1"
        ).fetchone()  # type: ignore[misc]
        assert (acao, tabela, registro, ator) == ("localizacao_expurgada", "requisicoes", None, None)
        assert set(detalhes) == {"recurso", "apagadas", "corte"}
        assert detalhes["apagadas"] == n
        texto = json.dumps(detalhes).lower()
        for proibido in ("latitude", "longitude", "precisao", str(LAT), str(LON), str(c.req)):
            assert proibido not in texto


@pytest.mark.db_local
@pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado")
def test_execucao_sem_vencidas_tambem_audita_zero() -> None:
    with cena() as c:
        c.inserir("saida", "1 month")
        antes = c.conn.execute("select count(*) from public.auditoria where acao = 'localizacao_expurgada'").fetchone()[0]  # type: ignore[index]
        c.conn.execute("select public.expurgar_localizacoes_vencidas()")
        depois = c.conn.execute("select count(*) from public.auditoria where acao = 'localizacao_expurgada'").fetchone()[0]  # type: ignore[index]
        assert depois == antes + 1
        assert c.total() == 1


@pytest.mark.db_local
@pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado")
@pytest.mark.parametrize("idade", ["12 months 1 day", "1 day"])
def test_delete_direto_continua_bloqueado(idade: str) -> None:
    with cena() as c:
        c.inserir("saida", idade)
        with pytest.raises(psycopg.Error) as ei:
            c.conn.execute("delete from public.localizacoes_viagem where requisicao_id = %s", (c.req,))
        assert h.sqlstate(ei) == AG003


@pytest.mark.db_local
@pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado")
def test_update_e_truncate_continuam_bloqueados() -> None:
    with cena() as c:
        c.inserir("saida", "13 months")
        with pytest.raises(psycopg.Error) as ei:
            c.conn.execute("update public.localizacoes_viagem set latitude = 0 where requisicao_id = %s", (c.req,))
        assert h.sqlstate(ei) == AG003
    with cena() as c:
        with pytest.raises(psycopg.Error) as ei:
            c.conn.execute("truncate public.localizacoes_viagem")
        assert h.sqlstate(ei) == AG003


@pytest.mark.db_local
@pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado")
def test_marcador_nao_libera_linha_recente() -> None:
    """Piso de 12 meses no proprio gatilho: o marcador sozinho nao basta."""
    with cena() as c:
        c.inserir("saida", "11 months")
        c.conn.execute("select set_config('sisveiculos.expurgo_localizacoes', '1', true)")
        with pytest.raises(psycopg.Error) as ei:
            c.conn.execute("delete from public.localizacoes_viagem where requisicao_id = %s", (c.req,))
        assert h.sqlstate(ei) == AG003


@pytest.mark.db_local
@pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado")
def test_marcador_desligado_apos_expurgo() -> None:
    with cena() as c:
        c.inserir("saida", "13 months")
        c.conn.execute("select public.expurgar_localizacoes_vencidas()")
        assert c.conn.execute("select current_setting('sisveiculos.expurgo_localizacoes', true)").fetchone()[0] in ("", None)  # type: ignore[index]


@pytest.mark.db_local
@pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado")
@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_grants_api_nao_executa(papel: str) -> None:
    with h.como(papel) as conn:
        with pytest.raises(psycopg.Error) as ei:
            conn.execute("select public.expurgar_localizacoes_vencidas()")
        assert h.sqlstate(ei) == NEGADO


@pytest.mark.db_local
@pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado")
def test_grants_service_role_executa_mas_nao_deleta_direto() -> None:
    with h.como("service_role") as conn:
        assert conn.execute("select public.expurgar_localizacoes_vencidas()").fetchone() is not None
    with h.como("service_role") as conn:
        with pytest.raises(psycopg.Error) as ei:
            conn.execute("delete from public.localizacoes_viagem")
        assert h.sqlstate(ei) == NEGADO
    with h.conectar() as conn:
        # Nenhum ACL para PUBLIC (grantee 0) na funcao.
        row = conn.execute(
            "select count(*) from pg_proc p, aclexplode(p.proacl) a "
            "where p.oid = 'public.expurgar_localizacoes_vencidas()'::regprocedure and a.grantee = 0"
        ).fetchone()
        assert row == (0,)
