"""Testes das migrations 25a/25b/25c: foto de perfil (tabela, RPCs, remocao por admin, auditoria, bucket).

Somente banco local/dev descartavel (TEST_DATABASE_URL + TEST_DATABASE_CONFIRM=descartavel + marcador
`comment on database ... is 'sisveiculos:descartavel'`; sem eles os testes sao pulados). Ver dbhelpers.py. Recurso sempre ligado:
nao depende de flags em `configuracoes`. Auditoria e append-only e as fotos sao imutaveis (sem
DELETE): os testes nao limpam o que criam; usam ids unicos. A guarda de banco descartavel fica em
dbhelpers.py (host local, confirmacao e marcador no banco). Sem paralelismo (sem xdist).
Os testes de storage pulam se o schema storage nao existir.

    pytest supabase/tests/test_perfil_foto.py -m db_local
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    # A guarda (host local, TEST_DATABASE_CONFIRM, marcador no banco) vive em dbhelpers.DSN.
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

NEGADO = "42501"
CHECK = "23514"
UNICO = "23505"
MB1 = 1024 * 1024
EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

TROCAR = "public.trocar_foto_perfil(uuid, uuid, text, integer)"
REMOVER = "public.remover_foto_perfil(uuid)"
REMOVER_POR = "public.remover_foto_perfil_por(uuid, uuid)"
GATILHOS = (
    "public.perfil_fotos_validar()",
    "public.perfil_fotos_so_remocao()",
    "public.perfil_fotos_auditar()",
)


@pytest.fixture
def usuario() -> uuid.UUID:
    return h.criar_usuario("fotografado")


# --- helpers ----------------------------------------------------------------------------------
def _trocar(
    uid: uuid.UUID | None,
    *,
    pid: uuid.UUID | None = None,
    mime: str = "image/png",
    tamanho: int = 1000,
    com_id: bool = True,
) -> tuple[uuid.UUID | None, uuid.UUID | None, str | None]:
    """Chama a RPC; devolve (id_novo, id_anterior, caminho_anterior)."""
    novo = (pid or uuid.uuid4()) if com_id else None
    with h.conectar() as c:
        row = c.execute(
            "select id_anterior, caminho_anterior from public.trocar_foto_perfil(%s, %s, %s, %s)",
            (uid, novo, mime, tamanho),
        ).fetchone()
    assert row is not None
    return novo, row[0], row[1]


def _remover(uid: uuid.UUID | None) -> tuple[uuid.UUID | None, str | None]:
    with h.conectar() as c:
        row = c.execute(
            "select id_anterior, caminho_anterior from public.remover_foto_perfil(%s)", (uid,)
        ).fetchone()
    assert row is not None
    return row[0], row[1]


def _remover_por(
    usuario: uuid.UUID | None, ator: uuid.UUID | None
) -> tuple[uuid.UUID | None, str | None]:
    with h.conectar() as c:
        row = c.execute(
            "select id_anterior, caminho_anterior from public.remover_foto_perfil_por(%s, %s)",
            (usuario, ator),
        ).fetchone()
    assert row is not None
    return row[0], row[1]


def _fotos(uid: uuid.UUID) -> list[tuple]:
    with h.conectar() as c:
        return c.execute(
            "select id, mime, tamanho_bytes, caminho, removida_em from public.perfil_fotos "
            "where usuario_id=%s order by criado_em, id",
            (uid,),
        ).fetchall()


def _ativas(uid: uuid.UUID) -> list[tuple]:
    return [f for f in _fotos(uid) if f[4] is None]


def _insert_direto(uid: uuid.UUID, pid: uuid.UUID | None = None, mime: str = "image/png") -> None:
    with h.conectar() as c:
        c.execute(
            "insert into public.perfil_fotos (id, usuario_id, mime, tamanho_bytes) values (%s, %s, %s, 10)",
            (pid or uuid.uuid4(), uid, mime),
        )


def _erro(sql: str, params: tuple, esperado: str) -> "psycopg.Error":
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(sql, params)
    assert h.sqlstate(exc) == esperado, str(exc.value)
    return exc.value


def _sem_conteudo(exc: "psycopg.Error") -> None:
    assert "Failing row" not in str(exc)
    assert not exc.diag.message_detail


# --- estrutura ----------------------------------------------------------------------------------
def test_colunas_tipos_e_caminho_gerado() -> None:
    with h.conectar() as c:
        cols = {
            r[0]: r[1:]
            for r in c.execute(
                "select column_name, data_type, is_nullable, is_generated, generation_expression "
                "from information_schema.columns where table_schema='public' and table_name='perfil_fotos'"
            ).fetchall()
        }
    assert set(cols) == {
        "id", "usuario_id", "mime", "tamanho_bytes", "caminho", "criado_em", "removida_em", "removida_por"
    }  # fmt: skip
    assert cols["removida_por"][0] == "uuid" and cols["removida_por"][1] == "YES"
    assert cols["id"][0] == "uuid" and cols["usuario_id"][0] == "uuid"
    assert cols["tamanho_bytes"][0] == "integer"
    assert cols["criado_em"][0] == "timestamp with time zone"
    assert cols["removida_em"][0] == "timestamp with time zone" and cols["removida_em"][1] == "YES"
    assert cols["caminho"][2] == "ALWAYS"


def test_fk_para_perfis_com_restrict() -> None:
    with h.conectar() as c:
        row = c.execute(
            "select confrelid::regclass::text, confdeltype from pg_constraint "
            "where conrelid='public.perfil_fotos'::regclass and contype='f'"
        ).fetchall()
    assert row == [("perfis", "r"), ("perfis", "r")]  # usuario_id e removida_por (25c)


def test_indice_unico_parcial_por_usuario_ativo() -> None:
    with h.conectar() as c:
        (defn,) = c.execute(
            "select indexdef from pg_indexes where schemaname='public' and tablename='perfil_fotos' "
            "and indexname='uq_perfil_fotos_ativa'"
        ).fetchone() or ("",)
    assert "UNIQUE" in defn and "(usuario_id)" in defn and "removida_em IS NULL" in defn


def test_sem_flag_em_configuracoes() -> None:
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from information_schema.columns where table_schema='public' "
            "and table_name='configuracoes' and (column_name like '%foto%' or column_name like '%avatar%')"
        ).fetchone() or (-1,)
    assert n == 0


def test_gatilhos_sempre_ativos() -> None:
    esperados = {
        "trg_perfil_fotos_validar",
        "trg_perfil_fotos_so_remocao",
        "trg_perfil_fotos_sem_delete",
        "trg_perfil_fotos_sem_truncate",
        "trg_perfil_fotos_auditoria",
        "trg_perfil_fotos_auditoria_remocao",
    }
    with h.conectar() as c:
        rows = dict(
            c.execute(
                "select tgname, tgenabled from pg_trigger "
                "where tgrelid='public.perfil_fotos'::regclass and not tgisinternal"
            ).fetchall()
        )
    assert set(rows) == esperados
    assert all(v == "A" for v in rows.values())  # ENABLE ALWAYS


def test_migration_nao_cria_gatilho_em_perfis() -> None:
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from pg_trigger t join pg_proc p on p.oid = t.tgfoid "
            "where t.tgrelid='public.perfis'::regclass and not t.tgisinternal "
            "and p.proname like 'perfil_fotos%'"
        ).fetchone() or (-1,)
    assert n == 0


# --- caminho, unicidade da foto ativa -------------------------------------------------------------
@pytest.mark.parametrize("mime", list(EXT))
def test_caminho_gerado_pela_convencao(usuario: uuid.UUID, mime: str) -> None:
    pid, _, _ = _trocar(usuario, mime=mime)
    (foto,) = _ativas(usuario)
    assert foto[0] == pid
    assert foto[3] == f"{usuario}/{pid}.{EXT[mime]}"


def test_caminho_nao_e_informavel(usuario: uuid.UUID) -> None:
    outro = h.criar_usuario("outro")
    _erro(
        "insert into public.perfil_fotos (usuario_id, mime, tamanho_bytes, caminho) "
        "values (%s, 'image/png', 10, %s)",
        (usuario, f"{outro}/{uuid.uuid4()}.png"),
        "428C9",
    )


def test_segunda_foto_ativa_por_insert_direto_viola_indice_parcial(usuario: uuid.UUID) -> None:
    _insert_direto(usuario)
    with pytest.raises(psycopg.Error) as exc:
        _insert_direto(usuario)
    assert h.sqlstate(exc) == UNICO
    assert exc.value.diag.constraint_name == "uq_perfil_fotos_ativa"


def test_insert_direto_nasce_ativa_e_com_carimbo_do_servidor(usuario: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute(
            "insert into public.perfil_fotos (usuario_id, mime, tamanho_bytes, criado_em, removida_em) "
            "values (%s, 'image/png', 10, '2000-01-01', '2000-01-02')",
            (usuario,),
        )
        row = c.execute(
            "select extract(year from criado_em)::int, removida_em from public.perfil_fotos "
            "where usuario_id=%s",
            (usuario,),
        ).fetchone()
    assert row is not None and row[0] >= 2026 and row[1] is None


def test_insert_direto_para_usuario_inexistente_viola_fk() -> None:
    with pytest.raises(psycopg.Error) as exc:
        _insert_direto(uuid.uuid4())
    assert h.sqlstate(exc) == "23503"


# --- validacao de conteudo (mensagem fixa, sem DETAIL) ----------------------------------------------
@pytest.mark.parametrize("mime", ["image/gif", "application/pdf", "image/svg+xml", "IMAGE/JPEG", ""])
def test_mime_restrito(usuario: uuid.UUID, mime: str) -> None:
    with pytest.raises(psycopg.Error) as exc:
        _trocar(usuario, mime=mime)
    assert h.sqlstate(exc) == CHECK
    assert exc.value.diag.constraint_name == "ck_perfil_fotos_mime"
    _sem_conteudo(exc.value)
    assert _fotos(usuario) == []


@pytest.mark.parametrize("tamanho", [0, -1, MB1 + 1])
def test_tamanho_fora_do_limite(usuario: uuid.UUID, tamanho: int) -> None:
    with pytest.raises(psycopg.Error) as exc:
        _trocar(usuario, tamanho=tamanho)
    assert h.sqlstate(exc) == CHECK
    assert exc.value.diag.constraint_name == "ck_perfil_fotos_tamanho"
    _sem_conteudo(exc.value)


@pytest.mark.parametrize("tamanho", [1, MB1])
def test_tamanho_nos_limites_vale(usuario: uuid.UUID, tamanho: int) -> None:
    _trocar(usuario, tamanho=tamanho)
    assert len(_ativas(usuario)) == 1


def test_id_nulo_e_usuario_nulo_recusados_sem_conteudo(usuario: uuid.UUID) -> None:
    with pytest.raises(psycopg.Error) as exc:
        _trocar(usuario, com_id=False)
    assert h.sqlstate(exc) == "23502" and exc.value.diag.column_name == "id"
    _sem_conteudo(exc.value)
    with pytest.raises(psycopg.Error) as exc2:
        _trocar(None)
    assert h.sqlstate(exc2) == "23502" and exc2.value.diag.column_name == "usuario_id"
    with pytest.raises(psycopg.Error) as exc3:
        _remover(None)
    assert h.sqlstate(exc3) == "23502"


def test_troca_recusada_nao_remove_a_foto_atual(usuario: uuid.UUID) -> None:
    atual, _, _ = _trocar(usuario)
    with pytest.raises(psycopg.Error):
        _trocar(usuario, mime="image/gif")
    (foto,) = _ativas(usuario)
    assert foto[0] == atual  # atomico: a marca de remocao da antiga foi desfeita


def test_id_repetido_recusado(usuario: uuid.UUID) -> None:
    pid, _, _ = _trocar(usuario)
    with pytest.raises(psycopg.Error) as exc:
        _trocar(usuario, pid=pid)
    assert h.sqlstate(exc) == UNICO
    assert [f[0] for f in _ativas(usuario)] == [pid]


# --- RPCs ---------------------------------------------------------------------------------------
def test_primeira_troca_nao_tem_anterior(usuario: uuid.UUID) -> None:
    pid, ant, cam = _trocar(usuario)
    assert ant is None and cam is None
    assert [f[0] for f in _ativas(usuario)] == [pid]


def test_troca_marca_antiga_insere_nova_e_devolve_caminho_antigo(usuario: uuid.UUID) -> None:
    p1, _, _ = _trocar(usuario, mime="image/jpeg")
    p2, ant, cam = _trocar(usuario, mime="image/webp")
    assert ant == p1
    assert cam == f"{usuario}/{p1}.jpg"
    fotos = {f[0]: f for f in _fotos(usuario)}
    assert fotos[p1][4] is not None  # removida
    assert fotos[p2][4] is None  # ativa
    assert fotos[p2][3] == f"{usuario}/{p2}.webp"
    assert len(fotos) == 2  # sem DELETE fisico


def test_troca_nunca_devolve_caminho_de_outro_usuario(usuario: uuid.UUID) -> None:
    outro = h.criar_usuario("vizinho")
    po, _, _ = _trocar(outro)
    _, ant, cam = _trocar(usuario)
    assert ant is None and cam is None
    p2, ant2, cam2 = _trocar(usuario)
    assert cam2 is not None and cam2.startswith(f"{usuario}/")
    assert [f[0] for f in _ativas(outro)] == [po]  # a foto do outro nao foi tocada
    assert p2 != po


def test_remover_marca_e_devolve_caminho_e_e_idempotente(usuario: uuid.UUID) -> None:
    pid, _, _ = _trocar(usuario, mime="image/png")
    ant, cam = _remover(usuario)
    assert ant == pid and cam == f"{usuario}/{pid}.png"
    assert _ativas(usuario) == []
    assert _remover(usuario) == (None, None)  # idempotente
    assert len(_fotos(usuario)) == 1  # a linha continua (remocao logica)


def test_remover_sem_nenhuma_foto_devolve_nulos(usuario: uuid.UUID) -> None:
    assert _remover(usuario) == (None, None)


def test_depois_de_remover_pode_enviar_outra(usuario: uuid.UUID) -> None:
    _trocar(usuario)
    _remover(usuario)
    p2, ant, _ = _trocar(usuario)
    assert ant is None
    assert [f[0] for f in _ativas(usuario)] == [p2]


def test_usuario_inexistente_recusado_sem_detail() -> None:
    inexistente = uuid.uuid4()
    for chamada in (lambda: _trocar(inexistente), lambda: _remover(inexistente)):
        with pytest.raises(psycopg.Error) as exc:
            chamada()
        assert h.sqlstate(exc) == "AG017"
        _sem_conteudo(exc.value)
        assert str(inexistente) not in str(exc.value)


def test_troca_de_usuario_inativo_recusada_sem_detail() -> None:
    admin = h.criar_usuario("admin-ag017", "admin")
    alvo = h.criar_usuario("inativo-rpc")
    h.alterar_perfil(alvo, admin, ativo=False)
    with pytest.raises(psycopg.Error) as exc:
        _trocar(alvo)
    assert h.sqlstate(exc) == "AG017"
    _sem_conteudo(exc.value)
    assert str(alvo) not in str(exc.value)
    assert _fotos(alvo) == []


def test_usuario_inativo_nao_troca_mas_pode_ter_a_foto_removida() -> None:
    admin = h.criar_usuario("admin-foto", "admin")
    alvo = h.criar_usuario("sairah")
    _trocar(alvo)
    h.alterar_perfil(alvo, admin, ativo=False)
    with pytest.raises(psycopg.Error) as exc:
        _trocar(alvo)
    assert h.sqlstate(exc) == "AG017"
    with pytest.raises(psycopg.Error) as exc2:
        _insert_direto(h.criar_usuario("inativo-direto", ativo=False))
    assert h.sqlstate(exc2) == "AG017"
    _sem_conteudo(exc2.value)
    ant, _ = _remover(alvo)  # remover dado pessoal de conta desativada e permitido
    assert ant is not None and _ativas(alvo) == []


def test_troca_nao_escreve_em_perfis(usuario: uuid.UUID) -> None:
    def xmin() -> str:
        with h.conectar() as c:
            (x,) = c.execute("select xmin::text from public.perfis where id=%s", (usuario,)).fetchone() or ("",)
        return x

    antes = xmin()
    _trocar(usuario)
    _trocar(usuario)
    _remover(usuario)
    assert xmin() == antes


# --- imutabilidade --------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "set_sql",
    [
        "mime = 'image/png'",
        "tamanho_bytes = 1",
        "usuario_id = usuario_id",
        "criado_em = now()",
        "id = gen_random_uuid()",
    ],
)
def test_update_de_outras_colunas_bloqueado(usuario: uuid.UUID, set_sql: str) -> None:
    pid, _, _ = _trocar(usuario, mime="image/jpeg")
    _erro(f"update public.perfil_fotos set {set_sql} where id = %s", (pid,), "AG003")


def test_update_de_removida_em_so_uma_vez_e_sem_voltar(usuario: uuid.UUID) -> None:
    pid, _, _ = _trocar(usuario)
    _erro("update public.perfil_fotos set removida_em = null where id = %s", (pid,), "AG003")
    _erro("update public.perfil_fotos set removida_em = now(), tamanho_bytes = 2 where id = %s", (pid,), "AG003")
    with h.conectar() as c:
        c.execute("update public.perfil_fotos set removida_em = now() where id = %s", (pid,))
    _erro("update public.perfil_fotos set removida_em = now() where id = %s", (pid,), "AG003")
    _erro("update public.perfil_fotos set removida_em = null where id = %s", (pid,), "AG003")


def test_delete_bloqueado(usuario: uuid.UUID) -> None:
    pid, _, _ = _trocar(usuario)
    _erro("delete from public.perfil_fotos where id = %s", (pid,), "AG003")


def test_truncate_bloqueado_sozinho_e_junto_das_referenciadoras() -> None:
    # perfil_fotos nao e referenciada por FK: o gatilho dispara ja no TRUNCATE simples.
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("truncate table public.perfil_fotos")
    assert h.sqlstate(exc) == "AG003"
    assert exc.value.diag.message_primary == "Operação TRUNCATE proibida em perfil_fotos: registros nunca são alterados/apagados fisicamente."
    # Junto de perfis (que ela referencia) E de todas as tabelas que referenciam perfis (fecho
    # transitivo das FKs): sem isso o Postgres recusa com 0A000 antes do gatilho. Com a lista
    # completa a instrucao chega a executar e o gatilho da perfil_fotos responde AG003.
    with h.conectar() as c:
        tabelas = [
            r[0]
            for r in c.execute(
                """with recursive dep(oid) as (
                       select 'public.perfis'::regclass::oid
                       union
                       select f.conrelid from pg_constraint f join dep on f.confrelid = dep.oid
                        where f.contype = 'f')
                   select distinct oid::regclass::text from dep order by 1"""
            ).fetchall()
        ]
    assert "perfil_fotos" in tabelas and "perfis" in tabelas
    with pytest.raises(psycopg.Error) as exc_junto:
        with h.conectar() as c:
            c.execute("truncate table " + ", ".join(tabelas))
    assert h.sqlstate(exc_junto) == "AG003", tabelas
    # a mensagem nomeia a tabela cujo gatilho disparou primeiro (uma da lista; perfil_fotos acima)
    assert (exc_junto.value.diag.message_primary or "").split(" proibida em ")[1].split(":")[0] in tabelas
    with pytest.raises(psycopg.Error) as exc2:
        with h.conectar() as c:
            c.execute("truncate table public.perfil_fotos cascade")
    assert h.sqlstate(exc2) == "AG003"
    assert "proibida em perfil_fotos:" in (exc2.value.diag.message_primary or "")


# --- concorrencia e locks ---------------------------------------------------------------------------
def test_duas_trocas_simultaneas_do_mesmo_usuario_deixam_uma_ativa(usuario: uuid.UUID) -> None:
    barreira = threading.Barrier(2)
    # Conexoes abertas ANTES da barreira: a corrida e so a chamada da RPC.
    conexoes = [h.conectar(), h.conectar()]
    ids = [uuid.uuid4(), uuid.uuid4()]

    def tentar(par: tuple["psycopg.Connection", uuid.UUID]) -> tuple[str, object]:
        c, pid = par
        barreira.wait(timeout=60)
        try:
            row = c.execute(
                "select id_anterior from public.trocar_foto_perfil(%s, %s, 'image/png', 10)",
                (usuario, pid),
            ).fetchone()
            return "ok", row[0] if row else None
        except psycopg.Error as e:
            return "erro", e.sqlstate

    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            futuros = [ex.submit(tentar, par) for par in zip(conexoes, ids, strict=True)]
            resultados = [f.result(timeout=90) for f in futuros]
    finally:
        for c in conexoes:
            c.close()

    assert [r[0] for r in resultados] == ["ok", "ok"], resultados  # serializadas, nenhuma falha
    assert len(_ativas(usuario)) == 1
    assert len(_fotos(usuario)) == 2
    # exatamente uma enxergou a foto da outra como anterior
    anteriores = [r[1] for r in resultados]
    assert sum(a is None for a in anteriores) == 1
    assert sum(a in ids for a in anteriores) == 1


def test_troca_nao_toma_perfis_admin(usuario: uuid.UUID) -> None:
    """Outra sessao segurando 'sisveiculos.perfis_admin' nao bloqueia a troca."""
    with psycopg.connect(h.DSN) as seg:  # type: ignore[arg-type]
        seg.execute("select pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0))")
        with h.conectar() as c:
            c.execute("set lock_timeout = '3s'")
            row = c.execute(
                "select id_anterior from public.trocar_foto_perfil(%s, %s, 'image/png', 10)",
                (usuario, uuid.uuid4()),
            ).fetchone()
            assert row is not None
            c.execute("select id_anterior from public.remover_foto_perfil(%s)", (usuario,))
        seg.rollback()
    assert _ativas(usuario) == []


def test_lock_do_avatar_e_por_usuario(usuario: uuid.UUID) -> None:
    """Segurar o lock do avatar de A bloqueia A (55P03), mas nao B."""
    outro = h.criar_usuario("sem-bloqueio")
    with psycopg.connect(h.DSN) as seg:  # type: ignore[arg-type]
        seg.execute(
            "select pg_advisory_xact_lock(hashtextextended('sisveiculos.avatar:' || %s::text, 0))",
            (str(usuario),),
        )
        with h.conectar() as c:
            c.execute("set lock_timeout = '1s'")
            c.execute("select * from public.trocar_foto_perfil(%s, %s, 'image/png', 10)", (outro, uuid.uuid4()))
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute("set lock_timeout = '1s'")
                c.execute("select * from public.trocar_foto_perfil(%s, %s, 'image/png', 10)", (usuario, uuid.uuid4()))
        assert h.sqlstate(exc) == "55P03"
        seg.rollback()
    assert _ativas(usuario) == []


# --- auditoria -------------------------------------------------------------------------------------
def _audit(uid: uuid.UUID) -> list[tuple]:
    with h.conectar() as c:
        return c.execute(
            "select acao, registro_id, ator_id, de, para, detalhes from public.auditoria "
            "where tabela='perfil_fotos' and ator_id=%s order by id",
            (uid,),
        ).fetchall()


def test_auditoria_sem_pii_e_com_as_acoes_novas(usuario: uuid.UUID) -> None:
    p1, _, _ = _trocar(usuario, mime="image/jpeg", tamanho=111)
    p2, _, _ = _trocar(usuario, mime="image/webp", tamanho=222)
    _remover(usuario)
    linhas = _audit(usuario)
    assert [(a, r) for a, r, *_ in linhas] == [
        ("foto_perfil_alterada", p1),
        ("foto_perfil_removida", p1),
        ("foto_perfil_alterada", p2),
        ("foto_perfil_removida", p2),
    ]
    for acao, _, ator, de, para, det in linhas:
        assert ator == usuario and de is None and para is None
        # remocao do dono (ou pela troca) grava por_admin=false; a alteracao nao tem a chave.
        assert set(det) == ({"mime", "tamanho_bytes"} | ({"por_admin"} if acao.endswith("removida") else set()))
        assert det.get("por_admin", False) is False
    assert linhas[0][5] == {"mime": "image/jpeg", "tamanho_bytes": 111}
    texto = str(linhas)
    assert ".jpg" not in texto and ".webp" not in texto and str(usuario) not in str([x[5] for x in linhas])


def test_troca_recusada_nao_deixa_auditoria(usuario: uuid.UUID) -> None:
    with pytest.raises(psycopg.Error):
        _trocar(usuario, mime="image/gif")
    assert _audit(usuario) == []


def test_remocao_idempotente_nao_audita_de_novo(usuario: uuid.UUID) -> None:
    _trocar(usuario)
    _remover(usuario)
    _remover(usuario)
    assert [a for a, *_ in _audit(usuario)] == ["foto_perfil_alterada", "foto_perfil_removida"]


ACOES_ANTIGAS = (
    "criada", "transicao", "convite_criado", "convite_usado", "convite_revogado",
    "perfil_criado", "perfil_alterado", "perfil_unidade_alterada", "admin_semeado",
    "configuracao_alterada", "veiculo_criado", "veiculo_alterado",
    "acao_administrativa", "acesso_negado", "checklist_registrado",
)  # fmt: skip
TABELAS_ANTIGAS_AUD = ("requisicoes", "convites", "perfis", "veiculos", "configuracoes", "checklists")


def test_ck_auditoria_preserva_valores_antigos_e_inclui_os_novos() -> None:
    with h.conectar() as c:
        defs = dict(
            c.execute(
                "select conname, pg_get_constraintdef(oid) from pg_constraint "
                "where conrelid='public.auditoria'::regclass "
                "and conname in ('ck_auditoria_acao', 'ck_auditoria_tabela')"
            ).fetchall()
        )
    assert len(ACOES_ANTIGAS) == 15 and len(TABELAS_ANTIGAS_AUD) == 6
    # Contagem exata: 17 acoes / 7 tabelas + as da migration de relatorios (19 acoes / 9 tabelas).
    assert len(set(re.findall(r"'([a-z_]+)'::text", defs["ck_auditoria_acao"]))) == 19
    assert len(set(re.findall(r"'([a-z_]+)'::text", defs["ck_auditoria_tabela"]))) == 9
    for v in (*ACOES_ANTIGAS, "foto_perfil_alterada", "foto_perfil_removida",
              "relatorio_gerado", "relatorio_enviado"):
        assert f"'{v}'" in defs["ck_auditoria_acao"], v
    for v in (*TABELAS_ANTIGAS_AUD, "perfil_fotos", "relatorios", "envios_email"):
        assert f"'{v}'" in defs["ck_auditoria_tabela"], v


def test_registrar_auditoria_do_backend_nao_aceita_acoes_de_foto() -> None:
    for acao in ("foto_perfil_alterada", "foto_perfil_removida"):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute("select public.registrar_auditoria('perfil_fotos', null, %s, null)", (acao,))
        assert h.sqlstate(exc) == "AG003"


# --- RLS, grants e funcoes ----------------------------------------------------------------------------
def test_rls_ligado_e_grants_minimos() -> None:
    with h.conectar() as c:
        (rls,) = c.execute(
            "select relrowsecurity from pg_class where oid = 'public.perfil_fotos'::regclass"
        ).fetchone() or (False,)
        assert rls is True
        for papel in ("anon", "authenticated"):
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                (ok,) = c.execute(
                    "select has_table_privilege(%s, 'public.perfil_fotos', %s)", (papel, priv)
                ).fetchone() or (True,)
                assert ok is False, (papel, priv)
        for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            (ok,) = c.execute(
                "select has_table_privilege('service_role', 'public.perfil_fotos', %s)", (priv,)
            ).fetchone() or (True,)
            assert ok is False, priv
        (sel,) = c.execute(
            "select has_table_privilege('service_role', 'public.perfil_fotos', 'SELECT')"
        ).fetchone() or (False,)
        assert sel is True
        (pol,) = c.execute(
            "select count(*) from pg_policies where schemaname='public' and tablename='perfil_fotos' "
            "and policyname='perfil_fotos_sem_acesso_api'"
        ).fetchone() or (0,)
        assert pol == 1


@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_api_nao_le_nem_escreve_nem_chama_rpc(usuario: uuid.UUID, papel: str) -> None:
    uid = usuario if papel == "authenticated" else None
    for sql in (
        "select * from public.perfil_fotos",
        "insert into public.perfil_fotos (usuario_id, mime, tamanho_bytes) values (gen_random_uuid(), 'image/png', 1)",
        "update public.perfil_fotos set removida_em = now()",
        "delete from public.perfil_fotos",
        f"select * from {TROCAR.split('(')[0]}(gen_random_uuid(), gen_random_uuid(), 'image/png', 1)",
        f"select * from {REMOVER.split('(')[0]}(gen_random_uuid())",
        f"select * from {REMOVER_POR.split('(')[0]}(gen_random_uuid(), gen_random_uuid())",
    ):
        with pytest.raises(psycopg.Error) as exc:
            with h.como(papel, uid) as c:
                c.execute(sql)
        assert h.sqlstate(exc) == NEGADO, sql


def test_service_role_le_chama_rpc_mas_nao_escreve_direto(usuario: uuid.UUID) -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.como("service_role") as c:
            c.execute(
                "insert into public.perfil_fotos (usuario_id, mime, tamanho_bytes) values (%s, 'image/png', 1)",
                (usuario,),
            )
    assert h.sqlstate(exc) == NEGADO
    with pytest.raises(psycopg.Error) as exc2:
        with h.como("service_role") as c:
            c.execute("update public.perfil_fotos set removida_em = now()")
    assert h.sqlstate(exc2) == NEGADO
    with h.como("service_role") as c:  # rollback ao sair
        row = c.execute("select * from public.trocar_foto_perfil(%s, %s, 'image/png', 10)", (usuario, uuid.uuid4())).fetchone()
        assert row == (None, None)
        (n,) = c.execute(
            "select count(*) from public.perfil_fotos where usuario_id=%s and removida_em is null", (usuario,)
        ).fetchone() or (0,)
        assert n == 1
        assert c.execute("select * from public.remover_foto_perfil(%s)", (usuario,)).fetchone() is not None
        c.execute("select * from public.trocar_foto_perfil(%s, %s, 'image/png', 10)", (usuario, uuid.uuid4()))
        assert c.execute(
            "select * from public.remover_foto_perfil_por(%s, %s)", (usuario, usuario)
        ).fetchone() is not None


@pytest.mark.parametrize("assinatura", [TROCAR, REMOVER, REMOVER_POR, *GATILHOS])
def test_execute_apenas_onde_deve(assinatura: str) -> None:
    rpc = assinatura in (TROCAR, REMOVER, REMOVER_POR)
    with h.conectar() as c:
        for papel in ("anon", "authenticated"):
            (ok,) = c.execute(
                "select has_function_privilege(%s, %s::regprocedure, 'execute')", (papel, assinatura)
            ).fetchone() or (True,)
            assert ok is False, papel
        (publico,) = c.execute(
            "select exists (select 1 from pg_proc p, "
            "aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
            "where p.oid = %s::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE')",
            (assinatura,),
        ).fetchone() or (True,)
        assert publico is False
        (sr,) = c.execute(
            "select has_function_privilege('service_role', %s::regprocedure, 'execute')", (assinatura,)
        ).fetchone() or (None,)
        assert sr is rpc, assinatura  # RPC: so service_role; gatilhos: ninguem


def test_funcoes_security_definer_com_search_path_vazio() -> None:
    with h.conectar() as c:
        rows = c.execute(
            "select proname, prosecdef, proconfig from pg_proc where pronamespace='public'::regnamespace "
            "and proname in ('trocar_foto_perfil', 'remover_foto_perfil', 'remover_foto_perfil_por', "
            "'perfil_fotos_validar', 'perfil_fotos_auditar', 'perfil_fotos_so_remocao')"
        ).fetchall()
    assert len(rows) == 6
    for nome, secdef, config in rows:
        assert config and any(x.replace(" ", "") in ("search_path=", 'search_path=""') for x in config), nome
        if nome != "perfil_fotos_so_remocao":
            assert secdef is True, nome


# --- storage ---------------------------------------------------------------------------------------
@pytest.fixture
def storage() -> None:
    with h.conectar() as c:
        (r,) = c.execute("select to_regclass('storage.objects') is not null").fetchone() or (False,)
    if not r:
        pytest.skip("schema storage ausente (use `supabase start`)")


def test_bucket_avatares_privado_com_limites(storage: None) -> None:
    with h.conectar() as c:
        row = c.execute(
            "select public, file_size_limit, allowed_mime_types from storage.buckets where id='avatares'"
        ).fetchone()
    assert row is not None
    assert row[0] is False
    assert row[1] == MB1
    assert sorted(row[2]) == ["image/jpeg", "image/png", "image/webp"]


def test_politica_restritiva_do_bucket_avatares(storage: None) -> None:
    with h.conectar() as c:
        row = c.execute(
            "select permissive, roles::text[] from pg_policies where schemaname='storage' "
            "and tablename='objects' and policyname='avatares_bucket_sem_acesso_api'"
        ).fetchone()
    assert row is not None
    assert row[0] == "RESTRICTIVE"
    assert set(row[1]) == {"anon", "authenticated"}


@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_storage_nega_tudo_a_anon_e_authenticated(storage: None, usuario: uuid.UUID, papel: str) -> None:
    nome = f"{usuario}/{uuid.uuid4()}.png"
    with h.conectar() as c:
        c.execute("insert into storage.objects (bucket_id, name) values ('avatares', %s)", (nome,))
    uid = usuario if papel == "authenticated" else None
    with h.como(papel, uid) as c:
        (n,) = c.execute("select count(*) from storage.objects where bucket_id='avatares'").fetchone() or (-1,)
        assert n == 0
    for sql in (
        "insert into storage.objects (bucket_id, name) values ('avatares', %s)",
        "update storage.objects set name = name || 'x' where bucket_id='avatares' and name <> %s",
    ):
        try:
            with h.como(papel, uid) as c:
                cur = c.execute(sql, (f"novo/{uuid.uuid4()}",))
                assert cur.rowcount in (0, -1), sql
        except psycopg.Error as e:
            assert getattr(e, "sqlstate", None) == NEGADO, e


def test_politica_restritiva_vence_permissiva_ampla_no_bucket_avatares(storage: None, usuario: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute(
            "insert into storage.objects (bucket_id, name) values ('avatares', %s)",
            (f"{usuario}/{uuid.uuid4()}.png",),
        )
    conn = psycopg.connect(h.DSN)  # type: ignore[arg-type]
    try:
        conn.execute(
            "create policy tmp_libera_tudo_avatar on storage.objects for all to authenticated "
            "using (true) with check (true)"
        )
        conn.execute("set local role authenticated")
        conn.execute(
            "select set_config('request.jwt.claims', %s, true)",
            (f'{{"sub": "{usuario}", "role": "authenticated"}}',),
        )
        (n,) = conn.execute("select count(*) from storage.objects where bucket_id='avatares'").fetchone() or (-1,)
        assert n == 0
    finally:
        conn.rollback()
        conn.close()


def test_tabelas_antigas_continuam_sem_privilegio_para_anon_e_authenticated() -> None:
    """Sanidade (so privilegios de tabela): nenhuma tabela antiga foi aberta a anon/authenticated."""
    with h.conectar() as c:
        for tabela in ("perfis", "veiculos", "requisicoes", "auditoria", "configuracoes", "convites"):
            for papel in ("anon", "authenticated"):
                (ok,) = c.execute(
                    "select has_table_privilege(%s, %s, 'select, insert, update, delete')",
                    (papel, f"public.{tabela}"),
                ).fetchone() or (True,)
                assert ok is False, (papel, tabela)



# --- migration 25c: remover_foto_perfil_por (dono ou admin ativo) ----------------------------------
AG_NAO_AUTORIZADO = "AG022"


@pytest.fixture
def admin() -> uuid.UUID:
    return h.criar_usuario("admin-remocao", "admin")


def _audit_foto(foto_id: uuid.UUID) -> list[tuple]:
    """Auditoria por id da FOTO (registro_id), qualquer ator: (acao, ator_id, detalhes)."""
    with h.conectar() as c:
        return c.execute(
            "select acao, ator_id, detalhes from public.auditoria "
            "where tabela='perfil_fotos' and registro_id=%s order by id",
            (foto_id,),
        ).fetchall()


def _removida_por(foto_id: uuid.UUID) -> uuid.UUID | None:
    with h.conectar() as c:
        row = c.execute("select removida_por from public.perfil_fotos where id=%s", (foto_id,)).fetchone()
    assert row is not None
    return row[0]


def test_remocao_pelo_dono_via_rpc_nova_grava_ator_dono(usuario: uuid.UUID) -> None:
    pid, _, _ = _trocar(usuario, mime="image/jpeg", tamanho=77)
    ant, cam = _remover_por(usuario, usuario)
    assert ant == pid and cam == f"{usuario}/{pid}.jpg"
    assert _ativas(usuario) == []
    assert _removida_por(pid) == usuario
    assert _audit_foto(pid) == [
        ("foto_perfil_alterada", usuario, {"mime": "image/jpeg", "tamanho_bytes": 77}),
        ("foto_perfil_removida", usuario, {"mime": "image/jpeg", "tamanho_bytes": 77, "por_admin": False}),
    ]


def test_remocao_pelo_admin_ativo_grava_ator_admin_sem_pii(usuario: uuid.UUID, admin: uuid.UUID) -> None:
    pid, _, _ = _trocar(usuario, mime="image/webp", tamanho=55)
    ant, cam = _remover_por(usuario, admin)
    assert ant == pid and cam == f"{usuario}/{pid}.webp"
    assert _ativas(usuario) == []
    assert _removida_por(pid) == admin
    linhas = _audit_foto(pid)
    assert [(a, ator) for a, ator, _ in linhas] == [
        ("foto_perfil_alterada", usuario),  # o envio segue com ator = dono
        ("foto_perfil_removida", admin),  # a remocao tem ator = admin
    ]
    assert linhas[1][2] == {"mime": "image/webp", "tamanho_bytes": 55, "por_admin": True}
    texto = str(linhas[1][2])
    assert str(usuario) not in texto and str(admin) not in texto and ".webp" not in texto
    # o dono se obtem por join a partir do registro_id (a foto)
    with h.conectar() as c:
        (dono,) = c.execute(
            "select f.usuario_id from public.auditoria a join public.perfil_fotos f on f.id = a.registro_id "
            "where a.tabela='perfil_fotos' and a.registro_id=%s and a.acao='foto_perfil_removida'",
            (pid,),
        ).fetchone() or (None,)
    assert dono == usuario
    # a remocao do admin nao aparece como acao do dono
    assert [a for a, *_ in _audit(usuario)] == ["foto_perfil_alterada"]


def test_admin_removendo_a_propria_foto_nao_e_por_admin(admin: uuid.UUID) -> None:
    pid, _, _ = _trocar(admin)
    _remover_por(admin, admin)
    assert _audit_foto(pid)[1] == (
        "foto_perfil_removida",
        admin,
        {"mime": "image/png", "tamanho_bytes": 1000, "por_admin": False},
    )


def test_troca_pelo_dono_continua_com_ator_dono_e_removida_por_nulo(usuario: uuid.UUID) -> None:
    p1, _, _ = _trocar(usuario)
    p2, _, _ = _trocar(usuario)
    assert _removida_por(p1) is None and _removida_por(p2) is None
    assert [(a, ator) for a, ator, _ in _audit_foto(p1)] == [
        ("foto_perfil_alterada", usuario),
        ("foto_perfil_removida", usuario),
    ]
    _remover(usuario)  # RPC antiga continua igual: ator = dono
    assert [(a, ator) for a, ator, _ in _audit_foto(p2)] == [
        ("foto_perfil_alterada", usuario),
        ("foto_perfil_removida", usuario),
    ]
    assert _removida_por(p2) is None


def test_ator_por_evento_na_mesma_transacao(admin: uuid.UUID) -> None:
    """O gatilho de auditoria e DEFERRED: o ator tem de viajar na linha, nao em estado de sessao."""
    x, y, z = h.criar_usuario("ev-x"), h.criar_usuario("ev-y"), h.criar_usuario("ev-z")
    px, _, _ = _trocar(x)
    py, _, _ = _trocar(y)
    pz, _, _ = _trocar(z)
    with psycopg.connect(h.DSN) as c:  # type: ignore[arg-type]
        c.execute("select * from public.remover_foto_perfil_por(%s, %s)", (x, admin))  # admin
        c.execute("select * from public.trocar_foto_perfil(%s, %s, 'image/png', 10)", (y, uuid.uuid4()))
        c.execute("select * from public.remover_foto_perfil_por(%s, %s)", (z, z))  # dono
    rx, ry, rz = (_audit_foto(f)[1] for f in (px, py, pz))
    assert rx[:2] == ("foto_perfil_removida", admin) and rx[2]["por_admin"] is True
    assert ry[:2] == ("foto_perfil_removida", y) and ry[2]["por_admin"] is False
    assert rz[:2] == ("foto_perfil_removida", z) and rz[2]["por_admin"] is False


def test_admin_inativo_nao_remove_foto_de_outro(usuario: uuid.UUID) -> None:
    ativo = h.criar_usuario("admin-ativo-ref", "admin")
    inativo = h.criar_usuario("admin-inativo", "admin")
    h.alterar_perfil(inativo, ativo, ativo=False)
    pid, _, _ = _trocar(usuario)
    with pytest.raises(psycopg.Error) as exc:
        _remover_por(usuario, inativo)
    assert h.sqlstate(exc) == AG_NAO_AUTORIZADO
    _sem_conteudo(exc.value)
    assert str(usuario) not in str(exc.value) and str(inativo) not in str(exc.value)
    assert [f[0] for f in _ativas(usuario)] == [pid]
    assert [a for a, *_ in _audit_foto(pid)] == ["foto_perfil_alterada"]  # nada de remocao


@pytest.mark.parametrize("role", ["solicitante", "autorizador"])
def test_usuario_comum_nao_remove_foto_de_outro(usuario: uuid.UUID, role: str) -> None:
    ator = h.criar_usuario(f"comum-{role}", role)
    pid, _, _ = _trocar(usuario)
    with pytest.raises(psycopg.Error) as exc:
        _remover_por(usuario, ator)
    assert h.sqlstate(exc) == AG_NAO_AUTORIZADO
    assert [f[0] for f in _ativas(usuario)] == [pid]


def test_ator_inexistente_ou_nulo_recusado(usuario: uuid.UUID) -> None:
    pid, _, _ = _trocar(usuario)
    with pytest.raises(psycopg.Error) as exc:
        _remover_por(usuario, uuid.uuid4())
    assert h.sqlstate(exc) == AG_NAO_AUTORIZADO
    with pytest.raises(psycopg.Error) as exc2:
        _remover_por(usuario, None)
    assert h.sqlstate(exc2) == "23502" and exc2.value.diag.column_name == "removida_por"
    _sem_conteudo(exc2.value)
    with pytest.raises(psycopg.Error) as exc3:
        _remover_por(None, usuario)
    assert h.sqlstate(exc3) == "23502" and exc3.value.diag.column_name == "usuario_id"
    assert [f[0] for f in _ativas(usuario)] == [pid]


def test_alvo_inexistente_recusado_para_admin(admin: uuid.UUID) -> None:
    with pytest.raises(psycopg.Error) as exc:
        _remover_por(uuid.uuid4(), admin)
    assert h.sqlstate(exc) == "AG017"
    _sem_conteudo(exc.value)


def test_remocao_por_idempotente_e_alvo_sem_foto(usuario: uuid.UUID, admin: uuid.UUID) -> None:
    assert _remover_por(usuario, admin) == (None, None)  # alvo sem foto: sem erro
    assert _remover_por(usuario, usuario) == (None, None)
    pid, _, _ = _trocar(usuario)
    assert _remover_por(usuario, admin)[0] == pid
    assert _remover_por(usuario, admin) == (None, None)  # repetida
    assert _remover_por(usuario, usuario) == (None, None)
    assert [a for a, *_ in _audit_foto(pid)] == ["foto_perfil_alterada", "foto_perfil_removida"]
    assert len(_fotos(usuario)) == 1  # remocao logica
    assert _removida_por(pid) is not None


def test_conta_inativa_tem_foto_removida_pelo_dono_e_pelo_admin() -> None:
    adm = h.criar_usuario("admin-inativa", "admin")
    a, b = h.criar_usuario("inat-a"), h.criar_usuario("inat-b")
    pa, _, _ = _trocar(a)
    pb, _, _ = _trocar(b)
    h.alterar_perfil(a, adm, ativo=False)
    h.alterar_perfil(b, adm, ativo=False)
    assert _remover_por(a, a)[0] == pa  # o proprio dono, mesmo desativado
    assert _remover_por(b, adm)[0] == pb  # admin ativo remove de conta desativada
    assert _ativas(a) == [] and _ativas(b) == []


def test_removida_por_nao_e_informavel_no_insert(usuario: uuid.UUID, admin: uuid.UUID) -> None:
    pid = uuid.uuid4()
    with h.conectar() as c:
        c.execute(
            "insert into public.perfil_fotos (id, usuario_id, mime, tamanho_bytes, removida_por) "
            "values (%s, %s, 'image/png', 10, %s)",
            (pid, usuario, admin),
        )
    assert _removida_por(pid) is None
    assert [ator for _, ator, _ in _audit_foto(pid)] == [usuario]  # nao forja ator no INSERT


def test_removida_por_so_muda_na_remocao_e_so_uma_vez(usuario: uuid.UUID, admin: uuid.UUID) -> None:
    pid, _, _ = _trocar(usuario)
    _erro("update public.perfil_fotos set removida_por = %s where id = %s", (admin, pid), "AG003")
    _remover_por(usuario, usuario)
    _erro("update public.perfil_fotos set removida_por = %s where id = %s", (admin, pid), "AG003")
    _erro("update public.perfil_fotos set removida_por = null where id = %s", (pid,), "AG003")
    assert _removida_por(pid) == usuario


def test_remocao_por_nao_escreve_em_perfis(usuario: uuid.UUID, admin: uuid.UUID) -> None:
    def xmins() -> tuple[str, str]:
        with h.conectar() as c:
            rows = dict(
                c.execute("select id, xmin::text from public.perfis where id in (%s, %s)", (usuario, admin)).fetchall()
            )
        return rows[usuario], rows[admin]

    antes = xmins()
    _trocar(usuario)
    _remover_por(usuario, admin)
    _trocar(usuario)
    _remover_por(usuario, usuario)
    assert xmins() == antes


def test_remocao_por_nao_toma_perfis_admin(usuario: uuid.UUID, admin: uuid.UUID) -> None:
    """Outra sessao segurando 'sisveiculos.perfis_admin' nao bloqueia a remocao (dono nem admin)."""
    _trocar(usuario)
    with psycopg.connect(h.DSN) as seg:  # type: ignore[arg-type]
        seg.execute("select pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0))")
        with h.conectar() as c:
            c.execute("set lock_timeout = '3s'")
            c.execute("select * from public.remover_foto_perfil_por(%s, %s)", (usuario, admin))
            c.execute("select * from public.trocar_foto_perfil(%s, %s, 'image/png', 10)", (usuario, uuid.uuid4()))
            c.execute("select * from public.remover_foto_perfil_por(%s, %s)", (usuario, usuario))
        seg.rollback()
    assert _ativas(usuario) == []


def test_remocao_por_usa_o_lock_do_avatar_do_alvo(usuario: uuid.UUID, admin: uuid.UUID) -> None:
    """Lock do avatar do alvo segurado por outra sessao => 55P03; o de outro usuario nao interfere."""
    outro = h.criar_usuario("sem-bloqueio-2")
    _trocar(usuario)
    with psycopg.connect(h.DSN) as seg:  # type: ignore[arg-type]
        seg.execute(
            "select pg_advisory_xact_lock(hashtextextended('sisveiculos.avatar:' || %s::text, 0))",
            (str(usuario),),
        )
        with h.conectar() as c:
            c.execute("set lock_timeout = '1s'")
            c.execute("select * from public.remover_foto_perfil_por(%s, %s)", (outro, admin))
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute("set lock_timeout = '1s'")
                c.execute("select * from public.remover_foto_perfil_por(%s, %s)", (usuario, admin))
        assert h.sqlstate(exc) == "55P03"
        seg.rollback()
    assert len(_ativas(usuario)) == 1


@pytest.mark.parametrize("por_admin", [False, True])
def test_remocao_concorrente_com_troca_do_mesmo_usuario(
    usuario: uuid.UUID, admin: uuid.UUID, por_admin: bool
) -> None:
    """Troca e remocao simultaneas serializam (mesmo lock): nenhuma falha, no maximo 1 foto ativa."""
    p0, _, _ = _trocar(usuario)
    ator = admin if por_admin else usuario
    novo = uuid.uuid4()
    barreira = threading.Barrier(2)
    conexoes = [h.conectar(), h.conectar()]  # abertas ANTES da barreira

    def executar(c: "psycopg.Connection", sql: str, params: tuple) -> tuple[str, object]:
        barreira.wait(timeout=60)
        try:
            row = c.execute(sql, params).fetchone()
            return "ok", row[0] if row else None
        except psycopg.Error as e:
            return "erro", e.sqlstate

    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            ft = ex.submit(
                executar,
                conexoes[0],
                "select id_anterior from public.trocar_foto_perfil(%s, %s, 'image/png', 10)",
                (usuario, novo),
            )
            fr = ex.submit(
                executar,
                conexoes[1],
                "select id_anterior from public.remover_foto_perfil_por(%s, %s)",
                (usuario, ator),
            )
            rt, rr = ft.result(timeout=90), fr.result(timeout=90)
    finally:
        for c in conexoes:
            c.close()

    assert rt[0] == "ok" and rr[0] == "ok", (rt, rr)
    ativas = [f[0] for f in _ativas(usuario)]
    if rr[1] == p0:  # remocao primeiro: a troca nao encontra foto e deixa a nova ativa
        assert rt[1] is None and ativas == [novo]
    else:  # troca primeiro: a remocao leva a foto NOVA
        assert rt[1] == p0 and rr[1] == novo and ativas == []
    assert len(_fotos(usuario)) == 2
    # cada foto removida gerou exatamente uma linha de remocao
    for f in _fotos(usuario):
        if f[4] is not None:
            assert [x[0] for x in _audit_foto(f[0])].count("foto_perfil_removida") == 1
    # a foto levada pela remocao concorrente tem o ator da remocao
    assert isinstance(rr[1], uuid.UUID)
    assert [x for x in _audit_foto(rr[1]) if x[0] == "foto_perfil_removida"][0][1] == ator


def test_erro_de_autorizacao_sem_detail_e_com_mensagem_fixa(usuario: uuid.UUID) -> None:
    ator = h.criar_usuario("sem-poder")
    with pytest.raises(psycopg.Error) as exc:
        _remover_por(usuario, ator)
    assert exc.value.diag.message_primary == "Remoção da foto de perfil não autorizada."
    assert not exc.value.diag.message_detail and not exc.value.diag.message_hint


def test_staging_tmp_do_bucket_tambem_e_negado_a_anon_e_authenticated(storage: None, usuario: uuid.UUID) -> None:
    """Convencao do staging avatares/tmp/<usuario_id>/<uuid>: a politica RESTRICTIVE ja nega
    anon/authenticated em todo o bucket, inclusive tmp/. Nada a mudar no banco."""
    nome = f"tmp/{usuario}/{uuid.uuid4()}"
    with h.conectar() as c:
        c.execute("insert into storage.objects (bucket_id, name) values ('avatares', %s)", (nome,))
    for papel in ("anon", "authenticated"):
        with h.como(papel, usuario if papel == "authenticated" else None) as c:
            (n,) = c.execute(
                "select count(*) from storage.objects where bucket_id='avatares' and name = %s", (nome,)
            ).fetchone() or (-1,)
            assert n == 0, papel
    # INSERT e UPDATE em tmp/<uuid>/<uuid> tambem sao negados (policy RESTRICTIVE vale para tmp/)
    for papel in ("anon", "authenticated"):
        uid = usuario if papel == "authenticated" else None
        with pytest.raises(psycopg.Error) as exc:
            with h.como(papel, uid) as c:
                c.execute(
                    "insert into storage.objects (bucket_id, name) values ('avatares', %s)",
                    (f"tmp/{uuid.uuid4()}/{uuid.uuid4()}",),
                )
        assert h.sqlstate(exc) == NEGADO, papel
        with h.como(papel, uid) as c:
            cur = c.execute(
                "update storage.objects set name = name || 'x' where bucket_id='avatares' and name = %s", (nome,)
            )
            assert cur.rowcount in (0, -1), papel  # RLS filtra: nenhuma linha visivel
    with h.conectar() as c:
        (n,) = c.execute("select count(*) from storage.objects where bucket_id='avatares' and name=%s", (nome,)).fetchone() or (0,)
    assert n == 1  # o objeto de staging continua intacto


def test_ordem_das_checagens_ator_antes_do_alvo() -> None:
    """Quem nao e dono nem admin ativo ve AG022 mesmo com alvo inexistente (nao enumera usuarios)."""
    for role in ("solicitante", "autorizador"):
        ator = h.criar_usuario(f"ordem-{role}", role)
        with pytest.raises(psycopg.Error) as exc:
            _remover_por(uuid.uuid4(), ator)
        assert h.sqlstate(exc) == AG_NAO_AUTORIZADO, role
    with pytest.raises(psycopg.Error) as exc2:
        _remover_por(uuid.uuid4(), uuid.uuid4())  # ator e alvo inexistentes
    assert h.sqlstate(exc2) == AG_NAO_AUTORIZADO
    dono_fantasma = uuid.uuid4()
    with pytest.raises(psycopg.Error) as exc3:
        _remover_por(dono_fantasma, dono_fantasma)  # ator = alvo, inexistente
    assert h.sqlstate(exc3) == "AG017"
    assert exc3.value.diag.message_primary == "Usuário inexistente."
    _sem_conteudo(exc3.value)


def test_admin_desativado_durante_a_espera_do_lock_e_recusado(usuario: uuid.UUID) -> None:
    """A checagem de admin ativo e repetida DEPOIS do advisory: admin desativado enquanto a chamada
    esperava o lock e recusado (AG022) e a foto continua ativa."""
    estavel = h.criar_usuario("admin-estavel", "admin")
    cai = h.criar_usuario("admin-que-cai", "admin")
    pid, _, _ = _trocar(usuario)
    resultado: list[object] = []

    def chamar() -> None:
        try:
            with h.conectar() as c:
                c.execute("set lock_timeout = '30s'")
                c.execute("select * from public.remover_foto_perfil_por(%s, %s)", (usuario, cai))
            resultado.append("ok")
        except psycopg.Error as e:
            resultado.append(e.sqlstate)

    with psycopg.connect(h.DSN) as seg:  # type: ignore[arg-type]
        seg.execute(
            "select pg_advisory_xact_lock(hashtextextended('sisveiculos.avatar:' || %s::text, 0))",
            (str(usuario),),
        )
        t = threading.Thread(target=chamar)
        t.start()
        time.sleep(2)  # a chamada passou a 1a checagem (admin ativo) e espera o advisory
        assert resultado == []
        h.alterar_perfil(cai, estavel, ativo=False)
        seg.rollback()  # solta o lock
        t.join(timeout=90)
    assert resultado == [AG_NAO_AUTORIZADO]
    assert [f[0] for f in _ativas(usuario)] == [pid]
