"""Rotas dos dashboards. Só traduzem HTTP <-> services; permissão e filtros vivem no service."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify, request

from app.auth.decoradores import requer_auth
from app.extensions import chave_usuario, deps, limiter
from app.services.dashboards import ServicoDashboards
from app.services.fotos_exibicao import FotosExibicao
from app.services.requisicoes import ServicoRequisicoes
from app.services.resumo_ia import LIMITE_RESUMO, ServicoResumoIA

dashboards = Blueprint("dashboards", __name__, url_prefix="/api/dashboards")


def _limite_dashboards() -> str:
    return deps().config.limite_dashboards


def _servico() -> ServicoDashboards:
    d = deps()
    requisicoes = ServicoRequisicoes(
        d.repo,
        notificador=d.notificador,
        fotos=FotosExibicao(d.repo, d.armazenamento_avatares),
    )
    return ServicoDashboards(d.repo, requisicoes)


def _resumo() -> ServicoResumoIA:
    return ServicoResumoIA(deps().ia)


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
    resposta = _servico().obter(g.perfil, painel, request.args)
    resposta["resumo_ia"] = _resumo().disponivel()  # o front só mostra o botão se for true
    return jsonify(resposta)


@dashboards.post("/<painel>/resumo")
@requer_auth("autorizador", "admin")
@limiter.limit(LIMITE_RESUMO, key_func=chave_usuario)
def resumir_painel(painel: str) -> Any:
    """Resumo narrativo (IA) dos MESMOS agregados, permissão e filtros do GET do painel."""
    agregados = _servico().agregados(g.perfil, painel, request.args)
    return jsonify(_resumo().resumir_painel(g.perfil, agregados))
