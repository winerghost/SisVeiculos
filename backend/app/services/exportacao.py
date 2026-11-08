"""Exportação de relatórios: formatação pt-BR, CSV e XLSX.

Sem regra de negócio: recebe colunas e linhas já autorizadas pelo service. Duas defesas aqui:
  - injeção de fórmula: todo TEXTO que comece com = + - @ tab CR LF ganha um apóstrofo na frente
    (CSV e XLSX; no XLSX a célula também é forçada a tipo string);
  - caracteres de controle ilegais no XLSX são removidos.
Datas saem no fuso fixo -03:00 (dd/mm/aaaa hh:mm); durações em hh:mm:ss.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

# Brasil sem horário de verão desde 2019. Mesmo deslocamento de services/dashboards.FUSO.
FUSO = timezone(timedelta(hours=-3))
BOM = "﻿"
SEPARADOR = ";"
COR_CABECALHO = "0F3D75"  # primária AGU
_PERIGOSOS = ("=", "+", "-", "@", "\t", "\r", "\n")
_ILEGAIS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DIA = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_MES = re.compile(r"(\d{4})-(\d{2})")

TIPOS = ("texto", "datahora", "dia", "mes", "duracao", "inteiro", "decimal")


@dataclass(frozen=True)
class Coluna:
    chave: str
    rotulo: str
    tipo: str = "texto"
    mapa: dict[str, str] = field(default_factory=dict, hash=False, compare=False)


def neutralizar_formula(texto: str) -> str:
    """Prefixa com apóstrofo o texto que um editor de planilhas poderia tratar como fórmula."""
    return "'" + texto if texto.startswith(_PERIGOSOS) else texto


def _limpar(texto: str) -> str:
    return _ILEGAIS.sub("", texto)


def _datahora(valor: Any) -> datetime | None:
    if isinstance(valor, datetime):
        dt = valor
    elif isinstance(valor, str):
        try:
            dt = datetime.fromisoformat(valor.strip())
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(FUSO)


def _numero(valor: Any) -> int | float | None:
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, int | float):
        return valor
    if isinstance(valor, Decimal):
        return float(valor)
    try:
        return float(str(valor))
    except ValueError:
        return None


def duracao_texto(segundos: int | float) -> str:
    """hh:mm:ss (horas podem passar de 24); negativo (saída antes do previsto) leva sinal."""
    total = round(abs(segundos))
    h, resto = divmod(total, 3600)
    m, s = divmod(resto, 60)
    sinal = "-" if segundos < 0 and total else ""
    return f"{sinal}{h:02d}:{m:02d}:{s:02d}"


def _formatar(col: Coluna, valor: Any) -> tuple[str, bool]:
    """(texto pt-BR, confiavel). `confiavel` só quando o texto foi GERADO aqui a partir de um valor
    que casa com o tipo da coluna (data, número, duração). Texto livre, rótulos e qualquer valor
    que não case com o formato esperado são NÃO confiáveis: exportados com neutralização de
    fórmula. A duração negativa ("-00:05:00") é gerada pelo sistema, logo confiável."""
    if valor is None:
        return "", True
    if col.tipo == "datahora":
        dt = _datahora(valor)
        if dt:
            return dt.strftime("%d/%m/%Y %H:%M"), True
    elif col.tipo == "dia":
        m = _DIA.fullmatch(str(valor))
        if m:
            return f"{m[3]}/{m[2]}/{m[1]}", True
    elif col.tipo == "mes":
        m = _MES.fullmatch(str(valor))
        if m:
            return f"{m[2]}/{m[1]}", True
    elif col.tipo in {"duracao", "inteiro", "decimal"}:
        n = _numero(valor)
        if n is not None and math.isfinite(n):
            if col.tipo == "duracao":
                return duracao_texto(n), True
            if col.tipo == "inteiro":
                return str(round(n)), True
            return f"{n:.2f}".replace(".", ","), True
    elif col.tipo == "texto":
        texto = str(valor)
        return _limpar(col.mapa.get(texto, texto)), False
    return _limpar(str(valor)), False  # fallback: valor fora do formato esperado


def texto_exibicao(col: Coluna, valor: Any) -> str:
    """Valor como texto pt-BR (prévia e CSV). Nulo = vazio. Sem neutralização de fórmula."""
    return _formatar(col, valor)[0]


def _texto_seguro(col: Coluna, valor: Any) -> str:
    texto, confiavel = _formatar(col, valor)
    return texto if confiavel else neutralizar_formula(texto)


def _celula_csv(col: Coluna, valor: Any) -> str:
    return _texto_seguro(col, valor)


def gerar_csv(colunas: list[Coluna], linhas: list[dict[str, Any]]) -> bytes:
    """UTF-8 com BOM (Excel pt-BR abre acentos), separador ';', quebra de linha CRLF."""
    saida = io.StringIO(newline="")
    w = csv.writer(saida, delimiter=SEPARADOR, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow([neutralizar_formula(_limpar(c.rotulo)) for c in colunas])
    for linha in linhas:
        w.writerow([_celula_csv(c, linha.get(c.chave)) for c in colunas])
    return (BOM + saida.getvalue()).encode("utf-8")


def _largura(col: Coluna, linhas: list[dict[str, Any]]) -> float:
    maior = len(col.rotulo)
    for linha in linhas[:200]:
        maior = max(maior, len(texto_exibicao(col, linha.get(col.chave))))
    return float(min(max(maior + 2, 10), 50))


def gerar_xlsx(colunas: list[Coluna], linhas: list[dict[str, Any]], titulo: str) -> bytes:
    """XLSX em modo write_only (streaming: memória constante no tamanho das linhas)."""
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook(write_only=True)
    wb.properties.creator = "SisVeiculos"
    ws = wb.create_sheet(title=re.sub(r"[\\/*?:\[\]]", " ", titulo)[:31] or "Relatório")
    ws.freeze_panes = "A2"
    for i, col in enumerate(colunas):
        ws.column_dimensions[_letra(i)].width = _largura(col, linhas)

    negrito = Font(bold=True, color="FFFFFF")
    fundo = PatternFill("solid", start_color=COR_CABECALHO, end_color=COR_CABECALHO)
    cabecalho = []
    for col in colunas:
        c = WriteOnlyCell(ws, value=neutralizar_formula(_limpar(col.rotulo)))
        c.data_type = "s"
        c.font, c.fill = negrito, fundo
        c.alignment = Alignment(vertical="center", wrap_text=True)
        cabecalho.append(c)
    ws.append(cabecalho)

    for linha in linhas:
        ws.append([_celula_xlsx(ws, col, linha.get(col.chave)) for col in colunas])

    destino = io.BytesIO()
    wb.save(destino)
    return destino.getvalue()


def _letra(indice: int) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(indice + 1)


def _celula_xlsx(ws: Any, col: Coluna, valor: Any) -> Any:
    from openpyxl.cell import WriteOnlyCell

    if valor is None:
        return None
    if col.tipo == "datahora":
        dt = _datahora(valor)
        if dt is not None:
            c = WriteOnlyCell(ws, value=dt.replace(tzinfo=None))  # Excel não guarda fuso
            c.number_format = "dd/mm/yyyy hh:mm"
            return c
    if col.tipo in {"inteiro", "decimal"}:
        n = _numero(valor)
        if n is not None:
            c = WriteOnlyCell(ws, value=round(n) if col.tipo == "inteiro" else float(n))
            c.number_format = "0" if col.tipo == "inteiro" else "0.00"
            return c
    c = WriteOnlyCell(ws, value=_texto_seguro(col, valor))
    c.data_type = "s"  # nunca fórmula, mesmo que comece com "="
    return c


def formatar_linhas(colunas: list[Coluna], linhas: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Linhas como {chave: texto pt-BR} (prévia)."""
    return [{c.chave: texto_exibicao(c, linha.get(c.chave)) for c in colunas} for linha in linhas]


def data_arquivo(agora: datetime) -> date:
    return agora.astimezone(FUSO).date()
