"""Testes de supabase/ops/limpar_dados_de_teste.sql e lacrar_producao.sql (ferramentas de pre-lancamento).

Somente banco local/dev DESCARTAVEL (TEST_DATABASE_URL + TEST_DATABASE_CONFIRM=descartavel + marcador
`comment on database ... is 'sisveiculos:descartavel'`; sem eles os testes sao pulados). Ver dbhelpers.py.
ATENCAO: estes testes EXECUTAM a limpeza de verdade: apagam requisicoes, checklists, fotos de perfil,
acessos de login e auditoria de TODO o banco de teste (perfis, veiculos, configuracoes e convites
ficam). Os outros testes criam os proprios dados com ids unicos e nao dependem de dados antigos.
Alguns testes trocam o comentario do banco (lacre) e o restauram no fim. Sem paralelismo.

    pytest supabase/tests/test_ops_limpeza.py -m db_local -p no:xdist
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from psycopg import sql  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"),
]

_OPS = Path(__file__).resolve().parents[1] / "ops"
SCRIPT = (_OPS / "limpar_dados_de_teste.sql").read_text(encoding="utf-8")
LACRAR = (_OPS / "lacrar_producao.sql").read_text(encoding="utf-8")

FRASE = "APAGAR DADOS DE TESTE ofwvoztarroglekfwohs"
FRASE_LACRE = "LACRAR PRODUCAO ofwvoztarroglekfwohs"
ESCOPO = ("checklist_fotos", "checklists", "requisicoes", "perfil_fotos", "acessos_login", "auditoria")
MANTIDAS = ("perfis", "veiculos", "configuracoes", "convites")
GATILHOS_DESLIGADOS = {
    "checklist_fotos": "trg_checklist_fotos_imutavel",
    "checklists": "trg_checklists_imutavel",
    "requisicoes": "trg_requisicoes_sem_delete",
    "perfil_fotos": "trg_perfil_fotos_sem_delete",
    "acessos_login": "trg_acessos_login_imutavel",
    "auditoria": "trg_auditoria_append_only",
}
ANCORA = "-- >>> RELIGAR"
PADRAO = "default administrative connection database"


# --- runner do script --------------------------------------------------------------------------
@dataclass
class Resultado:
    ok: bool
    mensagem: str = ""
    sqlstate: str | None = None
    avisos: list[str] = field(default_factory=list)
    tabela_final: list[tuple] = field(default_factory=list)


def _lit(texto: str) -> str:
    """Literal SQL de texto (aspas simples dobradas)."""
    return "'" + texto.replace("'", "''") + "'"


_VALOR = r"(?:'(?:[^']|'')*'|[^;']+)"  # literal entre aspas (aceita ';' e '') ou token simples


def _param(texto: str, nome: str, tipo: str, valor: str) -> str:
    novo, n = re.subn(
        rf"({nome}\s+{tipo}\s*:=\s*){_VALOR};", lambda m: f"{m.group(1)}{valor};", texto, count=1
    )
    assert n == 1, nome
    return novo


def _rodar_texto(texto: str, papel: str | None = None) -> Resultado:
    """Executa o arquivo INTEIRO como uma unica consulta (como o SQL Editor), em autocommit."""
    r = Resultado(ok=True)
    with h.conectar() as c:
        c.add_notice_handler(lambda d: r.avisos.append(d.message_primary or ""))
        if papel:
            c.execute(sql.SQL("set role {}").format(sql.Identifier(papel)))
        try:
            cur = c.execute(texto)
            while True:
                if cur.description:
                    r.tabela_final = cur.fetchall()
                if not cur.nextset():
                    break
        except psycopg.Error as e:
            r.ok = False
            r.mensagem = e.diag.message_primary or ""
            r.sqlstate = e.sqlstate
    return r


def rodar(
    *,
    executar: bool = False,
    confirmacao: str = "",
    apagar_auditoria: bool = True,
    lacrar: bool = False,
    antes_de_religar: str | None = None,
    confere: int | str | None = "auto",
    papel: str | None = None,
) -> Resultado:
    """confere: 'auto' = total atual das 6 tabelas do escopo; None = deixa o padrao (-1); int = esse valor."""
    t = SCRIPT
    t = _param(t, "v_executar", "boolean", "true" if executar else "false")
    t = _param(t, "v_confirmacao", "text", _lit(confirmacao))
    if confere == "auto":
        confere = sum(contagens(ESCOPO).values())
    if confere is not None:
        t = _param(t, "v_confere_total", "int", str(confere))
    t = _param(t, "v_apagar_auditoria", "boolean", "true" if apagar_auditoria else "false")
    t = _param(t, "v_lacrar", "boolean", "true" if lacrar else "false")
    if antes_de_religar is not None:
        assert ANCORA in t
        t = t.replace(ANCORA, antes_de_religar + "\n" + ANCORA, 1)
    return _rodar_texto(t, papel)


def rodar_lacre(confirmacao: str) -> Resultado:
    t = _param(LACRAR, "v_confirmacao", "text", _lit(confirmacao))
    return _rodar_texto(t)


# --- estado do banco ----------------------------------------------------------------------------
def contagens(tabelas: tuple[str, ...] = ESCOPO + MANTIDAS) -> dict[str, int]:
    with h.conectar() as c:
        return {
            t: int(c.execute(sql.SQL("select count(*) from public.{}").format(sql.Identifier(t))).fetchone()[0])  # type: ignore[index]
            for t in tabelas
        }


def snapshot_gatilhos() -> list[tuple]:
    with h.conectar() as c:
        return c.execute(
            "select c.relname, t.tgname, t.tgenabled::text from pg_trigger t "
            "join pg_class c on c.oid = t.tgrelid join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' order by 1, 2"
        ).fetchall()


def _banco() -> str:
    with h.conectar() as c:
        return str(c.execute("select current_database()").fetchone()[0])  # type: ignore[index]


def _comentario() -> str | None:
    with h.conectar() as c:
        row = c.execute(
            "select shobj_description(oid, 'pg_database') from pg_database where datname = current_database()"
        ).fetchone()
    return row[0] if row else None


def _definir_comentario(valor: str | None) -> None:
    with h.conectar() as c:
        c.execute(
            sql.SQL("comment on database {} is {}").format(
                sql.Identifier(_banco()), sql.Literal(valor) if valor is not None else sql.SQL("NULL")
            )
        )


@pytest.fixture
def marcador() -> Iterator[None]:
    """Garante que o marcador de banco descartavel volte ao fim, aconteca o que acontecer."""
    original = _comentario()
    assert original == h.MARCADOR_BANCO
    try:
        yield
    finally:
        _definir_comentario(h.MARCADOR_BANCO)


# --- dados ficticios ----------------------------------------------------------------------------
@dataclass
class Mundo:
    admin: uuid.UUID
    autorizador: uuid.UUID
    solicitante: uuid.UUID
    veiculo: uuid.UUID
    ocupada: uuid.UUID  # requisicao em_uso (ocupa o veiculo)


def _flag_checklist(ligado: bool, por: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute("update public.configuracoes set checklist = %s, alterado_por = %s where id = true", (ligado, por))


def _checklist(rid: uuid.UUID, momento: str, por: uuid.UUID, fotos: int = 2) -> None:
    lista = [{"id": str(uuid.uuid4()), "mime_type": "image/jpeg", "tamanho_bytes": 1000} for _ in range(fotos)]
    with h.conectar() as c:
        c.execute(
            "select public.registrar_checklist(%s, %s, 'meio'::public.nivel_combustivel, false, null, %s, %s)",
            (rid, momento, por, Jsonb(lista)),
        )


def _acesso(uid: uuid.UUID) -> None:
    sessao = uuid.uuid4()
    quando = datetime.now(timezone.utc) - timedelta(seconds=1)
    with h.conectar() as c:
        c.execute("insert into auth.sessions (id, user_id, created_at) values (%s, %s, %s)", (sessao, uid, quando))
        c.execute(
            "select public.registrar_acesso_login(%s::uuid, %s::uuid, %s::timestamptz, 'password', 'aal1', "
            "'203.0.113.0/24', 'Chrome/Windows')",
            (uid, sessao, quando),
        )


def _foto(uid: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute("select public.trocar_foto_perfil(%s, %s, 'image/png', 1000)", (uid, uuid.uuid4()))


def popular() -> Mundo:
    """Dados ficticios completos: usuarios, veiculo, requisicoes em varios estados, checklists com
    fotos, fotos de perfil (uma removida), acessos de login, convite e auditoria."""
    admin = h.criar_usuario("admin-lmp", "admin")
    aut = h.criar_usuario("aut-lmp", "autorizador")
    sol = h.criar_usuario("sol-lmp")
    veic = h.novo_veiculo(admin)
    with h.conectar() as c:
        original = c.execute("select checklist from public.configuracoes").fetchone()[0]  # type: ignore[index]
    _flag_checklist(True, admin)
    try:
        with h.conectar() as c:
            concluida = h.inserir_requisicao(c, sol, veic)
        h.autorizar(concluida, aut)
        _checklist(concluida, "saida", aut)
        h.iniciar(concluida, aut)
        _checklist(concluida, "chegada", aut)
        h.concluir(concluida, aut)
        with h.conectar() as c:
            negada = h.inserir_requisicao(c, sol, veic)
        h.negar(negada, aut)
        with h.conectar() as c:
            cancelada = h.inserir_requisicao(c, sol, veic)
        h.cancelar(cancelada, sol)
        with h.conectar() as c:
            ocupada = h.inserir_requisicao(c, sol, veic)
        h.autorizar(ocupada, aut)
        _checklist(ocupada, "saida", aut, fotos=1)
        h.iniciar(ocupada, aut)
    finally:
        _flag_checklist(original, admin)
    _foto(sol)
    _foto(sol)  # a primeira fica removida (troca), a segunda ativa
    _foto(aut)
    _acesso(sol)
    _acesso(aut)
    h.novo_convite(admin, h.email_unico())
    return Mundo(admin, aut, sol, veic, ocupada)


def _confirmar_populado() -> dict[str, int]:
    c = contagens()
    for t in ESCOPO:
        assert c[t] > 0, t
    return c


# --- (a) ensaio ---------------------------------------------------------------------------------
def test_ensaio_padrao_nao_apaga_e_reporta_contagens() -> None:
    popular()
    antes = _confirmar_populado()
    gat = snapshot_gatilhos()
    r = rodar()
    assert not r.ok and r.sqlstate == "LD001"
    assert "ENSAIO: nada foi apagado" in r.mensagem
    assert contagens() == antes
    assert snapshot_gatilhos() == gat
    # a tabela do relatorio tem a linha de cada tabela do escopo com ANTES e APAGADAS iguais as reais
    for t in ESCOPO:
        m = re.search(rf"^{t}\s+(\d+)\s+(\d+)$", r.mensagem, re.MULTILINE)
        assert m, t
        assert (int(m.group(1)), int(m.group(2))) == (antes[t], antes[t]), t
    for t in MANTIDAS:
        assert f"{t}={antes[t]}" in r.mensagem


def test_ensaio_com_confirmacao_certa_e_executar_false_continua_ensaio() -> None:
    antes = contagens()
    r = rodar(executar=False, confirmacao=FRASE)
    assert r.sqlstate == "LD001" and contagens() == antes


# --- (b) travas de confirmacao ------------------------------------------------------------------
@pytest.mark.parametrize(
    "confirmacao",
    ["", "APAGAR DADOS DE TESTE", "apagar dados de teste ofwvoztarroglekfwohs", FRASE + " ", FRASE[:-1]],
)
def test_execucao_real_sem_ou_com_confirmacao_errada_aborta(confirmacao: str) -> None:
    popular()
    antes, gat = contagens(), snapshot_gatilhos()
    r = rodar(executar=True, confirmacao=confirmacao)
    assert not r.ok and r.sqlstate == "LD003"
    assert contagens() == antes and snapshot_gatilhos() == gat


# --- (c)(d) execucao real -----------------------------------------------------------------------
def test_execucao_real_apaga_exatamente_o_escopo_e_mantem_o_resto() -> None:
    popular()
    antes = _confirmar_populado()
    gat = snapshot_gatilhos()
    r = rodar(executar=True, confirmacao=FRASE)
    assert r.ok, r.mensagem
    depois = contagens()
    for t in ESCOPO[:-1]:
        assert depois[t] == 0, t
    assert depois["auditoria"] == 1  # o registro da limpeza
    for t in MANTIDAS:
        assert depois[t] == antes[t], t
    # (d) gatilhos identicos e os nomeados continuam em ENABLE ALWAYS ('A')
    assert snapshot_gatilhos() == gat
    estados = {(rel, nome): est for rel, nome, est in snapshot_gatilhos()}
    for tabela, gatilho in GATILHOS_DESLIGADOS.items():
        assert estados[(tabela, gatilho)] == "A", gatilho
    # resumo final visivel (segunda consulta) com as contagens finais
    final = {linha[0]: linha[1] for linha in r.tabela_final}
    apagadas = {linha[0]: linha[2] for linha in r.tabela_final}
    for t in ESCOPO[:-1]:
        assert final[t] == 0
    assert final["auditoria"] == 1
    for t in ESCOPO:  # as contagens apagadas saem no resultado final (nao so no NOTICE)
        assert apagadas[t] == str(antes[t]), t
    for t in MANTIDAS:
        assert final[t] == antes[t]
    assert any("LIMPEZA CONCLUIDA" in a for a in r.avisos)


def test_depois_da_limpeza_os_gatilhos_de_bloqueio_continuam_bloqueando() -> None:
    m = popular()
    assert rodar(executar=True, confirmacao=FRASE).ok
    m2 = popular()
    for tabela in ("requisicoes", "checklists", "checklist_fotos", "perfil_fotos", "acessos_login", "auditoria"):
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(sql.SQL("delete from public.{} where true").format(sql.Identifier(tabela)))
        assert h.sqlstate(exc) == "AG003", tabela
    assert m.veiculo != m2.veiculo


# --- (e) o sistema volta a funcionar -----------------------------------------------------------
def test_sistema_funciona_apos_a_limpeza() -> None:
    m = popular()
    # antes: o veiculo esta ocupado
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            h.inserir_requisicao(c, m.solicitante, m.veiculo)
    assert h.sqlstate(exc) == "23505"
    assert rodar(executar=True, confirmacao=FRASE).ok
    # depois: livre, nova requisicao, checklist de saida, saida real, acesso de login e troca de foto
    with h.conectar() as c:
        original = c.execute("select checklist from public.configuracoes").fetchone()[0]  # type: ignore[index]
    _flag_checklist(True, m.admin)
    try:
        with h.conectar() as c:
            rid = h.inserir_requisicao(c, m.solicitante, m.veiculo)
        h.autorizar(rid, m.autorizador)
        _checklist(rid, "saida", m.autorizador)
        h.iniciar(rid, m.autorizador)
        assert h.status_de(rid) == "em_uso"
    finally:
        _flag_checklist(bool(original), m.admin)
    _acesso(m.solicitante)
    _foto(m.solicitante)
    c = contagens()
    assert c["requisicoes"] == 1 and c["checklists"] == 1 and c["checklist_fotos"] == 2
    assert c["acessos_login"] == 1 and c["perfil_fotos"] == 1 and c["auditoria"] > 1
    # a auditoria volta a registrar as transicoes
    assert any(a[0] for a in h.auditoria_de("requisicoes", rid))


# --- (f) idempotencia ---------------------------------------------------------------------------
def test_idempotente_rodar_de_novo_sem_dados_nao_falha() -> None:
    popular()
    assert rodar(executar=True, confirmacao=FRASE).ok
    antes, gat = contagens(), snapshot_gatilhos()
    r = rodar(executar=True, confirmacao=FRASE)
    assert r.ok, r.mensagem
    assert contagens() == antes and snapshot_gatilhos() == gat
    ens = rodar()
    assert ens.sqlstate == "LD001"


# --- (g) falha no meio => rollback total --------------------------------------------------------
def test_falha_simulada_depois_dos_deletes_desfaz_tudo() -> None:
    popular()
    antes, gat = _confirmar_populado(), snapshot_gatilhos()
    r = rodar(
        executar=True,
        confirmacao=FRASE,
        antes_de_religar="  raise exception 'falha simulada' using errcode = 'XX999';",
    )
    assert not r.ok and r.sqlstate == "XX999"
    assert contagens() == antes
    assert snapshot_gatilhos() == gat  # nenhum gatilho ficou desligado
    estados = {(rel, nome): est for rel, nome, est in snapshot_gatilhos()}
    assert all(estados[(t, g)] == "A" for t, g in GATILHOS_DESLIGADOS.items())


def test_gatilho_de_delete_inesperado_aborta_sem_mudar_nada() -> None:
    popular()
    antes, gat = contagens(), snapshot_gatilhos()
    with h.conectar() as c:
        c.execute("create function public.zz_noop() returns trigger language plpgsql as $$ begin return old; end $$")
        c.execute("create trigger zz_extra before delete on public.requisicoes for each row execute function public.zz_noop()")
    try:
        r = rodar(executar=True, confirmacao=FRASE)
        assert not r.ok and r.sqlstate == "LD004" and "zz_extra" in r.mensagem
        assert contagens() == antes
    finally:
        with h.conectar() as c:
            c.execute("drop trigger zz_extra on public.requisicoes")
            c.execute("drop function public.zz_noop()")
    assert snapshot_gatilhos() == gat


def test_tabela_dependente_fora_do_escopo_aborta() -> None:
    popular()
    antes = contagens()
    with h.conectar() as c:
        c.execute("create table public.zz_dep (rid uuid references public.requisicoes (id))")
    try:
        r = rodar(executar=True, confirmacao=FRASE)
        assert not r.ok and r.sqlstate == "LD004" and "zz_dep" in r.mensagem
        assert contagens() == antes
    finally:
        with h.conectar() as c:
            c.execute("drop table public.zz_dep")


def test_gatilho_que_nao_esta_em_enable_always_aborta() -> None:
    popular()
    antes = contagens()
    with h.conectar() as c:
        c.execute("alter table public.requisicoes enable trigger trg_requisicoes_sem_delete")  # 'O'
    try:
        r = rodar(executar=True, confirmacao=FRASE)
        assert not r.ok and r.sqlstate == "LD004"
        assert contagens() == antes
    finally:
        with h.conectar() as c:
            c.execute("alter table public.requisicoes enable always trigger trg_requisicoes_sem_delete")


# --- (h) lacre ----------------------------------------------------------------------------------
def test_lacrado_recusa_ensaio_e_execucao(marcador: None) -> None:
    popular()
    antes = contagens()
    _definir_comentario("sisveiculos:lacrado")
    for kw in ({}, {"executar": True, "confirmacao": FRASE}):
        r = rodar(**kw)  # type: ignore[arg-type]
        assert not r.ok and r.sqlstate == "LD002" and "LACRADO" in r.mensagem
    assert contagens() == antes


@pytest.mark.parametrize("inicial", [None, PADRAO])
def test_v_lacrar_grava_o_lacre_e_depois_recusa(marcador: None, inicial: str | None) -> None:
    popular()
    _definir_comentario(inicial)  # producao: comentario vazio ou o padrao do Postgres
    r = rodar(executar=True, confirmacao=FRASE, lacrar=True)
    assert r.ok, r.mensagem
    assert _comentario() == "sisveiculos:lacrado"
    assert any(a[0] == "LACRE do banco" and a[3] == "sisveiculos:lacrado" for a in r.tabela_final)
    # o comentario substituido aparece no resumo final e no aviso
    anterior = [a for a in r.tabela_final if a[0].startswith("Comentario anterior")][0][3]
    assert anterior == (inicial or "-")
    assert any((inicial or "(vazio)") in a for a in r.avisos)
    popular()
    antes = contagens()
    r2 = rodar(executar=True, confirmacao=FRASE)
    assert r2.sqlstate == "LD002" and contagens() == antes


def test_desfazer_o_lacre_libera_a_limpeza_de_novo(marcador: None) -> None:
    popular()
    _definir_comentario("sisveiculos:lacrado")
    assert rodar(executar=True, confirmacao=FRASE).sqlstate == "LD002"
    _definir_comentario(PADRAO)  # desfazer manual (decisao consciente do dono)
    assert rodar(executar=True, confirmacao=FRASE).ok
    assert contagens()["requisicoes"] == 0


def test_ensaio_com_lacrar_nao_grava_o_lacre(marcador: None) -> None:
    _definir_comentario(None)
    r = rodar(lacrar=True)
    assert r.sqlstate == "LD001" and "simulado no ensaio" in r.mensagem
    assert _comentario() in (None, "")  # o comment on database do ensaio foi desfeito


def test_v_lacrar_nao_sobrescreve_o_marcador_descartavel(marcador: None) -> None:
    popular()
    antes = contagens()
    for kw in ({}, {"executar": True, "confirmacao": FRASE}):
        r = rodar(lacrar=True, **kw)  # type: ignore[arg-type]
        assert not r.ok and r.sqlstate == "LD006"
    assert _comentario() == h.MARCADOR_BANCO
    assert contagens() == antes


def test_v_lacrar_nao_sobrescreve_outro_comentario(marcador: None) -> None:
    _definir_comentario("alguma outra coisa")
    r = rodar(executar=True, confirmacao=FRASE, lacrar=True)
    assert r.sqlstate == "LD006" and _comentario() == "alguma outra coisa"
    assert rodar_lacre(FRASE_LACRE).sqlstate == "LD006" and _comentario() == "alguma outra coisa"


@pytest.mark.parametrize("inicial", [None, PADRAO])
def test_lacrar_producao_script(marcador: None, inicial: str | None) -> None:
    # banco de teste: recusa e preserva o marcador
    r = rodar_lacre(FRASE_LACRE)
    assert r.sqlstate == "LD006" and _comentario() == h.MARCADOR_BANCO
    # producao simulada: confirmacao errada recusa; certa lacra; de novo recusa
    _definir_comentario(inicial)
    assert rodar_lacre("").sqlstate == "LD003"
    assert _comentario() == inicial
    ok = rodar_lacre(FRASE_LACRE)
    assert ok.ok, ok.mensagem
    assert ok.tabela_final == [("sisveiculos:lacrado", inicial or "(vazio)")]
    assert rodar_lacre(FRASE_LACRE).sqlstate == "LD002"


# --- (i) auditoria preservada -------------------------------------------------------------------
def test_sem_apagar_auditoria_preserva_todas_as_linhas(marcador: None) -> None:
    popular()
    antes = _confirmar_populado()
    with h.conectar() as c:
        ids = c.execute("select id from public.auditoria order by id").fetchall()
    r = rodar(executar=True, confirmacao=FRASE, apagar_auditoria=False)
    assert r.ok, r.mensagem
    depois = contagens()
    assert depois["auditoria"] == antes["auditoria"] + 1  # + o registro da limpeza
    for t in ESCOPO[:-1]:
        assert depois[t] == 0
    with h.conectar() as c:
        assert c.execute("select id from public.auditoria order by id").fetchall()[:-1] == ids
    # o gatilho append-only da auditoria nao foi tocado e continua bloqueando
    estados = {(rel, nome): est for rel, nome, est in snapshot_gatilhos()}
    assert estados[("auditoria", "trg_auditoria_append_only")] == "A"
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("delete from public.auditoria where true")
    assert h.sqlstate(exc) == "AG003"


# --- A3: segunda confirmacao por contagem (LD007) ------------------------------------------------
def test_ensaio_destaca_o_numero_e_ele_permite_executar() -> None:
    popular()
    ens = rodar()
    m = re.search(r"COPIE ESTE NUMERO PARA v_confere_total: (\d+)", ens.mensagem)
    assert m and int(m.group(1)) == sum(contagens(ESCOPO).values()) > 0
    assert rodar(executar=True, confirmacao=FRASE, confere=int(m.group(1))).ok


@pytest.mark.parametrize("modo", ["padrao", "menos_um", "a_mais", "a_menos", "zero_com_dados"])
def test_contagem_errada_ou_ausente_aborta_com_ld007(modo: str) -> None:
    popular()
    n = sum(contagens(ESCOPO).values())
    confere = {"padrao": None, "menos_um": -1, "a_mais": n + 1, "a_menos": n - 1, "zero_com_dados": 0}[modo]
    antes, gat = contagens(), snapshot_gatilhos()
    r = rodar(executar=True, confirmacao=FRASE, confere=confere)
    assert not r.ok and r.sqlstate == "LD007"
    assert contagens() == antes and snapshot_gatilhos() == gat


def test_total_menos_um_e_zero_com_dados_nunca_passam_mesmo_apos_limpar() -> None:
    popular()
    assert rodar(executar=True, confirmacao=FRASE).ok
    # sobra 1 linha (o registro da limpeza): o total e 1, nao 0
    assert sum(contagens(ESCOPO).values()) == 1
    for ruim in (-1, None, 0, 2):
        assert rodar(executar=True, confirmacao=FRASE, confere=ruim).sqlstate == "LD007"
    assert rodar(executar=True, confirmacao=FRASE, confere=1).ok


def test_segunda_confirmacao_por_total_cobre_tabelas_alem_de_requisicoes() -> None:
    """Com 0 requisicoes mas outras tabelas com dados, o total do ensaio antigo nao vale."""
    popular()
    antes = contagens()
    total_antigo = sum(antes[t] for t in ESCOPO)
    # numero que seria o "so requisicoes" ou 0 nao passam
    for ruim in (0, antes["requisicoes"]):
        assert rodar(executar=True, confirmacao=FRASE, confere=ruim).sqlstate == "LD007"
    assert contagens() == antes
    assert rodar(executar=True, confirmacao=FRASE, confere=total_antigo).ok


def test_ensaio_nao_exige_a_contagem() -> None:
    assert rodar(confere=None).sqlstate == "LD001"


def test_confirmacao_com_ponto_e_virgula_e_aspas_nao_quebra_o_helper() -> None:
    antes = contagens()
    r = rodar(executar=True, confirmacao="a;b'c; v_executar := true;")
    assert r.sqlstate == "LD003" and contagens() == antes


# --- A2: registro duravel na auditoria ---------------------------------------------------------
@pytest.mark.parametrize("apagar_auditoria", [True, False])
def test_registro_duravel_na_auditoria_sem_dados_pessoais(apagar_auditoria: bool) -> None:
    popular()
    antes = contagens()
    with h.conectar() as c:
        previas = c.execute(
            "select count(*) from public.auditoria where de = 'limpeza_pre_lancamento'"
        ).fetchone()[0]  # type: ignore[index]
    assert rodar(executar=True, confirmacao=FRASE, apagar_auditoria=apagar_auditoria).ok
    with h.conectar() as c:
        linhas = c.execute(
            "select tabela, registro_id, acao, ator_id, de, para, detalhes from public.auditoria "
            "where de = 'limpeza_pre_lancamento' order by id"
        ).fetchall()
    # apagando a auditoria sobra so o registro desta limpeza; preservando, soma-se as anteriores
    assert len(linhas) == (1 if apagar_auditoria else previas + 1)
    tabela, registro_id, acao, ator_id, de, para, detalhes = linhas[-1]
    assert (tabela, registro_id, acao, ator_id, de, para) == ("perfis", None, "acao_administrativa", None, de, None)
    esperado = {t: antes[t] for t in ESCOPO if apagar_auditoria or t != "auditoria"}
    assert detalhes == {"apagadas": esperado, "auditoria_apagada": apagar_auditoria, "lacrado": False}
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}|@", str(detalhes))  # sem uuid nem e-mail


def test_registro_duravel_registra_lacrado(marcador: None) -> None:
    popular()
    _definir_comentario(None)
    assert rodar(executar=True, confirmacao=FRASE, lacrar=True).ok
    with h.conectar() as c:
        det = c.execute("select detalhes from public.auditoria where de = 'limpeza_pre_lancamento'").fetchone()
    assert det is not None and det[0]["lacrado"] is True


# --- LD005 exercitado ---------------------------------------------------------------------------
def test_ld005_gatilho_extra_desligado_desfaz_tudo() -> None:
    popular()
    antes, gat = contagens(), snapshot_gatilhos()
    r = rodar(
        executar=True,
        confirmacao=FRASE,
        antes_de_religar="  alter table public.requisicoes disable trigger trg_requisicoes_validar;",
    )
    assert not r.ok and r.sqlstate == "LD005" and "gatilhos" in r.mensagem
    assert contagens() == antes and snapshot_gatilhos() == gat


def test_ld005_tabela_mantida_alterada_desfaz_tudo() -> None:
    popular()
    antes, gat = contagens(), snapshot_gatilhos()
    r = rodar(
        executar=True,
        confirmacao=FRASE,
        apagar_auditoria=False,  # o insert em veiculos gera auditoria; assim cai na checagem de veiculos
        antes_de_religar=(
            "  insert into public.veiculos (descricao, alterado_por) "
            "select 'zz-ld005', id from public.perfis where role = 'admin' and ativo limit 1;"
        ),
    )
    assert not r.ok and r.sqlstate == "LD005" and "veiculos" in r.mensagem
    assert contagens() == antes and snapshot_gatilhos() == gat


# --- storage: caminho de erro NAO coberto: storage.objects pertence a supabase_storage_admin e o papel
# postgres nao consegue quebra-lo de forma segura; o bloco tem `exception when others` (ver o script).


# --- papel que nao e dono ------------------------------------------------------------------------
@pytest.fixture
def papel_nao_dono() -> Iterator[str]:
    nome = "zz_nao_dono_" + uuid.uuid4().hex[:6]
    with h.conectar() as c:
        c.execute(sql.SQL("create role {} nologin").format(sql.Identifier(nome)))
        # o postgres local nao e superusuario (como em producao): precisa do direito de SET ROLE
        c.execute(sql.SQL("grant {} to postgres with set true").format(sql.Identifier(nome)))
    try:
        yield nome
    finally:
        with h.conectar() as c:
            c.execute(sql.SQL("drop role {}").format(sql.Identifier(nome)))


def test_papel_nao_dono_e_recusado_sem_mudar_nada(papel_nao_dono: str, marcador: None) -> None:
    popular()
    antes, gat = contagens(), snapshot_gatilhos()
    r = rodar(papel=papel_nao_dono)  # ensaio
    assert not r.ok and r.sqlstate == "LD004" and "SEM PERMISSAO" in r.mensagem
    _definir_comentario(None)  # para chegar na checagem de dono do banco (e nao na do marcador)
    r2 = rodar(papel=papel_nao_dono, lacrar=True)
    assert not r2.ok and r2.sqlstate == "LD006" and "nao e dono do banco" in r2.mensagem
    assert contagens() == antes and snapshot_gatilhos() == gat
    assert _comentario() in (None, "")


# --- M3: parametro NULL nao contorna as travas ---------------------------------------------------
def _rodar_com_nulo(parametro: str) -> Resultado:
    t = SCRIPT
    t = _param(t, "v_executar", "boolean", "true")
    t = _param(t, "v_confirmacao", "text", _lit(FRASE))
    t = _param(t, "v_confere_total", "int", str(sum(contagens(ESCOPO).values())))
    tipos = {
        "v_executar": "boolean",
        "v_confirmacao": "text",
        "v_confere_total": "int",
        "v_apagar_auditoria": "boolean",
        "v_lacrar": "boolean",
        "v_ref_esperado": "text",
    }
    t = _param(t, parametro, tipos[parametro], "NULL")
    return _rodar_texto(t)


@pytest.mark.parametrize(
    "parametro",
    ["v_executar", "v_confirmacao", "v_confere_total", "v_apagar_auditoria", "v_lacrar", "v_ref_esperado"],
)
def test_parametro_nulo_e_recusado_sem_mudar_nada(parametro: str) -> None:
    popular()
    antes, gat = contagens(), snapshot_gatilhos()
    r = _rodar_com_nulo(parametro)
    assert not r.ok and r.sqlstate == "LD003" and "nulo" in r.mensagem
    assert contagens() == antes and snapshot_gatilhos() == gat


def test_v_executar_nulo_com_dados_nao_apaga_nem_comita() -> None:
    """Regressao: `if not NULL` e NULL fazia o ensaio nao abortar e o delete ser comitado."""
    popular()
    antes = _confirmar_populado()
    t = _param(SCRIPT, "v_executar", "boolean", "NULL")
    r = _rodar_texto(t)
    assert not r.ok and r.sqlstate == "LD003"
    assert contagens() == antes


@pytest.mark.parametrize("parametro", ["v_confirmacao", "v_ref_esperado"])
def test_lacrar_producao_parametro_nulo_recusa(marcador: None, parametro: str) -> None:
    _definir_comentario(None)
    t = _param(LACRAR, "v_confirmacao", "text", _lit(FRASE_LACRE))
    t = _param(t, parametro, "text", "NULL")
    r = _rodar_texto(t)
    assert not r.ok and r.sqlstate == "LD003"
    assert _comentario() in (None, "")
    # ambos nulos (NULL is not distinct from NULL) tambem recusa
    t2 = _param(_param(LACRAR, "v_confirmacao", "text", "NULL"), "v_ref_esperado", "text", "NULL")
    r2 = _rodar_texto(t2)
    assert r2.sqlstate == "LD003" and _comentario() in (None, "")
