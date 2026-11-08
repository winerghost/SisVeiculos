"""Consulta do histórico de acessos (somente admin) e último acesso por usuário.

Regra aqui, não na rota: validação dos filtros, auditoria da consulta e limites. Os dados já
chegam minimizados do banco (IP mascarado, navegador reduzido, `sessao_ref`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.errors import RequisicaoInvalida
from app.modelos import Perfil
from app.repositorios.base import Repositorio
from app.services.admin_usuarios import exigir_admin
from app.services.validacao import validar_uuid

PARAMETROS = frozenset({"usuario_id", "de", "ate", "limite", "offset"})
LIMITE_PADRAO = 50
LIMITE_MAX = 100
OFFSET_MAX = 100_000
INTERVALO_MAX = timedelta(days=400)  # um pouco acima da retenção de 12 meses

# A tabela `auditoria` só aceita tabelas de uma lista fechada (CHECK); 'acessos_login' ainda
# não consta nela, então a consulta é registrada em 'perfis' (sem registro_id).
TABELA_AUDITORIA = "perfis"
DETALHES_AUDITORIA = {"recurso": "acessos"}  # sem filtros pessoais nem resultados

_FUSO_COM_ESPACO = re.compile(r" (\d{2}:?\d{2})$")  # '+' sem escape na query vira espaço


def _unico(args: Mapping[str, Sequence[str]], nome: str) -> str | None:
    valores = args.get(nome)
    if not valores:
        return None
    if len(valores) > 1:
        raise RequisicaoInvalida(f"O parâmetro '{nome}' foi informado mais de uma vez.")
    return valores[0]


def _inteiro(
    args: Mapping[str, Sequence[str]], nome: str, padrao: int, minimo: int, maximo: int
) -> int:
    bruto = _unico(args, nome)
    if bruto is None:
        return padrao
    try:
        valor = int(bruto.strip())
    except ValueError:
        raise RequisicaoInvalida(f"O parâmetro '{nome}' deve ser um número inteiro.") from None
    if not minimo <= valor <= maximo:
        raise RequisicaoInvalida(f"O parâmetro '{nome}' deve estar entre {minimo} e {maximo}.")
    return valor


def _instante(args: Mapping[str, Sequence[str]], nome: str) -> datetime | None:
    bruto = _unico(args, nome)
    if bruto is None:
        return None
    texto = _FUSO_COM_ESPACO.sub(r"+\1", bruto.strip())
    msg = (
        f"O parâmetro '{nome}' deve ser data e hora ISO 8601 com fuso "
        "(ex.: 2026-01-31T00:00:00-03:00)."
    )
    if len(texto) > 40:
        raise RequisicaoInvalida(msg)
    try:
        dt = datetime.fromisoformat(texto)
    except ValueError:
        raise RequisicaoInvalida(msg) from None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise RequisicaoInvalida(msg)
    try:
        return dt.astimezone(UTC)
    except (OverflowError, ValueError):
        raise RequisicaoInvalida(msg) from None


class ServicoAcessos:
    def __init__(self, repo: Repositorio) -> None:
        self._repo = repo

    def listar(self, admin: Perfil, args: Mapping[str, Sequence[str]]) -> dict[str, Any]:
        exigir_admin(admin)
        desconhecidos = set(args) - PARAMETROS
        if desconhecidos:
            raise RequisicaoInvalida("Parâmetro de consulta não reconhecido.")
        bruto_usuario = _unico(args, "usuario_id")
        usuario_id = validar_uuid(bruto_usuario, "usuario_id") if bruto_usuario else None
        de = _instante(args, "de")
        ate = _instante(args, "ate")
        if de is not None and ate is not None:
            if de > ate:
                raise RequisicaoInvalida("O parâmetro 'de' não pode ser posterior a 'ate'.")
            if ate - de > INTERVALO_MAX:
                raise RequisicaoInvalida("O intervalo máximo da consulta é de 400 dias.")
        limite = _inteiro(args, "limite", LIMITE_PADRAO, 1, LIMITE_MAX)
        offset = _inteiro(args, "offset", 0, 0, OFFSET_MAX)

        # Auditoria ANTES de entregar dados: se falhar, a consulta falha (503) e nada é exposto.
        # Sem filtros (podem identificar pessoas) e sem resultados.
        self._repo.registrar_auditoria(
            tabela=TABELA_AUDITORIA,
            registro_id=None,
            ator_id=admin.id,
            detalhes=dict(DETALHES_AUDITORIA),
        )
        res = self._repo.listar_acessos_login(
            usuario_id=usuario_id,
            de=de.isoformat() if de else None,
            ate=ate.isoformat() if ate else None,
            limite=limite,
            offset=offset,
        )
        return {
            "itens": res["itens"],
            "total": res["total"],
            "limite": res["limite"],
            "offset": res["offset"],
        }
