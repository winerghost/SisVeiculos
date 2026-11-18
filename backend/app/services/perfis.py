"""Perfil do usuário logado e permissões derivadas do role (calculadas no servidor)."""

from __future__ import annotations

from typing import Any

from app.modelos import UNIDADES_ORDEM, Configuracoes, Perfil

_PERMISSOES: dict[str, list[str]] = {
    "solicitante": ["criar_requisicao", "ver_proprias_requisicoes", "ver_calendario"],
    "autorizador": [
        "criar_requisicao",
        "ver_todas_requisicoes",
        "autorizar",
        "negar",
        "registrar_saida",
        "registrar_chegada",
        "cancelar",
        "ver_calendario",
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
        "ver_calendario",
    ],
    "motorista": ["ver_viagens_atribuidas"],
    "vigilante": [
        "criar_requisicao",
        "ver_proprias_requisicoes",
        "ver_requisicoes_para_registro",
        "registrar_saida",
        "registrar_chegada",
        "ver_calendario",
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


def permissoes(perfil: Perfil, config: Configuracoes) -> list[str]:
    """Permissões efetivas (role do banco + recursos ligados). Fonte de `eu.permissoes` e das
    checagens de acesso que dependem delas (ex.: `ver_calendario`)."""
    lista = list(_PERMISSOES[perfil.role])
    if perfil.role == "motorista" and config.motorista:
        # Calendário: mesma condição da listagem de viagens (recurso Motorista ligado).
        lista.append("ver_calendario")
        if config.motorista_registra:
            lista += ["registrar_saida", "registrar_chegada"]
    return lista


def montar_eu(perfil: Perfil, config: Configuracoes, foto_url: str | None = None) -> dict[str, Any]:
    return {
        "id": perfil.id,
        "nome": perfil.nome,
        "unidade": perfil.unidade,
        "role": perfil.role,
        "rotulo_perfil": rotulo_perfil(perfil),
        "foto_url": foto_url,  # URL assinada curta da própria foto (ou null)
        "unidades": list(UNIDADES_ORDEM),
        "permissoes": permissoes(perfil, config),
        # Recursos opcionais habilitados (todos desligados por padrão).
        "recursos": config.como_dict(),
    }
