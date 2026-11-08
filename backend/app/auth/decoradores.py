"""Decorator de autenticação/autorização das rotas protegidas.

Fluxo: Bearer JWT -> verificação (assinatura, exp, aud, iss) -> perfil em `perfis`
(role e ativo vêm do banco) -> checagem de role no servidor.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from flask import g, request

from app.errors import NaoAutenticado, SemPermissao
from app.extensions import deps, ip_cliente
from app.modelos import Perfil

log = logging.getLogger(__name__)
F = TypeVar("F", bound=Callable[..., Any])


def _token() -> str:
    cab = request.headers.get("Authorization", "")
    partes = cab.split(" ", 1)
    if len(partes) != 2 or partes[0].lower() != "bearer" or not partes[1].strip():
        raise NaoAutenticado("Autenticação necessária.")
    return partes[1].strip()


def _tem_segundo_fator(claims: dict[str, Any]) -> bool:
    """aal2 e um método TOTP em `amr` (Supabase: lista de {"method": "totp", ...})."""
    if claims.get("aal") != "aal2":
        return False
    amr = claims.get("amr")
    if not isinstance(amr, list):
        return False
    metodos = {m.get("method") if isinstance(m, dict) else m for m in amr}
    return "totp" in metodos


def tem_segundo_fator(claims: dict[str, Any]) -> bool:
    return _tem_segundo_fator(claims)


def autenticar_token() -> tuple[str, dict[str, Any]]:
    """Valida só o JWT (assinatura, exp, aud, iss). Devolve (id do usuário, claims).

    Não consulta `perfis`: usado apenas pelo aceite de convite, quando o perfil ainda não existe.
    """
    claims = deps().verificador.verificar(_token())
    try:
        usuario_id = str(uuid.UUID(claims["sub"]))
    except ValueError:
        raise NaoAutenticado("Sessão inválida ou expirada. Faça login novamente.") from None
    return usuario_id, claims


def _registrar_acesso(usuario_id: str, claims: dict[str, Any]) -> None:
    """Histórico de logins: best-effort, nunca falha nem atrasa a requisição (o registrador
    grava em segundo plano e engole qualquer erro). Só roda com JWT, perfil e MFA já ok."""
    registrador = deps().registrador_acessos
    if registrador is None:
        return
    try:
        registrador.registrar(
            usuario_id,
            claims,
            ip=ip_cliente(),
            user_agent=request.headers.get("User-Agent"),
        )
    except Exception as e:  # defesa em profundidade: o registrador já não levanta
        log.info("registro_acesso_falhou codigo=%s", type(e).__name__)


def autenticar() -> Perfil:
    d = deps()
    usuario_id, claims = autenticar_token()

    perfil = d.repo.obter_perfil(usuario_id)
    # Mesma resposta para "sem perfil" e "inativo": não revela a existência da conta.
    if perfil is None or not perfil.ativo:
        raise SemPermissao("Conta sem acesso ao sistema.", codigo="conta_inativa")
    # MFA: com o recurso ligado, admin e autorizador precisam de sessão aal2 (TOTP).
    # Solicitante segue só com senha. A verificação é do servidor, sobre claims já validadas.
    exige_mfa = perfil.role in {"admin", "autorizador"} and d.repo.ler_configuracoes().mfa
    if exige_mfa and not _tem_segundo_fator(claims):
        raise SemPermissao(
            "Este perfil exige verificação em duas etapas. Conclua o segundo fator.",
            codigo="mfa_requerido",
        )
    g.perfil = perfil
    g.claims = claims
    _registrar_acesso(perfil.id, claims)
    return perfil


def requer_auth(*roles: str) -> Callable[[F], F]:
    """Exige login e, se informado, que o role do perfil esteja em `roles`."""

    def deco(fn: F) -> F:
        @wraps(fn)
        def interno(*args: Any, **kwargs: Any) -> Any:
            perfil = autenticar()
            if roles and perfil.role not in roles:
                raise SemPermissao("Você não tem permissão para esta ação.")
            return fn(*args, **kwargs)

        return interno  # type: ignore[return-value]

    return deco
