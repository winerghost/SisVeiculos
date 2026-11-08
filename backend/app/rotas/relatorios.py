"""Rotas de relatórios. Só traduzem HTTP <-> services; permissão, filtros e escopo vivem no service.

Query string nunca é logada (o formato de acesso do gunicorn registra só o caminho).
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, g, jsonify, request

from app.auth.decoradores import requer_auth
from app.extensions import chave_usuario, deps, limiter
from app.repositorios.auth_admin import AuthAdmin
from app.services import relatorios as modulo
from app.services.relatorios import ServicoRelatorios

relatorios = Blueprint("relatorios", __name__, url_prefix="/api/relatorios")


def _limite_leitura() -> str:
    return deps().config.limite_relatorios


def _limite_arquivo() -> str:
    return deps().config.limite_relatorios_arquivo


def _limite_email() -> str:
    return deps().config.limite_relatorios_email


def _servico() -> ServicoRelatorios:
    d = deps()
    auth: AuthAdmin | None = d.auth_admin
    # `EXECUTOR_EMAIL` é lido do módulo a cada chamada (os testes o substituem).
    return ServicoRelatorios(d.repo, d.config, auth_admin=auth, executor=modulo.EXECUTOR_EMAIL)


@relatorios.get("")
@requer_auth()
@limiter.limit(_limite_leitura, key_func=chave_usuario)
def catalogo() -> Any:
    return jsonify(_servico().catalogo(g.perfil))


@relatorios.get("/destinatarios")
@requer_auth()
@limiter.limit(_limite_leitura, key_func=chave_usuario)
def destinatarios() -> Any:
    return jsonify(_servico().destinatarios(g.perfil))


@relatorios.get("/envios/<envio_id>")
@requer_auth()
@limiter.limit(_limite_leitura, key_func=chave_usuario)
def consultar_envio(envio_id: str) -> Any:
    return jsonify(_servico().consultar_envio(g.perfil, envio_id))


@relatorios.get("/<tipo>/previa")
@requer_auth()
@limiter.limit(_limite_leitura, key_func=chave_usuario)
def previa(tipo: str) -> Any:
    return jsonify(_servico().previa(g.perfil, tipo, request.args))


@relatorios.get("/<tipo>/arquivo")
@requer_auth()
@limiter.limit(_limite_arquivo, key_func=chave_usuario)
def arquivo(tipo: str) -> Any:
    arq = _servico().arquivo(
        g.perfil,
        tipo,
        request.args,
        formato=request.args.get("formato"),
        via=request.args.get("via"),
    )
    return Response(
        arq.conteudo,
        mimetype=arq.mimetype.split(";")[0],
        headers={
            "Content-Type": arq.mimetype,
            "Content-Disposition": f'attachment; filename="{arq.nome}"',
            "Cache-Control": "no-store",
        },
    )


@relatorios.post("/<tipo>/email")
@requer_auth()
@limiter.limit(_limite_email, key_func=chave_usuario)
def enviar_email(tipo: str) -> Any:
    return jsonify(_servico().enviar_email(g.perfil, tipo, request.get_json(silent=True))), 202
