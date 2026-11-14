"""Inscrições de Web Push. O usuário vem sempre do JWT; a flag `notificacao_push` manda.

Anti-SSRF: o servidor faz POST no endpoint da inscrição, então só `https`, sem credenciais,
domínio de serviço push conhecido (allowlist por host exato ou sufixo) e host que resolve só
para IPs públicos. Endpoint e chaves nunca vão a log nem a mensagens de erro (LGPD).
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from app.config import Config, vapid_valido
from app.errors import RecursoDesligado, RequisicaoInvalida
from app.modelos import Perfil
from app.notificacoes.n8n import exigir_destino_publico, resolver_dns
from app.repositorios.base import Repositorio
from app.services.validacao_admin import _objeto

MSG_PUSH_DESLIGADO = "As notificações push estão desligadas nas Configurações."
MSG_ENDPOINT = "Endereço de notificação inválido ou de um serviço não permitido."
MSG_CHAVES = "Chaves da inscrição inválidas."
MAX_ENDPOINT = 2048
BYTES_P256DH, BYTES_AUTH = 65, 16
_RE_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")

Resolvedor = Callable[[str, int], list[str]]


def decodificar_b64url(valor: Any, tamanho: int) -> bytes | None:
    """Base64url (com ou sem `=`) de exatamente `tamanho` bytes; senão None."""
    if not isinstance(valor, str):
        return None
    sem_pad = valor.rstrip("=")
    if not sem_pad or len(sem_pad) > 200 or not _RE_B64URL.fullmatch(sem_pad):
        return None
    try:
        bruto = base64.urlsafe_b64decode(sem_pad + "=" * (-len(sem_pad) % 4))
    except (binascii.Error, ValueError):
        return None
    return bruto if len(bruto) == tamanho else None


def host_permitido(host: str, permitidos: list[str]) -> bool:
    """Item com "." inicial é sufixo de domínio; os demais, host exato."""
    for item in permitidos:
        item = item.strip().lower()
        if not item:
            continue
        if item.startswith("."):
            if host.endswith(item):
                return True
        elif host == item:
            return True
    return False


def validar_endpoint(
    valor: Any, permitidos: list[str], resolvedor: Resolvedor = resolver_dns
) -> str:
    """Devolve o endpoint aceito ou lança RequisicaoInvalida (mensagem fixa, sem eco)."""
    if not isinstance(valor, str) or not valor or len(valor) > MAX_ENDPOINT:
        raise RequisicaoInvalida(MSG_ENDPOINT, "push_endpoint_invalido")
    try:
        partes = urlsplit(valor)
        host = (partes.hostname or "").lower().rstrip(".")
        porta = partes.port
    except ValueError:
        raise RequisicaoInvalida(MSG_ENDPOINT, "push_endpoint_invalido") from None
    if (
        partes.scheme != "https"
        or not host
        or partes.username is not None
        or partes.password is not None
        or partes.fragment
        or porta not in (None, 443)
        or not host_permitido(host, permitidos)
    ):
        raise RequisicaoInvalida(MSG_ENDPOINT, "push_endpoint_invalido")
    try:
        exigir_destino_publico(host, porta or 443, resolvedor)
    except RuntimeError:
        raise RequisicaoInvalida(MSG_ENDPOINT, "push_endpoint_invalido") from None
    return valor


def validar_chaves(corpo: Any) -> tuple[str, str]:
    """`keys` = {p256dh (65 bytes), auth (16 bytes)} em base64url."""
    chaves = corpo.get("keys") if isinstance(corpo, dict) else None
    if not isinstance(chaves, dict) or set(chaves) != {"p256dh", "auth"}:
        raise RequisicaoInvalida(MSG_CHAVES, "push_chaves_invalidas")
    p256dh, auth = chaves["p256dh"], chaves["auth"]
    if (
        decodificar_b64url(p256dh, BYTES_P256DH) is None
        or decodificar_b64url(auth, BYTES_AUTH) is None
    ):
        raise RequisicaoInvalida(MSG_CHAVES, "push_chaves_invalidas")
    return p256dh.rstrip("="), auth.rstrip("=")


class ServicoPush:
    def __init__(
        self, repo: Repositorio, config: Config, resolvedor: Resolvedor = resolver_dns
    ) -> None:
        self._repo = repo
        self._config = config
        self._resolvedor = resolvedor

    def _vapid_configurado(self) -> bool:
        """Mesmo critério do despachante: chaves E subject válidos (senão nada seria enviado)."""
        c = self._config
        return vapid_valido(c.vapid_public_key, c.vapid_private_key, c.vapid_subject)

    def _exigir_ligado(self) -> None:
        if not self._repo.ler_configuracoes().notificacao_push:
            raise RecursoDesligado(MSG_PUSH_DESLIGADO)

    def configuracao(self, perfil: Perfil) -> dict[str, Any]:
        """`habilitado` = flag ligada + VAPID configurado. Só a chave pública sai daqui."""
        _ = perfil  # exige login; a resposta não depende do usuário
        ligado = self._repo.ler_configuracoes().notificacao_push and self._vapid_configurado()
        return {
            "habilitado": ligado,
            "chave_publica": self._config.vapid_public_key if ligado else None,
        }

    def registrar(self, perfil: Perfil, corpo: Any) -> None:
        self._exigir_ligado()
        if not self._vapid_configurado():
            raise RecursoDesligado(MSG_PUSH_DESLIGADO)
        dados = _objeto(corpo, {"endpoint", "keys"})
        endpoint = validar_endpoint(
            dados.get("endpoint"), self._config.push_endpoint_hosts, self._resolvedor
        )
        p256dh, auth = validar_chaves(dados)
        self._repo.registrar_inscricao_push(perfil.id, endpoint, p256dh, auth)

    def revogar(self, perfil: Perfil, corpo: Any) -> None:
        """Revoga só a inscrição do próprio usuário. Não exige a flag ligada: permite limpar
        a inscrição no logout mesmo com o recurso desligado. Não valida DNS (só compara)."""
        dados = _objeto(corpo, {"endpoint"})
        endpoint = dados.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint or len(endpoint) > MAX_ENDPOINT:
            raise RequisicaoInvalida(MSG_ENDPOINT, "push_endpoint_invalido")
        self._repo.revogar_inscricao_push(perfil.id, endpoint)
