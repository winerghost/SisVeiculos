"""Horários relativos ao agora para os testes (a regra de 'não passado' usa o relógio real)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.fuso import FUSO


def hora_saida_futura(dias: int = 1) -> str:
    """ISO 8601 com fuso -03:00, `dias` à frente do agora real (sempre válido na criação)."""
    return (datetime.now(UTC) + timedelta(days=dias)).astimezone(FUSO).isoformat(timespec="seconds")
