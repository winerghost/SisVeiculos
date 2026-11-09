"""Regras do texto de uma saudação (espelham os CHECK da tabela `saudacoes`)."""

from __future__ import annotations

import re
import unicodedata

MIN_CHARS, MAX_CHARS = 3, 80
PLACEHOLDER = "{nome}"
_DOMINIO = re.compile(r"\w+\.(com|gov|br|org|net)\b", re.IGNORECASE)


def normalizar(texto: str) -> str:
    """Forma de comparação para deduplicar: sem acento, minúscula, espaços colapsados."""
    base = unicodedata.normalize("NFKD", texto)
    base = "".join(c for c in base if not unicodedata.combining(c))
    return " ".join(base.casefold().split())


def motivo_invalido(texto: object) -> str | None:
    """None se o texto é aceitável; senão uma descrição curta do problema."""
    if not isinstance(texto, str):
        return "O texto deve ser uma string."
    if not MIN_CHARS <= len(texto) <= MAX_CHARS or texto != texto.strip():
        return f"O texto deve ter de {MIN_CHARS} a {MAX_CHARS} caracteres, sem espaços nas pontas."
    if any(unicodedata.category(c).startswith("C") for c in texto):  # quebra de linha, controle
        return "O texto não pode ter quebra de linha nem caracteres de controle."
    if "<" in texto or ">" in texto:
        return "O texto não pode conter '<' nem '>'."
    baixo = texto.casefold()
    if "http" in baixo or "www." in baixo:
        return "O texto não pode conter links."
    if "@" in texto or _DOMINIO.search(texto):
        return "O texto não pode conter e-mail, endereço de site nem '@'."
    sem_nome = texto.replace(PLACEHOLDER, "")
    if "{" in sem_nome or "}" in sem_nome:
        return "O único marcador permitido é {nome}."
    return None


def tem_placeholder(texto: str) -> bool:
    return PLACEHOLDER in texto


def sem_placeholder(texto: str) -> str:
    """Remove o marcador (e a vírgula/espaço que o antecede) para quem não tem nome."""
    limpo = re.sub(r",?\s*\{nome\}", "", texto)
    return " ".join(limpo.split())
