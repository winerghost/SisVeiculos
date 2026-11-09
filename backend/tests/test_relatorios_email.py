"""Envio de relatórios por e-mail: permissões, cota, assíncrono, falhas do SMTP e privacidade."""

from __future__ import annotations

import logging
import smtplib
import socket
import threading
import uuid
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from app.errors import ErroInterno
from app.modelos import Configuracoes
from app.notificacoes.email import (
    ConfigSmtp,
    EnviadorSmtp,
    FalhaEmail,
    codigo_da_excecao,
    limpar_cabecalho,
)
from app.services import relatorios as modulo
from tests.test_relatorios import BASE, app, get, mk, nova_req  # noqa: F401  (fixtures)

SENHA = "SENHA-SECRETA-123"


class Sincrono(Executor):
    """Executa na hora, na thread do teste (resultado determinístico)."""

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[no-untyped-def]
        f: Future[Any] = Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except BaseException as e:  # noqa: BLE001
            f.set_exception(e)
        return f


class SmtpFalso:
    """Substitui smtplib.SMTP/SMTP_SSL: nada sai da máquina."""

    instancias: list[SmtpFalso] = []
    falha_em: str | None = None  # "conectar" | "login" | "enviar"
    excecao: Exception | None = None
    bloqueio: threading.Event | None = None
    dentro: threading.Event | None = None

    def __init__(self, host: str, port: int, timeout: float | None = None, **kw: Any) -> None:
        self.host, self.port, self.timeout, self.kw = host, port, timeout, kw
        self.chamadas: list[str] = []
        self.login_args: tuple[str, str] | None = None
        self.mensagens: list[Any] = []
        type(self).instancias.append(self)
        self._falhar("conectar")

    def _falhar(self, etapa: str) -> None:
        if type(self).falha_em == etapa and type(self).excecao is not None:
            raise type(self).excecao

    def __enter__(self) -> SmtpFalso:
        return self

    def __exit__(self, *a: Any) -> None:
        self.chamadas.append("quit")

    def ehlo(self) -> None:
        self.chamadas.append("ehlo")

    def starttls(self, context: Any = None) -> None:
        self.chamadas.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.chamadas.append("login")
        self.login_args = (user, password)
        self._falhar("login")

    def send_message(self, msg: Any) -> None:
        if type(self).dentro is not None:
            type(self).dentro.set()
        if type(self).bloqueio is not None:
            type(self).bloqueio.wait(5)
        self._falhar("enviar")
        self.mensagens.append(msg)
        self.chamadas.append("send")


@pytest.fixture(autouse=True)
def smtp(monkeypatch):  # type: ignore[no-untyped-def]
    SmtpFalso.instancias = []
    SmtpFalso.falha_em, SmtpFalso.excecao = None, None
    SmtpFalso.bloqueio = SmtpFalso.dentro = None
    monkeypatch.setattr(smtplib, "SMTP", SmtpFalso)
    monkeypatch.setattr(smtplib, "SMTP_SSL", SmtpFalso)
    monkeypatch.setattr(modulo, "EXECUTOR_EMAIL", Sincrono())
    return SmtpFalso


@pytest.fixture
def ligado(repo):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(relatorio_email=True)
    return repo


def _dominios(client, monkeypatch, valor: str) -> None:  # type: ignore[no-untyped-def]
    """Define os domínios institucionais no Config da app (fonte única; sem ler o ambiente)."""
    d = client.application.extensions["sisveiculos"]
    lista = [x.strip().lstrip("@").lower() for x in valor.split(",") if x.strip()]
    monkeypatch.setattr(d, "config", replace(d.config, email_dominios_relatorio=lista))


def enviar(client, auth, quem, tipo="pendencias", **corpo):  # type: ignore[no-untyped-def]
    corpo.setdefault("formato", "csv")
    corpo.setdefault("filtros", {})
    return client.post(f"{BASE}/{tipo}/email", headers=auth(quem.id), json=corpo)


# ----------------------------------------------------------------------- destinatários
def test_destinatarios_staff_lista_ativos_sem_email(client, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    inativo = repo.novo_perfil("autorizador", ativo=False)
    r = get(client, auth, c.aut, f"{BASE}/destinatarios")
    assert r.status_code == 200
    itens = r.get_json()["itens"]
    ids = {i["id"] for i in itens}
    assert {c.aut.id, c.adm.id} <= ids and inativo.id not in ids
    assert c.sol.id not in ids and c.outro.id not in ids  # solicitantes nunca
    assert all(set(i) == {"id", "nome"} for i in itens)
    assert "@" not in r.get_data(as_text=True)


def test_destinatarios_solicitante_so_ele_mesmo(client, auth, c, ligado):  # type: ignore[no-untyped-def]
    j = get(client, auth, c.sol, f"{BASE}/destinatarios").get_json()
    assert j["itens"] == [{"id": c.sol.id, "nome": c.sol.nome}]


def test_destinatarios_exige_canal_ligado(client, auth, c):  # type: ignore[no-untyped-def]
    r = get(client, auth, c.aut, f"{BASE}/destinatarios")
    assert r.status_code == 404 and r.get_json()["erro"]["codigo"] == "recurso_desligado"


# ----------------------------------------------------------------------------- recusas
def test_recurso_desligado(client, auth, c, repo):  # type: ignore[no-untyped-def]
    r = enviar(client, auth, c.aut, destinatario_id=c.aut.id)
    assert r.status_code == 404 and r.get_json()["erro"]["codigo"] == "recurso_desligado"
    assert repo.envios == {} and SmtpFalso.instancias == []


def test_email_enabled_false_no_ambiente(mk, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    cli = mk(email_enabled=False).test_client()
    r = enviar(cli, auth, c.aut, destinatario_id=c.aut.id)
    assert r.status_code == 404 and r.get_json()["erro"]["codigo"] == "recurso_desligado"
    assert repo.envios == {} and SmtpFalso.instancias == []


def test_permissao_do_relatorio_vale_no_email(client, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    assert enviar(client, auth, c.sol, "resumo", destinatario_id=c.sol.id).status_code == 403
    assert enviar(client, auth, c.aut, "auditoria", destinatario_id=c.aut.id).status_code == 403
    assert repo.envios == {}


def test_solicitante_so_envia_para_si(client, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    r = enviar(client, auth, c.sol, destinatario_id=c.outro.id)
    assert r.status_code == 403 and repo.envios == {}
    assert enviar(client, auth, c.sol, destinatario_id=c.sol.id).status_code == 202


def test_destinatario_inativo_ou_inexistente(client, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    inativo = repo.novo_perfil("autorizador", ativo=False)
    for destino in (inativo.id, str(uuid.uuid4())):
        r = enviar(client, auth, c.aut, destinatario_id=destino)
        assert r.status_code == 422 and r.get_json()["erro"]["codigo"] == "destinatario_invalido"
    assert repo.envios == {}


def test_destinatario_sem_email_cadastrado(client, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    repo.contas_auth[c.adm.id]["email"] = None
    r = enviar(client, auth, c.aut, destinatario_id=c.adm.id)
    assert r.status_code == 422 and repo.envios == {}


@pytest.mark.parametrize(
    "corpo",
    [
        {"formato": "csv"},  # sem destinatário
        {"formato": "csv", "destinatario_id": "nao-uuid"},
        {"formato": "pdf", "destinatario_id": "X"},
        {"formato": "csv", "destinatario_id": "X", "email": "a@b.com"},  # campo extra
        {"formato": "csv", "destinatario_id": "X", "filtros": [1]},
        {"formato": "csv", "destinatario_id": "X", "filtros": {"unidade": {"a": 1}}},
    ],
)
def test_corpo_invalido_400(client, auth, c, ligado, repo, corpo):  # type: ignore[no-untyped-def]
    if corpo.get("destinatario_id") == "X":
        corpo = {**corpo, "destinatario_id": c.aut.id}
    r = client.post(f"{BASE}/pendencias/email", headers=auth(c.aut.id), json=corpo)
    assert r.status_code == 400 and repo.envios == {}


def test_corpo_nao_json_400(client, auth, c, ligado):  # type: ignore[no-untyped-def]
    r = client.post(f"{BASE}/pendencias/email", headers=auth(c.aut.id), data="texto")
    assert r.status_code == 400


def test_filtros_invalidos_no_corpo_400(client, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    r = enviar(client, auth, c.aut, destinatario_id=c.aut.id, filtros={"unidade": "XX"})
    assert r.status_code == 400 and repo.envios == {}


def test_excesso_de_linhas_nao_gasta_cota(mk, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    for _ in range(4):
        nova_req(repo, c.sol)
    cli = mk(relatorio_max_linhas=3).test_client()
    r = enviar(cli, auth, c.aut, "movimentacao", destinatario_id=c.aut.id)
    assert r.status_code == 422 and r.get_json()["erro"]["codigo"] == "relatorio_muito_grande"
    assert repo.envios == {}


# ------------------------------------------------------------------------------- cota
def test_cota_por_hora_429(mk, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    cli = mk(email_max_per_hour=2).test_client()
    codigos = [enviar(cli, auth, c.aut, destinatario_id=c.aut.id).status_code for _ in range(3)]
    assert codigos == [202, 202, 429]
    erro = enviar(cli, auth, c.aut, destinatario_id=c.aut.id).get_json()["erro"]
    assert erro["codigo"] == "cota_envio_excedida" and "por hora" in erro["mensagem"]
    assert len(repo.envios) == 2


def test_cota_sob_concorrencia_so_uma_reserva(mk, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    repo.dedupe_envios = False  # envios idênticos simultâneos seriam deduplicados; aqui é a cota
    cli = mk(email_max_per_hour=1).test_client()
    resultados: list[int] = []
    barreira = threading.Barrier(5)

    def tentar() -> None:
        barreira.wait(5)
        resultados.append(enviar(cli, auth, c.aut, destinatario_id=c.aut.id).status_code)

    ts = [threading.Thread(target=tentar) for _ in range(5)]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert sorted(resultados) == [202, 429, 429, 429, 429] and len(repo.envios) == 1


def test_rate_limit_do_endpoint_de_email(mk, auth, c, ligado):  # type: ignore[no-untyped-def]
    cli = mk(limite_relatorios_email="1 per minute").test_client()
    assert enviar(cli, auth, c.aut, destinatario_id=c.aut.id).status_code == 202
    r = enviar(cli, auth, c.aut, destinatario_id=c.aut.id)
    assert r.status_code == 429 and r.get_json()["erro"]["codigo"] == "limite_excedido"


# ----------------------------------------------------------------------------- sucesso
def test_sucesso_marca_enviado_e_nao_vaza_dados(client, auth, c, ligado, repo, caplog):  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    nova_req(repo, c.adm)
    r = enviar(
        client,
        auth,
        c.aut,
        "movimentacao",
        destinatario_id=c.adm.id,
        formato="xlsx",
        filtros={"unidade": "PU"},
    )
    assert r.status_code == 202
    j = r.get_json()
    assert set(j) == {"id", "status"} and j["status"] == "pendente"
    assert [m[1] for m in repo.marcas] == ["enviando", "enviado"]
    assert repo.envios[j["id"]]["tentativas"] == 1

    (smtp,) = SmtpFalso.instancias
    assert smtp.chamadas == ["ehlo", "starttls", "ehlo", "login", "send", "quit"]
    assert smtp.login_args == ("usuario-smtp", SENHA)
    msg = smtp.mensagens[0]
    assert msg["To"].addresses[0].addr_spec == repo.contas_auth[c.adm.id]["email"]
    assert msg["From"].addresses[0].addr_spec == "sistema@teste.gov.br"
    assert "Movimentação" in str(msg["Subject"])
    anexo = next(msg.iter_attachments())
    assert anexo.get_filename().startswith("relatorio-movimentacao-")
    assert anexo.get_filename().endswith(".xlsx") and anexo.get_content()[:2] == b"PK"
    corpo = msg.get_body().get_content()
    assert c.adm.nome not in corpo and "Segue o relatório" in corpo

    # status consultável pelo solicitante do envio
    s = get(client, auth, c.aut, f"{BASE}/envios/{j['id']}").get_json()
    assert s == {"id": j["id"], "status": "enviado", "erro_codigo": None, "mensagem": None}
    # nem senha nem endereço em log
    assert SENHA not in caplog.text and repo.contas_auth[c.adm.id]["email"] not in caplog.text


def test_auditoria_do_envio_sem_email(client, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    r = enviar(client, auth, c.aut, destinatario_id=c.adm.id, filtros={"unidade": "PU"})
    (ev,) = repo.eventos_de("relatorio_enviado")
    assert ev["tabela"] == "envios_email" and ev["registro_id"] == r.get_json()["id"]
    assert ev["ator_id"] == c.aut.id
    d = ev["detalhes"]
    assert set(d) == {"tipo", "filtros", "formato", "destinatario_id", "total_linhas"}
    assert d["destinatario_id"] == c.adm.id and d["tipo"] == "pendencias"
    assert "@" not in str(d)
    assert "@" not in str(repo.envios[r.get_json()["id"]]["filtros"])


def test_escopo_do_solicitante_vale_no_email(client, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol)
    nova_req(repo, c.outro)
    enviar(
        client,
        auth,
        c.sol,
        "movimentacao",
        destinatario_id=c.sol.id,
        filtros={"usuario": c.outro.id, "incluir_nomes": True},
    )
    p = repo.chamadas_de("movimentacao")[-1]
    assert p["usuario"] == c.sol.id and p["incluir_nomes"] is False
    assert "usuario" not in repo.envios[next(iter(repo.envios))]["filtros"]


def test_ssl_usa_smtp_ssl_e_none_nao_negocia_tls(mk, auth, c, ligado):  # type: ignore[no-untyped-def]
    cli = mk(email_security="none", email_user="").test_client()
    enviar(cli, auth, c.aut, destinatario_id=c.aut.id)
    assert SmtpFalso.instancias[-1].chamadas == ["send", "quit"]  # sem starttls nem login
    cli = mk(email_security="ssl").test_client()
    enviar(cli, auth, c.aut, destinatario_id=c.aut.id)
    assert "starttls" not in SmtpFalso.instancias[-1].chamadas
    assert SmtpFalso.instancias[-1].timeout == 15


# ----------------------------------------------------------------------------- falhas
CASOS = [
    (
        "login",
        smtplib.SMTPAuthenticationError(535, b"bad user ana@exemplo.gov.br"),
        "smtp_autenticacao",
    ),
    ("conectar", TimeoutError("timed out"), "smtp_timeout"),
    ("conectar", TimeoutError("t"), "smtp_timeout"),
    ("conectar", ConnectionRefusedError("recusou ana@exemplo.gov.br"), "smtp_conexao"),
    ("conectar", socket.gaierror("dns"), "smtp_conexao"),
    ("conectar", smtplib.SMTPConnectError(421, b"fechado"), "smtp_conexao"),
    ("enviar", smtplib.SMTPServerDisconnected("caiu"), "smtp_conexao"),
    (
        "enviar",
        smtplib.SMTPRecipientsRefused({"ana@exemplo.gov.br": (550, b"no")}),
        "smtp_destinatario_recusado",
    ),
    (
        "enviar",
        smtplib.SMTPSenderRefused(550, b"x", "sistema@teste.gov.br"),
        "smtp_remetente_recusado",
    ),
    ("enviar", smtplib.SMTPDataError(554, b"spam ana@exemplo.gov.br"), "smtp_recusado"),
    ("enviar", smtplib.SMTPException("outro"), "smtp_erro"),
    ("enviar", RuntimeError("inesperado ana@exemplo.gov.br"), "smtp_erro"),
]


@pytest.mark.parametrize(("etapa", "excecao", "codigo"), CASOS)
def test_falha_smtp_marca_falhou_sem_vazar(  # type: ignore[no-untyped-def]
    client, auth, c, ligado, repo, caplog, etapa, excecao, codigo
):
    caplog.set_level(logging.DEBUG)
    SmtpFalso.falha_em, SmtpFalso.excecao = etapa, excecao
    r = enviar(client, auth, c.aut, destinatario_id=c.adm.id)
    assert r.status_code == 202  # a falha é do envio em segundo plano, não da requisição
    envio = repo.envios[r.get_json()["id"]]
    assert envio["status"] == "falhou" and envio["erro_codigo"] == codigo
    s = get(client, auth, c.aut, f"{BASE}/envios/{envio['id']}").get_json()
    assert s["status"] == "falhou" and s["erro_codigo"] == codigo and s["mensagem"]
    email = repo.contas_auth[c.adm.id]["email"]
    for segredo in (SENHA, email, "ana@exemplo.gov.br", "spam", "inesperado"):
        assert segredo not in caplog.text
        assert segredo not in str(s)


def test_certificado_invalido_vira_smtp_tls():  # type: ignore[no-untyped-def]
    import ssl

    assert codigo_da_excecao(ssl.SSLError("CERTIFICATE_VERIFY_FAILED")) == "smtp_tls"


def test_executor_encerrado_marca_falha(client, auth, c, ligado, repo, monkeypatch):  # type: ignore[no-untyped-def]
    morto = ThreadPoolExecutor(1)
    morto.shutdown()
    monkeypatch.setattr(modulo, "EXECUTOR_EMAIL", morto)
    r = enviar(client, auth, c.aut, destinatario_id=c.aut.id)
    assert r.status_code == 503
    (envio,) = repo.envios.values()
    assert envio["status"] == "falhou" and envio["erro_codigo"] == "fila_indisponivel"


def test_falha_na_auditoria_do_envio_nao_envia(client, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    repo.falha_auditoria = ErroInterno("falha")
    r = enviar(client, auth, c.aut, destinatario_id=c.aut.id)
    assert r.status_code == 503 and SmtpFalso.instancias == []
    (envio,) = repo.envios.values()
    assert envio["status"] == "falhou" and envio["erro_codigo"] == "auditoria_falhou"


# ------------------------------------------------------------------------ assíncrono
def test_requisicao_nao_espera_o_smtp(client, auth, c, ligado, repo, monkeypatch):  # type: ignore[no-untyped-def]
    pool = ThreadPoolExecutor(1)
    monkeypatch.setattr(modulo, "EXECUTOR_EMAIL", pool)
    SmtpFalso.bloqueio, SmtpFalso.dentro = threading.Event(), threading.Event()
    r = enviar(client, auth, c.aut, destinatario_id=c.aut.id)  # volta com o SMTP ainda travado
    assert r.status_code == 202
    eid = r.get_json()["id"]
    assert SmtpFalso.dentro.wait(5)
    s = get(client, auth, c.aut, f"{BASE}/envios/{eid}").get_json()
    assert s["status"] == "enviando"
    SmtpFalso.bloqueio.set()
    pool.shutdown(wait=True)
    assert repo.envios[eid]["status"] == "enviado"


# ------------------------------------------------------------------------------- IDOR
def test_status_do_envio_so_para_quem_pediu(client, auth, c, ligado):  # type: ignore[no-untyped-def]
    eid = enviar(client, auth, c.sol, destinatario_id=c.sol.id).get_json()["id"]
    assert get(client, auth, c.sol, f"{BASE}/envios/{eid}").status_code == 200
    for outro in (c.outro, c.aut, c.adm):
        r = get(client, auth, outro, f"{BASE}/envios/{eid}")
        assert r.status_code == 404 and r.get_json()["erro"]["codigo"] == "nao_encontrado"
    for ruim in (str(uuid.uuid4()), "nao-uuid"):
        assert get(client, auth, c.sol, f"{BASE}/envios/{ruim}").status_code == 404


# ----------------------------------------------------------------- unidade do SMTP
def smtp_cfg(**kw: Any) -> ConfigSmtp:
    base: dict[str, Any] = {
        "host": "h",
        "port": 587,
        "user": "u",
        "password": SENHA,
        "security": "starttls",
        "remetente": "sistema@teste.gov.br",
        "nome_remetente": "Sistema",
        "timeout": 5,
    }
    return ConfigSmtp(**{**base, **kw})


def test_cabecalhos_sem_injecao():  # type: ignore[no-untyped-def]
    assert limpar_cabecalho("Relatório\r\nBcc: x@y.com") == "Relatório Bcc: x@y.com"
    env = EnviadorSmtp(smtp_cfg(nome_remetente="Sistema\r\nBcc: espiao@x.com"))
    msg = env._montar(
        "ana@exemplo.gov.br", "Assunto\r\nBcc: e@x.com", "corpo", "a\r\nb.csv", b"x", "text/csv"
    )
    for cab in ("From", "To", "Subject"):
        assert "\r" not in str(msg[cab]) and "\n" not in str(msg[cab])
    assert msg["Bcc"] is None
    assert "\r\nBcc" not in msg.as_string()


@pytest.mark.parametrize(
    "destino",
    [
        "ana@exemplo.gov.br\r\nBcc: x@y.com",
        "a@b.com,c@d.com",
        "sem-arroba",
        "a b@c.com",
        "<a@b.com>",
        "",
    ],
)
def test_destinatario_com_injecao_recusado(destino):  # type: ignore[no-untyped-def]
    with pytest.raises(FalhaEmail) as e:
        EnviadorSmtp(smtp_cfg()).enviar(
            destino=destino,
            assunto="a",
            corpo="b",
            anexo_nome="c.csv",
            anexo=b"x",
            mimetype="text/csv",
        )
    assert e.value.codigo == "destinatario_invalido" and SmtpFalso.instancias == []


def test_repr_da_config_nao_mostra_senha():  # type: ignore[no-untyped-def]
    assert SENHA not in repr(smtp_cfg())


# ------------------------------------------- staff nunca envia a solicitante alheio (M1)
MSG_M1 = (
    "Relatórios de autorizadores e administradores só podem ser enviados a autorizadores "
    "ou administradores."
)


@pytest.mark.parametrize(
    ("quem", "tipo"),
    [
        ("aut", "movimentacao"),
        ("aut", "pendencias"),
        ("aut", "resumo"),
        ("adm", "movimentacao"),
        ("adm", "auditoria"),
    ],
)
def test_staff_nao_envia_a_solicitante_alheio(client, auth, c, ligado, repo, quem, tipo):  # type: ignore[no-untyped-def]
    r = enviar(client, auth, getattr(c, quem), tipo, destinatario_id=c.sol.id)
    assert r.status_code == 422
    erro = r.get_json()["erro"]
    assert erro["codigo"] == "destinatario_sem_permissao_pii" and erro["mensagem"] == MSG_M1
    assert repo.envios == {} and SmtpFalso.instancias == []


def test_staff_envia_a_staff_e_a_si_mesmo_com_pii(client, auth, c, ligado, monkeypatch):  # type: ignore[no-untyped-def]
    _dominios(client, monkeypatch, "agu.gov.br")
    filtros = {"incluir_nomes": True, "incluir_trajeto": True}
    for destino in (c.adm, c.aut):
        r = enviar(client, auth, c.aut, "movimentacao", destinatario_id=destino.id, filtros=filtros)
        assert r.status_code == 202


def test_solicitante_continua_so_para_si(client, auth, c, ligado):  # type: ignore[no-untyped-def]
    assert enviar(client, auth, c.sol, destinatario_id=c.sol.id).status_code == 202
    assert enviar(client, auth, c.sol, destinatario_id=c.aut.id).status_code == 403


def test_destinatarios_truncado(client, auth, c, ligado, monkeypatch):  # type: ignore[no-untyped-def]
    j = get(client, auth, c.aut, f"{BASE}/destinatarios").get_json()
    assert j["truncado"] is False
    monkeypatch.setattr(modulo, "MAX_DESTINATARIOS", 1)
    j = get(client, auth, c.aut, f"{BASE}/destinatarios").get_json()
    assert j["truncado"] is True and len(j["itens"]) == 1
    sol = get(client, auth, c.sol, f"{BASE}/destinatarios").get_json()
    assert sol["truncado"] is False and len(sol["itens"]) == 1


# ------------------------------------------------------------ cota por usuário (M2)
def test_cota_por_usuario_por_hora(mk, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    cli = mk(email_max_per_hour=100, email_max_per_user_hour=5).test_client()
    for _ in range(5):
        assert enviar(cli, auth, c.aut, destinatario_id=c.adm.id).status_code == 202
    r = enviar(cli, auth, c.aut, destinatario_id=c.adm.id)
    assert r.status_code == 429
    erro = r.get_json()["erro"]
    assert erro["codigo"] == "cota_envio_excedida" and erro["mensagem"].startswith("Você atingiu")
    # outro usuário não é afetado (inclusive solicitante, para si mesmo)
    assert enviar(cli, auth, c.adm, destinatario_id=c.adm.id).status_code == 202
    assert enviar(cli, auth, c.sol, destinatario_id=c.sol.id).status_code == 202


def test_cota_global_mantem_mensagem_propria(mk, auth, c, ligado):  # type: ignore[no-untyped-def]
    cli = mk(email_max_per_hour=1).test_client()
    enviar(cli, auth, c.aut, destinatario_id=c.adm.id)
    erro = enviar(cli, auth, c.adm, destinatario_id=c.adm.id).get_json()["erro"]
    assert erro["codigo"] == "cota_envio_excedida" and "Você" not in erro["mensagem"]


def test_repo_envia_parametro_de_cota_por_usuario():  # type: ignore[no-untyped-def]
    from unittest.mock import MagicMock

    from app.repositorios.supabase_repo import SupabaseRepositorio

    r = SupabaseRepositorio.__new__(SupabaseRepositorio)
    r._c = MagicMock()
    r._c.rpc.return_value.execute.return_value.data = {"id": "x"}
    r.reservar_envio_email(
        solicitante="a",
        destinatario="b",
        tipo="resumo",
        formato="csv",
        filtros={},
        max_por_hora=30,
        max_por_usuario_hora=5,
    )
    assert r._c.rpc.call_args.args[1]["p_max_por_usuario_hora"] == 5


# ------------------------------------------------------------------- SMTP sem TLS (B1)
def test_login_so_com_tls(mk, auth, c, ligado):  # type: ignore[no-untyped-def]
    cli = mk(email_security="none", email_user="u", email_password=SENHA).test_client()
    enviar(cli, auth, c.aut, destinatario_id=c.adm.id)
    assert "login" not in SmtpFalso.instancias[-1].chamadas
    assert SmtpFalso.instancias[-1].login_args is None
    cli = mk(email_security="ssl").test_client()
    enviar(cli, auth, c.aut, destinatario_id=c.adm.id)
    assert "login" in SmtpFalso.instancias[-1].chamadas


def test_producao_recusa_none_com_credencial():  # type: ignore[no-untyped-def]
    from app.config import Config

    base = {
        "supabase_url": "https://x.supabase.co",
        "supabase_service_role_key": "k",
        "jwks_url": "https://x.supabase.co/auth/v1/.well-known/jwks.json",
        "jwt_issuer": "https://x.supabase.co/auth/v1",
        "email_enabled": True,
        "email_host": "h",
        "email_from": "a@b.gov.br",
        "email_security": "none",
    }
    # S-08: none é recusado em produção, com ou sem credencial.
    for extra in ({}, {"email_user": "u"}, {"email_password": "p"}):
        with pytest.raises(RuntimeError, match="EMAIL_SECURITY=none"):
            Config(**base, **extra).validar_producao()


# ------------------------------------------------------------------ filtros booleanos
@pytest.mark.parametrize(("bruto", "esperado"), [("true", True), ("false", False)])
def test_bool_na_query(client, auth, c, repo, bruto, esperado):  # type: ignore[no-untyped-def]
    r = get(client, auth, c.aut, f"{BASE}/movimentacao/previa", incluir_nomes=bruto)
    assert r.status_code == 200 and r.get_json()["filtros"]["incluir_nomes"] is esperado
    assert repo.chamadas_de("movimentacao")[-1]["incluir_nomes"] is esperado


@pytest.mark.parametrize("valor", [True, False])
def test_bool_no_corpo_json(client, auth, c, ligado, repo, valor, monkeypatch):  # type: ignore[no-untyped-def]
    _dominios(client, monkeypatch, "agu.gov.br")
    r = enviar(
        client,
        auth,
        c.aut,
        "movimentacao",
        destinatario_id=c.adm.id,
        filtros={"incluir_nomes": valor},
    )
    assert r.status_code == 202
    assert repo.chamadas_de("movimentacao")[-1]["incluir_nomes"] is valor
    assert next(iter(repo.envios.values()))["filtros"]["incluir_nomes"] is valor


def test_bool_invalido_400_query_e_corpo(client, auth, c, ligado):  # type: ignore[no-untyped-def]
    assert (
        get(client, auth, c.aut, f"{BASE}/movimentacao/previa", incluir_nomes="talvez").status_code
        == 400
    )
    r = enviar(
        client,
        auth,
        c.aut,
        "movimentacao",
        destinatario_id=c.adm.id,
        filtros={"incluir_nomes": "talvez"},
    )
    assert r.status_code == 400


# ------------------------------------------------- dedupe (RPC B) e reaper (RPC C)
class Parado(Executor):
    """Não executa: o envio fica `pendente`, como numa fila ocupada."""

    def __init__(self) -> None:
        self.enviados = 0

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.enviados += 1
        return Future()


def test_envio_duplicado_nao_reenvia_nem_consome_cota(mk, auth, c, ligado, repo, monkeypatch):  # type: ignore[no-untyped-def]
    parado = Parado()
    monkeypatch.setattr(modulo, "EXECUTOR_EMAIL", parado)
    cli = mk(email_max_per_hour=1).test_client()  # a cota só comporta UM envio
    r1 = enviar(cli, auth, c.aut, destinatario_id=c.adm.id, filtros={"unidade": "PU"})
    r2 = enviar(cli, auth, c.aut, destinatario_id=c.adm.id, filtros={"unidade": "PU"})
    assert r1.status_code == 202 and r2.status_code == 202  # sem erro, idempotente
    assert r1.get_json() == r2.get_json()
    assert len(repo.envios) == 1 and parado.enviados == 1
    assert len(repo.eventos_de("relatorio_enviado")) == 1
    # outro envio (filtros diferentes) não é duplicado e estoura a cota
    r3 = enviar(cli, auth, c.aut, destinatario_id=c.adm.id, filtros={"unidade": "PF"})
    assert r3.status_code == 429


def test_envio_ja_concluido_nao_e_duplicado(client, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    r1 = enviar(client, auth, c.aut, destinatario_id=c.adm.id)  # Sincrono: já 'enviado'
    r2 = enviar(client, auth, c.aut, destinatario_id=c.adm.id)
    assert r1.get_json()["id"] != r2.get_json()["id"] and len(repo.envios) == 2


def test_reaper_roda_no_maximo_uma_vez_no_intervalo(client, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    modulo.REAPER.reiniciar()
    for _ in range(3):
        assert enviar(client, auth, c.aut, destinatario_id=c.adm.id).status_code == 202
    assert repo.chamadas_reaper == 1


def test_reaper_move_envio_preso_para_falhou_timeout(client, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    modulo.REAPER.reiniciar()
    r = enviar(client, auth, c.aut, destinatario_id=c.adm.id)
    eid = r.get_json()["id"]
    repo.envios[eid].update(status="enviando", criado_em=repo.envios[eid]["criado_em"] - _HORA)
    modulo.REAPER.reiniciar()
    enviar(client, auth, c.aut, destinatario_id=c.adm.id)
    assert repo.envios[eid]["status"] == "falhou" and repo.envios[eid]["erro_codigo"] == "timeout"


def test_reaper_com_falha_nao_derruba_a_requisicao_nem_loga_pii(  # type: ignore[no-untyped-def]
    client, auth, c, ligado, repo, caplog
):
    modulo.REAPER.reiniciar()
    repo.falha_reaper = RuntimeError("dado.pessoal@agu.gov.br")
    caplog.set_level(logging.DEBUG)
    r = enviar(client, auth, c.aut, destinatario_id=c.adm.id)
    assert r.status_code == 202 and repo.chamadas_reaper == 1
    assert "reaper_envios_email_falhou tipo=RuntimeError" in caplog.text
    assert "dado.pessoal" not in caplog.text


def test_marcar_enviado_apos_reaper_e_benigno(client, auth, c, ligado, repo, caplog):  # type: ignore[no-untyped-def]
    """falhou -> enviado devolve AG001 (TransicaoInvalida): não vira erro nem alerta."""
    original = repo.marcar_envio_email

    def reaper_antes(envio_id, status, erro_codigo=None):  # type: ignore[no-untyped-def]
        if status == "enviado":
            repo.envios[envio_id]["status"] = "falhou"  # o reaper agiu durante o SMTP
        return original(envio_id, status, erro_codigo)

    repo.marcar_envio_email = reaper_antes  # type: ignore[method-assign]
    caplog.set_level(logging.DEBUG)
    r = enviar(client, auth, c.aut, destinatario_id=c.adm.id)
    assert r.status_code == 202
    assert "envio_marcar_enviado_falhou" not in caplog.text
    assert repo.chamadas_sistema == []


_HORA = timedelta(hours=1)


# ------------------------------------------- S-01/S-02: destino institucional e papel
def _erro(r: Any) -> dict[str, str]:
    return r.get_json()["erro"]


@pytest.mark.parametrize("filtro", ["incluir_nomes", "incluir_trajeto"])
def test_pii_sem_dominio_configurado_e_bloqueado(  # type: ignore[no-untyped-def]
    client, auth, c, ligado, repo, monkeypatch, filtro
):
    _dominios(client, monkeypatch, "")
    r = enviar(
        client, auth, c.aut, "movimentacao", destinatario_id=c.adm.id, filtros={filtro: True}
    )
    assert r.status_code == 422 and _erro(r)["codigo"] == "destinatario_externo_pii"
    assert "Baixe o arquivo" in _erro(r)["mensagem"]
    assert repo.envios == {} and SmtpFalso.instancias == []


def test_pii_para_dominio_fora_da_lista_e_bloqueado(  # type: ignore[no-untyped-def]
    client, auth, c, ligado, repo, monkeypatch
):
    _dominios(client, monkeypatch, "agu.gov.br")
    repo.contas_auth[c.adm.id]["email"] = "fulano@gmail.com"
    for email in ("fulano@gmail.com", "fulano@agu.gov.br.evil.com", "fulano@sub.agu.gov.br"):
        repo.contas_auth[c.adm.id]["email"] = email
        r = enviar(
            client,
            auth,
            c.aut,
            "movimentacao",
            destinatario_id=c.adm.id,
            filtros={"incluir_nomes": True},
        )
        assert r.status_code == 422 and _erro(r)["codigo"] == "destinatario_externo_pii", email
    assert repo.envios == {} and SmtpFalso.instancias == []


def test_pii_para_dominio_institucional_passa_sem_diferenciar_caixa(  # type: ignore[no-untyped-def]
    client, auth, c, ligado, repo, monkeypatch
):
    _dominios(client, monkeypatch, " @AGU.gov.br , outro.gov.br")
    repo.contas_auth[c.adm.id]["email"] = "Fulano@Agu.GOV.br"
    r = enviar(
        client, auth, c.aut, "movimentacao", destinatario_id=c.adm.id, filtros={"incluir_nomes": 1}
    )
    assert r.status_code == 400  # 1 não é bool/str: filtros só aceitam texto ou verdadeiro/falso
    r = enviar(
        client,
        auth,
        c.aut,
        "movimentacao",
        destinatario_id=c.adm.id,
        filtros={"incluir_nomes": True},
    )
    assert r.status_code == 202


def test_sem_pii_gmail_continua_permitido_sem_dominio_configurado(  # type: ignore[no-untyped-def]
    client, auth, c, ligado, repo, monkeypatch
):
    _dominios(client, monkeypatch, "")
    repo.contas_auth[c.adm.id]["email"] = "fulano@gmail.com"
    r = enviar(client, auth, c.aut, "movimentacao", destinatario_id=c.adm.id)
    assert r.status_code == 202


def test_auditoria_so_vai_a_admin(client, auth, c, ligado, repo, monkeypatch):  # type: ignore[no-untyped-def]
    _dominios(client, monkeypatch, "agu.gov.br")
    r = enviar(client, auth, c.adm, "auditoria", destinatario_id=c.aut.id)
    assert r.status_code == 422 and _erro(r)["codigo"] == "destinatario_sem_permissao"
    assert repo.envios == {} and SmtpFalso.instancias == []
    outro_admin = repo.novo_perfil("admin")
    assert enviar(client, auth, c.adm, "auditoria", destinatario_id=c.adm.id).status_code == 202
    assert (
        enviar(client, auth, c.adm, "auditoria", destinatario_id=outro_admin.id).status_code == 202
    )


def test_auditoria_exige_dominio_institucional(  # type: ignore[no-untyped-def]
    client, auth, c, ligado, repo, monkeypatch
):
    _dominios(client, monkeypatch, "")
    r = enviar(client, auth, c.adm, "auditoria", destinatario_id=c.adm.id)
    assert r.status_code == 422 and _erro(r)["codigo"] == "destinatario_externo_pii"
    _dominios(client, monkeypatch, "agu.gov.br")
    assert enviar(client, auth, c.adm, "auditoria", destinatario_id=c.adm.id).status_code == 202


def test_dominios_institucionais_le_so_o_config_e_normaliza():  # type: ignore[no-untyped-def]
    class Cfg:
        email_dominios_relatorio = ("A.gov.br", "@b.gov.br", " ")

    assert modulo.dominios_institucionais(Cfg()) == {"a.gov.br", "b.gov.br"}
    assert modulo.dominios_institucionais(object()) == frozenset()  # falha fechada


def test_variavel_de_ambiente_crua_nao_vale_fora_do_config(client, auth, c, ligado, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("EMAIL_DOMINIOS_RELATORIO", "agu.gov.br")  # o Config é a fonte
    r = enviar(
        client,
        auth,
        c.aut,
        "movimentacao",
        destinatario_id=c.adm.id,
        filtros={"incluir_nomes": True},
    )
    assert r.status_code == 422 and _erro(r)["codigo"] == "destinatario_externo_pii"
