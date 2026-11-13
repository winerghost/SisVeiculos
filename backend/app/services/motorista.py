"""Perfil Motorista (recurso opcional, desligado por padrão).

O motorista só enxerga as viagens atribuídas a ele (anti-IDOR na consulta e na leitura por id).
Registrar saída/chegada reaproveita `ServicoRequisicoes.executar` (km e checklist continuam
obrigatórios); a permissão é decidida lá, por `motorista_registra`. Recurso desligado: 404
`recurso_desligado`.
"""

from __future__ import annotations

from typing import Any

from app.modelos import Perfil
from app.repositorios.base import Repositorio
from app.services.requisicoes import ServicoRequisicoes


class ServicoMotorista:
    def __init__(self, repo: Repositorio, requisicoes: ServicoRequisicoes) -> None:
        self._repo = repo
        self._requisicoes = requisicoes

    def listar_viagens(
        self,
        perfil: Perfil,
        *,
        status: str | None,
        limite: int,
        offset: int,
        arquivadas: bool | None = False,
    ) -> dict[str, Any]:
        """Só as viagens com `motorista_id` = JWT (o id nunca vem do cliente)."""
        return self._requisicoes.listar(
            perfil, status=status, limite=limite, offset=offset, arquivadas=arquivadas
        )

    def obter_viagem(self, perfil: Perfil, requisicao_id: str) -> dict[str, Any]:
        # 404 tanto para viagem inexistente quanto para a de outro motorista.
        return self._requisicoes.apresentar(perfil, self._requisicoes.obter(perfil, requisicao_id))

    def listar_motoristas(self) -> dict[str, Any]:
        """Seletor da equipe: motoristas ATIVOS, só `id` e `nome` (minimização, LGPD)."""
        self._requisicoes.exigir_motorista_ligado()
        itens, padrao = self._requisicoes.seletor_motoristas()
        # `padrao`: único motorista ativo (designado ao autorizar); null se houver 0 ou 2+.
        return {"itens": itens, "padrao": padrao}
