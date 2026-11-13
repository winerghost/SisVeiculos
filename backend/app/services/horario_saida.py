"""Regras de tempo da `hora_saida` (saída prevista): não-passado, sugestão e situação (cor).

Tudo é decidido aqui, com o relógio do servidor. O frontend só exibe o que a API devolve.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.errors import RequisicaoInvalida
from app.fuso import FUSO, FUSO_NOME

# Quem escolhe "agora" envia um minuto cheio e ainda preenche o formulário: aceita até 10 min
# para trás. Não é antecedência mínima (saída imediata é válida).
TOLERANCIA_PASSADO = timedelta(minutes=10)
# A partir de 10 min antes da saída prevista a requisição aberta fica em alerta (amarelo).
JANELA_ALERTA = timedelta(minutes=10)
PASSO_SUGESTAO_MIN = 5
STATUS_COM_SITUACAO = frozenset({"solicitada", "autorizada"})

NO_PRAZO, PROXIMO, ATRASADO = "no_prazo", "proximo", "atrasado"


def _rotulo(dt: datetime) -> str:
    local = dt.astimezone(FUSO)
    return f"{local:%d/%m/%Y} às {local:%H:%M}"


def validar_hora_saida_nao_passada(hora_saida: str, agora: datetime) -> None:
    """Recusa `hora_saida` (ISO) anterior a `agora - TOLERANCIA_PASSADO`."""
    limite = agora - TOLERANCIA_PASSADO
    if datetime.fromisoformat(hora_saida) < limite:
        raise RequisicaoInvalida(
            "A hora de saída não pode ser anterior ao horário atual. "
            f"Agora são {_rotulo(agora)} (horário de Brasília). "
            f"Escolha um horário a partir das {limite.astimezone(FUSO):%H:%M}.",
            codigo="hora_saida_passada",
        )


def sugestao(agora: datetime) -> dict[str, Any]:
    """Agora (Brasília) arredondado para cima ao próximo múltiplo de 5 min."""
    local = agora.astimezone(FUSO)
    base = local.replace(second=0, microsecond=0)
    falta = (-base.minute) % PASSO_SUGESTAO_MIN
    if falta == 0 and local != base:
        falta = PASSO_SUGESTAO_MIN
    alvo = base + timedelta(minutes=falta)
    return {"data": f"{alvo:%Y-%m-%d}", "hora": alvo.hour, "minuto": alvo.minute}


def horario_referencia(agora: datetime) -> dict[str, Any]:
    return {
        "fuso": FUSO_NOME,
        "agora": agora.astimezone(FUSO).isoformat(timespec="seconds"),
        "sugestao": sugestao(agora),
    }


def situacao_do_horario(status: str, hora_saida: str | None, agora: datetime) -> str | None:
    """no_prazo | proximo | atrasado para requisição aberta antes da saída; null nos demais."""
    if status not in STATUS_COM_SITUACAO or not hora_saida:
        return None
    prevista = datetime.fromisoformat(hora_saida).astimezone(UTC)
    if agora > prevista:
        return ATRASADO
    if agora >= prevista - JANELA_ALERTA:
        return PROXIMO
    return NO_PRAZO
