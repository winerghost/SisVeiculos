"""Veículos e disponibilidade (calculada aqui; a UI só exibe)."""

from __future__ import annotations

import re
from typing import Any

from app.errors import NaoEncontrado, RecursoDesligado, RequisicaoInvalida
from app.repositorios.base import Repositorio

# Código público opaco do QR (minúsculas e dígitos). `fullmatch` não aceita quebra de linha no fim.
_CODIGO_QR = re.compile(r"[a-z0-9]{16,32}")
MSG_QR_DESLIGADO = "O leitor de QR Code está desligado nas Configurações."
MSG_QR_CORPO = "Envie apenas o campo 'codigo'."
MSG_QR_NAO_ENCONTRADO = "Veículo não encontrado."


class ServicoVeiculos:
    def __init__(self, repo: Repositorio) -> None:
        self._repo = repo

    @staticmethod
    def _apresentar(v: dict[str, Any], ocupados: set[str]) -> dict[str, Any]:
        """Item único do contrato de veículo (usado pela lista e pela leitura de QR).
        Nunca inclui `codigo_publico`."""
        livre = v["id"] not in ocupados
        return {
            "id": v["id"],
            "descricao": v["descricao"],
            "placa": v.get("placa"),
            "disponivel": livre,
            "motivo_indisponibilidade": (
                None if livre else "Há uma requisição em aberto para este veículo."
            ),
            "acoes_permitidas": ["solicitar"] if livre else [],
        }

    def listar(self) -> list[dict[str, Any]]:
        ocupados = self._repo.veiculos_ocupados()
        return [self._apresentar(v, ocupados) for v in self._repo.listar_veiculos()]

    def por_codigo(self, corpo: Any) -> dict[str, Any]:
        """Identifica o veículo pelo código do QR (corpo `{"codigo": "..."}`). Só leitura: não
        concede permissão alguma (as ações seguem as regras de sempre). Formato inválido,
        inexistente e inativo dão exatamente a mesma resposta (404)."""
        if not self._repo.ler_configuracoes().leitor_qr_veiculos:
            raise RecursoDesligado(MSG_QR_DESLIGADO)
        if not isinstance(corpo, dict) or set(corpo) != {"codigo"}:
            raise RequisicaoInvalida(MSG_QR_CORPO)
        codigo = corpo["codigo"]
        # Antes de qualquer consulta; formato inválido = mesmo 404 do inexistente.
        if not isinstance(codigo, str) or not _CODIGO_QR.fullmatch(codigo):
            raise NaoEncontrado(MSG_QR_NAO_ENCONTRADO)
        v = self._repo.obter_veiculo_ativo_por_codigo(codigo)
        if v is None:
            raise NaoEncontrado(MSG_QR_NAO_ENCONTRADO)
        return self._apresentar(v, self._repo.veiculos_ocupados())
