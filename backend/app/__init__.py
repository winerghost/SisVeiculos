"""Factory da aplicação Flask (gunicorn: app:create_app())."""

from __future__ import annotations

import atexit
import logging
import os

from flask import Flask
from flask_cors import CORS

from app.auth.jwt import VerificadorJWT
from app.config import Config
from app.errors import registrar_erros
from app.extensions import Dependencias, limiter, registrar_deps
from app.notificacoes import Notificador, criar_notificador
from app.repositorios.armazenamento import Armazenamento
from app.repositorios.auth_admin import AuthAdmin
from app.repositorios.base import Repositorio


def create_app(
    config: Config | None = None,
    *,
    repo: Repositorio | None = None,
    verificador: VerificadorJWT | None = None,
    auth_admin: AuthAdmin | None = None,
    armazenamento: Armazenamento | None = None,
    notificador: Notificador | None = None,
) -> Flask:
    """Dependências (`repo`, `verificador`, `auth_admin`, `armazenamento`) podem ser injetadas
    nos testes; senão vêm da config."""
    cfg = config or Config.from_env()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL") or "INFO")
    # Em INFO estas bibliotecas registram URLs com UUID/e-mail em query string (LGPD).
    for nome in ("httpx", "httpcore", "hpack", "postgrest", "gotrue", "supabase_auth", "werkzeug"):
        logging.getLogger(nome).setLevel(logging.WARNING)

    if repo is None or verificador is None:
        cfg.validar_producao()
    if repo is None:
        from app.repositorios.auth_admin import SupabaseAuthAdmin
        from app.repositorios.supabase_repo import SupabaseRepositorio

        repo = SupabaseRepositorio(cfg.supabase_url, cfg.supabase_service_role_key)
        if armazenamento is None:
            from app.repositorios.armazenamento import SupabaseArmazenamento

            armazenamento = SupabaseArmazenamento(cfg.supabase_url, cfg.supabase_service_role_key)
        if auth_admin is None:  # produção: mesma credencial service_role, só no backend
            auth_admin = SupabaseAuthAdmin(cfg.supabase_url, cfg.supabase_service_role_key)
    if verificador is None:
        verificador = VerificadorJWT(
            issuer=cfg.jwt_issuer, audience=cfg.jwt_audience, jwks_url=cfg.jwks_url
        )

    if notificador is None:  # desligado (no-op) sem N8N_WEBHOOK_URL/N8N_WEBHOOK_TOKEN
        notificador = criar_notificador(cfg.n8n_webhook_url, cfg.n8n_webhook_token)
    atexit.register(notificador.fechar)

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = cfg.max_content_length
    app.config["TESTING"] = cfg.testing
    app.json.ensure_ascii = False  # type: ignore[attr-defined]

    # CORS restrito às origens configuradas (vazio = nenhuma origem cruzada permitida).
    CORS(
        app,
        resources={r"/api/*": {"origins": cfg.cors_origins}},
        allow_headers=["Authorization", "Content-Type"],
        methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],
        max_age=600,
    )

    app.config["RATELIMIT_DEFAULT"] = cfg.limite_padrao
    app.config["RATELIMIT_HEADERS_ENABLED"] = True
    limiter.init_app(app)

    registrar_deps(
        app,
        Dependencias(
            config=cfg,
            repo=repo,
            verificador=verificador,
            auth_admin=auth_admin,
            armazenamento=armazenamento,
            notificador=notificador,
        ),
    )
    registrar_erros(app)

    from app.rotas.admin import admin
    from app.rotas.api import api
    from app.rotas.convites import convites

    app.register_blueprint(api)
    app.register_blueprint(admin)
    app.register_blueprint(convites)

    @app.after_request
    def _cabecalhos(resp):  # type: ignore[no-untyped-def]
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        return resp

    return app
