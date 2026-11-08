"""Leitura administrativa das coordenadas do motorista (somente admin, cada leitura auditada)."""

from __future__ import annotations

from typing import Any

from app.errors import NaoEncontrado
from app.modelos import Perfil
from app.repositorios.base import Repositorio
from app.services.admin_usuarios import exigir_admin
from app.services.validacao import validar_uuid


class ServicoLocalizacoes:
    """Coordenadas são dado sensível (LGPD): só o admin lê, e cada leitura vai para a auditoria."""

    def __init__(self, repo: Repositorio) -> None:
        self._repo = repo

    def listar(self, admin: Perfil, requisicao_id: str) -> dict[str, Any]:
        exigir_admin(admin)
        rid = validar_uuid(requisicao_id)
        if self._repo.obter_requisicao(rid) is None:
            raise NaoEncontrado("Requisição não encontrada.")
        linhas = self._repo.listar_localizacoes(rid)
        # Auditoria ANTES de devolver (falha fechada: sem trilha, sem dados). Só contagem: as
        # coordenadas nunca vão à auditoria.
        self._repo.registrar_auditoria(
            tabela="requisicoes",
            registro_id=rid,
            ator_id=admin.id,
            detalhes={"requisicao_id": rid, "itens": len(linhas)},
            acao="localizacao_lida",
        )
        nomes = self._repo.nomes_perfis({r["registrado_por"] for r in linhas})
        return {
            "itens": [
                {
                    "momento": r["momento"],
                    "latitude": r["latitude"],
                    "longitude": r["longitude"],
                    "precisao_m": r.get("precisao_m"),
                    "registrado_em": r["registrado_em"],
                    "registrado_por_nome": nomes.get(r["registrado_por"]),
                }
                for r in linhas
            ]
        }
