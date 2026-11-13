"""Fuso fixo do projeto (único ponto de definição; módulo folha, sem dependências internas)."""

from __future__ import annotations

from datetime import timedelta, timezone

FUSO_NOME = "America/Sao_Paulo"
# Brasil sem horário de verão desde 2019: deslocamento fixo -03:00. O banco continua agrupando
# por America/Sao_Paulo, equivalente para datas >= 2019.
FUSO = timezone(timedelta(hours=-3))
