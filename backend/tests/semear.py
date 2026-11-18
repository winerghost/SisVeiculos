"""Semeia requisições direto no repositório em memória (sem passar pela API), para cenários
de listagem/calendário com vários estados e veículos. Respeita as colunas da tabela."""

from __future__ import annotations

import uuid
from typing import Any

from app.modelos import Perfil
from tests.fakes import RepoMemoria

ANTES = "2026-11-01T12:00:00+00:00"


def semear(repo: RepoMemoria, dono: Perfil, veiculo: str, **campos: Any) -> str:
    rid = str(uuid.uuid4())
    repo.requisicoes[rid] = {
        "id": rid,
        "usuario_id": dono.id,
        "veiculo_id": veiculo,
        "nome_solicitante": "Fulano de Tal",
        "unidade": "PU",
        "hora_saida": "2026-11-10T12:00:00+00:00",
        "trajeto": "AGU -> Fórum",
        "observacao": None,
        "status": "solicitada",
        "autorizado_por": None,
        "autorizado_em": None,
        "saida_real_em": None,
        "chegada_real_em": None,
        "saida_registrada_por": None,
        "chegada_registrada_por": None,
        "cancelada_por": None,
        "cancelada_em": None,
        "km_saida": None,
        "km_chegada": None,
        "km_percorrido": None,
        "motorista_id": None,
        "motorista_alterado_por": None,
        "criado_em": ANTES,
        "atualizado_em": ANTES,
        **campos,
    }
    return rid
