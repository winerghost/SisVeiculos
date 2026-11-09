"""Fake em memória do repositório de saudações (espelha as regras da tabela)."""

from __future__ import annotations

import uuid
from typing import Any

from app.errors import Conflito


class RepoSaudacoesMemoria:
    def __init__(self) -> None:
        self.linhas: list[dict[str, Any]] = []
        self.falhar_aprovadas = False
        self._n = 0

    def listar(
        self, *, status: str | None = None, ocasiao: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in reversed(self.linhas)
            if (status is None or r["status"] == status)
            and (ocasiao is None or r["ocasiao"] == ocasiao)
        ]

    def inserir(self, *, ocasiao: str, texto: str, origem: str, criado_por: str) -> dict[str, Any]:
        if any(r["ocasiao"] == ocasiao and r["texto"] == texto for r in self.linhas):
            raise Conflito("Esta frase já existe para a ocasião.", "saudacao_duplicada")
        self._n += 1
        linha = {
            "id": str(uuid.uuid4()),
            "ocasiao": ocasiao,
            "texto": texto,
            "origem": origem,
            "status": "pendente",
            "criado_em": f"2026-11-08T12:00:{self._n:02d}+00:00",
            "criado_por": criado_por,
            "decidido_por": None,
        }
        self.linhas.append(linha)
        return dict(linha)

    def semear(self, *, status: str, **kw: Any) -> dict[str, Any]:
        """Atalho de teste: insere e leva ao status pedido (como o banco permite)."""
        linha = self.inserir(**kw)
        if status != "pendente":
            self.decidir(linha["id"], status=status, ator_id=kw["criado_por"])
        return self.obter(linha["id"]) or linha

    def decidir(self, saudacao_id: str, *, status: str, ator_id: str) -> dict[str, Any] | None:
        for r in self.linhas:
            if r["id"] == saudacao_id and r["status"] != status:
                r["status"] = status
                r["decidido_por"] = ator_id
                return dict(r)
        return None

    def obter(self, saudacao_id: str) -> dict[str, Any] | None:
        return next((dict(r) for r in self.linhas if r["id"] == saudacao_id), None)

    def listar_aprovadas(self, ocasiao: str) -> list[str]:
        if self.falhar_aprovadas:
            raise RuntimeError("banco fora")
        return [
            r["texto"] for r in self.linhas if r["ocasiao"] == ocasiao and r["status"] == "aprovada"
        ]
