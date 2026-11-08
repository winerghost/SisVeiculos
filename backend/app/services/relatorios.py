"""Relatórios: catálogo por perfil, validação dos filtros, consulta, exportação e envio por e-mail.

O banco só LÊ (RPCs `rel_*`); aqui ficam a permissão por role, o escopo do solicitante (sempre o
id do JWT, nunca um parâmetro do cliente), os flags de dado pessoal, a validação de tudo o que
vem da query/corpo, o limite de linhas e a auditoria. As rotas só traduzem HTTP.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.config import Config
from app.errors import (
    DestinatarioInvalido,
    ErroApi,
    ErroInterno,
    NaoEncontrado,
    RecursoDesligado,
    RelatorioGrande,
    RequisicaoInvalida,
    SemPermissao,
)
from app.modelos import UNIDADES_ORDEM, Configuracoes, Perfil
from app.notificacoes.email import (
    ConfigSmtp,
    EnviadorEmail,
    EnviadorSmtp,
    FalhaEmail,
    mensagem_do_codigo,
)
from app.repositorios.auth_admin import AuthAdmin
from app.repositorios.base import Repositorio
from app.services.dashboards import (
    DIAS_MAXIMOS,
    DIAS_PADRAO,
    FUSO,
    FUSO_NOME,
    _data,
    _meia_noite_local,
    _texto,
)
from app.services.exportacao import (
    Coluna,
    data_arquivo,
    formatar_linhas,
    gerar_csv,
    gerar_xlsx,
)
from app.services.notificacoes import CODIGOS_SEM_ALERTA, registrar_falha_sistema
from app.services.requisicoes import STAFF
from app.services.validacao import validar_uuid

log = logging.getLogger(__name__)

PREVIA_MAX = 50
FORMATOS = ("csv", "xlsx")
VIAS = ("download", "compartilhar")
MIMETYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
MAX_DESTINATARIOS = 500
MSG_SEM_PERMISSAO = "Você não tem permissão para este relatório."

# Execução dos envios em segundo plano (a requisição devolve 202 sem esperar o SMTP). Um processo
# gunicorn com 8 threads: 2 trabalhadores de e-mail bastam. Os testes substituem este objeto.
EXECUTOR_EMAIL: Executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="relatorio-email")


# -- catálogo ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class Relatorio:
    tipo: str
    rotulo: str
    descricao: str
    filtros: tuple[str, ...]
    papeis: frozenset[str]  # quem pode pedir
    recurso: str | None = None  # recurso opcional que precisa estar ligado


TODOS = frozenset({"solicitante", "autorizador", "admin"})
RELATORIOS: dict[str, Relatorio] = {
    r.tipo: r
    for r in (
        Relatorio(
            "movimentacao",
            "Movimentação de requisições",
            "Uma linha por requisição: datas, status, veículo e tempos.",
            (
                "inicio",
                "fim",
                "campo_data",
                "unidade",
                "veiculo",
                "incluir_nomes",
                "incluir_trajeto",
            ),
            TODOS,
        ),
        Relatorio(
            "resumo",
            "Resumo do período",
            "Contagem de requisições e horas de uso por dia, semana, mês, unidade ou veículo.",
            ("inicio", "fim", "agrupar", "unidade", "veiculo"),
            STAFF,
        ),
        Relatorio(
            "tempos",
            "Tempos do fluxo",
            "Espera pela autorização, duração do uso e diferença entre a saída real e a prevista.",
            ("inicio", "fim", "agrupar", "unidade", "veiculo"),
            STAFF,
        ),
        Relatorio(
            "pendencias",
            "Pendências agora",
            "Requisições abertas neste momento (solicitadas, autorizadas e em uso).",
            ("unidade", "veiculo", "incluir_nomes"),
            TODOS,
        ),
        Relatorio(
            "auditoria",
            "Trilha de auditoria",
            "Eventos registrados no período (somente administradores).",
            ("inicio", "fim", "acao"),
            frozenset({"admin"}),
        ),
        Relatorio(
            "opcionais_km",
            "Quilometragem por viagem",
            "Viagens concluídas com km de saída, de chegada e km rodado.",
            ("inicio", "fim", "unidade", "veiculo"),
            STAFF,
            "quilometragem",
        ),
        Relatorio(
            "opcionais_avarias",
            "Checklists com avaria",
            "Checklists registrados com avaria e nível de combustível (sem o texto livre).",
            ("inicio", "fim", "unidade", "veiculo"),
            STAFF,
            "checklist",
        ),
    )
}

# Painel do dashboard -> relatório equivalente (botão "exportar" de cada painel).
EXPORTACAO_PAINEIS: dict[str, tuple[str, dict[str, str]]] = {
    "operacao": ("pendencias", {}),
    "volume": ("resumo", {"agrupar": "dia"}),
    "tempos": ("tempos", {"agrupar": "unidade"}),
    "unidades": ("resumo", {"agrupar": "unidade"}),
    "veiculos": ("resumo", {"agrupar": "veiculo"}),
    "governanca": ("auditoria", {}),
    "opcionais": ("opcionais_km", {}),
}

CAMPOS_DATA = (
    ("criacao", "Data de criação"),
    ("saida_prevista", "Saída prevista"),
    ("saida_real", "Saída real"),
)
AGRUPAR_RESUMO = (
    ("dia", "Dia"),
    ("semana", "Semana"),
    ("mes", "Mês"),
    ("unidade", "Unidade"),
    ("veiculo", "Veículo"),
)
AGRUPAR_TEMPOS = (("unidade", "Unidade"), ("veiculo", "Veículo"), ("mes", "Mês"))
ACOES_AUDITORIA = (
    ("criada", "Requisição criada"),
    ("transicao", "Mudança de estado"),
    ("convite_criado", "Convite criado"),
    ("convite_usado", "Convite usado"),
    ("convite_revogado", "Convite revogado"),
    ("perfil_criado", "Perfil criado"),
    ("perfil_alterado", "Perfil alterado"),
    ("perfil_unidade_alterada", "Unidade do perfil alterada"),
    ("admin_semeado", "Administrador inicial"),
    ("configuracao_alterada", "Configuração alterada"),
    ("veiculo_criado", "Veículo criado"),
    ("veiculo_alterado", "Veículo alterado"),
    ("acao_administrativa", "Ação administrativa"),
    ("acesso_negado", "Acesso negado"),
    ("checklist_registrado", "Checklist registrado"),
    ("foto_perfil_alterada", "Foto de perfil alterada"),
    ("foto_perfil_removida", "Foto de perfil removida"),
    ("relatorio_gerado", "Relatório gerado"),
    ("relatorio_enviado", "Relatório enviado"),
    ("motorista_designado", "Motorista designado"),
    ("localizacao_registrada", "Localização registrada"),
)

_STATUS = {
    "solicitada": "Solicitada",
    "autorizada": "Autorizada",
    "em_uso": "Em uso",
    "concluida": "Concluída",
    "negada": "Negada",
    "cancelada": "Cancelada",
}
_MOMENTO = {"saida": "Saída", "chegada": "Chegada"}
_COMBUSTIVEL = {
    "vazio": "Vazio",
    "um_quarto": "1/4",
    "meio": "1/2",
    "tres_quartos": "3/4",
    "cheio": "Cheio",
}
_TABELAS = {
    "requisicoes": "Requisições",
    "convites": "Convites",
    "perfis": "Perfis",
    "veiculos": "Veículos",
    "configuracoes": "Configurações",
    "checklists": "Checklists",
    "perfil_fotos": "Fotos de perfil",
    "relatorios": "Relatórios",
    "envios_email": "Envios de e-mail",
}


def _c(chave: str, rotulo: str, tipo: str = "texto", mapa: dict[str, str] | None = None) -> Coluna:
    return Coluna(chave, rotulo, tipo, mapa or {})


# Lista branca de colunas: o que o banco devolver fora daqui (ex.: `veiculo_id`, uma futura
# `descricao_avarias`) nunca chega à prévia nem ao arquivo.
COLUNAS: dict[str, Coluna] = {
    c.chave: c
    for c in (
        _c("codigo", "Código"),
        _c("criada_em", "Criada em", "datahora"),
        _c("unidade", "Unidade"),
        _c("veiculo", "Veículo"),
        _c("status", "Status", mapa=_STATUS),
        _c("hora_saida_prevista", "Saída prevista", "datahora"),
        _c("autorizada_em", "Decisão em", "datahora"),
        _c("saida_real_em", "Saída real", "datahora"),
        _c("chegada_real_em", "Chegada real", "datahora"),
        _c("espera_autorizacao_s", "Espera pela autorização (hh:mm:ss)", "duracao"),
        _c("duracao_uso_s", "Duração do uso (hh:mm:ss)", "duracao"),
        _c("km_saida", "Km de saída", "inteiro"),
        _c("km_chegada", "Km de chegada", "inteiro"),
        _c("km_rodado", "Km rodado", "inteiro"),
        _c("solicitante", "Solicitante"),
        _c("autorizado_por", "Autorizado por"),
        _c("saida_por", "Saída registrada por"),
        _c("chegada_por", "Chegada registrada por"),
        _c("trajeto", "Trajeto"),
        _c("desde", "Desde", "datahora"),
        _c("decorrido_s", "Tempo no estado atual (hh:mm:ss)", "duracao"),
        _c("ocorrido_em", "Ocorrido em", "datahora"),
        _c("tabela", "Tabela", mapa=_TABELAS),
        _c("acao", "Ação", mapa=dict(ACOES_AUDITORIA)),
        _c("de", "De"),
        _c("para", "Para"),
        _c("ator_nome", "Responsável"),
        _c("registro_id", "Registro"),
        _c("momento", "Momento", mapa=_MOMENTO),
        _c("registrado_em", "Registrado em", "datahora"),
        _c("combustivel", "Combustível", mapa=_COMBUSTIVEL),
        _c("requisicoes", "Requisições", "inteiro"),
        _c("autorizadas", "Autorizadas", "inteiro"),
        _c("negadas", "Negadas", "inteiro"),
        _c("canceladas", "Canceladas", "inteiro"),
        _c("concluidas", "Concluídas", "inteiro"),
        _c("horas_uso", "Horas de uso", "decimal"),
        _c("espera_n", "Espera: quantidade", "inteiro"),
        _c("espera_media_s", "Espera: média (hh:mm:ss)", "duracao"),
        _c("espera_mediana_s", "Espera: mediana (hh:mm:ss)", "duracao"),
        _c("espera_max_s", "Espera: máxima (hh:mm:ss)", "duracao"),
        _c("uso_n", "Uso: quantidade", "inteiro"),
        _c("uso_media_s", "Uso: média (hh:mm:ss)", "duracao"),
        _c("uso_mediana_s", "Uso: mediana (hh:mm:ss)", "duracao"),
        _c("uso_max_s", "Uso: máxima (hh:mm:ss)", "duracao"),
        _c("saida_dif_n", "Saída x prevista: quantidade", "inteiro"),
        _c("saida_dif_media_s", "Saída x prevista: média (hh:mm:ss)", "duracao"),
        _c("saida_dif_mediana_s", "Saída x prevista: mediana (hh:mm:ss)", "duracao"),
        _c("saida_dif_min_s", "Saída x prevista: mínima (hh:mm:ss)", "duracao"),
        _c("saida_dif_max_s", "Saída x prevista: máxima (hh:mm:ss)", "duracao"),
    )
}
COLUNAS_NOMES = frozenset({"solicitante", "autorizado_por", "saida_por", "chegada_por"})
COLUNAS_RESUMO = (
    "requisicoes",
    "autorizadas",
    "negadas",
    "canceladas",
    "concluidas",
    "horas_uso",
)
COLUNAS_TEMPOS = (
    "espera_n",
    "espera_media_s",
    "espera_mediana_s",
    "espera_max_s",
    "uso_n",
    "uso_media_s",
    "uso_mediana_s",
    "uso_max_s",
    "saida_dif_n",
    "saida_dif_media_s",
    "saida_dif_mediana_s",
    "saida_dif_min_s",
    "saida_dif_max_s",
)
_ROTULO_GRUPO = {
    "dia": ("Dia", "dia"),
    "semana": ("Semana (início)", "dia"),
    "mes": ("Mês", "mes"),
    "unidade": ("Unidade", "texto"),
    "veiculo": ("Veículo", "texto"),
}


@dataclass(frozen=True)
class Conjunto:
    """Resultado de um relatório, ainda com os valores crus do banco."""

    tipo: str
    rotulo: str
    filtros: dict[str, Any]
    colunas: list[Coluna]
    linhas: list[dict[str, Any]]
    truncado: bool  # havia mais linhas que o limite

    @property
    def total(self) -> int:
        return len(self.linhas)


@dataclass(frozen=True)
class Arquivo:
    conteudo: bytes
    mimetype: str
    nome: str
    formato: str


def _bool(valor: Any, nome: str) -> bool:
    if isinstance(valor, bool):
        return valor
    if valor is None:
        return False
    texto = str(valor).strip().lower()
    if texto in {"", "false", "0", "nao", "não", "no"}:
        return False
    if texto in {"true", "1", "sim", "yes"}:
        return True
    raise RequisicaoInvalida(f"O parâmetro '{nome}' deve ser verdadeiro ou falso.")


def _enum(args: Mapping[str, Any], nome: str, validos: tuple[str, ...], padrao: str | None) -> Any:
    valor = _texto(args, nome)
    if valor is None:
        return padrao
    if valor not in validos:
        raise RequisicaoInvalida(f"O parâmetro '{nome}' deve ser: {', '.join(validos)}.")
    return valor


def _janela(args: Mapping[str, Any], hoje: date) -> tuple[date, date]:
    """Datas AAAA-MM-DD, fim inclusivo. Padrão: últimos 30 dias (hoje incluído)."""
    inicio, fim = _data(args, "inicio"), _data(args, "fim")
    if fim is None:
        fim = hoje
    if inicio is None:
        inicio = fim - timedelta(days=DIAS_PADRAO - 1)
    if fim < inicio:
        raise RequisicaoInvalida("A data final não pode ser anterior à data inicial.")
    if (fim - inicio).days + 1 > DIAS_MAXIMOS:
        raise RequisicaoInvalida(f"O período máximo do relatório é de {DIAS_MAXIMOS} dias.")
    return inicio, fim


def _agora_utc() -> datetime:
    return datetime.now(UTC)


class ServicoRelatorios:
    def __init__(
        self,
        repo: Repositorio,
        config: Config,
        *,
        auth_admin: AuthAdmin | None = None,
        enviador: EnviadorEmail | None = None,
        executor: Executor | None = None,
        relogio: Callable[[], datetime] = _agora_utc,
    ) -> None:
        self._repo = repo
        self._cfg = config
        self._auth = auth_admin
        self._enviador = enviador
        self._executor = executor
        self._relogio = relogio

    # -- permissões e catálogo --------------------------------------------------------------
    def _config_recursos(self) -> Configuracoes:
        return self._repo.ler_configuracoes()

    @staticmethod
    def _staff(perfil: Perfil) -> bool:
        return perfil.role in STAFF

    def _visivel(self, rel: Relatorio, perfil: Perfil, cfg: Configuracoes) -> bool:
        return perfil.role in rel.papeis and (
            rel.recurso is None or bool(getattr(cfg, rel.recurso))
        )

    def _exigir(self, tipo: str, perfil: Perfil) -> Relatorio:
        """404 tipo desconhecido; 403 sem permissão; recurso desligado => RecursoDesligado."""
        rel = RELATORIOS.get(tipo)
        if rel is None:
            raise NaoEncontrado("Relatório não encontrado.")
        if perfil.role not in rel.papeis:
            raise SemPermissao(MSG_SEM_PERMISSAO)
        if rel.recurso and not getattr(self._config_recursos(), rel.recurso):
            raise RecursoDesligado("O recurso necessário para este relatório está desligado.")
        return rel

    def _filtros_do_perfil(self, rel: Relatorio, perfil: Perfil) -> tuple[str, ...]:
        """Solicitante nunca recebe (nem pode ligar) os flags de dado pessoal."""
        if self._staff(perfil):
            return rel.filtros
        return tuple(f for f in rel.filtros if not f.startswith("incluir_"))

    def canais(self, cfg: Configuracoes | None = None) -> dict[str, bool]:
        cfg = cfg or self._config_recursos()
        return {
            "download": True,
            "email": bool(cfg.relatorio_email and self._cfg.email_enabled),
            "whatsapp": bool(cfg.relatorio_whatsapp),
        }

    def _def_filtro(
        self, nome: str, tipo_rel: str, hoje: date, veiculos: list[dict[str, str]]
    ) -> dict[str, Any]:
        def op(pares: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
            return [{"valor": v, "rotulo": r} for v, r in pares]

        def f(
            rotulo: str, tipo: str, opcoes: Any = None, padrao: Any = None, obrig: bool = False
        ) -> dict[str, Any]:
            return {
                "nome": nome,
                "rotulo": rotulo,
                "tipo": tipo,
                "opcoes": opcoes,
                "obrigatorio": obrig,
                "padrao": padrao,
            }

        if nome == "inicio":
            ini = hoje - timedelta(days=DIAS_PADRAO - 1)
            return f("Data inicial", "data", padrao=ini.isoformat())
        if nome == "fim":
            return f("Data final", "data", padrao=hoje.isoformat())
        if nome == "campo_data":
            return f("Data considerada", "enum", op(CAMPOS_DATA), "criacao")
        if nome == "agrupar":
            if tipo_rel == "resumo":
                return f("Agrupar por", "enum", op(AGRUPAR_RESUMO), "dia")
            return f("Agrupar por", "enum", op(AGRUPAR_TEMPOS), "unidade")
        if nome == "acao":
            return f("Ação", "enum", op(ACOES_AUDITORIA))
        if nome == "unidade":
            return f("Unidade", "enum", [{"valor": u, "rotulo": u} for u in UNIDADES_ORDEM])
        if nome == "veiculo":
            return f("Veículo", "enum", veiculos)
        if nome == "incluir_nomes":
            return f("Incluir nomes de pessoas", "bool", padrao=False)
        return f("Incluir trajeto", "bool", padrao=False)  # incluir_trajeto

    def _veiculos_opcoes(self) -> list[dict[str, str]]:
        return [
            {
                "valor": str(v["id"]),
                "rotulo": f"{v['placa']} - {v['descricao']}" if v.get("placa") else v["descricao"],
            }
            for v in self._repo.listar_veiculos_admin(ativo=None)
        ]

    def catalogo(self, perfil: Perfil) -> dict[str, Any]:
        cfg = self._config_recursos()
        hoje = self._hoje()
        veiculos = self._veiculos_opcoes()
        visiveis = [r for r in RELATORIOS.values() if self._visivel(r, perfil, cfg)]
        itens = [
            {
                "tipo": r.tipo,
                "rotulo": r.rotulo,
                "descricao": r.descricao,
                "filtros": [
                    self._def_filtro(n, r.tipo, hoje, veiculos)
                    for n in self._filtros_do_perfil(r, perfil)
                ],
            }
            for r in visiveis
        ]
        tipos = {r.tipo for r in visiveis}
        paineis: dict[str, Any] = {}
        for painel, (tipo, params) in EXPORTACAO_PAINEIS.items():
            if painel == "opcionais" and "opcionais_km" not in tipos:
                tipo = "opcionais_avarias"  # só Checklist ligado: exporta as avarias
            if tipo in tipos:
                paineis[painel] = {"tipo": tipo, "parametros": dict(params)}
        return {
            "relatorios": itens,
            "canais": self.canais(cfg),
            "exportacao_paineis": paineis,
            "limite_linhas": self._cfg.relatorio_max_linhas,
            "fuso": FUSO_NOME,
        }

    # -- validação dos filtros ----------------------------------------------------------------
    def _hoje(self) -> date:
        return self._relogio().astimezone(FUSO).date()

    def _validar(self, rel: Relatorio, perfil: Perfil, args: Mapping[str, Any]) -> dict[str, Any]:
        """Filtros efetivos (valores já validados). Parâmetros desconhecidos são ignorados; o
        escopo do solicitante NÃO é um filtro: vem do JWT em `_consultar`."""
        nomes = self._filtros_do_perfil(rel, perfil)
        ef: dict[str, Any] = {}
        if "inicio" in nomes:
            inicio, fim = _janela(args, self._hoje())
            ef["inicio"], ef["fim"] = inicio.isoformat(), fim.isoformat()
        if "campo_data" in nomes:
            ef["campo_data"] = _enum(
                args, "campo_data", tuple(v for v, _ in CAMPOS_DATA), "criacao"
            )
        if "agrupar" in nomes:
            validos = AGRUPAR_RESUMO if rel.tipo == "resumo" else AGRUPAR_TEMPOS
            ef["agrupar"] = _enum(args, "agrupar", tuple(v for v, _ in validos), validos[0][0])
        if "acao" in nomes:
            ef["acao"] = _enum(args, "acao", tuple(v for v, _ in ACOES_AUDITORIA), None)
        if "unidade" in nomes:
            ef["unidade"] = _enum(args, "unidade", UNIDADES_ORDEM, None)
        if "veiculo" in nomes:
            v = _texto(args, "veiculo")
            ef["veiculo"] = validar_uuid(v, "veiculo") if v else None
        for flag in ("incluir_nomes", "incluir_trajeto"):
            if flag in nomes:
                ef[flag] = _bool(args.get(flag), flag)
        return ef

    # -- consulta -----------------------------------------------------------------------------
    def _consultar(
        self, rel: Relatorio, perfil: Perfil, ef: dict[str, Any], limite: int | None = None
    ) -> Conjunto:
        r = self._repo
        # `limite`: arquivo/e-mail usam o teto do relatório; a prévia pede só PREVIA_MAX.
        limite = limite or self._cfg.relatorio_max_linhas
        pedir = limite + 1  # uma a mais só para saber se estourou
        # Escopo: o solicitante só vê as próprias requisições. SEMPRE o id do JWT.
        usuario = None if self._staff(perfil) else perfil.id
        janela: dict[str, Any] = {}
        if "inicio" in ef:
            janela = {
                "inicio": _meia_noite_local(date.fromisoformat(ef["inicio"])),
                "fim": _meia_noite_local(date.fromisoformat(ef["fim"]) + timedelta(days=1)),
            }
        ent = {"unidade": ef.get("unidade"), "veiculo": ef.get("veiculo")}
        nomes = bool(ef.get("incluir_nomes"))
        tipo = rel.tipo
        if tipo == "movimentacao":
            bruto = r.rel_movimentacao(
                **janela,
                **ent,
                campo_data=ef["campo_data"],
                usuario=usuario,
                incluir_nomes=nomes,
                incluir_trajeto=bool(ef.get("incluir_trajeto")),
                limite=pedir,
            )
        elif tipo == "resumo":
            bruto = r.rel_resumo(**janela, **ent, agrupar=ef["agrupar"], usuario=usuario)
        elif tipo == "tempos":
            bruto = r.rel_tempos(**janela, **ent, agrupar=ef["agrupar"], usuario=usuario)
        elif tipo == "pendencias":
            bruto = r.rel_pendencias(**ent, usuario=usuario, incluir_nomes=nomes, limite=pedir)
        elif tipo == "auditoria":
            bruto = r.rel_auditoria(**janela, acao=ef.get("acao"), limite=pedir)
        elif tipo == "opcionais_km":
            bruto = r.rel_opcionais_km(**janela, **ent, usuario=usuario, limite=pedir)
        else:
            bruto = r.rel_opcionais_avarias(**janela, **ent, usuario=usuario, limite=pedir)
        if bruto.get("habilitado") is False:  # recurso desligado entre a checagem e a consulta
            raise RecursoDesligado("O recurso necessário para este relatório está desligado.")

        linhas = list(bruto.get("linhas") or [])
        truncado = bool(bruto.get("truncado")) or len(linhas) > limite
        linhas = linhas[:limite]
        return Conjunto(tipo, rel.rotulo, ef, self._colunas(rel, ef, bruto), linhas, truncado)

    @staticmethod
    def _colunas(rel: Relatorio, ef: dict[str, Any], bruto: dict[str, Any]) -> list[Coluna]:
        tipo = rel.tipo
        if tipo in {"resumo", "tempos"}:
            rotulo, kind = _ROTULO_GRUPO[ef["agrupar"]]
            grupo = Coluna("rotulo", rotulo, kind)
            fixas = COLUNAS_RESUMO if tipo == "resumo" else COLUNAS_TEMPOS
            return [grupo, *(COLUNAS[k] for k in fixas)]
        chaves = [str(k) for k in bruto.get("colunas") or []]
        proibidas: set[str] = set()
        if not ef.get("incluir_nomes"):
            proibidas |= COLUNAS_NOMES
        if not ef.get("incluir_trajeto"):
            proibidas.add("trajeto")
        return [COLUNAS[k] for k in chaves if k in COLUNAS and k not in proibidas]

    # -- prévia, arquivo -----------------------------------------------------------------------
    def previa(self, perfil: Perfil, tipo: str, args: Mapping[str, Any]) -> dict[str, Any]:
        """Prévia de até PREVIA_MAX linhas (o banco recebe PREVIA_MAX + 1). `total_linhas` é o
        número de linhas devolvidas e `truncado` = há mais linhas que a prévia. Se o arquivo
        passaria do limite de linhas, quem avisa é o 422 do arquivo/e-mail: saber isso exigiria
        trazer até 5001 linhas em toda prévia (caro), então não há campo para isso."""
        rel = self._exigir(tipo, perfil)
        ef = self._validar(rel, perfil, args)
        c = self._consultar(rel, perfil, ef, limite=PREVIA_MAX)
        if ef.get("incluir_nomes") or ef.get("incluir_trajeto") or rel.tipo == "auditoria":
            # Prévia com dado pessoal (ou trilha de auditoria) também deixa rastro. Falha fechada.
            self._repo.registrar_auditoria_relatorio(
                acao="relatorio_gerado",
                registro_id=None,
                ator_id=perfil.id,
                detalhes={
                    "tipo": rel.tipo,
                    "filtros": ef,
                    "via": "previa",
                    "total_linhas": c.total,
                },
            )
        return {
            "tipo": rel.tipo,
            "rotulo": rel.rotulo,
            "filtros": ef,
            "colunas": [{"chave": col.chave, "rotulo": col.rotulo} for col in c.colunas],
            "linhas": formatar_linhas(c.colunas, c.linhas),
            "total_linhas": c.total,
            "truncado": c.truncado,
        }

    def _gerar(self, c: Conjunto, formato: str) -> Arquivo:
        if c.truncado:
            raise RelatorioGrande(
                f"O relatório tem mais de {self._cfg.relatorio_max_linhas} linhas. "
                "Estreite o filtro (período, unidade ou veículo) e tente novamente."
            )
        if formato == "csv":
            dados = gerar_csv(c.colunas, c.linhas)
        else:
            dados = gerar_xlsx(c.colunas, c.linhas, c.rotulo)
        # Nome sem dado pessoal: só tipo e data de geração.
        nome = f"relatorio-{c.tipo}-{data_arquivo(self._relogio()):%Y%m%d}.{formato}"
        return Arquivo(dados, MIMETYPES[formato], nome, formato)

    @staticmethod
    def _formato(valor: Any) -> str:
        formato = str(valor).strip().lower() if valor is not None else "csv"
        if formato not in FORMATOS:
            raise RequisicaoInvalida("O parâmetro 'formato' deve ser csv ou xlsx.")
        return formato

    def arquivo(
        self, perfil: Perfil, tipo: str, args: Mapping[str, Any], *, formato: Any, via: Any
    ) -> Arquivo:
        rel = self._exigir(tipo, perfil)
        formato = self._formato(formato)
        via = str(via).strip().lower() if via else "download"
        if via not in VIAS:
            raise RequisicaoInvalida("O parâmetro 'via' deve ser download ou compartilhar.")
        if via == "compartilhar" and not self.canais()["whatsapp"]:
            raise RecursoDesligado("O compartilhamento por WhatsApp está desligado.")
        ef = self._validar(rel, perfil, args)
        c = self._consultar(rel, perfil, ef)
        arq = self._gerar(c, formato)
        # Falha fechada: sem trilha de auditoria, o arquivo não sai.
        self._repo.registrar_auditoria_relatorio(
            acao="relatorio_gerado",
            registro_id=None,
            ator_id=perfil.id,
            detalhes={
                "tipo": rel.tipo,
                "filtros": ef,
                "formato": formato,
                "via": via,
                "total_linhas": c.total,
            },
        )
        return arq

    # -- e-mail ---------------------------------------------------------------------------------
    def _exigir_email(self) -> None:
        if not self.canais()["email"]:
            raise RecursoDesligado("O envio de relatórios por e-mail está desligado.")

    def destinatarios(self, perfil: Perfil) -> dict[str, Any]:
        """Staff envia só a staff (autorizadores e administradores ativos, ele incluído);
        solicitante só a si mesmo. `truncado`: havia mais que MAX_DESTINATARIOS."""
        self._exigir_email()
        if not self._staff(perfil):
            return {"itens": [{"id": perfil.id, "nome": perfil.nome}], "truncado": False}
        todos: list[dict[str, Any]] = []
        for role in ("autorizador", "admin"):
            todos += self._repo.listar_perfis(
                role=role, ativo=True, limite=MAX_DESTINATARIOS + 1, offset=0
            )
        todos.sort(key=lambda p: (str(p["nome"]).lower(), str(p["id"])))
        return {
            "itens": [{"id": p["id"], "nome": p["nome"]} for p in todos[:MAX_DESTINATARIOS]],
            "truncado": len(todos) > MAX_DESTINATARIOS,
        }

    @staticmethod
    def _corpo_envio(corpo: Any) -> tuple[str, dict[str, Any], str]:
        if not isinstance(corpo, dict):
            raise RequisicaoInvalida("Envie um objeto JSON.")
        extras = sorted(set(corpo) - {"formato", "filtros", "destinatario_id"})
        if extras:
            raise RequisicaoInvalida("Campos não permitidos: " + ", ".join(extras) + ".")
        filtros = corpo.get("filtros") or {}
        if not isinstance(filtros, dict):
            raise RequisicaoInvalida("O campo 'filtros' deve ser um objeto.")
        if any(not isinstance(v, str | bool | type(None)) for v in filtros.values()):
            raise RequisicaoInvalida("Os valores de 'filtros' devem ser texto ou verdadeiro/falso.")
        return (
            ServicoRelatorios._formato(corpo.get("formato")),
            filtros,
            validar_uuid(corpo.get("destinatario_id"), "destinatario_id"),
        )

    def _email_do_destinatario(self, perfil: Perfil, destinatario_id: str) -> str:
        if not self._staff(perfil) and destinatario_id != perfil.id:
            raise SemPermissao("Você só pode enviar relatórios para você mesmo.")
        alvo = self._repo.obter_perfil(destinatario_id)
        if alvo is None or not alvo.ativo:
            raise DestinatarioInvalido("O destinatário não existe ou está inativo.")
        if alvo.role not in STAFF and alvo.id != perfil.id:
            # O relatório de staff tem escopo global: nunca vai a um solicitante alheio.
            raise ErroApi(
                "Relatórios de autorizadores e administradores só podem ser enviados a "
                "autorizadores ou administradores.",
                "destinatario_sem_permissao_pii",
                422,
            )
        if self._auth is None:
            raise ErroInterno("Serviço de autenticação indisponível. Tente novamente.")
        conta = self._auth.obter_conta(destinatario_id)
        if conta is None or not conta.email:
            raise DestinatarioInvalido("O destinatário não possui e-mail cadastrado.")
        return conta.email

    def enviar_email(self, perfil: Perfil, tipo: str, corpo: Any) -> dict[str, Any]:
        rel = self._exigir(tipo, perfil)
        self._exigir_email()
        formato, filtros, destinatario_id = self._corpo_envio(corpo)
        email = self._email_do_destinatario(perfil, destinatario_id)
        ef = self._validar(rel, perfil, filtros)
        c = self._consultar(rel, perfil, ef)
        arq = self._gerar(c, formato)  # 422 se passar do limite, antes de gastar cota

        envio_id = self._repo.reservar_envio_email(
            solicitante=perfil.id,
            destinatario=destinatario_id,
            tipo=rel.tipo,
            formato=formato,
            filtros=ef,
            max_por_hora=self._cfg.email_max_per_hour,
            max_por_usuario_hora=self._cfg.email_max_per_user_hour,
        )
        try:
            self._repo.registrar_auditoria_relatorio(
                acao="relatorio_enviado",
                registro_id=envio_id,
                ator_id=perfil.id,
                detalhes={
                    "tipo": rel.tipo,
                    "filtros": ef,
                    "formato": formato,
                    "destinatario_id": destinatario_id,
                    "total_linhas": c.total,
                },
            )
        except ErroApi:
            self._marcar_falha(envio_id, "auditoria_falhou")
            raise
        try:
            (self._executor or EXECUTOR_EMAIL).submit(
                self._enviar_em_segundo_plano, envio_id, email, rel.rotulo, arq
            )
        except RuntimeError:  # executor encerrado
            self._marcar_falha(envio_id, "fila_indisponivel")
            raise ErroInterno("O envio não pôde ser agendado. Tente novamente.") from None
        return {"id": envio_id, "status": "pendente"}

    def _marcar_falha(self, envio_id: str, codigo: str) -> None:
        try:
            self._repo.marcar_envio_email(envio_id, "falhou", codigo)
        except Exception as e:  # nunca esconde o erro original; só o tipo vai ao log
            log.error("envio_marcar_falhou tipo=%s", type(e).__name__)
        if codigo not in CODIGOS_SEM_ALERTA:  # problema do endereço, não do sistema
            registrar_falha_sistema(self._repo, codigo)  # avisa os admins; nunca propaga

    def _enviar_em_segundo_plano(
        self, envio_id: str, destino: str, rotulo: str, arq: Arquivo
    ) -> None:
        """Roda numa thread: sem contexto Flask. Nunca levanta; o resultado vai ao banco."""
        try:
            self._repo.marcar_envio_email(envio_id, "enviando")
            enviador = self._enviador or EnviadorSmtp(ConfigSmtp.de(self._cfg))
            enviador.enviar(
                destino=destino,
                assunto=f"Relatório: {rotulo}",
                corpo=(
                    f'Segue o relatório "{rotulo}" gerado em '
                    f"{self._relogio().astimezone(FUSO):%d/%m/%Y}.\n\n"
                    "Esta é uma mensagem automática do sistema de controle de veículos."
                ),
                anexo_nome=arq.nome,
                anexo=arq.conteudo,
                mimetype=arq.mimetype.split(";")[0],
            )
        except FalhaEmail as e:
            self._marcar_falha(envio_id, e.codigo)
            return
        except Exception as e:
            log.error("envio_inesperado tipo=%s", type(e).__name__)
            self._marcar_falha(envio_id, "desconhecido")
            return
        try:
            self._repo.marcar_envio_email(envio_id, "enviado")
        except Exception as e:
            log.error("envio_marcar_enviado_falhou tipo=%s", type(e).__name__)

    def consultar_envio(self, perfil: Perfil, envio_id: str) -> dict[str, Any]:
        """Só quem solicitou o envio vê o status; para os demais, 404 (anti-IDOR)."""
        try:
            id_ok = validar_uuid(envio_id, "id")
        except RequisicaoInvalida:
            raise NaoEncontrado("Envio não encontrado.") from None
        envio = self._repo.obter_envio_email(id_ok)
        if envio is None or str(envio["solicitado_por"]) != perfil.id:
            raise NaoEncontrado("Envio não encontrado.")
        codigo = envio.get("erro_codigo")
        return {
            "id": str(envio["id"]),
            "status": envio["status"],
            "erro_codigo": codigo,
            "mensagem": mensagem_do_codigo(codigo),
        }
