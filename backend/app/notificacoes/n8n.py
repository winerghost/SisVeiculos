"""Integração com o webhook do n8n: só notifica; nenhuma resposta dele é lida para decidir.

Garantias:
- Desligado por padrão: sem URL ou sem token o notificador é um no-op.
- Assíncrono e best-effort: fila em memória + uma thread; `notificar` nunca bloqueia nem lança.
- Entrega at-most-once por processo (se o processo cair, eventos na fila se perdem).
- Payload mínimo por padrão, montado por allowlist (sem nome, e-mail, trajeto, placa, km, fotos,
  ids de pessoas). Exceção APROVADA pelo dono do projeto (LGPD): em `requisicao.saida` e
  `requisicao.chegada` o serviço envia também `detalhes` (dados completos da viagem; versão 2).
  A exceção aprovada inclui ids de pessoas (com nome), texto livre (`descricao_avarias`,
  `observacao`, `trajeto`) e coordenadas da localização. Nunca segue token, URL assinada, caminho
  de Storage, e-mail, CPF nem telefone (denylist por substring no serviço). Ver `montar_payload`.
- Nunca loga URL, token, corpo ou resposta; só o tipo da exceção e o nome do evento.
- Assinatura: `X-Assinatura: sha256=HMAC(chave, "<timestamp>.<corpo>")` e `X-Timestamp`
  (segundos Unix) para o n8n rejeitar replay. A chave é `N8N_HMAC_SECRET`; se ausente, cai no
  token (compatibilidade). O token é sempre e só o `Authorization: Bearer <token>`.
- Idempotência: cada evento tem um `evento_id` (uuid4) gerado uma vez em `montar_payload`, presente
  no corpo assinado e no header `X-Evento-Id`, e idêntico em todas as retentativas. O n8n DEVE
  deduplicar por `X-Evento-Id` (ou `evento_id` do corpo): uma mesma entrega pode chegar mais de
  uma vez (timeout com resposta perdida). `X-Entrega-Id` identifica só a sequência de tentativas.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import re
import socket
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

VERSAO_PAYLOAD = 1
VERSAO_PAYLOAD_DETALHES = 2
EVENTOS = frozenset(
    {
        "requisicao.criada",
        "requisicao.autorizada",
        "requisicao.negada",
        "requisicao.saida",
        "requisicao.chegada",
        "requisicao.cancelada",
        "checklist.registrado",
    }
)
# Campos da requisição que podem sair daqui (nenhum identifica pessoa).
_CAMPOS_REQ = ("status", "veiculo_id", "unidade")
_HOSTS_PROIBIDOS = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})
_SUFIXOS_PROIBIDOS = (".localhost", ".local", ".internal", ".intranet", ".lan", ".home.arpa")
_ROTULO = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_RE_HOST = re.compile(rf"(?=.{{1,253}}$){_ROTULO}(\.{_ROTULO})*")
_MSG_PUBLICO = "N8N_WEBHOOK_URL deve apontar para um host público."


class Notificador(Protocol):
    def notificar(
        self, evento: str, requisicao: dict[str, Any], detalhes: dict[str, Any] | None = None
    ) -> None:
        """Nunca bloqueia e nunca lança."""
        ...

    def fechar(self) -> None: ...


class NotificadorNulo:
    """No-op (integração desligada)."""

    def notificar(
        self, evento: str, requisicao: dict[str, Any], detalhes: dict[str, Any] | None = None
    ) -> None:
        return None

    def fechar(self) -> None:
        return None


def _como_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def normalizar_hosts(hosts: Iterable[str]) -> frozenset[str]:
    return frozenset(h.strip().lower().rstrip(".") for h in hosts if h and h.strip())


def host_valido(host: str) -> bool:
    """Nome de host simples (sem esquema, porta, caminho, credenciais ou curinga)."""
    return bool(_RE_HOST.fullmatch(host)) or _como_ip(host) is not None


def resolver_dns(host: str, porta: int) -> list[str]:
    """Endereços IP do host (consulta DNS real). Lança OSError se não resolver."""
    infos = socket.getaddrinfo(host, porta, proto=socket.IPPROTO_TCP)
    return sorted({str(i[4][0]) for i in infos})


def exigir_destino_publico(
    host: str, porta: int, resolvedor: Callable[[str, int], list[str]] = resolver_dns
) -> None:
    """Resolve o host e exige que TODOS os IPs sejam globais (anti-SSRF por DNS: um nome
    público que aponta para 127.0.0.1/10.x/169.254.x é recusado). Lança RuntimeError."""
    try:
        enderecos = resolvedor(host, porta)
    except Exception:
        raise RuntimeError("Host do webhook não resolveu.") from None
    if not enderecos:
        raise RuntimeError("Host do webhook não resolveu.")
    for bruto in enderecos:
        ip = _como_ip(str(bruto).split("%", 1)[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if ip is None or not ip.is_global or ip.is_multicast:
            raise RuntimeError("Host do webhook resolve para endereço não público.")


def validar_url_webhook(url: str, hosts_permitidos: Iterable[str] = ()) -> None:
    """Exige https e host público (anti-SSRF) e, se houver allowlist, que o host conste nela.
    Validação textual (sem DNS); a resolução é feita a cada entrega. Só mensagens genéricas,
    sem eco da URL."""
    try:
        partes = urlsplit(url)
        host = (partes.hostname or "").lower().rstrip(".")
        _ = partes.port  # lança ValueError se a porta for inválida
    except ValueError:
        raise RuntimeError("N8N_WEBHOOK_URL inválida.") from None
    if partes.scheme != "https" or not host:
        raise RuntimeError("N8N_WEBHOOK_URL deve usar https://.")
    if partes.username is not None or partes.password is not None:
        raise RuntimeError("N8N_WEBHOOK_URL não pode conter credenciais.")
    permitidos = normalizar_hosts(hosts_permitidos)
    if permitidos and host not in permitidos:
        raise RuntimeError("N8N_WEBHOOK_URL: host fora de N8N_ALLOWED_HOSTS.")
    ip = _como_ip(host)
    if ip is not None:
        if not ip.is_global or ip.is_multicast:
            raise RuntimeError(_MSG_PUBLICO)
        return
    if host in _HOSTS_PROIBIDOS or host.endswith(_SUFIXOS_PROIBIDOS) or "." not in host:
        raise RuntimeError(_MSG_PUBLICO)


def montar_payload(
    evento: str,
    requisicao: dict[str, Any],
    evento_id: str | None = None,
    detalhes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Payload mínimo por allowlist; com `detalhes` (só saída e chegada) acrescenta o bloco
    completo aprovado e sobe a versão para 2.

    `evento_id` (uuid4) é estável por evento; o n8n deduplica por ele."""
    payload: dict[str, Any] = {
        "evento": evento,
        "evento_id": evento_id or str(uuid.uuid4()),
        "requisicao_id": requisicao.get("id"),
        **{c: requisicao.get(c) for c in _CAMPOS_REQ},
        "ocorrido_em": datetime.now(UTC).isoformat(),
        "versao": VERSAO_PAYLOAD,
    }
    if detalhes:
        payload["detalhes"] = detalhes
        payload["versao"] = VERSAO_PAYLOAD_DETALHES
    return payload


class N8nWebhook:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout: float = 3.0,
        tentativas: int = 2,
        backoff: float = 0.5,
        max_fila: int = 100,
        cliente: httpx.Client | None = None,
        ao_desistir: Callable[[str], None] | None = None,
        hmac_secret: str = "",
        resolvedor: Callable[[str, int], list[str]] = resolver_dns,
    ) -> None:
        # httpx registra a URL completa em INFO; ela contém o segredo do caminho do webhook.
        for nome in ("httpx", "httpcore"):
            logging.getLogger(nome).setLevel(logging.WARNING)
        self._url = url
        self._resolvedor = resolvedor
        self._token = token
        self._chave_hmac = (hmac_secret or token).encode()  # fallback: o token
        self._tentativas = max(1, tentativas)
        self._backoff = backoff
        self._max_fila = max(1, max_fila)
        self._cliente = cliente or httpx.Client(timeout=timeout, follow_redirects=False)
        self._ao_desistir = ao_desistir  # recebe só o nome do evento (sem PII)
        self._fila: deque[tuple[str, bytes, str]] = deque()
        self._cond = threading.Condition()
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None
        self.descartados = 0  # contador: fila cheia (sem PII)
        self.falhas = 0  # entregas desistidas após as tentativas

    def __repr__(self) -> str:  # nunca expõe URL nem token
        return "N8nWebhook()"

    # -- API pública ---------------------------------------------------------------
    def notificar(
        self, evento: str, requisicao: dict[str, Any], detalhes: dict[str, Any] | None = None
    ) -> None:
        try:
            if evento not in EVENTOS or self._parar.is_set():
                return
            evento_id = str(uuid.uuid4())  # gerado uma vez; reutilizado nas retentativas
            corpo = json.dumps(
                montar_payload(evento, requisicao, evento_id, detalhes),
                separators=(",", ":"),
                ensure_ascii=False,
                default=str,  # datetime/UUID/Decimal vindos do banco
            ).encode()
            with self._cond:
                if len(self._fila) >= self._max_fila:
                    self._fila.popleft()  # descarta o mais antigo
                    self.descartados += 1
                    log.warning("n8n: fila cheia, evento antigo descartado")
                self._fila.append((evento, corpo, evento_id))
                self._iniciar_thread()
                self._cond.notify()
        except Exception as exc:  # noqa: BLE001 - best-effort: nunca propaga
            log.warning("n8n: falha ao enfileirar (%s)", type(exc).__name__)

    def fechar(self, espera: float = 2.0) -> None:
        """Encerra o worker; descarta o que restar na fila."""
        self._parar.set()
        with self._cond:
            self._cond.notify_all()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=espera)
        try:
            self._cliente.close()
        except Exception as exc:  # noqa: BLE001
            log.debug("n8n: erro ao fechar cliente (%s)", type(exc).__name__)

    # -- worker --------------------------------------------------------------------
    def _iniciar_thread(self) -> None:  # chamado com o lock; thread criada sob demanda (fork)
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="n8n-webhook", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not self._fila and not self._parar.is_set():
                    self._cond.wait()
                if self._parar.is_set():
                    return
                evento, corpo, evento_id = self._fila.popleft()
            self._entregar(evento, corpo, evento_id)

    def _entregar(self, evento: str, corpo: bytes, evento_id: str) -> None:
        entrega_id = str(uuid.uuid4())
        for tentativa in range(1, self._tentativas + 1):
            try:
                self._checar_destino()  # DNS pode mudar depois da subida (rebinding)
                resp = self._cliente.post(
                    self._url, content=corpo, headers=self._cabecalhos(corpo, entrega_id, evento_id)
                )
                if resp.status_code < 400:  # a resposta não é lida
                    return
                motivo = f"HTTP {resp.status_code // 100}xx"
            except Exception as exc:  # noqa: BLE001
                motivo = type(exc).__name__
            if tentativa < self._tentativas:
                if self._parar.wait(self._backoff * tentativa):
                    return
            else:
                self.falhas += 1
                log.warning("n8n: entrega desistida evento=%s motivo=%s", evento, motivo)
                if self._ao_desistir is not None:
                    try:
                        self._ao_desistir(evento)
                    except Exception as exc:  # noqa: BLE001 - nunca derruba o worker
                        log.warning("n8n: callback de falha erro (%s)", type(exc).__name__)

    def _checar_destino(self) -> None:
        partes = urlsplit(self._url)
        host = (partes.hostname or "").lower().rstrip(".")
        if _como_ip(host) is not None:  # IP literal já validado como global na criação
            return
        exigir_destino_publico(host, partes.port or 443, self._resolvedor)

    def _cabecalhos(self, corpo: bytes, entrega_id: str, evento_id: str) -> dict[str, str]:
        ts = str(int(time.time()))  # novo a cada tentativa (janela anti-replay)
        mac = hmac.new(self._chave_hmac, ts.encode() + b"." + corpo, hashlib.sha256)
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}",
            "X-Timestamp": ts,
            "X-Assinatura": "sha256=" + mac.hexdigest(),
            "X-Entrega-Id": entrega_id,
            "X-Evento-Id": evento_id,
        }


def criar_notificador(
    url: str,
    token: str,
    ao_desistir: Callable[[str], None] | None = None,
    hmac_secret: str = "",
    hosts_permitidos: Iterable[str] = (),
) -> Notificador:
    """No-op sem URL ou sem token; senão valida a URL (anti-SSRF) e cria o webhook.
    `ao_desistir` (opcional) é chamado, na thread do worker, quando uma entrega é desistida.
    `hmac_secret` (opcional) assina o corpo; ausente, a assinatura usa o token (aviso único)."""
    if not url or not token:
        return NotificadorNulo()
    validar_url_webhook(url, hosts_permitidos)
    if not hmac_secret:
        log.warning(
            "n8n: N8N_HMAC_SECRET não definido; a assinatura usa o N8N_WEBHOOK_TOKEN. "
            "Defina um segredo HMAC separado."
        )
    elif hmac.compare_digest(hmac_secret.encode(), token.encode()):
        log.warning("n8n: N8N_HMAC_SECRET igual ao N8N_WEBHOOK_TOKEN; use valores distintos.")
    return N8nWebhook(url, token, ao_desistir=ao_desistir, hmac_secret=hmac_secret)
