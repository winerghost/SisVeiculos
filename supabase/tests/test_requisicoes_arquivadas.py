"""Testes da migration 20261119120300: arquivamento pessoal (tabela requisicoes_arquivadas).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). Nada e apagado, exceto o vinculo de arquivamento
(desarquivar), e cada teste usa usuarios/requisicoes proprios.

    pytest supabase/tests/test_requisicoes_arquivadas.py -m db_local
"""

from __future__ import annotations

import threading
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

NEGADO = "42501"
TAB = "public.requisicoes_arquivadas"


class Cena:
    def __init__(self) -> None:
        self.admin = h.criar_usuario("arq-admin", "admin")
        self.aut = h.criar_usuario("arq-aut", "autorizador")
        self.sol = h.criar_usuario("arq-sol")
        self.outro = h.criar_usuario("arq-outro")

    def solicitada(self) -> uuid.UUID:
        with h.conectar() as c:
            return h.inserir_requisicao(c, self.sol, h.novo_veiculo(self.admin))

    def em_estado(self, estado: str) -> uuid.UUID:
        rid = self.solicitada()
        if estado == "solicitada":
            return rid
        if estado == "negada":
            h.negar(rid, self.aut)
        elif estado == "cancelada":
            h.cancelar(rid, self.sol)
        else:
            h.autorizar(rid, self.aut)
            if estado in ("em_uso", "concluida"):
                h.iniciar(rid, self.aut)
            if estado == "concluida":
                h.concluir(rid, self.aut)
        return rid


@pytest.fixture
def cena() -> Cena:
    return Cena()


def _arquivar(uid: uuid.UUID, rid: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute("insert into public.requisicoes_arquivadas (usuario_id, requisicao_id) values (%s, %s)", (uid, rid))


def _ids(uid: uuid.UUID) -> set[uuid.UUID]:
    with h.conectar() as c:
        rows = c.execute("select requisicao_id from public.requisicoes_arquivadas where usuario_id = %s", (uid,)).fetchall()
    return {r[0] for r in rows}


@pytest.mark.parametrize("estado", ["concluida", "negada", "cancelada"])
def test_arquiva_requisicao_encerrada(cena: Cena, estado: str) -> None:
    rid = cena.em_estado(estado)
    _arquivar(cena.sol, rid)
    assert _ids(cena.sol) == {rid}


@pytest.mark.parametrize("estado", ["solicitada", "autorizada", "em_uso"])
def test_nao_arquiva_requisicao_aberta_ag041(cena: Cena, estado: str) -> None:
    rid = cena.em_estado(estado)
    with pytest.raises(psycopg.Error) as exc:
        _arquivar(cena.sol, rid)
    assert h.sqlstate(exc) == "AG041"
    assert _ids(cena.sol) == set()


def test_arquivar_e_pessoal_nao_afeta_outros_usuarios(cena: Cena) -> None:
    rid = cena.em_estado("concluida")
    _arquivar(cena.sol, rid)
    _arquivar(cena.aut, rid)
    assert _ids(cena.sol) == {rid} and _ids(cena.aut) == {rid} and _ids(cena.outro) == set()
    with h.conectar() as c:
        c.execute("delete from public.requisicoes_arquivadas where usuario_id = %s", (cena.sol,))  # desarquivar
    assert _ids(cena.sol) == set() and _ids(cena.aut) == {rid}
    assert h.status_de(rid) == "concluida"  # a requisicao nao foi tocada


def test_pk_composta_impede_duplicidade(cena: Cena) -> None:
    rid = cena.em_estado("negada")
    _arquivar(cena.sol, rid)
    with pytest.raises(psycopg.Error) as exc:
        _arquivar(cena.sol, rid)
    assert h.sqlstate(exc) == "23505"


def test_concorrencia_duas_chamadas_so_uma_persiste(cena: Cena) -> None:
    rid = cena.em_estado("concluida")
    resultados: list[str] = []
    barreira = threading.Barrier(2)

    def chamar() -> None:
        try:
            with h.conectar() as c:
                barreira.wait(timeout=10)
                c.execute(
                    "insert into public.requisicoes_arquivadas (usuario_id, requisicao_id) values (%s, %s)",
                    (cena.sol, rid),
                )
            resultados.append("ok")
        except psycopg.Error as e:
            resultados.append(str(e.sqlstate))

    ts = [threading.Thread(target=chamar) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(resultados) == ["23505", "ok"]
    assert _ids(cena.sol) == {rid}


def test_arquivada_em_e_carimbada_pelo_servidor(cena: Cena) -> None:
    rid = cena.em_estado("cancelada")
    with h.conectar() as c:
        c.execute(
            "insert into public.requisicoes_arquivadas (usuario_id, requisicao_id, arquivada_em) "
            "values (%s, %s, '2000-01-01T00:00:00Z')",
            (cena.sol, rid),
        )
        (ok,) = c.execute(
            "select arquivada_em > now() - interval '1 minute' from public.requisicoes_arquivadas "
            "where usuario_id = %s and requisicao_id = %s",
            (cena.sol, rid),
        ).fetchone() or (False,)
    assert ok is True


def test_fks_restrict_impedem_apagar_requisicao_ou_perfil(cena: Cena) -> None:
    rid = cena.em_estado("concluida")
    _arquivar(cena.sol, rid)
    with h.conectar() as c:
        rows = c.execute(
            "select confdeltype from pg_constraint where conrelid = %s::regclass and contype = 'f'", (TAB,)
        ).fetchall()
    assert [r[0] for r in rows] == ["r", "r"]
    with pytest.raises(psycopg.Error), h.conectar() as c:  # a FK restringe o apagamento
        c.execute("delete from public.requisicoes where id = %s", (rid,))


def test_trigger_de_encerrada_continua_congelando_a_requisicao(cena: Cena) -> None:
    rid = cena.em_estado("concluida")
    _arquivar(cena.sol, rid)
    with pytest.raises(psycopg.Error) as exc, h.conectar() as c:
        c.execute("update public.requisicoes set trajeto = 'X-Y' where id = %s", (rid,))
    assert h.sqlstate(exc) == "AG001"


# --- RLS / grants ------------------------------------------------------------------------------------------
def test_rls_ligado_e_sem_politica_permissiva() -> None:
    with h.conectar() as c:
        (rls,) = c.execute("select relrowsecurity from pg_class where oid = %s::regclass", (TAB,)).fetchone() or (False,)
        pols = c.execute(
            "select roles::text[], qual, with_check from pg_policies "
            "where schemaname='public' and tablename='requisicoes_arquivadas'"
        ).fetchall()
    assert rls is True
    assert all(set(roles) <= {"anon", "authenticated"} and qual == "false" for roles, qual, _ in pols)


@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_api_publica_nao_acessa(cena: Cena, papel: str) -> None:
    uid = cena.sol if papel == "authenticated" else None
    with pytest.raises(psycopg.Error) as exc, h.como(papel, uid) as c:
        c.execute("select * from public.requisicoes_arquivadas")
    assert h.sqlstate(exc) == NEGADO
    with pytest.raises(psycopg.Error) as exc2, h.como(papel, uid) as c:
        c.execute("insert into public.requisicoes_arquivadas (usuario_id, requisicao_id) values (%s, %s)",
                  (cena.sol, uuid.uuid4()))
    assert h.sqlstate(exc2) == NEGADO


def test_service_role_so_tem_select_insert_delete() -> None:
    with h.conectar() as c:
        privs = {
            p: c.execute("select has_table_privilege('service_role', %s, %s)", (TAB, p)).fetchone()[0]  # type: ignore[index]
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
        }
    assert privs == {
        "SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": True,
        "TRUNCATE": False, "REFERENCES": False, "TRIGGER": False,
    }  # fmt: skip


def test_funcao_do_gatilho_nao_executavel_pela_api() -> None:
    with h.conectar() as c:
        for papel in ("anon", "authenticated", "service_role"):
            (ok,) = c.execute(
                "select has_function_privilege(%s, 'public.requisicoes_arquivadas_validar()'::regprocedure, 'execute')",
                (papel,),
            ).fetchone() or (True,)
            assert ok is False, papel


def test_indice_de_listagem_e_coluna_timestamptz() -> None:
    with h.conectar() as c:
        idx = c.execute(
            "select indexdef from pg_indexes where schemaname='public' and tablename='requisicoes_arquivadas' "
            "and indexname='ix_requisicoes_arquivadas_usuario_data'"
        ).fetchone()
        tipo = c.execute(
            "select data_type from information_schema.columns where table_schema='public' "
            "and table_name='requisicoes_arquivadas' and column_name='arquivada_em'"
        ).fetchone()
    assert idx is not None and "(usuario_id, arquivada_em DESC)" in idx[0]
    assert tipo == ("timestamp with time zone",)


def test_arquivar_nao_gera_auditoria(cena: Cena) -> None:
    rid = cena.em_estado("negada")
    antes = h.auditoria_de("requisicoes", rid)
    _arquivar(cena.sol, rid)
    assert h.auditoria_de("requisicoes", rid) == antes
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.auditoria where tabela = 'requisicoes_arquivadas'"
        ).fetchone() or (0,)
    assert n == 0
