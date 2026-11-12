"""Relatórios: catálogo por perfil, validação dos filtros, consulta, exportação e envio por e-mail.

O banco só LÊ (RPCs `rel_*`); aqui ficam a permissão por role, o escopo do solicitante (sempre o
id do JWT, nunca um parâmetro do cliente), os flags de dado pessoal, a validação de tudo o que
vem da query/corpo, o limite de linhas e a auditoria. As rotas só traduzem HTTP.
"""

from __future__ import annotations

import logging
import re
import threading
import time
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
    TransicaoInvalida,
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

REAPER_INTERVALO_S = 300  # no máximo 1 chamada do reaper a cada 5 min por processo
REAPER_MINUTOS = 15  # envio pendente/enviando parado além disso vira falhou/timeout


class _Reaper:
    """Chama `reaper_envios_email` de forma barata e segura: com limite de frequência por
    processo, exceções engolidas e só o tipo do erro no log (sem PII)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ultimo: float | None = None

    def reiniciar(self) -> None:
        with self._lock:
            self._ultimo = None

    def talvez_rodar(self, repo: Repositorio) -> None:
        agora = time.monotonic()
        with self._lock:
            if self._ultimo is not None and agora - self._ultimo < REAPER_INTERVALO_S:
                return
            self._ultimo = agora  # marca antes: um banco instável não é martelado a cada request
        try:
            n = repo.reaper_envios_email(REAPER_MINUTOS)
            if n:
                log.info("reaper_envios_email movidos=%d", n)
        except Exception as e:  # noqa: BLE001 - best-effort: nunca derruba a requisição
            log.warning("reaper_envios_email_falhou tipo=%s", type(e).__name__)


REAPER = _Reaper()

PREVIA_MAX = 50
SEM_RESUMO = frozenset({"auditoria"})  # eventos com responsáveis nominais: nunca vão à IA
FORMATOS = ("csv", "xlsx")
VIAS = ("download", "compartilhar")
MIMETYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
MAX_DESTINATARIOS = 500
MSG_SEM_PERMISSAO = "Você não tem permissão para este relatório."
MSG_DESTINO_SEM_PAPEL = (
    "Este relatório só pode ser enviado a destinatários com permissão para consultá-lo."
)
MSG_DESTINO_EXTERNO = (
    "Relatórios com nomes, trajetos, observações ou trilha de auditoria só podem ser enviados "
    "por e-mail a endereços de domínio institucional. Baixe o arquivo pela tela ou desmarque "
    "essas opções."
)

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
                "incluir_observacao",
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
            ("inicio", "fim", "unidade", "veiculo", "lancado_por_vigilante", "incluir_nomes"),
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
_SIM_NAO = {"True": "Sim", "False": "Não"}
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
        _c("km_lancado_por_vigilante", "Km lançado por vigilante", mapa=_SIM_NAO),
        _c("lancado_por", "Lançado por (vigilante)"),
        _c("solicitante", "Solicitante"),
        _c("autorizado_por", "Autorizado por"),
        _c("saida_por", "Saída registrada por"),
        _c("chegada_por", "Chegada registrada por"),
        _c("trajeto", "Trajeto"),
        _c("observacao", "Observação"),
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
COLUNAS_NOMES = frozenset(
    {"solicitante", "autorizado_por", "saida_por", "chegada_por", "lancado_por"}
)
FLAGS_BOOL = ("incluir_nomes", "incluir_trajeto", "incluir_observacao", "lancado_por_vigilante")


@dataclass(frozen=True)
class ColunaSel:
    """Coluna que o cliente pode escolher. `exige_flag`: a opção `incluir_*` que precisa estar
    ligada; `recurso`: o recurso opcional que precisa estar habilitado."""

    chave: str
    exige_flag: str | None = None
    recurso: str | None = None


def _sel(*itens: str | tuple[str, str | None, str | None]) -> tuple[ColunaSel, ...]:
    return tuple(ColunaSel(i) if isinstance(i, str) else ColunaSel(*i) for i in itens)


# Lista branca da seleção (ordem = ordem padrão do catálogo). Só estes três relatórios aceitam
# `colunas`; resumo, tempos, auditoria e avarias mantêm colunas fixas.
COLUNAS_SELECIONAVEIS: dict[str, tuple[ColunaSel, ...]] = {
    "movimentacao": _sel(
        "codigo",
        "criada_em",
        "unidade",
        "veiculo",
        "status",
        "hora_saida_prevista",
        "autorizada_em",
        "saida_real_em",
        "chegada_real_em",
        "espera_autorizacao_s",
        "duracao_uso_s",
        ("km_saida", None, "quilometragem"),
        ("km_chegada", None, "quilometragem"),
        ("solicitante", "incluir_nomes", None),
        ("autorizado_por", "incluir_nomes", None),
        ("saida_por", "incluir_nomes", None),
        ("chegada_por", "incluir_nomes", None),
        ("trajeto", "incluir_trajeto", None),
        ("observacao", "incluir_observacao", None),
    ),
    "pendencias": _sel(
        "codigo",
        "status",
        "unidade",
        "veiculo",
        "hora_saida_prevista",
        "desde",
        "decorrido_s",
        ("solicitante", "incluir_nomes", None),
    ),
    "opcionais_km": _sel(
        "codigo",
        "unidade",
        "veiculo",
        "saida_real_em",
        "chegada_real_em",
        "km_saida",
        "km_chegada",
        "km_rodado",
        "km_lancado_por_vigilante",
        ("lancado_por", "incluir_nomes", None),
    ),
}
MSG_COLUNA_INVALIDA = "Coluna não disponível neste relatório."
MAX_TEXTO_COLUNAS = 1000


def _lista_colunas(args: Mapping[str, Any]) -> list[str] | None:
    """Lê `colunas` (CSV, parâmetro repetido ou lista JSON), preservando a ordem. Ausente ou
    vazio = None (usa o padrão). Item vazio ou repetido => 400."""
    getlist = getattr(args, "getlist", None)
    if getlist:
        brutos: list[Any] = list(getlist("colunas"))
    else:
        valor = args.get("colunas")
        brutos = list(valor) if isinstance(valor, list) else ([] if valor is None else [valor])
    itens: list[str] = []
    for b in brutos:
        if not isinstance(b, str) or len(b) > MAX_TEXTO_COLUNAS:
            raise RequisicaoInvalida("O parâmetro 'colunas' deve ser uma lista de nomes.")
        itens += [p.strip() for p in b.split(",")]
    if not any(itens):
        return None
    if not all(itens):
        raise RequisicaoInvalida("O parâmetro 'colunas' tem item vazio.")
    if len(set(itens)) != len(itens):
        raise RequisicaoInvalida("O parâmetro 'colunas' tem coluna repetida.")
    return itens


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


def _lancamento_da_linha(
    origem: dict[str, dict[str, Any]], linha: dict[str, Any]
) -> dict[str, Any] | None:
    """Associa a linha do RPC (que só traz `left(id, 8)`) ao lançamento de vigilante.

    O prefixo sozinho pode colidir; por isso também se exige o mesmo veículo e o mesmo instante
    de saída. Sem correspondência única e inequívoca, a linha NÃO é atribuída ao vigilante.
    """
    codigo = str(linha.get("codigo"))
    saida = _instante_relatorio(linha.get("saida_real_em"))
    achados = [
        info
        for rid, info in origem.items()
        if rid.startswith(codigo)
        and str(info.get("veiculo_id")) == str(linha.get("veiculo_id"))
        and saida is not None
        and _instante_relatorio(info.get("saida_real_em")) == saida
    ]
    return achados[0] if len(achados) == 1 else None


def _instante_relatorio(valor: Any) -> datetime | None:
    if not isinstance(valor, str):
        return None
    try:
        dt = datetime.fromisoformat(valor)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


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


def dominios_institucionais(config: object) -> frozenset[str]:
    """Domínios de e-mail que podem receber relatório com dado pessoal (falha fechada).

    Lê só `config.email_dominios_relatorio` (validado em `Config.validar_producao`). Sem o
    campo ou vazio: conjunto vazio, ou seja, nenhum destinatário é considerado institucional.
    """
    itens = getattr(config, "email_dominios_relatorio", None) or ()
    return frozenset(d.strip().lstrip("@").lower() for d in itens if str(d).strip())


# Endereço estrito (ASCII, sem espaços/aspas/comentários): parte local conservadora e domínio
# com ao menos um ponto. Qualquer coisa fora disso é tratada como externa (falha fechada).
RE_EMAIL = re.compile(r"^[a-z0-9._%+\-]{1,64}@([a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _dominio_do_email(email: str) -> str:
    """Domínio do e-mail, ou '' se o endereço não for estritamente válido."""
    texto = email.strip().lower()
    return texto.rpartition("@")[2] if RE_EMAIL.fullmatch(texto) else ""


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
        if nome == "lancado_por_vigilante":
            return f("Somente km lançado por vigilante", "bool", padrao=False)
        if nome == "incluir_nomes":
            return f("Incluir nomes de pessoas", "bool", padrao=False)
        if nome == "incluir_observacao":
            return f("Incluir observação (texto livre)", "bool", padrao=False)
        return f("Incluir trajeto", "bool", padrao=False)  # incluir_trajeto

    def _veiculos_opcoes(self) -> list[dict[str, str]]:
        return [
            {
                "valor": str(v["id"]),
                "rotulo": f"{v['placa']} - {v['descricao']}" if v.get("placa") else v["descricao"],
            }
            for v in self._repo.listar_veiculos_admin(ativo=None)
        ]

    def _colunas_disponiveis(
        self, rel: Relatorio, perfil: Perfil, cfg: Configuracoes
    ) -> list[dict[str, Any]]:
        """Colunas escolhíveis por este perfil (flags e recursos que ele não pode usar somem)."""
        flags = self._filtros_do_perfil(rel, perfil)
        return [
            {"chave": c.chave, "rotulo": COLUNAS[c.chave].rotulo, "exige_flag": c.exige_flag}
            for c in COLUNAS_SELECIONAVEIS.get(rel.tipo, ())
            if (c.exige_flag is None or c.exige_flag in flags)
            and (c.recurso is None or bool(getattr(cfg, c.recurso)))
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
                "colunas_disponiveis": self._colunas_disponiveis(r, perfil, cfg),
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
        for flag in FLAGS_BOOL:
            if flag in nomes:
                ef[flag] = _bool(args.get(flag), flag)
        if rel.tipo in COLUNAS_SELECIONAVEIS:
            selecao = self._validar_colunas(rel, args, ef)
            if selecao is not None:  # ausente: a chave nem existe (mantém o hash dos envios)
                ef["colunas"] = selecao
                if "observacao" not in selecao:
                    # Normaliza: sem a coluna escolhida, a observação não é buscada, não vai à
                    # auditoria e não exige destino institucional.
                    ef["incluir_observacao"] = False
        return ef

    def _validar_colunas(
        self, rel: Relatorio, args: Mapping[str, Any], ef: dict[str, Any]
    ) -> list[str] | None:
        """Lista branca por relatório; coluna que exige flag/recurso desligado => 400. O nome
        informado pelo cliente nunca é devolvido na mensagem."""
        pedidas = _lista_colunas(args)
        if pedidas is None:
            return None
        permitidas = {c.chave: c for c in COLUNAS_SELECIONAVEIS[rel.tipo]}
        cfg: Configuracoes | None = None
        for chave in pedidas:
            c = permitidas.get(chave)
            if c is None:
                raise RequisicaoInvalida(MSG_COLUNA_INVALIDA)
            if c.exige_flag and not ef.get(c.exige_flag):
                raise RequisicaoInvalida(
                    f"A coluna '{chave}' exige a opção '{c.exige_flag}' ligada."
                )
            if c.recurso:
                cfg = cfg or self._config_recursos()
                if not getattr(cfg, c.recurso):
                    raise RequisicaoInvalida(
                        f"A coluna '{chave}' exige o recurso '{c.recurso}' habilitado."
                    )
        return pedidas

    # -- consulta -----------------------------------------------------------------------------
    def _consultar(
        self,
        rel: Relatorio,
        perfil: Perfil,
        ef: dict[str, Any],
        limite: int | None = None,
        *,
        previa: bool = False,
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
            # A observação (texto livre, pode ter dado pessoal) nunca é buscada para a prévia.
            obs = bool(ef.get("incluir_observacao")) and not previa
            extra = {"incluir_observacao": True} if obs else {}
            bruto = r.rel_movimentacao(
                **janela,
                **ent,
                campo_data=ef["campo_data"],
                usuario=usuario,
                incluir_nomes=nomes,
                incluir_trajeto=bool(ef.get("incluir_trajeto")),
                limite=pedir,
                **extra,
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
            bruto = self._opcionais_km(janela, ent, usuario, ef, pedir)
        else:
            bruto = r.rel_opcionais_avarias(**janela, **ent, usuario=usuario, limite=pedir)
        if bruto.get("habilitado") is False:  # recurso desligado entre a checagem e a consulta
            raise RecursoDesligado("O recurso necessário para este relatório está desligado.")

        linhas = list(bruto.get("linhas") or [])
        truncado = bool(bruto.get("truncado")) or len(linhas) > limite
        linhas = linhas[:limite]
        return Conjunto(
            tipo, rel.rotulo, ef, self._colunas(rel, ef, bruto, previa), linhas, truncado
        )

    def _opcionais_km(
        self,
        janela: dict[str, Any],
        ent: dict[str, Any],
        usuario: str | None,
        ef: dict[str, Any],
        pedir: int,
    ) -> dict[str, Any]:
        """Km por viagem + origem do lançamento (vigilante ou equipe), derivada de quem registrou.

        Com o filtro `lancado_por_vigilante` o banco devolve até o teto do relatório e o corte é
        feito aqui, para a prévia não perder linhas que um corte anterior esconderia.
        """
        filtrar = bool(ef.get("lancado_por_vigilante"))
        nomes = bool(ef.get("incluir_nomes"))
        r = self._repo
        bruto = r.rel_opcionais_km(
            **janela,
            **ent,
            usuario=usuario,
            limite=self._cfg.relatorio_max_linhas + 1 if filtrar else pedir,
        )
        if bruto.get("habilitado") is False:
            return bruto
        origem = r.lancamentos_vigilante_km(**janela, **ent, usuario=usuario, incluir_nomes=nomes)
        linhas = []
        for linha in bruto.get("linhas") or []:
            info = _lancamento_da_linha(origem, linha)
            nova = {**linha, "km_lancado_por_vigilante": info is not None}
            if info is not None and nomes:
                nova["lancado_por"] = info.get("lancado_por")
            linhas.append(nova)
        resultado = {
            **bruto,
            "colunas": [
                *(bruto.get("colunas") or []),
                "km_lancado_por_vigilante",
                *(["lancado_por"] if nomes else []),
            ],
        }
        if filtrar:
            linhas = [x for x in linhas if x["km_lancado_por_vigilante"]]
            resultado["totais"] = {
                "viagens": len(linhas),
                "km_rodado": sum(int(x.get("km_rodado") or 0) for x in linhas),
            }
        return {**resultado, "linhas": linhas, "total_linhas": len(linhas)}

    @staticmethod
    def _colunas(
        rel: Relatorio, ef: dict[str, Any], bruto: dict[str, Any], previa: bool = False
    ) -> list[Coluna]:
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
        if previa or not ef.get("incluir_observacao"):
            proibidas.add("observacao")
        disponiveis = [k for k in chaves if k in COLUNAS and k not in proibidas]
        selecao: list[str] | None = ef.get("colunas")
        if selecao is None:
            return [COLUNAS[k] for k in disponiveis]
        escolhidas: list[Coluna] = []
        for k in selecao:  # a ordem pedida é preservada
            if k in disponiveis:
                escolhidas.append(COLUNAS[k])
            elif not (previa and k == "observacao"):
                raise RequisicaoInvalida(MSG_COLUNA_INVALIDA)
        if not escolhidas:
            raise RequisicaoInvalida("A observação não aparece na prévia; escolha outra coluna.")
        return escolhidas

    # -- prévia, arquivo -----------------------------------------------------------------------
    def previa(self, perfil: Perfil, tipo: str, args: Mapping[str, Any]) -> dict[str, Any]:
        """Prévia de até PREVIA_MAX linhas (o banco recebe PREVIA_MAX + 1). `total_linhas` é o
        número de linhas devolvidas e `truncado` = há mais linhas que a prévia. Se o arquivo
        passaria do limite de linhas, quem avisa é o 422 do arquivo/e-mail: saber isso exigiria
        trazer até 5001 linhas em toda prévia (caro), então não há campo para isso."""
        rel = self._exigir(tipo, perfil)
        ef = self._validar(rel, perfil, args)
        c = self._consultar(rel, perfil, ef, limite=PREVIA_MAX, previa=True)
        omitida = bool(ef.get("incluir_observacao")) and (
            "colunas" not in ef or "observacao" in ef["colunas"]
        )
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
            "colunas_omitidas": ["observacao"] if omitida else [],
        }

    def conjunto_para_resumo(
        self, perfil: Perfil, tipo: str, args: Mapping[str, Any]
    ) -> tuple[Conjunto, dict[str, Any]]:
        """Mesma permissão, filtros e escopo da prévia, para o resumo narrativo (IA).

        Nunca pede nomes nem trajeto ao banco (`incluir_*` forçados a falso): o resumo só usa
        contagens, status, unidades e durações. A trilha de auditoria não é resumida.
        """
        rel = self._exigir(tipo, perfil)
        if rel.tipo in SEM_RESUMO:
            raise RequisicaoInvalida("Este relatório não tem resumo automático.")
        # `colunas` e a observação não entram no resumo: ignorados (nem validados).
        sem_selecao = {k: v for k, v in args.items() if k not in {"colunas", "incluir_observacao"}}
        ef = self._validar(rel, perfil, sem_selecao)
        for flag in ("incluir_nomes", "incluir_trajeto", "incluir_observacao"):
            if flag in ef:
                ef[flag] = False
        return self._consultar(rel, perfil, ef), ef

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
        colunas = filtros.get("colunas")
        if (
            isinstance(colunas, list)
            and len(colunas) <= 64
            and all(isinstance(c, str) for c in colunas)
        ):
            filtros = {**filtros, "colunas": ",".join(colunas)}  # lista JSON: mesma validação
        if any(not isinstance(v, str | bool | type(None)) for v in filtros.values()):
            raise RequisicaoInvalida(
                "Os valores de 'filtros' devem ser texto ou verdadeiro/falso "
                "('colunas' também aceita lista de textos)."
            )
        return (
            ServicoRelatorios._formato(corpo.get("formato")),
            filtros,
            validar_uuid(corpo.get("destinatario_id"), "destinatario_id"),
        )

    def _email_do_destinatario(self, perfil: Perfil, destinatario_id: str, rel: Relatorio) -> str:
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
        if alvo.role not in rel.papeis:
            # Ex.: a trilha de auditoria é só de admin: nunca vai a um autorizador.
            raise ErroApi(MSG_DESTINO_SEM_PAPEL, "destinatario_sem_permissao", 422)
        if self._auth is None:
            raise ErroInterno("Serviço de autenticação indisponível. Tente novamente.")
        conta = self._auth.obter_conta(destinatario_id)
        if conta is None or not conta.email:
            raise DestinatarioInvalido("O destinatário não possui e-mail cadastrado.")
        return conta.email

    def _exigir_destino_institucional(self, rel: Relatorio, ef: dict[str, Any], email: str) -> None:
        """LGPD: nomes, trajetos e a trilha de auditoria (responsáveis nominais) só por e-mail a
        domínio institucional. Sem domínio configurado, nada disso sai por e-mail."""
        com_pii = bool(
            ef.get("incluir_nomes") or ef.get("incluir_trajeto") or ef.get("incluir_observacao")
        ) or (rel.tipo == "auditoria")
        if com_pii and _dominio_do_email(email) not in dominios_institucionais(self._cfg):
            raise ErroApi(MSG_DESTINO_EXTERNO, "destinatario_externo_pii", 422)

    def enviar_email(self, perfil: Perfil, tipo: str, corpo: Any) -> dict[str, Any]:
        rel = self._exigir(tipo, perfil)
        self._exigir_email()
        formato, filtros, destinatario_id = self._corpo_envio(corpo)
        email = self._email_do_destinatario(perfil, destinatario_id, rel)
        ef = self._validar(rel, perfil, filtros)
        self._exigir_destino_institucional(rel, ef, email)
        c = self._consultar(rel, perfil, ef)
        arq = self._gerar(c, formato)  # 422 se passar do limite, antes de gastar cota

        REAPER.talvez_rodar(self._repo)  # best-effort: libera envios presos antes de contar cota
        reserva = self._repo.reservar_envio_email(
            solicitante=perfil.id,
            destinatario=destinatario_id,
            tipo=rel.tipo,
            formato=formato,
            filtros=ef,
            max_por_hora=self._cfg.email_max_per_hour,
            max_por_usuario_hora=self._cfg.email_max_per_user_hour,
        )
        envio_id = str(reserva["id"])
        if reserva.get("duplicado") is True:
            # Duplo clique/retentativa: o envio idêntico já está em andamento. Não consome cota,
            # não audita nem envia de novo; devolve o mesmo envio (idempotente, sem erro).
            return {"id": envio_id, "status": "pendente"}
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
        except TransicaoInvalida:
            # AG001: o reaper já marcou falhou/timeout antes do fim do envio. Benigno.
            log.info("envio_marcar_enviado_apos_reaper")
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
