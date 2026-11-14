"""Testes da migration 20261120120000 (Web Push: flag, push_inscricoes, fila push em notificacoes).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). Os testes gravam e nao apagam (notificacoes sao
imutaveis); cada teste cria os proprios usuarios. A fixture autouse desliga a flag e esvazia a fila de push
antes e depois. Rodar sem paralelismo.

    pytest supabase/tests/test_web_push.py -m db_local -p no:xdist
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261120120000_web_push.sql"
).read_text(encoding="utf-8")

RPCS = (
    "public.reservar_push_notificacao(integer)",
    "public.marcar_push_notificacao(uuid, text, text)",
    "public.registrar_inscricao_push(uuid, text, text, text)",
    "public.revogar_inscricao_push(uuid, text)",
    "public.listar_inscricoes_push(uuid)",
    "public.marcar_resultado_inscricao_push(uuid, boolean, boolean)",
    "public.purgar_inscricoes_push()",
)
P256 = "B" * 87  # base64url plausivel (65 bytes => 87 chars)
AUTH = "a" * 22


# --- infraestrutura -----------------------------------------------------------------------------
def _flag(valor: bool, por: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute("update public.configuracoes set notificacao_push = %s, alterado_por = %s", (valor, por))


def _flag_atual() -> bool:
    with h.conectar() as c:
        return bool(c.execute("select notificacao_push from public.configuracoes").fetchone()[0])  # type: ignore[index]


def _esvaziar_fila() -> None:
    with h.conectar() as c:
        c.execute("update public.notificacoes set push_status = 'ignorado' "
                  "where push_status in ('pendente','falhou','enviando')")


@pytest.fixture(autouse=True)
def _isolado() -> Iterator[None]:
    por = h.admin_ativo_qualquer() or h.criar_usuario("wp-admin-cfg", "admin")
    orig = _flag_atual()
    if orig:
        _flag(False, por)
    _esvaziar_fila()
    try:
        yield
    finally:
        _esvaziar_fila()
        if _flag_atual() != orig:
            _flag(orig, por)


@pytest.fixture
def adm() -> uuid.UUID:
    return h.admin_ativo_qualquer() or h.criar_usuario("wp-admin", "admin")


def _chamar(sql: str, params: tuple = ()):
    with h.conectar() as c:
        return c.execute(sql, params).fetchone()[0]  # type: ignore[index]


def _erro(sql: str, params: tuple = ()) -> str | None:
    with h.conectar() as c, pytest.raises(psycopg.Error) as exc:
        c.execute(sql, params)
    return h.sqlstate(exc)


def _ep() -> str:
    return f"https://fcm.googleapis.com/fcm/send/{uuid.uuid4().hex}"


def _inscrever(usuario: uuid.UUID, endpoint: str | None = None) -> dict:
    return _chamar("select public.registrar_inscricao_push(%s, %s, %s, %s)",
                   (usuario, endpoint or _ep(), P256, AUTH))


def _insc(iid: str | uuid.UUID) -> tuple:
    """(usuario_id, falhas, revogada_em is not null, ultimo_sucesso_em is not null)"""
    with h.conectar() as c:
        return c.execute("select usuario_id, falhas, revogada_em is not null, ultimo_sucesso_em is not null "
                         "from public.push_inscricoes where id = %s", (iid,)).fetchone()  # type: ignore[return-value]


def _inserir(usuario: uuid.UUID, *, push: str = "pendente", dias: float = 0, tentativas: int = 0,
             critica: bool = False, tipo: str = "configuracao_alterada") -> uuid.UUID:
    nid = uuid.uuid4()
    with h.conectar() as c:
        c.execute(
            "insert into public.notificacoes (id, usuario_id, tipo, critica, criada_em, push_status, "
            "push_tentativas) values (%s, %s, %s, %s, now() - make_interval(secs => %s), %s, %s)",
            (nid, usuario, tipo, critica, dias * 86400, push, tentativas))
    return nid


def _estado(nid: uuid.UUID) -> tuple:
    with h.conectar() as c:
        return c.execute("select push_status, push_tentativas, push_erro_codigo, push_enviado_em "
                         "from public.notificacoes where id = %s", (nid,)).fetchone()  # type: ignore[return-value]


def _reservar(limite: int = 20) -> list[dict]:
    return _chamar("select public.reservar_push_notificacao(%s)", (limite,))


@pytest.fixture
def fila(adm: uuid.UUID) -> dict:
    _flag(True, adm)
    return {"adm": adm, "u": h.criar_usuario("wp-fila", "autorizador")}


# --- flag e auditoria ----------------------------------------------------------------------------
def test_flag_existe_desligada_por_padrao_e_e_auditada(adm: uuid.UUID) -> None:
    with h.conectar() as c:
        assert c.execute(
            "select data_type, is_nullable, column_default from information_schema.columns "
            "where table_name = 'configuracoes' and column_name = 'notificacao_push'"
        ).fetchone() == ("boolean", "NO", "false")
        antes = c.execute("select count(*) from public.auditoria where acao = 'configuracao_alterada' "
                          "and detalhes ->> 'recurso' = 'notificacao_push'").fetchone()[0]  # type: ignore[index]
    _flag(True, adm)
    _flag(False, adm)
    with h.conectar() as c:
        linhas = c.execute(
            "select de, para, ator_id from public.auditoria where acao = 'configuracao_alterada' "
            "and detalhes ->> 'recurso' = 'notificacao_push' order by criada_em desc, id desc limit 2"
        ).fetchall()
        total = c.execute("select count(*) from public.auditoria where acao = 'configuracao_alterada' "
                          "and detalhes ->> 'recurso' = 'notificacao_push'").fetchone()[0]  # type: ignore[index]
    assert total == antes + 2
    assert {(r[0], r[1]) for r in linhas} == {("false", "true"), ("true", "false")}
    assert all(r[2] == adm for r in linhas)


def test_flag_so_admin_ativo_altera() -> None:
    sol = h.criar_usuario("wp-sol")
    assert _erro("update public.configuracoes set notificacao_push = true, alterado_por = %s", (sol,)) == "AG002"
    assert _erro("update public.configuracoes set notificacao_push = true") == "AG002"
    assert _flag_atual() is False


def test_flags_antigas_continuam_auditadas(adm: uuid.UUID) -> None:
    with h.conectar() as c:
        v = c.execute("select checklist from public.configuracoes").fetchone()[0]  # type: ignore[index]
        c.execute("update public.configuracoes set checklist = %s, alterado_por = %s", (not v, adm))
        n = c.execute("select count(*) from public.auditoria where acao = 'configuracao_alterada' "
                      "and detalhes ->> 'recurso' = 'checklist' and criada_em > now() - interval '1 minute'"
                      ).fetchone()[0]  # type: ignore[index]
        c.execute("update public.configuracoes set checklist = %s, alterado_por = %s", (v, adm))
    assert n >= 1


# --- permissoes e RLS ----------------------------------------------------------------------------
@pytest.mark.parametrize("papel", ["anon", "authenticated", "service_role"])
def test_tabela_push_inscricoes_sem_acesso_direto(papel: str) -> None:
    u = h.criar_usuario("wp-rls")
    with h.conectar() as c:
        assert c.execute("select relrowsecurity from pg_class where oid = 'public.push_inscricoes'::regclass"
                         ).fetchone() == (True,)
        assert c.execute("select count(*) from pg_policy where polrelid = 'public.push_inscricoes'::regclass"
                         ).fetchone() == (0,)
        for p in ("select", "insert", "update", "delete", "truncate"):
            assert c.execute("select has_table_privilege(%s, 'public.push_inscricoes', %s)",
                             (papel, p)).fetchone() == (False,), (papel, p)
    with h.como(papel, u) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("select * from public.push_inscricoes")


@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_rpcs_negadas_a_anon_e_authenticated(papel: str) -> None:
    with h.conectar() as c:
        for rpc in RPCS:
            assert c.execute("select has_function_privilege(%s, %s::regprocedure, 'execute')",
                             (papel, rpc)).fetchone() == (False,), rpc


def test_service_role_executa_as_rpcs_e_so_elas() -> None:
    with h.conectar() as c:
        for rpc in RPCS:
            assert c.execute("select has_function_privilege('service_role', %s::regprocedure, 'execute')",
                             (rpc,)).fetchone() == (True,), rpc
        # nao ve push_* direto na tabela de inscricoes, e so le notificacoes (como antes)
        assert c.execute("select has_table_privilege('service_role', 'public.notificacoes', 'update')"
                         ).fetchone() == (False,)
    with h.como("service_role") as c:
        assert c.execute("select public.listar_inscricoes_push(%s)", (h.criar_usuario("wp-sr"),)
                         ).fetchone() == ([],)


def test_funcoes_definer_com_search_path_vazio_e_triggers_sempre_ativos() -> None:
    with h.conectar() as c:
        rows = c.execute(
            "select p.proname, p.prosecdef, p.proconfig from pg_proc p where p.pronamespace = 'public'::regnamespace "
            "and p.proname in ('reservar_push_notificacao','marcar_push_notificacao','registrar_inscricao_push',"
            "'revogar_inscricao_push','listar_inscricoes_push','marcar_resultado_inscricao_push',"
            "'purgar_inscricoes_push','push_inscricoes_proteger')").fetchall()
        trg = c.execute("select tgname, tgenabled from pg_trigger where not tgisinternal and tgname in "
                        "('trg_push_inscricoes_proteger','trg_push_inscricoes_sem_truncate',"
                        "'trg_notificacoes_proteger')").fetchall()
    assert len(rows) == 8
    for nome, secdef, cfg in rows:
        assert secdef is True, nome
        assert cfg and any(x.replace(" ", "") in ("search_path=", 'search_path=""') for x in cfg), nome
    assert {(r[0], r[1]) for r in trg} == {("trg_push_inscricoes_proteger", "A"),
                                           ("trg_push_inscricoes_sem_truncate", "A"),
                                           ("trg_notificacoes_proteger", "A")}


# --- inscricoes ----------------------------------------------------------------------------------
def test_registrar_cria_lista_e_devolve_endpoint_so_ao_service_role() -> None:
    u = h.criar_usuario("wp-reg")
    ep = _ep()
    r = _inscrever(u, ep)
    assert r["reatribuida"] is False and uuid.UUID(r["id"])
    lista = _chamar("select public.listar_inscricoes_push(%s)", (u,))
    assert lista == [{"id": r["id"], "endpoint": ep, "p256dh": P256, "auth": AUTH}]
    with h.conectar() as c:
        h_ = c.execute("select endpoint_hash from public.push_inscricoes where id = %s", (r["id"],)).fetchone()[0]  # type: ignore[index]
        assert h_ == c.execute("select encode(sha256(convert_to(%s, 'UTF8')), 'hex')", (ep,)).fetchone()[0]  # type: ignore[index]


@pytest.mark.parametrize("ep", ["http://push.example/x", "ftp://x/y", "https://", "https://a b", "", "javascript:1",
                                "https://x/" + "a" * 2100])
def test_endpoint_invalido(ep: str) -> None:
    u = h.criar_usuario("wp-ep")
    assert _erro("select public.registrar_inscricao_push(%s, %s, %s, %s)", (u, ep, P256, AUTH)) == "AG042"


@pytest.mark.parametrize("p256dh,auth", [("curta", AUTH), (P256, "x"), ("com espaco" * 3, AUTH), (P256, "a b c d e f g h"),
                                         (None, AUTH), (P256, None)])
def test_chaves_invalidas(p256dh: str | None, auth: str | None) -> None:
    u = h.criar_usuario("wp-key")
    assert _erro("select public.registrar_inscricao_push(%s, %s, %s, %s)", (u, _ep(), p256dh, auth)) == "AG042"
    assert _erro("select public.registrar_inscricao_push(%s, %s, %s, %s)", (None, _ep(), P256, AUTH)) == "AG042"


def test_so_perfil_ativo_se_inscreve() -> None:
    off = h.criar_usuario("wp-off", ativo=False)
    assert _erro("select public.registrar_inscricao_push(%s, %s, %s, %s)", (off, _ep(), P256, AUTH)) == "AG044"
    assert _erro("select public.registrar_inscricao_push(%s, %s, %s, %s)", (uuid.uuid4(), _ep(), P256, AUTH)) == "AG044"


def test_reinscrever_reatribui_o_endpoint_ao_novo_usuario_e_zera_falhas() -> None:
    a, b = h.criar_usuario("wp-a"), h.criar_usuario("wp-b")
    ep = _ep()
    ia = _inscrever(a, ep)["id"]
    _chamar("select public.marcar_resultado_inscricao_push(%s, false, false)", (ia,))
    r = _inscrever(b, ep)
    assert r == {"id": ia, "reatribuida": True}
    assert _insc(ia)[:3] == (b, 0, False)
    assert _chamar("select public.listar_inscricoes_push(%s)", (a,)) == []
    assert len(_chamar("select public.listar_inscricoes_push(%s)", (b,))) == 1
    with h.conectar() as c:
        assert c.execute("select count(*) from public.push_inscricoes where endpoint_hash = "
                         "encode(sha256(convert_to(%s, 'UTF8')), 'hex')", (ep,)).fetchone() == (1,)


def test_reinscrever_reativa_a_revogada_do_mesmo_usuario() -> None:
    u = h.criar_usuario("wp-reativa")
    ep = _ep()
    iid = _inscrever(u, ep)["id"]
    assert _chamar("select public.revogar_inscricao_push(%s, %s)", (u, ep)) == {"revogadas": 1}
    assert _chamar("select public.listar_inscricoes_push(%s)", (u,)) == []
    assert _inscrever(u, ep)["id"] == iid
    assert _insc(iid)[2] is False


def test_limite_de_10_ativas_por_usuario_e_revogada_libera_vaga() -> None:
    u = h.criar_usuario("wp-lim")
    eps = [_ep() for _ in range(10)]
    for ep in eps:
        _inscrever(u, ep)
    assert _erro("select public.registrar_inscricao_push(%s, %s, %s, %s)", (u, _ep(), P256, AUTH)) == "AG043"
    # reinscrever uma que ja e minha (ativa) nao conta como a 11a
    assert _inscrever(u, eps[0])["reatribuida"] is True
    # outro usuario nao e afetado
    assert _inscrever(h.criar_usuario("wp-lim2"))["reatribuida"] is False
    _chamar("select public.revogar_inscricao_push(%s, %s)", (u, eps[1]))
    _inscrever(u)  # vaga liberada
    # reatribuir de outro usuario para quem esta cheio tambem e recusado
    outro_ep = _ep()
    _inscrever(h.criar_usuario("wp-lim3"), outro_ep)
    assert _erro("select public.registrar_inscricao_push(%s, %s, %s, %s)", (u, outro_ep, P256, AUTH)) == "AG043"


def test_limite_resiste_a_concorrencia() -> None:
    u = h.criar_usuario("wp-conc")
    for _ in range(8):
        _inscrever(u)
    resultados: list[str] = []
    barreira = threading.Barrier(5, timeout=60)

    def worker() -> None:
        with psycopg.connect(h.DSN, autocommit=True) as c:
            barreira.wait()
            try:
                c.execute("select public.registrar_inscricao_push(%s, %s, %s, %s)", (u, _ep(), P256, AUTH))
                resultados.append("ok")
            except psycopg.Error as exc:
                resultados.append(exc.sqlstate or "?")

    ts = [threading.Thread(target=worker) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(90)
    assert resultados.count("ok") == 2 and resultados.count("AG043") == 3, resultados
    assert len(_chamar("select public.listar_inscricoes_push(%s)", (u,))) == 10


def test_mesmo_endpoint_concorrente_gera_uma_linha() -> None:
    a, b = h.criar_usuario("wp-ca"), h.criar_usuario("wp-cb")
    ep = _ep()
    barreira = threading.Barrier(4, timeout=60)
    erros: list[str] = []

    def worker(uid: uuid.UUID) -> None:
        with psycopg.connect(h.DSN, autocommit=True) as c:
            barreira.wait()
            try:
                c.execute("select public.registrar_inscricao_push(%s, %s, %s, %s)", (uid, ep, P256, AUTH))
            except psycopg.Error as exc:
                erros.append(exc.sqlstate or "?")

    ts = [threading.Thread(target=worker, args=(x,)) for x in (a, b, a, b)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(90)
    assert not erros, erros
    with h.conectar() as c:
        assert c.execute("select count(*) from public.push_inscricoes where endpoint = %s", (ep,)).fetchone() == (1,)


def test_revogar_so_a_propria_e_idempotente() -> None:
    dono, outro = h.criar_usuario("wp-dono"), h.criar_usuario("wp-outro")
    ep = _ep()
    iid = _inscrever(dono, ep)["id"]
    assert _chamar("select public.revogar_inscricao_push(%s, %s)", (outro, ep)) == {"revogadas": 0}  # IDOR
    assert _insc(iid)[2] is False
    assert _chamar("select public.revogar_inscricao_push(%s, %s)", (dono, ep)) == {"revogadas": 1}
    assert _chamar("select public.revogar_inscricao_push(%s, %s)", (dono, ep)) == {"revogadas": 0}
    assert _chamar("select public.revogar_inscricao_push(%s, %s)", (dono, _ep())) == {"revogadas": 0}


def test_listar_so_ativas_de_perfil_ativo(adm: uuid.UUID) -> None:
    u = h.criar_usuario("wp-lista")
    _inscrever(u)
    ep2 = _ep()
    _inscrever(u, ep2)
    _chamar("select public.revogar_inscricao_push(%s, %s)", (u, ep2))
    assert len(_chamar("select public.listar_inscricoes_push(%s)", (u,))) == 1
    with h.conectar() as c:  # perfil desativado nao recebe push, mesmo com inscricao ativa
        c.execute("update public.perfis set ativo = false, alterado_por = %s where id = %s", (adm, u))
    assert _chamar("select public.listar_inscricoes_push(%s)", (u,)) == []
    assert _erro("select public.listar_inscricoes_push(%s)", (None,)) == "AG042"


def test_marcar_resultado_ok_falhas_expirada_e_inexistente() -> None:
    u = h.criar_usuario("wp-res")
    i1 = _inscrever(u)["id"]
    assert _chamar("select public.marcar_resultado_inscricao_push(%s, true, false)", (i1,)) == {
        "id": i1, "falhas": 0, "revogada": False}
    assert _insc(i1)[3] is True
    for n in (1, 2, 3, 4):
        assert _chamar("select public.marcar_resultado_inscricao_push(%s, false, false)", (i1,)) == {
            "id": i1, "falhas": n, "revogada": False}
    assert _chamar("select public.marcar_resultado_inscricao_push(%s, false, false)", (i1,)) == {
        "id": i1, "falhas": 5, "revogada": True}  # 5 falhas seguidas revogam
    i2 = _inscrever(u)["id"]
    assert _chamar("select public.marcar_resultado_inscricao_push(%s, false, true)", (i2,)) == {
        "id": i2, "falhas": 1, "revogada": True}  # 404/410 revoga na hora
    assert _chamar("select public.listar_inscricoes_push(%s)", (u,)) == []
    assert _erro("select public.marcar_resultado_inscricao_push(%s, true, false)", (uuid.uuid4(),)) == "AG045"
    assert _erro("select public.marcar_resultado_inscricao_push(%s, null, false)", (i1,)) == "AG042"


def test_identidade_da_inscricao_e_imutavel_e_delete_truncate_proibidos() -> None:
    u = h.criar_usuario("wp-imut")
    iid = _inscrever(u)["id"]
    for sql in ("update public.push_inscricoes set endpoint = 'https://outro.example/x' where id = %s",
                "update public.push_inscricoes set endpoint_hash = repeat('0', 64) where id = %s",
                "update public.push_inscricoes set criada_em = now() - interval '1 day' where id = %s",
                "update public.push_inscricoes set id = gen_random_uuid() where id = %s"):
        assert _erro(sql, (iid,)) == "AG004", sql
    assert _erro("delete from public.push_inscricoes where id = %s", (iid,)) == "AG003"
    assert _erro("truncate public.push_inscricoes") == "AG003"
    with h.conectar() as c, c.transaction():
        c.execute("select set_config('sisveiculos.expurgo_push', '1', true)")
        with pytest.raises(psycopg.Error) as exc:  # variavel forjada nao vale para linha ativa e sem falhas
            c.execute("delete from public.push_inscricoes where id = %s", (iid,))
    assert h.sqlstate(exc) == "AG003"


def test_purgar_remove_so_revogadas_antigas_e_revoga_as_com_5_falhas() -> None:
    u = h.criar_usuario("wp-purga")
    ativa, rev_nova, rev_velha, morta = (uuid.UUID(_inscrever(u)["id"]) for _ in range(4))
    with h.conectar() as c:
        c.execute("update public.push_inscricoes set revogada_em = now() - interval '5 days' where id = %s", (rev_nova,))
        c.execute("update public.push_inscricoes set revogada_em = now() - interval '31 days' where id = %s", (rev_velha,))
        c.execute("update public.push_inscricoes set falhas = 5 where id = %s", (morta,))
    assert _chamar("select public.purgar_inscricoes_push()") >= 1
    with h.conectar() as c:
        vivos = {r[0] for r in c.execute("select id from public.push_inscricoes where id = any(%s)",
                                         ([ativa, rev_nova, rev_velha, morta],)).fetchall()}
    assert vivos == {ativa, rev_nova, morta}  # 5 falhas REVOGA (nao apaga na hora); so revogada > 30 dias e apagada
    assert _insc(morta)[2] is True and _insc(ativa)[2] is False


# --- notificacoes.push_* -------------------------------------------------------------------------
def test_linhas_novas_nascem_pendentes_e_o_gatilho_do_banco_preenche(adm: uuid.UUID) -> None:
    sol = h.criar_usuario("wp-sol-req")
    vid = h.novo_veiculo()
    with h.conectar() as c:
        rid = h.inserir_requisicao(c, sol, vid)
        rows = c.execute("select push_status, push_tentativas, push_enviado_em from public.notificacoes "
                         "where requisicao_id = %s and tipo = 'requisicao_nova'", (rid,)).fetchall()
    assert rows and {r for r in rows} == {("pendente", 0, None)}


def test_linhas_antigas_ja_estao_ignoradas_e_default_e_pendente() -> None:
    with h.conectar() as c:
        # nenhuma linha anterior a migration ficou pendente/enviando por causa dela
        assert c.execute("select count(*) from public.notificacoes where push_status is null").fetchone() == (0,)
        assert c.execute("select column_default from information_schema.columns where table_name = 'notificacoes' "
                         "and column_name = 'push_status'").fetchone()[0].startswith("'pendente'")  # type: ignore[index]
        assert c.execute("select column_default from information_schema.columns where table_name = 'notificacoes' "
                         "and column_name = 'push_tentativas'").fetchone() == ("0",)


def test_reservar_move_para_enviando_com_formato_e_limite(fila: dict) -> None:
    for _ in range(5):
        _inserir(fila["u"], tipo="sistema_falha_envio", critica=True)
    r = _reservar(3)
    assert len(r) == 3
    assert set(r[0]) == {"id", "tipo", "usuario_id", "requisicao_id", "codigo", "critica"}
    assert {x["usuario_id"] for x in r} == {str(fila["u"])} and {x["critica"] for x in r} == {True}
    for x in r:
        assert _estado(uuid.UUID(x["id"]))[:2] == ("enviando", 1)
    assert len(_reservar(10)) == 2
    assert _reservar(10) == []


def test_reservar_so_para_notificacoes_pendentes_nao_reserva_ignorado_nem_enviado(fila: dict) -> None:
    ign = _inserir(fila["u"], push="ignorado")
    ok = _inserir(fila["u"])
    assert [x["id"] for x in _reservar(10)] == [str(ok)]
    assert _estado(ign)[0] == "ignorado"


def test_reservar_com_skip_locked_nao_bloqueia_nem_repete(fila: dict) -> None:
    for _ in range(6):
        _inserir(fila["u"])
    a, b = psycopg.connect(h.DSN), psycopg.connect(h.DSN)
    try:
        a.execute("set lock_timeout = '5s'")
        b.execute("set lock_timeout = '5s'")
        ra = a.execute("select public.reservar_push_notificacao(3)").fetchone()[0]  # type: ignore[index]
        rb = b.execute("select public.reservar_push_notificacao(10)").fetchone()[0]  # type: ignore[index]
        ida, idb = {x["id"] for x in ra}, {x["id"] for x in rb}
        assert len(ida) == 3 and len(idb) == 3 and not (ida & idb)
        a.commit()
        b.commit()
    finally:
        a.close()
        b.close()


def test_reservar_concorrente_em_threads_nao_duplica(fila: dict) -> None:
    for _ in range(8):
        _inserir(fila["u"])
    saidas: list[set[str]] = []
    barreira = threading.Barrier(4, timeout=60)

    def worker() -> None:
        with psycopg.connect(h.DSN, autocommit=True) as c:
            barreira.wait()
            saidas.append({x["id"] for x in c.execute(
                "select public.reservar_push_notificacao(5)").fetchone()[0]})  # type: ignore[index]

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(90)
    todos = [i for s in saidas for i in s]
    assert len(todos) == len(set(todos)) == 8


def test_marcar_enviado_falhou_ignorado_e_validacoes(fila: dict) -> None:
    n1 = _inserir(fila["u"])
    _reservar(1)
    assert _chamar("select public.marcar_push_notificacao(%s, 'enviado')", (n1,)) == {
        "id": str(n1), "push_status": "enviado", "push_tentativas": 1}
    st = _estado(n1)
    assert st[0] == "enviado" and st[3] is not None
    assert _erro("select public.marcar_push_notificacao(%s, 'falhou')", (n1,)) == "AG001"  # enviado e terminal

    n2 = _inserir(fila["u"])
    _reservar(1)
    _chamar("select public.marcar_push_notificacao(%s, 'falhou', 'http_500')", (n2,))
    assert _estado(n2)[:3] == ("falhou", 1, "http_500")
    _chamar("select public.marcar_push_notificacao(%s, 'ignorado', 'sem_inscricao')", (n2,))
    assert _estado(n2)[:3] == ("ignorado", 1, "sem_inscricao")
    assert _erro("select public.marcar_push_notificacao(%s, 'enviado')", (n2,)) == "AG001"  # ignorado e terminal

    n3 = _inserir(fila["u"])
    for status, cod, esperado in (("pendente", None, "AG042"), ("enviando", None, "AG042"), (None, None, "AG042"),
                                  ("falhou", "tem espaco", "AG042"), ("falhou", "@x", "AG042"),
                                  ("enviado", None, "AG001")):  # pendente -> enviado e invalida
        assert _erro("select public.marcar_push_notificacao(%s, %s, %s)", (n3, status, cod)) == esperado, (status, cod)
    assert _erro("select public.marcar_push_notificacao(%s, 'enviado')", (uuid.uuid4(),)) == "AG029"


def test_falhou_so_reenvia_apos_espera_e_ate_3_tentativas(fila: dict) -> None:
    n = _inserir(fila["u"])
    for tentativa in (1, 2, 3):
        assert [x["id"] for x in _reservar(5)] == [str(n)], tentativa
        assert _estado(n)[1] == tentativa
        _chamar("select public.marcar_push_notificacao(%s, 'falhou', 'http_500')", (n,))
        assert _reservar(5) == []  # espera de 2 min
        with h.conectar() as c:
            c.execute("update public.notificacoes set push_reservado_em = now() - interval '10 minutes' "
                      "where id = %s", (n,))
    assert _reservar(5) == []  # 3 tentativas: encerra como ignorado/esgotado (B7)
    assert _estado(n)[:3] == ("ignorado", 3, "esgotado")


def test_enviando_travado_vira_timeout_e_e_reservado_de_novo(fila: dict) -> None:
    n = _inserir(fila["u"])
    assert len(_reservar(5)) == 1
    assert _reservar(5) == []
    with h.conectar() as c:
        c.execute("update public.notificacoes set push_reservado_em = now() - interval '20 minutes' where id = %s", (n,))
    assert [x["id"] for x in _reservar(5)] == [str(n)] and _estado(n)[1] == 2


def test_reservar_ignora_inelegiveis_inativo_e_velha(fila: dict) -> None:
    inativo = h.criar_usuario("wp-fila-off", "autorizador", ativo=False)
    velha = _inserir(fila["u"], dias=1.1)
    off = _inserir(inativo)
    ok = _inserir(fila["u"])
    assert [x["id"] for x in _reservar(10)] == [str(ok)]
    assert _estado(velha)[:3] == ("ignorado", 0, "inelegivel")
    assert _estado(off)[:3] == ("ignorado", 0, "inelegivel")


def test_flag_desligada_marca_ignorado_desligado_e_nao_devolve_nada(fila: dict) -> None:
    pend = _inserir(fila["u"])
    falhou = _inserir(fila["u"], push="falhou", tentativas=1)
    _flag(False, fila["adm"])
    assert _reservar(10) == []
    assert _estado(pend)[:3] == ("ignorado", 0, "desligado")
    assert _estado(falhou)[:3] == ("ignorado", 1, "desligado")
    # religar nao ressuscita o que foi ignorado
    _flag(True, fila["adm"])
    assert _reservar(10) == []


def test_limite_de_reserva_e_ajustado(fila: dict) -> None:
    assert isinstance(_reservar(0), list) and isinstance(_reservar(100000), list)


def test_maquina_de_estados_do_push_no_trigger(fila: dict) -> None:
    n = _inserir(fila["u"])
    # pendente nao vai direto a enviado/falhou
    assert _erro("update public.notificacoes set push_status = 'enviado', push_enviado_em = now() where id = %s", (n,)) == "AG001"
    assert _erro("update public.notificacoes set push_status = 'falhou' where id = %s", (n,)) == "AG001"
    # enviando sem contar a tentativa e recusado
    assert _erro("update public.notificacoes set push_status = 'enviando' where id = %s", (n,)) == "AG001"
    # tentativas nao decrescem
    _reservar(1)
    assert _erro("update public.notificacoes set push_tentativas = 0 where id = %s", (n,)) == "AG001"
    # identidade continua imutavel
    assert _erro("update public.notificacoes set usuario_id = %s where id = %s", (h.criar_usuario("wp-x"), n)) == "AG004"


def test_ler_lida_e_email_nao_interferem_no_push(fila: dict) -> None:
    n = _inserir(fila["u"])
    with h.conectar() as c:
        c.execute("update public.notificacoes set lida_em = now() where id = %s", (n,))
    assert _estado(n)[0] == "pendente"
    assert _chamar("select public.listar_notificacoes(%s)", (fila["u"],))["itens"]  # formato antigo intacto
    assert "push_status" not in _chamar("select public.listar_notificacoes(%s)", (fila["u"],))["itens"][0]


def test_expurgo_de_notificacoes_continua_funcionando() -> None:
    u = h.criar_usuario("wp-expurgo")
    velha = _inserir(u, push="ignorado", dias=100)
    assert _chamar("select public.purgar_notificacoes(90)") >= 1
    with h.conectar() as c:
        assert c.execute("select count(*) from public.notificacoes where id = %s", (velha,)).fetchone() == (0,)


# --- migration -----------------------------------------------------------------------------------
def test_migration_e_idempotente_e_nao_quebra_a_fila_existente(fila: dict) -> None:
    n = _inserir(fila["u"])
    with h.reaplicando_migration() as c:
        c.execute(MIGRATION)
        c.execute(MIGRATION)
        assert c.execute("select push_status from public.notificacoes where id = %s", (n,)).fetchone() == ("pendente",)
    assert _estado(n)[0] == "pendente"


def test_migration_sem_segredos_nem_dml_perigoso() -> None:
    baixo = MIGRATION.lower()
    for proibido in ("vapid", "private key", "drop table", "disable row level security"):
        assert proibido not in baixo, proibido


# --- ajustes da revisao (migration 20261120120100) -------------------------------------------------
AJUSTES = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261120120100_web_push_ajustes.sql"
).read_text(encoding="utf-8")


def _resultado(iid: str | uuid.UUID, ok: bool, expirada: bool | None) -> dict:
    return _chamar("select public.marcar_resultado_inscricao_push(%s, %s, %s)", (iid, ok, expirada))


def test_erro_transitorio_nao_conta_falha_nem_revoga() -> None:
    iid = _inscrever(h.criar_usuario("wp-trans"))["id"]
    for _ in range(10):  # 5xx, 429, timeout, rede: p_expirada nulo
        assert _resultado(iid, False, None) == {"id": iid, "falhas": 0, "revogada": False}
    assert _insc(iid)[1:3] == (0, False)


def test_erro_definitivo_conta_e_na_quinta_falha_revoga_sem_apagar() -> None:
    u = h.criar_usuario("wp-def")
    iid = _inscrever(u)["id"]
    for n in (1, 2, 3, 4):
        assert _resultado(iid, False, False) == {"id": iid, "falhas": n, "revogada": False}
    _resultado(iid, False, None)  # transitorio no meio nao zera nem soma
    assert _resultado(iid, False, False) == {"id": iid, "falhas": 5, "revogada": True}
    assert _chamar("select public.purgar_inscricoes_push()") >= 0
    assert _insc(iid)[2] is True  # continua gravada ate completar 30 dias revogada
    assert _chamar("select public.listar_inscricoes_push(%s)", (u,)) == []


def test_expirada_revoga_na_hora_e_sucesso_zera_falhas() -> None:
    iid = _inscrever(h.criar_usuario("wp-exp"))["id"]
    _resultado(iid, False, False)
    assert _resultado(iid, True, False)["falhas"] == 0
    assert _resultado(iid, False, True) == {"id": iid, "falhas": 1, "revogada": True}


def test_purga_revoga_ativa_com_falhas_antigas_e_nao_apaga_revogada_recente() -> None:
    u = h.criar_usuario("wp-purga2")
    morta, rec, velha = (uuid.UUID(_inscrever(u)["id"]) for _ in range(3))
    with h.conectar() as c:
        c.execute("update public.push_inscricoes set falhas = 7 where id = %s", (morta,))
        c.execute("update public.push_inscricoes set falhas = 9, revogada_em = now() - interval '29 days' "
                  "where id = %s", (rec,))
        c.execute("update public.push_inscricoes set revogada_em = now() - interval '31 days' where id = %s", (velha,))
    _chamar("select public.purgar_inscricoes_push()")
    with h.conectar() as c:
        vivos = {r[0] for r in c.execute("select id from public.push_inscricoes where id = any(%s)",
                                         ([morta, rec, velha],)).fetchall()}
    assert vivos == {morta, rec}
    assert _insc(morta)[2] is True


def test_delete_forjado_nao_apaga_ativa_nem_revogada_recente_mesmo_com_falhas() -> None:
    iid = _inscrever(h.criar_usuario("wp-forja"))["id"]
    with h.conectar() as c:
        c.execute("update public.push_inscricoes set falhas = 9, revogada_em = now() - interval '5 days' "
                  "where id = %s", (iid,))
    with h.conectar() as c, c.transaction():
        c.execute("select set_config('sisveiculos.expurgo_push', '1', true)")
        with pytest.raises(psycopg.Error) as exc:
            c.execute("delete from public.push_inscricoes where id = %s", (iid,))
    assert h.sqlstate(exc) == "AG003"


def test_falhou_esgotada_vira_ignorado_esgotado_e_sai_da_fila(fila: dict) -> None:
    esgotada = _inserir(fila["u"], push="falhou", tentativas=3)
    ainda = _inserir(fila["u"], push="falhou", tentativas=2)
    _reservar(5)
    assert _estado(esgotada)[:3] == ("ignorado", 3, "esgotado")
    assert _estado(ainda)[0] == "enviando"
    _reservar(5)
    assert _estado(esgotada)[:3] == ("ignorado", 3, "esgotado")  # terminal


def test_esgotada_tambem_e_encerrada_com_a_flag_desligada(fila: dict) -> None:
    n = _inserir(fila["u"], push="falhou", tentativas=3)
    _flag(False, fila["adm"])
    assert _reservar(5) == []
    assert _estado(n)[:3] == ("ignorado", 3, "esgotado")


def test_indice_da_fila_exclui_esgotadas() -> None:
    with h.conectar() as c:
        d = c.execute("select indexdef from pg_indexes where schemaname = 'public' "
                      "and indexname = 'ix_notificacoes_push_fila'").fetchone()[0]  # type: ignore[index]
    assert "push_tentativas < 3" in d


def test_ajustes_mantem_rls_grants_e_search_path_e_sao_idempotentes() -> None:
    with h.reaplicando_migration() as c:
        c.execute(MIGRATION)
        c.execute(AJUSTES)
        c.execute(AJUSTES)
    with h.conectar() as c:
        for rpc in RPCS:
            row = c.execute("select has_function_privilege('anon', %s::regprocedure, 'execute'), "
                            "has_function_privilege('authenticated', %s::regprocedure, 'execute'), "
                            "has_function_privilege('service_role', %s::regprocedure, 'execute')",
                            (rpc, rpc, rpc)).fetchone()
            assert row == (False, False, True), rpc


def test_ajustes_sem_segredos_nem_dml_perigoso() -> None:
    baixo = AJUSTES.lower()
    for proibido in ("vapid", "private key", "drop table", "disable row level security", "truncate"):
        assert proibido not in baixo, proibido
