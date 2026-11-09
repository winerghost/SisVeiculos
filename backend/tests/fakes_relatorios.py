"""Fake das RPCs `rel_*`, de `reservar_envio_email`/`marcar_envio_email` e da auditoria de
relatórios (migration 20261109120000). Registra os parâmetros recebidos para os testes
inspecionarem o que o service enviou (escopo `usuario`, flags de PII, limite)."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.modelos import ESTADOS_ABERTOS
from tests.fakes_admin import erro_db

TRANSICOES = {
    "pendente": {"enviando", "falhou"},
    "enviando": {"enviado", "falhou"},
    "falhou": {"enviando"},
}


class RelatoriosMemoria:
    """Mixin do RepoMemoria."""

    def _init_relatorios(self) -> None:
        self.chamadas_rel: list[tuple[str, dict[str, Any]]] = []
        self.envios: dict[str, dict[str, Any]] = {}
        self.marcas: list[tuple[str, str, str | None]] = []  # (id, status, erro_codigo)
        self.rel_extra_colunas: dict[str, Any] = {}  # injeta colunas "vazadas" pelo banco
        self._lock_envios = threading.Lock()
        self.dedupe_envios = True  # False isola testes de cota de envios idênticos
        self.chamadas_reaper = 0
        self.falha_reaper: Exception | None = None

    def chamadas_de(self, nome: str) -> list[dict[str, Any]]:
        return [p for n, p in self.chamadas_rel if n == nome]

    # -- listas -----------------------------------------------------------------------------
    def _lista(
        self, nome: str, params: dict[str, Any], cols: list[str], linhas: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.chamadas_rel.append((nome, params))
        limite = params["limite"]
        return {
            "colunas": cols,
            "total_linhas": min(len(linhas), limite),
            "truncado": len(linhas) > limite,
            "linhas": linhas[:limite],
        }

    def _requisicoes_filtradas(self, p: dict[str, Any], so_abertas: bool = False) -> list[Any]:
        return [
            r
            for r in self.requisicoes.values()
            if (p.get("usuario") is None or r["usuario_id"] == p["usuario"])
            and (p.get("unidade") is None or r["unidade"] == p["unidade"])
            and (p.get("veiculo") is None or r["veiculo_id"] == p["veiculo"])
            and (not so_abertas or r["status"] in ESTADOS_ABERTOS)
        ]

    def rel_movimentacao(self, **p: Any) -> dict[str, Any]:
        cols = [
            "codigo",
            "criada_em",
            "unidade",
            "veiculo_id",
            "veiculo",
            "status",
            "hora_saida_prevista",
            "duracao_uso_s",
        ]
        if p["incluir_nomes"]:
            cols.append("solicitante")
        if p["incluir_trajeto"]:
            cols.append("trajeto")
        linhas = []
        for r in self._requisicoes_filtradas(p):
            linha = {
                "codigo": r["id"][:8],
                "criada_em": r["criado_em"],
                "unidade": r["unidade"],
                "veiculo_id": r["veiculo_id"],
                "veiculo": "Carro",
                "status": r["status"],
                "hora_saida_prevista": r["hora_saida"],
                "duracao_uso_s": 3725,
            }
            if p["incluir_nomes"]:
                linha["solicitante"] = r["nome_solicitante"]
            if p["incluir_trajeto"]:
                linha["trajeto"] = r["trajeto"]
            linha.update(self.rel_extra_colunas)
            linhas.append(linha)
        return self._lista("movimentacao", p, cols + list(self.rel_extra_colunas), linhas)

    def rel_pendencias(self, **p: Any) -> dict[str, Any]:
        cols = ["codigo", "status", "unidade", "veiculo_id", "veiculo", "desde", "decorrido_s"]
        if p["incluir_nomes"]:
            cols.append("solicitante")
        linhas = []
        for r in self._requisicoes_filtradas(p, so_abertas=True):
            linha = {
                "codigo": r["id"][:8],
                "status": r["status"],
                "unidade": r["unidade"],
                "veiculo_id": r["veiculo_id"],
                "veiculo": "Carro",
                "desde": r["criado_em"],
                "decorrido_s": 60,
            }
            if p["incluir_nomes"]:
                linha["solicitante"] = r["nome_solicitante"]
            linhas.append(linha)
        return self._lista("pendencias", p, cols, linhas)

    def _agrupado(self, nome: str, p: dict[str, Any], linha: dict[str, Any]) -> dict[str, Any]:
        self.chamadas_rel.append((nome, p))
        return {"agrupar": p["agrupar"], "total_linhas": 1, "truncado": False, "linhas": [linha]}

    def rel_resumo(self, **p: Any) -> dict[str, Any]:
        n = len(self._requisicoes_filtradas(p))
        return self._agrupado(
            "resumo",
            p,
            {
                "grupo": "2026-11-01",
                "rotulo": "2026-11-01",
                "requisicoes": n,
                "autorizadas": 0,
                "negadas": 0,
                "canceladas": 0,
                "concluidas": 0,
                "horas_uso": 1.5,
            },
        )

    def rel_tempos(self, **p: Any) -> dict[str, Any]:
        return self._agrupado(
            "tempos",
            p,
            {
                "grupo": "PU",
                "rotulo": "PU",
                "espera_n": 2,
                "espera_media_s": 90,
                "saida_dif_min_s": -300,
            },
        )

    def rel_auditoria(self, **p: Any) -> dict[str, Any]:
        linhas = [
            {
                "ocorrido_em": "2026-11-08T12:00:00+00:00",
                "tabela": "perfis",
                "acao": "perfil_alterado",
                "de": None,
                "para": None,
                "ator_nome": "Admin",
                "registro_id": str(uuid.uuid4()),
            }
        ]
        cols = ["ocorrido_em", "tabela", "acao", "de", "para", "ator_nome", "registro_id"]
        return self._lista("auditoria", p, cols, linhas)

    def rel_opcionais_km(self, **p: Any) -> dict[str, Any]:
        self.chamadas_rel.append(("opcionais_km", p))
        if not self.config.quilometragem:
            return {"habilitado": False}
        return {
            "habilitado": True,
            "colunas": ["codigo", "unidade", "veiculo", "km_saida", "km_chegada", "km_rodado"],
            "totais": {"viagens": 1, "km_rodado": 10},
            "total_linhas": 1,
            "truncado": False,
            "linhas": [
                {
                    "codigo": "abcd1234",
                    "unidade": "PU",
                    "veiculo": "Carro",
                    "km_saida": 100,
                    "km_chegada": 110,
                    "km_rodado": 10,
                }
            ],
        }

    def rel_opcionais_avarias(self, **p: Any) -> dict[str, Any]:
        self.chamadas_rel.append(("opcionais_avarias", p))
        if not self.config.checklist:
            return {"habilitado": False}
        return {
            "habilitado": True,
            "colunas": [
                "codigo",
                "momento",
                "registrado_em",
                "unidade",
                "veiculo",
                "combustivel",
                *self.rel_extra_colunas,
            ],
            "totais": {"checklists": 1, "com_avaria": 1},
            "total_linhas": 1,
            "truncado": False,
            "linhas": [
                {
                    "codigo": "abcd1234",
                    "momento": "saida",
                    "registrado_em": "2026-11-08T12:00:00+00:00",
                    "unidade": "PU",
                    "veiculo": "Carro",
                    "combustivel": "meio",
                    **self.rel_extra_colunas,
                }
            ],
        }

    # -- envios por e-mail --------------------------------------------------------------------
    def reservar_envio_email(
        self,
        *,
        solicitante: str,
        destinatario: str,
        tipo: str,
        formato: str,
        filtros: dict[str, Any],
        max_por_hora: int,
        max_por_usuario_hora: int | None = None,
    ) -> dict[str, Any]:
        with self._lock_envios:  # o advisory lock do banco
            if "@" in str(filtros):
                raise erro_db("AG027")
            if not self.config.relatorio_email:
                raise erro_db("AG026")
            d = self.perfis.get(destinatario)
            if d is None or not d.ativo:
                raise erro_db("AG024")
            recente = datetime.now(UTC) - timedelta(seconds=30)
            for e in self.envios.values():  # dedupe de 30 s, antes das cotas
                if (
                    self.dedupe_envios
                    and e["solicitado_por"] == solicitante
                    and e["destinatario_id"] == destinatario
                    and e["tipo_relatorio"] == tipo
                    and e["formato"] == formato
                    and e["filtros"] == filtros
                    and e["status"] in ("pendente", "enviando")
                    and e["criado_em"] > recente
                ):
                    return {"id": e["id"], "duplicado": True}
            limite = datetime.now(UTC) - timedelta(hours=1)
            recentes = sum(1 for e in self.envios.values() if e["criado_em"] > limite)
            if recentes >= max_por_hora:
                raise erro_db("AG025", "Limite de envios por hora atingido.")
            proprios = sum(
                1
                for e in self.envios.values()
                if e["criado_em"] > limite and e["solicitado_por"] == solicitante
            )
            if max_por_usuario_hora is not None and proprios >= max_por_usuario_hora:
                raise erro_db("AG025", "Limite de envios por usuário por hora atingido.")
            eid = str(uuid.uuid4())
            self.envios[eid] = {
                "id": eid,
                "solicitado_por": solicitante,
                "destinatario_id": destinatario,
                "tipo_relatorio": tipo,
                "formato": formato,
                "filtros": filtros,
                "status": "pendente",
                "tentativas": 0,
                "erro_codigo": None,
                "criado_em": datetime.now(UTC),
            }
            return {"id": eid, "duplicado": False}

    def reaper_envios_email(self, minutos: int = 15) -> int:
        self.chamadas_reaper += 1
        if self.falha_reaper is not None:
            raise self.falha_reaper
        if not 5 <= minutos <= 1440:
            raise erro_db("AG027")
        with self._lock_envios:
            corte = datetime.now(UTC) - timedelta(minutes=minutos)
            n = 0
            for e in self.envios.values():
                if e["status"] in ("pendente", "enviando") and e["criado_em"] < corte:
                    e["status"], e["erro_codigo"] = "falhou", "timeout"
                    n += 1
            return n

    def marcar_envio_email(
        self, envio_id: str, status: str, erro_codigo: str | None = None
    ) -> None:
        with self._lock_envios:
            e = self.envios.get(envio_id)
            if e is None:
                raise erro_db("AG028")
            if status not in TRANSICOES.get(e["status"], set()):
                raise erro_db("AG001")
            e["status"] = status
            e["erro_codigo"] = (erro_codigo or "desconhecido") if status == "falhou" else None
            if status == "enviando":
                e["tentativas"] += 1
            self.marcas.append((envio_id, status, erro_codigo))

    def obter_envio_email(self, envio_id: str) -> dict[str, Any] | None:
        e = self.envios.get(envio_id)
        if e is None:
            return None
        return {k: e[k] for k in ("id", "solicitado_por", "status", "erro_codigo")}

    def registrar_auditoria_relatorio(
        self, *, acao: str, registro_id: str | None, ator_id: str, detalhes: dict[str, Any]
    ) -> None:
        if self.falha_auditoria is not None:
            raise self.falha_auditoria
        permitidas = {"tipo", "filtros", "formato", "via", "destinatario_id", "total_linhas"}
        if set(detalhes) - permitidas or "@" in str(detalhes):
            raise erro_db("AG003", "Detalhes fora da lista permitida.")
        tabela = "relatorios" if acao == "relatorio_gerado" else "envios_email"
        self._audit(tabela, registro_id, acao, ator_id, detalhes=detalhes)
