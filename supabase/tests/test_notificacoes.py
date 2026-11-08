"""Testes da migration 20261110120000 (notificacoes: central no app + e-mail de criticos).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). Os testes gravam e nao apagam (auditoria e
notificacoes sao imutaveis); por isso cada teste cria os proprios usuarios/requisicoes e compara
destinatarios com o conjunto de staff ATIVO calculado na hora (o banco pode ter staff de outros testes).

    pytest supabase/tests/test_notificacoes.py -m db_local -p no:xdist
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261110120000_notificacoes.sql"
).read_text(encoding="utf-8")

RPCS = (
    "public.listar_notificacoes(uuid, integer, integer, boolean)",
    "public.contar_notificacoes_nao_lidas(uuid)",
    "public.marcar_notificacao_lida(uuid, uuid)",
    "public.marcar_todas_lidas(uuid)",
    "public.registrar_notificacao_sistema(text, text)",
    "public.reservar_emails_notificacao(integer)",
    "public.marcar_email_notificacao(uuid, text, text)",
    "public.purgar_notificacoes(integer)",
)
TIPOS = {
    "requisicao_nova", "requisicao_autorizada", "requisicao_negada", "requisicao_saida",
    "requisicao_chegada", "requisicao_cancelada_terceiros", "requisicao_cancelada_pelo_solicitante",
    "checklist_avaria", "convite_aceito", "perfil_alterado_admin", "configuracao_alterada",
    "sistema_falha_envio",
}  # fmt: skip


# --- infraestrutura -----------------------------------------------------------------------------
def _flag(coluna: str, valor: bool, por: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute(f"update public.configuracoes set {coluna} = %s, alterado_por = %s", (valor, por))


def _ler_flags() -> dict[str, bool]:
    with h.conectar() as c:
        row = c.execute("select checklist, notificacao_email_criticos from public.configuracoes").fetchone()
    assert row is not None
    return {"checklist": bool(row[0]), "notificacao_email_criticos": bool(row[1])}


@pytest.fixture(autouse=True)
def _flags() -> Iterator[None]:
    """Isola os flags do teste: ambos desligados antes; restaura os valores originais depois."""
    por = h.admin_ativo_qualquer() or h.criar_usuario("n-admin-cfg", "admin")
    orig = _ler_flags()
    for k, v in orig.items():
        if v:
            _flag(k, False, por)
    try:
        yield
    finally:
        for k, v in orig.items():
            if _ler_flags()[k] != v:
                _flag(k, v, por)


@pytest.fixture
def e() -> dict:
    adm = h.admin_ativo_qualquer() or h.criar_usuario("n-admin", "admin")
    return {
        "adm": adm,
        "adm2": h.criar_usuario("n-adm2", "admin"),
        "aut": h.criar_usuario("n-aut", "autorizador"),
        "aut_inativo": h.criar_usuario("n-aut-off", "autorizador", ativo=False),
        "sol": h.criar_usuario("n-sol"),
        "sol_inativo": h.criar_usuario("n-sol-off", ativo=False),
    }


def _staff_ativo() -> set[uuid.UUID]:
    with h.conectar() as c:
        return {r[0] for r in c.execute(
            "select id from public.perfis where ativo and role in ('autorizador','admin')").fetchall()}


def _admins_ativos() -> set[uuid.UUID]:
    with h.conectar() as c:
        return {r[0] for r in c.execute(
            "select id from public.perfis where ativo and role = 'admin'").fetchall()}


def _por_req(rid: uuid.UUID, tipo: str | None = None) -> list[tuple]:
    """[(usuario_id, tipo, critica, email_status, codigo)] das notificacoes da requisicao."""
    with h.conectar() as c:
        return c.execute(
            "select usuario_id, tipo, critica, email_status, codigo from public.notificacoes "
            "where requisicao_id = %s and (%s::text is null or tipo = %s) order by criada_em, id",
            (rid, tipo, tipo),
        ).fetchall()


def _dest(rid: uuid.UUID, tipo: str) -> set[uuid.UUID]:
    return {r[0] for r in _por_req(rid, tipo)}


def _nova_req(dono: uuid.UUID) -> uuid.UUID:
    vid = h.novo_veiculo()
    with h.conectar() as c:
        return h.inserir_requisicao(c, dono, vid)


def _por_ref(ref: uuid.UUID, tipo: str) -> list[tuple]:
    with h.conectar() as c:
        return c.execute(
            "select usuario_id, tipo, critica, email_status, codigo from public.notificacoes "
            "where referencia_id = %s and tipo = %s", (ref, tipo)).fetchall()


def _chamar(sql: str, params: tuple = ()):  # noqa: ANN202
    with h.conectar() as c:
        return c.execute(sql, params).fetchone()[0]  # type: ignore[index]


def _inserir(usuario: uuid.UUID, *, tipo: str = "configuracao_alterada", dias: float = 0,
             lida: bool = False, email: str | None = None, critica: bool = False,
             codigo: str | None = None, tentativas: int = 0) -> uuid.UUID:
    """Insere direto (dono do banco) para montar cenarios; criada_em recuada em `dias`."""
    nid = uuid.uuid4()
    with h.conectar() as c:
        c.execute(
            "insert into public.notificacoes (id, usuario_id, tipo, codigo, critica, criada_em, lida_em, "
            "email_status, email_tentativas, email_reservado_em) values (%s, %s, %s, %s, %s, "
            "now() - make_interval(secs => %s), %s, %s, %s, %s)",
            (nid, usuario, tipo, codigo, critica or email is not None, dias * 86400,
             None, email, tentativas, None),
        )
        if lida:
            c.execute("update public.notificacoes set lida_em = now() where id = %s", (nid,))
    return nid


def _esvaziar_fila_email() -> None:
    """Zera a fila de e-mail do banco de teste (outros testes deixam pendentes)."""
    with h.conectar() as c:
        c.execute("update public.notificacoes set email_status = 'ignorado' "
                  "where email_status in ('pendente','falhou','enviando')")


# --- tipos e destinatarios -------------------------------------------------------------------------
def test_requisicao_nova_vai_para_todo_staff_ativo_exceto_o_ator(e: dict) -> None:
    rid = _nova_req(e["sol"])
    linhas = _por_req(rid, "requisicao_nova")
    dest = {r[0] for r in linhas}
    assert dest == _staff_ativo()
    assert e["aut"] in dest and e["adm2"] in dest
    assert e["aut_inativo"] not in dest and e["sol"] not in dest
    assert all(r[2] is True for r in linhas)  # critica
    assert len(linhas) == len(dest)  # cada perfil recebe 1 so
    assert {r[1] for r in _por_req(rid)} == {"requisicao_nova"}


def test_ator_e_excluido_quando_staff_cria_a_propria_requisicao(e: dict) -> None:
    rid = _nova_req(e["aut"])
    dest = _dest(rid, "requisicao_nova")
    assert e["aut"] not in dest
    assert dest == _staff_ativo() - {e["aut"]}


def test_autorizada_negada_saida_chegada_vao_so_ao_solicitante(e: dict) -> None:
    rid = _nova_req(e["sol"])
    h.autorizar(rid, e["aut"])
    h.iniciar(rid, e["aut"])
    h.concluir(rid, e["adm2"])
    esperado = {
        "requisicao_autorizada": True,
        "requisicao_saida": False,
        "requisicao_chegada": True,
    }
    for tipo, critica in esperado.items():
        linhas = _por_req(rid, tipo)
        assert [(r[0], r[2]) for r in linhas] == [(e["sol"], critica)], tipo

    rid2 = _nova_req(e["sol"])
    h.negar(rid2, e["aut"])
    assert [(r[0], r[2]) for r in _por_req(rid2, "requisicao_negada")] == [(e["sol"], True)]


def test_cancelada_por_terceiro_notifica_o_dono_e_pelo_dono_notifica_staff(e: dict) -> None:
    rid = _nova_req(e["sol"])
    h.cancelar(rid, e["aut"])
    assert [(r[0], r[2]) for r in _por_req(rid, "requisicao_cancelada_terceiros")] == [(e["sol"], True)]
    assert _por_req(rid, "requisicao_cancelada_pelo_solicitante") == []

    rid2 = _nova_req(e["sol"])
    h.cancelar(rid2, e["sol"])
    linhas = _por_req(rid2, "requisicao_cancelada_pelo_solicitante")
    assert {r[0] for r in linhas} == _staff_ativo()
    assert all(r[2] is False for r in linhas)  # nao critica
    assert _por_req(rid2, "requisicao_cancelada_terceiros") == []


def test_autoautorizacao_nao_notifica_o_ator(e: dict) -> None:
    rid = _nova_req(e["aut"])
    h.autorizar(rid, e["aut"])  # dono e ator: ninguem a notificar
    assert _por_req(rid, "requisicao_autorizada") == []


def test_dono_inativo_nao_recebe(e: dict) -> None:
    rid = _nova_req(e["sol"])
    h.alterar_perfil(e["sol"], e["adm"], ativo=False)
    try:
        h.autorizar(rid, e["aut"])
        assert _por_req(rid, "requisicao_autorizada") == []
    finally:
        h.alterar_perfil(e["sol"], e["adm"], ativo=True)


def test_deduplicacao_de_evento_unico(e: dict) -> None:
    rid = _nova_req(e["sol"])
    h.autorizar(rid, e["aut"])
    with h.conectar() as c:
        # a mesma notificacao de novo e barrada pelo indice unico (inserindo direto)...
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(
                "insert into public.notificacoes (usuario_id, tipo, requisicao_id, critica) "
                "values (%s, 'requisicao_autorizada', %s, true)", (e["sol"], rid))
        # ...e pelo helper do trigger (ON CONFLICT DO NOTHING), sem erro e sem linha nova.
        n = c.execute(
            "select privado.notificar('requisicao_autorizada', %s, null, true, null, %s, 'usuario', %s)",
            (rid, e["aut"], e["sol"])).fetchone()
    assert n == (0,)
    assert len(_por_req(rid, "requisicao_autorizada")) == 1


# --- checklist_avaria -----------------------------------------------------------------------------
def _checklist(rid: uuid.UUID, momento: str, avarias: bool, por: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute(
            "select public.registrar_checklist(%s, %s, 'meio'::public.nivel_combustivel, %s, %s, %s, '[]'::jsonb)",
            (rid, momento, avarias, "arranhao" if avarias else None, por))


def test_checklist_avaria_so_com_recurso_ligado_e_so_com_avaria(e: dict) -> None:
    # recurso desligado: o banco aceita o checklist, mas nao notifica
    rid_off = _nova_req(e["sol"])
    h.autorizar(rid_off, e["aut"])
    _checklist(rid_off, "saida", True, e["aut"])
    assert _por_req(rid_off, "checklist_avaria") == []

    _flag("checklist", True, e["adm"])
    rid = _nova_req(e["sol"])
    h.autorizar(rid, e["aut"])
    _checklist(rid, "saida", False, e["aut"])  # sem avaria: nada
    assert _por_req(rid, "checklist_avaria") == []
    h.iniciar(rid, e["aut"])
    _checklist(rid, "chegada", True, e["adm2"])
    linhas = _por_req(rid, "checklist_avaria")
    assert {r[0] for r in linhas} == _staff_ativo() - {e["adm2"]}  # ator excluido
    assert all(r[2] is True and r[4] == "chegada" for r in linhas)
    assert e["sol"] not in {r[0] for r in linhas}


def test_checklist_avaria_saida_e_chegada_geram_uma_cada(e: dict) -> None:
    _flag("checklist", True, e["adm"])
    rid = _nova_req(e["sol"])
    h.autorizar(rid, e["aut"])
    _checklist(rid, "saida", True, e["aut"])
    h.iniciar(rid, e["aut"])
    _checklist(rid, "chegada", True, e["aut"])
    por_dest = {(r[0], r[4]) for r in _por_req(rid, "checklist_avaria")}
    alvo = e["adm2"]
    assert (alvo, "saida") in por_dest and (alvo, "chegada") in por_dest


# --- convite, perfil e configuracao ----------------------------------------------------------------
def test_convite_aceito_vai_para_admins_ativos(e: dict) -> None:
    email = h.email_unico()
    uid = h.criar_auth(email)
    cid, token_hash = h.novo_convite(e["adm"], email)
    assert _por_ref(cid, "convite_aceito") == []  # criar o convite nao notifica
    with h.conectar() as c:
        c.execute("select public.consumir_convite(%s, %s, 'Convidado')", (token_hash, uid))
    linhas = _por_ref(cid, "convite_aceito")
    assert {r[0] for r in linhas} == _admins_ativos()
    assert all(r[2] is False for r in linhas)
    assert uid not in {r[0] for r in linhas}


def _notif_perfil(alvo: uuid.UUID) -> list[tuple]:
    return _por_ref(alvo, "perfil_alterado_admin")


def test_perfil_alterado_notifica_outros_admins_em_troca_de_role_ou_desativacao(e: dict) -> None:
    alvo = h.criar_usuario("n-alvo", "autorizador")
    assert _notif_perfil(alvo) == []  # criacao de perfil nao notifica
    h.alterar_perfil(alvo, e["adm"], role="solicitante")
    linhas = _notif_perfil(alvo)
    assert {r[0] for r in linhas} == _admins_ativos() - {e["adm"]}
    assert all(r[2] is False and r[4] == "papel_alterado" for r in linhas)

    h.alterar_perfil(alvo, e["adm2"], ativo=False)
    desat = [r for r in _notif_perfil(alvo) if r[4] == "desativado"]
    assert {r[0] for r in desat} == _admins_ativos() - {e["adm2"]}

    antes = len(_notif_perfil(alvo))
    h.alterar_perfil(alvo, e["adm"], ativo=True)  # reativacao: sem notificacao
    h.alterar_unidade(alvo, e["adm"], "PF")  # unidade: sem notificacao
    assert len(_notif_perfil(alvo)) == antes


def test_admin_desativado_nao_recebe_a_propria_notificacao(e: dict) -> None:
    alvo = h.criar_usuario("n-adm-alvo", "admin")
    h.alterar_perfil(alvo, e["adm"], ativo=False)
    dest = {r[0] for r in _notif_perfil(alvo)}
    assert alvo not in dest and e["adm"] not in dest


def test_configuracao_alterada_vai_para_outros_admins_com_codigo_do_recurso(e: dict) -> None:
    with h.conectar() as c:
        (t0,) = c.execute("select clock_timestamp()").fetchone() or (None,)
    _flag("checklist", True, e["adm"])
    with h.conectar() as c:
        linhas = c.execute(
            "select usuario_id, critica, codigo from public.notificacoes where tipo='configuracao_alterada' "
            "and criada_em >= %s", (t0,)).fetchall()
    assert {r[0] for r in linhas} == _admins_ativos() - {e["adm"]}
    assert all(r[1] is False and r[2] == "checklist" for r in linhas)


def test_perfil_criado_convite_criado_e_revogado_nao_geram_notificacao(e: dict) -> None:
    with h.conectar() as c:
        (antes,) = c.execute("select count(*) from public.notificacoes").fetchone() or (0,)
    h.criar_usuario("n-so-perfil", "solicitante")
    cid, _ = h.novo_convite(e["adm"], h.email_unico())
    with h.conectar() as c:
        c.execute("update public.convites set revogado_em = now(), revogado_por = %s where id = %s",
                  (e["adm"], cid))
        (depois,) = c.execute("select count(*) from public.notificacoes").fetchone() or (0,)
    assert depois == antes


# --- email_status conforme a flag ------------------------------------------------------------------
def test_email_status_conforme_flag_so_para_criticas(e: dict) -> None:
    rid = _nova_req(e["sol"])  # flag desligada
    assert {r[3] for r in _por_req(rid)} == {None}

    _flag("notificacao_email_criticos", True, e["adm"])
    rid2 = _nova_req(e["sol"])
    assert {r[3] for r in _por_req(rid2, "requisicao_nova")} == {"pendente"}
    h.autorizar(rid2, e["aut"])
    h.iniciar(rid2, e["aut"])
    assert {r[3] for r in _por_req(rid2, "requisicao_autorizada")} == {"pendente"}
    assert {r[3] for r in _por_req(rid2, "requisicao_saida")} == {None}  # nao critica


# --- mesma transacao e resiliencia -----------------------------------------------------------------
def test_rollback_da_transicao_desfaz_a_notificacao(e: dict) -> None:
    vid = h.novo_veiculo()
    with psycopg.connect(h.DSN) as conn:
        rid = h.inserir_requisicao(conn, e["sol"], vid)
        (dentro,) = conn.execute(
            "select count(*) from public.notificacoes where requisicao_id = %s", (rid,)).fetchone() or (0,)
        assert dentro > 0  # nasce na mesma transacao (visivel antes do commit, por ela)
        conn.rollback()
    with h.conectar() as c:
        assert c.execute("select count(*) from public.notificacoes where requisicao_id = %s",
                         (rid,)).fetchone() == (0,)


def test_falha_do_trigger_nao_derruba_a_operacao_e_nao_vaza_dados(e: dict) -> None:
    vid = h.novo_veiculo()
    avisos: list[str] = []
    with psycopg.connect(h.DSN) as conn:
        conn.add_notice_handler(lambda d: avisos.append(d.message_primary or ""))
        # DDL transacional: um CHECK que recusa qualquer notificacao nova (desfeito no rollback).
        conn.execute("alter table public.notificacoes add constraint ck_tmp_falha check (false) not valid")
        rid = h.inserir_requisicao(conn, e["sol"], vid)  # a operacao principal segue
        assert conn.execute("select count(*) from public.requisicoes where id = %s", (rid,)).fetchone() == (1,)
        assert conn.execute("select count(*) from public.notificacoes where requisicao_id = %s",
                            (rid,)).fetchone() == (0,)
        assert conn.execute("select count(*) from public.auditoria where registro_id = %s and acao = 'criada'",
                            (rid,)).fetchone() == (1,)
        conn.rollback()
    aviso = [a for a in avisos if "notificacoes_gerar" in a]
    assert aviso and "23514" in aviso[0]
    assert str(e["sol"]) not in aviso[0] and "@" not in aviso[0]


def test_caminho_normal_cria_notificacao_sem_aviso(e: dict) -> None:
    avisos: list[str] = []
    vid = h.novo_veiculo()
    with psycopg.connect(h.DSN) as conn:
        conn.add_notice_handler(lambda d: avisos.append(d.message_primary or ""))
        rid = h.inserir_requisicao(conn, e["sol"], vid)
        conn.commit()
    assert not [a for a in avisos if "notificacoes_gerar" in a]
    assert _dest(rid, "requisicao_nova") == _staff_ativo()


# --- permissoes e RLS --------------------------------------------------------------------------------
@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_api_sem_select_nem_execute(papel: str, e: dict) -> None:
    with h.como(papel, e["sol"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("select * from public.notificacoes")
    with h.conectar() as c:
        for rpc in RPCS:
            (ok,) = c.execute("select has_function_privilege(%s, %s::regprocedure, 'execute')",
                              (papel, rpc)).fetchone() or (True,)
            assert ok is False, (papel, rpc)
        for tabela_priv in ("select", "insert", "update", "delete", "truncate"):
            (ok,) = c.execute("select has_table_privilege(%s, 'public.notificacoes', %s)",
                              (papel, tabela_priv)).fetchone() or (True,)
            assert ok is False
        assert c.execute("select has_function_privilege(%s, 'public.notificacoes_gerar()', 'execute')",
                         (papel,)).fetchone() == (False,)


def test_service_role_so_le_a_tabela_e_executa_as_rpcs(e: dict) -> None:
    with h.conectar() as c:
        privs = {p: c.execute("select has_table_privilege('service_role', 'public.notificacoes', %s)",
                              (p,)).fetchone()[0] for p in ("select", "insert", "update", "delete", "truncate")}  # type: ignore[index]
        assert privs == {"select": True, "insert": False, "update": False, "delete": False, "truncate": False}
        for rpc in RPCS:
            assert c.execute("select has_function_privilege('service_role', %s::regprocedure, 'execute')",
                             (rpc,)).fetchone() == (True,), rpc
        assert c.execute(
            "select has_function_privilege('service_role', "
            "'privado.notificar(text,uuid,uuid,boolean,text,uuid,text,uuid,uuid)'::regprocedure, 'execute')"
        ).fetchone() == (False,)
    with h.como("service_role") as c:
        c.execute("select count(*) from public.notificacoes")
        r = c.execute("select public.contar_notificacoes_nao_lidas(%s)", (e["sol"],)).fetchone()
        assert r is not None and "nao_lidas" in r[0]
    with h.como("service_role") as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("update public.notificacoes set lida_em = now()")


def test_rls_ligado_e_funcoes_definer_com_search_path_vazio() -> None:
    with h.conectar() as c:
        assert c.execute("select relrowsecurity from pg_class where oid = 'public.notificacoes'::regclass"
                         ).fetchone() == (True,)
        rows = c.execute(
            "select p.proname, p.prosecdef, p.proconfig from pg_proc p where p.pronamespace in "
            "('public'::regnamespace, 'privado'::regnamespace) and p.proname in "
            "('listar_notificacoes','contar_notificacoes_nao_lidas','marcar_notificacao_lida',"
            "'marcar_todas_lidas','registrar_notificacao_sistema','reservar_emails_notificacao',"
            "'marcar_email_notificacao','purgar_notificacoes','notificacoes_gerar','notificar')"
        ).fetchall()
    assert len(rows) == 10
    for nome, secdef, cfg in rows:
        assert secdef is True, nome
        assert cfg and any(x.replace(" ", "") in ("search_path=", 'search_path=""') for x in cfg), nome


def test_triggers_sempre_ativos_e_unico_na_auditoria() -> None:
    with h.conectar() as c:
        rows = c.execute(
            "select tgrelid::regclass::text, tgname, tgenabled from pg_trigger where not tgisinternal "
            "and tgname in ('trg_auditoria_notificar','trg_notificacoes_proteger','trg_notificacoes_sem_truncate')"
        ).fetchall()
    assert {(r[1], r[2]) for r in rows} == {
        ("trg_auditoria_notificar", "A"), ("trg_notificacoes_proteger", "A"),
        ("trg_notificacoes_sem_truncate", "A")}


# --- RPCs de usuario / IDOR -------------------------------------------------------------------------
def test_marcar_lida_idor_e_idempotencia() -> None:
    dono, outro = h.criar_usuario("n-dono"), h.criar_usuario("n-outro")
    nid = _inserir(dono)
    with h.conectar() as c:
        with pytest.raises(psycopg.Error) as exc:
            c.execute("select public.marcar_notificacao_lida(%s, %s)", (outro, nid))
        assert h.sqlstate(exc) == "AG029"
        msg_alheia = str(exc.value)
        with pytest.raises(psycopg.Error) as exc2:
            c.execute("select public.marcar_notificacao_lida(%s, %s)", (outro, uuid.uuid4()))
        assert h.sqlstate(exc2) == "AG029" and str(exc2.value) == msg_alheia  # nao revela existencia
        assert c.execute("select lida_em from public.notificacoes where id = %s", (nid,)).fetchone() == (None,)
        r1 = c.execute("select public.marcar_notificacao_lida(%s, %s)", (dono, nid)).fetchone()[0]  # type: ignore[index]
        r2 = c.execute("select public.marcar_notificacao_lida(%s, %s)", (dono, nid)).fetchone()[0]  # type: ignore[index]
    assert r1["lida_em"] == r2["lida_em"] and r1["id"] == str(nid)


def test_marcar_todas_lidas_so_do_proprio_usuario() -> None:
    a, b = h.criar_usuario("n-a"), h.criar_usuario("n-b")
    for _ in range(3):
        _inserir(a)
    _inserir(b)
    assert _chamar("select public.marcar_todas_lidas(%s)", (a,)) == {"marcadas": 3}
    assert _chamar("select public.contar_notificacoes_nao_lidas(%s)", (a,)) == {"nao_lidas": 0}
    assert _chamar("select public.contar_notificacoes_nao_lidas(%s)", (b,)) == {"nao_lidas": 1}
    assert _chamar("select public.marcar_todas_lidas(%s)", (a,)) == {"marcadas": 0}


def test_listar_formato_ordem_paginacao_e_limites() -> None:
    u, outro = h.criar_usuario("n-lista"), h.criar_usuario("n-lista2")
    ids = [_inserir(u, dias=d, codigo="checklist") for d in (5, 4, 3, 2, 1)]  # mais nova por ultimo
    _inserir(outro)
    nid_lida = ids[0]
    _chamar("select public.marcar_notificacao_lida(%s, %s)", (u, nid_lida))

    r = _chamar("select public.listar_notificacoes(%s)", (u,))
    assert set(r) == {"total", "nao_lidas", "limite", "offset", "itens"}
    assert (r["total"], r["nao_lidas"], r["limite"], r["offset"]) == (5, 4, 20, 0)
    assert [i["id"] for i in r["itens"]] == [str(x) for x in reversed(ids)]  # criada_em desc
    assert set(r["itens"][0]) == {"id", "tipo", "requisicao_id", "referencia_id", "codigo", "critica",
                                  "criada_em", "lida_em"}
    assert "usuario_id" not in r["itens"][0] and not any(k.startswith("email") for k in r["itens"][0])

    pag = _chamar("select public.listar_notificacoes(%s, 2, 2)", (u,))
    assert [i["id"] for i in pag["itens"]] == [str(ids[2]), str(ids[1])] and pag["total"] == 5

    nl = _chamar("select public.listar_notificacoes(%s, 20, 0, true)", (u,))
    assert nl["total"] == 4 and nl["nao_lidas"] == 4 and str(nid_lida) not in {i["id"] for i in nl["itens"]}

    assert _chamar("select public.listar_notificacoes(%s, 1000)", (u,))["limite"] == 100
    assert _chamar("select public.listar_notificacoes(%s, 0)", (u,))["limite"] == 1
    assert _chamar("select public.listar_notificacoes(%s, 20, -5)", (u,))["offset"] == 0
    vazio = _chamar("select public.listar_notificacoes(%s)", (uuid.uuid4(),))
    assert vazio["itens"] == [] and vazio["total"] == 0

    for sql, params in (("select public.listar_notificacoes(%s, 20, 100001)", (u,)),
                        ("select public.listar_notificacoes(null)", ()),
                        ("select public.contar_notificacoes_nao_lidas(null)", ()),
                        ("select public.marcar_todas_lidas(null)", ())):
        with h.conectar() as c, pytest.raises(psycopg.Error) as exc:
            c.execute(sql, params)
        assert h.sqlstate(exc) == "AG030", sql


def test_ler_e_marcar_nao_geram_auditoria() -> None:
    u = h.criar_usuario("n-sem-aud")
    nid = _inserir(u)
    with h.conectar() as c:
        (antes,) = c.execute("select count(*) from public.auditoria").fetchone() or (0,)
    _chamar("select public.listar_notificacoes(%s)", (u,))
    _chamar("select public.marcar_notificacao_lida(%s, %s)", (u, nid))
    _chamar("select public.marcar_todas_lidas(%s)", (u,))
    with h.conectar() as c:
        assert c.execute("select count(*) from public.auditoria").fetchone() == (antes,)


# --- imutabilidade --------------------------------------------------------------------------------
def test_update_delete_truncate_restritos() -> None:
    u = h.criar_usuario("n-imut")
    nid = _inserir(u)
    casos = [
        ("update public.notificacoes set tipo = 'requisicao_nova' where id = %s", "AG004"),
        ("update public.notificacoes set usuario_id = gen_random_uuid() where id = %s", "AG004"),
        ("update public.notificacoes set critica = true where id = %s", "AG004"),
        ("update public.notificacoes set criada_em = now() where id = %s", "AG004"),
        ("update public.notificacoes set email_status = 'pendente' where id = %s", "AG004"),
        ("delete from public.notificacoes where id = %s", "AG003"),
    ]
    for sql, esperado in casos:
        with h.conectar() as c, pytest.raises(psycopg.Error) as exc:
            c.execute(sql, (nid,))
        assert h.sqlstate(exc) == esperado, sql
    with h.conectar() as c, pytest.raises(psycopg.Error) as exc:
        c.execute("truncate public.notificacoes")
    assert h.sqlstate(exc) == "AG003"
    # lida_em: null -> agora, depois imutavel
    with h.conectar() as c:
        c.execute("update public.notificacoes set lida_em = now() - interval '1 day' where id = %s", (nid,))
        (lida,) = c.execute("select lida_em > now() - interval '1 minute' from public.notificacoes where id = %s",
                            (nid,)).fetchone() or (False,)
        assert lida  # o banco carimba com o relogio do servidor
        with pytest.raises(psycopg.Error) as exc:
            c.execute("update public.notificacoes set lida_em = null where id = %s", (nid,))
    assert h.sqlstate(exc) == "AG004"


# --- sistema_falha_envio ----------------------------------------------------------------------------
def test_sistema_falha_envio_para_admins_ativos_com_dedupe_de_janela(e: dict) -> None:
    cod = "smtp_" + uuid.uuid4().hex[:8]
    r = _chamar("select public.registrar_notificacao_sistema('sistema_falha_envio', %s)", (cod,))
    admins = _admins_ativos()
    assert r == {"criadas": len(admins)}
    with h.conectar() as c:
        linhas = c.execute("select usuario_id, critica, email_status, requisicao_id from public.notificacoes "
                           "where tipo = 'sistema_falha_envio' and codigo = %s", (cod,)).fetchall()
    assert {x[0] for x in linhas} == admins and e["aut"] not in admins
    assert all(x[1] is True and x[3] is None for x in linhas)
    # repetir na mesma hora nao cria; outro codigo cria
    assert _chamar("select public.registrar_notificacao_sistema('sistema_falha_envio', %s)", (cod,)) == {"criadas": 0}
    outro = "smtp_" + uuid.uuid4().hex[:8]
    assert _chamar("select public.registrar_notificacao_sistema('sistema_falha_envio', %s)",
                   (outro,))["criadas"] == len(admins)


def test_sistema_falha_envio_valida_parametros_e_segue_flag_de_email(e: dict) -> None:
    for tipo, cod in (("outro_tipo", "abc"), ("sistema_falha_envio", "Com Espaco"),
                      ("sistema_falha_envio", "x" * 41), ("sistema_falha_envio", "a@b.c"),
                      ("sistema_falha_envio", None), (None, "abc")):
        with h.conectar() as c, pytest.raises(psycopg.Error) as exc:
            c.execute("select public.registrar_notificacao_sistema(%s, %s)", (tipo, cod))
        assert h.sqlstate(exc) == "AG030", (tipo, cod)
    _flag("notificacao_email_criticos", True, e["adm"])
    cod = "flag_" + uuid.uuid4().hex[:8]
    _chamar("select public.registrar_notificacao_sistema('sistema_falha_envio', %s)", (cod,))
    with h.conectar() as c:
        sts = {r[0] for r in c.execute("select email_status from public.notificacoes where codigo = %s",
                                       (cod,)).fetchall()}
    assert sts == {"pendente"}


def test_sistema_falha_envio_concorrente_respeita_a_janela() -> None:
    cod = "conc_" + uuid.uuid4().hex[:8]
    admins = _admins_ativos()
    resultados: list[int] = []
    barreira = threading.Barrier(2, timeout=60)

    def worker() -> None:
        with psycopg.connect(h.DSN, autocommit=True) as c:
            barreira.wait()
            r = c.execute("select public.registrar_notificacao_sistema('sistema_falha_envio', %s)",
                          (cod,)).fetchone()
            resultados.append(r[0]["criadas"])  # type: ignore[index]

    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(90)
    assert sorted(resultados) == [0, len(admins)]


# --- fila de e-mail ---------------------------------------------------------------------------------
@pytest.fixture
def fila(e: dict) -> Iterator[dict]:
    """Fila limpa e flag ligada. Cria um usuario ativo para as linhas montadas a mao."""
    _esvaziar_fila_email()
    _flag("notificacao_email_criticos", True, e["adm"])
    yield {"u": h.criar_usuario("n-fila", "autorizador"), **e}
    _esvaziar_fila_email()


def _reservar(limite: int = 20) -> list[dict]:
    return _chamar("select public.reservar_emails_notificacao(%s)", (limite,))


def _estado(nid: uuid.UUID) -> tuple:
    with h.conectar() as c:
        return c.execute("select email_status, email_tentativas, email_erro_codigo, email_enviado_em "
                         "from public.notificacoes where id = %s", (nid,)).fetchone()  # type: ignore[return-value]


def test_reservar_move_para_enviando_com_formato_e_limite(fila: dict) -> None:
    ids = [_inserir(fila["u"], tipo="sistema_falha_envio", codigo="c1", email="pendente") for _ in range(5)]
    r = _reservar(3)
    assert len(r) == 3 and set(r[0]) == {"id", "usuario_id", "tipo", "requisicao_id", "referencia_id", "codigo"}
    assert {x["usuario_id"] for x in r} == {str(fila["u"])} and {x["tipo"] for x in r} == {"sistema_falha_envio"}
    for x in r:
        assert _estado(uuid.UUID(x["id"]))[:2] == ("enviando", 1)
    assert len(_reservar(10)) == 2
    assert _reservar(10) == []
    del ids


def test_reservar_com_skip_locked_nao_bloqueia_nem_repete(fila: dict) -> None:
    for _ in range(6):
        _inserir(fila["u"], tipo="sistema_falha_envio", codigo="c2", email="pendente")
    a = psycopg.connect(h.DSN)
    b = psycopg.connect(h.DSN)
    try:
        a.execute("set lock_timeout = '5s'")
        b.execute("set lock_timeout = '5s'")
        ra = a.execute("select public.reservar_emails_notificacao(3)").fetchone()[0]  # type: ignore[index]
        rb = b.execute("select public.reservar_emails_notificacao(10)").fetchone()[0]  # type: ignore[index]
        ida, idb = {x["id"] for x in ra}, {x["id"] for x in rb}
        assert len(ida) == 3 and len(idb) == 3 and not (ida & idb)
        a.commit()
        b.commit()
    finally:
        a.close()
        b.close()


def test_reservar_concorrente_em_threads_nao_duplica(fila: dict) -> None:
    for _ in range(8):
        _inserir(fila["u"], tipo="sistema_falha_envio", codigo="c3", email="pendente")
    saidas: list[set[str]] = []
    barreira = threading.Barrier(4, timeout=60)

    def worker() -> None:
        with psycopg.connect(h.DSN, autocommit=True) as c:
            barreira.wait()
            saidas.append({x["id"] for x in c.execute(
                "select public.reservar_emails_notificacao(5)").fetchone()[0]})  # type: ignore[index]

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(90)
    todos = [i for s in saidas for i in s]
    assert len(todos) == len(set(todos)) == 8


def test_marcar_email_enviado_falhou_ignorado_e_validacoes(fila: dict) -> None:
    n1 = _inserir(fila["u"], tipo="sistema_falha_envio", codigo="c4", email="pendente")
    _reservar(1)
    assert _chamar("select public.marcar_email_notificacao(%s, 'enviado')", (n1,)) == {
        "id": str(n1), "email_status": "enviado", "email_tentativas": 1}
    st = _estado(n1)
    assert st[0] == "enviado" and st[3] is not None
    with h.conectar() as c, pytest.raises(psycopg.Error) as exc:
        c.execute("select public.marcar_email_notificacao(%s, 'falhou')", (n1,))
    assert h.sqlstate(exc) == "AG001"  # enviado e terminal

    n2 = _inserir(fila["u"], tipo="sistema_falha_envio", codigo="c4", email="pendente")
    _reservar(1)
    _chamar("select public.marcar_email_notificacao(%s, 'falhou', 'smtp_timeout')", (n2,))
    assert _estado(n2)[:3] == ("falhou", 1, "smtp_timeout")
    _chamar("select public.marcar_email_notificacao(%s, 'ignorado')", (n2,))
    assert _estado(n2)[0] == "ignorado"

    n3 = _inserir(fila["u"], tipo="sistema_falha_envio", codigo="c4", email="pendente")
    for status, cod, esperado in (("pendente", None, "AG030"), ("enviando", None, "AG030"),
                                  ("falhou", "tem espaco", "AG030"), ("falhou", "@x", "AG030"),
                                  ("enviado", None, "AG001")):  # pendente -> enviado e transicao invalida
        with h.conectar() as c, pytest.raises(psycopg.Error) as exc:
            c.execute("select public.marcar_email_notificacao(%s, %s, %s)", (n3, status, cod))
        assert h.sqlstate(exc) == esperado, (status, cod)
    sem_email = _inserir(fila["u"])
    for alvo in (uuid.uuid4(), sem_email):
        with h.conectar() as c, pytest.raises(psycopg.Error) as exc:
            c.execute("select public.marcar_email_notificacao(%s, 'enviado')", (alvo,))
        assert h.sqlstate(exc) == "AG029"


def test_falhou_so_reenvia_apos_espera_e_ate_3_tentativas(fila: dict) -> None:
    n = _inserir(fila["u"], tipo="sistema_falha_envio", codigo="c5", email="pendente")
    for tentativa in (1, 2, 3):
        r = _reservar(5)
        assert [x["id"] for x in r] == [str(n)], tentativa
        assert _estado(n)[1] == tentativa
        _chamar("select public.marcar_email_notificacao(%s, 'falhou', 'smtp')", (n,))
        assert _reservar(5) == []  # espera de 2 min depois da ultima reserva
        with h.conectar() as c:
            c.execute("update public.notificacoes set email_reservado_em = now() - interval '10 minutes' "
                      "where id = %s", (n,))
    assert _reservar(5) == []  # 3 tentativas: fica falhou
    assert _estado(n)[:2] == ("falhou", 3)


def test_enviando_travado_vira_timeout_e_e_reservado_de_novo(fila: dict) -> None:
    n = _inserir(fila["u"], tipo="sistema_falha_envio", codigo="c6", email="pendente")
    assert len(_reservar(5)) == 1
    assert _reservar(5) == []  # ainda recente
    with h.conectar() as c:
        c.execute("update public.notificacoes set email_reservado_em = now() - interval '20 minutes' "
                  "where id = %s", (n,))
    # falhou/timeout e, sem esperar, elegivel: a espera de 2 min ja passou (reserva antiga)
    r = _reservar(5)
    assert [x["id"] for x in r] == [str(n)] and _estado(n)[1] == 2


def test_reservar_ignora_inelegiveis(fila: dict) -> None:
    inativo = h.criar_usuario("n-fila-off", "autorizador", ativo=False)
    velho = _inserir(fila["u"], tipo="sistema_falha_envio", codigo="c7", email="pendente", dias=1.1)
    off = _inserir(inativo, tipo="sistema_falha_envio", codigo="c7", email="pendente")
    ok = _inserir(fila["u"], tipo="sistema_falha_envio", codigo="c7", email="pendente")
    r = _reservar(10)
    assert [x["id"] for x in r] == [str(ok)]
    assert _estado(velho)[:3] == ("ignorado", 0, "inelegivel")
    assert _estado(off)[:3] == ("ignorado", 0, "inelegivel")

    # flag desligada depois: o que estiver pendente e ignorado
    pend = _inserir(fila["u"], tipo="sistema_falha_envio", codigo="c7", email="pendente")
    _flag("notificacao_email_criticos", False, fila["adm"])
    assert _reservar(10) == []
    assert _estado(pend)[0] == "ignorado"


def test_limite_de_reserva_e_ajustado() -> None:
    assert isinstance(_reservar(0), list) and isinstance(_reservar(100000), list)


# --- expurgo ----------------------------------------------------------------------------------------
def test_expurgo_respeita_piso_e_so_remove_antigas() -> None:
    u = h.criar_usuario("n-purga")
    velha, media, nova = _inserir(u, dias=100), _inserir(u, dias=40), _inserir(u, dias=10)
    for p in (29, 0, -1, 3651, None):
        with h.conectar() as c, pytest.raises(psycopg.Error) as exc:
            c.execute("select public.purgar_notificacoes(%s)", (p,))
        assert h.sqlstate(exc) == "AG030", p

    def existe(nid: uuid.UUID) -> bool:
        with h.conectar() as c:
            return c.execute("select count(*) from public.notificacoes where id = %s", (nid,)).fetchone() == (1,)

    assert _chamar("select public.purgar_notificacoes(%s)", (90,)) >= 1
    assert not existe(velha) and existe(media) and existe(nova)
    assert _chamar("select public.purgar_notificacoes(%s)", (30,)) >= 1
    assert not existe(media) and existe(nova)
    assert _chamar("select public.purgar_notificacoes()") >= 0  # padrao 90


def test_delete_fora_do_expurgo_continua_proibido_mesmo_com_variavel_forjada_em_linha_nova() -> None:
    u = h.criar_usuario("n-forja")
    nova = _inserir(u, dias=10)
    with h.conectar() as c:
        with c.transaction():
            c.execute("select set_config('sisveiculos.expurgo_notificacoes', '1', true)")
            with pytest.raises(psycopg.Error) as exc:
                c.execute("delete from public.notificacoes where id = %s", (nova,))
    assert h.sqlstate(exc) == "AG003"  # piso de 30 dias no proprio trigger


# --- configuracoes_validar --------------------------------------------------------------------------
def _recursos_orig() -> dict[str, bool]:
    with h.conectar() as c:
        cols = ("quilometragem", "abastecimento_manutencao", "checklist", "mfa", "relatorio_email",
                "relatorio_whatsapp", "notificacao_email_criticos")
        row = c.execute(f"select {', '.join(cols)} from public.configuracoes").fetchone()
    assert row is not None
    return dict(zip(cols, map(bool, row), strict=True))


def test_configuracoes_audita_a_nova_chave_e_as_antigas(e: dict) -> None:
    adm = e["adm"]
    orig = _recursos_orig()
    if orig["abastecimento_manutencao"]:
        _flag("abastecimento_manutencao", False, adm)
    alvos = ["checklist", "mfa", "relatorio_email", "relatorio_whatsapp", "quilometragem",
             "notificacao_email_criticos"]

    def audit_count(chave: str) -> int:
        with h.conectar() as c:
            return c.execute("select count(*) from public.auditoria where acao='configuracao_alterada' "
                             "and detalhes->>'recurso' = %s and ator_id = %s", (chave, adm)).fetchone()[0]  # type: ignore[index]

    try:
        for chave in alvos:
            antes = audit_count(chave)
            novo = not _recursos_orig()[chave]
            _flag(chave, novo, adm)
            assert audit_count(chave) == antes + 1, chave
            with h.conectar() as c:
                de, para = c.execute(
                    "select de, para from public.auditoria where acao='configuracao_alterada' "
                    "and detalhes->>'recurso' = %s order by id desc limit 1", (chave,)).fetchone()  # type: ignore[misc]
            assert (de, para) == (str(not novo).lower(), str(novo).lower()), chave
            _flag(chave, not novo, adm)
        # abastecimento exige quilometragem (AG010) e tambem e auditado
        _flag("quilometragem", True, adm)
        antes = audit_count("abastecimento_manutencao")
        _flag("abastecimento_manutencao", True, adm)
        assert audit_count("abastecimento_manutencao") == antes + 1
        _flag("abastecimento_manutencao", False, adm)
        _flag("quilometragem", orig["quilometragem"], adm)
        # sem mudanca: sem auditoria
        antes = audit_count("notificacao_email_criticos")
        _flag("notificacao_email_criticos", _recursos_orig()["notificacao_email_criticos"], adm)
        assert audit_count("notificacao_email_criticos") == antes
    finally:
        for chave in ("abastecimento_manutencao", "quilometragem", "checklist", "mfa", "relatorio_email",
                      "relatorio_whatsapp", "notificacao_email_criticos"):
            if _recursos_orig()[chave] != orig[chave]:
                _flag(chave, orig[chave], adm)


def test_nova_chave_so_admin_ativo_altera(e: dict) -> None:
    for quem in (e["aut"], e["sol"], None):
        with pytest.raises(psycopg.Error) as exc:
            _flag("notificacao_email_criticos", True, quem)  # type: ignore[arg-type]
        assert h.sqlstate(exc) == "AG002"
    with h.conectar() as c:
        assert c.execute("select column_default, is_nullable from information_schema.columns "
                         "where table_schema='public' and table_name='configuracoes' "
                         "and column_name='notificacao_email_criticos'").fetchone() == ("false", "NO")


# --- sem PII, lista fechada, idempotencia -----------------------------------------------------------
def test_sem_pii_em_notificacoes_nem_nas_linhas_de_auditoria_do_gatilho(e: dict) -> None:
    rid = _nova_req(e["sol"])
    h.autorizar(rid, e["aut"])
    with h.conectar() as c:
        (n,) = c.execute("select count(*) from public.notificacoes where position('@' in to_jsonb(notificacoes)::text) > 0"
                         ).fetchone() or (-1,)
        assert n == 0
        cols = {r[0] for r in c.execute("select column_name from information_schema.columns "
                                        "where table_schema='public' and table_name='notificacoes'").fetchall()}
    assert not {"mensagem", "texto", "titulo", "email", "nome", "payload", "detalhes"} & cols
    with h.conectar() as c:
        tipos = {r[0] for r in c.execute("select distinct tipo from public.notificacoes").fetchall()}
    assert tipos <= TIPOS


def test_migration_e_idempotente_e_mantem_o_gatilho_unico(e: dict) -> None:
    with h.conectar() as c:
        (antes,) = c.execute("select count(*) from public.notificacoes").fetchone() or (0,)
        c.execute(MIGRATION)
        c.execute(MIGRATION)
        (depois,) = c.execute("select count(*) from public.notificacoes").fetchone() or (0,)
        (gatilhos,) = c.execute(
            "select count(*) from pg_trigger where tgrelid = 'public.auditoria'::regclass "
            "and tgname = 'trg_auditoria_notificar'").fetchone() or (0,)
        (indices,) = c.execute(
            "select count(*) from pg_indexes where schemaname='public' and tablename='notificacoes'"
        ).fetchone() or (0,)
    assert depois >= antes and gatilhos == 1 and indices == 6  # pkey + 4 indices + dedupe
    rid = _nova_req(e["sol"])  # continua funcionando depois de reaplicar
    assert _dest(rid, "requisicao_nova") == _staff_ativo()


# --- regressao: auditoria ganha so as linhas esperadas -----------------------------------------------
def test_auditoria_de_requisicao_continua_com_uma_linha_por_evento(e: dict) -> None:
    rid = _nova_req(e["sol"])
    h.autorizar(rid, e["aut"])
    linhas = h.auditoria_de("requisicoes", rid)
    assert [(a, de, para) for a, _, de, para in linhas] == [("criada", None, "solicitada"),
                                                            ("transicao", "solicitada", "autorizada")]
