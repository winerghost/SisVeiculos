"""Localização do motorista (recurso opcional `motorista_localizacao`).

LGPD: coordenadas são dado pessoal sensível (deslocamento do servidor). Aqui elas:
  * só são lidas do corpo quando o registro é do role `motorista` e o recurso está ligado;
  * nunca aparecem em mensagens de erro, logs, auditoria, notificações, relatórios ou IA
    (todas as mensagens abaixo são fixas);
  * só são devolvidas ao `admin` (`services/localizacoes_admin.py`), cada leitura auditada.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.errors import RequisicaoInvalida

PRECISAO_MAX_M = 999_999  # numeric(8,2) da coluna precisao_m
MSG_OBRIGATORIA = (
    "Informe a localização (latitude e longitude) para registrar a saída ou a chegada."
)
MSG_LATITUDE = "Latitude inválida: informe um número entre -90 e 90."
MSG_LONGITUDE = "Longitude inválida: informe um número entre -180 e 180."
MSG_PRECISAO = "Precisão da localização inválida: informe um número maior ou igual a zero."


@dataclass(frozen=True)
class Coordenadas:
    latitude: float
    longitude: float
    precisao_m: float | None


def _numero(valor: Any, minimo: float, maximo: float, mensagem: str) -> float:
    """Número finito dentro da faixa. Recusa bool, texto, NaN e infinito (o JSON do Python aceita
    NaN/Infinity). A mensagem é fixa: o valor recebido nunca é ecoado."""
    if isinstance(valor, bool) or not isinstance(valor, int | float):
        raise RequisicaoInvalida(mensagem, "localizacao_invalida")
    # Comparar antes de converter: ints gigantes não estouram e NaN/inf falham na faixa.
    if not minimo <= valor <= maximo:
        raise RequisicaoInvalida(mensagem, "localizacao_invalida")
    resultado = float(valor)
    if not math.isfinite(resultado):  # defesa extra
        raise RequisicaoInvalida(mensagem, "localizacao_invalida")
    return resultado


def validar_localizacao(corpo: Any) -> Coordenadas:
    """Latitude e longitude obrigatórias; `precisao_m` opcional. O corpo vem do motorista."""
    if (
        not isinstance(corpo, dict)
        or corpo.get("latitude") is None
        or corpo.get("longitude") is None
    ):
        raise RequisicaoInvalida(MSG_OBRIGATORIA, "localizacao_obrigatoria")
    lat = _numero(corpo["latitude"], -90, 90, MSG_LATITUDE)
    lon = _numero(corpo["longitude"], -180, 180, MSG_LONGITUDE)
    precisao: float | None = None
    if corpo.get("precisao_m") is not None:
        precisao = round(_numero(corpo["precisao_m"], 0, PRECISAO_MAX_M, MSG_PRECISAO), 2)
    # numeric(9,6): ~0,1 m. Arredondar aqui evita dígitos que o banco descartaria de qualquer modo.
    return Coordenadas(round(lat, 6), round(lon, 6), precisao)
