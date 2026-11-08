"""Serviço de execução de IA: decide SE pode chamar e chama; qualquer falha devolve None.

Ordem das barreiras em `gerar` (a primeira que falhar devolve None, e o chamador usa o texto
padrão): AI_ENABLED no ambiente -> chave mestra e flag da função no banco -> cota (por usuário e
por unidade na última hora; tokens no mês) -> por provedor (ativo e depois reserva): chave
presente, circuito fechado, chamada com timeout. Nunca lança; nunca registra prompt/resposta.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.ia.circuito import Circuito
from app.ia.config import PROVEDORES, ConfigIA
from app.ia.provedores import ErroProvedor, Provedor, criar_provedor
from app.ia.repositorio import ConfigIABanco, Consumo, RepositorioIA
from app.modelos import Perfil

log = logging.getLogger(__name__)

# Função -> coluna de `ia_configuracoes` que a liga. Função nova = nova coluna (nova migration).
FUNCOES: dict[str, str] = {"saudacao": "saudacao"}
JANELA_HORA = timedelta(hours=1)


@dataclass(frozen=True)
class ResultadoGeracao:
    texto: str
    provedor: str
    modelo: str


TESTE_SISTEMA = "Você é um verificador de conectividade."
TESTE_USUARIO = "Responda apenas com a palavra: ok"
MENSAGENS_TESTE: dict[str, str] = {
    "auth": "O provedor recusou a chave: ela é inválida, expirou ou não tem permissão.",
    "limite": "O provedor limitou as requisições (limite de uso ou de crédito). Tente mais tarde.",
    "timeout": "Tempo esgotado: o provedor não respondeu dentro do prazo (AI_TIMEOUT_S).",
    "rede": "Sem conexão com o provedor: verifique a rede e o acesso de saída do servidor.",
    "http": (
        "O provedor rejeitou a requisição: o modelo pode não existir ou a chave não tem acesso "
        "a ele, ou o provedor está com problemas."
    ),
    "formato": (
        "Resposta vazia ou inesperada do provedor. Em modelos de raciocínio isso pode ser "
        "AI_MAX_TOKENS baixo demais: tente aumentá-lo."
    ),
    "cota": "Cota de uso de IA atingida (por hora ou teto mensal). Tente mais tarde.",
    "erro": "Falha inesperada ao testar a conexão com o provedor.",
}


@dataclass(frozen=True)
class ResultadoTeste:
    ok: bool
    provedor: str
    modelo: str
    latencia_ms: int | None
    tokens_entrada: int
    tokens_saida: int
    tipo: str | None
    mensagem: str


def _falha(provedor: str, modelo: str, tipo: str, latencia_ms: int | None) -> ResultadoTeste:
    return ResultadoTeste(False, provedor, modelo, latencia_ms, 0, 0, tipo, MENSAGENS_TESTE[tipo])


def modelo_efetivo(cfg: ConfigIA, banco: ConfigIABanco, provedor: str) -> str | None:
    """Modelo do provedor: o escolhido na tela (só para o ativo, e só se estiver na lista
    permitida); senão o primeiro da lista permitida."""
    permitidos = cfg.modelos_de(provedor)
    if provedor == banco.provedor and banco.modelo in permitidos:
        return banco.modelo
    return permitidos[0] if permitidos else None


def teto_efetivo_tokens(cfg: ConfigIA, banco: ConfigIABanco) -> int:
    """O menor entre o teto do ambiente e o da tela."""
    if banco.teto_tokens_mes is None:
        return cfg.teto_tokens_mes
    return min(cfg.teto_tokens_mes, banco.teto_tokens_mes)


def inicio_do_mes(agora: datetime) -> datetime:
    """Início do mês corrente em America/Sao_Paulo, em UTC."""
    from zoneinfo import ZoneInfo

    sp = ZoneInfo("America/Sao_Paulo")
    local = agora.astimezone(sp).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC)


class ServicoIA:
    def __init__(
        self,
        cfg: ConfigIA,
        repo: RepositorioIA | None,
        *,
        provedores: dict[str, Provedor] | None = None,
        relogio: Callable[[], float] = time.monotonic,
        agora: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.cfg = cfg
        self.repo = repo
        self._provedores = dict(provedores or {})
        self._agora = agora
        self._circuitos = {
            p: Circuito(cfg.circuito_falhas, cfg.circuito_abertura_s, relogio) for p in PROVEDORES
        }

    def __repr__(self) -> str:
        return "ServicoIA()"

    # -- consulta ------------------------------------------------------------------------
    def circuito_aberto(self, provedor: str) -> bool:
        c = self._circuitos.get(provedor)
        return c.aberto if c else False

    def disponivel(self, funcao: str) -> bool:
        """True se a função pode tentar usar IA agora (sem checar cota nem circuito)."""
        coluna = FUNCOES.get(funcao)
        if coluna is None or not self.cfg.habilitado or self.repo is None:
            return False
        try:
            banco = self.repo.ler_config()
        except Exception as exc:  # noqa: BLE001 - nunca propaga
            log.warning("ia_config_indisponivel tipo=%s", type(exc).__name__)
            return False
        return bool(banco.mestre and getattr(banco, coluna))

    # -- geração -------------------------------------------------------------------------
    def gerar(
        self, funcao: str, *, sistema: str, usuario: str, perfil: Perfil | None = None
    ) -> ResultadoGeracao | None:
        """Texto da IA ou None (use o texto padrão). Nunca lança."""
        try:
            return self._gerar(funcao, sistema, usuario, perfil)
        except Exception as exc:  # noqa: BLE001 - IA nunca derruba a funcionalidade
            log.warning("ia_erro_inesperado tipo=%s", type(exc).__name__)
            return None

    def _gerar(
        self, funcao: str, sistema: str, usuario: str, perfil: Perfil | None
    ) -> ResultadoGeracao | None:
        coluna = FUNCOES.get(funcao)
        if coluna is None or not self.cfg.habilitado or self.repo is None:
            return None
        banco = self.repo.ler_config()
        if not (banco.mestre and getattr(banco, coluna)):
            return None

        candidatos = [p for p in (banco.provedor, banco.provedor_reserva) if p in PROVEDORES]
        candidatos = [p for p in candidatos if self.cfg.tem_chave(p)]
        if not candidatos:
            return None

        if not self._dentro_da_cota(banco, perfil):
            return None

        for nome in candidatos:
            modelo = modelo_efetivo(self.cfg, banco, nome)
            if modelo is None:
                continue
            circuito = self._circuitos[nome]
            if not circuito.permite():
                log.info("ia_circuito_aberto provedor=%s", nome)
                continue
            inicio = time.monotonic()
            try:
                r = self._provedor(nome).gerar(
                    modelo=modelo,
                    sistema=sistema,
                    usuario=usuario,
                    max_tokens=self.cfg.max_tokens,
                    timeout=self.cfg.timeout_s,
                )
            except Exception as exc:  # noqa: BLE001 - qualquer falha conta e libera o circuito
                # Não só ErroProvedor: uma exceção inesperada (ex.: chave com caractere inválido
                # no cabeçalho) também precisa fechar a tentativa do meio-aberto, senão o
                # provedor ficaria bloqueado até reiniciar o processo.
                tipo = exc.tipo if isinstance(exc, ErroProvedor) else type(exc).__name__
                circuito.falha()
                log.warning("ia_falha provedor=%s tipo=%s", nome, tipo)
                self._registrar(
                    funcao,
                    nome,
                    modelo,
                    perfil,
                    0,
                    0,
                    "timeout" if tipo == "timeout" else "erro",
                    inicio,
                )
                continue
            circuito.sucesso()
            self._registrar(
                funcao, nome, modelo, perfil, r.tokens_entrada, r.tokens_saida, "ok", inicio
            )
            return ResultadoGeracao(r.texto, nome, modelo)
        return None

    # -- teste de conexão ----------------------------------------------------------------
    def testar(
        self, provedor: str, *, banco: ConfigIABanco, perfil: Perfil | None = None
    ) -> ResultadoTeste:
        """Chamada mínima e fixa ao provedor para o admin validar chave/modelo. Nunca lança.

        Ignora a chave geral, a função e o circuito aberto como bloqueio (o admin precisa
        diagnosticar), mas passa pela cota e atualiza o circuito. O texto do modelo é descartado.
        """
        modelo = modelo_efetivo(self.cfg, banco, provedor) or ""
        try:
            return self._testar(provedor, modelo, banco, perfil)
        except Exception as exc:  # noqa: BLE001 - o teste nunca vira erro 500
            log.warning("ia_teste_erro_inesperado tipo=%s", type(exc).__name__)
            return _falha(provedor, modelo, "erro", None)

    def _testar(
        self, provedor: str, modelo: str, banco: ConfigIABanco, perfil: Perfil | None
    ) -> ResultadoTeste:
        if not modelo:
            return _falha(provedor, modelo, "erro", None)
        if not self._dentro_da_cota(banco, perfil):
            return _falha(provedor, modelo, "cota", None)
        circuito = self._circuitos[provedor]
        inicio = time.monotonic()
        try:
            r = self._provedor(provedor).gerar(
                modelo=modelo,
                sistema=TESTE_SISTEMA,
                usuario=TESTE_USUARIO,
                max_tokens=self.cfg.max_tokens,
                timeout=self.cfg.timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - qualquer falha conta e libera o circuito
            tipo = exc.tipo if isinstance(exc, ErroProvedor) else "erro"
            if tipo not in MENSAGENS_TESTE:
                tipo = "erro"
            circuito.falha()
            log.warning("ia_teste_falha provedor=%s tipo=%s", provedor, tipo)
            self._registrar(
                "teste",
                provedor,
                modelo,
                perfil,
                0,
                0,
                "timeout" if tipo == "timeout" else "erro",
                inicio,
            )
            return _falha(provedor, modelo, tipo, int((time.monotonic() - inicio) * 1000))
        circuito.sucesso()
        self._registrar(
            "teste", provedor, modelo, perfil, r.tokens_entrada, r.tokens_saida, "ok", inicio
        )
        return ResultadoTeste(
            ok=True,
            provedor=provedor,
            modelo=modelo,
            latencia_ms=int((time.monotonic() - inicio) * 1000),
            tokens_entrada=r.tokens_entrada,
            tokens_saida=r.tokens_saida,
            tipo=None,
            mensagem="Conexão funcionando: o provedor respondeu normalmente.",
        )

    # -- internos ------------------------------------------------------------------------
    def _provedor(self, nome: str) -> Provedor:
        if nome not in self._provedores:
            self._provedores[nome] = criar_provedor(nome, self.cfg.chave(nome))
        return self._provedores[nome]

    def _repo(self) -> RepositorioIA:
        if self.repo is None:  # chamadores já checam; protege contra uso indevido
            raise RuntimeError("repositório de IA ausente")
        return self.repo

    def _dentro_da_cota(self, banco: ConfigIABanco, perfil: Perfil | None) -> bool:
        agora = self._agora()
        consumo: Consumo = self._repo().consumo(
            usuario_id=perfil.id if perfil else None,
            unidade=perfil.unidade if perfil else None,
            desde_hora=agora - JANELA_HORA,
            desde_mes=inicio_do_mes(agora),
        )
        if perfil is not None:
            if consumo.usuario_hora >= self.cfg.limite_por_usuario_hora:
                log.info("ia_cota_usuario_excedida")
                return False
            if consumo.unidade_hora >= self.cfg.limite_por_unidade_hora:
                log.info("ia_cota_unidade_excedida")
                return False
        if consumo.tokens_mes_total >= teto_efetivo_tokens(self.cfg, banco):
            log.info("ia_cota_mensal_excedida")
            return False
        return True

    def _registrar(
        self,
        funcao: str,
        provedor: str,
        modelo: str,
        perfil: Perfil | None,
        tokens_entrada: int,
        tokens_saida: int,
        status: str,
        inicio: float,
    ) -> None:
        try:
            self._repo().registrar_uso(
                funcao=funcao,
                provedor=provedor,
                modelo=modelo,
                usuario_id=perfil.id if perfil else None,
                unidade=perfil.unidade if perfil else None,
                tokens_entrada=tokens_entrada,
                tokens_saida=tokens_saida,
                status=status,
                latencia_ms=int((time.monotonic() - inicio) * 1000),
            )
        except Exception as exc:  # noqa: BLE001 - falha de registro não derruba a função
            log.warning("ia_uso_nao_registrado tipo=%s", type(exc).__name__)
