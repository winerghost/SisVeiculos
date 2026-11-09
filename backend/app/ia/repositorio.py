"""Acesso a dados da IA (tabelas `ia_configuracoes` e `ia_uso`, RPC `ia_consumo`).

Fica fora do contrato `Repositorio` principal de propósito: o pacote é opcional e isolado.
O ator das escritas vem sempre do JWT (nunca do corpo).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from supabase import Client, ClientOptions, create_client

from app.errors import ErroInterno
from app.repositorios.supabase_repo import TIMEOUT_POSTGREST, _exec

CAMPOS_CONFIG: tuple[str, ...] = (
    "mestre",
    "saudacao",
    "resumo",
    "provedor",
    "modelo",
    "provedor_reserva",
    "teto_tokens_mes",
)
CHAVES_LIGA_DESLIGA: tuple[str, ...] = ("mestre", "saudacao", "resumo")


@dataclass(frozen=True)
class ConfigIABanco:
    """Configuração escolhida na tela. Tudo desligado por padrão (espelha o banco)."""

    mestre: bool = False
    saudacao: bool = False
    resumo: bool = False
    provedor: str | None = None
    modelo: str | None = None
    provedor_reserva: str | None = None
    teto_tokens_mes: int | None = None

    def como_dict(self) -> dict[str, Any]:
        return {c: getattr(self, c) for c in CAMPOS_CONFIG}

    @classmethod
    def de_linha(cls, r: dict[str, Any]) -> ConfigIABanco:
        return cls(
            mestre=bool(r.get("mestre")),
            saudacao=bool(r.get("saudacao")),
            resumo=bool(r.get("resumo")),
            provedor=r.get("provedor"),
            modelo=r.get("modelo"),
            provedor_reserva=r.get("provedor_reserva"),
            teto_tokens_mes=r.get("teto_tokens_mes"),
        )


@dataclass(frozen=True)
class Consumo:
    usuario_hora: int = 0
    unidade_hora: int = 0
    tokens_mes: dict[str, int] = field(default_factory=dict)

    @property
    def tokens_mes_total(self) -> int:
        return sum(self.tokens_mes.values())


class RepositorioIA(Protocol):
    def ler_config(self) -> ConfigIABanco: ...

    def atualizar_config(self, campos: dict[str, Any], *, ator_id: str) -> ConfigIABanco:
        """Grava só os campos informados. A auditoria sai do trigger do banco."""
        ...

    def registrar_uso(
        self,
        *,
        funcao: str,
        provedor: str,
        modelo: str,
        usuario_id: str | None,
        unidade: str | None,
        tokens_entrada: int,
        tokens_saida: int,
        status: str,
        latencia_ms: int | None,
    ) -> None:
        """Append-only. Sem conteúdo de prompt nem de resposta."""
        ...

    def consumo(
        self,
        *,
        usuario_id: str | None,
        unidade: str | None,
        desde_hora: datetime,
        desde_mes: datetime,
    ) -> Consumo: ...


class SupabaseRepositorioIA:
    def __init__(self, cliente: Client) -> None:
        self._c = cliente

    @classmethod
    def criar(cls, url: str, service_role_key: str) -> SupabaseRepositorioIA:
        return cls(
            create_client(
                url,
                service_role_key,
                options=ClientOptions(postgrest_client_timeout=TIMEOUT_POSTGREST),
            )
        )

    def ler_config(self) -> ConfigIABanco:
        res = _exec(
            lambda: self._c.table("ia_configuracoes")
            .select(",".join(CAMPOS_CONFIG))
            .limit(1)
            .execute()
        )
        return ConfigIABanco.de_linha(res.data[0]) if res.data else ConfigIABanco()

    def atualizar_config(self, campos: dict[str, Any], *, ator_id: str) -> ConfigIABanco:
        res = _exec(
            lambda: self._c.table("ia_configuracoes")
            .update({**campos, "alterado_por": ator_id})
            .eq("id", True)
            .execute(),
            repetir_lock=True,
            admin=True,
        )
        if not res.data:
            raise ErroInterno("Configurações indisponíveis. Tente novamente.")
        return ConfigIABanco.de_linha(res.data[0])

    def registrar_uso(
        self,
        *,
        funcao: str,
        provedor: str,
        modelo: str,
        usuario_id: str | None,
        unidade: str | None,
        tokens_entrada: int,
        tokens_saida: int,
        status: str,
        latencia_ms: int | None,
    ) -> None:
        _exec(
            lambda: self._c.table("ia_uso")
            .insert(
                {
                    "funcao": funcao,
                    "provedor": provedor,
                    "modelo": modelo,
                    "usuario_id": usuario_id,
                    "unidade": unidade,
                    "tokens_entrada": tokens_entrada,
                    "tokens_saida": tokens_saida,
                    "status": status,
                    "latencia_ms": latencia_ms,
                }
            )
            .execute()
        )

    def consumo(
        self,
        *,
        usuario_id: str | None,
        unidade: str | None,
        desde_hora: datetime,
        desde_mes: datetime,
    ) -> Consumo:
        res = _exec(
            lambda: self._c.rpc(
                "ia_consumo",
                {
                    "p_usuario": usuario_id,
                    "p_unidade": unidade,
                    "p_desde_hora": desde_hora.isoformat(),
                    "p_desde_mes": desde_mes.isoformat(),
                },
            ).execute()
        )
        d = res.data if isinstance(res.data, dict) else {}
        tokens = d.get("tokens_mes") if isinstance(d.get("tokens_mes"), dict) else {}
        return Consumo(
            usuario_hora=int(d.get("usuario_hora") or 0),
            unidade_hora=int(d.get("unidade_hora") or 0),
            tokens_mes={str(k): int(v or 0) for k, v in tokens.items()},
        )
