"""Testes das migrations 19-22: recurso Checklist (tabelas, fotos, storage, obrigatoriedade).

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.
Uma fixture autouse (_estado_recursos) le o estado de quilometragem, abastecimento_manutencao e
checklist, desliga os tres antes de cada teste (abastecimento -> km -> checklist) e restaura o
original no teardown (km -> abastecimento -> checklist). Cada alternancia gera linha
'configuracao_alterada' em auditoria (append-only, nao limpavel) e os checklists criados sao
imutaveis e sem DELETE: rodar SO em banco descartavel. A fixture pula (skip) se o DSN nao parecer
local; TEST_DATABASE_ALLOW_REMOTE=1 forca a execucao. Sem paralelismo (nao usar pytest-xdist).
Os testes de storage pulam se o schema storage nao existir.

    pytest supabase/tests/test_checklist.py -m db_local
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

psycopg = pytest.importorskip("psycopg")

from psycopg.conninfo import conninfo_to_dict  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

NEGADO = "42501"
CHECK = "23514"
UNICO = "23505"
FK = "23503"
MB5 = 5 * 1024 * 1024
TABELAS_ANTIGAS = ("perfis", "veiculos", "requisicoes", "auditoria", "configuracoes", "convites")


@dataclass
class Atores:
    dono: uuid.UUID
    autorizador: uuid.UUID
    admin: uuid.UUID


@pytest.fixture
def atores() -> Atores:
    return Atores(
        dono=h.criar_usuario("dono"),
        autorizador=h.criar_usuario("autorizador", "autorizador"),
        admin=h.criar_usuario("admin", "admin"),
    )


# --- fixture de flags ---------------------------------------------------------------------
def _ler_recursos() -> tuple[bool, bool, bool]:
    with h.conectar() as c:
        row = c.execute(
            "select quilometragem, abastecimento_manutencao, checklist from public.configuracoes"
        ).fetchone()
    assert row is not None
    return bool(row[0]), bool(row[1]), bool(row[2])


def _definir(coluna: str, ligado: bool, por: uuid.UUID) -> None:
    assert coluna in ("quilometragem", "abastecimento_manutencao", "checklist")
    with h.conectar() as c:
        c.execute(
            f"update public.configuracoes set {coluna} = %s, alterado_por = %s where id = true",
            (ligado, por),
        )


def _dsn_local() -> bool:
    if os.environ.get("TEST_DATABASE_ALLOW_REMOTE") == "1":
        return True
    host = conninfo_to_dict(h.DSN or "").get("host") or ""
    return host == "" or host.startswith("/") or host in ("localhost", "127.0.0.1", "::1")


@pytest.fixture(autouse=True)
def _estado_recursos() -> Iterator[None]:
    """Isola os flags globais: desliga antes (ab -> km -> checklist) e restaura depois."""
    if not _dsn_local():
        pytest.skip("DSN nao parece banco local/descartavel (TEST_DATABASE_ALLOW_REMOTE=1 forca)")
    orig_km, orig_ab, orig_chk = _ler_recursos()
    por = h.admin_ativo_qualquer() or h.criar_usuario("admin-cfg", "admin")
    if orig_ab:
        _definir("abastecimento_manutencao", False, por)
    if orig_km:
        _definir("quilometragem", False, por)
    if orig_chk:
        _definir("checklist", False, por)
    try:
        yield
    finally:
        atual_km, atual_ab, atual_chk = _ler_recursos()
        if atual_ab:  # nunca mexer em km com abastecimento ligado (AG010)
            _definir("abastecimento_manutencao", False, por)
        if atual_km != orig_km:
            _definir("quilometragem", orig_km, por)
        if orig_ab:
            _definir("abastecimento_manutencao", True, por)
        if atual_chk != orig_chk:
            _definir("checklist", orig_chk, por)


def _ligar(coluna: str, admin: uuid.UUID) -> None:
    _definir(coluna, True, admin)


# --- helpers de dominio -------------------------------------------------------------------
def _autorizada(a: Atores) -> uuid.UUID:
    with h.conectar() as c:
        rid = h.inserir_requisicao(c, a.dono, h.novo_veiculo())
    h.autorizar(rid, a.autorizador)
    return rid


def _em_uso(a: Atores) -> uuid.UUID:
    rid = _autorizada(a)
    h.iniciar(rid, a.autorizador)
    return rid


def _foto(mime: str = "image/jpeg", tamanho: int = 1000) -> dict[str, object]:
    return {"id": str(uuid.uuid4()), "mime_type": mime, "tamanho_bytes": tamanho}


def _registrar(
    rid: uuid.UUID,
    momento: str,
    por: uuid.UUID,
    *,
    combustivel: str = "meio",
    avarias: bool = False,
    descricao: str | None = None,
    fotos: list[dict[str, object]] | None = None,
) -> uuid.UUID:
    with h.conectar() as c:
        row = c.execute(
            "select public.registrar_checklist(%s, %s, %s::public.nivel_combustivel, %s, %s, %s, %s)",
            (rid, momento, combustivel, avarias, descricao, por, Jsonb(fotos or [])),
        ).fetchone()
    assert row is not None
    return row[0]


def _inserir_direto(
    rid: uuid.UUID,
    momento: str,
    por: uuid.UUID,
    *,
    combustivel: str = "meio",
    avarias: bool = False,
    descricao: str | None = None,
) -> None:
    with h.conectar() as c:
        c.execute(
            "insert into public.checklists (requisicao_id, momento, combustivel, avarias, "
            "descricao_avarias, registrado_por) values (%s, %s, %s, %s, %s, %s)",
            (rid, momento, combustivel, avarias, descricao, por),
        )


def _erro(sql: str, params: tuple, esperado: str, contem: str | None = None) -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(sql, params)
    assert h.sqlstate(exc) == esperado, str(exc.value)
    if contem:
        assert contem in str(exc.value)


def _falha_ao_registrar(esperado: str, contem: str | None = None, **kw: object) -> None:
    with pytest.raises(psycopg.Error) as exc:
        _registrar(**kw)  # type: ignore[arg-type]
    assert h.sqlstate(exc) == esperado, str(exc.value)
    if contem:
        assert contem in str(exc.value)


def _fotos_de(rid: uuid.UUID, momento: str) -> list[tuple]:
    with h.conectar() as c:
        return c.execute(
            "select id, posicao, mime_type, tamanho_bytes, caminho from public.checklist_fotos "
            "where requisicao_id=%s and momento=%s order by posicao",
            (rid, momento),
        ).fetchall()


# --- estrutura ------------------------------------------------------------------------------
def test_flag_checklist_desligado_por_padrao_e_gatilhos_sempre_ativos() -> None:
    with h.conectar() as c:
        (padrao,) = c.execute(
            "select column_default from information_schema.columns "
            "where table_schema='public' and table_name='configuracoes' and column_name='checklist'"
        ).fetchone() or (None,)
        rows = dict(
            c.execute(
                "select tgname, tgenabled from pg_trigger "
                "where not tgisinternal and tgrelid in ('public.checklists'::regclass, "
                "'public.checklist_fotos'::regclass, 'public.requisicoes'::regclass) "
                "and (tgname like '%checklist%')"
            ).fetchall()
        )
    assert padrao == "false"
    esperados = {
        "trg_checklists_imutavel",
        "trg_checklists_sem_truncate",
        "trg_checklists_validar",
        "trg_checklists_auditoria",
        "trg_checklist_fotos_imutavel",
        "trg_checklist_fotos_sem_truncate",
        "trg_checklist_fotos_validar",
        "trg_requisicoes_validar_pos_km_checklist",
    }
    assert esperados <= set(rows), set(rows)
    assert all(rows[n] == "A" for n in esperados)  # ENABLE ALWAYS


def test_ordem_dos_gatilhos_de_requisicoes() -> None:
    with h.conectar() as c:
        todos = [
            r[0]
            for r in c.execute(
                "select tgname from pg_trigger where tgrelid = 'public.requisicoes'::regclass "
                "and not tgisinternal and (tgtype & 2) = 2 order by tgname"
            ).fetchall()
        ]
    assert todos.index("trg_requisicoes_validar") < todos.index("trg_requisicoes_validar_km")
    assert todos.index("trg_requisicoes_validar_km") < todos.index(
        "trg_requisicoes_validar_pos_km_checklist"
    )


# --- obrigatoriedade: recurso DESLIGADO ---------------------------------------------------------
def test_desligado_banco_nao_exige_checklist(atores: Atores) -> None:
    rid = _autorizada(atores)
    h.iniciar(rid, atores.autorizador)
    h.concluir(rid, atores.autorizador)
    assert h.status_de(rid) == "concluida"


# --- obrigatoriedade: recurso LIGADO ------------------------------------------------------------
def test_ligado_saida_exige_checklist_de_saida(atores: Atores) -> None:
    rid = _autorizada(atores)
    _ligar("checklist", atores.admin)
    with pytest.raises(psycopg.Error) as exc:
        h.iniciar(rid, atores.autorizador)
    assert h.sqlstate(exc) == "AG013"
    assert h.status_de(rid) == "autorizada"
    _registrar(rid, "saida", atores.autorizador)
    h.iniciar(rid, atores.autorizador)
    assert h.status_de(rid) == "em_uso"


def test_ligado_checklist_de_chegada_nao_substitui_o_de_saida(atores: Atores) -> None:
    rid = _autorizada(atores)
    _ligar("checklist", atores.admin)
    # 'chegada' nao pode ser registrado com a requisicao autorizada (janela)
    _falha_ao_registrar(
        "AG014", rid=rid, momento="chegada", por=atores.autorizador
    )
    with pytest.raises(psycopg.Error) as exc:
        h.iniciar(rid, atores.autorizador)
    assert h.sqlstate(exc) == "AG013"


def test_ligado_chegada_exige_checklist_de_chegada(atores: Atores) -> None:
    rid = _autorizada(atores)
    _ligar("checklist", atores.admin)
    _registrar(rid, "saida", atores.autorizador)
    h.iniciar(rid, atores.autorizador)
    with pytest.raises(psycopg.Error) as exc:
        h.concluir(rid, atores.autorizador)
    assert h.sqlstate(exc) == "AG013"
    assert h.status_de(rid) == "em_uso"
    _registrar(rid, "chegada", atores.autorizador)
    h.concluir(rid, atores.autorizador)
    assert h.status_de(rid) == "concluida"


def test_borda_ligado_com_viagem_ja_em_uso_sem_checklist_de_saida(atores: Atores) -> None:
    """Regra de borda: so o de chegada e exigido; o de saida nao pode mais ser registrado."""
    rid = _em_uso(atores)  # recurso desligado
    _ligar("checklist", atores.admin)
    _falha_ao_registrar("AG014", rid=rid, momento="saida", por=atores.autorizador)
    with pytest.raises(psycopg.Error) as exc:
        h.concluir(rid, atores.autorizador)
    assert h.sqlstate(exc) == "AG013"
    _registrar(rid, "chegada", atores.autorizador)
    h.concluir(rid, atores.autorizador)
    assert h.status_de(rid) == "concluida"


def test_desligar_nao_apaga_e_volta_a_nao_exigir(atores: Atores) -> None:
    rid = _autorizada(atores)
    _ligar("checklist", atores.admin)
    cid = _registrar(rid, "saida", atores.autorizador, fotos=[_foto()])
    _definir("checklist", False, atores.admin)
    with h.conectar() as c:
        (n,) = c.execute("select count(*) from public.checklists where id=%s", (cid,)).fetchone() or (0,)
    assert n == 1
    assert len(_fotos_de(rid, "saida")) == 1
    rid2 = _autorizada(atores)
    h.iniciar(rid2, atores.autorizador)  # sem checklist, recurso desligado
    assert h.status_de(rid2) == "em_uso"


def test_prioridade_ator_invalido_antes_do_checklist(atores: Atores) -> None:
    rid = _autorizada(atores)
    inativo = h.criar_usuario("inativo", "autorizador", ativo=False)
    _ligar("checklist", atores.admin)
    with pytest.raises(psycopg.Error) as exc:
        h.iniciar(rid, inativo)
    assert h.sqlstate(exc) == "AG002"  # trg_requisicoes_validar antes do checklist


def test_prioridade_km_antes_do_checklist(atores: Atores) -> None:
    rid = _autorizada(atores)
    _ligar("quilometragem", atores.admin)
    _ligar("checklist", atores.admin)
    with pytest.raises(psycopg.Error) as exc:
        h.iniciar(rid, atores.autorizador)  # sem km e sem checklist
    assert h.sqlstate(exc) == "AG012"


def test_cancelar_nao_exige_checklist(atores: Atores) -> None:
    rid = _autorizada(atores)
    _ligar("checklist", atores.admin)
    _registrar(rid, "saida", atores.autorizador)
    h.cancelar(rid, atores.autorizador)
    assert h.status_de(rid) == "cancelada"


# --- janela e ator -----------------------------------------------------------------------------
def test_saida_so_com_requisicao_autorizada(atores: Atores) -> None:
    with h.conectar() as c:
        rid = h.inserir_requisicao(c, atores.dono, h.novo_veiculo())  # solicitada
    _falha_ao_registrar("AG014", rid=rid, momento="saida", por=atores.autorizador)


def test_chegada_so_com_requisicao_em_uso(atores: Atores) -> None:
    rid = _autorizada(atores)
    _falha_ao_registrar("AG014", rid=rid, momento="chegada", por=atores.autorizador)


def test_nao_registra_em_requisicao_encerrada(atores: Atores) -> None:
    rid = _em_uso(atores)
    h.concluir(rid, atores.autorizador)
    _falha_ao_registrar("AG014", rid=rid, momento="chegada", por=atores.autorizador)


def test_solicitante_nao_registra_checklist(atores: Atores) -> None:
    rid = _autorizada(atores)
    _falha_ao_registrar("AG002", rid=rid, momento="saida", por=atores.dono)


def test_ator_inativo_nao_registra_checklist(atores: Atores) -> None:
    rid = _autorizada(atores)
    inativo = h.criar_usuario("inativo2", "autorizador", ativo=False)
    _falha_ao_registrar("AG002", rid=rid, momento="saida", por=inativo)


def test_admin_registra_checklist(atores: Atores) -> None:
    rid = _autorizada(atores)
    _registrar(rid, "saida", atores.admin)


def test_requisicao_inexistente(atores: Atores) -> None:
    _falha_ao_registrar(FK, rid=uuid.uuid4(), momento="saida", por=atores.autorizador)


# --- unicidade, campos e CHECKs ---------------------------------------------------------------
def test_unico_por_requisicao_e_momento(atores: Atores) -> None:
    rid = _autorizada(atores)
    _registrar(rid, "saida", atores.autorizador)
    _falha_ao_registrar(UNICO, rid=rid, momento="saida", por=atores.autorizador)


def test_momentos_diferentes_convivem(atores: Atores) -> None:
    rid = _autorizada(atores)
    _registrar(rid, "saida", atores.autorizador)
    h.iniciar(rid, atores.autorizador)
    _registrar(rid, "chegada", atores.autorizador)


def test_momento_invalido(atores: Atores) -> None:
    rid = _autorizada(atores)
    _falha_ao_registrar(CHECK, "ck_checklists_momento", rid=rid, momento="meio", por=atores.autorizador)


@pytest.mark.parametrize("nivel", ["vazio", "um_quarto", "meio", "tres_quartos", "cheio"])
def test_combustivel_aceita_niveis_do_enum(atores: Atores, nivel: str) -> None:
    rid = _autorizada(atores)
    _registrar(rid, "saida", atores.autorizador, combustivel=nivel)


def test_combustivel_fora_do_enum(atores: Atores) -> None:
    rid = _autorizada(atores)
    _falha_ao_registrar(
        "22P02", rid=rid, momento="saida", por=atores.autorizador, combustivel="reserva"
    )


def test_avarias_exige_descricao(atores: Atores) -> None:
    rid = _autorizada(atores)
    for descricao in (None, "", "   "):
        _falha_ao_registrar(
            CHECK,
            "ck_checklists_avarias_descricao",
            rid=rid,
            momento="saida",
            por=atores.autorizador,
            avarias=True,
            descricao=descricao,
        )
    _registrar(rid, "saida", atores.autorizador, avarias=True, descricao="Arranhao na porta direita")


def test_sem_avarias_nao_aceita_descricao(atores: Atores) -> None:
    rid = _autorizada(atores)
    _falha_ao_registrar(
        CHECK,
        "ck_checklists_avarias_descricao",
        rid=rid,
        momento="saida",
        por=atores.autorizador,
        avarias=False,
        descricao="texto solto",
    )


def test_descricao_maxima_500(atores: Atores) -> None:
    rid = _autorizada(atores)
    _falha_ao_registrar(
        CHECK,
        "ck_checklists_descricao_tamanho",
        rid=rid,
        momento="saida",
        por=atores.autorizador,
        avarias=True,
        descricao="x" * 501,
    )
    _registrar(rid, "saida", atores.autorizador, avarias=True, descricao="x" * 500)


def test_carimbo_do_servidor_sobrescreve_registrado_em(atores: Atores) -> None:
    rid = _autorizada(atores)
    with h.conectar() as c:
        c.execute(
            "insert into public.checklists (requisicao_id, momento, combustivel, avarias, "
            "registrado_por, registrado_em) values (%s, 'saida', 'meio', false, %s, '2000-01-01')",
            (rid, atores.autorizador),
        )
        (ano,) = c.execute(
            "select extract(year from registrado_em)::int from public.checklists "
            "where requisicao_id=%s and momento='saida'",
            (rid,),
        ).fetchone() or (0,)
    assert ano >= 2026


# --- fotos ------------------------------------------------------------------------------------
def test_ate_seis_fotos(atores: Atores) -> None:
    rid = _autorizada(atores)
    fotos = [_foto() for _ in range(6)]
    _registrar(rid, "saida", atores.autorizador, fotos=fotos)
    rows = _fotos_de(rid, "saida")
    assert [r[1] for r in rows] == [1, 2, 3, 4, 5, 6]
    assert {str(r[0]) for r in rows} == {f["id"] for f in fotos}


def test_setima_foto_recusada_e_nada_e_gravado(atores: Atores) -> None:
    rid = _autorizada(atores)
    _falha_ao_registrar(
        CHECK,
        "ck_checklist_fotos_posicao",
        rid=rid,
        momento="saida",
        por=atores.autorizador,
        fotos=[_foto() for _ in range(7)],
    )
    with h.conectar() as c:
        (n,) = c.execute("select count(*) from public.checklists where requisicao_id=%s", (rid,)).fetchone() or (
            -1,
        )
    assert n == 0  # atomico: o checklist tambem foi desfeito
    assert _fotos_de(rid, "saida") == []


def test_posicao_duplicada_recusada(atores: Atores) -> None:
    rid = _autorizada(atores)
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            with c.transaction():
                _insere_checklist_na_conexao(c, rid, atores.autorizador)
                for _ in range(2):
                    c.execute(
                        "insert into public.checklist_fotos (requisicao_id, momento, posicao, mime_type, "
                        "tamanho_bytes) values (%s, 'saida', 1, 'image/png', 10)",
                        (rid,),
                    )
    assert h.sqlstate(exc) == UNICO


def _insere_checklist_na_conexao(c: "psycopg.Connection", rid: uuid.UUID, por: uuid.UUID) -> None:
    c.execute(
        "insert into public.checklists (requisicao_id, momento, combustivel, avarias, registrado_por) "
        "values (%s, 'saida', 'meio', false, %s)",
        (rid, por),
    )


@pytest.mark.parametrize("mime", ["image/gif", "application/pdf", "image/svg+xml", "IMAGE/JPEG", ""])
def test_mime_restrito(atores: Atores, mime: str) -> None:
    rid = _autorizada(atores)
    _falha_ao_registrar(
        CHECK,
        "ck_checklist_fotos_mime",
        rid=rid,
        momento="saida",
        por=atores.autorizador,
        fotos=[_foto(mime=mime)],
    )


@pytest.mark.parametrize("mime", ["image/jpeg", "image/png", "image/webp"])
def test_mime_aceitos(atores: Atores, mime: str) -> None:
    rid = _autorizada(atores)
    _registrar(rid, "saida", atores.autorizador, fotos=[_foto(mime=mime)])


@pytest.mark.parametrize("tamanho", [0, -1, MB5 + 1])
def test_tamanho_fora_do_limite(atores: Atores, tamanho: int) -> None:
    rid = _autorizada(atores)
    _falha_ao_registrar(
        CHECK,
        "ck_checklist_fotos_tamanho",
        rid=rid,
        momento="saida",
        por=atores.autorizador,
        fotos=[_foto(tamanho=tamanho)],
    )


@pytest.mark.parametrize("tamanho", [1, MB5])
def test_tamanho_nos_limites_vale(atores: Atores, tamanho: int) -> None:
    rid = _autorizada(atores)
    _registrar(rid, "saida", atores.autorizador, fotos=[_foto(tamanho=tamanho)])


def test_caminho_gerado_pela_convencao(atores: Atores) -> None:
    rid = _autorizada(atores)
    fotos = [_foto("image/jpeg"), _foto("image/png"), _foto("image/webp")]
    _registrar(rid, "saida", atores.autorizador, fotos=fotos)
    rows = _fotos_de(rid, "saida")
    exts = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
    for fid, _, mime, _, caminho in rows:
        assert caminho == f"{rid}/saida/{fid}.{exts[mime]}"
        assert re.fullmatch(r"[0-9a-f-]{36}/(saida|chegada)/[0-9a-f-]{36}\.(jpg|png|webp)", caminho)


def test_caminho_nao_e_informavel(atores: Atores) -> None:
    rid = _autorizada(atores)
    outra = _autorizada(atores)
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            with c.transaction():
                _insere_checklist_na_conexao(c, rid, atores.autorizador)
                c.execute(
                    "insert into public.checklist_fotos (requisicao_id, momento, posicao, mime_type, "
                    "tamanho_bytes, caminho) values (%s, 'saida', 1, 'image/png', 10, %s)",
                    (rid, f"{outra}/saida/{uuid.uuid4()}.png"),
                )
    assert h.sqlstate(exc) == "428C9"  # coluna gerada


def test_foto_de_checklist_inexistente(atores: Atores) -> None:
    rid = _autorizada(atores)
    _erro(
        "insert into public.checklist_fotos (requisicao_id, momento, posicao, mime_type, tamanho_bytes) "
        "values (%s, 'saida', 1, 'image/png', 10)",
        (rid,),
        FK,
    )


def test_foto_nao_entra_depois_da_criacao_do_checklist(atores: Atores) -> None:
    rid = _autorizada(atores)
    _registrar(rid, "saida", atores.autorizador)  # transacao propria, ja encerrada
    _erro(
        "insert into public.checklist_fotos (requisicao_id, momento, posicao, mime_type, tamanho_bytes) "
        "values (%s, 'saida', 1, 'image/png', 10)",
        (rid,),
        "AG014",
    )


def test_foto_id_repetido_recusado(atores: Atores) -> None:
    rid = _autorizada(atores)
    f = _foto()
    _falha_ao_registrar(
        UNICO, rid=rid, momento="saida", por=atores.autorizador, fotos=[f, dict(f)]
    )


# --- imutabilidade ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql",
    [
        "update public.checklists set combustivel = 'cheio' where id = %s",
        "update public.checklists set avarias = false where id = %s",
        "delete from public.checklists where id = %s",
    ],
)
def test_checklist_imutavel(atores: Atores, sql: str) -> None:
    rid = _autorizada(atores)
    cid = _registrar(rid, "saida", atores.autorizador, avarias=True, descricao="Risco no para-choque")
    _erro(sql, (cid,), "AG003")


@pytest.mark.parametrize(
    "sql",
    [
        "update public.checklist_fotos set tamanho_bytes = 1 where requisicao_id = %s",
        "delete from public.checklist_fotos where requisicao_id = %s",
    ],
)
def test_fotos_imutaveis(atores: Atores, sql: str) -> None:
    rid = _autorizada(atores)
    _registrar(rid, "saida", atores.autorizador, fotos=[_foto()])
    _erro(sql, (rid,), "AG003")


@pytest.mark.parametrize("tabela", ["checklists", "checklist_fotos"])
def test_sem_truncate(tabela: str) -> None:
    # checklists e referenciada por FK (fotos): o Postgres pode recusar (0A000) antes do gatilho.
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(f"truncate table public.{tabela}")
    assert h.sqlstate(exc) in ("AG003", "0A000")


def test_requisicao_com_checklist_nao_e_apagavel(atores: Atores) -> None:
    rid = _autorizada(atores)
    _registrar(rid, "saida", atores.autorizador)
    _erro("delete from public.requisicoes where id = %s", (rid,), "AG003")


# --- auditoria sem PII ----------------------------------------------------------------------------
def test_auditoria_do_checklist_sem_dado_pessoal(atores: Atores) -> None:
    rid = _autorizada(atores)
    cid = _registrar(
        rid,
        "saida",
        atores.autorizador,
        combustivel="cheio",
        avarias=True,
        descricao="Amassado no capo, falar com Maria 11 99999-0000",
        fotos=[_foto(), _foto("image/png")],
    )
    with h.conectar() as c:
        rows = c.execute(
            "select tabela, acao, ator_id, de, para, detalhes from public.auditoria "
            "where registro_id=%s and tabela='checklists'",
            (cid,),
        ).fetchall()
    assert len(rows) == 1
    tabela, acao, ator, de, para, det = rows[0]
    assert (tabela, acao, ator, de, para) == ("checklists", "checklist_registrado", atores.autorizador, None, "saida")
    assert det == {"requisicao_id": str(rid), "momento": "saida", "avarias": True, "fotos": 2}
    texto = str(rows[0])
    assert "Maria" not in texto and "99999" not in texto and "cheio" not in texto


def test_auditoria_sem_fotos_conta_zero(atores: Atores) -> None:
    rid = _autorizada(atores)
    cid = _registrar(rid, "saida", atores.autorizador)
    with h.conectar() as c:
        (det,) = c.execute(
            "select detalhes from public.auditoria where registro_id=%s and tabela='checklists'", (cid,)
        ).fetchone() or ({},)
    assert det["fotos"] == 0 and det["avarias"] is False


def test_checklist_recusado_nao_deixa_auditoria(atores: Atores) -> None:
    rid = _autorizada(atores)
    _falha_ao_registrar("AG014", rid=rid, momento="chegada", por=atores.autorizador)
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.auditoria where tabela='checklists' "
            "and detalhes->>'requisicao_id' = %s",
            (str(rid),),
        ).fetchone() or (-1,)
    assert n == 0


def test_auditoria_continua_append_only() -> None:
    for sql in ("update public.auditoria set acao = acao", "delete from public.auditoria"):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(sql)
        assert h.sqlstate(exc) in ("AG003", NEGADO)


def test_registrar_auditoria_do_backend_continua_restrito() -> None:
    """A funcao do backend nao aceita a acao nova (ela so sai do trigger)."""
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("select public.registrar_auditoria('checklists', null, 'checklist_registrado', null)")
    assert h.sqlstate(exc) == "AG003"


# --- RLS e grants ---------------------------------------------------------------------------------
@pytest.mark.parametrize("tabela", ["checklists", "checklist_fotos"])
def test_rls_ligado_e_sem_privilegios_diretos(tabela: str) -> None:
    with h.conectar() as c:
        (rls,) = c.execute(
            "select relrowsecurity from pg_class where oid = %s::regclass", (f"public.{tabela}",)
        ).fetchone() or (False,)
        assert rls is True
        for papel in ("anon", "authenticated"):
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                (ok,) = c.execute(
                    "select has_table_privilege(%s, %s, %s)", (papel, f"public.{tabela}", priv)
                ).fetchone() or (True,)
                assert ok is False, (papel, priv)
        # service_role: so SELECT
        for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            (ok,) = c.execute(
                "select has_table_privilege('service_role', %s, %s)", (f"public.{tabela}", priv)
            ).fetchone() or (True,)
            assert ok is False, priv
        (sel,) = c.execute(
            "select has_table_privilege('service_role', %s, 'SELECT')", (f"public.{tabela}",)
        ).fetchone() or (False,)
        assert sel is True


@pytest.mark.parametrize("tabela", ["checklists", "checklist_fotos"])
@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_api_nao_le_nem_escreve_checklists(atores: Atores, tabela: str, papel: str) -> None:
    uid = atores.admin if papel == "authenticated" else None
    for sql in (
        f"select * from public.{tabela}",
        f"update public.{tabela} set momento = momento",
        f"delete from public.{tabela}",
    ):
        with pytest.raises(psycopg.Error) as exc:
            with h.como(papel, uid) as c:
                c.execute(sql)
        assert h.sqlstate(exc) == NEGADO, sql


def test_service_role_nao_escreve_direto_mas_chama_a_funcao(atores: Atores) -> None:
    rid = _autorizada(atores)
    with pytest.raises(psycopg.Error) as exc:
        with h.como("service_role") as c:
            c.execute(
                "insert into public.checklists (requisicao_id, momento, combustivel, avarias, "
                "registrado_por) values (%s, 'saida', 'meio', false, %s)",
                (rid, atores.autorizador),
            )
    assert h.sqlstate(exc) == NEGADO
    with h.como("service_role") as c:
        row = c.execute(
            "select public.registrar_checklist(%s, 'saida', 'meio', false, null, %s, '[]'::jsonb)",
            (rid, atores.autorizador),
        ).fetchone()
        assert row is not None
        (n,) = c.execute("select count(*) from public.checklists where id=%s", (row[0],)).fetchone() or (0,)
        assert n == 1  # (rollback ao sair)


@pytest.mark.parametrize(
    "assinatura",
    [
        "public.registrar_checklist(uuid, text, public.nivel_combustivel, boolean, text, uuid, jsonb)",
        "public.checklists_validar()",
        "public.checklist_fotos_validar()",
        "public.checklists_auditar()",
        "public.requisicoes_validar_checklist()",
    ],
)
def test_funcoes_nao_executaveis_por_public_anon_authenticated(assinatura: str) -> None:
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
        if "registrar_checklist" not in assinatura:  # B9: funcoes-gatilho nem para service_role
            (ok,) = c.execute(
                "select has_function_privilege('service_role', %s::regprocedure, 'execute')", (assinatura,)
            ).fetchone() or (True,)
            assert ok is False, assinatura


def test_registrar_checklist_so_service_role_executa() -> None:
    sig = "public.registrar_checklist(uuid, text, public.nivel_combustivel, boolean, text, uuid, jsonb)"
    with h.conectar() as c:
        (ok,) = c.execute("select has_function_privilege('service_role', %s::regprocedure, 'execute')", (sig,)).fetchone() or (
            False,
        )
    assert ok is True


def test_funcoes_security_definer_com_search_path_vazio() -> None:
    with h.conectar() as c:
        rows = c.execute(
            "select proname, prosecdef, proconfig from pg_proc where pronamespace = 'public'::regnamespace "
            "and proname in ('registrar_checklist', 'checklists_validar', 'checklist_fotos_validar', "
            "'checklists_auditar', 'requisicoes_validar_checklist')"
        ).fetchall()
    assert len(rows) == 5
    for nome, secdef, config in rows:
        assert secdef is True, nome
        assert config and any(x.replace(" ", "") in ("search_path=", 'search_path=""') for x in config), (nome, config)


@pytest.mark.parametrize("tabela", TABELAS_ANTIGAS)
def test_tabelas_antigas_continuam_fechadas(tabela: str) -> None:
    with h.conectar() as c:
        (rls,) = c.execute(
            "select relrowsecurity from pg_class where oid = %s::regclass", (f"public.{tabela}",)
        ).fetchone() or (False,)
        assert rls is True
        for papel in ("anon", "authenticated"):
            (ok,) = c.execute(
                "select has_table_privilege(%s, %s, 'select, insert, update, delete')",
                (papel, f"public.{tabela}"),
            ).fetchone() or (True,)
            assert ok is False, (papel, tabela)
        # service_role sem DELETE/TRUNCATE/TRIGGER/REFERENCES (auditoria: nem INSERT/UPDATE direto)
        privs = ("DELETE", "TRUNCATE", "TRIGGER", "REFERENCES")
        if tabela == "auditoria":
            privs += ("INSERT", "UPDATE")
        for priv in privs:
            (ok,) = c.execute(
                "select has_table_privilege('service_role', %s, %s)", (f"public.{tabela}", priv)
            ).fetchone() or (True,)
            assert ok is False, (tabela, priv)


# --- storage -------------------------------------------------------------------------------------
def _tem_storage() -> bool:
    with h.conectar() as c:
        (r,) = c.execute("select to_regclass('storage.objects') is not null").fetchone() or (False,)
    return bool(r)


@pytest.fixture
def storage() -> None:
    if not _tem_storage():
        pytest.skip("schema storage ausente (use `supabase start`)")


def test_bucket_privado_com_limites(storage: None) -> None:
    with h.conectar() as c:
        row = c.execute(
            "select public, file_size_limit, allowed_mime_types from storage.buckets where id='checklists'"
        ).fetchone()
    assert row is not None
    assert row[0] is False
    assert row[1] == MB5
    assert sorted(row[2]) == ["image/jpeg", "image/png", "image/webp"]


def _objeto_no_bucket() -> str:
    nome = f"teste/{uuid.uuid4()}.png"
    with h.conectar() as c:
        # storage.objects nao aceita DELETE direto; nome unico dispensa limpeza.
        c.execute("insert into storage.objects (bucket_id, name) values ('checklists', %s)", (nome,))
    return nome


@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_storage_nega_tudo_a_anon_e_authenticated(storage: None, atores: Atores, papel: str) -> None:
    nome = _objeto_no_bucket()
    uid = atores.admin if papel == "authenticated" else None
    with h.como(papel, uid) as c:
        (n,) = c.execute("select count(*) from storage.objects where bucket_id='checklists'").fetchone() or (-1,)
        assert n == 0
    for sql in (
        "insert into storage.objects (bucket_id, name) values ('checklists', %s)",
        "update storage.objects set name = name || 'x' where bucket_id='checklists' and name <> %s",
    ):
        try:
            with h.como(papel, uid) as c:
                cur = c.execute(sql, (f"novo/{uuid.uuid4()}",))
                assert cur.rowcount in (0, -1), sql  # update: nenhuma linha visivel
        except psycopg.Error as e:
            assert getattr(e, "sqlstate", None) in (NEGADO, "42501"), e
    assert nome  # o objeto de teste existe para o postgres, nao para a API


def test_storage_politica_restritiva_vence_politica_permissiva_ampla(storage: None, atores: Atores) -> None:
    """Mesmo com uma politica permissiva 'tudo liberado', o bucket checklists segue fechado."""
    _objeto_no_bucket()
    with psycopg.connect(h.DSN) as conn:  # type: ignore[arg-type]
        try:
            conn.execute(
                "create policy tmp_libera_tudo on storage.objects for all to authenticated "
                "using (true) with check (true)"
            )
            conn.execute("set local role authenticated")
            conn.execute(
                "select set_config('request.jwt.claims', %s, true)",
                (f'{{"sub": "{atores.admin}", "role": "authenticated"}}',),
            )
            (n,) = conn.execute("select count(*) from storage.objects where bucket_id='checklists'").fetchone() or (-1,)
            assert n == 0
        finally:
            conn.rollback()


def test_storage_politica_restritiva_existe(storage: None) -> None:
    with h.conectar() as c:
        row = c.execute(
            "select permissive, roles::text[] from pg_policies where schemaname='storage' "
            "and tablename='objects' and policyname='checklists_bucket_sem_acesso_api'"
        ).fetchone()
    assert row is not None
    assert row[0] == "RESTRICTIVE"
    assert set(row[1]) == {"anon", "authenticated"}


# --- migration 23: correcoes da revisao de seguranca ------------------------------------------------
SEGREDO = "SEGREDO-PESSOAL-Maria-99999"
ACOES_AUDITORIA = (
    "criada", "transicao", "convite_criado", "convite_usado", "convite_revogado",
    "perfil_criado", "perfil_alterado", "perfil_unidade_alterada", "admin_semeado",
    "configuracao_alterada", "veiculo_criado", "veiculo_alterado",
    "acao_administrativa", "acesso_negado", "checklist_registrado",
)  # fmt: skip
TABELAS_AUDITORIA = ("requisicoes", "convites", "perfis", "veiculos", "configuracoes", "checklists")


def _erro_sem_conteudo(exc: "pytest.ExceptionInfo[psycopg.Error]") -> None:
    """M1: nem a mensagem, nem o DETAIL, nem a linha inteira carregam a descricao."""
    diag = exc.value.diag
    assert SEGREDO not in str(exc.value)
    assert SEGREDO not in (diag.message_primary or "")
    assert SEGREDO not in (diag.message_detail or "")
    assert "Failing row" not in str(exc.value)
    assert not diag.message_detail


@pytest.mark.parametrize(
    ("avarias", "descricao", "constraint"),
    [
        (True, SEGREDO + "x" * 500, "ck_checklists_descricao_tamanho"),  # > 500
        (False, SEGREDO, "ck_checklists_avarias_descricao"),  # sem avarias, com texto
        (True, " \n\t\r ", "ck_checklists_avarias_descricao"),  # B1: so espaco em branco
        (True, "\n\t", "ck_checklists_avarias_descricao"),  # B1
        (True, None, "ck_checklists_avarias_descricao"),
    ],
)
def test_m1_erro_de_validacao_sem_conteudo_da_descricao(
    atores: Atores, avarias: bool, descricao: str | None, constraint: str
) -> None:
    rid = _autorizada(atores)
    with pytest.raises(psycopg.Error) as exc:
        _registrar(rid, "saida", atores.autorizador, avarias=avarias, descricao=descricao)
    assert h.sqlstate(exc) == CHECK
    assert exc.value.diag.constraint_name == constraint
    _erro_sem_conteudo(exc)


def test_m1_momento_invalido_e_nulos_sem_conteudo(atores: Atores) -> None:
    rid = _autorizada(atores)
    with pytest.raises(psycopg.Error) as exc:
        _registrar(rid, "meio", atores.autorizador, avarias=True, descricao=SEGREDO)
    assert h.sqlstate(exc) == CHECK
    assert exc.value.diag.constraint_name == "ck_checklists_momento"
    _erro_sem_conteudo(exc)
    # NOT NULL (23502) tambem montaria DETAIL com a linha: avarias nulo com texto preenchido.
    with pytest.raises(psycopg.Error) as exc2:
        with h.conectar() as c:
            c.execute(
                "select public.registrar_checklist(%s, 'saida', 'meio', null, %s, %s, '[]'::jsonb)",
                (rid, SEGREDO, atores.autorizador),
            )
    assert h.sqlstate(exc2) == "23502"
    _erro_sem_conteudo(exc2)


def test_m1_insert_direto_tambem_sem_conteudo(atores: Atores) -> None:
    rid = _autorizada(atores)
    with pytest.raises(psycopg.Error) as exc:
        _inserir_direto(rid, "saida", atores.autorizador, avarias=False, descricao=SEGREDO)
    assert h.sqlstate(exc) == CHECK
    _erro_sem_conteudo(exc)


def test_b1_constraint_usa_espaco_em_branco_generico() -> None:
    with h.conectar() as c:
        (defn,) = c.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid='public.checklists'::regclass and conname='ck_checklists_avarias_descricao'"
        ).fetchone() or ("",)
    assert "\\S" in defn and "btrim" not in defn


def test_b2_truncate_das_duas_tabelas_na_mesma_instrucao_da_ag003() -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("truncate table public.checklists, public.checklist_fotos")
    assert h.sqlstate(exc) == "AG003"


def test_b3_1_auditoria_preserva_todos_os_valores_antigos() -> None:
    with h.conectar() as c:
        defs = {
            nome: d
            for nome, d in c.execute(
                "select conname, pg_get_constraintdef(oid) from pg_constraint "
                "where conrelid='public.auditoria'::regclass "
                "and conname in ('ck_auditoria_acao', 'ck_auditoria_tabela')"
            ).fetchall()
        }
    assert set(defs) == {"ck_auditoria_acao", "ck_auditoria_tabela"}
    assert len(ACOES_AUDITORIA) == 15 and len(TABELAS_AUDITORIA) == 6
    for v in ACOES_AUDITORIA:
        assert f"'{v}'" in defs["ck_auditoria_acao"], v
    for v in TABELAS_AUDITORIA:
        assert f"'{v}'" in defs["ck_auditoria_tabela"], v


@pytest.mark.parametrize(
    ("tabela", "acao", "constraint"),
    [
        ("requisicoes", "acao_desconhecida", "ck_auditoria_acao"),
        ("tabela_desconhecida", "criada", "ck_auditoria_tabela"),
    ],
)
def test_b3_1_valor_desconhecido_de_auditoria_e_recusado(tabela: str, acao: str, constraint: str) -> None:
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("insert into public.auditoria (tabela, acao) values (%s, %s)", (tabela, acao))
    assert h.sqlstate(exc) == CHECK
    assert exc.value.diag.constraint_name == constraint


def test_b3_2_dono_da_conexao_sem_forja_nao_insere_foto_e_com_forja_so_o_dono_consegue(
    atores: Atores,
) -> None:
    """B-e: o gatilho confia na variavel de sessao; quem tem INSERT na tabela (so o dono/postgres,
    nunca a API) consegue forja-la. Limite aceito: o dono ja tem controle total do banco.
    Sem a variavel, o mesmo INSERT do dono e recusado (AG014); com ela, passa (revertido ao fim)."""
    rid = _autorizada(atores)
    cid = _registrar(rid, "saida", atores.autorizador)  # transacao propria, ja encerrada
    insere = (
        "insert into public.checklist_fotos (requisicao_id, momento, posicao, mime_type, "
        "tamanho_bytes) values (%s, 'saida', 1, 'image/png', 10)"
    )
    with pytest.raises(psycopg.Error) as exc:  # sem forja
        with h.conectar() as c:
            with c.transaction():
                c.execute(insere, (rid,))
    assert h.sqlstate(exc) == "AG014"
    with h.conectar() as c:  # com forja, na mesma transacao, sempre revertida
        with c.transaction(force_rollback=True):
            c.execute(
                "select set_config(%s, '1', true)",
                (f"sisveiculos.checklist_aberto_{str(cid).replace('-', '')}",),
            )
            c.execute(insere, (rid,))
    assert _fotos_de(rid, "saida") == []


def test_b3_2_service_role_nao_tem_insert_direto_em_fotos(atores: Atores) -> None:
    """Prova apenas que service_role nao tem INSERT (42501); a forja pelo dono esta acima."""
    rid = _autorizada(atores)
    cid = _registrar(rid, "saida", atores.autorizador)  # transacao propria, ja encerrada
    with pytest.raises(psycopg.Error) as exc:
        with h.como("service_role") as c:
            c.execute(
                "select set_config(%s, '1', true)",
                (f"sisveiculos.checklist_aberto_{str(cid).replace('-', '')}",),
            )
            c.execute(
                "insert into public.checklist_fotos (requisicao_id, momento, posicao, mime_type, "
                "tamanho_bytes) values (%s, 'saida', 1, 'image/png', 10)",
                (rid,),
            )
    assert h.sqlstate(exc) == NEGADO


def test_b3_3_concorrencia_mesma_requisicao_e_momento_so_um_persiste(atores: Atores) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    rid = _autorizada(atores)
    barreira = threading.Barrier(2)
    # B-f: conexoes abertas ANTES da barreira, para que a corrida seja so a chamada da RPC.
    conexoes = [h.conectar(), h.conectar()]

    def tentar(c: "psycopg.Connection") -> tuple[str, object]:
        barreira.wait(timeout=60)
        try:
            row = c.execute(
                "select public.registrar_checklist(%s, 'saida', 'meio', false, null, %s, %s)",
                (rid, atores.autorizador, Jsonb([_foto()])),
            ).fetchone()
            return "ok", row
        except psycopg.Error as e:
            return "erro", e.sqlstate

    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            resultados = list(ex.map(tentar, conexoes))
    finally:
        for c in conexoes:
            c.close()

    assert sorted(r[0] for r in resultados) == ["erro", "ok"], resultados
    assert next(r[1] for r in resultados if r[0] == "erro") == UNICO
    with h.conectar() as c:
        (n,) = c.execute(
            "select count(*) from public.checklists where requisicao_id=%s and momento='saida'", (rid,)
        ).fetchone() or (-1,)
    assert n == 1
    assert len(_fotos_de(rid, "saida")) == 1  # a foto da transacao perdedora tambem foi desfeita


def test_m24_id_nulo_explicito_recusado_sem_conteudo(atores: Atores) -> None:
    """B-a/B-b: id nulo explicito -> 23502 com mensagem fixa e campos de diagnostico."""
    rid = _autorizada(atores)
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute(
                "insert into public.checklists (id, requisicao_id, momento, combustivel, avarias, "
                "descricao_avarias, registrado_por) values (null, %s, 'saida', 'meio', false, %s, %s)",
                (rid, SEGREDO, atores.autorizador),
            )
    assert h.sqlstate(exc) == "23502"
    assert exc.value.diag.column_name == "id"
    assert exc.value.diag.table_name == "checklists"
    assert exc.value.diag.schema_name == "public"
    _erro_sem_conteudo(exc)


def test_m24_diagnostico_de_constraint_preenche_coluna_e_tabela(atores: Atores) -> None:
    rid = _autorizada(atores)
    with pytest.raises(psycopg.Error) as exc:
        _registrar(rid, "saida", atores.autorizador, avarias=False, descricao=SEGREDO)
    diag = exc.value.diag
    assert (diag.table_name, diag.column_name) == ("checklists", "descricao_avarias")
    assert diag.constraint_name == "ck_checklists_avarias_descricao"
