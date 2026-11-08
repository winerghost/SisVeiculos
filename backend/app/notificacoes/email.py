"""Envio de e-mail (SMTP) dos relatórios. Credenciais só do ambiente, só no backend.

LGPD: o endereço do destinatário, o corpo e o anexo NUNCA vão para log; falhas viram um código
curto (`smtp_timeout`, `smtp_autenticacao`...) e só o código é registrado. O corpo é genérico
(sem dado pessoal). Cabeçalhos são higienizados (sem CR/LF) antes de montar a mensagem.
"""

from __future__ import annotations

import logging
import re
import smtplib
import socket
import ssl
from dataclasses import dataclass, field
from email.headerregistry import Address
from email.message import EmailMessage
from typing import Protocol

from app.config import Config

log = logging.getLogger(__name__)

_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")
# Endereço simples: um só "@", sem espaço, vírgula, ponto e vírgula, aspas nem colchetes.
_ENDERECO = re.compile(r"^[^@\s,;<>()\[\]\"'\\]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}$")

MENSAGENS: dict[str, str] = {
    "email_desligado": "O envio de e-mail não está habilitado neste ambiente.",
    "destinatario_invalido": "O endereço de e-mail do destinatário é inválido.",
    "smtp_timeout": "O servidor de e-mail demorou demais para responder.",
    "smtp_conexao": "Não foi possível conectar ao servidor de e-mail.",
    "smtp_tls": "Falha na conexão segura (TLS) com o servidor de e-mail.",
    "smtp_autenticacao": "O servidor de e-mail recusou as credenciais configuradas.",
    "smtp_destinatario_invalido": "O endereço de e-mail do destinatário não pôde ser usado.",
    "smtp_destinatario_recusado": "O servidor de e-mail recusou o endereço do destinatário.",
    "smtp_remetente_recusado": "O servidor de e-mail recusou o remetente configurado.",
    "smtp_recusado": "O servidor de e-mail recusou a mensagem.",
    "smtp_erro": "Falha ao enviar o e-mail.",
    "geracao_falhou": "Não foi possível gerar o arquivo do relatório.",
    "auditoria_falhou": "Não foi possível registrar a auditoria do envio.",
    "fila_indisponivel": "O envio não pôde ser agendado. Tente novamente.",
    "desconhecido": "Falha ao enviar o e-mail.",
}


def mensagem_do_codigo(codigo: str | None) -> str | None:
    """Mensagem clara em pt-BR para um `erro_codigo` (None se não houver erro)."""
    if not codigo:
        return None
    return MENSAGENS.get(codigo, MENSAGENS["desconhecido"])


class FalhaEmail(Exception):  # noqa: N818
    """Falha de envio. Carrega só o código curto (nunca a mensagem do servidor SMTP)."""

    def __init__(self, codigo: str) -> None:
        super().__init__(codigo)
        self.codigo = codigo


def limpar_cabecalho(valor: str, limite: int = 200) -> str:
    """Remove CR/LF e demais controles (injeção de cabeçalho) e limita o tamanho."""
    return " ".join(_CONTROLE.sub(" ", valor).split())[:limite]


def endereco_valido(valor: str) -> bool:
    return bool(_ENDERECO.fullmatch(valor)) and len(valor) <= 254


@dataclass(frozen=True)
class ConfigSmtp:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    security: str
    remetente: str
    nome_remetente: str
    timeout: int

    @classmethod
    def de(cls, c: Config) -> ConfigSmtp:
        return cls(
            c.email_host,
            c.email_port,
            c.email_user,
            c.email_password,
            c.email_security,
            c.email_from,
            c.email_from_name,
            c.email_timeout_seconds,
        )


class EnviadorEmail(Protocol):
    def enviar(
        self,
        *,
        destino: str,
        assunto: str,
        corpo: str,
        anexo_nome: str = "",
        anexo: bytes | None = None,
        mimetype: str = "",
    ) -> None:
        """Levanta FalhaEmail(codigo) em qualquer falha."""
        ...


class EnviadorSmtp:
    def __init__(self, cfg: ConfigSmtp) -> None:
        self._cfg = cfg

    def _montar(
        self,
        destino: str,
        assunto: str,
        corpo: str,
        anexo_nome: str,
        anexo: bytes | None,
        mimetype: str,
    ) -> EmailMessage:
        destino = destino.strip()
        if not endereco_valido(destino):
            raise FalhaEmail("destinatario_invalido")
        try:
            remetente = Address(
                display_name=limpar_cabecalho(self._cfg.nome_remetente, 80),
                addr_spec=limpar_cabecalho(self._cfg.remetente, 254),
            )
        except Exception:  # HeaderParseError não herda de ValueError
            raise FalhaEmail("smtp_remetente_recusado") from None
        try:
            para = Address(addr_spec=destino)
        except Exception:  # HeaderParseError não herda de ValueError
            raise FalhaEmail("smtp_destinatario_invalido") from None
        try:
            msg = EmailMessage()
            msg["From"] = remetente
            msg["To"] = para
            msg["Subject"] = limpar_cabecalho(assunto)
        except Exception:  # HeaderParseError não herda de ValueError
            raise FalhaEmail("smtp_erro") from None
        msg.set_content(corpo)
        if anexo is None:  # mensagem sem anexo (ex.: aviso de notificação)
            return msg
        tipo, _, subtipo = mimetype.partition("/")
        msg.add_attachment(
            anexo,
            maintype=tipo or "application",
            subtype=subtipo or "octet-stream",
            filename=limpar_cabecalho(anexo_nome, 100),
        )
        return msg

    def enviar(
        self,
        *,
        destino: str,
        assunto: str,
        corpo: str,
        anexo_nome: str = "",
        anexo: bytes | None = None,
        mimetype: str = "",
    ) -> None:
        msg = self._montar(destino, assunto, corpo, anexo_nome, anexo, mimetype)
        c = self._cfg
        try:
            contexto = ssl.create_default_context()
            if c.security == "ssl":
                servidor: smtplib.SMTP = smtplib.SMTP_SSL(
                    c.host, c.port, timeout=c.timeout, context=contexto
                )
            else:
                servidor = smtplib.SMTP(c.host, c.port, timeout=c.timeout)
            with servidor:
                if c.security == "starttls":
                    servidor.ehlo()
                    servidor.starttls(context=contexto)
                    servidor.ehlo()
                # Nunca envia credencial em claro: login só com a conexão em TLS.
                if c.user and c.security in {"starttls", "ssl"}:
                    servidor.login(c.user, c.password)
                servidor.send_message(msg)
        except FalhaEmail:
            raise
        except Exception as e:
            codigo = codigo_da_excecao(e)
            # Só o código: a mensagem do servidor pode repetir o endereço do destinatário.
            log.error("email_falhou codigo=%s", codigo)
            raise FalhaEmail(codigo) from None


def codigo_da_excecao(e: BaseException) -> str:
    """Mapeia a exceção para um código curto. A ordem importa: várias são subclasses de OSError."""
    if isinstance(e, TimeoutError | socket.timeout):
        return "smtp_timeout"
    if isinstance(e, ssl.SSLError):
        return "smtp_tls"
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return "smtp_autenticacao"
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return "smtp_destinatario_recusado"
    if isinstance(e, smtplib.SMTPSenderRefused):
        return "smtp_remetente_recusado"
    if isinstance(e, smtplib.SMTPConnectError | smtplib.SMTPServerDisconnected):
        return "smtp_conexao"
    if isinstance(e, smtplib.SMTPResponseException):  # inclui SMTPDataError
        return "smtp_recusado"
    if isinstance(e, smtplib.SMTPException):
        return "smtp_erro"
    if isinstance(e, OSError):  # recusa de conexão, DNS, rede
        return "smtp_conexao"
    return "smtp_erro"
