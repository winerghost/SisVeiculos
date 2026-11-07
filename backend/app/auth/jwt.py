"""Validação do JWT do Supabase por JWKS (assinatura, exp, aud, iss). Sem HS256."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import jwt
from jwt import PyJWKClient, PyJWKSet

from app.errors import NaoAutenticado

log = logging.getLogger(__name__)

# Só algoritmos assimétricos: bloqueia confusão alg=none/HS256.
ALGORITMOS = ["ES256", "RS256", "EdDSA"]
MSG = "Sessão inválida ou expirada. Faça login novamente."

ResolvedorChave = Callable[[str], Any]


class ResolvedorJWKS:
    """Chaves do JWKS com cache e cooldown próprio de refresh.

    Um `kid` desconhecido só provoca nova busca externa se a última tentativa foi há mais
    de `cooldown` segundos: um atacante com tokens de kid aleatório não gera uma chamada
    externa por requisição.
    """

    def __init__(
        self,
        jwks_url: str,
        *,
        cooldown: float = 60.0,
        lifespan: float = 3600.0,
        relogio: Callable[[], float] = time.monotonic,
        cliente: PyJWKClient | None = None,
    ) -> None:
        self._cliente = cliente or PyJWKClient(jwks_url, cache_keys=False, timeout=5)
        self._cooldown = cooldown
        self._lifespan = lifespan
        self._relogio = relogio
        self._chaves: dict[str, Any] = {}
        self._ultima_tentativa: float | None = None
        self._ultimo_sucesso: float | None = None
        self._lock = threading.Lock()

    def __call__(self, token: str) -> Any:
        kid = jwt.get_unverified_header(token).get("kid")
        if not isinstance(kid, str) or not kid:
            raise jwt.PyJWTError("kid ausente")
        with self._lock:
            agora = self._relogio()
            vencido = self._ultimo_sucesso is None or agora - self._ultimo_sucesso > self._lifespan
            if kid in self._chaves and not vencido:
                return self._chaves[kid]
            pode_buscar = (
                self._ultima_tentativa is None or agora - self._ultima_tentativa >= self._cooldown
            )
            if pode_buscar:
                self._ultima_tentativa = agora
                conjunto = PyJWKSet.from_dict(self._cliente.fetch_data())
                self._chaves = {k.key_id: k.key for k in conjunto.keys if k.key_id}
                self._ultimo_sucesso = agora
            if kid not in self._chaves:
                raise jwt.PyJWTError("kid desconhecido")
            return self._chaves[kid]


class VerificadorJWT:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str = "",
        resolvedor_chave: ResolvedorChave | None = None,
    ) -> None:
        if not issuer or not audience:
            raise RuntimeError("Issuer e audience do JWT são obrigatórios.")
        self._issuer = issuer
        self._audience = audience
        if resolvedor_chave is None:
            resolvedor_chave = ResolvedorJWKS(jwks_url)
        self._chave = resolvedor_chave

    def verificar(self, token: str) -> dict[str, Any]:
        """Devolve as claims ou levanta NaoAutenticado (sem detalhar o motivo ao cliente)."""
        try:
            chave = self._chave(token)
            claims = jwt.decode(
                token,
                chave,
                algorithms=ALGORITMOS,
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
                leeway=5,
            )
        except Exception as e:  # PyJWTError, falha ao buscar JWKS, etc.
            log.info("jwt_recusado tipo=%s", type(e).__name__)
            raise NaoAutenticado(MSG) from None
        if not isinstance(claims.get("sub"), str):
            raise NaoAutenticado(MSG)
        return claims
