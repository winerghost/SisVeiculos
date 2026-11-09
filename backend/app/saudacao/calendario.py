"""Calendário de ocasiões: dado um instante, qual a ocasião de maior prioridade.

Prioridade: data comemorativa > dia especial (segunda, sexta, fim de semana) > período do dia.
Fuso: o fixo do projeto (`app.services.dashboards.FUSO`, America/Sao_Paulo = -03:00, sem horário
de verão desde 2019). Um instante com outro fuso é convertido antes de decidir a data e a hora.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.services.dashboards import FUSO

PERIODOS: tuple[str, ...] = ("madrugada", "manha", "tarde", "noite")
DIAS_ESPECIAIS: tuple[str, ...] = ("segunda", "sexta", "fim_de_semana")
DATAS: tuple[str, ...] = (
    "ano_novo",
    "vespera_ano_novo",
    "carnaval",
    "sexta_santa",
    "pascoa",
    "tiradentes",
    "trabalho",
    "corpus_christi",
    "independencia",
    "padroeira",
    "finados",
    "republica",
    "consciencia_negra",
    "dia_servidor",
    "vespera_natal",
    "natal",
)
# Lista fixa e única de ocasiões válidas (a tabela `saudacoes` só aceita estas).
OCASIOES: tuple[str, ...] = PERIODOS + DIAS_ESPECIAIS + DATAS

# (mês, dia) -> ocasião, para as datas fixas.
_FIXAS: dict[tuple[int, int], str] = {
    (1, 1): "ano_novo",
    (4, 21): "tiradentes",
    (5, 1): "trabalho",
    (9, 7): "independencia",
    (10, 12): "padroeira",
    (10, 28): "dia_servidor",
    (11, 2): "finados",
    (11, 15): "republica",
    (11, 20): "consciencia_negra",
    (12, 24): "vespera_natal",
    (12, 25): "natal",
    (12, 31): "vespera_ano_novo",
}


@dataclass(frozen=True)
class Ocasiao:
    principal: str
    apoio: tuple[str, ...]  # ocasiões de apoio (período do dia) para o fallback
    data: date  # data local usada na decisão
    periodo: str = ""  # período do dia local (entra na semente da frase)


def pascoa(ano: int) -> date:
    """Domingo de Páscoa (algoritmo gregoriano anônimo de Meeus/Jones/Butcher)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - nome do algoritmo
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(ano, mes, dia + 1)


def _moveis(ano: int) -> dict[date, str]:
    p = pascoa(ano)
    return {
        p - timedelta(days=48): "carnaval",  # segunda de Carnaval
        p - timedelta(days=47): "carnaval",  # terça de Carnaval
        p - timedelta(days=2): "sexta_santa",
        p: "pascoa",
        p + timedelta(days=60): "corpus_christi",
    }


def data_comemorativa(dia: date) -> str | None:
    return _FIXAS.get((dia.month, dia.day)) or _moveis(dia.year).get(dia)


def periodo(hora: int) -> str:
    if hora < 5:
        return "madrugada"
    if hora < 12:
        return "manha"
    if hora < 18:
        return "tarde"
    return "noite"


def dia_especial(dia: date) -> str | None:
    wd = dia.weekday()
    if wd == 0:
        return "segunda"
    if wd == 4:
        return "sexta"
    if wd >= 5:
        return "fim_de_semana"
    return None


def _local(instante: datetime) -> datetime:
    return instante.replace(tzinfo=FUSO) if instante.tzinfo is None else instante.astimezone(FUSO)


def resolver(instante: datetime) -> Ocasiao:
    """Ocasião de maior prioridade para o instante (sem fuso = já no fuso do projeto)."""
    local = _local(instante)
    dia = local.date()
    per = periodo(local.hour)
    principal = data_comemorativa(dia) or dia_especial(dia) or per
    return Ocasiao(principal=principal, apoio=(per,), data=dia, periodo=per)


# Horas locais em que o período muda (0h é a virada de dia).
_FRONTEIRAS: tuple[int, ...] = (0, 5, 12, 18)


def chave_validade(instante: datetime) -> tuple[str, datetime]:
    """Chave `AAAA-MM-DD|periodo` (fuso do projeto) e o próximo instante em que ela muda.

    A chave não leva nenhum dado pessoal. `valido_ate` cai em 00:00, 05:00, 12:00 ou 18:00 locais.
    """
    local = _local(instante)
    chave = f"{local.date().isoformat()}|{periodo(local.hour)}"
    inicio_dia = datetime(local.year, local.month, local.day, tzinfo=FUSO)
    proxima = next((h for h in _FRONTEIRAS if h > local.hour), None)
    if proxima is None:
        return chave, inicio_dia + timedelta(days=1)
    return chave, inicio_dia + timedelta(hours=proxima)
