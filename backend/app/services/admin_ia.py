"""Configuração de IA (somente admin): chave mestra, funções, provedor, modelo, reserva e teto.

Fonte da verdade: `ia_configuracoes` (a auditoria sai do trigger do banco). `AI_ENABLED=false`
no ambiente vence tudo: a tela mostra o motivo e as escritas são recusadas. As chaves de API
nunca aparecem aqui: só o booleano `chave_configurada`.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.errors import DependenciaRecurso, ErroApi, RecursoDesligado, RequisicaoInvalida
from app.ia.config import PROVEDORES, ROTULOS_PROVEDORES
from app.ia.repositorio import CAMPOS_CONFIG, ConfigIABanco
from app.ia.servico import ServicoIA, modelo_efetivo, teto_efetivo_tokens
from app.modelos import Perfil
from app.services.admin_usuarios import exigir_admin

MSG_DESLIGADO_SERVIDOR = (
    "A IA está desligada no servidor (AI_ENABLED). Peça a quem administra o servidor para ligar."
)
MSG_SEM_PROVEDOR = "Escolha um provedor com chave configurada antes de ligar a IA."

# chave -> (rótulo, descrição)
ITENS: dict[str, tuple[str, str]] = {
    "mestre": (
        "Inteligência artificial",
        "Chave geral. Desligada, nenhum recurso de IA é usado e o sistema segue com os "
        "textos padrão.",
    ),
    "saudacao": (
        "Saudação sazonal no login",
        "Mensagem de boas-vindas com felicitações por data comemorativa (sem dados pessoais).",
    ),
}


def _texto_ou_none(valor: Any, nome: str) -> str | None:
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise RequisicaoInvalida(f"O campo '{nome}' deve ser um texto.")
    return valor.strip() or None


def _validar_corpo(corpo: Any) -> dict[str, Any]:
    if not isinstance(corpo, dict) or not corpo:
        raise RequisicaoInvalida("Informe ao menos um campo de configuração de IA.")
    desconhecidos = sorted(set(corpo) - set(CAMPOS_CONFIG))
    if desconhecidos:
        raise RequisicaoInvalida("Campos não reconhecidos: " + ", ".join(desconhecidos) + ".")
    pedido: dict[str, Any] = {}
    for chave, valor in corpo.items():
        if chave in {"mestre", "saudacao"}:
            if not isinstance(valor, bool):
                raise RequisicaoInvalida(f"O campo '{chave}' deve ser verdadeiro ou falso.")
            pedido[chave] = valor
        elif chave == "teto_tokens_mes":
            if valor is not None and (
                isinstance(valor, bool)
                or not isinstance(valor, int)
                or not 1 <= valor <= 2_000_000_000
            ):
                raise RequisicaoInvalida("O teto mensal deve ser um inteiro positivo ou nulo.")
            pedido[chave] = valor
        else:
            pedido[chave] = _texto_ou_none(valor, chave)
    return pedido


class ServicoIAAdmin:
    def __init__(self, ia: ServicoIA) -> None:
        if ia.repo is None:
            raise ErroApi("Configuração de IA indisponível.", "indisponivel", 503)
        self._ia = ia
        self._repo = ia.repo

    # -- apresentação ----------------------------------------------------------------------
    def apresentar(self, banco: ConfigIABanco) -> dict[str, Any]:
        cfg = self._ia.cfg
        servidor = cfg.habilitado
        itens = []
        for chave, (rotulo, descricao) in ITENS.items():
            ligado = bool(getattr(banco, chave))
            motivo: str | None = None
            if not servidor:
                motivo = MSG_DESLIGADO_SERVIDOR
            elif chave == "mestre" and not ligado and not self._provedor_ativo_valido(banco):
                motivo = MSG_SEM_PROVEDOR
            elif chave != "mestre" and not ligado and not banco.mestre:
                motivo = "Ligue antes a chave geral de IA."
            if not servidor:
                acoes = ["desligar"] if ligado else []  # com o servidor desligado só se desliga
            else:
                acoes = [] if motivo else (["desligar"] if ligado else ["ligar"])
            itens.append(
                {
                    "chave": chave,
                    "rotulo": rotulo,
                    "descricao": descricao,
                    "habilitado": ligado,
                    "motivo_indisponibilidade": motivo,
                    "acoes_permitidas": acoes,
                }
            )
        provedores = [
            {
                "chave": p,
                "rotulo": ROTULOS_PROVEDORES[p],
                "chave_configurada": cfg.tem_chave(p),  # só o booleano, nunca a chave
                "modelos": list(cfg.modelos_de(p)),
                "circuito_aberto": self._ia.circuito_aberto(p),
            }
            for p in PROVEDORES
        ]
        return {
            "servidor_habilitado": servidor,
            "motivo_indisponibilidade": None if servidor else MSG_DESLIGADO_SERVIDOR,
            "itens": itens,
            "provedores": provedores,
            "provedor": banco.provedor,
            "modelo": banco.modelo,
            "provedor_reserva": banco.provedor_reserva,
            "teto_tokens_mes": banco.teto_tokens_mes,
            "teto_efetivo_tokens_mes": teto_efetivo_tokens(cfg, banco),
            "acoes_permitidas": ["editar"] if servidor else [],
            "testes_disponiveis": self.testes_disponiveis(banco),
        }

    def _provedor_ativo_valido(self, banco: ConfigIABanco) -> bool:
        return banco.provedor in PROVEDORES and self._ia.cfg.tem_chave(banco.provedor)

    # -- operações -------------------------------------------------------------------------
    def obter(self, admin: Perfil) -> dict[str, Any]:
        exigir_admin(admin)
        return self.apresentar(self._repo.ler_config())

    def testes_disponiveis(self, banco: ConfigIABanco) -> list[str]:
        """Provedores testáveis: ativo e reserva (quando definidos) com chave no servidor."""
        if not self._ia.cfg.habilitado:
            return []
        candidatos = [banco.provedor, banco.provedor_reserva]
        return [p for p in candidatos if p in PROVEDORES and self._ia.cfg.tem_chave(p)]

    def testar(self, admin: Perfil, corpo: Any) -> dict[str, Any]:
        """Teste de conexão com o provedor já salvo. Falha do provedor volta como ok=false."""
        exigir_admin(admin)
        if corpo is None:
            corpo = {}
        if not isinstance(corpo, dict):
            raise RequisicaoInvalida("O corpo deve ser um objeto JSON.")
        extras = sorted(set(corpo) - {"provedor"})
        if extras:
            raise RequisicaoInvalida("Campos não reconhecidos: " + ", ".join(extras) + ".")
        pedido = corpo.get("provedor")
        if pedido is not None and (not isinstance(pedido, str) or pedido not in PROVEDORES):
            raise RequisicaoInvalida(
                "O campo 'provedor' deve ser um de: " + ", ".join(PROVEDORES) + "."
            )
        if not self._ia.cfg.habilitado:
            raise RecursoDesligado(MSG_DESLIGADO_SERVIDOR)
        banco = self._repo.ler_config()
        provedor = pedido or banco.provedor
        if provedor is None:
            raise DependenciaRecurso("Escolha e salve um provedor antes de testar a conexão.")
        if provedor not in self.testes_disponiveis(banco):
            raise DependenciaRecurso(
                "Só é possível testar o provedor ativo ou o reserva, com chave no servidor."
            )
        return asdict(self._ia.testar(provedor, banco=banco, perfil=admin))

    def atualizar(self, admin: Perfil, corpo: Any) -> dict[str, Any]:
        exigir_admin(admin)
        pedido = _validar_corpo(corpo)
        cfg = self._ia.cfg
        # Servidor desligado: só é permitido DESLIGAR (evita ficar "ligado" no banco e reaparecer
        # quando o servidor for religado). Qualquer outra escrita é recusada.
        so_desliga = all(k in {"mestre", "saudacao"} and v is False for k, v in pedido.items())
        if not cfg.habilitado and not so_desliga:
            raise RecursoDesligado(MSG_DESLIGADO_SERVIDOR)

        atual = self._repo.ler_config()
        novo = {**atual.como_dict(), **pedido}

        # Trocar de provedor sem informar modelo: o modelo antigo deixa de valer.
        if "provedor" in pedido and "modelo" not in pedido and pedido["provedor"] != atual.provedor:
            novo["modelo"] = None

        provedor, reserva, modelo = novo["provedor"], novo["provedor_reserva"], novo["modelo"]
        for nome, valor in (("provedor", provedor), ("provedor_reserva", reserva)):
            if valor is not None and valor not in PROVEDORES:
                raise RequisicaoInvalida(
                    f"O campo '{nome}' deve ser um de: " + ", ".join(PROVEDORES) + "."
                )
            if valor is not None and nome in pedido and not cfg.tem_chave(valor):
                raise DependenciaRecurso(
                    f"O provedor {ROTULOS_PROVEDORES[valor]} não tem chave configurada no servidor."
                )
        if reserva is not None and reserva == provedor:
            raise RequisicaoInvalida("O provedor reserva deve ser diferente do provedor ativo.")
        if reserva is not None and provedor is None:
            raise DependenciaRecurso("Escolha o provedor ativo antes do provedor reserva.")
        if modelo is not None:
            if provedor is None:
                raise DependenciaRecurso("Escolha o provedor antes do modelo.")
            if modelo not in cfg.modelos_de(provedor):
                raise RequisicaoInvalida("Modelo não permitido para este provedor.")
        if provedor is not None and modelo is None and "modelo" not in pedido:
            novo["modelo"] = modelo_efetivo(cfg, ConfigIABanco(provedor=provedor), provedor)

        if novo["mestre"] and not (provedor in PROVEDORES and cfg.tem_chave(provedor)):
            raise DependenciaRecurso(MSG_SEM_PROVEDOR)
        # Só ao LIGAR a função: desligar a chave geral preserva os flags já escolhidos.
        if pedido.get("saudacao") and not novo["mestre"]:
            raise DependenciaRecurso("Ligue antes a chave geral de IA.")

        mudou = {k: v for k, v in novo.items() if atual.como_dict()[k] != v}
        if not mudou:
            return self.apresentar(atual)
        gravado = self._repo.atualizar_config(mudou, ator_id=admin.id)
        return self.apresentar(gravado)
