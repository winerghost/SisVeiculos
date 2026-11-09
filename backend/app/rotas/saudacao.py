"""Rotas da saudação sazonal. Só traduzem HTTP <-> `ServicoSaudacao`.

`GET /api/saudacao`: qualquer perfil ativo autenticado (exige JWT, portanto não aparece no login).
`/api/admin/ia/saudacoes*`: só admin (role de `perfis`, checada no `before_request`).
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, g, jsonify, request

from app.auth.decoradores import autenticar, requer_auth
from app.errors import SemPermissao
from app.extensions import chave_usuario, deps, limiter
from app.saudacao.servico import ServicoSaudacao

saudacao = Blueprint("saudacao", __name__, url_prefix="/api/saudacao")
saudacao_admin = Blueprint("saudacao_admin", __name__, url_prefix="/api/admin/ia/saudacoes")

LIMITE_LEITURA = "30 per minute"  # por usuário; a UI pede 1 vez por carregamento
LIMITE_GERAR = "5 per hour"  # conservador: cada chamada gasta cota e tokens do provedor


@saudacao_admin.before_request
def _exigir_admin() -> None:
    if request.method == "OPTIONS":  # preflight do CORS não leva Authorization
        return
    if autenticar().role != "admin":
        raise SemPermissao("Você não tem permissão para esta ação.")


def _limite_escrita() -> str:
    return deps().config.limite_escrita


def _servico() -> ServicoSaudacao:
    return ServicoSaudacao(deps().ia, current_app.extensions.get("saudacoes_repo"))


@saudacao.get("")
@requer_auth()
@limiter.limit(LIMITE_LEITURA, key_func=chave_usuario)
def obter_saudacao() -> Any:
    return jsonify({"texto": _servico().para_usuario(g.perfil)})


@saudacao_admin.get("")
def listar() -> Any:
    return jsonify(
        _servico().listar(g.perfil, request.args.get("status"), request.args.get("ocasiao"))
    )


@saudacao_admin.post("")
@limiter.limit(_limite_escrita)
def criar() -> Any:
    return jsonify(_servico().criar_manual(g.perfil, request.get_json(silent=True))), 201


@saudacao_admin.post("/gerar")
@limiter.limit(LIMITE_GERAR, key_func=chave_usuario)
def gerar() -> Any:
    return jsonify(_servico().gerar(g.perfil, request.get_json(silent=True))), 201


@saudacao_admin.post("/<saudacao_id>/aprovar")
@limiter.limit(_limite_escrita)
def aprovar(saudacao_id: str) -> Any:
    return jsonify(_servico().decidir(g.perfil, saudacao_id, aprovar=True))


@saudacao_admin.post("/<saudacao_id>/rejeitar")
@limiter.limit(_limite_escrita)
def rejeitar(saudacao_id: str) -> Any:
    return jsonify(_servico().decidir(g.perfil, saudacao_id, aprovar=False))
