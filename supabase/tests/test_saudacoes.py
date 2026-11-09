"""Testes da migration 20261115120100 (saudacoes: frases adicionais com aprovacao do admin).

Somente banco local/dev DESCARTAVEL (TEST_DATABASE_URL + TEST_DATABASE_CONFIRM=descartavel + marcador
`comment on database ... is 'sisveiculos:descartavel'`; sem eles os testes sao pulados). Ver dbhelpers.py.
As linhas criadas ficam (sem DELETE fisico e auditoria append-only); textos usam sufixo unico.

    pytest supabase/tests/test_saudacoes.py -m db_local -p no:xdist
"""

from __future__ import annotations

import json
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]


def _admin() -> uuid.UUID:
    return h.admin_ativo_qualquer() or h.criar_usuario("admin-sau", "admin")


def _texto(prefixo: str = "Bom dia") -> str:
    return f"{prefixo} {uuid.uuid4().hex[:8]}"


def _criar(
    texto: str | None = None,
    ocasiao: str = "manha",
    origem: str = "manual",
    por: uuid.UUID | None = None,
) -> uuid.UUID:
    sid = uuid.uuid4()
    with h.conectar() as c:
        c.execute(
            "insert into public.saudacoes (id, ocasiao, texto, origem, criado_por) "
            "values (%s, %s, %s, %s, %s)",
            (sid, ocasiao, texto or _texto(), origem, por or _admin()),
        )
    return sid


def _decidir(sid: uuid.UUID, status: str, por: uuid.UUID | None) -> None:
    with h.conectar() as c:
        c.execute(
            "update public.saudacoes set status = %s, decidido_por = %s where id = %s",
            (status, por, sid),
        )


def _auditoria(sid: uuid.UUID) -> list[tuple]:
    with h.conectar() as c:
        return c.execute(
            "select tabela, acao, ator_id, de, para, detalhes from public.auditoria "
            "where registro_id = %s and detalhes->>'recurso' = 'saudacao' order by id",
            (sid,),
        ).fetchall()


# --- RLS e privilegios -------------------------------------------------------
@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_rls_nega_anon_e_authenticated(papel: str) -> None:
    uid = h.criar_usuario("sol-sau") if papel == "authenticated" else None
    for sql in (
        "select count(*) from public.saudacoes",
        "insert into public.saudacoes (ocasiao, texto, origem) values ('manha', 'Oi tudo bem', 'ia')",
        "update public.saudacoes set status = 'aprovada'",
    ):
        with pytest.raises(psycopg.Error) as exc:
            with h.como(papel, uid) as c:
                c.execute(sql)  # type: ignore[arg-type]
        assert h.sqlstate(exc) == "42501", sql


def test_rls_ligado_e_so_a_policy_de_negacao() -> None:
    # Desde 20261116120000 existe UMA policy explicita (using false) para anon/authenticated (DB-03).
    with h.conectar() as c:
        (rls,) = c.execute("select relrowsecurity from pg_class where oid = 'public.saudacoes'::regclass").fetchone() or (
            False,
        )
        pols = c.execute("select policyname from pg_policies where tablename = 'saudacoes'").fetchall()
    assert rls is True
    assert pols == [("saudacoes_sem_acesso_api",)]


def test_service_role_sem_truncate_e_update_so_nas_colunas_de_decisao() -> None:
    # DELETE (so de rejeitadas) foi liberado pela migration 20261115120400; ver test_saudacoes_exclusao.py.
    with h.conectar() as c:
        (ok,) = c.execute(
            "select has_table_privilege('service_role', 'public.saudacoes', 'truncate')"
        ).fetchone() or (True,)
        assert ok is False
        for col, esperado in (("texto", False), ("ocasiao", False), ("origem", False), ("status", True)):
            (ok,) = c.execute(
                "select has_column_privilege('service_role', 'public.saudacoes', %s, 'update')", (col,)
            ).fetchone() or (None,)
            assert ok is esperado, col


def test_service_role_insere_e_decide_mas_nao_apaga() -> None:
    adm = _admin()
    sid = uuid.uuid4()
    with h.como("service_role") as c:
        c.execute(
            "insert into public.saudacoes (id, ocasiao, texto, origem, criado_por) "
            "values (%s, 'tarde', %s, 'manual', %s)",
            (sid, _texto(), adm),
        )
        c.execute(
            "update public.saudacoes set status = 'aprovada', decidido_por = %s where id = %s", (adm, sid)
        )
    with pytest.raises(psycopg.Error) as exc:
        with h.como("service_role") as c:
            c.execute("truncate public.saudacoes")
    assert h.sqlstate(exc) == "42501"
    with pytest.raises(psycopg.Error) as exc:
        with h.como("service_role") as c:
            c.execute("update public.saudacoes set texto = 'outro texto'")
    assert h.sqlstate(exc) == "42501"


def test_delete_e_truncate_bloqueados_ate_para_dono() -> None:
    sid = _criar()
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("delete from public.saudacoes where id = %s", (sid,))
    assert h.sqlstate(exc) == "AG003"
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("truncate public.saudacoes")
    assert h.sqlstate(exc) == "AG003"


# --- CHECKs ------------------------------------------------------------------
@pytest.mark.parametrize(
    "texto",
    [
        "ab",  # curto
        "x" * 81,  # longo
        "Ola <b>mundo</b>",
        "Veja https://exemplo.invalid",
        "Veja HTTP agora",
        "linha um\nlinha dois",
        "tab\taqui",
        " espaco no inicio",
        "Oi {usuario}",
        "Oi {nome",
        "Oi {{nome}}",
        "Oi }",
        "Veja www.exemplo.invalid",
        "Veja WwW.exemplo",
        "fale com a@b.invalid",
        "Oi\u200bmundo",  # zero-width space
        "Oi\u200emundo",  # LRM
        "Oi\u202emundo",  # RLO (bidi)
        "Oi\u2066mundo",  # LRI (isolate)
        "Oi\u2069mundo",  # PDI
        "Oi\ufeffmundo",  # BOM / ZWNBSP
        "Oi\x85mundo",  # NEL (controle C1)
        "Oi\x9fmundo",  # controle C1
        "Oi\x7fmundo",  # DEL
        "Oi\x01mundo",
        "Oi\u2028mundo",
        "Oi\u2060mundo",
    ],
)
def test_check_texto_invalido(texto: str) -> None:
    with pytest.raises(psycopg.Error) as exc:
        _criar(texto)
    # 23514 = check_violation (o CHECK vem antes do unique, entao o texto fixo nao colide)
    assert h.sqlstate(exc) == "23514"


def test_acentos_e_emoji_continuam_permitidos() -> None:
    _criar(f"Boa tarde, José! ☀️ {uuid.uuid4().hex[:6]}")
    _criar(f"Olá {{nome}}, sextou 🎉 {uuid.uuid4().hex[:6]}")


def test_placeholder_nome_permitido_e_limites_de_tamanho() -> None:
    _criar(f"Ola, {{nome}}! {uuid.uuid4().hex[:6]}")
    _criar("a" + uuid.uuid4().hex[:2])  # 3 chars: minimo
    _criar(uuid.uuid4().hex + "x" * 48)  # 80 chars exatos


def test_check_ocasiao_origem_status() -> None:
    adm = _admin()
    for col, val in (("ocasiao", "dia_do_gato"), ("origem", "robo")):
        valores = {"ocasiao": "manha", "origem": "manual", col: val}
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(
                    "insert into public.saudacoes (ocasiao, texto, origem, criado_por) values (%s, %s, %s, %s)",
                    (valores["ocasiao"], _texto(), valores["origem"], adm),
                )
        assert h.sqlstate(exc) == "23514", col
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                "insert into public.saudacoes (ocasiao, texto, origem, status, criado_por) "
                "values ('manha', %s, 'manual', 'arquivada', %s)",
                (_texto(), adm),
            )
    # o gatilho (BEFORE) barra qualquer status inicial diferente de pendente antes do CHECK
    assert h.sqlstate(exc) == "AG001"


def test_todas_as_ocasioes_validas() -> None:
    ocasioes = (
        "madrugada manha tarde noite segunda sexta fim_de_semana ano_novo vespera_ano_novo carnaval "
        "sexta_santa pascoa tiradentes trabalho corpus_christi independencia padroeira finados "
        "republica consciencia_negra dia_servidor vespera_natal natal"
    ).split()
    assert len(ocasioes) == 23
    for o in ocasioes:
        _criar(ocasiao=o)


def test_unico_ocasiao_texto() -> None:
    t = _texto()
    _criar(t, "noite")
    with pytest.raises(psycopg.Error) as exc:
        _criar(t, "noite")
    assert h.sqlstate(exc) == "23505"
    _criar(t, "tarde")  # mesma frase em outra ocasiao e permitida


# --- Criacao -----------------------------------------------------------------
def test_nasce_pendente_e_recusa_nascer_decidida() -> None:
    sid = _criar()
    with h.conectar() as c:
        row = c.execute(
            "select status, decidido_por, decidido_em from public.saudacoes where id = %s", (sid,)
        ).fetchone()
    assert row == ("pendente", None, None)
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                "insert into public.saudacoes (ocasiao, texto, origem, status, criado_por, decidido_por, decidido_em) "
                "values ('manha', %s, 'manual', 'aprovada', %s, %s, now())",
                (_texto(), _admin(), _admin()),
            )
    assert h.sqlstate(exc) == "AG001"


def test_criador_precisa_ser_admin_ativo_e_manual_exige_responsavel() -> None:
    sol = h.criar_usuario("sol-sau2")
    aut = h.criar_usuario("aut-sau")
    inativo = h.criar_usuario("admin-sau-inativo", "admin", ativo=False)
    for quem in (sol, aut, inativo):
        with pytest.raises(psycopg.Error) as exc:
            _criar(por=quem)
        assert h.sqlstate(exc) == "AG002"
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                "insert into public.saudacoes (ocasiao, texto, origem) values ('manha', %s, 'manual')",
                (_texto(),),
            )
    assert h.sqlstate(exc) == "AG002"


def test_job_de_ia_sem_responsavel_pode_criar_pendente() -> None:
    sid = uuid.uuid4()
    with h.conectar() as c:
        c.execute(
            "insert into public.saudacoes (id, ocasiao, texto, origem) values (%s, 'sexta', %s, 'ia')",
            (sid, _texto("Sextou")),
        )
    (linha,) = _auditoria(sid)
    assert linha[2] is None  # ator nulo (job)
    assert linha[4] == "pendente"


# --- Imutabilidade -----------------------------------------------------------
@pytest.mark.parametrize(
    "set_sql,param",
    [
        ("texto = %s", "Texto alterado"),
        ("ocasiao = %s", "noite"),
        ("origem = %s", "ia"),
        ("criado_em = %s", "2000-01-01T00:00:00Z"),
    ],
)
def test_texto_ocasiao_origem_criado_em_imutaveis(set_sql: str, param: str) -> None:
    sid = _criar(ocasiao="manha", origem="manual")
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(f"update public.saudacoes set {set_sql} where id = %s", (param, sid))
    assert h.sqlstate(exc) == "AG004"


def test_criado_por_e_id_imutaveis_e_decisao_so_com_status() -> None:
    sid = _criar()
    outro = h.criar_usuario("admin-sau-2", "admin")
    for sql, params in (
        ("update public.saudacoes set criado_por = %s where id = %s", (outro, sid)),
        ("update public.saudacoes set id = %s where id = %s", (uuid.uuid4(), sid)),
        ("update public.saudacoes set decidido_por = %s where id = %s", (outro, sid)),
    ):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(sql, params)  # type: ignore[arg-type]
        assert h.sqlstate(exc) == "AG004", sql


# --- Transicoes --------------------------------------------------------------
def test_pendente_para_aprovada_carimba_e_audita() -> None:
    adm = _admin()
    sid = _criar(por=adm)
    _decidir(sid, "aprovada", adm)
    with h.conectar() as c:
        row = c.execute(
            "select status, decidido_por, decidido_em is not null from public.saudacoes where id = %s", (sid,)
        ).fetchone()
    assert row == ("aprovada", adm, True)
    criada, aprovada = _auditoria(sid)
    assert criada[:5] == ("configuracoes", "configuracao_alterada", adm, None, "pendente")
    assert aprovada[:5] == ("configuracoes", "configuracao_alterada", adm, "pendente", "aprovada")
    assert aprovada[5]["evento"] == "aprovada"
    assert criada[5]["evento"] == "criada"


def test_rejeitar_reabilitar_e_nao_voltar_a_pendente() -> None:
    adm = _admin()
    sid = _criar(por=adm)
    _decidir(sid, "rejeitada", adm)
    _decidir(sid, "aprovada", adm)
    _decidir(sid, "rejeitada", adm)
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                "update public.saudacoes set status = 'pendente', decidido_por = null, decidido_em = null "
                "where id = %s",
                (sid,),
            )
    assert h.sqlstate(exc) == "AG001"
    eventos = [a[5]["evento"] for a in _auditoria(sid)]
    assert eventos == ["criada", "rejeitada", "aprovada", "rejeitada"]


def test_decisao_exige_admin_ativo() -> None:
    sid = _criar()
    aut = h.criar_usuario("aut-sau2", "autorizador")
    inativo = h.criar_usuario("admin-sau-inativo2", "admin", ativo=False)
    for quem in (aut, inativo, None):
        with pytest.raises(psycopg.Error) as exc:
            _decidir(sid, "aprovada", quem)
        # decidido_por nulo com status <> pendente tambem e barrado pelo gatilho antes do CHECK
        assert h.sqlstate(exc) == "AG002"
    assert len(_auditoria(sid)) == 1  # so a criacao


def test_auditoria_sem_texto_nem_dado_pessoal() -> None:
    adm = _admin()
    t = _texto("Segredinho")
    sid = _criar(t, "natal", "ia", adm)
    _decidir(sid, "aprovada", adm)
    for linha in _auditoria(sid):
        bruto = json.dumps(linha[5])
        assert t not in bruto
        assert set(linha[5]) == {"recurso", "evento", "ocasiao", "origem"}
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("update public.auditoria set para = 'x' where registro_id = %s", (sid,))
    assert h.sqlstate(exc) == "AG003"
