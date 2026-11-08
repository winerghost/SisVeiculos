"""Fakes da IA: repositório em memória e provedor roteirizado (nenhuma chamada de rede)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from app.ia.provedores import ErroProvedor, Resultado
from app.ia.repositorio import ConfigIABanco, Consumo


class RepoIAMemoria:
    def __init__(self, config: ConfigIABanco | None = None) -> None:
        self.config = config or ConfigIABanco()
        self.usos: list[dict[str, Any]] = []
        self.atualizacoes: list[tuple[dict[str, Any], str]] = []
        self.consumo_fixo: Consumo | None = None
        self.falhar_leitura = False
        self.falhar_registro = False

    def ler_config(self) -> ConfigIABanco:
        if self.falhar_leitura:
            raise RuntimeError("banco fora")
        return self.config

    def atualizar_config(self, campos: dict[str, Any], *, ator_id: str) -> ConfigIABanco:
        self.config = replace(self.config, **campos)
        self.atualizacoes.append((dict(campos), ator_id))
        return self.config

    def registrar_uso(self, **kw: Any) -> None:
        if self.falhar_registro:
            raise RuntimeError("banco fora")
        self.usos.append(kw)

    def consumo(
        self,
        *,
        usuario_id: str | None,
        unidade: str | None,
        desde_hora: datetime,
        desde_mes: datetime,
    ) -> Consumo:
        if self.consumo_fixo is not None:
            return self.consumo_fixo
        ok = [u for u in self.usos if u["status"] in {"ok", "erro", "timeout"}]
        tokens: dict[str, int] = {}
        for u in self.usos:
            tokens[u["provedor"]] = (
                tokens.get(u["provedor"], 0) + u["tokens_entrada"] + u["tokens_saida"]
            )
        return Consumo(
            usuario_hora=sum(1 for u in ok if u["usuario_id"] == usuario_id),
            unidade_hora=sum(1 for u in ok if u["unidade"] == unidade),
            tokens_mes=tokens,
        )


class ProvedorRoteirizado:
    """Responde na ordem de `respostas`: Resultado devolve, ErroProvedor/Exception levanta."""

    def __init__(self, nome: str, respostas: list[Resultado | Exception] | None = None) -> None:
        self.nome = nome
        self.respostas = list(respostas or [])
        self.chamadas: list[dict[str, Any]] = []

    def gerar(
        self, *, modelo: str, sistema: str, usuario: str, max_tokens: int, timeout: float
    ) -> Resultado:
        self.chamadas.append(
            {"modelo": modelo, "sistema": sistema, "usuario": usuario, "max_tokens": max_tokens}
        )
        r = self.respostas.pop(0) if self.respostas else Resultado("Olá!", 10, 5)
        if isinstance(r, Exception):
            raise r
        return r


def erro(tipo: str = "http") -> ErroProvedor:
    return ErroProvedor(tipo)
