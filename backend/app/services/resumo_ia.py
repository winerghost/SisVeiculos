"""Resumo narrativo em dashboards e relatórios (IA opcional, Fase 2).

A IA só redige: descreve em palavras números que o servidor já calculou. O prompt é montado aqui,
no servidor, a partir dos agregados do próprio serviço (nunca de dados enviados pelo cliente) e
por LISTA BRANCA: cada função lê chaves nomeadas e escreve uma linha de texto. Nada é copiado em
bloco, então um campo novo no banco jamais chega ao provedor sem alguém acrescentá-lo aqui.

Nunca vão ao provedor: nomes, e-mails, placas, trajetos, motoristas, observações, localização,
ids (de requisição, veículo ou usuário) e rótulos de veículo. Células pequenas (menos de
`LIMITE_CELULA` casos) são generalizadas ("menos de 3") ou omitidas, para que um número raro não
aponte para uma viagem ou pessoa. A saída é validada e descartada se fugir do formato; o texto
gerado e o prompt nunca são logados.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.errors import ErroApi
from app.ia.servico import ServicoIA
from app.modelos import UNIDADES, Perfil
from app.saudacao.servico import CacheTTL
from app.services.relatorios import RELATORIOS, SEM_RESUMO, Conjunto

FUNCAO_IA = "resumo"
LIMITE_CELULA = 3  # abaixo disso o número é generalizado ou omitido
MAX_CHARS = 600
MIN_CHARS = 10
TTL_CACHE_S = 300.0  # mesmo painel, mesmos filtros e mesmos números: reaproveita o texto
MAX_PERIODOS_ROTULADOS = 12
LIMITE_RESUMO = "10 per hour"  # por usuário; cada geração gasta cota e tokens

MSG_IA_INDISPONIVEL = (
    "O resumo por IA não está disponível: ligue a chave geral e o resumo em Configurações "
    "e confira o servidor."
)
MSG_IA_FALHOU = (
    "Não foi possível gerar o resumo agora (provedor indisponível ou cota atingida). "
    "Os números continuam disponíveis na tela."
)
MSG_SAIDA_INVALIDA = (
    "O resumo gerado não passou na validação e foi descartado. Tente novamente em instantes."
)

PROMPT_SISTEMA = (
    "Você redige resumos de estatísticas de um sistema interno de controle de saída e chegada de "
    "veículos de um órgão público brasileiro. Use SOMENTE os números e rótulos fornecidos. Não "
    "invente valores, causas, tendências nem comparações que não estejam nos dados. Não faça "
    "recomendações, não avalie pessoas e não tome nem sugira decisões. Quando um dado vier como "
    "'menos de 3' ou 'omitido', repita assim, sem estimar. Copie os valores exatamente como "
    "aparecem (mesma unidade e formato), sem converter, arredondar nem somar. Se os dados "
    "forem insuficientes, diga isso. Escreva de 2 a 4 frases curtas em português do Brasil, em "
    "texto corrido, sem listas, sem emojis, sem links, sem HTML e sem aspas."
)

CACHE_RESUMO = CacheTTL(ttl=TTL_CACHE_S)

# Qualquer palavra.palavra com letras depois do ponto parece domínio (agu.gov.br, x.io). Frase
# normal tem espaço depois do ponto final, então "Bom dia." e "1.200" não casam.
_DOMINIO = re.compile(r"\b[\w-]+\.[a-z]{2,}\b", re.IGNORECASE)
_NUMERO = re.compile(r"\d+(?:[.,]\d+)?%?")
_PERIODO = re.compile(r"\d{4}-\d{2}(-\d{2})?")


# -- formatação segura ----------------------------------------------------------------------
def _int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v == v:  # descarta NaN
        return int(round(v))
    return None


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and v == v:
        return float(v)
    return None


def _fmt_num(v: float) -> str:
    return f"{v:.1f}".replace(".", ",") if v != int(v) else str(int(v))


def _duracao(segundos: Any) -> str | None:
    s = _num(segundos)
    if s is None:
        return None
    s = abs(s)
    if s < 60:
        return f"{int(s)} s"
    if s < 3600:
        return f"{round(s / 60)} min"
    h, resto = divmod(int(s), 3600)
    return f"{h} h {resto // 60:02d} min"


def _contagem(v: Any) -> str | None:
    """Contagem aberta por categoria: abaixo do limite vira "menos de 3"."""
    n = _int(v)
    if n is None:
        return None
    return str(n) if n == 0 or n >= LIMITE_CELULA else f"menos de {LIMITE_CELULA}"


def _linha(rotulo: str, valor: str | None) -> list[str]:
    return [f"- {rotulo}: {valor}"] if valor is not None else []


def _estatistica(rotulo: str, bloco: Any, chaves: tuple[tuple[str, str], ...]) -> list[str]:
    """Bloco {n, media_s, ...}: com poucos casos (um valor seria uma viagem) omite os valores."""
    if not isinstance(bloco, dict):
        return []
    n = _int(bloco.get("n"))
    if n is None:
        return []
    if n == 0:
        return _linha(rotulo, "sem casos no recorte")
    if n < LIMITE_CELULA:
        return _linha(rotulo, f"{_contagem(n)} casos (valores omitidos)")
    partes = [f"{n} casos"]
    for chave, nome in chaves:
        d = _duracao(bloco.get(chave))
        if d is not None:
            partes.append(f"{nome} {d}")
    return _linha(rotulo, ", ".join(partes))


_TEMPOS = (("media_s", "média"), ("mediana_s", "mediana"), ("max_s", "máxima"))


# -- painéis ----------------------------------------------------------------------------------
def _fatos_operacao(d: dict[str, Any]) -> list[str]:
    out: list[str] = []
    v = d.get("veiculos")
    if isinstance(v, dict):
        for chave, nome in (
            ("ativos", "Veículos ativos"),
            ("livres", "Veículos livres"),
            ("reservados", "Veículos reservados (pedido solicitado ou autorizado)"),
            ("em_uso", "Veículos em uso"),
        ):
            # reservados/em uso espelham requisições individuais: mesma regra de célula pequena
            fmt = _contagem if chave in {"reservados", "em_uso"} else _fmt_int
            out += _linha(nome, fmt(v.get(chave)))
    for chave, nome in (
        ("solicitadas", "Requisições aguardando autorização"),
        ("autorizadas_sem_saida", "Requisições autorizadas ainda sem saída"),
    ):
        b = d.get(chave)
        if isinstance(b, dict):
            out += _linha(nome, _contagem(b.get("total")))
    uso = d.get("em_uso")
    if isinstance(uso, dict):
        out += _linha("Requisições em uso", _contagem(uso.get("total")))
        n = _int(uso.get("total"))
        if n is not None and n >= LIMITE_CELULA:
            for chave, nome in (("decorrido_medio_s", "média"), ("decorrido_max_s", "máximo")):
                out += _linha(f"Tempo decorrido em uso ({nome})", _duracao(uso.get(chave)))
    # Abertas por unidade e situação: só contagens (ids e veículos ficam de fora).
    contagem: dict[tuple[str, str], int] = {}
    for a in d.get("abertas") or []:
        if isinstance(a, dict) and a.get("unidade") in UNIDADES:
            chave2 = (str(a["unidade"]), str(a.get("status")))
            contagem[chave2] = contagem.get(chave2, 0) + 1
    por_unidade: dict[str, int] = {}
    for (unidade, _), n in contagem.items():
        por_unidade[unidade] = por_unidade.get(unidade, 0) + n
    for unidade in sorted(por_unidade):
        out += _linha(f"Requisições abertas da unidade {unidade}", _contagem(por_unidade[unidade]))
    return out


def _fmt_int(v: Any) -> str | None:
    n = _int(v)
    return None if n is None else str(n)


def _fmt_pct(v: Any) -> str | None:
    x = _num(v)
    return None if x is None else f"{_fmt_num(x)}%"


def _fatos_volume(d: dict[str, Any]) -> list[str]:
    out: list[str] = []
    t = d.get("totais")
    if isinstance(t, dict):
        total = _int(t.get("total"))
        for chave, nome in (
            ("total", "Requisições criadas"),
            ("autorizadas", "Autorizadas"),
            ("negadas", "Negadas"),
            ("canceladas", "Canceladas"),
            ("solicitadas_pendentes", "Ainda aguardando autorização"),
        ):
            out += _linha(nome, _contagem(t.get(chave)))
        # Percentual revelaria a contagem pequena (ou de uma base pequena): só com base e
        # numerador de 3 ou mais.
        if total is not None and total >= LIMITE_CELULA:
            for chave, base, nome in (
                ("pct_autorizadas", "autorizadas", "Percentual autorizado"),
                ("pct_negadas", "negadas", "Percentual negado"),
                ("pct_canceladas", "canceladas", "Percentual cancelado"),
            ):
                n = _int(t.get(base))
                if n is not None and (n == 0 or n >= LIMITE_CELULA):
                    out += _linha(nome, _fmt_pct(t.get(chave)))
    serie = [p for p in d.get("serie") or [] if isinstance(p, dict)]
    if serie:
        out += _linha("Períodos na série", str(len(serie)))
        validos = [p for p in serie if _int(p.get("total")) is not None]
        soma = sum(_int(p.get("total")) or 0 for p in validos)
        if validos and soma >= LIMITE_CELULA:
            maior = max(validos, key=lambda p: _int(p.get("total")) or 0)
            periodo = str(maior.get("periodo"))
            if _PERIODO.fullmatch(periodo):
                out += _linha(
                    "Período com mais requisições",
                    f"{periodo} ({_contagem(maior.get('total'))} requisições)",
                )
            out += _linha(
                "Média de requisições por período", _fmt_num(round(soma / len(validos), 1))
            )
    return out


def _fatos_tempos(d: dict[str, Any]) -> list[str]:
    out = _estatistica("Espera pela autorização", d.get("espera_autorizacao"), _TEMPOS)
    out += _estatistica("Duração do uso", d.get("duracao_uso"), _TEMPOS)
    dif = d.get("saida_vs_prevista")
    out += _estatistica(
        "Diferença entre saída real e prevista",
        dif,
        (("media_s", "média"), ("mediana_s", "mediana"), ("min_s", "mínima"), ("max_s", "máxima")),
    )
    return out


def _fatos_unidades(d: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in d.get("itens") or []:
        if not isinstance(item, dict) or item.get("unidade") not in UNIDADES:
            continue
        u = str(item["unidade"])
        out += _linha(f"Unidade {u}: requisições", _contagem(item.get("requisicoes")))
        usos = _int(item.get("usos"))
        out += _linha(f"Unidade {u}: usos concluídos", _contagem(usos))
        if usos is not None and usos >= LIMITE_CELULA:
            h = _num(item.get("horas_uso"))
            out += _linha(f"Unidade {u}: horas de uso", None if h is None else _fmt_num(h))
    return out


def _fatos_veiculos(d: dict[str, Any]) -> list[str]:
    """Só o conjunto da frota: nenhum veículo é identificado nem listado."""
    itens = [i for i in d.get("itens") or [] if isinstance(i, dict)]
    if not itens:
        return []
    out = _linha("Veículos considerados", str(len(itens)))
    usos = sum(_int(i.get("usos")) or 0 for i in itens)
    out += _linha("Usos concluídos (soma)", _contagem(usos))
    horas = sum(_num(i.get("horas_uso")) or 0.0 for i in itens)
    if usos >= LIMITE_CELULA:  # horas de poucos usos descreveriam uma viagem
        out += _linha("Horas de uso (soma)", _fmt_num(round(horas, 1)))
    taxas = [t for t in (_num(i.get("taxa_ocupacao")) for i in itens) if t is not None]
    if taxas:
        out += _linha("Taxa de ocupação média (0 a 1)", _fmt_num(round(sum(taxas) / len(taxas), 2)))
    out += _linha("Horas do período", _fmt_num(_num(d.get("horas_periodo")) or 0.0))
    return out


def _fatos_opcionais(d: dict[str, Any]) -> list[str]:
    out: list[str] = []
    km = d.get("quilometragem")
    if isinstance(km, dict) and km.get("habilitado"):
        viagens = _int(km.get("viagens"))
        out += _linha("Viagens com quilometragem", _contagem(viagens))
        if viagens is not None and viagens >= LIMITE_CELULA:
            out += _linha("Quilômetros rodados (soma)", _fmt_int(km.get("km_rodado")))
    ck = d.get("checklist")
    if isinstance(ck, dict) and ck.get("habilitado"):
        out += _linha("Checklists registrados", _contagem(ck.get("total")))
        out += _linha("Checklists com avaria", _contagem(ck.get("com_avaria")))
    return out


def _fatos_governanca(d: dict[str, Any]) -> list[str]:
    out: list[str] = []
    perfis = d.get("perfis")
    if isinstance(perfis, dict):
        for role, nome in (
            ("admin", "administradores"),
            ("autorizador", "autorizadores"),
            ("solicitante", "solicitantes"),
        ):
            b = perfis.get(role)
            if isinstance(b, dict):
                out += _linha(f"Contas ativas de {nome}", _contagem(b.get("ativos")))
                out += _linha(f"Contas inativas de {nome}", _contagem(b.get("inativos")))
    conv = d.get("convites")
    if isinstance(conv, dict):
        for chave, nome in (
            ("pendentes", "Convites pendentes"),
            ("expirados", "Convites expirados"),
            ("usados", "Convites usados"),
            ("revogados", "Convites revogados"),
        ):
            out += _linha(nome, _contagem(conv.get(chave)))
    cfg = d.get("configuracoes")
    if isinstance(cfg, dict):
        for chave, nome in (
            ("quilometragem", "Quilometragem"),
            ("abastecimento_manutencao", "Abastecimento e manutenção"),
            ("checklist", "Checklist"),
            ("mfa", "Verificação em duas etapas"),
        ):
            if isinstance(cfg.get(chave), bool):
                out += _linha(f"Recurso {nome}", "ligado" if cfg[chave] else "desligado")
    return out


_PAINEIS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "operacao": _fatos_operacao,
    "volume": _fatos_volume,
    "tempos": _fatos_tempos,
    "unidades": _fatos_unidades,
    "veiculos": _fatos_veiculos,
    "opcionais": _fatos_opcionais,
    "governanca": _fatos_governanca,
}


def _cabecalho_filtros(filtros: dict[str, Any]) -> list[str]:
    """Só filtros de valor controlado: datas, código de unidade e opções fixas."""
    out: list[str] = []
    ini, fim = filtros.get("inicio"), filtros.get("fim")
    if isinstance(ini, str) and isinstance(fim, str) and _PERIODO.fullmatch(ini[:10]):
        out.append(f"Período: {ini[:10]} a {fim[:10]} (fuso de São Paulo)")
    if filtros.get("unidade") in UNIDADES:
        out.append(f"Filtro de unidade: {filtros['unidade']}")
    if filtros.get("veiculo"):
        out.append("Filtro por um veículo específico: sim")  # o veículo não é identificado
    for chave, nome in (("granularidade", "Granularidade"), ("agrupar", "Agrupamento")):
        v = filtros.get(chave)
        if isinstance(v, str) and v in {"dia", "semana", "mes", "unidade", "veiculo"}:
            out.append(f"{nome}: {v}")
    campo = filtros.get("campo_data")
    if isinstance(campo, str) and campo in {"criacao", "saida_prevista", "saida_real"}:
        out.append(f"Data usada no filtro: {campo}")
    return out


def prompt_painel(agregados: dict[str, Any]) -> str:
    """Texto enviado ao provedor para um painel. `agregados` vem de `ServicoDashboards`."""
    chave = str(agregados.get("painel"))
    construtor = _PAINEIS.get(chave)
    fatos = construtor(agregados.get("dados") or {}) if construtor else []
    cab = [f"Painel: {agregados.get('rotulo') or chave}"]
    cab += _cabecalho_filtros(agregados.get("filtros") or {})
    return _montar(cab, fatos)


# -- relatórios -------------------------------------------------------------------------------
def _valores(linhas: list[dict[str, Any]], chave: str) -> list[float]:
    return [x for x in (_num(r.get(chave)) for r in linhas) if x is not None]


def _fatos_duracao(rotulo: str, valores: list[float]) -> list[str]:
    if not valores:
        return []
    if len(valores) < LIMITE_CELULA:
        return _linha(rotulo, f"menos de {LIMITE_CELULA} casos (valores omitidos)")
    media = _duracao(sum(valores) / len(valores))
    return _linha(rotulo, f"{len(valores)} casos, média {media}, máxima {_duracao(max(valores))}")


def _contar_por(linhas: list[dict[str, Any]], chave: str, validos: set[str]) -> dict[str, int]:
    cont: dict[str, int] = {}
    for r in linhas:
        v = r.get(chave)
        if isinstance(v, str) and v in validos:
            cont[v] = cont.get(v, 0) + 1
    return cont


_STATUS = {"solicitada", "autorizada", "em_uso", "concluida", "negada", "cancelada"}
_COMBUSTIVEL = {"vazio", "um_quarto", "meio", "tres_quartos", "cheio"}


def _fatos_linhas(c: Conjunto) -> list[str]:
    """Relatórios de uma linha por registro: só contagens por categoria e durações."""
    linhas = c.linhas
    out = _linha("Registros no relatório", _contagem(len(linhas)))
    for nome, chave, validos in (
        ("Situação", "status", _STATUS),
        ("Unidade", "unidade", set(UNIDADES)),
        ("Momento do checklist", "momento", {"saida", "chegada"}),
        ("Combustível", "combustivel", _COMBUSTIVEL),
    ):
        for valor, n in sorted(_contar_por(linhas, chave, validos).items()):
            out += _linha(f"{nome} {valor}", _contagem(n))
    out += _fatos_duracao("Espera pela autorização", _valores(linhas, "espera_autorizacao_s"))
    out += _fatos_duracao("Duração do uso", _valores(linhas, "duracao_uso_s"))
    out += _fatos_duracao("Tempo no estado atual", _valores(linhas, "decorrido_s"))
    km = _valores(linhas, "km_rodado")
    if len(km) >= LIMITE_CELULA:
        out += _linha("Km rodado (soma)", str(int(sum(km))))
        out += _linha("Km rodado (média por viagem)", _fmt_num(round(sum(km) / len(km), 1)))
    elif km:
        out += _linha("Km rodado", f"menos de {LIMITE_CELULA} viagens (valores omitidos)")
    return out


def _rotulo_seguro(rotulo: Any, agrupar: str) -> str | None:
    """Rótulo de grupo só quando é data/mês/código de unidade; veículo nunca."""
    if not isinstance(rotulo, str):
        return None
    if agrupar in {"dia", "semana", "mes"} and _PERIODO.fullmatch(rotulo[:10]):
        return rotulo[:10]
    if agrupar == "unidade" and rotulo in UNIDADES:
        return rotulo
    return None


def _fatos_resumo(c: Conjunto, agrupar: str) -> list[str]:
    linhas = c.linhas
    out = _linha("Grupos no relatório", str(len(linhas)))
    for chave, nome in (
        ("requisicoes", "Requisições"),
        ("autorizadas", "Autorizadas"),
        ("negadas", "Negadas"),
        ("canceladas", "Canceladas"),
        ("concluidas", "Concluídas"),
    ):
        valores = _valores(linhas, chave)
        if valores:
            out += _linha(f"{nome} (soma)", _contagem(int(sum(valores))))
    horas = _valores(linhas, "horas_uso")
    if horas and sum(_valores(linhas, "requisicoes")) >= LIMITE_CELULA:
        out += _linha("Horas de uso (soma)", _fmt_num(round(sum(horas), 1)))
    rotulados = [
        (_rotulo_seguro(r.get("rotulo"), agrupar), r)
        for r in linhas
        if _rotulo_seguro(r.get("rotulo"), agrupar) is not None
    ]
    if agrupar == "unidade":
        for rotulo, r in rotulados:
            out += _linha(f"Unidade {rotulo}: requisições", _contagem(r.get("requisicoes")))
    elif rotulados:
        melhor = max(rotulados, key=lambda x: _int(x[1].get("requisicoes")) or 0)
        out += _linha(
            "Grupo com mais requisições",
            f"{melhor[0]} ({_contagem(_int(melhor[1].get('requisicoes')) or 0)})",
        )
    return out


_TEMPOS_REL = (
    ("espera", "Espera pela autorização"),
    ("uso", "Duração do uso"),
    ("saida_dif", "Diferença entre saída real e prevista"),
)


def _fatos_tempos_rel(c: Conjunto, agrupar: str) -> list[str]:
    linhas = c.linhas
    out = _linha("Grupos no relatório", str(len(linhas)))
    for prefixo, nome in _TEMPOS_REL:
        total = sum(_int(r.get(f"{prefixo}_n")) or 0 for r in linhas)
        out += _linha(f"{nome}: casos", _contagem(total))
        if agrupar in {"unidade", "mes"}:  # rótulos seguros: estatística por grupo
            for r in linhas[:MAX_PERIODOS_ROTULADOS]:
                rotulo = _rotulo_seguro(r.get("rotulo"), "mes" if agrupar == "mes" else "unidade")
                n = _int(r.get(f"{prefixo}_n"))
                if rotulo is None or n is None:
                    continue
                if n < LIMITE_CELULA:
                    out += _linha(f"{nome} em {rotulo}", f"menos de {LIMITE_CELULA} casos")
                else:
                    media = _duracao(r.get(f"{prefixo}_media_s"))
                    out += _linha(f"{nome} em {rotulo}", f"{n} casos, média {media}")
        elif total >= LIMITE_CELULA:
            maximos = _valores(linhas, f"{prefixo}_max_s")
            if maximos:
                out += _linha(f"{nome}: maior valor", _duracao(max(maximos)))
    return out


def _unidade_escopo(c: Conjunto) -> str:
    return "grupos" if c.tipo in {"resumo", "tempos"} else "registros"


def _escopo_prompt(c: Conjunto) -> str:
    n, un = _contagem(len(c.linhas)), _unidade_escopo(c)
    if c.truncado:
        return (
            f"parcial, os primeiros {n} {un}; o relatório tem mais {un} que o limite de "
            "processamento"
        )
    return f"conjunto completo do relatório, {n} {un}"


def escopo_relatorio(c: Conjunto) -> str:
    """Texto curto para a UI: deixa claro que o resumo NÃO é só das linhas da prévia."""
    n, un = _contagem(len(c.linhas)), _unidade_escopo(c)
    if c.truncado:
        return f"Resumo parcial: primeiros {n} {un} (o relatório tem mais que o limite)"
    return f"Resumo do relatório completo ({n} {un})"


ESCOPO_PAINEL = "Resumo dos números do painel com os filtros atuais"
ESCOPO_PREVIA_GET = "Resumo do relatório completo, não só das linhas da prévia"


def prompt_relatorio(c: Conjunto, ef: dict[str, Any]) -> str:
    """Texto enviado ao provedor para uma prévia de relatório (já filtrada e com escopo)."""
    if c.tipo in SEM_RESUMO:
        raise ErroApi("Este relatório não tem resumo automático.", "resumo_indisponivel", 400)
    if c.tipo == "resumo":
        fatos = _fatos_resumo(c, str(ef.get("agrupar")))
    elif c.tipo == "tempos":
        fatos = _fatos_tempos_rel(c, str(ef.get("agrupar")))
    else:
        fatos = _fatos_linhas(c)
    cab = [f"Relatório: {RELATORIOS[c.tipo].rotulo}", f"Escopo: {_escopo_prompt(c)}"]
    cab += _cabecalho_filtros(ef)
    return _montar(cab, fatos)


def _montar(cabecalho: list[str], fatos: list[str]) -> str:
    corpo = fatos or ["- (sem dados no recorte)"]
    return "\n".join([*cabecalho, "Dados agregados:", *corpo])


# -- saída ------------------------------------------------------------------------------------
def _numeros(texto: str) -> list[str]:
    """Números do texto, sem separador de milhar ("1.200" vira "1200"); mantém o "%"."""
    return _NUMERO.findall(re.sub(r"(?<=\d)\.(?=\d{3}\b)", "", texto))


def _numeros_inventados(texto: str, prompt: str) -> bool:
    """True se o texto cita um número relevante (2+ dígitos ou percentual) fora do prompt.

    Números de um dígito ("3 a 4 frases", "1 caso") passam: são comuns na prosa. A checagem não
    impede um erro de interpretação, só números que o modelo criou do nada.
    """
    permitidos = {n.rstrip("%") for n in _numeros(prompt)}
    for n in _numeros(texto):
        digitos = n.rstrip("%").replace(",", "").replace(".", "")
        if (len(digitos) >= 2 or n.endswith("%")) and n.rstrip("%") not in permitidos:
            return True
    return False


def validar_saida(bruto: str, prompt: str | None = None) -> str | None:
    """Texto limpo (uma linha) ou None se não servir: tamanho, HTML, link, e-mail, domínio,
    marcador e (com `prompt`) números que não estavam nos dados."""
    texto = " ".join(bruto.split())
    if not MIN_CHARS <= len(texto) <= MAX_CHARS:
        return None
    if any(unicodedata.category(c).startswith("C") for c in texto):
        return None
    baixo = texto.casefold()
    if "<" in texto or ">" in texto or "@" in texto or "{" in texto or "}" in texto:
        return None
    if "http" in baixo or "www." in baixo or _DOMINIO.search(texto):
        return None
    if prompt is not None and _numeros_inventados(texto, prompt):
        return None
    return texto


# -- serviço ----------------------------------------------------------------------------------
class ServicoResumoIA:
    def __init__(
        self,
        ia: ServicoIA | None,
        *,
        cache: CacheTTL | None = None,
        agora: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ia = ia
        self._cache = cache if cache is not None else CACHE_RESUMO
        self._agora = agora

    def disponivel(self) -> bool:
        """Chave geral e resumo ligados, servidor com IA e repositório (sem chamar o provedor)."""
        return bool(self._ia is not None and self._ia.disponivel(FUNCAO_IA))

    def disponivel_relatorio(self, tipo: str) -> bool:
        return tipo not in SEM_RESUMO and self.disponivel()

    def resumir_painel(self, perfil: Perfil, agregados: dict[str, Any]) -> dict[str, Any]:
        self._exigir_disponivel()
        return self._resumir(
            perfil, f"painel:{agregados.get('painel')}", prompt_painel(agregados), ESCOPO_PAINEL
        )

    def resumir_relatorio(self, perfil: Perfil, c: Conjunto, ef: dict[str, Any]) -> dict[str, Any]:
        self._exigir_disponivel()
        return self._resumir(
            perfil, f"relatorio:{c.tipo}", prompt_relatorio(c, ef), escopo_relatorio(c)
        )

    def _exigir_disponivel(self) -> None:
        if not self.disponivel():
            raise ErroApi(MSG_IA_INDISPONIVEL, "ia_indisponivel", 409)

    def _resumir(self, perfil: Perfil, contexto: str, prompt: str, escopo: str) -> dict[str, Any]:
        # O escopo do usuário (id e papel) entra na chave: um solicitante nunca reaproveita o
        # texto de outro, mesmo que os números coincidam.
        chave = hashlib.sha256(
            "|".join([perfil.id, perfil.role, contexto, prompt]).encode("utf-8")
        ).hexdigest()

        def gerar() -> dict[str, Any]:
            assert self._ia is not None  # noqa: S101 - garantido por _exigir_disponivel
            r = self._ia.gerar(FUNCAO_IA, sistema=PROMPT_SISTEMA, usuario=prompt, perfil=perfil)
            if r is None:
                raise ErroApi(MSG_IA_FALHOU, "ia_falhou", 502)
            texto = validar_saida(r.texto, prompt)
            if texto is None:
                raise ErroApi(MSG_SAIDA_INVALIDA, "ia_falhou", 502)
            return {"texto": texto, "gerado_em": self._agora().isoformat()}

        resultado = self._cache.obter(("resumo", chave), gerar)
        return {
            "texto": resultado["texto"],
            "gerado_por_ia": True,
            "gerado_em": resultado["gerado_em"],
            "escopo": escopo,
        }
