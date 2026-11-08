"""Dashboards: quais painéis cada perfil vê, validação dos filtros e montagem da resposta.

A agregação é feita no banco (RPCs `dash_*`, que devolvem jsonb sem dado pessoal); aqui ficam a
permissão, a validação de tudo o que vem da query e a conversão de datas locais em janela.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from app.errors import NaoEncontrado, RecursoDesligado, RequisicaoInvalida, SemPermissao
from app.modelos import UNIDADES, Perfil
from app.repositorios.base import Repositorio
from app.services.requisicoes import STAFF, ServicoRequisicoes
from app.services.validacao import validar_uuid

FUSO_NOME = "America/Sao_Paulo"
# Brasil sem horário de verão desde 2019: deslocamento fixo -03:00. O banco continua agrupando
# por America/Sao_Paulo, equivalente para datas >= 2019. Único ponto de definição do fuso.
FUSO = timezone(timedelta(hours=-3))
DIAS_PADRAO = 30
DIAS_MAXIMOS = 366  # espelha dash_validar_janela
GRANULARIDADES = ("dia", "semana", "mes")
# 2019: o deslocamento fixo -03:00 só equivale ao America/Sao_Paulo do banco a partir daí.
ANO_MIN, ANO_MAX = 2019, 2100
MAX_ITENS_OPERACAO = 200  # corte defensivo de `abertas`/`itens` (ordem de urgência preservada)
_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Painel:
    chave: str
    rotulo: str
    janela: bool = True  # usa inicio/fim
    granularidade: bool = False
    entidades: bool = True  # usa unidade/veiculo


PAINEIS: dict[str, Painel] = {
    p.chave: p
    for p in (
        Painel("operacao", "Operação agora", janela=False),
        Painel("volume", "Volume e desfecho", granularidade=True),
        Painel("tempos", "Tempos do fluxo"),
        Painel("unidades", "Uso por unidade"),
        Painel("veiculos", "Uso por veículo"),
        Painel("opcionais", "Recursos opcionais"),
        Painel("governanca", "Governança", janela=False, entidades=False),
    )
}


@dataclass(frozen=True)
class Filtros:
    inicio: date  # inclusivo, data local
    fim: date  # inclusivo, data local
    unidade: str | None
    veiculo: str | None
    granularidade: str

    @property
    def inicio_utc(self) -> str:
        return _meia_noite_local(self.inicio)

    @property
    def fim_exclusivo_utc(self) -> str:
        """Fim inclusivo vira exclusivo: 00:00 local do dia seguinte."""
        return _meia_noite_local(self.fim + timedelta(days=1))


def _meia_noite_local(dia: date) -> str:
    return datetime(dia.year, dia.month, dia.day, tzinfo=FUSO).astimezone(UTC).isoformat()


def _texto(args: Any, nome: str) -> str | None:
    bruto = args.get(nome)
    if bruto is None:
        return None
    valor = str(bruto).strip()
    return valor or None  # campo vazio de formulário = não informado


def _data(args: Any, nome: str) -> date | None:
    valor = _texto(args, nome)
    if valor is None:
        return None
    msg = f"O parâmetro '{nome}' deve ser uma data no formato AAAA-MM-DD."
    if not _DATA.fullmatch(valor):
        raise RequisicaoInvalida(msg)
    try:
        dia = date.fromisoformat(valor)
    except ValueError:
        raise RequisicaoInvalida(msg) from None
    if not ANO_MIN <= dia.year <= ANO_MAX:
        raise RequisicaoInvalida(f"O parâmetro '{nome}' deve estar entre {ANO_MIN} e {ANO_MAX}.")
    return dia


def validar_filtros(args: Any, hoje: date) -> Filtros:
    """Valida a query string. Padrão: últimos 30 dias (hoje incluído) no fuso de São Paulo."""
    inicio, fim = _data(args, "inicio"), _data(args, "fim")
    if inicio is None and fim is None:
        fim = hoje
    if fim is None:
        fim = hoje
    if inicio is None:
        inicio = fim - timedelta(days=DIAS_PADRAO - 1)
    if fim < inicio:
        raise RequisicaoInvalida("A data final não pode ser anterior à data inicial.")
    if (fim - inicio).days + 1 > DIAS_MAXIMOS:
        raise RequisicaoInvalida(f"O período máximo do painel é de {DIAS_MAXIMOS} dias.")

    unidade = _texto(args, "unidade")
    if unidade is not None and unidade not in UNIDADES:
        raise RequisicaoInvalida("O parâmetro 'unidade' deve ser PU, PF, CJU ou UEA.")
    veiculo = _texto(args, "veiculo")
    if veiculo is not None:
        veiculo = validar_uuid(veiculo, "veiculo")
    granularidade = _texto(args, "granularidade") or "dia"
    if granularidade not in GRANULARIDADES:
        raise RequisicaoInvalida("O parâmetro 'granularidade' deve ser dia, semana ou mes.")
    return Filtros(inicio, fim, unidade, veiculo, granularidade)


def _agora_utc() -> datetime:
    return datetime.now(UTC)


class ServicoDashboards:
    def __init__(
        self,
        repo: Repositorio,
        requisicoes: ServicoRequisicoes,
        *,
        relogio: Callable[[], datetime] = _agora_utc,
    ) -> None:
        self._repo = repo
        self._req = requisicoes
        self._relogio = relogio

    # -- permissões -----------------------------------------------------------------
    def _chaves_permitidas(self, perfil: Perfil) -> list[str]:
        if perfil.role not in STAFF:
            raise SemPermissao("Você não tem permissão para esta ação.")
        config = self._repo.ler_configuracoes()
        chaves = ["operacao", "volume", "tempos", "unidades", "veiculos"]
        if config.quilometragem or config.checklist:
            chaves.append("opcionais")
        if perfil.role == "admin":
            chaves.append("governanca")
        return chaves

    def paineis_permitidos(self, perfil: Perfil) -> dict[str, Any]:
        return {
            "paineis": [
                {"chave": c, "rotulo": PAINEIS[c].rotulo} for c in self._chaves_permitidas(perfil)
            ],
            "fuso": FUSO_NOME,
            "periodo_padrao_dias": DIAS_PADRAO,
            "periodo_maximo_dias": DIAS_MAXIMOS,
        }

    # -- leitura --------------------------------------------------------------------
    def obter(self, perfil: Perfil, chave: str, args: Any) -> dict[str, Any]:
        permitidas = self._chaves_permitidas(perfil)  # solicitante: 403 antes de tudo
        painel = PAINEIS.get(chave)
        if painel is None:
            raise NaoEncontrado("Painel não encontrado.")
        if chave not in permitidas:
            if chave == "opcionais":
                raise RecursoDesligado(
                    "Nenhum recurso opcional (quilometragem ou checklist) está habilitado."
                )
            raise SemPermissao("Você não tem permissão para ver este painel.")
        hoje = self._relogio().astimezone(FUSO).date()
        f = validar_filtros(args, hoje)
        dados = self._consultar(chave, f)
        resposta: dict[str, Any] = {"painel": chave, "filtros": self._efetivos(painel, f)}
        if chave == "operacao":
            abertas = dados.get("abertas") or []
            resposta["itens_truncados"] = len(abertas) > MAX_ITENS_OPERACAO
            dados["abertas"] = abertas[:MAX_ITENS_OPERACAO]
            resposta["itens"] = self._itens_operacao(perfil, dados)
        resposta["dados"] = dados
        return resposta

    @staticmethod
    def _efetivos(painel: Painel, f: Filtros) -> dict[str, Any]:
        """Só os filtros que o painel usa, com os valores efetivamente aplicados."""
        saida: dict[str, Any] = {}
        if painel.janela:
            saida |= {"inicio": f.inicio.isoformat(), "fim": f.fim.isoformat(), "fuso": FUSO_NOME}
        if painel.granularidade:
            saida["granularidade"] = f.granularidade
        if painel.entidades:
            saida |= {"unidade": f.unidade, "veiculo": f.veiculo}
        return saida

    def _consultar(self, chave: str, f: Filtros) -> dict[str, Any]:
        r = self._repo
        if chave == "operacao":
            return r.dash_operacao_agora(unidade=f.unidade, veiculo=f.veiculo)
        if chave == "governanca":
            return r.dash_governanca()
        janela = {
            "inicio": f.inicio_utc,
            "fim": f.fim_exclusivo_utc,
            "unidade": f.unidade,
            "veiculo": f.veiculo,
        }
        if chave == "volume":
            return r.dash_volume_desfecho(**janela, granularidade=f.granularidade)
        consulta = {
            "tempos": r.dash_tempos_fluxo,
            "unidades": r.dash_uso_unidade,
            "veiculos": r.dash_uso_veiculo,
            "opcionais": r.dash_opcionais,
        }[chave]
        return consulta(**janela)

    def _itens_operacao(self, perfil: Perfil, dados: dict[str, Any]) -> list[dict[str, Any]]:
        """Requisições abertas, na ordem de urgência da RPC, em uma só consulta (sem N+1)."""
        ids = [str(a["id"]) for a in dados.get("abertas") or [] if a.get("id")]
        por_id = {r["id"]: r for r in self._repo.obter_requisicoes_por_ids(ids)}
        return self._req.apresentar_lote(perfil, [por_id[i] for i in ids if i in por_id])
