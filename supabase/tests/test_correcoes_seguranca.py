"""Testes da migration 10: correcoes da revisao de seguranca.

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.
Cobre carimbos do servidor, congelamento de campos, veiculo ativo, grants do
service_role, triggers ENABLE ALWAYS, auditoria (CHECKs, perfis, veiculos,
cancelada_por) e concorrencia com sobreposicao forcada.

    pytest supabase/tests/test_correcoes_seguranca.py -m db_local
"""

from __future__ import annotations

import json
import threading
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
class A:
    dono: uuid.UUID
    autorizador: uuid.UUID
    admin: uuid.UUID


@pytest.fixture
def a() -> A:
    return A(
        dono=h.criar_usuario("dono"),
        autorizador=h.criar_usuario("aut", "autorizador"),
        admin=h.criar_usuario("adm", "admin"),
    )


def _nova(a: A) -> uuid.UUID:
    with h.conectar() as c:
        return h.inserir_requisicao(c, a.dono, h.novo_veiculo())


def _exec(sql: str, params: tuple = ()) -> None:
    with h.conectar() as c:
        c.execute(sql, params)


def _falha(sql: str, params: tuple, esperado: str) -> None:
    with pytest.raises(psycopg.Error) as exc:
        _exec(sql, params)
    assert h.sqlstate(exc) == esperado


# --- carimbos do servidor ----------------------------------------------------------
def test_carimbos_sao_do_servidor_e_ordenados(a: A) -> None:
    rid = _nova(a)
    h.autorizar(rid, a.autorizador)
    h.iniciar(rid, a.autorizador)
    h.concluir(rid, a.autorizador)
    with h.conectar() as c:
        row = c.execute(
            "select criado_em, autorizado_em, saida_real_em, chegada_real_em, "
            "clock_timestamp() - chegada_real_em < interval '1 minute' "
            "from public.requisicoes where id=%s",
            (rid,),
        ).fetchone()
    assert row is not None
    criado, autorizado, saida, chegada, recente = row
    assert criado <= autorizado <= saida < chegada
    assert recente


@pytest.mark.parametrize(
    "coluna,status,extra",
    [
        ("autorizado_em", "autorizada", "autorizado_por=%(p)s"),
        ("cancelada_em", "cancelada", "cancelada_por=%(p)s"),
    ],
)
def test_carimbo_enviado_pelo_chamador_e_recusado(a: A, coluna: str, status: str, extra: str) -> None:
    rid = _nova(a)
    _falha(
        f"update public.requisicoes set status='{status}', {extra.replace('%(p)s', '%s')}, "
        f"{coluna}='2000-01-01' where id=%s",
        (a.autorizador, rid),
        "AG004",
    )
    assert h.status_de(rid) == "solicitada"


def test_saida_e_chegada_enviadas_pelo_chamador_sao_recusadas(a: A) -> None:
    rid = _nova(a)
    h.autorizar(rid, a.autorizador)
    _falha(
        "update public.requisicoes set status='em_uso', saida_registrada_por=%s, "
        "saida_real_em='2000-01-01' where id=%s",
        (a.autorizador, rid),
        "AG004",
    )
    h.iniciar(rid, a.autorizador)
    _falha(
        "update public.requisicoes set status='concluida', chegada_registrada_por=%s, "
        "chegada_real_em=now() + interval '1 day' where id=%s",
        (a.autorizador, rid),
        "AG004",
    )


def test_insert_com_carimbo_e_recusado_e_criado_em_e_forcado(a: A) -> None:
    v = h.novo_veiculo()
    _falha(
        "insert into public.requisicoes (usuario_id, nome_solicitante, unidade, hora_saida, trajeto, "
        "veiculo_id, saida_real_em) values (%s,'T','UEA',now(),'A-B',%s,now())",
        (a.dono, v),
        "AG004",
    )
    rid = uuid.uuid4()
    _exec(
        "insert into public.requisicoes (id, usuario_id, nome_solicitante, unidade, hora_saida, trajeto, "
        "veiculo_id, criado_em) values (%s,%s,'T','UEA',now(),'A-B',%s,'2000-01-01')",
        (rid, a.dono, v),
    )
    with h.conectar() as c:
        (recente,) = c.execute(
            "select now() - criado_em < interval '1 minute' from public.requisicoes where id=%s", (rid,)
        ).fetchone() or (False,)
    assert recente


# --- AG004 nas demais colunas --------------------------------------------------------
@pytest.mark.parametrize(
    "set_clause,valor",
    [
        ("hora_saida = now() + interval '9 days'", None),
        ("trajeto = 'outro'", None),
        ("unidade = 'PF'", None),
        ("nome_solicitante = 'Outro'", None),
        ("observacao = 'nova'", None),
        ("criado_em = now() + interval '1 day'", None),
        ("usuario_id = %s", "outro_usuario"),
        ("veiculo_id = %s", "outro_veiculo"),
        ("autorizado_por = %s", "outro_usuario"),
        ("saida_registrada_por = %s", "outro_usuario"),
        ("chegada_registrada_por = %s", "outro_usuario"),
        ("cancelada_por = %s", "outro_usuario"),
    ],
)
def test_colunas_congeladas_apos_a_decisao(a: A, set_clause: str, valor: str | None) -> None:
    rid = _nova(a)
    h.autorizar(rid, a.autorizador)
    params: tuple = ()
    if valor == "outro_usuario":
        params = (h.criar_usuario("outro", "autorizador"),)
    elif valor == "outro_veiculo":
        params = (h.novo_veiculo(),)
    _falha(f"update public.requisicoes set {set_clause} where id=%s", (*params, rid), "AG004")


def test_campos_descritivos_editaveis_enquanto_solicitada(a: A) -> None:
    rid = _nova(a)
    _exec("update public.requisicoes set trajeto='novo', observacao='ok' where id=%s", (rid,))


# --- estados terminais -----------------------------------------------------------------
def _levar_a(final: str, a: A) -> uuid.UUID:
    rid = _nova(a)
    if final == "negada":
        h.negar(rid, a.autorizador)
    elif final == "cancelada":
        h.cancelar(rid, a.dono)
    else:
        h.autorizar(rid, a.autorizador)
        h.iniciar(rid, a.autorizador)
        h.concluir(rid, a.autorizador)
    return rid


@pytest.mark.parametrize("final", ["negada", "cancelada", "concluida"])
@pytest.mark.parametrize(
    "destino", ["solicitada", "autorizada", "em_uso", "concluida", "negada", "cancelada"]
)
def test_terminal_nao_vai_para_nenhum_status(a: A, final: str, destino: str) -> None:
    if destino == final:
        pytest.skip("status igual ao atual e um no-op, nao uma transicao")
    rid = _levar_a(final, a)
    _falha(
        "update public.requisicoes set status=%s::public.status_requisicao where id=%s",
        (destino, rid),
        "AG001",
    )
    assert h.status_de(rid) == final


@pytest.mark.parametrize("final", ["negada", "cancelada", "concluida"])
def test_terminal_nao_edita_nenhum_campo(a: A, final: str) -> None:
    rid = _levar_a(final, a)
    _falha("update public.requisicoes set trajeto='x' where id=%s", (rid,), "AG001")


# --- ator inexistente e veiculo inativo -----------------------------------------------
def test_ator_inexistente_e_recusado(a: A) -> None:
    rid = _nova(a)
    _falha(
        "update public.requisicoes set status='autorizada', autorizado_por=%s where id=%s",
        (uuid.uuid4(), rid),
        "AG002",
    )


def test_veiculo_inativo_nao_aceita_requisicao_nem_troca(a: A) -> None:
    inativo = h.novo_veiculo()
    h.alterar_veiculo(inativo, a.admin, "ativo=false")  # migration 13: responsavel admin
    with h.conectar() as c:
        with pytest.raises(psycopg.Error) as exc:
            h.inserir_requisicao(c, a.dono, inativo)
    assert h.sqlstate(exc) == "AG011"
    rid = _nova(a)
    _falha("update public.requisicoes set veiculo_id=%s where id=%s", (inativo, rid), "AG011")


def test_veiculo_ficticio_da_seed_esta_inativo() -> None:
    """Presume banco recem-criado: a migration 10 desativa o veiculo de exemplo."""
    with h.conectar() as c:
        rows = c.execute(
            "select ativo from public.veiculos where placa='AAA0A00' and descricao='Veículo de exemplo'"
        ).fetchall()
    assert all(not ativo for (ativo,) in rows)


# --- auditoria: cancelada_por, perfis, veiculos, CHECKs ---------------------------------
@pytest.mark.parametrize("quem", ["dono", "autorizador", "admin"])
def test_cancelamento_registra_o_ator_na_auditoria(a: A, quem: str) -> None:
    rid = _nova(a)
    ator = getattr(a, quem)
    h.cancelar(rid, ator)
    linhas = h.auditoria_de("requisicoes", rid)
    assert linhas[-1] == ("transicao", ator, "solicitada", "cancelada")


def test_unidade_do_perfil_alterada_gera_auditoria() -> None:
    uid = h.criar_usuario("x")
    assert h.auditoria_de("perfis", uid)[0][0] == "perfil_criado"
    admin = h.criar_usuario("adm", "admin")
    h.alterar_unidade(uid, admin, "PF")  # migration 13: ator na auditoria
    assert h.auditoria_de("perfis", uid)[-1] == ("perfil_unidade_alterada", admin, "UEA", "PF")


def test_veiculo_criado_e_alterado_geram_auditoria() -> None:
    admin = h.criar_usuario("adm", "admin")
    v = h.novo_veiculo(admin)
    h.alterar_veiculo(v, admin, "descricao='novo nome', ativo=false")
    linhas = h.auditoria_de("veiculos", v)
    assert [x[0] for x in linhas] == ["veiculo_criado", "veiculo_alterado"]
    assert [x[1] for x in linhas] == [admin, admin]
    with h.conectar() as c:
        (campos,) = c.execute(
            "select detalhes->'campos' from public.auditoria where registro_id=%s and acao='veiculo_alterado'",
            (v,),
        ).fetchone() or (None,)
    assert sorted(campos) == ["ativo", "descricao"]


def test_auditoria_checks_de_acao_tabela_e_tamanho() -> None:
    for acao, tabela, detalhes in [
        ("acao_inventada", "perfis", "{}"),
        ("acesso_negado", "tabela_inventada", "{}"),
        ("acesso_negado", "perfis", json.dumps({"x": "a" * 3000})),
    ]:
        with pytest.raises(psycopg.errors.CheckViolation):
            _exec(
                "insert into public.auditoria (tabela, acao, detalhes) values (%s,%s,%s::jsonb)",
                (tabela, acao, detalhes),
            )


def test_registrar_auditoria_so_aceita_acoes_do_backend() -> None:
    admin = h.criar_usuario("adm", "admin")
    with h.conectar() as c:
        (novo_id,) = c.execute(
            "select public.registrar_auditoria('perfis', null, 'acao_administrativa', %s)", (admin,)
        ).fetchone() or (None,)
    assert novo_id is not None
    for proibida in ("transicao", "criada", "configuracao_alterada"):
        _falha(
            "select public.registrar_auditoria('requisicoes', null, %s, %s)", (proibida, admin), "AG003"
        )


# --- grants / privilegios ---------------------------------------------------------------
TABELAS = ["perfis", "veiculos", "requisicoes", "auditoria", "configuracoes", "convites"]


def _tem(papel: str, tabela: str, priv: str) -> bool:
    with h.conectar() as c:
        (r,) = c.execute(
            "select has_table_privilege(%s, %s, %s)", (papel, f"public.{tabela}", priv)
        ).fetchone() or (False,)
    return bool(r)


@pytest.mark.parametrize("tabela", TABELAS)
@pytest.mark.parametrize("priv", ["TRIGGER", "REFERENCES"])
def test_service_role_sem_trigger_nem_references(tabela: str, priv: str) -> None:
    assert not _tem("service_role", tabela, priv)


@pytest.mark.parametrize("tabela", [t for t in TABELAS if t != "auditoria"])
@pytest.mark.parametrize("priv", ["DELETE", "TRUNCATE"])
def test_service_role_sem_delete_nem_truncate(tabela: str, priv: str) -> None:
    assert not _tem("service_role", tabela, priv)


@pytest.mark.parametrize("priv", ["INSERT", "UPDATE", "DELETE", "TRUNCATE", "TRIGGER", "REFERENCES"])
def test_service_role_so_le_auditoria(priv: str) -> None:
    assert not _tem("service_role", "auditoria", priv)
    assert _tem("service_role", "auditoria", "SELECT")


@pytest.mark.parametrize(
    "sql",
    [
        "insert into public.auditoria (tabela, acao) values ('perfis', 'acesso_negado')",
        "update public.auditoria set acao = 'acesso_negado'",
        "delete from public.auditoria",
        "truncate public.auditoria",
        "delete from public.requisicoes",
        "truncate public.perfis",
    ],
)
def test_service_role_recebe_42501(sql: str) -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.como("service_role") as c:
            c.execute(sql)
    assert h.sqlstate(exc) == "42501"


def test_service_role_ainda_opera_o_fluxo_normal(a: A) -> None:
    """Triggers SECURITY DEFINER escrevem na auditoria mesmo sem INSERT do service_role."""
    v = h.novo_veiculo()
    rid = uuid.uuid4()
    with h.como("service_role") as c:
        c.execute(
            "insert into public.requisicoes (id, usuario_id, nome_solicitante, unidade, hora_saida, "
            "trajeto, veiculo_id) values (%s,%s,'T','UEA',now(),'A-B',%s)",
            (rid, a.dono, v),
        )
        (n,) = c.execute("select count(*) from public.auditoria where registro_id=%s", (rid,)).fetchone() or (0,)
        assert n == 1


def test_gatilhos_de_guarda_estao_em_enable_always() -> None:
    with h.conectar() as c:
        rows = c.execute(
            "select c.relname, t.tgname, t.tgenabled from pg_trigger t "
            "join pg_class c on c.oid = t.tgrelid "
            "where c.relnamespace = 'public'::regnamespace and not t.tgisinternal "
            "and c.relname = any(%s)",
            (TABELAS,),
        ).fetchall()
    assert rows
    fora = [(r, t) for r, t, e in rows if e != "A"]
    assert fora == []


def test_politicas_explicitas_de_negacao_existem() -> None:
    with h.conectar() as c:
        rows = c.execute(
            "select tablename, cmd from pg_policies where schemaname='public' and policyname like %s",
            ("%\\_sem\\_%",),
        ).fetchall()
    for t in TABELAS:
        cmds = {cmd for tab, cmd in rows if tab == t}
        assert {"INSERT", "UPDATE", "DELETE"} <= cmds, (t, cmds)


def test_indices_das_fks_de_requisicoes() -> None:
    esperados = {
        "ix_requisicoes_autorizado_por",
        "ix_requisicoes_saida_registrada_por",
        "ix_requisicoes_chegada_registrada_por",
        "ix_requisicoes_cancelada_por",
    }
    with h.conectar() as c:
        existentes = {
            r[0]
            for r in c.execute(
                "select indexname from pg_indexes where schemaname='public' and tablename='requisicoes'"
            ).fetchall()
        }
    assert esperados <= existentes


@pytest.mark.parametrize("tabela", ["perfis", "veiculos", "requisicoes"])
def test_truncate_cascade_e_bloqueado(tabela: str) -> None:
    with pytest.raises(psycopg.Error) as exc:
        _exec(f"truncate public.{tabela} cascade")
    assert h.sqlstate(exc) == "AG003"


@pytest.mark.parametrize("tabela", ["perfis", "veiculos", "convites"])
def test_truncate_simples_e_bloqueado(tabela: str) -> None:
    # perfis/veiculos sao referenciadas por FK: o Postgres pode recusar antes do trigger (0A000).
    with pytest.raises(psycopg.Error) as exc:
        _exec(f"truncate public.{tabela}")
    assert h.sqlstate(exc) in {"AG003", "0A000"}


# --- concorrencia com sobreposicao forcada ---------------------------------------------------
def _rodar_em_paralelo(tarefas: list) -> list[str]:
    # Timeouts folgados (barreira 60 s, join 90 s): em Docker Desktop/WSL a VM chega a congelar ~50 s
    # sob carga, e um timeout curto virava falha espuria sem relacao com o banco.
    resultados: list[str] = []
    lock = threading.Lock()

    def envolver(fn) -> None:
        try:
            r = fn()
        except psycopg.Error as e:
            r = str(e.sqlstate)
        except threading.BrokenBarrierError:
            r = "barreira_quebrada"
        with lock:
            resultados.append(r)

    ts = [threading.Thread(target=envolver, args=(f,)) for f in tarefas]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=90)
    return resultados


def test_duas_insercoes_sobrepostas_so_uma_persiste(a: A) -> None:
    veiculo = h.novo_veiculo()
    barreira = threading.Barrier(2)

    def inserir() -> str:
        with psycopg.connect(h.DSN) as conn:
            barreira.wait(timeout=60)
            try:
                h.inserir_requisicao(conn, a.dono, veiculo)
                conn.execute("select pg_sleep(0.5)")  # segura a transacao aberta: forca a disputa
                conn.commit()
                return "ok"
            except psycopg.errors.UniqueViolation:
                conn.rollback()
                return "conflito"

    assert sorted(_rodar_em_paralelo([inserir, inserir])) == ["conflito", "ok"]
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.requisicoes where veiculo_id=%s "
            "and status in ('solicitada','autorizada','em_uso')",
            (veiculo,),
        ).fetchone() or (0,)
    assert n == 1


def test_autorizar_e_cancelar_simultaneos_so_um_vence(a: A) -> None:
    rid = _nova(a)
    barreira = threading.Barrier(2)

    def agir(sql: str, ator: uuid.UUID) -> str:
        with psycopg.connect(h.DSN) as conn:
            barreira.wait(timeout=60)
            try:
                conn.execute(sql, (ator, rid))
                conn.execute("select pg_sleep(0.5)")  # mantem o lock da linha ate o commit
                conn.commit()
                return "ok"
            except psycopg.Error as e:
                conn.rollback()
                return str(e.sqlstate)

    autorizar = lambda: agir(  # noqa: E731
        "update public.requisicoes set status='autorizada', autorizado_por=%s where id=%s", a.autorizador
    )
    cancelar = lambda: agir(  # noqa: E731
        "update public.requisicoes set status='cancelada', cancelada_por=%s where id=%s", a.dono
    )
    res = _rodar_em_paralelo([autorizar, cancelar])
    assert res.count("ok") == 1, res
    perdedor = next(r for r in res if r != "ok")
    # Autorizada primeiro => cancelar do dono e sem permissao (AG002); cancelada primeiro => AG001.
    assert perdedor in {"AG001", "AG002"}
    assert h.status_de(rid) in {"autorizada", "cancelada"}
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.auditoria where registro_id=%s and acao='transicao'", (rid,)
        ).fetchone() or (0,)
    assert n == 1
