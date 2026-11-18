"""Rotas de Web Push. Só traduzem HTTP <-> service; o dono é sempre o perfil do JWT.

Nunca logam corpo, endpoint nem chaves das inscrições.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, g, jsonify, request

from app.auth.decoradores import requer_auth
from app.extensions import chave_usuario, deps, limiter
from app.services.push import ServicoPush

push = Blueprint("push", __name__, url_prefix="/api/push")


def _limite() -> str:
    return deps().config.limite_push


def _servico() -> ServicoPush:
    d = deps()
    return ServicoPush(d.repo, d.config)


@push.get("/config")
@requer_auth()
def obter_config() -> Any:
    return jsonify(_servico().configuracao(g.perfil))


@push.post("/inscricoes")
@requer_auth()
@limiter.limit(_limite, key_func=chave_usuario)
def inscrever() -> Any:
    _servico().registrar(g.perfil, request.get_json(silent=True))
    resp = jsonify({"inscrito": True})
    resp.status_code = 201
    return resp


@push.post("/reativar")
@requer_auth()
@limiter.limit(_limite, key_func=chave_usuario)
def reativar() -> Any:
    return jsonify({"reativada": _servico().reativar(g.perfil, request.get_json(silent=True))})


@push.delete("/inscricoes")
@requer_auth()
@limiter.limit(_limite, key_func=chave_usuario)
def revogar() -> Any:
    logout, revogada = _servico().revogar(g.perfil, request.get_json(silent=True))
    if logout:
        # Só no logout: o aparelho mantém a inscrição local apenas se ela era deste usuário.
        return jsonify({"revogada": revogada})
    # "Desativar" manual: resposta igual sempre (não revela de quem é o endpoint).
    return Response(status=204)
