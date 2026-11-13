"""Validação dos corpos das rotas administrativas (lista fechada de campos, tudo no servidor)."""

from __future__ import annotations

import re
from typing import Any

from app.errors import ConviteInvalido, RequisicaoInvalida
from app.modelos import UNIDADES
from app.services.validacao import MAX_NOME, validar_texto

ROLES = ("solicitante", "autorizador", "admin", "motorista", "vigilante")
# Mesmo critério do CHECK ck_convites_email_dominio: Gmail ou *.agu.gov.br.
_EMAIL = re.compile(r"^[a-z0-9._%+-]+@(gmail\.com|([a-z0-9-]+\.)*agu\.gov\.br)$")
_PLACA = re.compile(r"^[A-Z]{3}[0-9][A-Z0-9][0-9]{2}$")
MAX_EMAIL = 254
MAX_DESCRICAO = 120
VALIDADE_PADRAO_DIAS, VALIDADE_MAX_DIAS = 7, 30
RECURSOS = (
    "quilometragem",
    "abastecimento_manutencao",
    "checklist",
    "mfa",
    "relatorio_email",
    "relatorio_whatsapp",
    "notificacao_email_criticos",
    "motorista",
    "motorista_registra",
    "motorista_localizacao",
    "vigilante_km",
    "autorizador_decide_propria",
    "autorizador_km",
)


def _objeto(corpo: Any, permitidos: set[str]) -> dict[str, Any]:
    if not isinstance(corpo, dict):
        raise RequisicaoInvalida("Envie um objeto JSON.")
    extras = sorted(set(corpo) - permitidos)
    if extras:
        raise RequisicaoInvalida("Campos não permitidos: " + ", ".join(extras) + ".")
    return corpo


def validar_email(valor: Any) -> str:
    if not isinstance(valor, str):
        raise RequisicaoInvalida("O campo 'email' é obrigatório.")
    email = valor.strip().lower()
    if len(email) > MAX_EMAIL or not _EMAIL.fullmatch(email):
        raise RequisicaoInvalida("Informe um e-mail institucional (@agu.gov.br) ou Gmail.")
    return email


MAX_BUSCA = 80
# Caracteres com sentido na sintaxe de filtro do PostgREST (",", "(", ")", aspas, "\") ou
# curinga (* e % no PostgREST; "_" é o curinga de 1 caractere do ILIKE do Postgres): recusados
# em vez de escapados, para o termo nunca virar filtro nem padrão. Escapar "_" dependeria de
# como o PostgREST trata a barra dentro de or=(...), comportamento frágil entre versões.
# Custo: não se busca por "_" (nomes quase nunca o têm). Permitidos: . : & # + espaço e
# unicode; "&", "#" e "+" são codificados pelo cliente HTTP ao montar a query.
_BUSCA_PROIBIDA = re.compile(r'[,()"\\*%_\x00-\x1f\x7f]')


def validar_busca(valor: str | None) -> str | None:
    """Termo de busca (`q`) da listagem de usuários: aparado, com tamanho limitado."""
    if valor is None:
        return None
    termo = " ".join(valor.split())
    if not termo:
        return None
    if len(termo) > MAX_BUSCA:
        raise RequisicaoInvalida(f"A busca deve ter no máximo {MAX_BUSCA} caracteres.")
    if _BUSCA_PROIBIDA.search(termo):
        raise RequisicaoInvalida("A busca contém caracteres não permitidos.")
    return termo


def _role(valor: Any) -> str:
    if valor not in ROLES:
        raise RequisicaoInvalida(
            "O campo 'role' deve ser solicitante, autorizador, admin, motorista ou vigilante."
        )
    return str(valor)


def _unidade(valor: Any) -> str:
    if valor not in UNIDADES:
        raise RequisicaoInvalida("O campo 'unidade' deve ser PU, PF, CJU ou UEA.")
    return str(valor)


def validar_convite(corpo: Any) -> dict[str, Any]:
    c = _objeto(corpo, {"email", "nome", "unidade", "role", "validade_dias"})
    validade = c.get("validade_dias", VALIDADE_PADRAO_DIAS)
    if isinstance(validade, bool) or not isinstance(validade, int):
        raise RequisicaoInvalida("O campo 'validade_dias' deve ser um número inteiro.")
    if not 1 <= validade <= VALIDADE_MAX_DIAS:
        raise RequisicaoInvalida(
            f"O campo 'validade_dias' deve estar entre 1 e {VALIDADE_MAX_DIAS}."
        )
    return {
        "email": validar_email(c.get("email")),
        "nome": validar_texto(c, "nome", MAX_NOME, True),
        "unidade": _unidade(c.get("unidade")),
        "role": _role(c.get("role", "solicitante")),
        "validade_dias": validade,
    }


def validar_edicao_usuario(corpo: Any) -> dict[str, Any]:
    """Só nome, unidade e role. `ativo` tem rota própria; e-mail e id não mudam."""
    c = _objeto(corpo, {"nome", "unidade", "role"})
    if not c:
        raise RequisicaoInvalida("Informe ao menos um campo: nome, unidade ou role.")
    campos: dict[str, Any] = {}
    if "nome" in c:
        campos["nome"] = validar_texto(c, "nome", MAX_NOME, True)
    if "unidade" in c:
        campos["unidade"] = _unidade(c["unidade"])
    if "role" in c:
        campos["role"] = _role(c["role"])
    return campos


def normalizar_placa(valor: Any) -> str | None:
    """Maiúsculas, sem hífen/espaço. None/vazio = sem placa. Formato antigo ou Mercosul."""
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise RequisicaoInvalida("O campo 'placa' deve ser texto.")
    placa = re.sub(r"[\s-]", "", valor).upper()
    if not placa:
        return None
    if not _PLACA.fullmatch(placa):
        raise RequisicaoInvalida("Placa inválida. Use o formato ABC1234 ou ABC1D23.")
    return placa


def validar_veiculo_criacao(corpo: Any) -> dict[str, Any]:
    c = _objeto(corpo, {"descricao", "placa"})
    return {
        "descricao": validar_texto(c, "descricao", MAX_DESCRICAO, True),
        "placa": normalizar_placa(c.get("placa")),
    }


def validar_veiculo_edicao(corpo: Any) -> dict[str, Any]:
    """`ativo` tem rotas próprias (ativar/desativar)."""
    c = _objeto(corpo, {"descricao", "placa"})
    if not c:
        raise RequisicaoInvalida("Informe ao menos um campo: descricao ou placa.")
    campos: dict[str, Any] = {}
    if "descricao" in c:
        campos["descricao"] = validar_texto(c, "descricao", MAX_DESCRICAO, True)
    if "placa" in c:
        campos["placa"] = normalizar_placa(c["placa"])
    return campos


def validar_recursos(corpo: Any) -> dict[str, bool]:
    c = _objeto(corpo, set(RECURSOS))
    if not c:
        raise RequisicaoInvalida("Informe ao menos um recurso: " + ", ".join(RECURSOS) + ".")
    for chave, valor in c.items():
        if not isinstance(valor, bool):
            raise RequisicaoInvalida(f"O recurso '{chave}' deve ser verdadeiro ou falso.")
    return dict(c)


def validar_aceite(corpo: Any) -> tuple[str, str | None]:
    """Token do convite (obrigatório) e nome de reserva (usado só se o convite não trouxer)."""
    c = _objeto(corpo, {"token", "nome"})
    token = c.get("token")
    if not isinstance(token, str) or not 16 <= len(token) <= 128 or not token.isascii():
        raise ConviteInvalido("Convite inválido.")
    return token, validar_texto(c, "nome", MAX_NOME, False)
