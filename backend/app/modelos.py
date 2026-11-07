"""Tipos de domínio compartilhados."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Role = Literal["solicitante", "autorizador", "admin"]
Status = Literal["solicitada", "autorizada", "em_uso", "concluida", "negada", "cancelada"]

ESTADOS_ABERTOS: frozenset[str] = frozenset({"solicitada", "autorizada", "em_uso"})
UNIDADES_ORDEM: tuple[str, ...] = ("PU", "PF", "CJU", "UEA")  # fonte única das unidades
UNIDADES: frozenset[str] = frozenset(UNIDADES_ORDEM)

Requisicao = dict[str, Any]


@dataclass(frozen=True)
class Perfil:
    """Perfil lido de `perfis` no servidor. Role e ativo nunca vêm do cliente."""

    id: str
    nome: str
    unidade: str
    role: Role
    ativo: bool


@dataclass(frozen=True)
class Configuracoes:
    """Recursos opcionais. Tudo desligado por padrão (espelha o banco)."""

    quilometragem: bool = False
    abastecimento_manutencao: bool = False
    checklist: bool = False
    mfa: bool = False

    def como_dict(self) -> dict[str, bool]:
        return {
            "quilometragem": self.quilometragem,
            "abastecimento_manutencao": self.abastecimento_manutencao,
            "checklist": self.checklist,
            "mfa": self.mfa,
        }
