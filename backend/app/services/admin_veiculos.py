"""Administração de veículos (nunca apagar: ativar/desativar). Auditoria sai dos triggers
(`veiculo_criado`/`veiculo_alterado`) quando `alterado_por` é o admin; nada é duplicado."""

from __future__ import annotations

from typing import Any

from app.errors import NaoEncontrado
from app.modelos import Perfil
from app.repositorios.base import Repositorio
from app.services.admin_usuarios import exigir_admin, parse_ativo
from app.services.validacao import validar_uuid
from app.services.validacao_admin import validar_veiculo_criacao, validar_veiculo_edicao


class ServicoVeiculosAdmin:
    def __init__(self, repo: Repositorio) -> None:
        self._repo = repo

    @staticmethod
    def _acoes(v: dict[str, Any]) -> list[str]:
        return ["editar", "desativar"] if v["ativo"] else ["editar", "ativar"]

    def _apresentar(self, itens: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ocupados = self._repo.veiculos_ocupados()
        return [
            {
                **v,
                # Informativo para o admin: desativar não cancela requisição em andamento.
                "requisicao_aberta": v["id"] in ocupados,
                "acoes_permitidas": self._acoes(v),
            }
            for v in itens
        ]

    def listar(self, admin: Perfil, *, ativo: str | None) -> dict[str, Any]:
        exigir_admin(admin)
        itens = self._repo.listar_veiculos_admin(ativo=parse_ativo(ativo))
        return {"itens": self._apresentar(itens), "acoes_permitidas": ["criar"]}

    def _carregar(self, veiculo_id: str) -> dict[str, Any]:
        v = self._repo.obter_veiculo(validar_uuid(veiculo_id))
        if v is None:
            raise NaoEncontrado("Veículo não encontrado.")
        return v

    def obter(self, admin: Perfil, veiculo_id: str) -> dict[str, Any]:
        exigir_admin(admin)
        return self._apresentar([self._carregar(veiculo_id)])[0]

    def criar(self, admin: Perfil, corpo: Any) -> dict[str, Any]:
        exigir_admin(admin)
        dados = validar_veiculo_criacao(corpo)
        # Placa única: o índice parcial do banco decide (race-safe) e a API traduz o erro.
        return self._apresentar([self._repo.inserir_veiculo(dados, ator_id=admin.id)])[0]

    def editar(self, admin: Perfil, veiculo_id: str, corpo: Any) -> dict[str, Any]:
        exigir_admin(admin)
        campos = validar_veiculo_edicao(corpo)
        atual = self._carregar(veiculo_id)
        mudou = {k: v for k, v in campos.items() if atual.get(k) != v}
        if not mudou:
            return self.obter(admin, atual["id"])
        atualizado = self._repo.atualizar_veiculo(atual["id"], mudou, ator_id=admin.id)
        if atualizado is None:
            raise NaoEncontrado("Veículo não encontrado.")
        return self._apresentar([atualizado])[0]

    def _definir_ativo(self, admin: Perfil, veiculo_id: str, ativo: bool) -> dict[str, Any]:
        exigir_admin(admin)
        atual = self._carregar(veiculo_id)
        if bool(atual["ativo"]) == ativo:
            return self.obter(admin, atual["id"])  # idempotente: sem escrita nem auditoria
        atualizado = self._repo.atualizar_veiculo(atual["id"], {"ativo": ativo}, ator_id=admin.id)
        if atualizado is None:
            raise NaoEncontrado("Veículo não encontrado.")
        return self._apresentar([atualizado])[0]

    def ativar(self, admin: Perfil, veiculo_id: str) -> dict[str, Any]:
        return self._definir_ativo(admin, veiculo_id, True)

    def desativar(self, admin: Perfil, veiculo_id: str) -> dict[str, Any]:
        return self._definir_ativo(admin, veiculo_id, False)
