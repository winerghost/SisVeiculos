"""Tipos de domínio compartilhados."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Role = Literal["solicitante", "autorizador", "admin", "motorista", "vigilante"]
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
    relatorio_email: bool = False
    relatorio_whatsapp: bool = False
    notificacao_email_criticos: bool = False
    motorista: bool = False
    motorista_registra: bool = False  # exige `motorista`
    motorista_localizacao: bool = False  # exige `motorista_registra`
    vigilante_km: bool = False  # exige `quilometragem`
    autorizador_decide_propria: bool = False  # staff decide a própria requisição
    autorizador_km: bool = False  # exige `quilometragem`; staff lança km

    def como_dict(self) -> dict[str, bool]:
        return {
            "quilometragem": self.quilometragem,
            "abastecimento_manutencao": self.abastecimento_manutencao,
            "checklist": self.checklist,
            "mfa": self.mfa,
            "relatorio_email": self.relatorio_email,
            "relatorio_whatsapp": self.relatorio_whatsapp,
            "notificacao_email_criticos": self.notificacao_email_criticos,
            "motorista": self.motorista,
            "motorista_registra": self.motorista_registra,
            "motorista_localizacao": self.motorista_localizacao,
            "vigilante_km": self.vigilante_km,
            "autorizador_decide_propria": self.autorizador_decide_propria,
            "autorizador_km": self.autorizador_km,
        }
