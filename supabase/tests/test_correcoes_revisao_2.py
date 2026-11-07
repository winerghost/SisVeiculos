"""Testes da migration 11: correcoes da segunda revisao de seguranca.

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.
Cobre A1 (admin so por admin), A2 (conta do Auth valida), M1 (criado_em do servidor),
M3 (default privileges), M6 (registrar_auditoria), B2 (um convite aberto por e-mail),
B4 (uso do convite so por consumir_convite), ciclo com service_role (M7),
funcoes privadas negadas, B9 (conta inativa) e auditoria de convite sem dados pessoais.

NAO coberto aqui: bootstrap do primeiro admin (exige banco sem admin ativo; nao e
reproduzivel numa base compartilhada pelos demais testes).

    pytest supabase/tests/test_correcoes_revisao_2.py -m db_local
"""

from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

NEGADO = "42501"


def _consumir(token_hash: str, usuario: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute("select public.consumir_convite(%s, %s, 'Convidado')", (token_hash, usuario))


def _convite_aberto(cid: uuid.UUID) -> bool:
    with h.conectar() as c:
        row = c.execute(
            "select usado_em is null and revogado_em is null from public.convites where id=%s", (cid,)
        ).fetchone()
    return bool(row and row[0])


# --- A1: perfil admin exige admin ativo --------------------------------------------
def test_service_role_nao_cria_admin_sem_ator_havendo_admin_ativo() -> None:
    h.criar_usuario("admin", "admin")  # garante admin ativo
    uid = h.criar_auth(h.email_unico())
    with pytest.raises(psycopg.Error) as exc:
        with h.como("service_role") as c:
            c.execute(
                "insert into public.perfis (id, nome, unidade, role) values (%s, 'X', 'UEA', 'admin')",
                (uid,),
            )
    assert h.sqlstate(exc) == "AG002"


@pytest.mark.parametrize("papel", ["solicitante", "autorizador"])
def test_ator_nao_admin_nao_cria_admin(papel: str) -> None:
    h.criar_usuario("admin", "admin")
    ator = h.criar_usuario("ator", papel)
    uid = h.criar_auth(h.email_unico())
    with pytest.raises(psycopg.Error) as exc:
        with h.como("service_role") as c:
            c.execute(
                "insert into public.perfis (id, nome, unidade, role, alterado_por) "
                "values (%s, 'X', 'UEA', 'admin', %s)",
                (uid, ator),
            )
    assert h.sqlstate(exc) == "AG002"


def test_admin_ativo_cria_admin_como_service_role() -> None:
    admin = h.criar_usuario("admin", "admin")
    uid = h.criar_auth(h.email_unico())
    with h.como("service_role") as c:
        c.execute(
            "insert into public.perfis (id, nome, unidade, role, alterado_por) "
            "values (%s, 'X', 'UEA', 'admin', %s)",
            (uid, admin),
        )
        assert c.execute("select role::text from public.perfis where id=%s", (uid,)).fetchone() == ("admin",)
        # alterado_por nunca persiste
        assert c.execute("select alterado_por from public.perfis where id=%s", (uid,)).fetchone() == (None,)


def test_criar_perfil_nao_admin_continua_sem_ator() -> None:
    uid = h.criar_auth(h.email_unico())
    with h.como("service_role") as c:
        c.execute("insert into public.perfis (id, nome, unidade) values (%s, 'X', 'UEA')", (uid,))


def test_convite_de_admin_cria_admin_com_o_criador_como_responsavel() -> None:
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    _, th = h.novo_convite(admin, email, role="admin")
    uid = h.criar_auth(email)
    _consumir(th, uid)
    assert h.perfil_de(uid) == ("admin", True)


def test_convite_de_admin_falha_se_o_criador_deixou_de_ser_admin() -> None:
    criador = h.criar_usuario("criador", "admin")
    outro = h.criar_usuario("outro", "admin")
    email = h.email_unico()
    cid, th = h.novo_convite(criador, email, role="admin")
    h.alterar_perfil(criador, outro, role="autorizador")
    uid = h.criar_auth(email)
    with pytest.raises(psycopg.Error) as exc:
        _consumir(th, uid)
    assert h.sqlstate(exc) == "AG002"
    assert _convite_aberto(cid)  # atomico: nada foi gravado
    assert h.auditoria_de("perfis", uid) == []


# --- A2: conta do Auth precisa estar valida -------------------------------------------
@pytest.mark.parametrize(
    "opcoes",
    [
        {"confirmado": False},
        {"apagado": True},
        {"banido_ate": "now() + interval '1 day'"},
    ],
)
def test_conta_auth_invalida_nao_consome_convite(opcoes: dict) -> None:
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    cid, th = h.novo_convite(admin, email)
    uid = h.criar_auth(email, **opcoes)
    with pytest.raises(psycopg.Error) as exc:
        _consumir(th, uid)
    assert h.sqlstate(exc) == "AG007"
    assert _convite_aberto(cid)
    assert h.auditoria_de("perfis", uid) == []


def test_banimento_vencido_nao_impede_o_convite() -> None:
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    _, th = h.novo_convite(admin, email)
    uid = h.criar_auth(email, banido_ate="now() - interval '1 day'")
    _consumir(th, uid)
    assert h.perfil_de(uid) == ("solicitante", True)


# --- M1: criado_em do servidor / prazo efetivo ---------------------------------------
def test_criado_em_futuro_nao_estende_o_prazo_maximo() -> None:
    admin = h.criar_usuario("admin", "admin")
    with pytest.raises(psycopg.errors.CheckViolation):
        with h.conectar() as c:
            c.execute(
                "insert into public.convites (email, unidade, token_hash, criado_por, criado_em, expira_em) "
                "values (%s, 'UEA', %s, %s, now() + interval '10 days', now() + interval '39 days')",
                (h.email_unico(), h.novo_token()[1], admin),
            )


def test_criado_em_enviado_pelo_chamador_e_ignorado() -> None:
    admin = h.criar_usuario("admin", "admin")
    cid = uuid.uuid4()
    with h.conectar() as c:
        c.execute(
            "insert into public.convites (id, email, unidade, token_hash, criado_por, criado_em, expira_em) "
            "values (%s, %s, 'UEA', %s, %s, now() + interval '10 days', now() + interval '12 days')",
            (cid, h.email_unico(), h.novo_token()[1], admin),
        )
        (recente,) = c.execute(
            "select abs(extract(epoch from (clock_timestamp() - criado_em))) < 60 "
            "from public.convites where id=%s",
            (cid,),
        ).fetchone() or (False,)
    assert recente


# --- B2: um convite aberto por e-mail ----------------------------------------------------
def test_segundo_convite_aberto_para_o_mesmo_email_e_recusado() -> None:
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    cid, _ = h.novo_convite(admin, email)
    with pytest.raises(psycopg.errors.UniqueViolation):
        h.novo_convite(admin, email)
    with h.conectar() as c:
        c.execute(
            "update public.convites set revogado_em = now(), revogado_por = %s where id = %s", (admin, cid)
        )
    h.novo_convite(admin, email)  # revogado libera o e-mail


# --- B4: uso do convite so via consumir_convite --------------------------------------------
def test_update_direto_de_uso_e_recusado() -> None:
    admin = h.criar_usuario("admin", "admin")
    cid, _ = h.novo_convite(admin, h.email_unico())
    outro = h.criar_usuario("outro")
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                "update public.convites set usado_em = now(), usado_por = %s where id = %s", (outro, cid)
            )
    assert h.sqlstate(exc) == "AG004"
    assert _convite_aberto(cid)


def test_variavel_de_sessao_de_outro_usuario_nao_vale() -> None:
    admin = h.criar_usuario("admin", "admin")
    cid, _ = h.novo_convite(admin, h.email_unico())
    a, b = h.criar_usuario("a"), h.criar_usuario("b")
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("begin")
            c.execute("select set_config('sisveiculos.convite_em_consumo', %s, true)", (f"{cid}:{a}",))
            c.execute("update public.convites set usado_em = now(), usado_por = %s where id = %s", (b, cid))
    assert h.sqlstate(exc) == "AG004"


def test_consumir_convite_que_falha_no_perfil_mantem_o_convite_aberto() -> None:
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    cid, th = h.novo_convite(admin, email)
    uid = h.criar_auth(email)
    with h.conectar() as c:  # perfil ja existente para a conta => 23505 dentro de consumir_convite
        c.execute("insert into public.perfis (id, nome, unidade) values (%s, 'Ja existe', 'UEA')", (uid,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        _consumir(th, uid)
    assert _convite_aberto(cid)
    assert [a for a, *_ in h.auditoria_de("convites", cid)] == ["convite_criado"]


def test_auditoria_de_convite_nao_guarda_email_nem_hash() -> None:
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    cid, th = h.novo_convite(admin, email)
    uid = h.criar_auth(email)
    _consumir(th, uid)
    with h.conectar() as c:
        rows = c.execute(
            "select acao, de, para, detalhes::text from public.auditoria "
            "where tabela='convites' and registro_id=%s order by id",
            (cid,),
        ).fetchall()
    assert [r[0] for r in rows] == ["convite_criado", "convite_usado"]
    for _, de, para, detalhes in rows:
        blob = f"{de} {para} {detalhes}"
        assert email not in blob and th not in blob and "@" not in blob


# --- M6: registrar_auditoria / CHECKs ----------------------------------------------------------
def test_registrar_auditoria_valida_tamanho_e_ator() -> None:
    admin = h.criar_usuario("admin", "admin")
    with h.conectar() as c:
        c.execute(
            "select public.registrar_auditoria('perfis', null, 'acao_administrativa', %s, %s, %s)",
            (admin, "d" * 64, "p" * 64),
        )
    for de, para in (("d" * 65, None), (None, "p" * 65)):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(
                    "select public.registrar_auditoria('perfis', null, 'acesso_negado', %s, %s, %s)",
                    (admin, de, para),
                )
        assert h.sqlstate(exc) == "22023"
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                "select public.registrar_auditoria('perfis', null, 'acesso_negado', %s)", (uuid.uuid4(),)
            )
    assert h.sqlstate(exc) == "AG002"


@pytest.mark.parametrize("coluna", ["de", "para"])
def test_auditoria_check_de_tamanho_de_de_e_para(coluna: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        with h.conectar() as c:
            c.execute(
                f"insert into public.auditoria (tabela, acao, {coluna}) values ('perfis', 'acesso_negado', %s)",
                ("x" * 65,),
            )


# --- M3: default privileges globais -----------------------------------------------------------
def test_funcao_nova_nasce_sem_execute_para_public() -> None:
    """Presume conexao como postgres (dono das migrations). Tudo desfeito no rollback."""
    with psycopg.connect(h.DSN) as conn:
        try:
            conn.execute("create function privado.tmp_m3() returns int language sql as 'select 1'")
            (anon, auth) = conn.execute(
                "select has_function_privilege('anon', 'privado.tmp_m3()', 'execute'), "
                "has_function_privilege('authenticated', 'privado.tmp_m3()', 'execute')"
            ).fetchone() or (True, True)
            assert not anon and not auth
        finally:
            conn.rollback()


# --- M7: ciclo de convite / guarda / configuracoes como service_role ----------------------------
def test_ciclo_de_convite_como_service_role() -> None:
    admin = h.criar_usuario("admin", "admin")
    email = h.email_unico()
    uid = h.criar_auth(email)
    _, th = h.novo_token()
    cid = uuid.uuid4()
    with h.como("service_role") as c:
        c.execute(
            "insert into public.convites (id, email, role, unidade, token_hash, criado_por) "
            "values (%s, %s, 'autorizador', 'UEA', %s, %s)",
            (cid, email, th, admin),
        )
        c.execute("select public.consumir_convite(%s, %s, 'Convidado')", (th, uid))
        assert c.execute("select role::text from public.perfis where id=%s", (uid,)).fetchone() == (
            "autorizador",
        )
        acoes = c.execute(
            "select acao from public.auditoria where tabela='convites' and registro_id=%s order by id", (cid,)
        ).fetchall()
        assert acoes == [("convite_criado",), ("convite_usado",)]


def test_service_role_nao_cria_convite_sem_admin_ativo() -> None:
    comum = h.criar_usuario("comum", "autorizador")
    with pytest.raises(psycopg.Error) as exc:
        with h.como("service_role") as c:
            c.execute(
                "insert into public.convites (email, unidade, token_hash, criado_por) "
                "values (%s, 'UEA', %s, %s)",
                (h.email_unico(), h.novo_token()[1], comum),
            )
    assert h.sqlstate(exc) == "AG002"


def test_ciclo_da_guarda_de_admin_como_service_role() -> None:
    a = h.criar_usuario("admin-a", "admin")
    alvo = h.criar_usuario("alvo")
    with h.como("service_role") as c:
        c.execute("update public.perfis set role='autorizador', alterado_por=%s where id=%s", (a, alvo))
        assert c.execute("select role::text from public.perfis where id=%s", (alvo,)).fetchone() == (
            "autorizador",
        )
    with pytest.raises(psycopg.Error) as exc:
        with h.como("service_role") as c:
            c.execute("update public.perfis set ativo=false, alterado_por=%s where id=%s", (a, a))
    assert h.sqlstate(exc) == "AG008"
    with pytest.raises(psycopg.Error) as exc:
        with h.como("service_role") as c:
            c.execute("update public.perfis set ativo=false where id=%s", (alvo,))
    assert h.sqlstate(exc) == "AG002"


def test_ciclo_de_configuracoes_como_service_role() -> None:
    admin = h.criar_usuario("admin", "admin")
    comum = h.criar_usuario("comum", "autorizador")
    with h.como("service_role") as c:  # rollback ao fim: nada fica ligado
        c.execute("update public.configuracoes set checklist=true, alterado_por=%s where id=true", (admin,))
        assert c.execute("select checklist from public.configuracoes").fetchone() == (True,)
        (qtd,) = c.execute(
            "select count(*) from public.auditoria where tabela='configuracoes' and ator_id=%s", (admin,)
        ).fetchone() or (0,)
        assert qtd >= 1
    with pytest.raises(psycopg.Error) as exc:
        with h.como("service_role") as c:
            c.execute("update public.configuracoes set checklist=true, alterado_por=%s where id=true", (comum,))
    assert h.sqlstate(exc) == "AG002"


# --- funcoes privadas negadas ---------------------------------------------------------------------
def test_service_role_nao_semeia_admin() -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.como("service_role") as c:
            c.execute("select privado.semear_primeiro_admin(%s)", (h.email_unico(),))
    assert h.sqlstate(exc) == NEGADO


def test_authenticated_nao_chama_exigir_admin_ativo() -> None:
    admin = h.criar_usuario("admin", "admin")
    with pytest.raises(psycopg.Error) as exc:
        with h.como("authenticated", admin) as c:
            c.execute("select privado.exigir_admin_ativo(%s)", (admin,))
    assert h.sqlstate(exc) == NEGADO


def test_service_role_nao_chama_exigir_admin_ativo() -> None:
    admin = h.criar_usuario("admin", "admin")
    with pytest.raises(psycopg.Error) as exc:
        with h.como("service_role") as c:
            c.execute("select privado.exigir_admin_ativo(%s)", (admin,))
    assert h.sqlstate(exc) == NEGADO


# --- B9 (revisto na migration 13): nenhuma conta le direto, ativa ou inativa -----------------------
@pytest.mark.parametrize("role", ["solicitante", "autorizador", "admin"])
@pytest.mark.parametrize("tabela", ["auditoria", "configuracoes", "perfis", "requisicoes", "veiculos"])
def test_conta_inativa_nao_le_nada(role: str, tabela: str) -> None:
    h.criar_usuario("admin", "admin")  # garante dados em auditoria/configuracoes/perfis
    inativo = h.criar_usuario("inativo", role, ativo=False)
    with pytest.raises(psycopg.Error) as exc:
        with h.como("authenticated", inativo) as c:
            c.execute(f"select count(*) from public.{tabela}")
    assert h.sqlstate(exc) == NEGADO
