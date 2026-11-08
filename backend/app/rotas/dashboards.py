"""Rotas dos dashboards. Só traduzem HTTP <-> services; permissão e filtros vivem no service."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify, request

from app.auth.decoradores import requer_auth
from app.extensions import chave_usuario, deps, limiter
from app.services.dashboards import ServicoDashboards
from app.services.fotos_exibicao import FotosExibicao
from app.services.requisicoes import ServicoRequisicoes

dashboards = Blueprint("dashboards", __name__, url_prefix="/api/dashboards")


def _limite_dashboards() -> str:
    return deps().config.limite_dashboards


def _servico() -> ServicoDashboards:
    d = deps()
    requisicoes = ServicoRequisicoes(
        d.repo,
        permitir_autoautorizacao=d.config.permitir_autoautorizacao,
        notificador=d.notificador,
        fotos=FotosExibicao(d.repo, d.armazenamento_avatares),
    )
    return ServicoDashboards(d.repo, requisicoes)


# Solicitante recebe 403 do decorator; sem token, 401. Query string: nada é logado.
@dashboards.get("")
@requer_auth("autorizador", "admin")
@limiter.limit(_limite_dashboards, key_func=chave_usuario)
def listar_paineis() -> Any:
    return jsonify(_servico().paineis_permitidos(g.perfil))


@dashboards.get("/<painel>")
@requer_auth("autorizador", "admin")
@limiter.limit(_limite_dashboards, key_func=chave_usuario)
def obter_painel(painel: str) -> Any:
    return jsonify(_servico().obter(g.perfil, painel, request.args))
