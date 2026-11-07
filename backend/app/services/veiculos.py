"""Veículos e disponibilidade (calculada aqui; a UI só exibe)."""

from __future__ import annotations

from typing import Any

from app.repositorios.base import Repositorio


class ServicoVeiculos:
    def __init__(self, repo: Repositorio) -> None:
        self._repo = repo

    def listar(self) -> list[dict[str, Any]]:
        ocupados = self._repo.veiculos_ocupados()
        itens = []
        for v in self._repo.listar_veiculos():
            livre = v["id"] not in ocupados
            itens.append(
                {
                    "id": v["id"],
                    "descricao": v["descricao"],
                    "placa": v.get("placa"),
                    "disponivel": livre,
                    "motivo_indisponibilidade": (
                        None if livre else "Há uma requisição em aberto para este veículo."
                    ),
                    "acoes_permitidas": ["solicitar"] if livre else [],
                }
            )
        return itens
