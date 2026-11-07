"""Regra de negócio das requisições: máquina de estados, permissões e anti-IDOR.

O banco repete estas regras (triggers) e registra a auditoria de cada transição; aqui
elas são aplicadas antes, para devolver mensagens claras e `acoes_permitidas`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.errors import (
    ChecklistObrigatorio,
    NaoEncontrado,
    RequisicaoInvalida,
    SemPermissao,
    TransicaoInvalida,
    VeiculoIndisponivel,
    VeiculoOcupado,
)
from app.modelos import Configuracoes, Perfil, Requisicao
from app.notificacoes import Notificador, NotificadorNulo
from app.repositorios.base import Repositorio
from app.services.validacao import validar_criacao, validar_km, validar_uuid

STAFF = frozenset({"autorizador", "admin"})
STATUS_VALIDOS = frozenset(
    {"solicitada", "autorizada", "em_uso", "concluida", "negada", "cancelada"}
)
MSG_OCUPADO = (
    "Este veículo já possui uma requisição em aberto. "
    "Ele só aceita novo pedido após a conclusão, negativa ou cancelamento da anterior."
)


@dataclass(frozen=True)
class Transicao:
    origem: frozenset[str]
    destino: str
    campo_ator: str
    mensagem_estado: str


# Máquina de estados (espelha o trigger do banco).
TRANSICOES: dict[str, Transicao] = {
    "autorizar": Transicao(
        frozenset({"solicitada"}),
        "autorizada",
        "autorizado_por",
        "Só é possível autorizar uma requisição solicitada.",
    ),
    "negar": Transicao(
        frozenset({"solicitada"}),
        "negada",
        "autorizado_por",
        "Só é possível negar uma requisição solicitada.",
    ),
    "registrar_saida": Transicao(
        frozenset({"autorizada"}),
        "em_uso",
        "saida_registrada_por",
        "A saída só pode ser registrada em requisição autorizada.",
    ),
    "registrar_chegada": Transicao(
        frozenset({"em_uso"}),
        "concluida",
        "chegada_registrada_por",
        "A chegada só pode ser registrada em requisição em uso.",
    ),
    "cancelar": Transicao(
        frozenset({"solicitada", "autorizada"}),
        "cancelada",
        "cancelada_por",
        "Esta requisição não pode mais ser cancelada.",
    ),
}


EVENTO_POR_ACAO = {
    "autorizar": "requisicao.autorizada",
    "negar": "requisicao.negada",
    "registrar_saida": "requisicao.saida",
    "registrar_chegada": "requisicao.chegada",
    "cancelar": "requisicao.cancelada",
}

CAMPOS_ATORES = ("autorizado_por", "saida_registrada_por", "chegada_registrada_por")
# Quem fez cada evento do histórico (campo `por`): inclui o dono (Solicitada) e quem cancelou.
CAMPOS_AUTORES_HISTORICO = ("usuario_id", *CAMPOS_ATORES, "cancelada_por")


def montar_historico(req: Requisicao, nomes: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Linha do tempo montada no servidor; só entram os eventos que já ocorreram.

    `por` é o nome (só `perfis.nome`) de quem fez o evento; None se desconhecido.
    """
    nomes = nomes or {}
    eventos = [
        ("Solicitada", req.get("criado_em"), req.get("usuario_id"), None),
        (
            "Negada" if req.get("status") == "negada" else "Autorizada",
            req.get("autorizado_em"),
            req.get("autorizado_por"),
            None,
        ),
        (
            "Saída registrada",
            req.get("saida_real_em"),
            req.get("saida_registrada_por"),
            req.get("km_saida"),
        ),
        (
            "Chegada registrada",
            req.get("chegada_real_em"),
            req.get("chegada_registrada_por"),
            req.get("km_chegada"),
        ),
        ("Cancelada", req.get("cancelada_em"), req.get("cancelada_por"), None),
    ]
    # `km` (odômetro do veículo, não é dado pessoal) só aparece quando gravado.
    return [
        {"rotulo": r, "quando": q, "por": nomes.get(a or ""), **({} if km is None else {"km": km})}
        for r, q, a, km in eventos
        if q
    ]


class ServicoRequisicoes:
    def __init__(
        self,
        repo: Repositorio,
        *,
        permitir_autoautorizacao: bool = False,
        notificador: Notificador | None = None,
    ) -> None:
        self._repo = repo
        self._notificador: Notificador = notificador or NotificadorNulo()
        self._autoauto = permitir_autoautorizacao

    # -- permissões -----------------------------------------------------------------
    def _pode(self, acao: str, perfil: Perfil, req: Requisicao) -> bool:
        t = TRANSICOES[acao]
        if req["status"] not in t.origem:
            return False
        if acao == "cancelar":
            if perfil.role in STAFF:
                return True
            # Solicitante: só a própria e só enquanto solicitada.
            return req["usuario_id"] == perfil.id and req["status"] == "solicitada"
        if perfil.role not in STAFF:
            return False
        if acao in {"autorizar", "negar"} and not self._autoauto:
            return req["usuario_id"] != perfil.id
        return True

    def acoes_permitidas(self, perfil: Perfil, req: Requisicao) -> list[str]:
        return [a for a in TRANSICOES if self._pode(a, perfil, req)]

    def campos_acao(
        self,
        perfil: Perfil,
        req: Requisicao,
        config: Configuracoes,
        checklists: dict[str, set[str]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Campos extras que o front deve pedir em cada ação permitida (decididos aqui).

        `checklist_pendente`: momento cujo checklist ainda precisa ser registrado antes da ação
        (só com o recurso ligado). Na chegada só se exige o checklist de chegada.
        """
        if not config.quilometragem and not config.checklist:
            return {}
        feitos = (checklists or {}).get(req["id"], set())
        saida: dict[str, dict[str, Any]] = {}
        for acao, momento in (("registrar_saida", "saida"), ("registrar_chegada", "chegada")):
            if not self._pode(acao, perfil, req):
                continue
            campos: list[str] = []
            if config.quilometragem:
                if acao == "registrar_saida":
                    campos = ["km_saida"]
                else:
                    campos = (
                        ["km_chegada"]
                        if req.get("km_saida") is not None
                        else [
                            "km_saida",
                            "km_chegada",
                        ]
                    )
            entrada: dict[str, Any] = {"campos_obrigatorios": campos}
            if config.checklist and momento not in feitos:
                entrada["checklist_pendente"] = momento
            saida[acao] = entrada
        return saida

    def apresentar(self, perfil: Perfil, req: Requisicao) -> dict[str, Any]:
        return self.apresentar_lote(perfil, [req])[0]

    def apresentar_lote(self, perfil: Perfil, reqs: list[Requisicao]) -> list[dict[str, Any]]:
        """Acrescenta ações, histórico, nomes (só `perfis.nome`) e descrição do veículo."""
        ids_perfis = {r[c] for r in reqs for c in CAMPOS_AUTORES_HISTORICO if r.get(c)}
        ids_veiculos = {r["veiculo_id"] for r in reqs if r.get("veiculo_id")}
        nomes = self._repo.nomes_perfis(ids_perfis)
        veiculos = self._repo.descricoes_veiculos(ids_veiculos)
        config = self._repo.ler_configuracoes()
        checklists = (
            self._repo.momentos_checklist({r["id"] for r in reqs}) if config.checklist else {}
        )
        return [
            {
                **r,
                # Sempre presentes (null se nunca gravados): desligar o recurso não esconde
                # o que já foi gravado.
                "km_saida": r.get("km_saida"),
                "km_chegada": r.get("km_chegada"),
                "acoes_permitidas": self.acoes_permitidas(perfil, r),
                "campos_acao": self.campos_acao(perfil, r, config, checklists),
                "historico": montar_historico(r, nomes),
                "autorizado_por_nome": nomes.get(r.get("autorizado_por") or ""),
                "saida_registrada_por_nome": nomes.get(r.get("saida_registrada_por") or ""),
                "chegada_registrada_por_nome": nomes.get(r.get("chegada_registrada_por") or ""),
                "veiculo_descricao": veiculos.get(r.get("veiculo_id") or ""),
            }
            for r in reqs
        ]

    # -- leitura (anti-IDOR) --------------------------------------------------------
    def obter(self, perfil: Perfil, requisicao_id: str) -> Requisicao:
        req = self._repo.obter_requisicao(validar_uuid(requisicao_id))
        # 404 (e não 403) para não revelar a existência de requisição alheia.
        if req is None or (perfil.role not in STAFF and req["usuario_id"] != perfil.id):
            raise NaoEncontrado("Requisição não encontrada.")
        return req

    def listar(
        self, perfil: Perfil, *, status: str | None, limite: int, offset: int
    ) -> dict[str, Any]:
        if status is not None and status not in STATUS_VALIDOS:
            raise RequisicaoInvalida("Status inválido.")
        limite = max(1, min(limite, 100))
        offset = max(0, offset)
        dono = None if perfil.role in STAFF else perfil.id
        itens = self._repo.listar_requisicoes(
            usuario_id=dono, status=status, limite=limite, offset=offset
        )
        total = self._repo.contar_requisicoes(usuario_id=dono, status=status)
        return {"itens": self.apresentar_lote(perfil, itens), "total": total}

    # -- escrita --------------------------------------------------------------------
    def criar(self, perfil: Perfil, corpo: Any) -> dict[str, Any]:
        dados = validar_criacao(corpo)
        veiculo = self._repo.obter_veiculo(dados["veiculo_id"])
        if veiculo is None or not veiculo.get("ativo", True):
            raise VeiculoIndisponivel("Veículo inexistente ou inativo.")
        # Checagem amigável; quem decide de fato é o índice único parcial do banco.
        if self._repo.veiculo_ocupado(dados["veiculo_id"]):
            raise VeiculoOcupado(MSG_OCUPADO)
        dados["usuario_id"] = perfil.id  # sempre do JWT, nunca do body
        criada = self._repo.inserir_requisicao(dados)
        self._notificador.notificar("requisicao.criada", criada)  # só após o banco confirmar
        return self.apresentar(perfil, criada)

    def executar(
        self, acao: str, perfil: Perfil, requisicao_id: str, corpo: Any = None
    ) -> dict[str, Any]:
        t = TRANSICOES[acao]
        req = self.obter(perfil, requisicao_id)  # anti-IDOR antes de qualquer outra coisa
        if req["status"] not in t.origem:
            raise TransicaoInvalida(t.mensagem_estado)
        if not self._pode(acao, perfil, req):
            raise SemPermissao(self._mensagem_negada(acao, perfil, req))
        self._exigir_checklist(acao, req)
        campos = {"status": t.destino, t.campo_ator: perfil.id, **self._km(acao, req, corpo)}
        atualizada = self._repo.transicionar(req["id"], req["status"], campos)
        if atualizada is None:  # outra requisição mudou o estado no meio do caminho
            raise TransicaoInvalida("A requisição foi alterada por outra pessoa. Atualize a tela.")
        self._notificador.notificar(EVENTO_POR_ACAO[acao], atualizada)  # após o commit
        return self.apresentar(perfil, atualizada)

    def _exigir_checklist(self, acao: str, req: Requisicao) -> None:
        """Recurso ligado: o checklist do momento precisa existir antes da saída/chegada.

        Desligado: nada é exigido. Viagem já em uso sem checklist de saída: a chegada só exige
        o de chegada (o banco repete a regra: AG013).
        """
        momento = {"registrar_saida": "saida", "registrar_chegada": "chegada"}.get(acao)
        if momento is None or not self._repo.ler_configuracoes().checklist:
            return
        if momento not in self._repo.momentos_checklist({req["id"]}).get(req["id"], set()):
            rotulo = "saída" if momento == "saida" else "chegada"
            raise ChecklistObrigatorio(
                f"O checklist de {rotulo} é obrigatório antes de registrar a {rotulo}."
            )

    def _km(self, acao: str, req: Requisicao, corpo: Any) -> dict[str, int]:
        """Km da transição. Recurso desligado: o corpo é ignorado (nada gravado, sem erro).

        Ligado: saída exige `km_saida`; chegada exige `km_chegada` e, se a saída ficou sem km
        (recurso ligado com a viagem em curso), também `km_saida`. Km já gravado é imutável:
        um `km_saida` enviado na chegada é ignorado quando a saída já o tem.
        """
        if acao not in {"registrar_saida", "registrar_chegada"}:
            return {}
        if not self._repo.ler_configuracoes().quilometragem:
            return {}
        if corpo is None:
            corpo = {}
        if not isinstance(corpo, dict):
            raise RequisicaoInvalida("Envie um objeto JSON.")
        if acao == "registrar_saida":
            return {"km_saida": validar_km(corpo.get("km_saida"), "km_saida")}
        saida = req.get("km_saida")
        extra: dict[str, int] = {}
        if saida is None:
            if corpo.get("km_saida") is None or corpo.get("km_chegada") is None:
                raise RequisicaoInvalida(
                    "A saída desta viagem foi registrada sem km. Envie km_saida e km_chegada "
                    "juntos ao registrar a chegada."
                )
            saida = extra["km_saida"] = validar_km(corpo.get("km_saida"), "km_saida")
        chegada = validar_km(corpo.get("km_chegada"), "km_chegada")
        if chegada < saida:
            raise RequisicaoInvalida("O km de chegada não pode ser menor que o km de saída.")
        return {**extra, "km_chegada": chegada}

    def _mensagem_negada(self, acao: str, perfil: Perfil, req: Requisicao) -> str:
        if (
            acao in {"autorizar", "negar"}
            and perfil.role in STAFF
            and req["usuario_id"] == perfil.id
        ):
            return "Você não pode decidir sobre a sua própria requisição."
        if acao == "cancelar":
            return "Você só pode cancelar a sua própria requisição enquanto estiver solicitada."
        return "Você não tem permissão para esta ação."
