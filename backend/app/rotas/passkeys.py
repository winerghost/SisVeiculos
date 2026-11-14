"""Rotas de passkeys (WebAuthn). Só traduzem HTTP <-> service; o usuário é sempre o do JWT.

Nenhuma rota devolve token ou sessão: o Supabase Auth é o único emissor. Nunca logam corpo,
credential_id, desafio nem chaves.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, g, jsonify, request

from app.auth.decoradores import requer_auth
from app.extensions import chave_usuario, deps, limiter
from app.services.passkeys import ServicoPasskeys

passkeys = Blueprint("passkeys", __name__, url_prefix="/api/passkeys")


def _limite() -> str:
    return deps().config.limite_passkeys


def _servico() -> ServicoPasskeys:
    d = deps()
    return ServicoPasskeys(d.repo, d.config)


@passkeys.get("")
@requer_auth()
def listar() -> Any:
    return jsonify(_servico().listar(g.perfil, g.claims))


@passkeys.post("/registro/opcoes")
@requer_auth()
@limiter.limit(_limite, key_func=chave_usuario)
def opcoes_registro() -> Any:
    return jsonify(_servico().opcoes_registro(g.perfil, g.claims, request.get_json(silent=True)))


@passkeys.post("/registro/confirmar")
@requer_auth()
@limiter.limit(_limite, key_func=chave_usuario)
def confirmar_registro() -> Any:
    resp = jsonify(_servico().confirmar_registro(g.perfil, g.claims, request.get_json(silent=True)))
    resp.status_code = 201
    return resp


@passkeys.post("/autenticacao/opcoes")
@requer_auth()
@limiter.limit(_limite, key_func=chave_usuario)
def opcoes_autenticacao() -> Any:
    return jsonify(_servico().opcoes_autenticacao(g.perfil))


@passkeys.post("/autenticacao/confirmar")
@requer_auth()
@limiter.limit(_limite, key_func=chave_usuario)
def confirmar_autenticacao() -> Any:
    return jsonify(
        _servico().confirmar_autenticacao(g.perfil, g.claims, request.get_json(silent=True))
    )


@passkeys.delete("/<credencial_id>")
@requer_auth()
@limiter.limit(_limite, key_func=chave_usuario)
def revogar(credencial_id: str) -> Any:
    _servico().revogar(g.perfil, g.claims, credencial_id)
    return Response(status=204)
