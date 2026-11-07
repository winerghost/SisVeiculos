"""Acesso à Admin API do Supabase Auth (service_role). Só o backend usa isto.

E-mail é dado pessoal: trafega apenas para convidar/exibir ao admin e nunca vai para log.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.errors import ContaExistente, ErroInterno

log = logging.getLogger(__name__)

BAN_PERMANENTE = "876000h"  # ~100 anos; "none" remove o bloqueio
POR_PAGINA = 200
MAX_PAGINAS = 25  # teto de segurança (5.000 contas) das varreduras por e-mail (raras)
MAX_LOTE_EMAILS = 100  # ids por chamada de emails_por_id (uma página da listagem)
PARALELISMO = 8
TIMEOUT_HTTP = 10.0


@dataclass(frozen=True)
class ContaAuth:
    id: str
    email: str | None
    nome: str | None  # user_metadata.nome, definido pelo admin no convite


@dataclass(frozen=True)
class IndiceEmails:
    """Mapa id -> e-mail de até MAX_PAGINAS*POR_PAGINA contas. `truncado`: havia mais contas."""

    emails: dict[str, str]
    truncado: bool


class AuthAdmin(Protocol):
    def convidar(self, email: str, *, nome: str | None, redirect_to: str | None) -> str:
        """Cria (ou reenvia o convite d)a conta e dispara o e-mail do Supabase. Devolve o id.

        Levanta ContaExistente se já houver conta confirmada com o e-mail. `nome=None` não
        sobrescreve o nome já gravado (reenvio).
        """
        ...

    def obter_conta(self, usuario_id: str) -> ContaAuth | None: ...

    def emails_por_id(self, ids: set[str]) -> dict[str, str]:
        """E-mail só dos ids pedidos (get_user_by_id; no máx. MAX_LOTE_EMAILS por chamada)."""
        ...

    def id_por_email(self, email: str) -> str | None:
        """Id da conta com este e-mail, ou None. Varredura limitada: usar só em caminho raro."""
        ...

    def indice_emails(self) -> IndiceEmails:
        """Varre as contas (limitado a MAX_PAGINAS) e devolve id -> e-mail. Caro: quem chama
        deve cachear por pouco tempo. Se truncar, registra no log sem PII."""
        ...

    def reenviar_acesso(self, email: str, *, redirect_to: str) -> None:
        """Conta já existente e sem perfil: envia e-mail de recuperação de senha com o mesmo
        `redirect_to` do convite (o link volta à página de aceite com ?convite=<token>).
        O link usa o fluxo implícito (sem PKCE): a página /convite lê #access_token."""
        ...

    def bloquear(self, usuario_id: str) -> None:
        """Impede novo login/refresh (ban). O histórico em `perfis` não é tocado."""
        ...

    def desbloquear(self, usuario_id: str) -> None: ...


def _falha(e: Exception) -> ErroInterno:
    # Só o tipo vai ao log: a mensagem pode trazer e-mail.
    log.error("erro_auth_admin tipo=%s", type(e).__name__)
    return ErroInterno("Serviço de autenticação indisponível. Tente novamente.")


_CODIGOS_JA_EXISTE = {"email_exists", "user_already_exists"}


def _ja_existe(e: Exception) -> bool:
    """Só o código estruturado do Auth vale: texto de mensagem muda e pode vir de terceiros."""
    return any(
        str(getattr(e, atributo, "") or "") in _CODIGOS_JA_EXISTE
        for atributo in ("code", "error_code")
    )


def _nome_do_usuario(usuario: Any) -> str | None:
    meta = getattr(usuario, "user_metadata", None) or {}
    nome = meta.get("nome") if isinstance(meta, dict) else None
    return nome if isinstance(nome, str) and nome.strip() else None


class SupabaseAuthAdmin:
    def __init__(
        self, url: str, service_role_key: str, *, http: httpx.Client | None = None
    ) -> None:
        from supabase import create_client

        cliente = create_client(url, service_role_key)
        self._admin = cliente.auth.admin
        self._url_recover = f"{url.rstrip('/')}/auth/v1/recover"
        self._chave = service_role_key
        self._http = http or httpx.Client(timeout=TIMEOUT_HTTP, follow_redirects=False)

    def convidar(self, email: str, *, nome: str | None, redirect_to: str | None) -> str:
        opcoes: dict[str, Any] = {}
        if nome is not None:
            opcoes["data"] = {"nome": nome}
        if redirect_to:
            opcoes["redirect_to"] = redirect_to
        try:
            return str(self._admin.invite_user_by_email(email, opcoes).user.id)
        except Exception as e:
            if _ja_existe(e):
                raise ContaExistente(
                    "Já existe uma conta com este e-mail. Não é possível convidar de novo."
                ) from None
            raise _falha(e) from None

    def obter_conta(self, usuario_id: str) -> ContaAuth | None:
        try:
            u = self._admin.get_user_by_id(usuario_id).user
        except Exception as e:
            if getattr(e, "status", None) == 404:
                return None
            raise _falha(e) from None
        if u is None:
            return None
        return ContaAuth(str(u.id), getattr(u, "email", None), _nome_do_usuario(u))

    def _varrer(self) -> Iterator[Any]:
        """Percorre as contas do Auth até MAX_PAGINAS páginas. Truncamento vai ao log sem PII."""
        for pagina in range(1, MAX_PAGINAS + 1):
            contas = self._admin.list_users(page=pagina, per_page=POR_PAGINA)
            yield from contas
            if len(contas) < POR_PAGINA:
                return
        log.warning("varredura_auth_truncada paginas=%d", MAX_PAGINAS)

    def id_por_email(self, email: str) -> str | None:
        alvo = email.strip().lower()
        try:
            for u in self._varrer():
                if str(getattr(u, "email", "") or "").lower() == alvo:
                    return str(u.id)
        except Exception as e:
            raise _falha(e) from None
        return None

    def indice_emails(self) -> IndiceEmails:
        emails: dict[str, str] = {}
        truncado = False
        try:
            for pagina in range(1, MAX_PAGINAS + 1):
                contas = self._admin.list_users(page=pagina, per_page=POR_PAGINA)
                for u in contas:
                    email = getattr(u, "email", None)
                    if email:
                        emails[str(u.id)] = str(email).lower()
                if len(contas) < POR_PAGINA:
                    break
            else:
                truncado = True  # última página cheia: pode haver mais contas
        except Exception as e:
            raise _falha(e) from None
        if truncado:
            log.warning("varredura_auth_truncada paginas=%d", MAX_PAGINAS)
        return IndiceEmails(emails, truncado)

    def reenviar_acesso(self, email: str, *, redirect_to: str) -> None:
        """POST /auth/v1/recover direto (GoTrue). Sem `code_challenge` no corpo o GoTrue gera o
        link do fluxo implícito (#access_token no fragmento), que é o que a página /convite
        espera. `reset_password_for_email` do supabase-py seguiria o flow_type do cliente
        (PKCE por padrão no 2.x), por isso não é usado. `redirect_to` vai na query, como no
        GoTrue; a mensagem de erro do httpx contém a URL (token), então só o tipo é logado."""
        try:
            r = self._http.post(
                self._url_recover,
                params={"redirect_to": redirect_to},
                json={"email": email},
                headers={"apikey": self._chave, "Authorization": f"Bearer {self._chave}"},
            )
        except Exception as e:
            raise _falha(e) from None
        if r.status_code >= 400:
            log.error("erro_auth_admin recover status=%d", r.status_code)
            raise ErroInterno("Serviço de autenticação indisponível. Tente novamente.")

    def _email_de(self, usuario_id: str) -> tuple[str, str | None]:
        try:
            u = self._admin.get_user_by_id(usuario_id).user
        except Exception as e:
            if getattr(e, "status", None) == 404:
                return usuario_id, None
            raise
        return usuario_id, (str(u.email) if u is not None and getattr(u, "email", None) else None)

    def emails_por_id(self, ids: set[str]) -> dict[str, str]:
        if not ids:
            return {}
        pedidos = sorted(ids)
        if len(pedidos) > MAX_LOTE_EMAILS:
            log.warning(
                "emails_por_id_truncado pedidos=%d limite=%d", len(pedidos), MAX_LOTE_EMAILS
            )
            pedidos = pedidos[:MAX_LOTE_EMAILS]
        try:
            with ThreadPoolExecutor(max_workers=PARALELISMO) as pool:
                resultados = list(pool.map(self._email_de, pedidos))
        except Exception as e:
            raise _falha(e) from None
        return {i: email for i, email in resultados if email}

    def _ban(self, usuario_id: str, duracao: str) -> None:
        try:
            self._admin.update_user_by_id(usuario_id, {"ban_duration": duracao})  # type: ignore[typeddict-item]
        except Exception as e:
            raise _falha(e) from None

    def bloquear(self, usuario_id: str) -> None:
        self._ban(usuario_id, BAN_PERMANENTE)

    def desbloquear(self, usuario_id: str) -> None:
        self._ban(usuario_id, "none")
