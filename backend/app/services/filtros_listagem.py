"""Filtros opcionais de `GET /api/requisicoes` (q, ordenar, veiculo_id, unidade).

Tudo é validado aqui por allowlist; o repositório só recebe valores já normalizados. Qualquer
valor inválido vira 400 `filtro_invalido`. A visibilidade (dono, motorista, vigilante) não
depende destes filtros: eles apenas restringem o que o usuário já pode ver.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from app.errors import RequisicaoInvalida
from app.modelos import UNIDADES, UNIDADES_ORDEM

CODIGO_FILTRO = "filtro_invalido"
BUSCA_MIN, BUSCA_MAX = 2, 80
ORDENAR_PADRAO = "-criado_em"
# valor do cliente -> (coluna, descendente). Nada fora daqui chega ao `order` do PostgREST.
ORDENACOES: dict[str, tuple[str, bool]] = {
    "-criado_em": ("criado_em", True),
    "criado_em": ("criado_em", False),
    "hora_saida": ("hora_saida", False),
    "-hora_saida": ("hora_saida", True),
    "status": ("status", False),
    "-status": ("status", True),
}
# Caracteres com sentido na sintaxe de filtro do PostgREST (`,` `(` `)` `"` `\` `:`... ) ou
# curinga (`*`, `%`, `_`): NEUTRALIZADOS, viram o curinga `*` (qualquer sequência). Assim o termo
# nunca altera a estrutura do `or=(...)` nem vira outro operador, e "Sede, TRF" ainda encontra
# "Sede, TRF" (busca um pouco mais ampla, nunca mais restrita nem fora da visibilidade).
# Mantidos: letras/dígitos unicode, espaço, `.`, `-`, `/`, `'`, `&`, `#`, `+` e `º`/`ª`
# (o cliente HTTP codifica a query; nenhum desses tem papel dentro de um valor `ilike`).
_PERMITIDO = re.compile(r"[\w .\-/'&#+ºª]", re.UNICODE)
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class FiltrosListagem:
    busca: str | None = None  # padrão `ilike` já neutralizado (só `*` como curinga)
    ordenar: str = ORDENAR_PADRAO
    veiculo_id: str | None = None
    unidade: str | None = None

    @property
    def ordem(self) -> tuple[str, bool]:
        return ORDENACOES[self.ordenar]

    def para_repo(self) -> dict[str, Any]:
        """Argumentos de `contar_requisicoes` (a ordenação só vale para `listar`)."""
        return {"busca": self.busca, "veiculo_id": self.veiculo_id, "unidade": self.unidade}


def _invalido(mensagem: str) -> RequisicaoInvalida:
    return RequisicaoInvalida(mensagem, CODIGO_FILTRO)


def _texto(valor: Any) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None  # campo vazio de formulário = não informado


def neutralizar_busca(valor: Any) -> str | None:
    """Termo `q` (2..80 caracteres após aparar) -> padrão `ilike` seguro para `or=(...)`."""
    termo = _texto(valor)
    if termo is None:
        return None
    termo = " ".join(termo.split())
    if not BUSCA_MIN <= len(termo) <= BUSCA_MAX:
        raise _invalido(f"A busca deve ter entre {BUSCA_MIN} e {BUSCA_MAX} caracteres.")
    if _CONTROLE.search(termo):
        raise _invalido("A busca contém caracteres não permitidos.")
    seguro = "".join(c if _PERMITIDO.fullmatch(c) and c != "_" else "*" for c in termo)
    seguro = re.sub(r"\*+", "*", seguro)
    if len(seguro.replace("*", "").replace(" ", "")) < BUSCA_MIN:
        raise _invalido("A busca deve ter ao menos 2 letras ou números.")
    return seguro


def validar_filtros_listagem(args: Any) -> FiltrosListagem:
    """`args`: mapeamento da query string (`request.args`)."""
    busca = neutralizar_busca(args.get("q"))
    ordenar = _texto(args.get("ordenar")) or ORDENAR_PADRAO
    if ordenar not in ORDENACOES:
        raise _invalido("O parâmetro 'ordenar' deve ser " + ", ".join(ORDENACOES) + ".")
    veiculo_id = _texto(args.get("veiculo_id"))
    if veiculo_id is not None:
        try:
            veiculo_id = str(uuid.UUID(veiculo_id))
        except ValueError:
            raise _invalido("O parâmetro 'veiculo_id' deve ser um identificador válido.") from None
    unidade = _texto(args.get("unidade"))
    if unidade is not None and unidade not in UNIDADES:
        raise _invalido("O parâmetro 'unidade' deve ser " + ", ".join(UNIDADES_ORDEM) + ".")
    return FiltrosListagem(busca, ordenar, veiculo_id, unidade)
