"""Despachante de e-mail dos críticos e hooks de falha de sistema (SMTP simulado, sem rede)."""

from __future__ import annotations

import logging
import smtplib
import threading
from typing import Any

import pytest

from app import create_app
from app.config import Config
from app.errors import ErroInterno
from app.modelos import Configuracoes
from app.notificacoes import despachante as modulo
from app.notificacoes.despachante import ASSUNTO, CORPO, Despachante
from app.notificacoes.email import ConfigSmtp, EnviadorSmtp, FalhaEmail
from app.services.notificacoes import registrar_falha_sistema
from app.services.relatorios import Arquivo, ServicoRelatorios
from tests.conftest import AUDIENCE, ISSUER
from tests.fakes import RepoMemoria
from tests.fakes_admin import AuthAdminMemoria

SENHA = "SENHA-SECRETA-123"
URL = "https://app.exemplo.gov.br/convite"


class EnviadorFalso:
    def __init__(self, falha: Exception | None = None) -> None:
        self.enviados: list[dict[str, Any]] = []
        self.falha = falha

    def enviar(self, **kw: Any) -> None:
        if self.falha is not None:
            raise self.falha
        self.enviados.append(kw)


class Relogio:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def ligado(repo: RepoMemoria) -> RepoMemoria:
    repo.config = Configuracoes(notificacao_email_criticos=True)
    return repo


def montar(
    repo: RepoMemoria,
    auth_admin: AuthAdminMemoria | None,
    enviador: Any,
    relogio: Any = None,
    **cfg: Any,
) -> Despachante:
    config = Config(url_aceite_convite=URL, **cfg)
    return Despachante(repo, auth_admin, config, enviador=enviador, relogio=relogio or Relogio())


def pendente(repo: RepoMemoria, quem: Any, tipo: str = "requisicao_chegada", **kw: Any) -> Any:
    if tipo.startswith("requisicao_"):
        kw.setdefault("requisicao_id", "11111111-1111-4111-8111-111111111111")
    return repo.nova_notificacao(quem.id, tipo, critica=True, email_status="pendente", **kw)


def test_envia_aviso_generico_sem_pii(ligado, auth_admin, c):  # type: ignore[no-untyped-def]
    n = pendente(ligado, c.sol)
    env = EnviadorFalso()
    assert montar(ligado, auth_admin, env).processar_uma_vez() == 1
    (e,) = env.enviados
    assert e["destino"] == ligado.contas_auth[c.sol.id]["email"]
    assert e["assunto"] == ASSUNTO == "Nova notificação importante no SisVeiculos AGU"
    assert e["corpo"] == f"{CORPO}\n\nhttps://app.exemplo.gov.br"
    assert "anexo" not in e or e["anexo"] is None
    # nada da requisição/perfil no assunto ou corpo
    texto = e["assunto"] + e["corpo"]
    for proibido in ("11111111", c.sol.nome.lower(), "trajeto", "placa", "chegada"):
        assert proibido not in texto.lower()
    assert n["email_status"] == "enviado"


def test_sem_url_configurada_corpo_so_com_aviso(ligado, auth_admin, c):  # type: ignore[no-untyped-def]
    pendente(ligado, c.sol)
    env = EnviadorFalso()
    d = Despachante(ligado, auth_admin, Config(), enviador=env)
    assert d.processar_uma_vez() == 1
    assert env.enviados[0]["corpo"] == CORPO


def test_sistema_falha_envio_ignorado_sem_enviar(ligado, auth_admin, c):  # type: ignore[no-untyped-def]
    n = pendente(ligado, c.adm, "sistema_falha_envio", codigo="smtp_timeout")
    env = EnviadorFalso()
    assert montar(ligado, auth_admin, env).processar_uma_vez() == 0
    assert env.enviados == [] and n["email_status"] == "ignorado"
    assert ligado.chamadas_sistema == []  # e não realimenta novos alertas


def test_sem_email_do_usuario_ignorado(ligado, auth_admin, c):  # type: ignore[no-untyped-def]
    n = pendente(ligado, c.sol)
    ligado.contas_auth[c.sol.id]["email"] = None
    env = EnviadorFalso()
    montar(ligado, auth_admin, env).processar_uma_vez()
    assert env.enviados == []
    assert (n["email_status"], n["email_erro_codigo"]) == ("ignorado", "sem_email")


def test_conta_inexistente_no_auth_ignorada(ligado, auth_admin, c):  # type: ignore[no-untyped-def]
    n = pendente(ligado, c.sol)
    del ligado.contas_auth[c.sol.id]
    montar(ligado, auth_admin, EnviadorFalso()).processar_uma_vez()
    assert n["email_status"] == "ignorado"


def test_auth_indisponivel_marca_falhou_e_avisa_admin(ligado, auth_admin, c):  # type: ignore[no-untyped-def]
    n = pendente(ligado, c.sol)

    def quebra(_uid: str) -> Any:
        raise ErroInterno("x")

    auth_admin.obter_conta = quebra  # type: ignore[method-assign]
    montar(ligado, auth_admin, EnviadorFalso()).processar_uma_vez()
    assert (n["email_status"], n["email_erro_codigo"]) == ("falhou", "auth_indisponivel")
    assert ligado.chamadas_sistema == [("sistema_falha_envio", "auth_indisponivel")]
    assert any(
        x["usuario_id"] == c.adm.id and x["codigo"] == "auth_indisponivel"
        for x in ligado.notificacoes
        if x["tipo"] == "sistema_falha_envio"
    )


def test_sem_auth_admin_falha_com_codigo(ligado, c):  # type: ignore[no-untyped-def]
    n = pendente(ligado, c.sol)
    montar(ligado, None, EnviadorFalso()).processar_uma_vez()
    assert n["email_status"] == "falhou"


def test_falha_smtp_marca_falhou_com_codigo_e_nao_vaza(ligado, auth_admin, c, caplog):  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    n = pendente(ligado, c.sol)
    email = ligado.contas_auth[c.sol.id]["email"]
    env = EnviadorFalso(FalhaEmail("smtp_autenticacao"))
    assert montar(ligado, auth_admin, env).processar_uma_vez() == 0
    assert (n["email_status"], n["email_erro_codigo"]) == ("falhou", "smtp_autenticacao")
    assert ligado.chamadas_sistema == [("sistema_falha_envio", "smtp_autenticacao")]
    assert email not in caplog.text and SENHA not in caplog.text
    assert "smtp_autenticacao" in caplog.text


def test_smtp_real_simulado_nao_vaza_endereco_nem_senha(  # type: ignore[no-untyped-def]
    ligado, auth_admin, c, caplog, monkeypatch
):
    caplog.set_level(logging.DEBUG)
    n = pendente(ligado, c.sol)
    email = ligado.contas_auth[c.sol.id]["email"]

    class SmtpQueFalha:
        def __init__(self, *a: Any, **k: Any) -> None:
            raise smtplib.SMTPAuthenticationError(535, f"recusado {email} {SENHA}".encode())

    monkeypatch.setattr(smtplib, "SMTP", SmtpQueFalha)
    smtp = EnviadorSmtp(
        ConfigSmtp("smtp.exemplo", 587, "user", SENHA, "starttls", "no@agu.gov.br", "AGU", 5)
    )
    montar(ligado, auth_admin, smtp).processar_uma_vez()
    assert (n["email_status"], n["email_erro_codigo"]) == ("falhou", "smtp_autenticacao")
    assert email not in caplog.text and SENHA not in caplog.text


def test_enviador_smtp_monta_mensagem_sem_anexo(monkeypatch):  # type: ignore[no-untyped-def]
    capturadas: list[Any] = []

    class Smtp:
        def __init__(self, *a: Any, **k: Any) -> None: ...
        def __enter__(self) -> Smtp:
            return self

        def __exit__(self, *a: Any) -> None: ...
        def ehlo(self) -> None: ...
        def starttls(self, **k: Any) -> None: ...
        def login(self, *a: Any) -> None: ...
        def send_message(self, msg: Any) -> None:
            capturadas.append(msg)

    monkeypatch.setattr(smtplib, "SMTP", Smtp)
    EnviadorSmtp(ConfigSmtp("h", 587, "u", SENHA, "starttls", "no@agu.gov.br", "AGU", 5)).enviar(
        destino="a@agu.gov.br", assunto=ASSUNTO, corpo=CORPO
    )
    (msg,) = capturadas
    assert list(msg.iter_attachments()) == [] and msg["Subject"] == ASSUNTO


def test_destinatario_invalido_nao_alerta_admins(ligado, auth_admin, c):  # type: ignore[no-untyped-def]
    n = pendente(ligado, c.sol)
    env = EnviadorFalso(FalhaEmail("destinatario_invalido"))
    montar(ligado, auth_admin, env).processar_uma_vez()
    assert n["email_status"] == "falhou" and ligado.chamadas_sistema == []


def test_teto_por_hora_janela_deslizante(ligado, auth_admin, c):  # type: ignore[no-untyped-def]
    rel = Relogio()
    ns = [pendente(ligado, c.sol) for _ in range(7)]
    env = EnviadorFalso()
    d = montar(ligado, auth_admin, env, rel, email_notif_max_por_hora=3)
    assert d.processar_uma_vez() == 3
    assert d.processar_uma_vez() == 0  # teto atingido: nem reserva
    assert sum(n["email_status"] == "pendente" for n in ns) == 4  # nada preso em "enviando"
    assert not any(n["email_status"] == "enviando" for n in ns)
    rel.t += 3599
    assert d.processar_uma_vez() == 0
    rel.t += 2  # a janela de 1 h passou
    assert d.processar_uma_vez() == 3
    assert len(env.enviados) == 6


def test_lote_respeita_o_que_resta_do_teto(ligado, auth_admin, c):  # type: ignore[no-untyped-def]
    for _ in range(10):
        pendente(ligado, c.sol)
    env = EnviadorFalso()
    d = montar(ligado, auth_admin, env, email_notif_max_por_hora=2)
    assert d.processar_uma_vez() == 2 and len(env.enviados) == 2


def test_flag_desligada_nao_envia(repo, auth_admin, c):  # type: ignore[no-untyped-def]
    n = pendente(repo, c.sol)
    env = EnviadorFalso()
    assert montar(repo, auth_admin, env).processar_uma_vez() == 0
    assert env.enviados == [] and n["email_status"] == "ignorado"


def test_excecao_na_reserva_nao_derruba(ligado, auth_admin, c, caplog):  # type: ignore[no-untyped-def]
    ligado.falha_reserva = ErroInterno("x")
    assert montar(ligado, auth_admin, EnviadorFalso()).processar_uma_vez() == 0
    ligado.falha_reserva = None
    pendente(ligado, c.sol)
    assert montar(ligado, auth_admin, EnviadorFalso()).processar_uma_vez() == 1


def test_excecao_inesperada_em_um_item_nao_derruba_o_lote(ligado, auth_admin, c):  # type: ignore[no-untyped-def]
    a, b = pendente(ligado, c.sol), pendente(ligado, c.outro)
    env = EnviadorFalso()
    original = env.enviar
    chamadas = {"n": 0}

    def enviar(**kw: Any) -> None:
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise RuntimeError("boom " + kw["destino"])
        original(**kw)

    env.enviar = enviar  # type: ignore[method-assign]
    assert montar(ligado, auth_admin, env).processar_uma_vez() == 1
    assert (a["email_status"], a["email_erro_codigo"]) == ("falhou", "desconhecido")
    assert b["email_status"] == "enviado"


def test_falha_ao_marcar_nao_derruba(ligado, auth_admin, c):  # type: ignore[no-untyped-def]
    pendente(ligado, c.sol)

    def quebra(*a: Any, **k: Any) -> None:
        raise ErroInterno("x")

    ligado.marcar_email_notificacao = quebra  # type: ignore[method-assign]
    assert montar(ligado, auth_admin, EnviadorFalso()).processar_uma_vez() == 1


def test_laco_da_thread_sobrevive_a_excecao(ligado, auth_admin, monkeypatch):  # type: ignore[no-untyped-def]
    d = montar(
        ligado, auth_admin, EnviadorFalso(), email_enabled=True, email_notif_intervalo_segundos=1
    )
    chamadas = []
    feito = threading.Event()

    def ciclo() -> int:
        chamadas.append(1)
        if len(chamadas) == 1:
            raise RuntimeError("boom")
        feito.set()
        return 0

    monkeypatch.setattr(d, "processar_uma_vez", ciclo)
    monkeypatch.setattr(d._parar, "wait", lambda _t: len(chamadas) >= 2)
    d._laco()  # 1º ciclo levanta, é engolido; 2º roda; depois encerra
    assert len(chamadas) == 2


# -- factory ---------------------------------------------------------------------------------------
def _fabrica(monkeypatch, repo, auth_admin, **cfg):  # type: ignore[no-untyped-def]
    iniciados: list[Despachante] = []
    monkeypatch.setattr(modulo.Despachante, "iniciar", lambda self: iniciados.append(self))
    from app.auth.jwt import VerificadorJWT
    from tests.conftest import _CHAVE

    verificador = VerificadorJWT(
        issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
    )
    create_app(
        Config(jwt_issuer=ISSUER, jwt_audience=AUDIENCE, **cfg),
        repo=repo,
        verificador=verificador,
        auth_admin=auth_admin,
    )
    return iniciados


def test_thread_so_inicia_com_email_ligado_e_fora_de_testes(monkeypatch, repo, auth_admin):  # type: ignore[no-untyped-def]
    # fora de testes a thread sobe sempre (expurgo); o e-mail só roda com EMAIL_ENABLED
    assert len(_fabrica(monkeypatch, repo, auth_admin, email_enabled=False)) == 1
    assert _fabrica(monkeypatch, repo, auth_admin, email_enabled=True, testing=True) == []
    assert len(_fabrica(monkeypatch, repo, auth_admin, email_enabled=True)) == 1


def test_config_do_ambiente(monkeypatch):  # type: ignore[no-untyped-def]
    padrao = Config.from_env()
    assert padrao.email_notif_intervalo_segundos == 30 and padrao.email_notif_max_por_hora == 20
    monkeypatch.setenv("EMAIL_NOTIF_INTERVALO_SEGUNDOS", "60")
    monkeypatch.setenv("EMAIL_NOTIF_MAX_POR_HORA", "5")
    cfg = Config.from_env()
    assert (cfg.email_notif_intervalo_segundos, cfg.email_notif_max_por_hora) == (60, 5)
    monkeypatch.setenv("EMAIL_NOTIF_INTERVALO_SEGUNDOS", "0")
    monkeypatch.setenv("EMAIL_NOTIF_MAX_POR_HORA", "lixo")
    cfg = Config.from_env()
    assert (cfg.email_notif_intervalo_segundos, cfg.email_notif_max_por_hora) == (5, 20)


# -- hooks de falha de sistema -------------------------------------------------------------------
def test_registrar_falha_sistema_chama_a_rpc(repo, c):  # type: ignore[no-untyped-def]
    registrar_falha_sistema(repo, "smtp_timeout")
    assert repo.chamadas_sistema == [("sistema_falha_envio", "smtp_timeout")]
    registrar_falha_sistema(repo, "Código Inválido!")  # fora do formato vira "desconhecido"
    assert repo.chamadas_sistema[-1] == ("sistema_falha_envio", "desconhecido")


def test_registrar_falha_sistema_nunca_propaga(repo, caplog):  # type: ignore[no-untyped-def]
    repo.falha_sistema = ErroInterno("banco caiu x@agu.gov.br")
    registrar_falha_sistema(repo, "smtp_timeout")  # não levanta
    assert "x@agu.gov.br" not in caplog.text


def _servico(repo: RepoMemoria) -> ServicoRelatorios:
    return ServicoRelatorios(repo, Config(), enviador=EnviadorFalso(FalhaEmail("smtp_timeout")))


def _envio(repo: RepoMemoria, c: Any) -> str:
    repo.config = Configuracoes(relatorio_email=True)
    return repo.reservar_envio_email(
        solicitante=c.aut.id,
        destinatario=c.adm.id,
        tipo="movimentacao",
        formato="csv",
        filtros={},
        max_por_hora=10,
    )["id"]


def test_falha_de_relatorio_avisa_admins(repo, c):  # type: ignore[no-untyped-def]
    envio = _envio(repo, c)
    arq = Arquivo(b"x", "text/csv", "r.csv", "csv")
    _servico(repo)._enviar_em_segundo_plano(envio, "a@agu.gov.br", "Rel", arq)
    assert repo.envios[envio]["status"] == "falhou"
    assert repo.chamadas_sistema == [("sistema_falha_envio", "smtp_timeout")]
    avisos = [n for n in repo.notificacoes if n["tipo"] == "sistema_falha_envio"]
    assert {n["usuario_id"] for n in avisos} == {c.adm.id}


def test_falha_de_relatorio_nao_propaga_se_o_aviso_falhar(repo, c):  # type: ignore[no-untyped-def]
    envio = _envio(repo, c)
    repo.falha_sistema = RuntimeError("x")
    arq = Arquivo(b"x", "text/csv", "r.csv", "csv")
    _servico(repo)._enviar_em_segundo_plano(envio, "a@agu.gov.br", "Rel", arq)  # não levanta
    assert repo.envios[envio]["status"] == "falhou"


def test_marcar_falha_direto_usa_codigo_curto(repo):  # type: ignore[no-untyped-def]
    _servico(repo)._marcar_falha(str(__import__("uuid").uuid4()), "fila_indisponivel")
    assert repo.chamadas_sistema == [("sistema_falha_envio", "fila_indisponivel")]
    _servico(repo)._marcar_falha("x", "destinatario_invalido")
    assert len(repo.chamadas_sistema) == 1


# -- expurgo, retentativa de "enviado" e códigos sem alerta -------------------------------------
def test_expurgo_1x_por_24h_com_90_dias(repo, auth_admin):  # type: ignore[no-untyped-def]
    rel, chamadas = Relogio(), []
    repo.purgar_notificacoes = lambda dias=90: chamadas.append(dias) or 4  # type: ignore[method-assign]
    d = montar(repo, auth_admin, EnviadorFalso(), rel)
    assert d.expurgar_se_devido() is None and chamadas == []  # ainda não passou ~1 min
    rel.t += 61
    assert d.expurgar_se_devido() == 4 and chamadas == [90]
    rel.t += 3600
    assert d.expurgar_se_devido() is None
    rel.t += 24 * 3600
    assert d.expurgar_se_devido() == 4 and chamadas == [90, 90]


def test_expurgo_com_excecao_nao_derruba_e_nao_repete_logo(repo, auth_admin, caplog):  # type: ignore[no-untyped-def]
    rel = Relogio()
    n = {"n": 0}

    def quebra(dias: int = 90) -> int:
        n["n"] += 1
        raise ErroInterno("x@agu.gov.br")

    repo.purgar_notificacoes = quebra  # type: ignore[method-assign]
    d = montar(repo, auth_admin, EnviadorFalso(), rel)
    rel.t += 61
    assert d.expurgar_se_devido() is None
    assert d.expurgar_se_devido() is None and n["n"] == 1
    assert "x@agu.gov.br" not in caplog.text


def test_expurgo_roda_no_laco_sem_email(repo, auth_admin, monkeypatch):  # type: ignore[no-untyped-def]
    d = montar(repo, auth_admin, EnviadorFalso(), email_enabled=False)
    eventos = []
    monkeypatch.setattr(d, "expurgar_se_devido", lambda: eventos.append("e"))
    monkeypatch.setattr(d, "processar_uma_vez", lambda: eventos.append("p") or 0)
    ciclos = iter([False, True])
    monkeypatch.setattr(d._parar, "wait", lambda _t: next(ciclos))
    d._laco()
    assert eventos == ["e"]  # e-mail desligado: só manutenção


def test_enviado_tenta_ate_3_vezes(ligado, auth_admin, c):  # type: ignore[no-untyped-def]
    n = pendente(ligado, c.sol)
    original = ligado.marcar_email_notificacao
    falhas = {"n": 0}

    def instavel(nid: str, status: str, cod: str | None = None) -> None:
        if status == "enviado" and falhas["n"] < 2:
            falhas["n"] += 1
            raise ErroInterno("x")
        original(nid, status, cod)

    ligado.marcar_email_notificacao = instavel  # type: ignore[method-assign]
    esperas: list[float] = []
    d = Despachante(ligado, auth_admin, Config(), enviador=EnviadorFalso(), dormir=esperas.append)
    assert d.processar_uma_vez() == 1
    assert n["email_status"] == "enviado" and len(esperas) == 2


def test_enviado_desiste_apos_3_tentativas(ligado, auth_admin, c):  # type: ignore[no-untyped-def]
    pendente(ligado, c.sol)
    chamadas = []

    def sempre(nid: str, status: str, cod: str | None = None) -> None:
        chamadas.append(status)
        raise ErroInterno("x")

    ligado.marcar_email_notificacao = sempre  # type: ignore[method-assign]
    d = Despachante(ligado, auth_admin, Config(), enviador=EnviadorFalso(), dormir=lambda _t: None)
    assert d.processar_uma_vez() == 1 and chamadas == ["enviado"] * 3


@pytest.mark.parametrize("codigo", ["smtp_destinatario_recusado", "smtp_destinatario_invalido"])
def test_endereco_do_usuario_nao_alerta_admins(ligado, auth_admin, c, codigo):  # type: ignore[no-untyped-def]
    n = pendente(ligado, c.sol)
    montar(ligado, auth_admin, EnviadorFalso(FalhaEmail(codigo))).processar_uma_vez()
    assert n["email_erro_codigo"] == codigo and ligado.chamadas_sistema == []
    _servico(ligado)._marcar_falha("x", codigo)
    assert ligado.chamadas_sistema == []


def test_destinatario_nao_montavel_tem_codigo_proprio(monkeypatch):  # type: ignore[no-untyped-def]
    import app.notificacoes.email as em

    monkeypatch.setattr(em, "endereco_valido", lambda _v: True)  # passa do filtro, falha no Address
    smtp = EnviadorSmtp(ConfigSmtp("h", 587, "u", SENHA, "starttls", "no@agu.gov.br", "AGU", 5))
    with pytest.raises(FalhaEmail) as e:
        smtp.enviar(destino="a@@b@c", assunto="x", corpo="y")
    assert e.value.codigo == "smtp_destinatario_invalido"
    assert "smtp_destinatario_invalido" in em.MENSAGENS
