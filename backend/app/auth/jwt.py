"""Validação do JWT do Supabase por JWKS (assinatura, exp, aud, iss). Sem HS256."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import jwt
from jwt import PyJWKClient, PyJWKSet

from app.errors import ErroInterno, NaoAutenticado

log = logging.getLogger(__name__)

# Só algoritmos assimétricos: bloqueia confusão alg=none/HS256.
ALGORITMOS = ["ES256", "RS256", "EdDSA"]
MSG = "Sessão inválida ou expirada. Faça login novamente."

ResolvedorChave = Callable[[str], Any]


class JWKSIndisponivel(Exception):  # noqa: N818
    """Falha de infraestrutura ao obter o JWKS (rede, timeout, resposta ilegível).

    Diferente de token inválido: vira 503 (o cliente não deve ser deslogado)."""


class ResolvedorJWKS:
    """Chaves do JWKS com cache e cooldown próprio de refresh.

    Um `kid` desconhecido só provoca nova busca externa depois do cooldown: um atacante com
    tokens de kid aleatório não gera uma chamada externa por requisição. A busca roda fora do
    lock de estado (quem tem a chave em cache nunca espera a rede); um segundo lock serializa
    as buscas, com double-check, para não haver várias buscas simultâneas. O cooldown cheio
    conta a partir de um sucesso; após falha de infra vale `cooldown_falha` (menor), para
    recuperar logo quando o JWKS voltar sem abrir a porta a uma enxurrada de chamadas.
    """

    def __init__(
        self,
        jwks_url: str,
        *,
        cooldown: float = 60.0,
        cooldown_falha: float = 5.0,
        lifespan: float = 3600.0,
        relogio: Callable[[], float] = time.monotonic,
        cliente: PyJWKClient | None = None,
    ) -> None:
        self._cliente = cliente or PyJWKClient(jwks_url, cache_keys=False, timeout=5)
        self._cooldown = cooldown
        self._cooldown_falha = cooldown_falha
        self._lifespan = lifespan
        self._relogio = relogio
        self._chaves: dict[str, Any] = {}
        self._bloqueado_ate: float | None = None
        self._ultimo_sucesso: float | None = None
        self._falha_recente = False
        self._lock = threading.Lock()  # estado; nunca retido durante a rede
        self._lock_busca = threading.Lock()  # serializa as buscas externas

    def _do_cache(self, kid: str) -> Any | None:
        with self._lock:
            vencido = (
                self._ultimo_sucesso is None
                or self._relogio() - self._ultimo_sucesso > self._lifespan
            )
            return None if vencido else self._chaves.get(kid)

    def _buscar(self) -> None:
        try:
            conjunto = PyJWKSet.from_dict(self._cliente.fetch_data())
            chaves = {k.key_id: k.key for k in conjunto.keys if k.key_id}
        except Exception as e:
            with self._lock:
                self._bloqueado_ate = self._relogio() + self._cooldown_falha
                self._falha_recente = True
            log.warning("jwks_indisponivel tipo=%s", type(e).__name__)
            raise JWKSIndisponivel from None
        with self._lock:
            agora = self._relogio()
            self._chaves = chaves
            self._ultimo_sucesso = agora
            self._bloqueado_ate = agora + self._cooldown
            self._falha_recente = False

    def __call__(self, token: str) -> Any:
        kid = jwt.get_unverified_header(token).get("kid")
        if not isinstance(kid, str) or not kid:
            raise jwt.PyJWTError("kid ausente")
        chave = self._do_cache(kid)
        if chave is not None:
            return chave
        with self._lock_busca:
            chave = self._do_cache(kid)  # double-check: outra thread pode ter buscado
            if chave is not None:
                return chave
            with self._lock:
                agora = self._relogio()
                pode_buscar = self._bloqueado_ate is None or agora >= self._bloqueado_ate
            if pode_buscar:
                self._buscar()
            with self._lock:
                chave = self._chaves.get(kid)
                if chave is None and self._falha_recente:
                    raise JWKSIndisponivel
        if chave is None:
            raise jwt.PyJWTError("kid desconhecido")
        return chave


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
        except JWKSIndisponivel:
            # Infra, não credencial: 503 evita deslogar todos os usuários numa falha do JWKS.
            raise ErroInterno("Serviço de autenticação indisponível. Tente novamente.") from None
        except Exception as e:  # PyJWTError (token inválido) e demais
            log.info("jwt_recusado tipo=%s", type(e).__name__)
            raise NaoAutenticado(MSG) from None
        if not isinstance(claims.get("sub"), str):
            raise NaoAutenticado(MSG)
        return claims
