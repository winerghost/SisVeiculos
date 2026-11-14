"""Repositório em memória para testes unitários.

Emula o que o banco garante: índice único parcial de veículo aberto (atômico, sob lock),
UPDATE condicional por status e carimbos do servidor.
"""

from __future__ import annotations

import itertools
import math
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
from tests.fakes_push import PushMemoria
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
    PushMemoria,
):
    def __init__(self) -> None:
        self._init_admin()
        self._init_checklist()
        self._init_foto()
        self._init_dashboards()
        self._init_relatorios()
        self._init_notificacoes()
        self._init_push()
        self._seq_eventos = 0
        self.perfis: dict[str, Perfil] = {}
        self.veiculos: dict[str, dict[str, Any]] = {}
        self.requisicoes: dict[str, Requisicao] = {}
        self.auditoria: list[tuple[str, str | None, str]] = []
        self.config = Configuracoes()
        # Tabela `localizacoes_viagem` (dado sensível) e as chamadas da RPC do motorista.
        self.localizacoes: list[dict[str, Any]] = []
        # Tabela `requisicoes_arquivadas`: (usuario_id, requisicao_id) -> arquivada_em (PK).
        self.arquivadas: dict[tuple[str, str], str] = {}
        self.chamadas_rpc_motorista: list[dict[str, Any]] = []
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
                "motorista_id": None,
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
        self,
        *,
        usuario_id: str | None,
        status: str | None,
        limite: int,
        offset: int,
        motorista_id: str | None = None,
        ou_status: tuple[str, ...] | None = None,
        ou_registrada_por: str | None = None,
        arquivador_id: str | None = None,
        arquivadas: bool | None = None,
    ) -> list[Requisicao]:
        itens = [
            dict(r)
            for r in self.requisicoes.values()
            if self._casa(
                r,
                usuario_id,
                status,
                motorista_id,
                ou_status,
                arquivador_id,
                arquivadas,
                ou_registrada_por,
            )
        ]
        return itens[offset : offset + limite]

    def _casa(
        self,
        r: Requisicao,
        usuario_id: str | None,
        status: str | None,
        motorista_id: str | None,
        ou_status: tuple[str, ...] | None,
        arquivador_id: str | None,
        arquivadas: bool | None,
        ou_registrada_por: str | None = None,
    ) -> bool:
        arquivada = (arquivador_id, r["id"]) in self.arquivadas
        if arquivadas is not None and arquivador_id is not None and arquivada != arquivadas:
            return False
        return (
            (
                usuario_id is None
                or r["usuario_id"] == usuario_id
                or r["status"] in (ou_status or ())
                or (
                    ou_registrada_por is not None
                    and r["status"] == "concluida"
                    and ou_registrada_por
                    in (r.get("saida_registrada_por"), r.get("chegada_registrada_por"))
                )
            )
            and (motorista_id is None or r.get("motorista_id") == motorista_id)
            and (status is None or r["status"] == status)
        )

    def contar_requisicoes(
        self,
        *,
        usuario_id: str | None,
        status: str | None,
        motorista_id: str | None = None,
        ou_status: tuple[str, ...] | None = None,
        ou_registrada_por: str | None = None,
        arquivador_id: str | None = None,
        arquivadas: bool | None = None,
    ) -> int:
        return sum(
            1
            for r in self.requisicoes.values()
            if self._casa(
                r,
                usuario_id,
                status,
                motorista_id,
                ou_status,
                arquivador_id,
                arquivadas,
                ou_registrada_por,
            )
        )

    # -- arquivamento pessoal (emula a tabela e o gatilho AG041) -------------------------------
    def arquivar_requisicao(self, usuario_id: str, requisicao_id: str) -> None:
        with self._lock:  # PK composta + ON CONFLICT DO NOTHING: uma só linha, sem erro
            r = self.requisicoes.get(requisicao_id)
            if r is None or r["status"] not in {"concluida", "negada", "cancelada"}:
                raise erro_db("AG041", "Só é possível arquivar requisição encerrada.")
            self.arquivadas.setdefault((usuario_id, requisicao_id), datetime.now(UTC).isoformat())

    def desarquivar_requisicao(self, usuario_id: str, requisicao_id: str) -> None:
        with self._lock:
            self.arquivadas.pop((usuario_id, requisicao_id), None)

    def ids_arquivados(self, usuario_id: str, ids: set[str]) -> set[str]:
        return {rid for (uid, rid) in self.arquivadas if uid == usuario_id and rid in ids}

    def nomes_perfis(self, ids: set[str]) -> dict[str, str]:
        return {i: self.perfis[i].nome for i in ids if i in self.perfis}

    def descricoes_veiculos(self, ids: set[str]) -> dict[str, str]:
        return {i: self.veiculos[i]["descricao"] for i in ids if i in self.veiculos}

    def transicionar(
        self, requisicao_id: str, status_atual: str, campos: dict[str, Any]
    ) -> Requisicao | None:
        with self._lock:
            return self._transicionar(requisicao_id, status_atual, campos)

    def _transicionar(
        self, requisicao_id: str, status_atual: str, campos: dict[str, Any]
    ) -> Requisicao | None:
        """UPDATE condicional (chamar com `self._lock`). Todos os triggers rodam ANTES de qualquer
        mutação: se um levantar, nada foi alterado (como o rollback do banco)."""
        r = self.requisicoes.get(requisicao_id)
        if r is None or r["status"] != status_atual:
            return None
        campos = dict(campos)
        ator_motorista = campos.pop("motorista_alterado_por", None)  # coluna de transporte
        self._trigger_autodecisao(r, campos)
        self._triggers_km(r, campos)
        self._triggers_checklist(r, campos)
        self._trigger_motorista(r, campos, ator_motorista, campos["status"])
        self.auditoria.append((requisicao_id, status_atual, campos["status"]))
        self._seq_eventos += 1
        if "motorista_id" in campos:
            self.emular_designacao(
                requisicao_id,
                r.get("motorista_id"),
                campos["motorista_id"],
                ator_motorista,
                f"ev{self._seq_eventos}",
            )
        if campos["status"] == "cancelada":
            self.emular_cancelamento(
                requisicao_id, r.get("motorista_id"), campos.get("cancelada_por")
            )
        r.update(campos)
        r[CARIMBOS[campos["status"]]] = datetime.now(UTC).isoformat()
        return dict(r)

    def _trigger_autodecisao(self, r: Requisicao, campos: dict[str, Any]) -> None:
        """Emula AG040: autorizar/negar a própria requisição com a flag desligada."""
        propria = campos.get("autorizado_por") == r["usuario_id"]
        if (
            campos.get("status") in {"autorizada", "negada"}
            and propria
            and not self.config.autorizador_decide_propria
        ):
            raise erro_db("AG040", "Você não pode decidir sobre a sua própria requisição.")

    def atribuir_motorista(
        self,
        requisicao_id: str,
        motorista_id: str | None,
        status_permitidos: list[str],
        *,
        ator_id: str,
    ) -> Requisicao | None:
        with self._lock:
            r = self.requisicoes.get(requisicao_id)
            if r is None or r["status"] not in status_permitidos:
                return None
            # Como o PostgREST: o UPDATE leva motorista_id + a coluna de transporte juntos.
            self._trigger_motorista(r, {"motorista_id": motorista_id}, ator_id, r["status"])
            self.auditoria.append((requisicao_id, r["status"], "motorista_atribuido"))
            self._seq_eventos += 1
            self.emular_designacao(
                requisicao_id,
                r.get("motorista_id"),
                motorista_id,
                ator_id,
                f"ev{self._seq_eventos}",
            )
            r["motorista_id"] = motorista_id
            return dict(r)

    def _trigger_motorista(
        self, r: Requisicao, campos: dict[str, Any], ator: str | None, status_novo: str
    ) -> None:
        """Emula `requisicoes_validar_motorista` (migration 20261111120100). `ator` é o valor de
        `motorista_alterado_por` enviado no UPDATE (None = não enviado)."""
        muda = "motorista_id" in campos and campos["motorista_id"] != r.get("motorista_id")
        if not muda:
            if ator is not None:
                raise erro_db("AG004", "Responsável pelo motorista informado sem mudança.")
            return
        if status_novo not in {"autorizada", "em_uso"}:
            raise erro_db("AG032", "Motorista só com a requisição autorizada ou em uso.")
        quem = self.perfis.get(ator or "")
        if quem is None or not quem.ativo or quem.role not in {"autorizador", "admin"}:
            raise erro_db("AG002", "Apenas autorizador ou admin ativo pode definir o motorista.")
        novo = campos["motorista_id"]
        if novo is not None:
            if not self.config.motorista:
                raise erro_db("AG033", "O Perfil Motorista está desligado.")
            alvo = self.perfis.get(novo)
            if alvo is None or not alvo.ativo or alvo.role != "motorista":
                raise erro_db("AG031", "Motorista inexistente, inativo ou sem o perfil.")
        self.eventos.append(
            {
                "tabela": "requisicoes",
                "registro_id": r["id"],
                "acao": "motorista_designado",
                "ator_id": ator,
                "de": r.get("motorista_id"),
                "para": novo,
                "detalhes": {"recurso": "motorista", "status": status_novo},
            }
        )

    # -- RPC do motorista (migration 20261112120200): localização + transição numa transação ----
    def registrar_transicao_motorista(
        self,
        *,
        requisicao_id: str,
        momento: str,
        ator_id: str,
        latitude: float | None,
        longitude: float | None,
        precisao_m: float | None,
        km_saida: int | None,
        km_chegada: int | None,
    ) -> dict[str, Any]:
        self.chamadas_rpc_motorista.append(
            {
                "requisicao_id": requisicao_id,
                "momento": momento,
                "ator_id": ator_id,
                "latitude": latitude,
                "longitude": longitude,
                "precisao_m": precisao_m,
                "km_saida": km_saida,
                "km_chegada": km_chegada,
            }
        )
        with self._lock:
            if momento not in {"saida", "chegada"}:
                raise erro_db("AG037", "Momento inválido.")
            r = self.requisicoes.get(requisicao_id)
            if r is None:
                raise erro_db("AG037", "Requisição inexistente.")
            p = self.perfis.get(ator_id)
            if p is None or not p.ativo or p.role != "motorista":
                raise erro_db("AG002", "Apenas motorista ativo usa este registro.")
            if not (self.config.motorista and self.config.motorista_registra):
                raise erro_db("AG034", "Registro pelo motorista desligado.")
            if r.get("motorista_id") != ator_id:
                raise erro_db("AG035", "Somente o motorista designado registra.")
            esperado = {"saida": "autorizada", "chegada": "em_uso"}[momento]
            if r["status"] != esperado:
                raise erro_db("AG001", "Momento incompatível com o status.")

            antes = (len(self.localizacoes), len(self.eventos))  # início da "transação"
            if self.config.motorista_localizacao:
                self._validar_coordenadas_rpc(latitude, longitude, precisao_m)
                self.localizacoes.append(
                    {
                        "requisicao_id": requisicao_id,
                        "momento": momento,
                        "latitude": latitude,
                        "longitude": longitude,
                        "precisao_m": precisao_m,
                        "registrado_por": ator_id,
                        "registrado_em": datetime.now(UTC).isoformat(),
                    }
                )
                # Auditoria do banco: sem latitude, longitude nem precisão.
                self.eventos.append(
                    {
                        "tabela": "requisicoes",
                        "registro_id": requisicao_id,
                        "acao": "localizacao_registrada",
                        "ator_id": ator_id,
                        "de": None,
                        "para": None,
                        "detalhes": {"recurso": "motorista_localizacao", "momento": momento},
                    }
                )
            campos: dict[str, Any] = (
                {"status": "em_uso", "saida_registrada_por": ator_id}
                if momento == "saida"
                else {"status": "concluida", "chegada_registrada_por": ator_id}
            )
            if km_saida is not None:
                campos["km_saida"] = km_saida
            if km_chegada is not None:
                campos["km_chegada"] = km_chegada
            try:
                atualizada = self._transicionar(requisicao_id, esperado, campos)
            except Exception:
                del self.localizacoes[antes[0] :], self.eventos[antes[1] :]  # ROLLBACK
                raise
            assert atualizada is not None
            return {
                "status": atualizada["status"],
                "localizacao_gravada": self.config.motorista_localizacao,
            }

    @staticmethod
    def _validar_coordenadas_rpc(
        latitude: float | None, longitude: float | None, precisao_m: float | None
    ) -> None:
        def ruim(v: float | None, lim: float) -> bool:
            return v is None or isinstance(v, bool) or not math.isfinite(v) or abs(v) > lim

        if ruim(latitude, 90) or ruim(longitude, 180):
            raise erro_db("AG036", "Coordenadas inválidas.")
        if precisao_m is not None and (
            not math.isfinite(precisao_m) or precisao_m < 0 or precisao_m > 999_999
        ):
            raise erro_db("AG036", "Precisão inválida.")

    def listar_localizacoes(self, requisicao_id: str) -> list[dict[str, Any]]:
        return [dict(x) for x in self.localizacoes if x["requisicao_id"] == requisicao_id]

    @staticmethod
    def _ck(nome: str) -> Exception:
        return erro_db("23514", f'violates check constraint "{nome}"')

    def _lancamento_sem_km(self, r: Requisicao, campos: dict[str, Any]) -> bool:
        """KM obrigatório por papel do ator (migration 20261119120200): vigilante com
        `vigilante_km`; autorizador/admin com `autorizador_km`; motorista e demais, nunca."""
        ator = self.perfis.get(
            str(campos.get("saida_registrada_por") or campos.get("chegada_registrada_por") or "")
        )
        if ator is None:
            return False
        if ator.role == "vigilante":
            return not self.config.vigilante_km
        if ator.role in {"autorizador", "admin"}:
            return not self.config.autorizador_km
        return True

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
        if self.config.quilometragem and not self._lancamento_sem_km(r, campos):
            if novo["status"] == "em_uso" and novo["km_saida"] is None:
                raise erro_db("AG012", "Quilometragem de saída é obrigatória.")
            if novo["status"] == "concluida" and len(km) < 2:
                raise erro_db("AG012", "Quilometragem de saída e de chegada é obrigatória.")
