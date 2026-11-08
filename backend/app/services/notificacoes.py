"""Notificações internas (sino). Regras vivem aqui; as rotas só traduzem HTTP.

O dono é SEMPRE o perfil autenticado (`perfil.id`, vindo do JWT): nenhum método aceita o dono
como parâmetro, e o banco filtra por ele (inexistente e alheia dão a mesma resposta, 404).

O banco guarda só tipo, ids e um código curto (sem PII). Título, texto e destino são montados
aqui a partir de mapas FIXOS: nada de nome, placa, trajeto ou e-mail, e o destino só pode ser uma
rota da allowlist (a UI não decide para onde navegar).
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Mapping
from typing import Any

from app.errors import NaoEncontrado, RequisicaoInvalida
from app.modelos import Perfil
from app.notificacoes.email import MENSAGENS as MENSAGENS_EMAIL
from app.repositorios.base import Repositorio
from app.services.admin_configuracoes import ROTULOS_RECURSOS

log = logging.getLogger(__name__)

LIMITE_PADRAO, LIMITE_MAX = 20, 100
OFFSET_MAX = 100_000
MSG_NAO_ENCONTRADA = "Notificação não encontrada."

TITULO_GENERICO = "Nova notificação"
TEXTO_GENERICO = "Você tem uma nova notificação."

# tipo -> (título, texto). Sem dados pessoais: o detalhe fica na tela da requisição.
TEXTOS: dict[str, tuple[str, str]] = {
    "requisicao_nova": (
        "Nova solicitação aguardando autorização",
        "Há uma nova solicitação de veículo aguardando autorização. Toque para ver os detalhes.",
    ),
    "requisicao_autorizada": (
        "Requisição autorizada",
        "Sua requisição de veículo foi autorizada. Toque para ver os detalhes.",
    ),
    "requisicao_negada": (
        "Requisição negada",
        "Sua requisição de veículo foi negada. Toque para ver os detalhes.",
    ),
    "requisicao_saida": (
        "Saída registrada",
        "A saída do veículo da sua requisição foi registrada. Toque para ver os detalhes.",
    ),
    "requisicao_chegada": (
        "Chegada registrada",
        "A chegada do veículo da sua requisição foi registrada. Toque para ver o histórico.",
    ),
    "requisicao_cancelada_terceiros": (
        "Requisição cancelada",
        "Sua requisição de veículo foi cancelada por outro usuário. Toque para ver os detalhes.",
    ),
    "requisicao_cancelada_pelo_solicitante": (
        "Requisição cancelada pelo solicitante",
        "Uma requisição de veículo foi cancelada pelo solicitante. Toque para ver o histórico.",
    ),
    "checklist_avaria": (
        "Checklist com avaria registrado",
        "Um checklist registrou avaria no veículo{momento}. Toque para ver os detalhes.",
    ),
    "convite_aceito": (
        "Convite aceito",
        "Um convite de acesso foi aceito. Toque para ver os convites.",
    ),
    "perfil_alterado_admin": (
        "Perfil de usuário alterado",
        "O perfil de um usuário foi alterado. Toque para ver os usuários.",
    ),
    "configuracao_alterada": (
        "Configuração alterada",
        "Uma configuração do sistema foi alterada.",
    ),
    "sistema_falha_envio": (
        "Falha em envio do sistema",
        "Um envio automático do sistema falhou. Confira as configurações.",
    ),
    # Motorista (somente sino): texto fixo, sem nome, placa nem trajeto.
    "motorista_designado": (
        "Você foi designado para uma viagem",
        "Você foi designado como motorista de uma viagem. Toque para ver os detalhes.",
    ),
    "motorista_removido": (
        "Você foi removido de uma viagem",
        "Você foi removido como motorista de uma viagem. Consulte suas viagens.",
    ),
    "viagem_cancelada_motorista": (
        "Viagem cancelada",
        "Uma viagem em que você era o motorista foi cancelada. Toque para ver os detalhes.",
    ),
}

_MOMENTOS = {"saida": " na saída", "chegada": " na chegada"}
_PERFIL_ALTERADO = {
    "papel_alterado": "O papel de um usuário foi alterado. Toque para ver os usuários.",
    "desativado": "Um usuário foi desativado. Toque para ver os usuários.",
}
# Códigos de falha que não são de e-mail (callbacks de outras integrações).
_FALHAS_EXTRAS = {"n8n_falha": "A integração de automação (n8n) falhou."}

TIPOS_COM_REQUISICAO = frozenset(
    {
        "requisicao_nova",
        "requisicao_autorizada",
        "requisicao_negada",
        "requisicao_saida",
        "requisicao_chegada",
        "requisicao_cancelada_terceiros",
        "requisicao_cancelada_pelo_solicitante",
        "checklist_avaria",
        "motorista_designado",
        "motorista_removido",
        "viagem_cancelada_motorista",
    }
)
# Tipos que só aparecem no sino: nunca geram e-mail (nem n8n). O banco os cria não críticos.
TIPOS_SO_SINO = frozenset(
    {"motorista_designado", "motorista_removido", "viagem_cancelada_motorista"}
)
# Destinos do motorista: o detalhe da viagem (só se ainda for dele) ou a lista, no caso de remoção.
DESTINO_VIAGEM_MOTORISTA = "/motorista/detalhe?id="
DESTINO_LISTA_MOTORISTA = "/motorista"
_TIPOS_DESTINO_VIAGEM = frozenset({"motorista_designado", "viagem_cancelada_motorista"})
# Destinos fixos por tipo (allowlist). Os de requisição levam o id, validado como UUID.
DESTINOS_FIXOS: dict[str, str] = {
    "convite_aceito": "/admin/convites",
    "perfil_alterado_admin": "/admin/usuarios",
    "configuracao_alterada": "/admin/configuracoes",
    "sistema_falha_envio": "/admin/configuracoes",
}
DESTINO_REQUISICAO = "/requisicoes/detalhe?id="

# Falhas do endereço do usuário (não do sistema) não alertam os administradores.
CODIGOS_SEM_ALERTA = frozenset(
    {
        "destinatario_invalido",
        "smtp_destinatario_invalido",
        "smtp_destinatario_recusado",
        "sem_email",
    }
)

_CODIGO = re.compile(r"^[a-z0-9_.-]{1,40}$")


def _uuid_ou_none(valor: Any) -> str | None:
    if not isinstance(valor, str):
        return None
    try:
        return str(uuid.UUID(valor))
    except ValueError:
        return None


def montar_destino(tipo: str, requisicao_id: Any) -> str | None:
    if tipo == "motorista_removido":  # já não enxerga a viagem (anti-IDOR): vai para a lista
        return DESTINO_LISTA_MOTORISTA
    if tipo in _TIPOS_DESTINO_VIAGEM:
        rid = _uuid_ou_none(requisicao_id)
        return f"{DESTINO_VIAGEM_MOTORISTA}{rid}" if rid else None
    if tipo in TIPOS_COM_REQUISICAO:
        rid = _uuid_ou_none(requisicao_id)
        return f"{DESTINO_REQUISICAO}{rid}" if rid else None
    return DESTINOS_FIXOS.get(tipo)


def montar_texto(tipo: str, codigo: Any) -> tuple[str, str]:
    """(título, texto) por mapa fixo; tipo ou código desconhecido cai no genérico seguro."""
    base = TEXTOS.get(tipo)
    if base is None:
        return TITULO_GENERICO, TEXTO_GENERICO
    titulo, texto = base
    cod = codigo if isinstance(codigo, str) and _CODIGO.fullmatch(codigo) else None
    if tipo == "checklist_avaria":
        return titulo, texto.format(momento=_MOMENTOS.get(cod or "", ""))
    if tipo == "perfil_alterado_admin" and cod in _PERFIL_ALTERADO:
        return titulo, _PERFIL_ALTERADO[cod]
    if tipo == "configuracao_alterada" and cod in ROTULOS_RECURSOS:
        return titulo, f"O recurso “{ROTULOS_RECURSOS[cod]}” foi alterado nas Configurações."
    if tipo == "sistema_falha_envio" and cod is not None:
        motivo = _FALHAS_EXTRAS.get(cod) or MENSAGENS_EMAIL.get(cod)
        if motivo:
            return titulo, f"{motivo} Confira as configurações."
    return titulo, texto


def apresentar(item: Mapping[str, Any]) -> dict[str, Any]:
    """Item do banco -> contrato da API (sem ids internos, código nem referência)."""
    tipo = str(item.get("tipo") or "")
    titulo, texto = montar_texto(tipo, item.get("codigo"))
    return {
        "id": str(item["id"]),
        "tipo": tipo,
        "titulo": titulo,
        "texto": texto,
        "critica": bool(item.get("critica")),
        "criada_em": item.get("criada_em"),
        "lida_em": item.get("lida_em"),
        "destino": montar_destino(tipo, item.get("requisicao_id")),
    }


def _inteiro(args: Mapping[str, Any], nome: str, padrao: int, minimo: int, maximo: int) -> int:
    bruto = args.get(nome)
    if bruto is None or bruto == "":
        return padrao
    if not isinstance(bruto, str) or not bruto.isascii() or not bruto.isdigit():
        raise RequisicaoInvalida(f"O parâmetro '{nome}' deve ser um número inteiro.")
    valor = int(bruto)
    if not minimo <= valor <= maximo:
        raise RequisicaoInvalida(f"O parâmetro '{nome}' deve estar entre {minimo} e {maximo}.")
    return valor


def _booleano(args: Mapping[str, Any], nome: str) -> bool:
    bruto = args.get(nome)
    if bruto is None or bruto == "":
        return False
    if bruto in ("true", "false"):
        return bruto == "true"
    raise RequisicaoInvalida(f"O parâmetro '{nome}' deve ser true ou false.")


class ServicoNotificacoes:
    def __init__(self, repo: Repositorio) -> None:
        self._repo = repo

    def listar(self, perfil: Perfil, args: Mapping[str, Any]) -> dict[str, Any]:
        limite = _inteiro(args, "limite", LIMITE_PADRAO, 1, LIMITE_MAX)
        offset = _inteiro(args, "offset", 0, 0, OFFSET_MAX)
        so_nao_lidas = _booleano(args, "somente_nao_lidas")
        dados = self._repo.listar_notificacoes(
            perfil.id, limite=limite, offset=offset, somente_nao_lidas=so_nao_lidas
        )
        return {
            "itens": [apresentar(i) for i in dados["itens"]],
            "total": int(dados.get("total", 0)),
            "nao_lidas": int(dados.get("nao_lidas", 0)),
        }

    def contagem(self, perfil: Perfil) -> dict[str, int]:
        return {"nao_lidas": self._repo.contar_notificacoes_nao_lidas(perfil.id)}

    def marcar_lida(self, perfil: Perfil, notificacao_id: str) -> dict[str, Any]:
        try:
            nid = str(uuid.UUID(notificacao_id))
        except ValueError:
            raise NaoEncontrado(MSG_NAO_ENCONTRADA) from None
        r = self._repo.marcar_notificacao_lida(perfil.id, nid)
        return {"id": str(r["id"]), "lida_em": r.get("lida_em")}

    def marcar_todas(self, perfil: Perfil) -> dict[str, int]:
        return {"marcadas": self._repo.marcar_todas_lidas(perfil.id)}


def registrar_falha_sistema(repo: Repositorio, codigo: str) -> None:
    """Avisa os admins (`sistema_falha_envio`, dedupe de 1 h no banco). NUNCA propaga erro: é
    chamada de dentro de caminhos de falha, que não podem falhar de novo. Só o código vai ao log."""
    try:
        cod = codigo if _CODIGO.fullmatch(codigo or "") else "desconhecido"
        repo.registrar_notificacao_sistema("sistema_falha_envio", cod)
    except Exception as e:
        log.error("notif_sistema_falhou tipo=%s", type(e).__name__)
