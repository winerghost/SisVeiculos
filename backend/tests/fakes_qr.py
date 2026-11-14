"""Suporte aos testes do QR Code: acrescenta ao repositório em memória o que o time Banco
entrega (`veiculos.codigo_publico` e a RPC `rotacionar_codigo_veiculo`) sem tocar nos fakes
compartilhados. A RPC real audita no banco; aqui o fake grava o mesmo evento."""

from __future__ import annotations

import secrets
import types
from typing import Any

from tests.fakes import RepoMemoria


def instalar_qr(repo: RepoMemoria) -> RepoMemoria:
    def por_codigo(self: RepoMemoria, codigo: str) -> dict[str, Any] | None:
        self.consultas_codigo.append(codigo)  # type: ignore[attr-defined]
        for v in self.veiculos.values():
            if v.get("codigo_publico") == codigo and v["ativo"]:
                return {k: v.get(k) for k in ("id", "descricao", "placa")}
        return None

    def rotacionar(self: RepoMemoria, veiculo_id: str, *, ator_id: str) -> str | None:
        v = self.veiculos.get(veiculo_id)
        if v is None:
            return None
        self._exigir_admin_ativo(ator_id)  # type: ignore[attr-defined]
        v["codigo_publico"] = secrets.token_hex(12)
        self._audit("veiculos", veiculo_id, "veiculo_codigo_rotacionado", ator_id)  # type: ignore[attr-defined]
        return str(v["codigo_publico"])

    repo.consultas_codigo = []  # type: ignore[attr-defined]
    repo.obter_veiculo_ativo_por_codigo = types.MethodType(por_codigo, repo)  # type: ignore[attr-defined]
    repo.rotacionar_codigo_veiculo = types.MethodType(rotacionar, repo)  # type: ignore[attr-defined]
    return repo
