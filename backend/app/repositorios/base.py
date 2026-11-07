"""Contrato de acesso a dados. Os services dependem só disto (fakes nos testes)."""

from __future__ import annotations

from typing import Any, Protocol

from app.modelos import Configuracoes, Perfil, Requisicao


class Repositorio(Protocol):
    def obter_perfil(self, usuario_id: str) -> Perfil | None: ...

    def ler_configuracoes(self) -> Configuracoes: ...

    def listar_veiculos(self) -> list[dict[str, Any]]:
        """Veículos ativos (id, descricao, placa)."""
        ...

    def obter_veiculo(self, veiculo_id: str) -> dict[str, Any] | None: ...

    def veiculos_ocupados(self) -> set[str]:
        """Ids de veículos com requisição aberta (solicitada, autorizada, em_uso)."""
        ...

    def veiculo_ocupado(self, veiculo_id: str) -> bool:
        """True se o veículo tem requisição aberta (consulta pontual, limit 1)."""
        ...

    def inserir_requisicao(self, dados: dict[str, Any]) -> Requisicao:
        """Insere como `solicitada`. Levanta VeiculoOcupado se o índice parcial recusar."""
        ...

    def obter_requisicao(self, requisicao_id: str) -> Requisicao | None: ...

    def listar_requisicoes(
        self, *, usuario_id: str | None, status: str | None, limite: int, offset: int
    ) -> list[Requisicao]:
        """`usuario_id` não nulo restringe ao dono (anti-IDOR aplicado na consulta)."""
        ...

    def contar_requisicoes(self, *, usuario_id: str | None, status: str | None) -> int:
        """Total com os mesmos filtros de `listar_requisicoes` (sem paginação)."""
        ...

    def nomes_perfis(self, ids: set[str]) -> dict[str, str]:
        """Só `perfis.nome` por id (nunca e-mail nem outros dados pessoais)."""
        ...

    def descricoes_veiculos(self, ids: set[str]) -> dict[str, str]:
        """`veiculos.descricao` por id (inclui veículos inativos, para o histórico)."""
        ...

    def transicionar(
        self, requisicao_id: str, status_atual: str, campos: dict[str, Any]
    ) -> Requisicao | None:
        """UPDATE condicional (`where status = status_atual`). None se ninguém casou."""
        ...

    # -- administração ------------------------------------------------------------------
    # Contrato da migration 15: cada método abaixo é UMA transação curta (um comando
    # PostgREST/RPC), nunca mistura escrita de requisição com escrita administrativa e
    # repete a operação em 55P03/40P01. Toda escrita leva o `ator_id` (admin ativo).
    def listar_perfis(
        self,
        *,
        role: str | None,
        ativo: bool | None,
        busca: str | None = None,
        ids_email: frozenset[str] = frozenset(),
        limite: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        """`busca` casa com `nome` (ilike, parametrizado); `ids_email` são ids já resolvidos
        pelo Auth cujo e-mail casa com a busca (OU lógico). Filtros combinam em E."""
        ...

    def contar_perfis(
        self,
        *,
        role: str | None,
        ativo: bool | None,
        busca: str | None = None,
        ids_email: frozenset[str] = frozenset(),
    ) -> int: ...

    def obter_perfil_admin(self, usuario_id: str) -> dict[str, Any] | None: ...

    def contar_admins_ativos(self) -> int: ...

    def atualizar_perfil(
        self, usuario_id: str, campos: dict[str, Any], *, ator_id: str
    ) -> dict[str, Any] | None:
        """UPDATE de nome/unidade/role/ativo com `alterado_por` = ator. None se não existe."""
        ...

    def inserir_convite(self, dados: dict[str, Any]) -> dict[str, Any]:
        """Sem `token_hash` no retorno. Índice de e-mail aberto => ConviteAbertoExistente."""
        ...

    def obter_convite(self, convite_id: str) -> dict[str, Any] | None: ...

    def obter_convite_aberto_por_email(self, email: str) -> dict[str, Any] | None:
        """Convite sem uso nem revogação (inclui expirado, que ainda bloqueia novo convite)."""
        ...

    def listar_convites(
        self, *, estado: str | None, agora: str, limite: int, offset: int
    ) -> list[dict[str, Any]]: ...

    def contar_convites(self, *, estado: str | None, agora: str) -> int: ...

    def revogar_convite(self, convite_id: str, *, ator_id: str) -> dict[str, Any] | None:
        """Revoga se ainda aberto (condicional). None se ninguém casou."""
        ...

    def consumir_convite(self, token_hash: str, usuario_id: str, nome: str) -> Perfil:
        """RPC atômica: valida, cria o perfil e marca o convite como usado."""
        ...

    def listar_veiculos_admin(self, *, ativo: bool | None) -> list[dict[str, Any]]: ...

    def inserir_veiculo(self, dados: dict[str, Any], *, ator_id: str) -> dict[str, Any]: ...

    def atualizar_veiculo(
        self, veiculo_id: str, campos: dict[str, Any], *, ator_id: str
    ) -> dict[str, Any] | None: ...

    def atualizar_configuracoes(self, campos: dict[str, bool], *, ator_id: str) -> Configuracoes:
        """Grava só os flags informados. A auditoria sai do trigger do banco."""
        ...

    def registrar_auditoria(
        self, *, tabela: str, registro_id: str | None, ator_id: str, detalhes: dict[str, Any]
    ) -> None:
        """Só para o que os triggers não cobrem (ex.: troca de nome). Sem dados pessoais."""
        ...

    # -- checklist (recurso opcional; migrations 19 a 22) ---------------------------------
    def momentos_checklist(self, requisicao_ids: set[str]) -> dict[str, set[str]]:
        """Momentos ('saida'/'chegada') com checklist já registrado, por requisição."""
        ...

    def obter_checklists(self, requisicao_id: str) -> list[dict[str, Any]]:
        """Checklists da requisição, cada um com a lista `fotos` (id, posicao, mime_type,
        tamanho_bytes, caminho). Inclui `descricao_avarias`: só o service decide quem vê."""
        ...

    def registrar_checklist(
        self,
        *,
        requisicao_id: str,
        momento: str,
        combustivel: str,
        avarias: bool,
        descricao_avarias: str | None,
        registrado_por: str,
        fotos: list[dict[str, Any]],
    ) -> str:
        """Grava checklist e fotos numa transação (RPC). `fotos`: id, mime_type, tamanho_bytes
        (a posição é a ordem). Devolve o id do checklist."""
        ...
