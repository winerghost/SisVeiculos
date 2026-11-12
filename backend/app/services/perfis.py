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
    "motorista": ["ver_viagens_atribuidas"],
    "vigilante": [
        "criar_requisicao",
        "ver_proprias_requisicoes",
        "ver_requisicoes_para_registro",
        "registrar_saida",
        "registrar_chegada",
    ],
}


_ROTULOS_ROLE = {
    "solicitante": "Solicitante",
    "autorizador": "Autorizador",
    "admin": "Administrador",
    "motorista": "Motorista",
    "vigilante": "Vigilante",
}


def rotulo_perfil(perfil: Perfil) -> str:
    return f"{perfil.unidade} · {_ROTULOS_ROLE[perfil.role]}"


def _permissoes(perfil: Perfil, config: Configuracoes) -> list[str]:
    permissoes = list(_PERMISSOES[perfil.role])
    if perfil.role == "motorista" and config.motorista and config.motorista_registra:
        permissoes += ["registrar_saida", "registrar_chegada"]
    return permissoes


def montar_eu(perfil: Perfil, config: Configuracoes, foto_url: str | None = None) -> dict[str, Any]:
    return {
        "id": perfil.id,
        "nome": perfil.nome,
        "unidade": perfil.unidade,
        "role": perfil.role,
        "rotulo_perfil": rotulo_perfil(perfil),
        "foto_url": foto_url,  # URL assinada curta da própria foto (ou null)
        "unidades": list(UNIDADES_ORDEM),
        "permissoes": _permissoes(perfil, config),
        # Recursos opcionais habilitados (todos desligados por padrão).
        "recursos": config.como_dict(),
    }
