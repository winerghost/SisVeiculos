"""Perfil do usuário logado e permissões derivadas do role (calculadas no servidor)."""

from __future__ import annotations

from typing import Any

from app.modelos import UNIDADES_ORDEM, Configuracoes, Perfil

_PERMISSOES: dict[str, list[str]] = {
    "solicitante": ["criar_requisicao", "ver_proprias_requisicoes"],
    "autorizador": [
        "criar_requisicao",
        "ver_todas_requisicoes",
        "autorizar",
        "negar",
        "registrar_saida",
        "registrar_chegada",
        "cancelar",
    ],
    "admin": [
        "criar_requisicao",
        "ver_todas_requisicoes",
        "autorizar",
        "negar",
        "registrar_saida",
        "registrar_chegada",
        "cancelar",
        "administrar",
        "ver_acessos",
    ],
}


_ROTULOS_ROLE = {
    "solicitante": "Solicitante",
    "autorizador": "Autorizador",
    "admin": "Administrador",
}


def rotulo_perfil(perfil: Perfil) -> str:
    return f"{perfil.unidade} · {_ROTULOS_ROLE[perfil.role]}"


def montar_eu(perfil: Perfil, config: Configuracoes, foto_url: str | None = None) -> dict[str, Any]:
    return {
        "id": perfil.id,
        "nome": perfil.nome,
        "unidade": perfil.unidade,
        "role": perfil.role,
        "rotulo_perfil": rotulo_perfil(perfil),
        "foto_url": foto_url,  # URL assinada curta da própria foto (ou null)
        "unidades": list(UNIDADES_ORDEM),
        "permissoes": _PERMISSOES[perfil.role],
        # Recursos opcionais habilitados (todos desligados por padrão).
        "recursos": config.como_dict(),
    }
