"""Registro (best-effort) do login bem-sucedido, disparado por `autenticar()`.

- Roda depois de JWT válido, perfil ativo e MFA ok, uma vez por `session_id` por processo.
- O cache em memória (limite de tamanho + TTL) NUNCA é a fonte da verdade: só evita RPC
  repetida. A RPC é idempotente (UNIQUE em session_id); o id só entra no cache após
  sucesso (true/false) da RPC.
- A gravação sai por uma fila pequena e uma thread (como o notificador n8n): a resposta ao
  usuário nunca espera o banco. Fila cheia descarta (best-effort).
- Falha nunca derruba a requisição. Log só do tipo/código da falha; contagem agregada por
  código em WARNING periódico. Sem IP, user-agent nem session_id em log.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.errors import FalhaRegistroAcesso
from app.repositorios.base import Repositorio
from app.services.acessos_minimizacao import mascarar_ip, metodo_autenticacao, reduzir_user_agent

log = logging.getLogger(__name__)

CACHE_MAX = 10_000
CACHE_TTL_S = 6 * 3600.0
FILA_MAX = 200
INTERVALO_AGREGADO_S = 60.0


@dataclass(frozen=True)
class _Registro:
    usuario_id: str
    session_id: str
    ocorrido_em: str
    metodo: str
    aal: str | None
    ip_mascarado: str | None
    ua_familia: str


class _Vistos:
    """Conjunto de sessões já registradas, com TTL e tamanho máximo (thread-safe)."""

    def __init__(self, maximo: int, ttl: float, relogio: Callable[[], float]) -> None:
        self._maximo, self._ttl, self._relogio = maximo, ttl, relogio
        self._d: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def __contains__(self, chave: str) -> bool:
        with self._lock:
            expira = self._d.get(chave)
            if expira is None:
                return False
            if expira <= self._relogio():
                del self._d[chave]
                return False
            return True

    def marcar(self, chave: str) -> None:
        with self._lock:
            self._d[chave] = self._relogio() + self._ttl
            self._d.move_to_end(chave)
            while len(self._d) > self._maximo:
                self._d.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._d)


def _session_id(claims: Mapping[str, Any]) -> str | None:
    bruto = claims.get("session_id")
    if not isinstance(bruto, str):
        return None
    try:
        return str(uuid.UUID(bruto))
    except ValueError:
        return None


def _ocorrido_em(claims: Mapping[str, Any]) -> str:
    """`iat` do token (limitado a agora); sem `iat` numérico válido, o instante atual."""
    agora = datetime.now(UTC)
    iat = claims.get("iat")
    if isinstance(iat, int | float) and not isinstance(iat, bool):
        try:
            return min(datetime.fromtimestamp(iat, UTC), agora).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    return agora.isoformat()


class RegistradorAcessos:
    def __init__(
        self,
        repo: Repositorio,
        *,
        assincrono: bool = True,
        relogio: Callable[[], float] = time.monotonic,
        cache_max: int = CACHE_MAX,
        cache_ttl: float = CACHE_TTL_S,
        fila_max: int = FILA_MAX,
        intervalo_agregado: float = INTERVALO_AGREGADO_S,
    ) -> None:
        self._repo = repo
        self._assincrono = assincrono
        self._relogio = relogio
        self._vistos = _Vistos(cache_max, cache_ttl, relogio)
        self._pendentes: set[str] = set()  # na fila/em gravação: evita enfileirar em duplicidade
        self._lock = threading.Lock()
        self._fila: queue.Queue[_Registro | None] = queue.Queue(maxsize=fila_max)
        self._thread: threading.Thread | None = None
        self._falhas: dict[str, int] = {}
        self._intervalo = intervalo_agregado
        self._ultimo_agregado = relogio() - intervalo_agregado  # 1ª falha já avisa

    # -- API usada pelo decorator ------------------------------------------------------
    def registrar(
        self, usuario_id: str, claims: Mapping[str, Any], *, ip: str | None, user_agent: str | None
    ) -> None:
        """Nunca levanta: qualquer problema vira contagem de falha."""
        try:
            sessao = _session_id(claims)
            if sessao is None or sessao in self._vistos:
                return
            with self._lock:
                if sessao in self._pendentes:
                    return
                self._pendentes.add(sessao)
            aal = claims.get("aal")
            item = _Registro(
                usuario_id=usuario_id,
                session_id=sessao,
                ocorrido_em=_ocorrido_em(claims),
                metodo=metodo_autenticacao(claims.get("amr")),
                aal=aal if aal in {"aal1", "aal2"} else None,
                ip_mascarado=mascarar_ip(ip),  # IP completo nunca passa daqui
                ua_familia=reduzir_user_agent(user_agent),  # nem o user-agent bruto
            )
            if not self._assincrono:
                self._gravar(item)
                return
            self._garantir_thread()
            try:
                self._fila.put_nowait(item)
            except queue.Full:
                self._soltar(sessao)
                self._falha("fila_cheia")
        except Exception as e:
            self._falha(type(e).__name__)

    def contadores(self) -> dict[str, int]:
        """Falhas desde o último log agregado (sem PII; só código -> contagem)."""
        with self._lock:
            return dict(self._falhas)

    def aguardar(self) -> None:
        """Espera a fila esvaziar (testes e desligamento)."""
        self._fila.join()

    def fechar(self, espera: float = 2.0) -> None:
        t = self._thread
        if t is None:
            return
        try:
            self._fila.put_nowait(None)
        except queue.Full:
            return
        t.join(timeout=espera)

    # -- internos ----------------------------------------------------------------------
    def _soltar(self, sessao: str) -> None:
        with self._lock:
            self._pendentes.discard(sessao)

    def _garantir_thread(self) -> None:
        with self._lock:  # thread criada sob demanda (seguro com fork do gunicorn)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._loop, name="acessos-login", daemon=True
                )
                self._thread.start()

    def _loop(self) -> None:
        while True:
            item = self._fila.get()
            try:
                if item is None:
                    return
                self._gravar(item)
            finally:
                self._fila.task_done()

    def _gravar(self, r: _Registro) -> None:
        try:
            self._repo.registrar_acesso_login(
                usuario_id=r.usuario_id,
                session_id=r.session_id,
                ocorrido_em=r.ocorrido_em,
                metodo=r.metodo,
                aal=r.aal,
                ip_mascarado=r.ip_mascarado,
                ua_familia=r.ua_familia,
            )
            self._vistos.marcar(r.session_id)  # true (gravou) ou false (já existia)
        except FalhaRegistroAcesso as e:
            self._falha(e.codigo)
        except Exception as e:
            self._falha(type(e).__name__)
        finally:
            self._soltar(r.session_id)

    def _falha(self, codigo: str) -> None:
        codigo = codigo[:32]
        log.info("registro_acesso_falhou codigo=%s", codigo)
        with self._lock:
            self._falhas[codigo] = self._falhas.get(codigo, 0) + 1
            agora = self._relogio()
            if agora - self._ultimo_agregado < self._intervalo:
                return
            resumo = " ".join(f"{k}={v}" for k, v in sorted(self._falhas.items()))
            self._falhas.clear()
            self._ultimo_agregado = agora
        log.warning("registro_acesso_falhas_agregado %s", resumo)
