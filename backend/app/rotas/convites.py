"""Aceite de convite: a única rota autenticada que não exige perfil prévio.

O convidado já tem conta no Supabase Auth (clicou no e-mail e definiu a senha) mas ainda não
tem linha em `perfis`. Role e unidade vêm do convite no banco; o corpo não aceita nenhum.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from app.auth.decoradores import autenticar_token
from app.errors import ErroApi
from app.extensions import deps, limiter
from app.services.convites import ServicoConvites

convites = Blueprint("convites", __name__, url_prefix="/api/convites")


@convites.post("/aceitar")
@limiter.limit("10 per minute")
def aceitar() -> Any:
    usuario_id, _claims = autenticar_token()
    d = deps()
    if d.auth_admin is None:
        raise ErroApi("Cadastro por convite indisponível.", "indisponivel", 503)
    svc = ServicoConvites(d.repo, d.auth_admin, url_aceite=d.config.url_aceite_convite)
    return jsonify(
        svc.aceitar(usuario_id, request.get_json(silent=True), d.repo.ler_configuracoes())
    )
