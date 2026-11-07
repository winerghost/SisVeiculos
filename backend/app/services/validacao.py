"""Validação do corpo da criação de requisição (a API é a única fonte das regras)."""

from __future__ import annotations

import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Any

from app.errors import RequisicaoInvalida
from app.modelos import UNIDADES

CAMPOS_CRIACAO = {
    "nome_solicitante",
    "unidade",
    "hora_saida",
    "trajeto",
    "observacao",
    "veiculo_id",
}
MAX_NOME, MAX_TRAJETO, MAX_OBS = 120, 500, 500
ANO_MIN, ANO_MAX = 2000, 2100  # recusa datas absurdas em hora_saida


def limpar_texto(valor: str, *, permitir_quebra: bool = False) -> str:
    """Normaliza em NFC e remove caracteres de controle/formatação (mantém a quebra de linha)."""
    quebra = chr(10)
    valor = unicodedata.normalize("NFC", valor)
    if permitir_quebra:
        valor = valor.replace(chr(13) + quebra, quebra).replace(chr(13), quebra)
    return "".join(
        c
        for c in valor
        if not unicodedata.category(c).startswith("C") or (permitir_quebra and c == quebra)
    )


def _texto(
    corpo: dict[str, Any],
    campo: str,
    maximo: int,
    obrigatorio: bool,
    *,
    permitir_quebra: bool = False,
) -> str | None:
    valor = corpo.get(campo)
    if valor is not None and not isinstance(valor, str):
        raise RequisicaoInvalida(f"O campo '{campo}' deve ser texto.")
    if valor is not None:
        valor = limpar_texto(valor, permitir_quebra=permitir_quebra).strip()
    if not valor:
        if obrigatorio:
            raise RequisicaoInvalida(f"O campo '{campo}' é obrigatório.")
        return None
    if len(valor) > maximo:
        raise RequisicaoInvalida(f"O campo '{campo}' aceita no máximo {maximo} caracteres.")
    return valor


def validar_texto(corpo: dict[str, Any], campo: str, maximo: int, obrigatorio: bool) -> str | None:
    """Texto limpo (NFC, sem controle), aparado e com tamanho máximo."""
    return _texto(corpo, campo, maximo, obrigatorio)


def validar_uuid(valor: Any, campo: str = "id") -> str:
    try:
        return str(uuid.UUID(str(valor)))
    except (ValueError, AttributeError, TypeError):
        raise RequisicaoInvalida(f"O campo '{campo}' deve ser um identificador válido.") from None


def _data_hora(valor: Any) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise RequisicaoInvalida("O campo 'hora_saida' é obrigatório.")
    try:
        dt = datetime.fromisoformat(valor.strip())
    except (ValueError, OverflowError):
        raise RequisicaoInvalida(
            "O campo 'hora_saida' deve ser data e hora ISO 8601 com fuso "
            "(ex.: 2026-11-10T08:30:00-03:00)."
        ) from None
    if dt.tzinfo is None:
        raise RequisicaoInvalida("O campo 'hora_saida' deve informar o fuso horário.")
    try:
        em_utc = dt.astimezone(UTC)
    except (ValueError, OverflowError):
        raise RequisicaoInvalida("O campo 'hora_saida' tem uma data fora do intervalo.") from None
    if not ANO_MIN <= em_utc.year <= ANO_MAX:
        raise RequisicaoInvalida(
            f"O campo 'hora_saida' deve estar entre os anos {ANO_MIN} e {ANO_MAX}."
        )
    return em_utc.isoformat()


def validar_criacao(corpo: Any) -> dict[str, Any]:
    if not isinstance(corpo, dict):
        raise RequisicaoInvalida("Envie um objeto JSON.")
    extras = sorted(set(corpo) - CAMPOS_CRIACAO)
    if extras:
        raise RequisicaoInvalida("Campos não permitidos: " + ", ".join(extras) + ".")
    unidade = corpo.get("unidade")
    if unidade not in UNIDADES:
        raise RequisicaoInvalida("O campo 'unidade' deve ser PU, PF, CJU ou UEA.")
    return {
        "nome_solicitante": _texto(corpo, "nome_solicitante", MAX_NOME, True),
        "unidade": unidade,
        "hora_saida": _data_hora(corpo.get("hora_saida")),
        "trajeto": _texto(corpo, "trajeto", MAX_TRAJETO, True),
        "observacao": _texto(corpo, "observacao", MAX_OBS, False, permitir_quebra=True),
        "veiculo_id": validar_uuid(corpo.get("veiculo_id"), "veiculo_id"),
    }


KM_MAX = 2_147_483_647  # limite do integer do Postgres


def validar_km(valor: Any, campo: str) -> int:
    """Quilometragem: inteiro JSON >= 0 e dentro do integer do Postgres (bool não vale)."""
    if valor is None:
        raise RequisicaoInvalida(f"O campo '{campo}' é obrigatório.")
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise RequisicaoInvalida(f"O campo '{campo}' deve ser um número inteiro.")
    if valor < 0:
        raise RequisicaoInvalida(f"O campo '{campo}' não pode ser negativo.")
    if valor > KM_MAX:
        raise RequisicaoInvalida(f"O campo '{campo}' aceita no máximo {KM_MAX}.")
    return valor
