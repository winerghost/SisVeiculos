"""Rotas de notificações. Só traduzem HTTP <-> service; o dono é sempre o perfil autenticado.

Query string nunca é logada (o formato de acesso do gunicorn registra só o caminho).
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify, request

from app.auth.decoradores import requer_auth
from app.extensions import chave_usuario, deps, limiter
from app.services.notificacoes import ServicoNotificacoes

notificacoes = Blueprint("notificacoes", __name__, url_prefix="/api/notificacoes")


def _limite_leitura() -> str:
    return deps().config.limite_notificacoes


def _limite_escrita() -> str:
    return deps().config.limite_notificacoes_escrita


def _servico() -> ServicoNotificacoes:
    return ServicoNotificacoes(deps().repo)


def _resposta(corpo: dict[str, Any], status: int = 200) -> Any:
    resp = jsonify(corpo)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp


@notificacoes.get("")
@requer_auth()
@limiter.limit(_limite_leitura, key_func=chave_usuario)
def listar() -> Any:
    return _resposta(_servico().listar(g.perfil, request.args))


@notificacoes.get("/contagem")
@requer_auth()
@limiter.limit(_limite_leitura, key_func=chave_usuario)
def contagem() -> Any:
    return _resposta(_servico().contagem(g.perfil))


@notificacoes.post("/lidas")
@requer_auth()
@limiter.limit(_limite_escrita, key_func=chave_usuario)
def marcar_todas() -> Any:
    return _resposta(_servico().marcar_todas(g.perfil))


@notificacoes.post("/<notificacao_id>/lida")
@requer_auth()
@limiter.limit(_limite_escrita, key_func=chave_usuario)
def marcar_lida(notificacao_id: str) -> Any:
    return _resposta(_servico().marcar_lida(g.perfil, notificacao_id))
