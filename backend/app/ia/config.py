"""Configuração de IA lida do ambiente (nunca de arquivo versionado)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

PROVEDORES: tuple[str, ...] = ("openai", "anthropic")
ROTULOS_PROVEDORES: dict[str, str] = {"openai": "OpenAI", "anthropic": "Anthropic"}

# Lista permitida de modelos por provedor (a tela só oferece estes). Pode ser substituída no
# ambiente com AI_MODELOS_OPENAI / AI_MODELOS_ANTHROPIC (lista separada por vírgulas), sem
# precisar de novo build de código. O primeiro da lista é o padrão.
MODELOS_PADRAO: dict[str, tuple[str, ...]] = {
    "openai": ("gpt-5.4-mini",),
    "anthropic": ("claude-haiku-5-5", "claude-sonnet-5-5"),
}

_MODELO_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


def _bool(valor: str | None) -> bool:
    return (valor or "").strip().lower() in {"1", "true", "sim", "yes"}


def _inteiro(valor: str | None, padrao: int, minimo: int, maximo: int) -> int:
    try:
        n = int((valor or "").strip())
    except ValueError:
        return padrao
    return n if minimo <= n <= maximo else padrao


def _decimal(valor: str | None, padrao: float, minimo: float, maximo: float) -> float:
    try:
        n = float((valor or "").strip())
    except ValueError:
        return padrao
    return n if minimo <= n <= maximo else padrao


def _modelos(valor: str | None, padrao: tuple[str, ...]) -> tuple[str, ...]:
    itens = tuple(m.strip() for m in (valor or "").split(",") if m.strip())
    if not itens or not all(_MODELO_RE.match(m) for m in itens):
        return padrao
    return itens


@dataclass(frozen=True)
class ConfigIA:
    """Valores de execução. Os campos de chave têm `repr=False` e nada aqui é logado."""

    habilitado: bool = False  # AI_ENABLED; desligado vence qualquer configuração da tela
    openai_api_key: str = field(default="", repr=False)
    anthropic_api_key: str = field(default="", repr=False)
    timeout_s: float = 20.0
    max_tokens: int = 800
    limite_por_usuario_hora: int = 20
    limite_por_unidade_hora: int = 100
    teto_tokens_mes: int = 200_000
    circuito_falhas: int = 3
    circuito_abertura_s: int = 300
    modelos: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(MODELOS_PADRAO))

    def chave(self, provedor: str) -> str:
        return {"openai": self.openai_api_key, "anthropic": self.anthropic_api_key}.get(
            provedor, ""
        )

    def tem_chave(self, provedor: str) -> bool:
        return bool(self.chave(provedor))

    def modelos_de(self, provedor: str) -> tuple[str, ...]:
        return self.modelos.get(provedor, ())

    @classmethod
    def from_env(cls) -> ConfigIA:
        env = os.environ
        return cls(
            habilitado=_bool(env.get("AI_ENABLED")),
            openai_api_key=env.get("OPENAI_API_KEY", "").strip(),
            anthropic_api_key=env.get("ANTHROPIC_API_KEY", "").strip(),
            timeout_s=_decimal(env.get("AI_TIMEOUT_SECONDS"), 20.0, 1.0, 120.0),
            max_tokens=_inteiro(env.get("AI_MAX_TOKENS"), 800, 16, 8000),
            limite_por_usuario_hora=_inteiro(env.get("AI_RATE_LIMIT_PER_USER"), 20, 1, 100_000),
            limite_por_unidade_hora=_inteiro(env.get("AI_RATE_LIMIT_PER_UNIT"), 100, 1, 1_000_000),
            teto_tokens_mes=_inteiro(env.get("AI_MONTHLY_TOKEN_CAP"), 200_000, 1, 2_000_000_000),
            circuito_falhas=_inteiro(env.get("AI_CIRCUIT_FAILURE_THRESHOLD"), 3, 1, 100),
            circuito_abertura_s=_inteiro(env.get("AI_CIRCUIT_OPEN_SECONDS"), 300, 1, 86_400),
            modelos={
                "openai": _modelos(env.get("AI_MODELOS_OPENAI"), MODELOS_PADRAO["openai"]),
                "anthropic": _modelos(env.get("AI_MODELOS_ANTHROPIC"), MODELOS_PADRAO["anthropic"]),
            },
        )
