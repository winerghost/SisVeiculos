"""Fake das RPCs `dash_*` (migration 20261108120000) e da busca de requisições por id.

As RPCs registram os parâmetros recebidos (para os testes verem o que o service enviou) e
devolvem um jsonb mínimo; `dash_operacao_agora` calcula `abertas` do estado em memória, na
mesma ordem de urgência do banco. O AG023 sai pela tradução real (`erro_db`).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.modelos import ESTADOS_ABERTOS, Requisicao
from tests.fakes_admin import agora_iso, erro_db

ORDEM_URGENCIA = {"solicitada": 1, "autorizada": 2, "em_uso": 3}


class DashboardsMemoria:
    """Mixin do RepoMemoria."""

    def _init_dashboards(self) -> None:
        self.chamadas_dash: list[tuple[str, dict[str, Any]]] = []
        self.chamadas_por_ids: list[list[str]] = []

    def _janela(self, nome: str, params: dict[str, Any], granularidade: str = "dia") -> None:
        self.chamadas_dash.append((nome, params))
        ini, fim = datetime.fromisoformat(params["inicio"]), datetime.fromisoformat(params["fim"])
        if fim <= ini or fim - ini > timedelta(days=366):
            raise erro_db("AG023", "Período inválido.")
        if granularidade not in {"dia", "semana", "mes"}:
            raise erro_db("AG023", "Granularidade inválida.")

    def obter_requisicoes_por_ids(self, ids: list[str]) -> list[Requisicao]:
        self.chamadas_por_ids.append(list(ids))
        return [dict(self.requisicoes[i]) for i in ids if i in self.requisicoes]

    def dash_operacao_agora(self, *, unidade: str | None, veiculo: str | None) -> dict[str, Any]:
        self.chamadas_dash.append(("operacao", {"unidade": unidade, "veiculo": veiculo}))
        desde = {
            "solicitada": "criado_em",
            "autorizada": "autorizado_em",
            "em_uso": "saida_real_em",
        }
        abertas = sorted(
            (
                r
                for r in self.requisicoes.values()
                if r["status"] in ESTADOS_ABERTOS
                and (unidade is None or r["unidade"] == unidade)
                and (veiculo is None or r["veiculo_id"] == veiculo)
            ),
            key=lambda r: (ORDEM_URGENCIA[r["status"]], str(r[desde[r["status"]]]), r["id"]),
        )

        def total(st: str) -> int:
            return sum(1 for r in abertas if r["status"] == st)

        # Espelha o contrato da RPC dash_operacao_agora (migration 20261108120000).
        return {
            "gerado_em": agora_iso(),
            "veiculos": {"ativos": 0, "livres": 0, "reservados": 0, "em_uso": 0},
            "solicitadas": {"total": total("solicitada"), "mais_antiga": None},
            "autorizadas_sem_saida": {"total": total("autorizada"), "mais_antiga": None},
            "em_uso": {
                "total": total("em_uso"),
                "decorrido_medio_s": None,
                "decorrido_max_s": None,
            },
            "abertas": [
                {
                    "id": r["id"],
                    "status": r["status"],
                    "veiculo_id": r["veiculo_id"],
                    "unidade": r["unidade"],
                    "desde": r[desde[r["status"]]],
                    "decorrido_s": 0,
                }
                for r in abertas
            ],
        }

    def dash_volume_desfecho(
        self,
        *,
        inicio: str,
        fim: str,
        unidade: str | None,
        veiculo: str | None,
        granularidade: str,
    ) -> dict[str, Any]:
        p = {"inicio": inicio, "fim": fim, "unidade": unidade, "veiculo": veiculo}
        self._janela("volume", {**p, "granularidade": granularidade}, granularidade)
        return {"inicio": inicio, "fim": fim, "granularidade": granularidade, "serie": []}

    def _painel_janela(
        self, nome: str, inicio: str, fim: str, unidade: str | None, veiculo: str | None
    ) -> dict[str, Any]:
        self._janela(nome, {"inicio": inicio, "fim": fim, "unidade": unidade, "veiculo": veiculo})
        return {"inicio": inicio, "fim": fim, "itens": []}

    def dash_tempos_fluxo(
        self, *, inicio: str, fim: str, unidade: str | None, veiculo: str | None
    ) -> dict[str, Any]:
        return self._painel_janela("tempos", inicio, fim, unidade, veiculo)

    def dash_uso_unidade(
        self, *, inicio: str, fim: str, unidade: str | None, veiculo: str | None
    ) -> dict[str, Any]:
        return self._painel_janela("unidades", inicio, fim, unidade, veiculo)

    def dash_uso_veiculo(
        self, *, inicio: str, fim: str, unidade: str | None, veiculo: str | None
    ) -> dict[str, Any]:
        return self._painel_janela("veiculos", inicio, fim, unidade, veiculo)

    def dash_opcionais(
        self, *, inicio: str, fim: str, unidade: str | None, veiculo: str | None
    ) -> dict[str, Any]:
        return self._painel_janela("opcionais", inicio, fim, unidade, veiculo)

    def dash_governanca(self) -> dict[str, Any]:
        self.chamadas_dash.append(("governanca", {}))
        return {"gerado_em": agora_iso(), "configuracoes": self.config.como_dict()}
