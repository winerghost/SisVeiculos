"""Acesso a dados da tabela `saudacoes` (fora do contrato `Repositorio` principal, como a IA).

O ator (`criado_por`/`decidido_por`) vem sempre do JWT, nunca do corpo. O texto é imutável no
banco; o INSERT nasce `pendente` e nunca se volta a `pendente` (a auditoria sai de trigger).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from supabase import Client, ClientOptions, create_client

from app.errors import Conflito, SemPermissao, TransicaoInvalida
from app.repositorios.supabase_repo import TIMEOUT_POSTGREST, _exec

COLUNAS = "id,ocasiao,texto,origem,status,criado_em"
LIMITE_LISTA = 500
MSG_TRANSICAO = "Esta frase não pode voltar a pendente nem ser decidida do mesmo modo duas vezes."
MSG_EXCLUSAO = "Só frases rejeitadas podem ser excluídas."
MSG_ATOR_INVALIDO = "Só um administrador ativo pode cadastrar ou decidir saudações."


class RepositorioSaudacoes(Protocol):
    def listar(
        self, *, status: str | None = None, ocasiao: str | None = None
    ) -> list[dict[str, Any]]: ...

    def inserir(self, *, ocasiao: str, texto: str, origem: str, criado_por: str) -> dict[str, Any]:
        """Sempre nasce `pendente`. Levanta `Conflito` se (ocasiao, texto) já existir."""
        ...

    def decidir(self, saudacao_id: str, *, status: str, ator_id: str) -> dict[str, Any] | None:
        """pendente->aprovada|rejeitada e aprovada<->rejeitada. None se não existe ou já está
        no estado pedido. Nunca volta a pendente (o banco recusa: AG001)."""
        ...

    def obter(self, saudacao_id: str) -> dict[str, Any] | None: ...

    def excluir_rejeitada(self, saudacao_id: str, *, ator_id: str) -> bool:
        """Via RPC `excluir_saudacao`: True se apagou, False se o id não existe. Levanta
        `TransicaoInvalida` (AG003) se não estiver rejeitada e `SemPermissao` (AG002)."""
        ...

    def listar_aprovadas(self, ocasiao: str) -> list[str]: ...


class SupabaseRepositorioSaudacoes:
    def __init__(self, cliente: Client) -> None:
        self._c = cliente

    @classmethod
    def criar(cls, url: str, service_role_key: str) -> SupabaseRepositorioSaudacoes:
        return cls(
            create_client(
                url,
                service_role_key,
                options=ClientOptions(postgrest_client_timeout=TIMEOUT_POSTGREST),
            )
        )

    def listar(
        self, *, status: str | None = None, ocasiao: str | None = None
    ) -> list[dict[str, Any]]:
        def consulta() -> Any:
            q = self._c.table("saudacoes").select(COLUNAS)
            if status is not None:
                q = q.eq("status", status)
            if ocasiao is not None:
                q = q.eq("ocasiao", ocasiao)
            return q.order("criado_em", desc=True).limit(LIMITE_LISTA).execute()

        return list(_exec(consulta).data or [])

    def inserir(self, *, ocasiao: str, texto: str, origem: str, criado_por: str) -> dict[str, Any]:
        linha: dict[str, Any] = {
            "ocasiao": ocasiao,
            "texto": texto,
            "origem": origem,
            "criado_por": criado_por,
        }  # o banco força status 'pendente' no INSERT
        try:
            res = _exec(
                lambda: self._c.table("saudacoes").insert(linha).execute(),
                repetir_lock=True,
                admin=True,
            )
        except Conflito:
            raise Conflito("Esta frase já existe para a ocasião.", "saudacao_duplicada") from None
        except SemPermissao:  # AG002: criado_por não é admin ativo
            raise SemPermissao(MSG_ATOR_INVALIDO) from None
        return dict(res.data[0])

    def decidir(self, saudacao_id: str, *, status: str, ator_id: str) -> dict[str, Any] | None:
        agora = datetime.now(UTC).isoformat()
        try:
            res = _exec(
                lambda: self._c.table("saudacoes")
                .update({"status": status, "decidido_por": ator_id, "decidido_em": agora})
                .eq("id", saudacao_id)
                .neq("status", status)  # nunca "decide" o mesmo estado de novo
                .execute(),
                repetir_lock=True,
                admin=True,
            )
        except TransicaoInvalida:  # AG001
            raise TransicaoInvalida(MSG_TRANSICAO) from None
        except SemPermissao:  # AG002: decidido_por não é admin ativo
            raise SemPermissao(MSG_ATOR_INVALIDO) from None
        return dict(res.data[0]) if res.data else None

    def excluir_rejeitada(self, saudacao_id: str, *, ator_id: str) -> bool:
        # Único caminho de exclusão: o RPC (service_role) valida o admin ativo, exige status
        # 'rejeitada' e grava a auditoria na mesma transação.
        params = {"p_id": saudacao_id, "p_ator": ator_id}
        try:
            res = _exec(
                lambda: self._c.rpc("excluir_saudacao", params).execute(),
                repetir_lock=True,
                admin=True,
            )
        except TransicaoInvalida:  # AG003: a frase não está rejeitada
            raise TransicaoInvalida(MSG_EXCLUSAO) from None
        except SemPermissao:  # AG002: p_ator não é admin ativo
            raise SemPermissao(MSG_ATOR_INVALIDO) from None
        return res.data is True

    def obter(self, saudacao_id: str) -> dict[str, Any] | None:
        res = _exec(
            lambda: self._c.table("saudacoes")
            .select(COLUNAS)
            .eq("id", saudacao_id)
            .limit(1)
            .execute()
        )
        return dict(res.data[0]) if res.data else None

    def listar_aprovadas(self, ocasiao: str) -> list[str]:
        res = _exec(
            lambda: self._c.table("saudacoes")
            .select("texto")
            .eq("ocasiao", ocasiao)
            .eq("status", "aprovada")
            .limit(LIMITE_LISTA)
            .execute()
        )
        return [str(r["texto"]) for r in res.data or []]
