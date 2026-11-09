"""Extensões e contêiner de dependências da aplicação."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from app.ia.servico import ServicoIA


@lru_cache(maxsize=8)
def _redes(cidrs: tuple[str, ...]) -> tuple[IPv4Network | IPv6Network, ...]:
    redes: list[IPv4Network | IPv6Network] = []
    for c in cidrs:
        try:
            redes.append(ipaddress.ip_network(c, strict=False))
        except ValueError:
            continue  # CIDR inválido é barrado em validar_producao; aqui apenas não confia
    return tuple(redes)


def _proxy_confiavel(remote_addr: str | None) -> bool:
    if not remote_addr:
        return False
    try:
        ip = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    cidrs = tuple(deps().config.trusted_proxy_cidrs)
    return any(ip.version == r.version and ip in r for r in _redes(cidrs))


def ip_cliente() -> str:
    """IP do cliente. Só confia em CF-Connecting-IP se o par imediato (remote_addr) for um dos
    proxies configurados (`TRUSTED_PROXY_CIDRS`; padrão: sub-rede do docker-compose onde roda o
    nginx do frontend, mais loopback). O nginx valida a origem (set_real_ip_from) e sobrescreve
    o cabeçalho com o IP real; qualquer outro par (ex.: acesso direto à porta do backend) tem o
    cabeçalho ignorado e vale o remote_addr, evitando forjar IP no rate limit e no histórico.
    """
    remoto = request.remote_addr
    if _proxy_confiavel(remoto):
        cabecalho = (request.headers.get("CF-Connecting-IP") or "").strip()
        try:
            return str(ipaddress.ip_address(cabecalho))  # forma canônica; lixo/vazio cai no remoto
        except ValueError:
            pass
    return remoto or "desconhecido"


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
    ia: ServicoIA | None = None  # IA opcional; None = sem IA (sempre cai no texto padrão)


def deps() -> Dependencias:
    return current_app.extensions["sisveiculos"]  # type: ignore[no-any-return]


def registrar_deps(app: Flask, d: Dependencias) -> None:
    app.extensions["sisveiculos"] = d
