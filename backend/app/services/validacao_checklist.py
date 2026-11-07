"""Validação dos corpos do checklist (a API é a única fonte das regras).

`descricao_avarias` é texto livre e pode conter dado pessoal: nenhuma mensagem de erro daqui
repete o valor recebido.
"""

from __future__ import annotations

from typing import Any

from app.errors import RequisicaoInvalida
from app.services.validacao import limpar_texto, validar_uuid

MOMENTOS = ("saida", "chegada")
COMBUSTIVEIS = ("vazio", "um_quarto", "meio", "tres_quartos", "cheio")
MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
TAMANHO_MAX = 5 * 1024 * 1024  # 5 MB, o mesmo teto do bucket e do CHECK do banco
MAX_FOTOS = 6
MAX_DESCRICAO = 500

CAMPOS_UPLOAD = {"mime", "tamanho"}
CAMPOS_FOTO = {"id", "mime", "tamanho"}
CAMPOS_REGISTRO = {"combustivel", "avarias", "descricao_avarias", "fotos"}


def validar_momento(valor: Any) -> str:
    if valor not in MOMENTOS:
        raise RequisicaoInvalida("O momento do checklist deve ser 'saida' ou 'chegada'.")
    return str(valor)


def _objeto(corpo: Any, permitidos: set[str]) -> dict[str, Any]:
    if not isinstance(corpo, dict):
        raise RequisicaoInvalida("Envie um objeto JSON.")
    # Não ecoa as chaves enviadas (podem ser enormes ou conter dado pessoal): só a contagem.
    extras = set(corpo) - permitidos
    if extras:
        raise RequisicaoInvalida(
            f"O corpo contém {len(extras)} campo(s) não permitido(s). "
            "Envie apenas: " + ", ".join(sorted(permitidos)) + "."
        )
    return corpo


def _mime_e_tamanho(corpo: dict[str, Any]) -> tuple[str, int]:
    mime = corpo.get("mime")
    if not isinstance(mime, str) or mime not in MIMES:
        raise RequisicaoInvalida("O campo 'mime' deve ser image/jpeg, image/png ou image/webp.")
    tamanho = corpo.get("tamanho")
    if isinstance(tamanho, bool) or not isinstance(tamanho, int):
        raise RequisicaoInvalida("O campo 'tamanho' deve ser um número inteiro de bytes.")
    if tamanho < 1:
        raise RequisicaoInvalida("O campo 'tamanho' deve ser maior que zero.")
    if tamanho > TAMANHO_MAX:
        raise RequisicaoInvalida("Cada foto aceita no máximo 5 MB.", "foto_grande")
    return mime, tamanho


def validar_upload(corpo: Any) -> dict[str, Any]:
    mime, tamanho = _mime_e_tamanho(_objeto(corpo, CAMPOS_UPLOAD))
    return {"mime": mime, "tamanho": tamanho}


def _fotos(valor: Any) -> list[dict[str, Any]]:
    if valor is None:
        return []
    if not isinstance(valor, list):
        raise RequisicaoInvalida("O campo 'fotos' deve ser uma lista.")
    if len(valor) > MAX_FOTOS:
        raise RequisicaoInvalida(f"O checklist aceita no máximo {MAX_FOTOS} fotos.")
    fotos: list[dict[str, Any]] = []
    for item in valor:
        f = _objeto(item, CAMPOS_FOTO)
        mime, tamanho = _mime_e_tamanho(f)
        fotos.append(
            {"id": validar_uuid(f.get("id"), "id da foto"), "mime": mime, "tamanho": tamanho}
        )
    if len({f["id"] for f in fotos}) != len(fotos):
        raise RequisicaoInvalida("Há fotos repetidas no checklist.")
    return fotos


def _descricao(avarias: bool, valor: Any) -> str | None:
    if valor is not None and not isinstance(valor, str):
        raise RequisicaoInvalida("O campo 'descricao_avarias' deve ser texto.")
    texto = limpar_texto(valor, permitir_quebra=True).strip() if valor is not None else ""
    if not avarias:
        if texto:
            raise RequisicaoInvalida("Não informe 'descricao_avarias' quando não há avarias.")
        return None
    if not texto:
        raise RequisicaoInvalida("Descreva as avarias em 'descricao_avarias'.")
    if len(texto) > MAX_DESCRICAO:
        raise RequisicaoInvalida(
            f"O campo 'descricao_avarias' aceita no máximo {MAX_DESCRICAO} caracteres."
        )
    return texto


def validar_registro(corpo: Any) -> dict[str, Any]:
    c = _objeto(corpo, CAMPOS_REGISTRO)
    combustivel = c.get("combustivel")
    if not isinstance(combustivel, str) or combustivel not in COMBUSTIVEIS:
        raise RequisicaoInvalida("O campo 'combustivel' deve ser: " + ", ".join(COMBUSTIVEIS) + ".")
    avarias = c.get("avarias")
    if not isinstance(avarias, bool):
        raise RequisicaoInvalida("O campo 'avarias' deve ser verdadeiro ou falso.")
    return {
        "combustivel": combustivel,
        "avarias": avarias,
        "descricao_avarias": _descricao(avarias, c.get("descricao_avarias")),
        "fotos": _fotos(c.get("fotos")),
    }
