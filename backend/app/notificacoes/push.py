"""Envio de Web Push (VAPID) das notificações internas.

Payload genérico e sem PII: `{titulo, texto, destino, tag}`, montado SÓ de
`services.notificacoes.montar_texto` e `montar_destino` (mapas fixos). Nunca nome, placa, trajeto,
localização ou km. `tag` = `n-<id da notificação>` (o aparelho colapsa repetições).

LGPD/segurança: o endpoint (URL do serviço push), as chaves e o corpo NUNCA vão a log nem a
mensagem de erro; a mensagem de `WebPushException` pode conter a URL e por isso é descartada. Só
sobem códigos curtos (`FalhaPush.codigo`) e o nome do tipo da exceção.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter

from app.config import PUSH_HOSTS_PADRAO, Config
from app.notificacoes.n8n import exigir_destino_publico, resolver_dns
from app.services.notificacoes import montar_destino, montar_texto

TTL_SEGUNDOS = 24 * 3600
TIMEOUT_SEGUNDOS = 10.0
TIPOS_PUSH = frozenset(
    {
        "requisicao_nova",
        "requisicao_autorizada",
        "requisicao_negada",
        "requisicao_saida",
        "requisicao_chegada",
        "requisicao_cancelada_terceiros",
        "requisicao_cancelada_pelo_solicitante",
        "motorista_designado",
        "motorista_removido",
        "viagem_cancelada_motorista",
        "checklist_avaria",
    }
)
DESTINO_PADRAO = "/notificacoes"
# Erros em que a inscrição em si é o problema (contam falha e podem revogá-la). 5xx, 429,
# timeout, rede e redirect são transitórios/de destino: não punem a inscrição.
HTTP_DEFINITIVOS = frozenset({400, 401, 403, 404, 410, 413})
Resolvedor = Callable[[str, int], list[str]]


class FalhaPush(Exception):
    """Falha de entrega. `codigo` é curto e seguro para log; `expirada` = inscrição inválida
    (404/410): deve ser removida; `definitiva` = erro que conta falha da inscrição (ver
    HTTP_DEFINITIVOS). Nunca carrega endpoint, chave nem corpo."""

    def __init__(self, codigo: str, *, expirada: bool = False, definitiva: bool = False) -> None:
        super().__init__(codigo)
        self.codigo = codigo
        self.expirada = expirada
        self.definitiva = definitiva or expirada


class EnviadorPush(Protocol):
    def enviar(self, inscricao: Mapping[str, Any], payload: Mapping[str, str]) -> None:
        """Entrega o payload à inscrição. Lança FalhaPush; nunca devolve dados."""
        ...


def montar_payload(item: Mapping[str, Any]) -> dict[str, str]:
    """Payload genérico da notificação (sem PII). Só mapas fixos; tipo desconhecido => genérico."""
    tipo = str(item.get("tipo") or "")
    titulo, texto = montar_texto(tipo, item.get("codigo"))
    destino = montar_destino(tipo, item.get("requisicao_id")) or DESTINO_PADRAO
    return {
        "titulo": titulo,
        "texto": texto,
        "destino": destino,
        "tag": f"n-{item['id']}",
    }


def endpoint_publico(
    endpoint: str,
    permitidos: list[str] | None = None,
    resolvedor: Resolvedor = resolver_dns,
) -> bool:
    """Reverifica no envio: https, porta 443, host na allowlist (`push_endpoint_hosts`) e só IPs
    públicos (anti-SSRF/DNS rebinding)."""
    return _ip_validado(endpoint, permitidos, resolvedor) is not None


def _ip_validado(
    endpoint: str, permitidos: list[str] | None, resolvedor: Resolvedor
) -> tuple[str, str] | None:
    """(host, ip público escolhido) se o endpoint passa em todas as checagens; senão None."""
    from app.services.push import host_permitido

    try:
        partes = urlsplit(endpoint)
        host = (partes.hostname or "").lower().rstrip(".")
        porta = partes.port or 443
        if (
            partes.scheme != "https"
            or not host
            or porta != 443
            or partes.username is not None
            or partes.password is not None
            or not host_permitido(host, list(permitidos or PUSH_HOSTS_PADRAO))
        ):
            return None
        enderecos: list[str] = []

        def captura(h: str, p: int) -> list[str]:
            enderecos[:] = resolvedor(h, p)
            return enderecos

        exigir_destino_publico(host, porta, captura)
        return host, str(enderecos[0]).split("%", 1)[0]
    except Exception:
        return None


class _AdaptadorIpFixo(HTTPAdapter):
    """Conecta ao IP já validado (fecha o DNS rebinding entre checagem e envio), mantendo Host,
    SNI e verificação do certificado do nome original."""

    def __init__(self, host: str, ip: str) -> None:
        self._host = host
        self._ip = ip
        super().__init__(max_retries=0)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["server_hostname"] = self._host
        kwargs["assert_hostname"] = self._host
        super().init_poolmanager(*args, **kwargs)

    def send(self, request: requests.PreparedRequest, *args: Any, **kwargs: Any) -> Any:
        ip = f"[{self._ip}]" if ":" in self._ip else self._ip
        request.url = (request.url or "").replace(f"//{self._host}", f"//{ip}", 1)
        request.headers["Host"] = self._host
        return super().send(request, *args, **kwargs)


class _SessaoSemRedirect(requests.Session):
    """Nunca segue redirect: um 30x de um host permitido não pode levar o POST a outro destino."""

    def __init__(self) -> None:
        super().__init__()
        self.max_redirects = 0
        self.trust_env = False  # sem proxy/netrc do ambiente

    def request(self, *args: Any, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        kwargs["allow_redirects"] = False
        return super().request(*args, **kwargs)


def _sessao_para(host: str, ip: str) -> requests.Session:
    sessao = _SessaoSemRedirect()
    sessao.mount("https://", _AdaptadorIpFixo(host, ip))
    return sessao


class EnviadorWebPush:
    """Implementação com pywebpush. Importa a biblioteca só no uso (testes injetam um falso)."""

    def __init__(self, cfg: Config, resolvedor: Resolvedor = resolver_dns) -> None:
        self._privada: str = getattr(cfg, "vapid_private_key", "") or ""
        self._subject: str = getattr(cfg, "vapid_subject", "") or ""
        self._hosts: list[str] = list(getattr(cfg, "push_endpoint_hosts", None) or [])
        self._resolvedor = resolvedor

    def enviar(self, inscricao: Mapping[str, Any], payload: Mapping[str, str]) -> None:
        from pywebpush import WebPushException, webpush

        endpoint = str(inscricao["endpoint"])
        validado = _ip_validado(endpoint, self._hosts, self._resolvedor)
        if validado is None:
            raise FalhaPush("destino_invalido")
        info = {
            "endpoint": endpoint,
            "keys": {"p256dh": str(inscricao["p256dh"]), "auth": str(inscricao["auth"])},
        }
        try:
            webpush(
                subscription_info=info,
                data=json.dumps(dict(payload), ensure_ascii=False),
                vapid_private_key=self._privada,
                vapid_claims={"sub": self._subject},
                ttl=TTL_SEGUNDOS,
                timeout=TIMEOUT_SEGUNDOS,
                requests_session=_sessao_para(*validado),
            )
        except WebPushException as e:  # a mensagem pode conter a URL: descartada
            resposta = getattr(e, "response", None)
            status = getattr(resposta, "status_code", None)
            if status in (404, 410):
                raise FalhaPush("inscricao_expirada", expirada=True) from None
            if isinstance(status, int) and 300 <= status < 400:
                raise FalhaPush("redirect") from None  # nunca vai ao Location
            if isinstance(status, int):
                raise FalhaPush(f"http_{status}", definitiva=status in HTTP_DEFINITIVOS) from None
            raise FalhaPush("push_erro") from None
        except requests.TooManyRedirects:  # max_redirects=0: o requests recusa o 3xx
            raise FalhaPush("redirect") from None
        except Exception as e:  # rede, timeout, chave inválida...
            raise FalhaPush(f"erro_{type(e).__name__.lower()[:30]}") from None
