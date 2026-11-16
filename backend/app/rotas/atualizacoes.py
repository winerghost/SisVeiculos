"""GET /api/atualizacoes: versões opacas por recurso para o polling do cliente.

Rota fina: autenticação (sem registro de acesso, configurações com cache curto), rate limit por
usuário e ETag/304. A regra (o que é relevante para cada perfil) fica em services/atualizacoes.py
e na função do banco. Sem dado pessoal na resposta e em log.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import Blueprint, Response, current_app, g, jsonify, request

from app.auth.decoradores import autenticar
from app.extensions import chave_usuario, deps, limiter
from app.services.atualizacoes import (
    CacheConfiguracoes,
    ServicoAtualizacoes,
    derivar_chave,
    etag_confere,
    etag_de,
)

atualizacoes = Blueprint("atualizacoes", __name__, url_prefix="/api/atualizacoes")

_CHAVE_CACHE = "sisveiculos_cache_config_atualizacoes"


def _cache() -> CacheConfiguracoes:
    return current_app.extensions.setdefault(_CHAVE_CACHE, CacheConfiguracoes())  # type: ignore[no-any-return]


def _limite() -> str:
    return deps().config.limite_atualizacoes


def _auth_polling(fn: Callable[..., Any]) -> Callable[..., Any]:
    """`requer_auth()` sem `_registrar_acesso` e com `ler_configuracoes` em cache curto."""

    @wraps(fn)
    def interno(*args: Any, **kwargs: Any) -> Any:
        repo = deps().repo
        autenticar(registrar_acesso=False, configuracoes=lambda: _cache().ler(repo))
        return fn(*args, **kwargs)

    return interno


@atualizacoes.get("")
@_auth_polling
@limiter.limit(_limite, key_func=chave_usuario)
def obter() -> Any:
    d = deps()
    corpo = ServicoAtualizacoes(d.repo, derivar_chave(d.config.supabase_service_role_key)).versoes(
        g.perfil
    )
    etag = etag_de(corpo)
    if etag_confere(request.headers.get("If-None-Match"), etag):
        resp = Response(status=304)
    else:
        resp = jsonify(corpo)
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "no-store"
    return resp
