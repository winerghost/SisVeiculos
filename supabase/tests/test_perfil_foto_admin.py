"""Testes da migration 20261113120100: troca de foto de perfil pelo admin (trocar_foto_perfil_por).

Somente banco local/dev DESCARTAVEL (TEST_DATABASE_URL + TEST_DATABASE_CONFIRM=descartavel + marcador
no banco; sem eles os testes sao pulados). Ver dbhelpers.py. Auditoria e fotos sao imutaveis: os
testes nao limpam o que criam; usam usuarios e ids novos.

    pytest supabase/tests/test_perfil_foto_admin.py -m db_local -p no:xdist
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

TROCAR_POR = "public.trocar_foto_perfil_por(uuid, uuid, uuid, text, integer)"
MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "20261113120100_perfil_foto_troca_admin.sql"
)


@pytest.fixture
def admin() -> uuid.UUID:
    return h.criar_usuario("admin-foto-troca", "admin")


@pytest.fixture
def usuario() -> uuid.UUID:
    return h.criar_usuario("alvo-foto-troca")


def _trocar_por(
    usuario: uuid.UUID | None,
    ator: uuid.UUID | None,
    *,
    foto: uuid.UUID | None | str = "novo",
    mime: str | None = "image/png",
    tamanho: int | None = 1000,
) -> tuple[uuid.UUID | None, str | None]:
    """Chama a RPC; devolve (id_da_foto_nova, caminho_anterior)."""
    pid = uuid.uuid4() if foto == "novo" else foto
    with h.conectar() as c:
        row = c.execute(
            "select public.trocar_foto_perfil_por(%s, %s, %s, %s, %s)",
            (usuario, ator, pid, mime, tamanho),
        ).fetchone()
    assert row is not None
    return pid, row[0]  # type: ignore[return-value]


def _audit(pid: uuid.UUID) -> list[tuple]:
    with h.conectar() as c:
        return c.execute(
            "select acao, ator_id, detalhes from public.auditoria "
            "where tabela = 'perfil_fotos' and registro_id = %s order by id",
            (pid,),
        ).fetchall()


def _linha(pid: uuid.UUID) -> tuple:
    with h.conectar() as c:
        row = c.execute(
            "select criada_por, removida_em is not null, removida_por from public.perfil_fotos "
            "where id = %s",
            (pid,),
        ).fetchone()
    assert row is not None
    return row


def _erro(sql: str, params: tuple, estado: str) -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(sql, params)
    assert h.sqlstate(exc) == estado


def test_admin_troca_foto_de_outro_usuario_e_audita_o_admin(
    usuario: uuid.UUID, admin: uuid.UUID
) -> None:
    p1, anterior = _trocar_por(usuario, usuario, mime="image/jpeg", tamanho=11)
    assert anterior is None
    p2, anterior = _trocar_por(usuario, admin, mime="image/webp", tamanho=22)
    assert anterior == f"{usuario}/{p1}.jpg"
    assert _linha(p1) == (None, True, admin)  # anterior removida pelo admin
    assert _linha(p2) == (admin, False, None)  # nova criada pelo admin
    assert _audit(p2) == [
        (
            "foto_perfil_alterada",
            admin,
            {"mime": "image/webp", "tamanho_bytes": 22, "por_admin": True},
        )
    ]
    assert _audit(p1) == [
        (
            "foto_perfil_alterada",
            usuario,
            {"mime": "image/jpeg", "tamanho_bytes": 11, "por_admin": False},
        ),
        (
            "foto_perfil_removida",
            admin,
            {"mime": "image/jpeg", "tamanho_bytes": 11, "por_admin": True},
        ),
    ]
    with h.conectar() as c:
        ativas = c.execute(
            "select id from public.perfil_fotos where usuario_id = %s and removida_em is null",
            (usuario,),
        ).fetchall()
    assert [r[0] for r in ativas] == [p2]


def test_dono_trocando_a_propria_foto_segue_com_ator_dono(usuario: uuid.UUID) -> None:
    p1, _ = _trocar_por(usuario, usuario)
    p2, anterior = _trocar_por(usuario, usuario)
    assert anterior == f"{usuario}/{p1}.png"
    assert _linha(p1) == (None, True, None)
    assert _linha(p2) == (None, False, None)
    assert [(a, ator, d["por_admin"]) for a, ator, d in _audit(p1)] == [
        ("foto_perfil_alterada", usuario, False),
        ("foto_perfil_removida", usuario, False),
    ]
    assert [(a, ator, d["por_admin"]) for a, ator, d in _audit(p2)] == [
        ("foto_perfil_alterada", usuario, False)
    ]


def test_admin_trocando_a_propria_foto_nao_e_por_admin(admin: uuid.UUID) -> None:
    pid, _ = _trocar_por(admin, admin)
    assert _linha(pid) == (None, False, None)
    assert _audit(pid)[0][1:] == (
        admin,
        {"mime": "image/png", "tamanho_bytes": 1000, "por_admin": False},
    )


def test_nao_admin_nao_troca_foto_alheia(usuario: uuid.UUID) -> None:
    outro = h.criar_usuario("nao-admin-foto")
    aut = h.criar_usuario("aut-foto-troca", "autorizador")
    for ator in (outro, aut, uuid.uuid4()):  # inclusive ator inexistente
        with pytest.raises(psycopg.Error) as exc:
            _trocar_por(usuario, ator)
        assert h.sqlstate(exc) == "AG022"


def test_admin_inativo_nao_troca_foto_alheia(usuario: uuid.UUID, admin: uuid.UUID) -> None:
    inativo = h.criar_usuario("admin-foto-inativo", "admin", por=admin)
    h.alterar_perfil(inativo, admin, ativo=False)
    with pytest.raises(psycopg.Error) as exc:
        _trocar_por(usuario, inativo)
    assert h.sqlstate(exc) == "AG022"


def test_alvo_inexistente_ou_inativo(admin: uuid.UUID) -> None:
    inativo = h.criar_usuario("alvo-foto-inativo")
    h.alterar_perfil(inativo, admin, ativo=False)
    for alvo in (uuid.uuid4(), inativo):
        with pytest.raises(psycopg.Error) as exc:
            _trocar_por(alvo, admin)
        assert h.sqlstate(exc) == "AG017"


def test_falha_nao_deixa_efeito_parcial(usuario: uuid.UUID, admin: uuid.UUID) -> None:
    p1, _ = _trocar_por(usuario, usuario)
    with pytest.raises(psycopg.Error) as exc:
        _trocar_por(usuario, admin, mime="image/gif")  # recusado pelo gatilho no INSERT
    assert h.sqlstate(exc) == "23514"
    assert _linha(p1) == (None, False, None)  # a anterior continua ativa (rollback da RPC)


def test_argumentos_nulos(usuario: uuid.UUID, admin: uuid.UUID) -> None:
    casos = [
        dict(usuario=None, ator=admin),
        dict(usuario=usuario, ator=None),
        dict(usuario=usuario, ator=admin, foto=None),
        dict(usuario=usuario, ator=admin, mime=None),
        dict(usuario=usuario, ator=admin, tamanho=None),
    ]
    for caso in casos:
        with pytest.raises(psycopg.Error) as exc:
            _trocar_por(**caso)  # type: ignore[arg-type]
        assert h.sqlstate(exc) == "23502", caso


def test_criada_por_forjado_no_insert_e_recusado(usuario: uuid.UUID, admin: uuid.UUID) -> None:
    outro = h.criar_usuario("forjador-foto")
    inativo = h.criar_usuario("admin-foto-forja-inativo", "admin", por=admin)
    h.alterar_perfil(inativo, admin, ativo=False)
    aut = h.criar_usuario("aut-foto-forja", "autorizador")
    sql = (
        "insert into public.perfil_fotos (id, usuario_id, mime, tamanho_bytes, criada_por) "
        "values (%s, %s, 'image/png', 10, %s)"
    )
    for forjado in (outro, aut, inativo):
        _erro(sql, (uuid.uuid4(), usuario, forjado), "AG022")
    # inexistente: AG022 do gatilho (nao e admin ativo), antes da FK
    _erro(sql, (uuid.uuid4(), usuario, uuid.uuid4()), "AG022")
    with h.conectar() as c:  # nada foi gravado para o alvo
        (n,) = c.execute(
            "select count(*) from public.perfil_fotos where usuario_id = %s", (usuario,)
        ).fetchone() or (0,)
    assert n == 0


def test_criada_por_igual_ao_dono_vira_null(usuario: uuid.UUID) -> None:
    pid = uuid.uuid4()
    with h.conectar() as c:
        c.execute(
            "insert into public.perfil_fotos (id, usuario_id, mime, tamanho_bytes, criada_por) "
            "values (%s, %s, 'image/png', 10, %s)",
            (pid, usuario, usuario),
        )
    assert _linha(pid)[0] is None
    assert _audit(pid)[0][1:] == (
        usuario,
        {"mime": "image/png", "tamanho_bytes": 10, "por_admin": False},
    )


def test_criada_por_nao_muda_depois(usuario: uuid.UUID, admin: uuid.UUID) -> None:
    pid, _ = _trocar_por(usuario, admin)
    _erro("update public.perfil_fotos set criada_por = null where id = %s", (pid,), "AG003")


def test_execute_da_rpc_so_para_service_role() -> None:
    with h.conectar() as c:
        for papel in ("anon", "authenticated"):
            (ok,) = c.execute(
                "select has_function_privilege(%s, %s, 'execute')", (papel, TROCAR_POR)
            ).fetchone() or (True,)
            assert ok is False, papel
        (svc,) = c.execute(
            "select has_function_privilege('service_role', %s, 'execute')", (TROCAR_POR,)
        ).fetchone() or (False,)
        assert svc is True
        (publico,) = c.execute(
            "select exists (select 1 from pg_proc x, "
            "aclexplode(coalesce(x.proacl, acldefault('f', x.proowner))) a "
            "where x.oid = %s::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE')",
            (TROCAR_POR,),
        ).fetchone() or (True,)
        assert publico is False
    for papel in ("anon", "authenticated"):
        with pytest.raises(psycopg.Error) as exc:
            with h.como(papel) as c:
                c.execute(
                    "select public.trocar_foto_perfil_por(%s, %s, %s, 'image/png', 10)",
                    (uuid.uuid4(), uuid.uuid4(), uuid.uuid4()),
                )
        assert h.sqlstate(exc) == "42501"


def test_service_role_executa_a_rpc(usuario: uuid.UUID, admin: uuid.UUID) -> None:
    pid = uuid.uuid4()
    with h.como("service_role") as c:
        row = c.execute(
            "select public.trocar_foto_perfil_por(%s, %s, %s, 'image/png', 10)",
            (usuario, admin, pid),
        ).fetchone()
        assert row is not None and row[0] is None


def test_rpc_nao_escreve_em_perfis(usuario: uuid.UUID, admin: uuid.UUID) -> None:
    def xmins() -> list[tuple]:
        with h.conectar() as c:
            return c.execute(
                "select id, xmin::text from public.perfis where id in (%s, %s) order by id",
                (usuario, admin),
            ).fetchall()

    antes = xmins()
    _trocar_por(usuario, admin)
    assert xmins() == antes


def test_migration_e_idempotente() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    with h.reaplicando_migration() as c:  # reaplicar o SQL inteiro duas vezes nao pode falhar
        c.execute(sql)
        c.execute(sql)
        (n,) = c.execute(
            "select count(*) from information_schema.columns where table_schema = 'public' "
            "and table_name = 'perfil_fotos' and column_name = 'criada_por'"
        ).fetchone() or (0,)
    assert n == 1
