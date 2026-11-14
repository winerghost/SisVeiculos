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
from app.ia.config import ConfigIA
from app.ia.repositorio import SupabaseRepositorioIA
from app.ia.servico import ServicoIA
from app.notificacoes import Notificador, criar_notificador
from app.repositorios.armazenamento import Armazenamento
from app.repositorios.armazenamento_avatares import ArmazenamentoAvatares
from app.repositorios.auth_admin import AuthAdmin
from app.repositorios.base import Repositorio
from app.saudacao.repositorio import RepositorioSaudacoes, SupabaseRepositorioSaudacoes
from app.services.notificacoes import registrar_falha_sistema
from app.services.registro_acessos import RegistradorAcessos


def create_app(
    config: Config | None = None,
    *,
    repo: Repositorio | None = None,
    verificador: VerificadorJWT | None = None,
    auth_admin: AuthAdmin | None = None,
    armazenamento: Armazenamento | None = None,
    armazenamento_avatares: ArmazenamentoAvatares | None = None,
    notificador: Notificador | None = None,
    registrador_acessos: RegistradorAcessos | None = None,
    servico_ia: ServicoIA | None = None,
    repo_saudacoes: RepositorioSaudacoes | None = None,
) -> Flask:
    """Dependências (`repo`, `verificador`, `auth_admin`, `armazenamento`) podem ser injetadas
    nos testes; senão vêm da config."""
    cfg = config or Config.from_env()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL") or "INFO")
    # Em INFO estas bibliotecas registram URLs com UUID/e-mail em query string (LGPD).
    for nome in (
        "httpx",
        "httpcore",
        "hpack",
        "postgrest",
        "gotrue",
        "supabase_auth",
        "werkzeug",
        "PIL",
    ):
        logging.getLogger(nome).setLevel(logging.WARNING)

    if repo is None or verificador is None:
        cfg.validar_producao()
    producao = repo is None
    if repo is None:
        from app.repositorios.auth_admin import SupabaseAuthAdmin
        from app.repositorios.supabase_repo import SupabaseRepositorio

        repo = SupabaseRepositorio(cfg.supabase_url, cfg.supabase_service_role_key)
        if armazenamento is None:
            from app.repositorios.armazenamento import SupabaseArmazenamento

            armazenamento = SupabaseArmazenamento(cfg.supabase_url, cfg.supabase_service_role_key)
        if armazenamento_avatares is None:
            from app.repositorios.armazenamento_avatares import SupabaseArmazenamentoAvatares

            armazenamento_avatares = SupabaseArmazenamentoAvatares(
                cfg.supabase_url, cfg.supabase_service_role_key
            )
        if auth_admin is None:  # produção: mesma credencial service_role, só no backend
            auth_admin = SupabaseAuthAdmin(cfg.supabase_url, cfg.supabase_service_role_key)
    if verificador is None:
        verificador = VerificadorJWT(
            issuer=cfg.jwt_issuer, audience=cfg.jwt_audience, jwks_url=cfg.jwks_url
        )

    if notificador is None:  # desligado (no-op) sem N8N_WEBHOOK_URL/N8N_WEBHOOK_TOKEN
        repo_falha = repo  # o repositório é thread-safe e já é usado fora da requisição

        def _n8n_desistiu(_evento: str) -> None:  # só o nome do evento; nunca propaga
            registrar_falha_sistema(repo_falha, "n8n_falha")

        notificador = criar_notificador(
            cfg.n8n_webhook_url,
            cfg.n8n_webhook_token,
            ao_desistir=_n8n_desistiu,
            hmac_secret=cfg.n8n_hmac_secret,
            hosts_permitidos=cfg.n8n_allowed_hosts,
        )
    atexit.register(notificador.fechar)

    if registrador_acessos is None:  # nos testes (testing=True) grava de forma síncrona
        registrador_acessos = RegistradorAcessos(repo, assincrono=not cfg.testing)
    atexit.register(registrador_acessos.fechar)

    if servico_ia is None:
        # Sem AI_ENABLED=true o serviço nunca chama provedor. O repositório de IA só existe em
        # produção; nos testes (repo injetado) o chamador injeta `servico_ia` se precisar.
        repo_ia = (
            SupabaseRepositorioIA.criar(cfg.supabase_url, cfg.supabase_service_role_key)
            if producao
            else None
        )
        servico_ia = ServicoIA(ConfigIA.from_env(), repo_ia)
    if repo_saudacoes is None and producao:
        repo_saudacoes = SupabaseRepositorioSaudacoes.criar(
            cfg.supabase_url, cfg.supabase_service_role_key
        )

    app = Flask(__name__)
    app.extensions["saudacoes_repo"] = repo_saudacoes
    app.config["MAX_CONTENT_LENGTH"] = cfg.max_content_length
    app.config["TESTING"] = cfg.testing
    app.json.ensure_ascii = False  # type: ignore[attr-defined]

    # CORS restrito às origens configuradas (vazio = nenhuma origem cruzada permitida).
    CORS(
        app,
        resources={r"/api/*": {"origins": cfg.cors_origins}},
        allow_headers=["Authorization", "Content-Type"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
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
            armazenamento_avatares=armazenamento_avatares,
            notificador=notificador,
            registrador_acessos=registrador_acessos,
            ia=servico_ia,
        ),
    )
    registrar_erros(app)

    from app.rotas.admin import admin
    from app.rotas.api import api
    from app.rotas.convites import convites
    from app.rotas.dashboards import dashboards
    from app.rotas.notificacoes import notificacoes
    from app.rotas.passkeys import passkeys
    from app.rotas.push import push
    from app.rotas.relatorios import relatorios
    from app.rotas.saudacao import saudacao, saudacao_admin

    app.register_blueprint(api)
    app.register_blueprint(admin)
    app.register_blueprint(convites)
    app.register_blueprint(dashboards)
    app.register_blueprint(relatorios)
    app.register_blueprint(notificacoes)
    app.register_blueprint(push)
    app.register_blueprint(passkeys)
    app.register_blueprint(saudacao)
    app.register_blueprint(saudacao_admin)

    if not cfg.testing:
        # Thread de manutenção (expurgo diário) e, com EMAIL_ENABLED, despachante de e-mail dos
        # críticos; daemon, estado em memória (1 worker).
        from app.notificacoes.despachante import Despachante

        despachante = Despachante(repo, auth_admin, cfg)
        despachante.iniciar()
        atexit.register(despachante.parar)

    @app.after_request
    def _cabecalhos(resp):  # type: ignore[no-untyped-def]
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        return resp

    return app
