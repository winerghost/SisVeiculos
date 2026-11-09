"""Saudação sazonal: escolha estável para o usuário e curadoria das frases (admin).

Regras de negócio ficam aqui, nunca nas rotas. O texto é sempre montado no servidor; o nome vem
só do perfil (JWT -> `perfis`). O prompt de geração leva apenas a ocasião: nunca nome, unidade,
e-mail, placa ou trajeto.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.errors import (
    Conflito,
    ErroApi,
    NaoEncontrado,
    RequisicaoInvalida,
    TransicaoInvalida,
)
from app.ia.servico import ServicoIA
from app.modelos import Perfil
from app.saudacao.calendario import OCASIOES, Ocasiao, chave_validade, resolver
from app.saudacao.catalogo import CATALOGO
from app.saudacao.repositorio import RepositorioSaudacoes
from app.saudacao.validacao import (
    PLACEHOLDER,
    motivo_invalido,
    normalizar,
    sem_placeholder,
    tem_placeholder,
)
from app.services.admin_usuarios import exigir_admin
from app.services.validacao import validar_uuid

log = logging.getLogger(__name__)

STATUS = ("pendente", "aprovada", "rejeitada")
FUNCAO_IA = "saudacao"
QTD_MIN, QTD_MAX, QTD_PADRAO = 1, 10, 5
MAX_NOME = 30

MSG_IA_INDISPONIVEL = (
    "A geração por IA não está disponível: ligue a chave geral e a saudação em "
    "Configurações e confira o servidor."
)
MSG_IA_FALHOU = (
    "Não foi possível gerar frases agora (provedor indisponível ou cota atingida). "
    "Tente novamente mais tarde ou cadastre as frases manualmente."
)
MSG_MANUAL_PENDENTE = (
    "A frase foi salva, mas não foi possível aprová-la agora. Ela está pendente: aprove-a na lista."
)
MSG_EXCLUSAO = "Só frases rejeitadas podem ser excluídas. Rejeite a frase antes de excluí-la."
MSG_SEM_REPOSITORIO = "Cadastro de saudações indisponível."

DESCRICOES: dict[str, str] = {
    "madrugada": "a madrugada (de 0h às 5h)",
    "manha": "a manhã",
    "tarde": "a tarde",
    "noite": "a noite",
    "segunda": "a segunda-feira, início da semana",
    "sexta": "a sexta-feira, fim da semana",
    "fim_de_semana": "o fim de semana",
    "ano_novo": "o Ano Novo (1º de janeiro)",
    "vespera_ano_novo": "a véspera de Ano Novo (31 de dezembro)",
    "carnaval": "o Carnaval",
    "sexta_santa": "a Sexta-feira Santa (feriado)",
    "pascoa": "o domingo de Páscoa",
    "tiradentes": "o feriado de Tiradentes (21 de abril)",
    "trabalho": "o Dia do Trabalho (1º de maio)",
    "corpus_christi": "o feriado de Corpus Christi",
    "independencia": "o Dia da Independência (7 de setembro)",
    "padroeira": "o feriado de Nossa Senhora Aparecida (12 de outubro)",
    "finados": "o Dia de Finados (2 de novembro)",
    "republica": "a Proclamação da República (15 de novembro)",
    "consciencia_negra": "o Dia da Consciência Negra (20 de novembro)",
    "dia_servidor": "o Dia do Servidor Público (28 de outubro)",
    "vespera_natal": "a véspera de Natal (24 de dezembro)",
    "natal": "o Natal (25 de dezembro)",
}

PROMPT_SISTEMA = (
    "Você escreve saudações curtas para a tela inicial de um sistema interno de um órgão público "
    "brasileiro. Tom leve, institucional e respeitoso; neutro em religião e política; sem piadas "
    "sobre excesso de trabalho; sem emojis, links, HTML ou aspas. Cada frase tem no máximo 80 "
    f"caracteres e, em parte delas, usa o marcador {PLACEHOLDER} no lugar do primeiro nome da "
    "pessoa. Nenhum outro marcador é permitido. Responda apenas com as frases, uma por linha, "
    "sem numeração nem comentários."
)

_PREFIXO = re.compile(r"^\s*(?:[-*•–]+|\d+\s*[.)\-:])\s*")
_ASPAS = "\"'“”‘’«»`"

assert set(DESCRICOES) == set(OCASIOES) == set(CATALOGO)  # noqa: S101 - consistência estática


class CacheTTL:
    """Cache em memória (por processo) com validade curta, para aliviar o banco.

    Guarda só o gate da IA, as frases aprovadas e textos de resumo (nada de dado pessoal). A cada
    gravação poda as entradas vencidas e, se passar de `max_entradas`, descarta as mais antigas,
    para a memória não crescer sem limite. Escritas do admin limpam o cache do processo; os demais
    processos convergem em até `ttl` segundos.
    """

    def __init__(
        self,
        ttl: float = 45.0,
        relogio: Callable[[], float] = time.monotonic,
        max_entradas: int = 512,
    ) -> None:
        self.ttl = ttl
        self.max_entradas = max_entradas
        self._relogio = relogio
        self._dados: dict[Any, tuple[float, Any]] = {}  # ordem de inserção = ordem de gravação
        self._trava = threading.Lock()

    def __len__(self) -> int:
        return len(self._dados)

    def obter(self, chave: Any, carregar: Callable[[], Any]) -> Any:
        agora = self._relogio()
        with self._trava:
            hit = self._dados.get(chave)
            if hit is not None and agora - hit[0] < self.ttl:
                return hit[1]
        valor = carregar()  # fora da trava: o banco pode demorar
        with self._trava:
            self._gravar(chave, agora, valor)
        return valor

    def _gravar(self, chave: Any, agora: float, valor: Any) -> None:
        for k in [k for k, (t, _) in self._dados.items() if agora - t >= self.ttl]:
            del self._dados[k]
        self._dados.pop(chave, None)  # regravar move a chave para o fim (mais recente)
        self._dados[chave] = (agora, valor)
        while len(self._dados) > self.max_entradas:
            del self._dados[next(iter(self._dados))]

    def limpar(self) -> None:
        with self._trava:
            self._dados.clear()


CACHE = CacheTTL()


def primeiro_nome(nome: str | None) -> str | None:
    """Primeiro nome sanitizado do perfil (sem controle, '<', '>', chaves); None se vazio."""
    if not nome:
        return None
    limpo = "".join(
        " " if c.isspace() else c
        for c in nome
        if (c.isspace() or not unicodedata.category(c).startswith("C")) and c not in "<>{}"
    )
    partes = limpo.split()
    return partes[0][:MAX_NOME] if partes else None


def _indice(semente: str, tamanho: int) -> int:
    h = hashlib.sha256(semente.encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") % tamanho


def escolher_texto(
    ocasiao: Ocasiao,
    *,
    usuario_id: str,
    nome: str | None,
    aprovadas: Callable[[str], list[str]],
) -> str:
    """Frase estável por (usuário, data, período, ocasião): no mesmo período devolve a mesma.

    `aprovadas(ocasiao)` traz as frases aprovadas no banco; revalidadas aqui (defesa em
    profundidade). Sem nome, só entram frases sem marcador (ou o marcador é removido).
    """
    primeiro = primeiro_nome(nome)
    for alvo in (ocasiao.principal, *ocasiao.apoio):
        pool = list(CATALOGO[alvo])
        conhecidas = {normalizar(t) for t in pool}
        for t in sorted(aprovadas(alvo)):
            if motivo_invalido(t) is None and normalizar(t) not in conhecidas:
                conhecidas.add(normalizar(t))
                pool.append(t)
        if primeiro is None:
            sem = [t for t in pool if not tem_placeholder(t)]
            pool = sem or [sem_placeholder(t) for t in pool]
        pool = [t for t in pool if t]
        if not pool:
            continue
        semente = f"{usuario_id}|{ocasiao.data.isoformat()}|{ocasiao.periodo}|{alvo}"
        texto = pool[_indice(semente, len(pool))]
        return texto.replace(PLACEHOLDER, primeiro) if primeiro else texto
    return ""  # inalcançável: o catálogo cobre todas as ocasiões


def _acoes(status: Any) -> list[str]:
    """Pendente: aprovar ou rejeitar. Aprovada/rejeitada: só a outra decisão (nunca pendente).

    Só a rejeitada pode ser excluída."""
    return {
        "pendente": ["aprovar", "rejeitar"],
        "aprovada": ["rejeitar"],
        "rejeitada": ["aprovar", "excluir"],
    }.get(status, [])


def _item(linha: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": linha["id"],
        "ocasiao": linha["ocasiao"],
        "texto": linha["texto"],
        "origem": linha["origem"],
        "status": linha["status"],
        "criado_em": linha.get("criado_em"),
        "acoes_permitidas": _acoes(linha.get("status")),
    }


def _ocasiao_valida(valor: Any) -> str:
    if not isinstance(valor, str) or valor not in OCASIOES:
        raise RequisicaoInvalida("Ocasião inválida.", "ocasiao_invalida")
    return valor


def _corpo_objeto(corpo: Any, permitidos: set[str]) -> dict[str, Any]:
    if not isinstance(corpo, dict):
        raise RequisicaoInvalida("O corpo deve ser um objeto JSON.")
    extras = sorted(set(corpo) - permitidos)
    if extras:
        raise RequisicaoInvalida("Campos não reconhecidos: " + ", ".join(extras) + ".")
    return corpo


def extrair_frases(bruto: str) -> list[str]:
    """Uma frase por linha; remove numeração, marcadores e aspas externas. Sem validar."""
    frases = []
    for linha in bruto.splitlines():
        limpa = _PREFIXO.sub("", linha).strip().strip(_ASPAS).strip()
        if limpa:
            frases.append(limpa)
    return frases


class ServicoSaudacao:
    def __init__(
        self,
        ia: ServicoIA | None,
        repo: RepositorioSaudacoes | None,
        *,
        agora: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ia = ia
        self._repo = repo
        self._agora = agora

    # -- usuário -------------------------------------------------------------------------
    def _ligada(self) -> bool:
        """Gate: chave geral E saudação ligadas no banco (não depende de chave de provedor)."""
        if self._ia is None or self._ia.repo is None:
            return False
        leitor = self._ia.repo.ler_config
        try:
            banco = CACHE.obter(("gate", id(self._ia.repo)), leitor)
        except Exception as exc:  # noqa: BLE001 - a saudação nunca derruba o app
            log.warning("saudacao_config_indisponivel tipo=%s", type(exc).__name__)
            return False
        return bool(banco.mestre and banco.saudacao)

    def _aprovadas(self, ocasiao: str) -> list[str]:
        if self._repo is None:
            return []
        repo = self._repo
        try:
            return list(
                CACHE.obter(
                    ("aprovadas", id(repo), ocasiao), lambda: repo.listar_aprovadas(ocasiao)
                )
            )
        except Exception as exc:  # noqa: BLE001 - cai no catálogo embutido
            log.warning("saudacao_aprovadas_indisponivel tipo=%s", type(exc).__name__)
            return []

    def para_usuario(self, perfil: Perfil) -> str | None:
        return self.atual(perfil)["texto"]

    def atual(self, perfil: Perfil) -> dict[str, str | None]:
        """Texto, chave do período e validade (ISO 8601 -03:00). Tudo nulo com o gate desligado."""
        vazio: dict[str, str | None] = {"texto": None, "chave": None, "valido_ate": None}
        if not self._ligada():
            return vazio
        agora = self._agora()
        texto = escolher_texto(
            resolver(agora), usuario_id=perfil.id, nome=perfil.nome, aprovadas=self._aprovadas
        )
        if not texto:
            return vazio
        chave, validade = chave_validade(agora)
        return {"texto": texto, "chave": chave, "valido_ate": validade.isoformat()}

    # -- admin ---------------------------------------------------------------------------
    def _repositorio(self) -> RepositorioSaudacoes:
        if self._repo is None:
            raise ErroApi(MSG_SEM_REPOSITORIO, "indisponivel", 503)
        return self._repo

    def listar(self, admin: Perfil, status: str | None, ocasiao: str | None) -> dict[str, Any]:
        exigir_admin(admin)
        if status is not None and status not in STATUS:
            raise RequisicaoInvalida("Status inválido.", "status_invalido")
        if ocasiao is not None:
            _ocasiao_valida(ocasiao)
        linhas = self._repositorio().listar(status=status, ocasiao=ocasiao)
        return {"itens": [_item(r) for r in linhas], "ocasioes": list(OCASIOES)}

    def _existentes(self, ocasiao: str) -> set[str]:
        achadas = {normalizar(t) for t in CATALOGO[ocasiao]}
        achadas |= {
            normalizar(str(r["texto"])) for r in self._repositorio().listar(ocasiao=ocasiao)
        }
        return achadas

    def criar_manual(self, admin: Perfil, corpo: Any) -> dict[str, Any]:
        exigir_admin(admin)
        dados = _corpo_objeto(corpo, {"ocasiao", "texto"})
        ocasiao = _ocasiao_valida(dados.get("ocasiao"))
        texto = dados.get("texto")
        if isinstance(texto, str):
            texto = texto.strip()
        motivo = motivo_invalido(texto)
        if motivo:
            raise RequisicaoInvalida(motivo, "texto_invalido")
        assert isinstance(texto, str)  # noqa: S101 - garantido por motivo_invalido
        if normalizar(texto) in self._existentes(ocasiao):
            raise Conflito("Esta frase já existe para a ocasião.", "saudacao_duplicada")
        repo = self._repositorio()
        # O banco só aceita INSERT pendente: a frase manual é criada e então aprovada pelo admin.
        criada = repo.inserir(ocasiao=ocasiao, texto=texto, origem="manual", criado_por=admin.id)
        CACHE.limpar()
        try:
            aprovada = repo.decidir(criada["id"], status="aprovada", ator_id=admin.id)
        except ErroApi:
            aprovada = None
        if aprovada is None:  # a frase existe, mas segue pendente: o admin pode aprovar na lista
            item = _item(criada)
            item["aviso"] = MSG_MANUAL_PENDENTE
            return item
        return _item(aprovada)

    def gerar(self, admin: Perfil, corpo: Any) -> dict[str, Any]:
        exigir_admin(admin)
        dados = _corpo_objeto(corpo, {"ocasiao", "quantidade"})
        ocasiao = _ocasiao_valida(dados.get("ocasiao"))
        qtd = dados.get("quantidade", QTD_PADRAO)
        if isinstance(qtd, bool) or not isinstance(qtd, int) or not QTD_MIN <= qtd <= QTD_MAX:
            raise RequisicaoInvalida(
                f"A quantidade deve ser um inteiro de {QTD_MIN} a {QTD_MAX}.", "quantidade_invalida"
            )
        repo = self._repositorio()
        if self._ia is None or not self._ia.disponivel(FUNCAO_IA):
            raise ErroApi(MSG_IA_INDISPONIVEL, "ia_indisponivel", 409)

        usuario = (
            f"Escreva {qtd} saudações diferentes para {DESCRICOES[ocasiao]}. "
            "Uma frase por linha."
        )
        r = self._ia.gerar(FUNCAO_IA, sistema=PROMPT_SISTEMA, usuario=usuario, perfil=admin)
        if r is None:
            raise ErroApi(MSG_IA_FALHOU, "ia_falhou", 502)

        existentes = self._existentes(ocasiao)
        criadas: list[dict[str, Any]] = []
        descartadas = 0
        for frase in extrair_frases(r.texto):
            if len(criadas) >= qtd:
                break
            if motivo_invalido(frase) or normalizar(frase) in existentes:
                descartadas += 1
                continue
            existentes.add(normalizar(frase))
            try:
                linha = repo.inserir(
                    ocasiao=ocasiao,
                    texto=frase,
                    origem="ia",
                    criado_por=admin.id,
                )
            except Conflito:  # corrida com outra geração: já existe
                descartadas += 1
                continue
            criadas.append(_item(linha))
        return {"itens": criadas, "descartadas": descartadas}

    def decidir(self, admin: Perfil, saudacao_id: str, *, aprovar: bool) -> dict[str, Any]:
        exigir_admin(admin)
        sid = validar_uuid(saudacao_id)
        repo = self._repositorio()
        linha = repo.decidir(sid, status="aprovada" if aprovar else "rejeitada", ator_id=admin.id)
        if linha is None:
            if repo.obter(sid) is None:
                raise NaoEncontrado("Saudação não encontrada.")
            raise TransicaoInvalida(
                "Esta frase já está " + ("aprovada." if aprovar else "rejeitada.")
            )
        CACHE.limpar()
        return _item(linha)

    def excluir(self, admin: Perfil, saudacao_id: str) -> dict[str, Any]:
        """Exclui (DELETE físico) só frase rejeitada; a auditoria sai do RPC no banco."""
        exigir_admin(admin)
        sid = validar_uuid(saudacao_id)
        repo = self._repositorio()
        atual = repo.obter(sid)
        if atual is None:
            raise NaoEncontrado("Saudação não encontrada.")
        if atual.get("status") != "rejeitada":
            raise TransicaoInvalida(MSG_EXCLUSAO)
        # Corrida: o RPC reconfere (AG003) e devolve False se outra exclusão chegou antes.
        if not repo.excluir_rejeitada(sid, ator_id=admin.id):
            raise NaoEncontrado("Saudação não encontrada.")
        CACHE.limpar()
        return {"id": sid, "excluida": True}
