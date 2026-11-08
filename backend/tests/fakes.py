"""Repositório em memória para testes unitários.

Emula o que o banco garante: índice único parcial de veículo aberto (atômico, sob lock),
UPDATE condicional por status e carimbos do servidor.
"""

from __future__ import annotations

import itertools
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from app.errors import VeiculoOcupado
from app.modelos import ESTADOS_ABERTOS, Configuracoes, Perfil, Requisicao
from tests.fakes_admin import ANTES, AdminMemoria, erro_db
from tests.fakes_checklist import ChecklistMemoria
from tests.fakes_dashboards import DashboardsMemoria
from tests.fakes_foto import FotoPerfilMemoria
from tests.fakes_notificacoes import NotificacoesMemoria
from tests.fakes_relatorios import RelatoriosMemoria

CARIMBOS = {
    "autorizada": "autorizado_em",
    "negada": "autorizado_em",
    "em_uso": "saida_real_em",
    "concluida": "chegada_real_em",
    "cancelada": "cancelada_em",
}


class RepoMemoria(
    AdminMemoria,
    ChecklistMemoria,
    FotoPerfilMemoria,
    DashboardsMemoria,
    RelatoriosMemoria,
    NotificacoesMemoria,
):
    def __init__(self) -> None:
        self._init_admin()
        self._init_checklist()
        self._init_foto()
        self._init_dashboards()
        self._init_relatorios()
        self._init_notificacoes()
        self.perfis: dict[str, Perfil] = {}
        self.veiculos: dict[str, dict[str, Any]] = {}
        self.requisicoes: dict[str, Requisicao] = {}
        self.auditoria: list[tuple[str, str | None, str]] = []
        self.config = Configuracoes()
        self._lock = threading.Lock()
        self._seq = itertools.count(1)

    # -- montagem dos testes ------------------------------------------------------
    def novo_perfil(self, role: str = "solicitante", ativo: bool = True) -> Perfil:
        n = next(self._seq)
        p = Perfil(str(uuid.uuid4()), f"Pessoa {n}", "PU", role, ativo)  # type: ignore[arg-type]
        self.perfis[p.id] = p
        email = f"pessoa{n}@agu.gov.br"
        self.contas_auth[p.id] = {
            "email": email,
            "nome": p.nome,
            "confirmado": True,
            "banido": False,
        }
        return p

    def novo_veiculo(self, ativo: bool = True) -> str:
        vid = str(uuid.uuid4())
        self.veiculos[vid] = {
            "id": vid,
            "descricao": "Carro",
            "placa": None,
            "ativo": ativo,
            "criado_em": ANTES,
            "atualizado_em": ANTES,
        }
        return vid

    # -- contrato -----------------------------------------------------------------
    def obter_perfil(self, usuario_id: str) -> Perfil | None:
        return self.perfis.get(usuario_id)

    def ler_configuracoes(self) -> Configuracoes:
        return self.config

    def listar_veiculos(self) -> list[dict[str, Any]]:
        return [v for v in self.veiculos.values() if v["ativo"]]

    def obter_veiculo(self, veiculo_id: str) -> dict[str, Any] | None:
        v = self.veiculos.get(veiculo_id)
        return dict(v) if v else None

    def veiculos_ocupados(self) -> set[str]:
        with self._lock:
            return {
                r["veiculo_id"] for r in self.requisicoes.values() if r["status"] in ESTADOS_ABERTOS
            }

    def veiculo_ocupado(self, veiculo_id: str) -> bool:
        return veiculo_id in self.veiculos_ocupados()

    def inserir_requisicao(self, dados: dict[str, Any]) -> Requisicao:
        with self._lock:  # a "constraint" do banco: checagem + gravação atômicas
            if any(
                r["veiculo_id"] == dados["veiculo_id"] and r["status"] in ESTADOS_ABERTOS
                for r in self.requisicoes.values()
            ):
                raise VeiculoOcupado("Este veículo já possui uma requisição em aberto.")
            rid = str(uuid.uuid4())
            agora = datetime.now(UTC).isoformat()
            req: Requisicao = {
                "id": rid,
                "status": "solicitada",
                "autorizado_por": None,
                "autorizado_em": None,
                "saida_real_em": None,
                "chegada_real_em": None,
                "saida_registrada_por": None,
                "chegada_registrada_por": None,
                "cancelada_por": None,
                "cancelada_em": None,
                "km_saida": None,
                "km_chegada": None,
                "criado_em": agora,
                "atualizado_em": agora,
                **dados,
            }
            self.requisicoes[rid] = req
            self.auditoria.append((rid, None, "solicitada"))
            return dict(req)

    def obter_requisicao(self, requisicao_id: str) -> Requisicao | None:
        r = self.requisicoes.get(requisicao_id)
        return dict(r) if r else None

    def listar_requisicoes(
        self, *, usuario_id: str | None, status: str | None, limite: int, offset: int
    ) -> list[Requisicao]:
        itens = [
            dict(r)
            for r in self.requisicoes.values()
            if (usuario_id is None or r["usuario_id"] == usuario_id)
            and (status is None or r["status"] == status)
        ]
        return itens[offset : offset + limite]

    def contar_requisicoes(self, *, usuario_id: str | None, status: str | None) -> int:
        return sum(
            1
            for r in self.requisicoes.values()
            if (usuario_id is None or r["usuario_id"] == usuario_id)
            and (status is None or r["status"] == status)
        )

    def nomes_perfis(self, ids: set[str]) -> dict[str, str]:
        return {i: self.perfis[i].nome for i in ids if i in self.perfis}

    def descricoes_veiculos(self, ids: set[str]) -> dict[str, str]:
        return {i: self.veiculos[i]["descricao"] for i in ids if i in self.veiculos}

    def transicionar(
        self, requisicao_id: str, status_atual: str, campos: dict[str, Any]
    ) -> Requisicao | None:
        with self._lock:
            r = self.requisicoes.get(requisicao_id)
            if r is None or r["status"] != status_atual:
                return None
            self._triggers_km(r, campos)
            self._triggers_checklist(r, campos)
            self.auditoria.append((requisicao_id, status_atual, campos["status"]))
            r.update(campos)
            r[CARIMBOS[campos["status"]]] = datetime.now(UTC).isoformat()
            return dict(r)

    @staticmethod
    def _ck(nome: str) -> Exception:
        return erro_db("23514", f'violates check constraint "{nome}"')

    def _triggers_km(self, r: Requisicao, campos: dict[str, Any]) -> None:
        """Emula as constraints e o trigger da migration 18 (erros traduzidos de verdade)."""
        for c in ("km_saida", "km_chegada"):
            if r.get(c) is not None and c in campos and campos[c] != r[c]:
                raise erro_db("AG004", "Quilometragem já gravada não pode mudar.")
        novo = {**r, **campos}
        km = [novo[c] for c in ("km_saida", "km_chegada") if novo[c] is not None]
        if novo["km_chegada"] is not None and novo["km_saida"] is None:
            raise self._ck("ck_requisicoes_km_chegada_exige_saida")
        if len(km) == 2 and novo["km_chegada"] < novo["km_saida"]:
            raise self._ck("ck_requisicoes_km_chegada_maior_igual_saida")
        if any(v < 0 for v in km):
            raise self._ck("ck_requisicoes_km_saida_nao_negativo")
        if any(v > 2_147_483_647 for v in km):
            raise erro_db("22003", "integer out of range")
        if self.config.quilometragem:
            if novo["status"] == "em_uso" and novo["km_saida"] is None:
                raise erro_db("AG012", "Quilometragem de saída é obrigatória.")
            if novo["status"] == "concluida" and len(km) < 2:
                raise erro_db("AG012", "Quilometragem de saída e de chegada é obrigatória.")
