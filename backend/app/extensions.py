"""Extensões e contêiner de dependências da aplicação."""

from __future__ import annotations

from dataclasses import dataclass, field

from flask import Flask, current_app, g, request
from flask_limiter import Limiter

from app.auth.jwt import VerificadorJWT
from app.config import Config
from app.notificacoes import Notificador, NotificadorNulo
from app.repositorios.armazenamento import Armazenamento
from app.repositorios.armazenamento_avatares import ArmazenamentoAvatares
from app.repositorios.auth_admin import AuthAdmin
from app.repositorios.base import Repositorio
from app.services.registro_acessos import RegistradorAcessos


def ip_cliente() -> str:
    """IP real: CF-Connecting-IP (Cloudflare) com fallback ao remote_addr.

    O backend só é alcançável via nginx do frontend em 127.0.0.1, atrás do Cloudflare;
    sem esse proxy o cabeçalho seria forjável. O nginx do frontend passará a sobrescrever
    CF-Connecting-IP (valor confiável vindo do Cloudflare, nunca o enviado pelo cliente),
    de modo que o Flask só enxergue o cabeçalho montado pelo proxy confiável. Este código
    não valida a origem do cabeçalho: a garantia é da borda (nginx), e a porta do backend
    nunca deve ser publicada.
    """
    return request.headers.get("CF-Connecting-IP") or request.remote_addr or "desconhecido"


def chave_usuario() -> str:
    """Chave de rate limit por usuário autenticado (id do perfil; sem PII).

    Só vale em rotas sob `requer_auth` (os limites decorados são avaliados dentro da view,
    depois da autenticação). Sem perfil, cai no IP.
    """
    perfil = g.get("perfil")
    return f"usuario:{perfil.id}" if perfil is not None else ip_cliente()


limiter = Limiter(key_func=ip_cliente, storage_uri="memory://")


@dataclass
class Dependencias:
    config: Config
    repo: Repositorio
    verificador: VerificadorJWT
    auth_admin: AuthAdmin | None = None
    armazenamento: Armazenamento | None = None
    armazenamento_avatares: ArmazenamentoAvatares | None = None
    notificador: Notificador = field(default_factory=NotificadorNulo)
    registrador_acessos: RegistradorAcessos | None = None


def deps() -> Dependencias:
    return current_app.extensions["sisveiculos"]  # type: ignore[no-any-return]


def registrar_deps(app: Flask, d: Dependencias) -> None:
    app.extensions["sisveiculos"] = d
