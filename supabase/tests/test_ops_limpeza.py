"""Testes de supabase/ops/limpar_dados_de_teste.sql e lacrar_producao.sql (ferramentas de pre-lancamento).

Somente banco local/dev DESCARTAVEL (TEST_DATABASE_URL + TEST_DATABASE_CONFIRM=descartavel + marcador
`comment on database ... is 'sisveiculos:descartavel'`; sem eles os testes sao pulados). Ver dbhelpers.py.
ATENCAO: estes testes EXECUTAM a limpeza de verdade em TODO o banco de teste: apagam o dado operacional
(requisicoes, arquivadas, checklists, notificacoes, localizacoes, envios, IA, acessos, desafios e
reautenticacoes WebAuthn) e a auditoria; os testes do modo "so admins" apagam tambem TODAS as contas que
nao sao admin ativo (perfis + auth.users) e todos os convites. veiculos, configuracoes, ia_configuracoes e
saudacoes ficam. Exige TODAS as migrations (inclusive 20261127120000 e 20261128120000/120100/120200).
Os outros testes criam os proprios dados com ids unicos e nao dependem de dados antigos.
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

REF = "ofwvoztarroglekfwohs"
FRASE = "APAGAR DADOS DE TESTE " + REF
FRASE_LACRE = "LACRAR PRODUCAO " + REF


def frase(*, apagar_auditoria: bool = True, manter_usuarios: bool = True) -> str:
    """Frase exata do modo: base + ' E AUDITORIA' (auditoria apagada) + ' E USUARIOS' (so admins)."""
    return FRASE + (" E AUDITORIA" if apagar_auditoria else "") + ("" if manter_usuarios else " E USUARIOS")


# Classificacao das 21 tabelas de public (+ auth.users), igual ao script.
SEMPRE = (
    "checklist_fotos",
    "checklists",
    "notificacoes",
    "localizacoes_viagem",
    "requisicoes_arquivadas",
    "requisicoes",
    "acessos_login",
    "envios_email",
    "ia_uso",
    "desafios_webauthn",
    "reautenticacoes_webauthn",
)
POR_USUARIO = ("perfil_fotos", "push_inscricoes", "credenciais_webauthn")  # coluna usuario_id
CONTAS = ("convites", *POR_USUARIO, "perfis", "auth.users")
MANTIDAS = ("veiculos", "configuracoes", "ia_configuracoes", "saudacoes")
TODAS = (*SEMPRE, "auditoria", *CONTAS, *MANTIDAS)
GATILHOS_DESLIGADOS = {
    "checklist_fotos": "trg_checklist_fotos_imutavel",
    "checklists": "trg_checklists_imutavel",
    "notificacoes": "trg_notificacoes_proteger",
    "localizacoes_viagem": "trg_localizacoes_viagem_imutavel",
    "requisicoes": "trg_requisicoes_sem_delete",
    "acessos_login": "trg_acessos_login_imutavel",
    "envios_email": "trg_envios_email_sem_delete",
    "ia_uso": "trg_ia_uso_imutavel",
    "desafios_webauthn": "trg_desafios_webauthn_proteger",
    "reautenticacoes_webauthn": "trg_reautenticacoes_webauthn_proteger",
    "convites": "trg_convites_sem_delete",
    "perfil_fotos": "trg_perfil_fotos_sem_delete",
    "push_inscricoes": "trg_push_inscricoes_proteger",
    "credenciais_webauthn": "trg_credenciais_webauthn_proteger",
    "auditoria": "trg_auditoria_append_only",
    "perfis": "trg_perfis_sem_delete",
}
# Gatilhos que NUNCA podem ser tocados pela limpeza (TRUNCATE segue bloqueado).
GATILHOS_TRUNCATE = (
    ("notificacoes", "trg_notificacoes_sem_truncate"),
    ("localizacoes_viagem", "trg_localizacoes_viagem_sem_truncate"),
    ("auditoria", "trg_auditoria_sem_truncate"),
    ("perfis", "trg_perfis_sem_truncate"),
    ("push_inscricoes", "trg_push_inscricoes_sem_truncate"),
    ("credenciais_webauthn", "trg_credenciais_webauthn_sem_truncate"),
)
LIGADOS = ("A", "O")  # ENABLE ALWAYS ou ENABLE padrao (envios_email/ia_uso nasceram em 'O')
ANCORA = "-- >>> RELIGAR"
PADRAO = "default administrative connection database"
P256 = "B" * 87
AUTH = "a" * 22
CHAVE = "pQECAyYgASFYIK" + "A" * 60
_UUID_OU_EMAIL = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}|@")


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


def _b(v: bool) -> str:
    return "true" if v else "false"


def rodar(
    *,
    executar: bool = False,
    confirmacao: str | None = None,
    apagar_auditoria: bool = True,
    manter_usuarios: bool = True,
    lacrar: bool = False,
    antes_de_religar: str | None = None,
    confere: int | str | None = "auto",
    papel: str | None = None,
    script: str = SCRIPT,
) -> Resultado:
    """confirmacao None = a frase certa do modo. confere: 'auto' = total atual do modo;
    None = deixa o padrao (-1); int = esse valor."""
    if confirmacao is None:
        confirmacao = frase(apagar_auditoria=apagar_auditoria, manter_usuarios=manter_usuarios) if executar else ""
    t = _param(script, "v_executar", "boolean", _b(executar))
    t = _param(t, "v_confirmacao", "text", _lit(confirmacao))
    if confere == "auto":
        confere = total_modo(manter_usuarios=manter_usuarios, apagar_auditoria=apagar_auditoria)
    if confere is not None:
        t = _param(t, "v_confere_total", "int", str(confere))
    t = _param(t, "v_manter_usuarios", "boolean", _b(manter_usuarios))
    t = _param(t, "v_apagar_auditoria", "boolean", _b(apagar_auditoria))
    t = _param(t, "v_lacrar", "boolean", _b(lacrar))
    if antes_de_religar is not None:
        assert ANCORA in t
        t = t.replace(ANCORA, antes_de_religar + "\n" + ANCORA, 1)
    return _rodar_texto(t, papel)


def rodar_lacre(confirmacao: str) -> Resultado:
    t = _param(LACRAR, "v_confirmacao", "text", _lit(confirmacao))
    return _rodar_texto(t)


# --- estado do banco ----------------------------------------------------------------------------
def _ident(tabela: str) -> sql.Identifier:
    esquema, _, nome = tabela.rpartition(".")
    return sql.Identifier(esquema or "public", nome)


def contagens(tabelas: tuple[str, ...] = TODAS) -> dict[str, int]:
    with h.conectar() as c:
        return {
            t: int(c.execute(sql.SQL("select count(*) from {}").format(_ident(t))).fetchone()[0])  # type: ignore[index]
            for t in tabelas
        }


def admins() -> list[uuid.UUID]:
    with h.conectar() as c:
        return [r[0] for r in c.execute("select id from public.perfis where role = 'admin' and ativo order by id")]


def _nao_admin(tabela: str, coluna: str, ids: list[uuid.UUID]) -> int:
    with h.conectar() as c:
        q = sql.SQL("select count(*) from {} where not ({} = any (%s))").format(_ident(tabela), sql.Identifier(coluna))
        return int(c.execute(q, (ids,)).fetchone()[0])  # type: ignore[index]


def total_modo(*, manter_usuarios: bool = True, apagar_auditoria: bool = True) -> int:
    """Total de linhas que o modo apaga (o numero do ensaio)."""
    cont = contagens((*SEMPRE, "auditoria", "convites"))
    total = sum(cont[t] for t in SEMPRE) + (cont["auditoria"] if apagar_auditoria else 0)
    if not manter_usuarios:
        ids = admins()
        total += cont["convites"] + sum(_nao_admin(t, "usuario_id", ids) for t in POR_USUARIO)
        total += _nao_admin("perfis", "id", ids) + _nao_admin("auth.users", "id", ids)
    return total


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
    orfao: uuid.UUID  # conta do Auth sem perfil (convite nao consumido)


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


def _push(uid: uuid.UUID) -> None:
    """Cria uma inscricao de push (regex corrigido em 20261130120000; erro aqui e regressao)."""
    with h.conectar() as c:
        c.execute(
            "select public.registrar_inscricao_push(%s, %s, %s, %s)",
            (uid, f"https://fcm.googleapis.com/fcm/send/{uuid.uuid4().hex}", P256, AUTH),
        )


def _passkey(uid: uuid.UUID) -> None:
    """Cria uma passkey (regex corrigido em 20261130120000; erro aqui e regressao)."""
    cid = "C" + uuid.uuid4().hex + uuid.uuid4().hex[:7]
    with h.conectar() as c:
        c.execute(
            "select public.registrar_credencial_webauthn(%s, %s, %s, 0, %s, 'Notebook')",
            (uid, cid, CHAVE, ["internal"]),
        )


def _webauthn_transitorio(uid: uuid.UUID) -> None:
    """Um desafio e uma reautenticacao (recentes: o expurgo do sistema nao os apagaria)."""
    with h.conectar() as c:
        c.execute("select public.criar_desafio_webauthn(%s, 'registro')", (uid,))
        c.execute("select public.registrar_reautenticacao_webauthn(%s, %s)", (uid, "sess-" + uuid.uuid4().hex))


_FLAGS_MOTORISTA = (
    "motorista_localizacao",
    "motorista_registra",
    "motorista",
    "checklist",
    "abastecimento_manutencao",
    "quilometragem",
)  # dependentes primeiro; o religamento usa a ordem inversa


def _flags() -> dict[str, bool]:
    with h.conectar() as c:
        row = c.execute(f"select {', '.join(_FLAGS_MOTORISTA)} from public.configuracoes").fetchone()
    assert row is not None
    return dict(zip(_FLAGS_MOTORISTA, (bool(v) for v in row), strict=True))


def _definir_flag(nome: str, valor: bool, por: uuid.UUID) -> None:
    with h.conectar() as c:
        c.execute(f"update public.configuracoes set {nome} = %s, alterado_por = %s where id = true", (valor, por))


def _localizacao(admin: uuid.UUID, aut: uuid.UUID, sol: uuid.UUID, mot: uuid.UUID) -> None:
    """Gera uma linha em localizacoes_viagem pela RPC do motorista (recurso opcional ligado so aqui)."""
    original = _flags()
    for f in _FLAGS_MOTORISTA:
        if original[f]:
            _definir_flag(f, False, admin)
    try:
        for f in ("motorista", "motorista_registra", "motorista_localizacao"):
            _definir_flag(f, True, admin)
        with h.conectar() as c:
            rid = h.inserir_requisicao(c, sol, h.novo_veiculo(admin))
        h.autorizar(rid, aut)
        with h.conectar() as c:
            c.execute(
                "update public.requisicoes set motorista_id = %s, motorista_alterado_por = %s where id = %s",
                (mot, aut, rid),
            )
            c.execute(
                "select public.registrar_transicao_motorista(%s, 'saida', %s, -15.793889::numeric, "
                "-47.882778::numeric, null::numeric, null::integer, null::integer)",
                (rid, mot),
            )
    finally:
        for f in _FLAGS_MOTORISTA:
            if _flags()[f]:
                _definir_flag(f, False, admin)
        for f in reversed(_FLAGS_MOTORISTA):
            if original[f]:
                _definir_flag(f, True, admin)


def _envio_e_uso_ia(admin: uuid.UUID, sol: uuid.UUID) -> None:
    """Uma linha de envios_email (outbox pendente) e uma de ia_uso (sem prompt nem dado pessoal)."""
    with h.conectar() as c:
        c.execute(
            "insert into public.envios_email (solicitado_por, destinatario_id, tipo_relatorio, formato) "
            "values (%s, %s, 'requisicoes', 'csv')",
            (admin, admin),
        )
        c.execute(
            "insert into public.ia_uso (funcao, provedor, modelo, usuario_id, status, tokens_entrada) "
            "values ('saudacao', 'openai', 'm', %s, 'ok', 1)",
            (sol,),
        )


def popular() -> Mundo:
    """Dados ficticios completos em TODAS as tabelas apagaveis: usuarios, veiculo, requisicoes em varios
    estados (uma arquivada), checklists com fotos, fotos de perfil (uma removida), acessos de login,
    convite, auditoria, notificacoes (nascem por gatilho), localizacao de motorista, envio de e-mail, uso
    de IA, push, passkey, desafio e reautenticacao WebAuthn, e uma conta do Auth sem perfil. O admin tambem
    tem foto, push e passkey (devem ficar no modo "so admins"). Tudo RECENTE, abaixo dos pisos de expurgo."""
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
    with h.conectar() as c:
        c.execute(
            "insert into public.requisicoes_arquivadas (usuario_id, requisicao_id) values (%s, %s)", (sol, concluida)
        )
    _foto(sol)
    _foto(sol)  # a primeira fica removida (troca), a segunda ativa
    _foto(aut)
    _foto(admin)
    _acesso(sol)
    _acesso(aut)
    h.novo_convite(admin, h.email_unico())
    _localizacao(admin, aut, sol, h.criar_usuario("mot-lmp", "motorista"))
    _envio_e_uso_ia(admin, sol)
    for u in (sol, admin):
        _push(u)
        _passkey(u)
    _webauthn_transitorio(sol)
    orfao = h.criar_auth(h.email_unico())
    return Mundo(admin, aut, sol, veic, ocupada, orfao)


def _confirmar_populado() -> dict[str, int]:
    c = contagens()
    for t in (*SEMPRE, "auditoria", *CONTAS):
        assert c[t] > 0, t
    return c


def _linha_relatorio(mensagem: str, tabela: str) -> tuple[str, str, str, str]:
    m = re.search(rf"^{re.escape(tabela)}\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)$", mensagem, re.MULTILINE)
    assert m, tabela
    return m.group(1), m.group(2), m.group(3), m.group(4)


# --- (a) ensaio ---------------------------------------------------------------------------------
def test_ensaio_padrao_nao_apaga_e_reporta_os_dois_modos() -> None:
    popular()
    antes = _confirmar_populado()
    ids = admins()
    gat = snapshot_gatilhos()
    r = rodar()
    assert not r.ok and r.sqlstate == "LD001"
    assert "ENSAIO: nada foi apagado" in r.mensagem
    assert "MODO: mantem usuarios" in r.mensagem and "auditoria apagada" in r.mensagem
    assert contagens() == antes
    assert snapshot_gatilhos() == gat
    for t in (*SEMPRE, "auditoria"):
        assert _linha_relatorio(r.mensagem, t) == (str(antes[t]),) * 4, t
    for t in CONTAS:
        alvo = antes[t] if t == "convites" else _nao_admin(t, "id" if t in ("perfis", "auth.users") else "usuario_id", ids)
        assert _linha_relatorio(r.mensagem, t) == (str(antes[t]), "mantida", "0", str(alvo)), t
    for t in MANTIDAS:
        assert f"{t}={antes[t]}" in r.mensagem
    contas = _nao_admin("auth.users", "id", ids)
    assert f"CONTAS: {len(ids)} admin(s) ativo(s) ficam sempre" in r.mensagem
    assert f"seriam apagadas {contas} conta(s) do Auth" in r.mensagem
    assert "Arquivos por bucket" in r.mensagem
    m = re.search(r"COPIE ESTE NUMERO PARA v_confere_total: (\d+)", r.mensagem)
    assert m and int(m.group(1)) == total_modo()


def test_ensaio_so_admins_nao_apaga_e_mostra_o_total_do_modo() -> None:
    popular()
    antes = _confirmar_populado()
    gat = snapshot_gatilhos()
    r = rodar(manter_usuarios=False)
    assert r.sqlstate == "LD001", r.mensagem
    assert "MODO: so admins ativos" in r.mensagem
    assert contagens() == antes and snapshot_gatilhos() == gat
    for t in CONTAS:  # APAGADAS = SO_ADMINS neste modo
        _, apagadas, manter, so_admins = _linha_relatorio(r.mensagem, t)
        assert apagadas == so_admins and manter == "0", t
    m = re.search(r"COPIE ESTE NUMERO PARA v_confere_total: (\d+)", r.mensagem)
    assert m and int(m.group(1)) == total_modo(manter_usuarios=False) > total_modo()
    assert not _UUID_OU_EMAIL.search(r.mensagem)  # so contagens (LGPD)


def test_ensaio_com_confirmacao_certa_e_executar_false_continua_ensaio() -> None:
    antes = contagens()
    r = rodar(executar=False, confirmacao=frase())
    assert r.sqlstate == "LD001" and contagens() == antes


# --- (b) frases ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "confirmacao",
    ["", FRASE[:-8], frase().lower(), frase() + " ", frase()[:-1], FRASE, frase(manter_usuarios=False)],
)
def test_execucao_padrao_sem_ou_com_confirmacao_errada_aborta(confirmacao: str) -> None:
    popular()
    antes, gat = contagens(), snapshot_gatilhos()
    r = rodar(executar=True, confirmacao=confirmacao)
    assert not r.ok and r.sqlstate == "LD003"
    assert contagens() == antes and snapshot_gatilhos() == gat


@pytest.mark.parametrize(
    ("manter", "aud", "errada"),
    [
        (False, True, frase()),  # so admins sem " E USUARIOS"
        (False, True, FRASE + " E USUARIOS E AUDITORIA"),  # ordem trocada
        (False, False, frase(manter_usuarios=False)),  # com " E AUDITORIA" sem apagar auditoria
        (True, False, frase()),  # " E AUDITORIA" com auditoria preservada
        (True, True, FRASE),  # auditoria apagada exige " E AUDITORIA"
    ],
)
def test_frase_reflete_o_que_sera_apagado(manter: bool, aud: bool, errada: str) -> None:
    antes = contagens()
    r = rodar(executar=True, confirmacao=errada, manter_usuarios=manter, apagar_auditoria=aud)
    assert not r.ok and r.sqlstate == "LD003", r.mensagem
    assert frase(apagar_auditoria=aud, manter_usuarios=manter) in r.mensagem
    assert contagens() == antes


def test_padroes_do_script_mantem_usuarios_e_apagam_auditoria() -> None:
    assert "v_manter_usuarios  boolean := true;" in SCRIPT
    assert "v_apagar_auditoria boolean := true;" in SCRIPT
    assert "v_executar         boolean := false;" in SCRIPT
    assert "v_lacrar           boolean := false;" in SCRIPT


# --- (c)(d) execucao real, modo padrao (mantem usuarios) ----------------------------------------
def test_execucao_padrao_apaga_operacional_e_auditoria_e_mantem_contas() -> None:
    m = popular()
    antes = _confirmar_populado()
    gat = snapshot_gatilhos()
    r = rodar(executar=True)
    assert r.ok, r.mensagem
    depois = contagens()
    for t in SEMPRE:
        assert depois[t] == 0, t
    assert depois["auditoria"] == 1  # o registro da limpeza
    for t in (*CONTAS, *MANTIDAS):
        assert depois[t] == antes[t], t
    # (d) gatilhos identicos, nomeados ligados e TRUNCATE intocado
    assert snapshot_gatilhos() == gat
    estados = {(rel, nome): est for rel, nome, est in snapshot_gatilhos()}
    for tabela, gatilho in GATILHOS_DESLIGADOS.items():
        assert estados[(tabela, gatilho)] in LIGADOS, gatilho
    for tabela, gatilho in GATILHOS_TRUNCATE:
        assert estados[(tabela, gatilho)] in LIGADOS, gatilho
    # resumo final visivel (segunda consulta)
    final = {linha[0]: linha[1] for linha in r.tabela_final}
    apagadas = {linha[0]: linha[2] for linha in r.tabela_final}
    for t in SEMPRE:
        assert final[t] == 0 and apagadas[t] == str(antes[t]), t
    assert final["auditoria"] == 1 and apagadas["auditoria"] == str(antes["auditoria"])
    for t in (*CONTAS, *MANTIDAS):
        assert final[t] == antes[t] and apagadas[t] is None, t
    assert any("LIMPEZA CONCLUIDA" in a for a in r.avisos)
    # a conta do solicitante continua no Auth e no perfil
    with h.conectar() as c:
        assert c.execute("select count(*) from auth.users where id = %s", (m.solicitante,)).fetchone()[0] == 1  # type: ignore[index]


def test_depois_da_limpeza_os_gatilhos_de_bloqueio_continuam_bloqueando() -> None:
    m = popular()
    assert rodar(executar=True).ok
    m2 = popular()
    excluidas = ("requisicoes_arquivadas", "auth.users")
    bloqueadas = [t for t in (*SEMPRE, "auditoria", *CONTAS) if t not in excluidas]
    for tabela in bloqueadas:
        with pytest.raises(psycopg.Error) as exc:
            with h.conectar() as c:
                c.execute(sql.SQL("delete from {} where true").format(_ident(tabela)))
        assert h.sqlstate(exc) == "AG003", tabela
    assert m.veiculo != m2.veiculo


def test_dados_recentes_de_notificacoes_e_localizacoes_sao_apagados_e_protecao_permanece() -> None:
    """Notificacoes (< 30 dias) e localizacoes (< 12 meses) estao abaixo dos pisos do sistema: o expurgo
    normal nao as apagaria; a limpeza as apaga desligando o gatilho nomeado e o religa igual."""
    popular()
    antes = contagens()
    recentes = ("notificacoes", "localizacoes_viagem", "envios_email", "ia_uso", "desafios_webauthn", "reautenticacoes_webauthn")
    for t in recentes:
        assert antes[t] > 0, t
    gat = snapshot_gatilhos()
    assert rodar(executar=True).ok
    depois = contagens()
    for t in recentes:
        assert depois[t] == 0, t
    assert snapshot_gatilhos() == gat
    with h.conectar() as c:
        fn = c.execute(
            "select p.proname from pg_trigger t join pg_proc p on p.oid = t.tgfoid "
            "where t.tgrelid = 'public.localizacoes_viagem'::regclass and t.tgname = 'trg_localizacoes_viagem_imutavel'"
        ).fetchone()
    assert fn is not None and fn[0] == "localizacoes_viagem_proteger"
    popular()
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("select set_config('sisveiculos.expurgo_localizacoes', '1', true)")
            c.execute("delete from public.localizacoes_viagem where true")  # marcador sem idade: recusa
    assert h.sqlstate(exc) == "AG003"
    assert contagens()["localizacoes_viagem"] > 0


# --- (e) o sistema volta a funcionar -----------------------------------------------------------
def test_sistema_funciona_apos_a_limpeza() -> None:
    m = popular()
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            h.inserir_requisicao(c, m.solicitante, m.veiculo)
    assert h.sqlstate(exc) == "23505"  # antes: veiculo ocupado
    assert rodar(executar=True).ok
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
    c = contagens()
    assert c["requisicoes"] == 1 and c["checklists"] == 1 and c["checklist_fotos"] == 2
    assert c["acessos_login"] == 1 and c["auditoria"] > 1
    assert any(a[0] for a in h.auditoria_de("requisicoes", rid))


# --- modo so admins (v_manter_usuarios = false) -------------------------------------------------
def test_execucao_so_admins_apaga_contas_e_mantem_admins_ativos() -> None:
    m = popular()
    inativo = h.criar_usuario("admin-inativo-lmp", "admin", ativo=False)
    antes = _confirmar_populado()
    ids = admins()
    assert m.admin in ids and inativo not in ids
    do_admin = {t: antes[t] - _nao_admin(t, "usuario_id", ids) for t in POR_USUARIO}
    for t in POR_USUARIO:  # o admin tem foto, push e passkey
        assert do_admin[t] > 0, t
    gat = snapshot_gatilhos()
    r = rodar(executar=True, manter_usuarios=False)
    assert r.ok, r.mensagem
    depois = contagens()
    for t in SEMPRE:
        assert depois[t] == 0, t
    assert depois["auditoria"] == 1 and depois["convites"] == 0
    assert depois["perfis"] == depois["auth.users"] == len(ids) >= 1
    assert admins() == ids
    for t in POR_USUARIO:
        assert depois[t] == do_admin[t] and _nao_admin(t, "usuario_id", ids) == 0, t
    for t in MANTIDAS:
        assert depois[t] == antes[t], t
    assert snapshot_gatilhos() == gat
    with h.conectar() as c:
        for u in (m.solicitante, m.autorizador, m.orfao, inativo):
            assert c.execute("select count(*) from auth.users where id = %s", (u,)).fetchone()[0] == 0  # type: ignore[index]
        # dependencias em auth.* sairam pelo cascade do Auth
        assert c.execute(
            "select count(*) from auth.sessions where user_id = any (%s)", ([m.solicitante, m.autorizador],)
        ).fetchone()[0] == 0  # type: ignore[index]
        det = c.execute("select detalhes from public.auditoria where de = 'limpeza_pre_lancamento'").fetchone()[0]  # type: ignore[index]
    assert det["usuarios_mantidos"] is False and det["auditoria_apagada"] is True
    assert det["apagadas"]["auth.users"] == antes["auth.users"] - len(ids)
    assert det["apagadas"]["perfis"] == antes["perfis"] - len(ids)
    assert not _UUID_OU_EMAIL.search(str(det))
    final = {linha[0]: linha[1] for linha in r.tabela_final}
    assert final["perfis"] == final["auth.users"] == len(ids)


def test_so_admins_depois_o_perfis_continua_protegido_e_o_sistema_funciona() -> None:
    popular()
    assert rodar(executar=True, manter_usuarios=False).ok
    estados = {(rel, nome): est for rel, nome, est in snapshot_gatilhos()}
    assert estados[("perfis", "trg_perfis_sem_delete")] in LIGADOS
    m = popular()  # novas contas, requisicoes etc. funcionam
    assert h.status_de(m.ocupada) == "em_uso"
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("delete from public.perfis where id = %s", (m.solicitante,))
    assert h.sqlstate(exc) == "AG003"


def test_so_admins_idempotente() -> None:
    popular()
    assert rodar(executar=True, manter_usuarios=False).ok
    antes, gat = contagens(), snapshot_gatilhos()
    assert total_modo(manter_usuarios=False) == 1  # so o registro da limpeza
    r = rodar(executar=True, manter_usuarios=False)
    assert r.ok, r.mensagem
    assert contagens() == antes and snapshot_gatilhos() == gat


def test_so_admins_com_auditoria_preservada_recusa_ld009_sem_apagar() -> None:
    """A auditoria preservada aponta (FK) para contas que seriam apagadas: recusa, nada muda."""
    popular()
    antes, gat = contagens(), snapshot_gatilhos()
    for executar in (False, True):
        r = rodar(executar=executar, manter_usuarios=False, apagar_auditoria=False)
        assert not r.ok and r.sqlstate == "LD009", r.mensagem
        assert "auditoria.ator_id=" in r.mensagem and not _UUID_OU_EMAIL.search(r.mensagem)
    assert contagens() == antes and snapshot_gatilhos() == gat


def test_sem_admin_ativo_recusa_ld008() -> None:
    """Simula banco sem admin ativo (o guarda do sistema impede chegar a isso de verdade)."""
    alvo = "from public.perfis p where p.role = 'admin' and p.ativo;"
    assert SCRIPT.count(alvo) == 1
    sem_admin = SCRIPT.replace(alvo, alvo[:-1] + " and false;")
    popular()
    antes, gat = contagens(), snapshot_gatilhos()
    for manter in (True, False):
        r = rodar(executar=True, manter_usuarios=manter, script=sem_admin)
        assert not r.ok and r.sqlstate == "LD008" and "admin ativo" in r.mensagem
    assert contagens() == antes and snapshot_gatilhos() == gat


def test_total_de_um_modo_nao_vale_no_outro() -> None:
    popular()
    n_manter = total_modo()
    n_admins = total_modo(manter_usuarios=False)
    assert n_manter != n_admins
    antes = contagens()
    assert rodar(executar=True, manter_usuarios=False, confere=n_manter).sqlstate == "LD007"
    assert rodar(executar=True, confere=n_admins).sqlstate == "LD007"
    assert rodar(executar=True, apagar_auditoria=False, confere=n_manter).sqlstate == "LD007"
    assert contagens() == antes


# --- (f) idempotencia ---------------------------------------------------------------------------
def test_idempotente_rodar_de_novo_sem_dados_nao_falha() -> None:
    popular()
    assert rodar(executar=True).ok
    antes, gat = contagens(), snapshot_gatilhos()
    r = rodar(executar=True)
    assert r.ok, r.mensagem
    assert contagens() == antes and snapshot_gatilhos() == gat
    assert rodar().sqlstate == "LD001"


# --- (g) falha no meio => rollback total; gatilhos religados -------------------------------------
@pytest.mark.parametrize("manter", [True, False])
def test_falha_simulada_depois_dos_deletes_desfaz_tudo(manter: bool) -> None:
    popular()
    antes, gat = _confirmar_populado(), snapshot_gatilhos()
    r = rodar(
        executar=True,
        manter_usuarios=manter,
        antes_de_religar="  raise exception 'falha simulada' using errcode = 'XX999';",
    )
    assert not r.ok and r.sqlstate == "XX999"
    assert contagens() == antes
    assert snapshot_gatilhos() == gat  # nenhum gatilho ficou desligado
    estados = {(rel, nome): est for rel, nome, est in snapshot_gatilhos()}
    assert all(estados[(t, g)] in LIGADOS for t, g in GATILHOS_DESLIGADOS.items())


def test_gatilho_de_delete_inesperado_aborta_sem_mudar_nada() -> None:
    popular()
    antes, gat = contagens(), snapshot_gatilhos()
    with h.conectar() as c:
        c.execute("create function public.zz_noop() returns trigger language plpgsql as $$ begin return old; end $$")
        c.execute("create trigger zz_extra before delete on public.requisicoes_arquivadas for each row execute function public.zz_noop()")
    try:
        r = rodar(executar=True)
        assert not r.ok and r.sqlstate == "LD004" and "zz_extra" in r.mensagem
        assert contagens() == antes
    finally:
        with h.conectar() as c:
            c.execute("drop trigger zz_extra on public.requisicoes_arquivadas")
            c.execute("drop function public.zz_noop()")
    assert snapshot_gatilhos() == gat


def test_tabela_desconhecida_em_public_aborta() -> None:
    popular()
    antes = contagens()
    with h.conectar() as c:
        c.execute("create table public.zz_nova (id int)")
    try:
        for manter in (True, False):
            r = rodar(manter_usuarios=manter)
            assert not r.ok and r.sqlstate == "LD004" and "zz_nova" in r.mensagem
        assert rodar(executar=True).sqlstate == "LD004"
        assert contagens() == antes
    finally:
        with h.conectar() as c:
            c.execute("drop table public.zz_nova")


def test_tabela_mantida_dependente_das_apagadas_aborta() -> None:
    popular()
    antes = contagens()
    with h.conectar() as c:
        c.execute("alter table public.saudacoes add column zz_req uuid references public.requisicoes (id)")
    try:
        r = rodar(executar=True)
        assert not r.ok and r.sqlstate == "LD004" and "saudacoes -> requisicoes" in r.mensagem
        assert contagens() == antes
    finally:
        with h.conectar() as c:
            c.execute("alter table public.saudacoes drop column zz_req")


@pytest.mark.parametrize(
    ("tabela", "gatilho", "manter"),
    [
        ("requisicoes", "trg_requisicoes_sem_delete", True),
        ("desafios_webauthn", "trg_desafios_webauthn_proteger", True),
        ("perfis", "trg_perfis_sem_delete", False),
    ],
)
def test_gatilho_desligado_aborta(tabela: str, gatilho: str, manter: bool) -> None:
    popular()
    antes = contagens()
    with h.conectar() as c:
        c.execute(sql.SQL("alter table {} disable trigger {}").format(_ident(tabela), sql.Identifier(gatilho)))
    try:
        r = rodar(executar=True, manter_usuarios=manter)
        assert not r.ok and r.sqlstate == "LD004" and gatilho in r.mensagem
        assert contagens() == antes
    finally:
        with h.conectar() as c:
            c.execute(sql.SQL("alter table {} enable always trigger {}").format(_ident(tabela), sql.Identifier(gatilho)))


def test_gatilho_de_conta_desligado_nao_importa_no_modo_que_mantem_usuarios() -> None:
    """No modo padrao perfis nao entra: o gatilho dela nem e conferido nem tocado."""
    popular()
    with h.conectar() as c:
        c.execute("alter table public.perfis disable trigger trg_perfis_sem_delete")
    try:
        gat = snapshot_gatilhos()
        assert rodar(executar=True).ok
        assert snapshot_gatilhos() == gat
    finally:
        with h.conectar() as c:
            c.execute("alter table public.perfis enable always trigger trg_perfis_sem_delete")


# --- (h) lacre ----------------------------------------------------------------------------------
def test_lacrado_recusa_ensaio_e_execucao(marcador: None) -> None:
    popular()
    antes = contagens()
    _definir_comentario("sisveiculos:lacrado")
    for kw in ({}, {"executar": True}, {"executar": True, "manter_usuarios": False}):
        r = rodar(**kw)  # type: ignore[arg-type]
        assert not r.ok and r.sqlstate == "LD002" and "LACRADO" in r.mensagem
    assert contagens() == antes


@pytest.mark.parametrize("inicial", [None, PADRAO])
def test_v_lacrar_grava_o_lacre_e_depois_recusa(marcador: None, inicial: str | None) -> None:
    popular()
    _definir_comentario(inicial)  # producao: comentario vazio ou o padrao do Postgres
    r = rodar(executar=True, lacrar=True)
    assert r.ok, r.mensagem
    assert _comentario() == "sisveiculos:lacrado"
    assert any(a[0] == "LACRE do banco" and a[3] == "sisveiculos:lacrado" for a in r.tabela_final)
    anterior = [a for a in r.tabela_final if a[0].startswith("Comentario anterior")][0][3]
    assert anterior == (inicial or "-")
    assert any((inicial or "(vazio)") in a for a in r.avisos)
    popular()
    antes = contagens()
    assert rodar(executar=True).sqlstate == "LD002" and contagens() == antes


def test_desfazer_o_lacre_libera_a_limpeza_de_novo(marcador: None) -> None:
    popular()
    _definir_comentario("sisveiculos:lacrado")
    assert rodar(executar=True).sqlstate == "LD002"
    _definir_comentario(PADRAO)  # desfazer manual (decisao consciente do dono)
    assert rodar(executar=True).ok
    assert contagens()["requisicoes"] == 0


def test_ensaio_com_lacrar_nao_grava_o_lacre(marcador: None) -> None:
    _definir_comentario(None)
    r = rodar(lacrar=True)
    assert r.sqlstate == "LD001" and "simulado no ensaio" in r.mensagem
    assert _comentario() in (None, "")


def test_v_lacrar_nao_sobrescreve_o_marcador_descartavel(marcador: None) -> None:
    popular()
    antes = contagens()
    for kw in ({}, {"executar": True}):
        r = rodar(lacrar=True, **kw)  # type: ignore[arg-type]
        assert not r.ok and r.sqlstate == "LD006"
    assert _comentario() == h.MARCADOR_BANCO
    assert contagens() == antes


def test_v_lacrar_nao_sobrescreve_outro_comentario(marcador: None) -> None:
    _definir_comentario("alguma outra coisa")
    r = rodar(executar=True, lacrar=True)
    assert r.sqlstate == "LD006" and _comentario() == "alguma outra coisa"
    assert rodar_lacre(FRASE_LACRE).sqlstate == "LD006" and _comentario() == "alguma outra coisa"


@pytest.mark.parametrize("inicial", [None, PADRAO])
def test_lacrar_producao_script(marcador: None, inicial: str | None) -> None:
    r = rodar_lacre(FRASE_LACRE)
    assert r.sqlstate == "LD006" and _comentario() == h.MARCADOR_BANCO
    _definir_comentario(inicial)
    assert rodar_lacre("").sqlstate == "LD003"
    assert _comentario() == inicial
    ok = rodar_lacre(FRASE_LACRE)
    assert ok.ok, ok.mensagem
    assert ok.tabela_final == [("sisveiculos:lacrado", inicial or "(vazio)")]
    assert rodar_lacre(FRASE_LACRE).sqlstate == "LD002"


# --- (i) auditoria preservada (opcional) --------------------------------------------------------
def test_sem_apagar_auditoria_preserva_todas_as_linhas() -> None:
    popular()
    antes = _confirmar_populado()
    with h.conectar() as c:
        ids = c.execute("select id from public.auditoria order by id").fetchall()
    r = rodar(executar=True, apagar_auditoria=False)
    assert r.ok, r.mensagem
    depois = contagens()
    assert depois["auditoria"] == antes["auditoria"] + 1  # + o registro da limpeza
    for t in SEMPRE:
        assert depois[t] == 0
    with h.conectar() as c:
        assert c.execute("select id from public.auditoria order by id").fetchall()[:-1] == ids
    estados = {(rel, nome): est for rel, nome, est in snapshot_gatilhos()}
    assert estados[("auditoria", "trg_auditoria_append_only")] == "A"
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("delete from public.auditoria where true")
    assert h.sqlstate(exc) == "AG003"


# --- segunda confirmacao por contagem (LD007) ----------------------------------------------------
def test_ensaio_destaca_o_numero_e_ele_permite_executar() -> None:
    popular()
    ens = rodar()
    m = re.search(r"COPIE ESTE NUMERO PARA v_confere_total: (\d+)", ens.mensagem)
    assert m and int(m.group(1)) == total_modo() > 0
    assert rodar(executar=True, confere=int(m.group(1))).ok


@pytest.mark.parametrize("modo", ["padrao", "menos_um", "a_mais", "a_menos", "zero_com_dados"])
def test_contagem_errada_ou_ausente_aborta_com_ld007(modo: str) -> None:
    popular()
    n = total_modo()
    confere = {"padrao": None, "menos_um": -1, "a_mais": n + 1, "a_menos": n - 1, "zero_com_dados": 0}[modo]
    antes, gat = contagens(), snapshot_gatilhos()
    r = rodar(executar=True, confere=confere)
    assert not r.ok and r.sqlstate == "LD007"
    assert contagens() == antes and snapshot_gatilhos() == gat


def test_total_menos_um_e_zero_com_dados_nunca_passam_mesmo_apos_limpar() -> None:
    popular()
    assert rodar(executar=True).ok
    assert total_modo() == 1  # sobra o registro da limpeza
    for ruim in (-1, None, 0, 2):
        assert rodar(executar=True, confere=ruim).sqlstate == "LD007"
    assert rodar(executar=True, confere=1).ok


def test_segunda_confirmacao_por_total_cobre_tabelas_alem_de_requisicoes() -> None:
    popular()
    antes = contagens()
    for ruim in (0, antes["requisicoes"]):
        assert rodar(executar=True, confere=ruim).sqlstate == "LD007"
    assert contagens() == antes
    assert rodar(executar=True, confere=total_modo()).ok


def test_ensaio_nao_exige_a_contagem() -> None:
    assert rodar(confere=None).sqlstate == "LD001"


def test_confirmacao_com_ponto_e_virgula_e_aspas_nao_quebra_o_helper() -> None:
    antes = contagens()
    r = rodar(executar=True, confirmacao="a;b'c; v_executar := true;")
    assert r.sqlstate == "LD003" and contagens() == antes


# --- registro duravel na auditoria --------------------------------------------------------------
@pytest.mark.parametrize("apagar_auditoria", [True, False])
def test_registro_duravel_na_auditoria_sem_dados_pessoais(apagar_auditoria: bool) -> None:
    popular()
    antes = contagens()
    with h.conectar() as c:
        previas = c.execute(
            "select count(*) from public.auditoria where de = 'limpeza_pre_lancamento'"
        ).fetchone()[0]  # type: ignore[index]
    assert rodar(executar=True, apagar_auditoria=apagar_auditoria).ok
    with h.conectar() as c:
        linhas = c.execute(
            "select tabela, registro_id, acao, ator_id, de, para, detalhes from public.auditoria "
            "where de = 'limpeza_pre_lancamento' order by id"
        ).fetchall()
    assert len(linhas) == (1 if apagar_auditoria else previas + 1)
    tabela, registro_id, acao, ator_id, de, para, detalhes = linhas[-1]
    assert (tabela, registro_id, acao, ator_id, de, para) == ("perfis", None, "acao_administrativa", None, de, None)
    esperado = {t: antes[t] for t in SEMPRE} | ({"auditoria": antes["auditoria"]} if apagar_auditoria else {})
    assert detalhes == {
        "apagadas": esperado,
        "auditoria_apagada": apagar_auditoria,
        "usuarios_mantidos": True,
        "lacrado": False,
    }
    assert not _UUID_OU_EMAIL.search(str(detalhes))


def test_registro_duravel_registra_lacrado(marcador: None) -> None:
    popular()
    _definir_comentario(None)
    assert rodar(executar=True, lacrar=True).ok
    with h.conectar() as c:
        det = c.execute("select detalhes from public.auditoria where de = 'limpeza_pre_lancamento'").fetchone()
    assert det is not None and det[0]["lacrado"] is True


# --- LD005 exercitado ---------------------------------------------------------------------------
def test_ld005_gatilho_extra_desligado_desfaz_tudo() -> None:
    popular()
    antes, gat = contagens(), snapshot_gatilhos()
    r = rodar(
        executar=True,
        antes_de_religar="  alter table public.requisicoes disable trigger trg_requisicoes_validar;",
    )
    assert not r.ok and r.sqlstate == "LD005" and "gatilhos" in r.mensagem
    assert contagens() == antes and snapshot_gatilhos() == gat


def test_ld005_tabela_mantida_alterada_desfaz_tudo() -> None:
    popular()
    antes, gat = contagens(), snapshot_gatilhos()
    r = rodar(
        executar=True,
        antes_de_religar=(
            "  insert into public.veiculos (descricao, alterado_por) "
            "select 'zz-ld005', id from public.perfis where role = 'admin' and ativo limit 1;"
        ),
    )
    assert not r.ok and r.sqlstate == "LD005" and "veiculos" in r.mensagem
    assert contagens() == antes and snapshot_gatilhos() == gat


def test_ld005_conta_mantida_alterada_desfaz_tudo() -> None:
    """Uma conta a mais no Auth depois do DELETE (modo padrao, contas mantidas) e pega na verificacao."""
    popular()
    antes, gat = contagens(), snapshot_gatilhos()
    r = rodar(
        executar=True,
        antes_de_religar=(
            "  insert into auth.users (id, email, email_confirmed_at) "
            "values (gen_random_uuid(), 'zz-ld005@teste.invalid', now());"
        ),
    )
    assert not r.ok and r.sqlstate == "LD005" and "auth.users" in r.mensagem
    assert contagens() == antes and snapshot_gatilhos() == gat


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


# --- parametro NULL nao contorna as travas -------------------------------------------------------
_TIPOS = {
    "v_executar": "boolean",
    "v_confirmacao": "text",
    "v_confere_total": "int",
    "v_manter_usuarios": "boolean",
    "v_apagar_auditoria": "boolean",
    "v_lacrar": "boolean",
    "v_ref_esperado": "text",
}


@pytest.mark.parametrize("parametro", list(_TIPOS))
def test_parametro_nulo_e_recusado_sem_mudar_nada(parametro: str) -> None:
    popular()
    antes, gat = contagens(), snapshot_gatilhos()
    t = _param(SCRIPT, "v_executar", "boolean", "true")
    t = _param(t, "v_confirmacao", "text", _lit(frase()))
    t = _param(t, "v_confere_total", "int", str(total_modo()))
    t = _param(t, parametro, _TIPOS[parametro], "NULL")
    r = _rodar_texto(t)
    assert not r.ok and r.sqlstate == "LD003" and "nulo" in r.mensagem
    assert contagens() == antes and snapshot_gatilhos() == gat


def test_v_executar_nulo_com_dados_nao_apaga_nem_comita() -> None:
    """Regressao: `if not NULL` e NULL fazia o ensaio nao abortar e o delete ser comitado."""
    popular()
    antes = _confirmar_populado()
    r = _rodar_texto(_param(SCRIPT, "v_executar", "boolean", "NULL"))
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
    t2 = _param(_param(LACRAR, "v_confirmacao", "text", "NULL"), "v_ref_esperado", "text", "NULL")
    r2 = _rodar_texto(t2)
    assert r2.sqlstate == "LD003" and _comentario() in (None, "")
