"""Calendário de requisições (somente leitura): janela de datas locais -> itens mínimos.

Permissão `ver_calendario` (services/perfis.py) e a MESMA visibilidade da listagem
(anti-IDOR na consulta: dono, motorista atribuído, vigilante), ignorando o arquivamento
pessoal. Cada item leva só o necessário para desenhar o evento (LGPD): nada de nome, e-mail,
telefone, trajeto, observação, km nem localização. `inicio`/`fim` são calculados aqui.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.errors import RequisicaoInvalida, SemPermissao
from app.fuso import FUSO
from app.modelos import Perfil
from app.repositorios.base import Repositorio
from app.services.filtros_listagem import CODIGO_FILTRO
from app.services.perfis import permissoes
from app.services.requisicoes import ESTADOS_VIGILANTE, STAFF

JANELA_MAX_DIAS = 62
MAX_ITENS = 500
ANO_MIN, ANO_MAX = 2019, 2100
STATUS_CALENDARIO = ("solicitada", "autorizada", "em_uso", "concluida")
# Só dígitos ASCII: `\d` aceitaria dígitos unicode (ex.: largura total "２０２６").
_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# Colunas lidas: as do item + as usadas só no cálculo (nunca dado pessoal).
CAMPOS_ITEM = ("id", "status", "veiculo_id", "unidade", "hora_saida")
CAMPOS_REAIS = ("saida_real_em", "chegada_real_em")


def _invalido(mensagem: str) -> RequisicaoInvalida:
    return RequisicaoInvalida(mensagem, CODIGO_FILTRO)


def _data(args: Any, nome: str) -> date:
    bruto = args.get(nome)
    valor = str(bruto).strip() if bruto is not None else ""
    if not valor:
        raise _invalido(f"O parâmetro '{nome}' é obrigatório (AAAA-MM-DD).")
    msg = f"O parâmetro '{nome}' deve ser uma data no formato AAAA-MM-DD."
    if not _DATA.fullmatch(valor):
        raise _invalido(msg)
    try:
        dia = date.fromisoformat(valor)
    except ValueError:
        raise _invalido(msg) from None
    if not ANO_MIN <= dia.year <= ANO_MAX:
        raise _invalido(f"O parâmetro '{nome}' deve estar entre {ANO_MIN} e {ANO_MAX}.")
    return dia


def _meia_noite_utc(dia: date) -> str:
    """00:00 local (America/Sao_Paulo) em UTC, sem caracteres especiais para o filtro."""
    local = datetime(dia.year, dia.month, dia.day, tzinfo=FUSO)
    return local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def validar_janela(args: Any) -> tuple[date, date]:
    inicio, fim = _data(args, "inicio"), _data(args, "fim")
    if fim < inicio:
        raise _invalido("A data final não pode ser anterior à data inicial.")
    if (fim - inicio).days + 1 > JANELA_MAX_DIAS:
        raise _invalido(f"A janela máxima do calendário é de {JANELA_MAX_DIAS} dias.")
    return inicio, fim


class ServicoCalendario:
    def __init__(self, repo: Repositorio) -> None:
        self._repo = repo

    def listar(self, perfil: Perfil, args: Any) -> dict[str, Any]:
        config = self._repo.ler_configuracoes()
        if "ver_calendario" not in permissoes(perfil, config):
            raise SemPermissao("Você não tem permissão para ver o calendário.")
        inicio, fim = validar_janela(args)

        # Visibilidade idêntica a ServicoRequisicoes.listar (sem o filtro de arquivadas).
        dono: str | None = None if perfil.role in STAFF else perfil.id
        motorista: str | None = None
        if perfil.role == "motorista":
            dono, motorista = None, perfil.id
        vigilante = perfil.role == "vigilante"
        linhas = self._repo.listar_calendario(
            inicio=_meia_noite_utc(inicio),
            fim=_meia_noite_utc(fim + timedelta(days=1)),  # fim inclusivo -> exclusivo
            usuario_id=dono,
            motorista_id=motorista,
            ou_status=ESTADOS_VIGILANTE if vigilante else None,
            ou_registrada_por=perfil.id if vigilante else None,
            limite=MAX_ITENS + 1,  # um a mais só para saber se truncou
        )
        truncado = len(linhas) > MAX_ITENS
        linhas = linhas[:MAX_ITENS]
        rotulos = self._repo.rotulos_veiculos(
            {r["veiculo_id"] for r in linhas if r.get("veiculo_id")}
        )
        return {"itens": [self._item(r, rotulos) for r in linhas], "truncado": truncado}

    @staticmethod
    def _item(r: dict[str, Any], rotulos: dict[str, str]) -> dict[str, Any]:
        """Allowlist explícita: só estas chaves saem, mesmo que o repositório traga mais."""
        item = {c: r.get(c) for c in CAMPOS_ITEM}
        item["veiculo_rotulo"] = rotulos.get(r.get("veiculo_id") or "")
        item["inicio"] = r.get("saida_real_em") or r.get("hora_saida")
        item["fim"] = r.get("chegada_real_em")
        item |= {c: r.get(c) for c in CAMPOS_REAIS}
        return item
