"""Regra de negócio das requisições: máquina de estados, permissões e anti-IDOR.

O banco repete estas regras (triggers) e registra a auditoria de cada transição; aqui
elas são aplicadas antes, para devolver mensagens claras e `acoes_permitidas`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.errors import (
    ChecklistObrigatorio,
    NaoEncontrado,
    RecursoDesligado,
    RequisicaoInvalida,
    SemPermissao,
    TransicaoInvalida,
    VeiculoIndisponivel,
    VeiculoOcupado,
)
from app.modelos import Configuracoes, Perfil, Requisicao
from app.notificacoes import Notificador, NotificadorNulo
from app.repositorios.base import Repositorio
from app.services.fotos_exibicao import FotosExibicao
from app.services.localizacao import validar_localizacao
from app.services.validacao import validar_criacao, validar_km, validar_uuid

STAFF = frozenset({"autorizador", "admin"})
STATUS_VALIDOS = frozenset(
    {"solicitada", "autorizada", "em_uso", "concluida", "negada", "cancelada"}
)
MSG_MOTORISTA_DESLIGADO = "O perfil Motorista está desligado nas Configurações."
MSG_MOTORISTA_INVALIDO = (
    "O motorista informado não existe, está inativo ou não tem o perfil motorista."
)
ESTADOS_ATRIBUICAO = ("autorizada", "em_uso")  # onde o motorista pode ser atribuído ou trocado
ACOES_MOTORISTA = frozenset({"registrar_saida", "registrar_chegada"})
# Vigilante: registra saída/chegada de QUALQUER requisição nestes estados (e só isso).
ESTADOS_VIGILANTE = ("autorizada", "em_uso")
# Arquivar é uma preferência PESSOAL (tabela `requisicoes_arquivadas`): só estados terminais.
ESTADOS_ARQUIVAVEIS = frozenset({"concluida", "negada", "cancelada"})
MSG_NAO_ARQUIVAVEL = (
    "Só é possível arquivar ou desarquivar uma requisição concluída, negada ou cancelada. "
    "Requisições em aberto não podem ser arquivadas."
)
LIMITE_SELETOR = 200  # motoristas ativos devolvidos para o seletor de quem autoriza
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


# Categoria de cada ação (fonte única; o front só agrupa e ordena, nunca decide). `cancelar` é
# "decidir" para a equipe e "solicitar" para o dono sem papel de equipe (ver `_categoria`).
CATEGORIA_ACAO: dict[str, str] = {
    "cancelar": "decidir",
    "autorizar": "decidir",
    "negar": "decidir",
    "registrar_saida": "operar",
    "registrar_chegada": "operar",
    "atribuir_motorista": "designar",
    "ver_checklist": "consultar",
    "ver_localizacao": "consultar",
    "arquivar": "organizar",
    "desarquivar": "organizar",
}
ORDEM_CATEGORIAS = ("solicitar", "decidir", "operar", "designar", "consultar", "organizar")
ESTADOS_CHECKLIST = ("autorizada", "em_uso", "concluida")

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


def montar_historico(
    req: Requisicao, nomes: dict[str, str] | None = None, *, incluir_por: bool = True
) -> list[dict[str, Any]]:
    """Linha do tempo montada no servidor; só entram os eventos que já ocorreram.

    `por` é o nome (só `perfis.nome`) de quem fez o evento; None se desconhecido. Visão do
    solicitante (`incluir_por=False`): o campo `por` não é devolvido.
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
        {
            "rotulo": r,
            "quando": q,
            **({"por": nomes.get(a or "")} if incluir_por else {}),
            **({} if km is None else {"km": km}),
        }
        for r, q, a, km in eventos
        if q
    ]


ETAPAS_FLUXO = ("solicitada", "autorizada", "em_uso", "concluida")
ROTULOS_ETAPA = {
    "solicitada": "Solicitada",
    "autorizada": "Autorizada",
    "em_uso": "Em uso",
    "concluida": "Concluída",
    "negada": "Negada",
    "cancelada": "Cancelada",
}
PAPEL_EQUIPE = "Autorizador"  # visão do solicitante: só o papel, nunca o nome da equipe
# chave da etapa -> (coluna do carimbo, coluna de quem agiu)
FONTES_ETAPA = {
    "solicitada": ("criado_em", "usuario_id"),
    "autorizada": ("autorizado_em", "autorizado_por"),
    "negada": ("autorizado_em", "autorizado_por"),
    "em_uso": ("saida_real_em", "saida_registrada_por"),
    "concluida": ("chegada_real_em", "chegada_registrada_por"),
    "cancelada": ("cancelada_em", "cancelada_por"),
}


def _instante(valor: Any) -> datetime | None:
    if not isinstance(valor, str):
        return None
    try:
        dt = datetime.fromisoformat(valor)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _segundos(inicio: datetime | None, fim: datetime | None) -> int | None:
    if inicio is None or fim is None:
        return None
    return max(0, int((fim - inicio).total_seconds()))


def montar_linha_do_tempo(
    req: Requisicao,
    nomes: dict[str, str] | None = None,
    *,
    ocultar_nomes: bool = False,
    agora: datetime | None = None,
) -> dict[str, Any]:
    """Linha do tempo por etapas, montada no servidor a partir das colunas de `requisicoes`.

    Em andamento: 4 etapas (solicitada, autorizada, em_uso, concluida); a etapa do estado atual é
    `atual`, as seguintes `pendente`. Negada e cancelada encerram a linha (sem etapas futuras).
    `ocultar_nomes` (visão do solicitante): só o nome do próprio dono; a equipe aparece como papel.
    """
    nomes = nomes or {}
    agora = agora or datetime.now(UTC)
    status = req.get("status")
    if status == "negada":
        chaves = ["solicitada", "negada"]
    elif status == "cancelada":
        chaves = ["solicitada", *(["autorizada"] if req.get("autorizado_em") else []), "cancelada"]
    else:
        chaves = list(ETAPAS_FLUXO)
    encerrada = status in {"negada", "cancelada", "concluida"}
    indice_atual = chaves.index(status) if status in chaves and not encerrada else -1

    etapas: list[dict[str, Any]] = []
    anterior: datetime | None = None
    for i, chave in enumerate(chaves):
        pendente = not encerrada and i > indice_atual
        if pendente:
            situacao = "pendente"
        elif i == indice_atual:
            situacao = "atual"
        elif chave in {"negada", "cancelada"}:
            situacao = chave
        else:
            situacao = "concluida"
        etapa: dict[str, Any] = {
            "chave": chave,
            "rotulo": ROTULOS_ETAPA[chave],
            "situacao": situacao,
            "em": None,
            "por": None,
            "duracao_desde_anterior_s": None,
            "decorrido_s": None,
        }
        if not pendente:
            col_em, col_por = FONTES_ETAPA[chave]
            etapa["em"] = req.get(col_em)
            etapa["por"] = _autor(req.get(col_por), req, nomes, ocultar_nomes)
            em = _instante(req.get(col_em))
            etapa["duracao_desde_anterior_s"] = _segundos(anterior, em)
            if situacao == "atual":
                etapa["decorrido_s"] = _segundos(em, agora)
            anterior = em or anterior
        etapas.append(etapa)

    etapa_atual = len(etapas) if encerrada else indice_atual + 1
    rotulo = ROTULOS_ETAPA.get(str(status), str(status)).lower()
    return {
        "desfecho": status if encerrada else "em_andamento",
        "etapa_atual": etapa_atual,
        "total_etapas": len(etapas),
        "texto_equivalente": f"Etapa {etapa_atual} de {len(etapas)}: {rotulo}",
        "gerado_em": agora.isoformat(),
        "etapas": etapas,
    }


def _autor(
    ator: Any, req: Requisicao, nomes: dict[str, str], ocultar_nomes: bool
) -> dict[str, str] | None:
    if not ator:
        return None
    if ocultar_nomes and ator != req.get("usuario_id"):
        # Sem nome da equipe; o motorista designado aparece como tal (id já conhecido da viagem).
        return {"papel": "Motorista" if ator == req.get("motorista_id") else PAPEL_EQUIPE}
    nome = nomes.get(ator)
    return {"nome": nome} if nome else None


class ServicoRequisicoes:
    def __init__(
        self,
        repo: Repositorio,
        *,
        notificador: Notificador | None = None,
        fotos: FotosExibicao | None = None,
    ) -> None:
        self._repo = repo
        self._fotos = fotos
        self._notificador: Notificador = notificador or NotificadorNulo()

    # -- permissões -----------------------------------------------------------------
    def _pode(self, acao: str, perfil: Perfil, req: Requisicao, config: Configuracoes) -> bool:
        t = TRANSICOES[acao]
        if req["status"] not in t.origem:
            return False
        if perfil.role == "vigilante":
            # Registra saída/chegada de qualquer autorizada/em_uso; nunca autoriza, nega
            # nem cancela (a origem do estado já foi checada acima).
            return acao in ACOES_MOTORISTA
        if perfil.role == "motorista":
            # Só registra saída/chegada das PRÓPRIAS viagens e só com o recurso ligado.
            return (
                acao in ACOES_MOTORISTA
                and config.motorista
                and config.motorista_registra
                and req.get("motorista_id") == perfil.id
            )
        if acao == "cancelar":
            if perfil.role in STAFF:
                return True
            # Solicitante: só a própria e só enquanto solicitada.
            return req["usuario_id"] == perfil.id and req["status"] == "solicitada"
        if perfil.role not in STAFF:
            return False
        if acao in {"autorizar", "negar"} and req["usuario_id"] == perfil.id:
            return config.autorizador_decide_propria  # flag das Configurações (padrão: desligada)
        return True

    def acoes_permitidas(
        self,
        perfil: Perfil,
        req: Requisicao,
        config: Configuracoes | None = None,
        arquivadas: set[str] | None = None,
    ) -> list[str]:
        """Ações da requisição para este usuário. `arquivadas`: ids que ele já arquivou (lote);
        None consulta o banco só para o caso necessário."""
        config = config or self._repo.ler_configuracoes()
        acoes = [a for a in TRANSICOES if self._pode(a, perfil, req, config)]
        if self._pode_atribuir(perfil, req, config):
            acoes.append("atribuir_motorista")
        if self._pode_ver_checklist(perfil, req, config):
            acoes.append("ver_checklist")
        if perfil.role == "admin" and req.get("saida_real_em"):
            acoes.append("ver_localizacao")  # só o admin lê as coordenadas (LGPD)
        if self._pode_arquivar(perfil, req, config):
            if arquivadas is None:
                arquivadas = self._repo.ids_arquivados(perfil.id, {req["id"]})
            acoes.append("desarquivar" if req["id"] in arquivadas else "arquivar")
        return acoes

    @staticmethod
    def _pode_ver_checklist(perfil: Perfil, req: Requisicao, config: Configuracoes) -> bool:
        if not config.checklist or req["status"] not in ESTADOS_CHECKLIST:
            return False
        if perfil.role in STAFF:
            return True
        return perfil.role == "motorista" and req.get("motorista_id") == perfil.id

    @staticmethod
    def _pode_arquivar(perfil: Perfil, req: Requisicao, config: Configuracoes) -> bool:
        """Concluída/negada/cancelada, nunca aberta. Solicitante: a própria; equipe: qualquer;
        motorista: só as atribuídas a ele, com o recurso ligado; vigilante: nunca."""
        if req["status"] not in ESTADOS_ARQUIVAVEIS:
            return False
        if perfil.role in STAFF:
            return True
        if perfil.role == "solicitante":
            return req["usuario_id"] == perfil.id
        if perfil.role == "motorista":
            return config.motorista and req.get("motorista_id") == perfil.id
        return False

    @staticmethod
    def _categoria(acao: str, perfil: Perfil) -> str:
        if acao == "cancelar" and perfil.role not in STAFF:
            return "solicitar"
        return CATEGORIA_ACAO[acao]

    def grupos_acao(self, perfil: Perfil, acoes: list[str]) -> dict[str, list[str]]:
        """Ações agrupadas por categoria, na ordem de exibição; só categorias não vazias."""
        por_categoria: dict[str, list[str]] = {}
        for a in acoes:
            por_categoria.setdefault(self._categoria(a, perfil), []).append(a)
        return {c: por_categoria[c] for c in ORDEM_CATEGORIAS if c in por_categoria}

    @staticmethod
    def _pode_atribuir(perfil: Perfil, req: Requisicao, config: Configuracoes) -> bool:
        return config.motorista and perfil.role in STAFF and req["status"] in ESTADOS_ATRIBUICAO

    def campos_acao(
        self,
        perfil: Perfil,
        req: Requisicao,
        config: Configuracoes,
        checklists: dict[str, set[str]] | None = None,
        seletor: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Campos extras que o front deve pedir em cada ação permitida (decididos aqui).

        `checklist_pendente`: momento cujo checklist ainda precisa ser registrado antes da ação
        (só com o recurso ligado). Na chegada só se exige o checklist de chegada.
        `seletor` (motoristas ativos e o padrão) acompanha `autorizar` com o recurso ligado.
        """
        saida: dict[str, dict[str, Any]] = {}
        if config.motorista and self._pode("autorizar", perfil, req, config):
            saida["autorizar"] = {
                "campos_obrigatorios": [],
                "campos_opcionais": ["motorista_id"],
                **({} if seletor is None else seletor),
            }
        # Localização: só o motorista a informa (autorizador/admin registrando não coleta).
        coleta = perfil.role == "motorista" and config.motorista_localizacao
        pede_km = self._pede_km(perfil, config)
        if not pede_km and not config.checklist and not coleta:
            return saida
        feitos = (checklists or {}).get(req["id"], set())
        for acao, momento in (("registrar_saida", "saida"), ("registrar_chegada", "chegada")):
            if not self._pode(acao, perfil, req, config):
                continue
            campos: list[str] = []
            if pede_km:
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
            if coleta:
                entrada["campos_obrigatorios"] = [*campos, "latitude", "longitude"]
                entrada["campos_opcionais"] = ["precisao_m"]
            if config.checklist and momento not in feitos:
                entrada["checklist_pendente"] = momento
            saida[acao] = entrada
        return saida

    @staticmethod
    def _pede_km(perfil: Perfil, config: Configuracoes) -> bool:
        """Km por perfil, sempre com a Quilometragem ligada: vigilante com `vigilante_km`;
        autorizador/admin com `autorizador_km`; motorista e solicitante nunca lançam km."""
        if not config.quilometragem:
            return False
        if perfil.role == "vigilante":
            return config.vigilante_km
        if perfil.role in STAFF:
            return config.autorizador_km
        return False

    def apresentar(self, perfil: Perfil, req: Requisicao) -> dict[str, Any]:
        return self.apresentar_lote(perfil, [req])[0]

    def apresentar_lote(self, perfil: Perfil, reqs: list[Requisicao]) -> list[dict[str, Any]]:
        """Acrescenta ações, histórico, nomes (só `perfis.nome`) e descrição do veículo."""
        staff = perfil.role in STAFF
        # Solicitante: só o próprio dono (e ele mesmo como ator) é consultado; nomes, fotos e ids
        # da equipe nem são buscados (minimização).
        ids_perfis = {
            r[c]
            for r in reqs
            for c in CAMPOS_AUTORES_HISTORICO
            if r.get(c) and (staff or r[c] == r.get("usuario_id"))
        }
        ids_veiculos = {r["veiculo_id"] for r in reqs if r.get("veiculo_id")}
        ids_motoristas = {r["motorista_id"] for r in reqs if r.get("motorista_id")}
        nomes = self._repo.nomes_perfis(ids_perfis)
        nomes_motoristas = self._repo.nomes_perfis(ids_motoristas) if ids_motoristas else {}
        veiculos = self._repo.descricoes_veiculos(ids_veiculos)
        # Fotos só de quem aparece nestas requisições (já filtradas por dono/role); inativo e
        # falha do Storage resultam em null, nunca em erro.
        fotos = self._fotos.urls(ids_perfis) if self._fotos is not None else {}
        config = self._repo.ler_configuracoes()
        agora = datetime.now(UTC)
        checklists = (
            self._repo.momentos_checklist({r["id"] for r in reqs}) if config.checklist else {}
        )
        arquivadas = self._arquivadas_do_usuario(perfil, reqs, config)
        seletor = self._seletor_para_autorizar(perfil, reqs, config)
        return [
            {
                **self._sem_equipe(r, staff, perfil),
                # Sempre presentes (null se nunca gravados): desligar o recurso não esconde
                # o que já foi gravado.
                "km_saida": r.get("km_saida"),
                "km_chegada": r.get("km_chegada"),
                **self._acoes(perfil, r, config, arquivadas),
                # Preferência pessoal de quem consulta (false para quem não pode arquivar).
                "arquivada": r["id"] in arquivadas,
                # Só id e nome (nunca e-mail nem unidade); null enquanto não atribuído.
                "motorista": self._motorista(r, nomes_motoristas),
                "campos_acao": self.campos_acao(perfil, r, config, checklists, seletor),
                "historico": montar_historico(r, nomes, incluir_por=staff),
                "linha_do_tempo": montar_linha_do_tempo(
                    r, nomes, ocultar_nomes=not staff, agora=agora
                ),
                "veiculo_descricao": veiculos.get(r.get("veiculo_id") or ""),
                # Só a equipe e o próprio dono veem a foto do solicitante (minimização, LGPD).
                "solicitante_foto_url": (
                    fotos.get(r.get("usuario_id") or "")
                    if staff or r.get("usuario_id") == perfil.id
                    else None
                ),
                **self._autores(r, staff, nomes, fotos),
            }
            for r in reqs
        ]

    def _acoes(
        self, perfil: Perfil, r: Requisicao, config: Configuracoes, arquivadas: set[str]
    ) -> dict[str, Any]:
        acoes = self.acoes_permitidas(perfil, r, config, arquivadas)
        return {"acoes_permitidas": acoes, "grupos_acao": self.grupos_acao(perfil, acoes)}

    def _arquivadas_do_usuario(
        self, perfil: Perfil, reqs: list[Requisicao], config: Configuracoes
    ) -> set[str]:
        """Ids do lote que o próprio usuário arquivou (uma consulta; só para quem pode arquivar)."""
        candidatos = {r["id"] for r in reqs if self._pode_arquivar(perfil, r, config)}
        return self._repo.ids_arquivados(perfil.id, candidatos) if candidatos else set()

    def _seletor_para_autorizar(
        self, perfil: Perfil, reqs: list[Requisicao], config: Configuracoes
    ) -> dict[str, Any] | None:
        """Motoristas ativos (id e nome) e o padrão, se alguma requisição puder ser autorizada."""
        if not config.motorista or not any(
            self._pode("autorizar", perfil, r, config) for r in reqs
        ):
            return None
        itens, padrao = self.seletor_motoristas()
        return {"motoristas": itens, "motorista_padrao": padrao}

    def seletor_motoristas(self) -> tuple[list[dict[str, Any]], str | None]:
        """Motoristas ATIVOS (só `id` e `nome`) e o id do único ativo (None se houver 0 ou 2+)."""
        perfis = self._repo.listar_perfis(
            role="motorista", ativo=True, limite=LIMITE_SELETOR, offset=0
        )
        itens = [{"id": p["id"], "nome": p["nome"]} for p in perfis]
        return itens, (str(perfis[0]["id"]) if len(perfis) == 1 else None)

    @staticmethod
    def _motorista(r: Requisicao, nomes: dict[str, str]) -> dict[str, Any] | None:
        mid = r.get("motorista_id")
        return {"id": mid, "nome": nomes.get(mid)} if mid else None

    @staticmethod
    def _sem_equipe(r: Requisicao, staff: bool, perfil: Perfil | None = None) -> Requisicao:
        """Visão do solicitante: ids de quem agiu só permanecem se for o próprio dono.

        Vigilante vendo requisição alheia: a observação (texto livre) não é devolvida.
        """
        if staff:
            return r
        visivel = {
            k: v
            for k, v in r.items()
            if k not in CAMPOS_ATORES + ("cancelada_por",) or v == r.get("usuario_id")
        }
        if perfil is not None and perfil.role == "vigilante" and r.get("usuario_id") != perfil.id:
            visivel.pop("observacao", None)
        return visivel

    @staticmethod
    def _autores(
        r: Requisicao, staff: bool, nomes: dict[str, str], fotos: dict[str, str | None]
    ) -> dict[str, Any]:
        """Nome e foto de quem autorizou/registrou. Solicitante: só se o autor é ele mesmo."""
        saida: dict[str, Any] = {}
        for campo in CAMPOS_ATORES:
            ator = r.get(campo) or ""
            if staff or (ator and ator == r.get("usuario_id")):
                saida[f"{campo}_nome"] = nomes.get(ator)
                saida[f"{campo}_foto_url"] = fotos.get(ator)
        return saida

    # -- leitura (anti-IDOR) --------------------------------------------------------
    def obter(self, perfil: Perfil, requisicao_id: str) -> Requisicao:
        req = self._repo.obter_requisicao(validar_uuid(requisicao_id))
        if perfil.role == "motorista":
            # Motorista: só as viagens atribuídas a ele, com o recurso ligado.
            self.exigir_motorista_ligado()
            if req is None or req.get("motorista_id") != perfil.id:
                raise NaoEncontrado("Viagem não encontrada.")
            return req
        if perfil.role == "vigilante" and req is not None and req["status"] in ESTADOS_VIGILANTE:
            return req  # vê para registrar saída/chegada (só autorizada/em_uso)
        # 404 (e não 403) para não revelar a existência de requisição alheia.
        if req is None or (perfil.role not in STAFF and req["usuario_id"] != perfil.id):
            raise NaoEncontrado("Requisição não encontrada.")
        return req

    def exigir_motorista_ligado(self) -> None:
        if not self._repo.ler_configuracoes().motorista:
            raise RecursoDesligado(MSG_MOTORISTA_DESLIGADO)

    def listar(
        self,
        perfil: Perfil,
        *,
        status: str | None,
        limite: int,
        offset: int,
        arquivadas: bool | None = False,
    ) -> dict[str, Any]:
        """`arquivadas`: False (padrão) oculta as que o usuário arquivou, True mostra só elas,
        None mostra todas. O arquivamento é pessoal e o `total` segue o mesmo filtro."""
        if status is not None and status not in STATUS_VALIDOS:
            raise RequisicaoInvalida("Status inválido.")
        limite = max(1, min(limite, 100))
        offset = max(0, offset)
        dono: str | None = None if perfil.role in STAFF else perfil.id
        motorista: str | None = None
        ou_status = ESTADOS_VIGILANTE if perfil.role == "vigilante" else None
        if perfil.role == "motorista":
            # O motorista nunca é dono: enxerga só as viagens atribuídas (filtro na consulta).
            self.exigir_motorista_ligado()
            dono, motorista = None, perfil.id
        # O vigilante não arquiva: sem junção (a lista dele nunca tem arquivadas).
        filtro_arquivo: dict[str, Any] = (
            {}
            if perfil.role == "vigilante"
            else {"arquivador_id": perfil.id, "arquivadas": arquivadas}
        )
        itens = self._repo.listar_requisicoes(
            usuario_id=dono,
            status=status,
            limite=limite,
            offset=offset,
            motorista_id=motorista,
            ou_status=ou_status,
            **filtro_arquivo,
        )
        total = self._repo.contar_requisicoes(
            usuario_id=dono,
            status=status,
            motorista_id=motorista,
            ou_status=ou_status,
            **filtro_arquivo,
        )
        return {"itens": self.apresentar_lote(perfil, itens), "total": total}

    # -- arquivamento pessoal -------------------------------------------------------
    def arquivar(self, perfil: Perfil, requisicao_id: str) -> dict[str, Any]:
        return self._alterar_arquivamento(perfil, requisicao_id, arquivar=True)

    def desarquivar(self, perfil: Perfil, requisicao_id: str) -> dict[str, Any]:
        return self._alterar_arquivamento(perfil, requisicao_id, arquivar=False)

    def _alterar_arquivamento(
        self, perfil: Perfil, requisicao_id: str, *, arquivar: bool
    ) -> dict[str, Any]:
        """Anti-IDOR primeiro (`obter`: 404 para o que o usuário não enxerga); idempotente.
        Só mexe em `requisicoes_arquivadas` do PRÓPRIO usuário do JWT."""
        req = self.obter(perfil, requisicao_id)
        config = self._repo.ler_configuracoes()
        if perfil.role == "vigilante":
            raise SemPermissao("Você não tem permissão para esta ação.")
        if req["status"] not in ESTADOS_ARQUIVAVEIS:
            raise TransicaoInvalida(MSG_NAO_ARQUIVAVEL)
        if not self._pode_arquivar(perfil, req, config):
            raise SemPermissao("Você não tem permissão para esta ação.")
        if arquivar:
            self._repo.arquivar_requisicao(perfil.id, req["id"])
        else:
            self._repo.desarquivar_requisicao(perfil.id, req["id"])
        return self.apresentar(perfil, req)

    # -- motorista ------------------------------------------------------------------
    def validar_motorista(self, valor: Any) -> str:
        """Id de um motorista ATIVO com role motorista (lido de `perfis`, nunca do cliente)."""
        mid = validar_uuid(valor, "motorista_id")
        alvo = self._repo.obter_perfil(mid)
        if alvo is None or not alvo.ativo or alvo.role != "motorista":
            raise RequisicaoInvalida(MSG_MOTORISTA_INVALIDO, "motorista_invalido")
        return mid

    def atribuir_motorista(self, perfil: Perfil, requisicao_id: str, corpo: Any) -> dict[str, Any]:
        """Atribui, troca (ou remove, com null) o motorista de requisição autorizada/em uso."""
        if perfil.role not in STAFF:
            raise SemPermissao("Você não tem permissão para esta ação.")
        self.exigir_motorista_ligado()
        req = self.obter(perfil, requisicao_id)
        if not isinstance(corpo, dict) or set(corpo) != {"motorista_id"}:
            raise RequisicaoInvalida("Envie um objeto JSON apenas com 'motorista_id'.")
        if req["status"] not in ESTADOS_ATRIBUICAO:
            raise TransicaoInvalida(
                "O motorista só pode ser atribuído a uma requisição autorizada ou em uso."
            )
        bruto = corpo["motorista_id"]
        alvo = None if bruto is None else self.validar_motorista(bruto)
        if alvo == req.get("motorista_id"):
            return self.apresentar(perfil, req)  # sem mudança: nada a gravar (o banco recusaria)
        # O ator (autorizador/admin do JWT) viaja no mesmo UPDATE: exigência do trigger do banco.
        atualizada = self._repo.atribuir_motorista(
            req["id"], alvo, list(ESTADOS_ATRIBUICAO), ator_id=perfil.id
        )
        if atualizada is None:
            raise TransicaoInvalida("A requisição foi alterada por outra pessoa. Atualize a tela.")
        return self.apresentar(perfil, atualizada)

    # -- escrita --------------------------------------------------------------------
    def criar(self, perfil: Perfil, corpo: Any) -> dict[str, Any]:
        if perfil.role == "motorista":
            raise SemPermissao("Você não tem permissão para esta ação.")
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
        config = self._repo.ler_configuracoes()
        if not self._pode(acao, perfil, req, config):
            raise SemPermissao(self._mensagem_negada(acao, perfil, req))
        self._exigir_checklist(acao, req)
        if perfil.role == "motorista":
            return self._executar_motorista(acao, perfil, req, corpo, config)
        campos = {
            "status": t.destino,
            t.campo_ator: perfil.id,
            **self._km(acao, req, corpo, perfil),
            # Autorizador/admin NÃO coleta localização: latitude/longitude do corpo são ignoradas.
            **self._motorista_na_autorizacao(acao, perfil, corpo, config),
        }
        atualizada = self._repo.transicionar(req["id"], req["status"], campos)
        if atualizada is None:  # outra requisição mudou o estado no meio do caminho
            raise TransicaoInvalida("A requisição foi alterada por outra pessoa. Atualize a tela.")
        self._notificador.notificar(EVENTO_POR_ACAO[acao], atualizada)  # após o commit
        return self.apresentar(perfil, atualizada)

    def _executar_motorista(
        self, acao: str, perfil: Perfil, req: Requisicao, corpo: Any, config: Configuracoes
    ) -> dict[str, Any]:
        """Saída/chegada pelo motorista: SEMPRE pela RPC `registrar_transicao_motorista`, que grava
        a localização e a transição na mesma transação (falhou uma, nenhuma persiste).

        Km e checklist seguem as mesmas validações do registro da equipe. Com a localização ligada,
        latitude e longitude são obrigatórias (sem exceção); desligada, são ignoradas.
        """
        km = self._km(acao, req, corpo, perfil)
        coordenadas = validar_localizacao(corpo) if config.motorista_localizacao else None
        momento = "saida" if acao == "registrar_saida" else "chegada"
        resultado = self._repo.registrar_transicao_motorista(
            requisicao_id=req["id"],
            momento=momento,
            ator_id=perfil.id,
            latitude=coordenadas.latitude if coordenadas else None,
            longitude=coordenadas.longitude if coordenadas else None,
            precisao_m=coordenadas.precisao_m if coordenadas else None,
            km_saida=km.get("km_saida"),
            km_chegada=km.get("km_chegada"),
        )
        atualizada = self._repo.obter_requisicao(req["id"])
        if atualizada is None or atualizada["status"] != resultado["status"]:
            raise TransicaoInvalida("A requisição foi alterada por outra pessoa. Atualize a tela.")
        self._notificador.notificar(EVENTO_POR_ACAO[acao], atualizada)  # após o commit
        return self.apresentar(perfil, atualizada)

    def _motorista_na_autorizacao(
        self, acao: str, perfil: Perfil, corpo: Any, config: Configuracoes
    ) -> dict[str, str]:
        """Autorizar aceita `motorista_id` opcional. Recurso desligado: o campo é ignorado.

        `motorista_alterado_por` (o autorizador/admin do JWT) vai no MESMO UPDATE que muda
        `motorista_id`: o trigger do banco o exige (AG002) e o zera em seguida.
        """
        if acao != "autorizar" or not config.motorista:
            return {}
        if corpo is not None and not isinstance(corpo, dict):
            raise RequisicaoInvalida("Envie um objeto JSON.")
        escolhido = None if corpo is None else corpo.get("motorista_id")
        if escolhido is None:
            escolhido = self.motorista_padrao()  # exatamente 1 motorista ativo, senão None
            if escolhido is None:
                return {}
            return {"motorista_id": escolhido, "motorista_alterado_por": perfil.id}
        return {
            "motorista_id": self.validar_motorista(escolhido),
            "motorista_alterado_por": perfil.id,
        }

    def motorista_padrao(self) -> str | None:
        """Id do único motorista ATIVO, ou None (nenhum ou mais de um: o autorizador escolhe)."""
        return self.seletor_motoristas()[1]

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

    def _km(self, acao: str, req: Requisicao, corpo: Any, perfil: Perfil) -> dict[str, int]:
        """Km da transição. Recurso desligado: o corpo é ignorado (nada gravado, sem erro).

        Ligado: saída exige `km_saida`; chegada exige `km_chegada` e, se a saída ficou sem km
        (recurso ligado com a viagem em curso), também `km_saida`. Km já gravado é imutável:
        um `km_saida` enviado na chegada é ignorado quando a saída já o tem.
        """
        if acao not in {"registrar_saida", "registrar_chegada"}:
            return {}
        if not self._pede_km(perfil, self._repo.ler_configuracoes()):
            return {}  # vigilante sem `vigilante_km`: o corpo é ignorado
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
        if acao == "cancelar" and perfil.role != "vigilante":
            return "Você só pode cancelar a sua própria requisição enquanto estiver solicitada."
        return "Você não tem permissão para esta ação."
