"""Erros da API no formato {"erro": {"codigo", "mensagem"}}."""

from __future__ import annotations

import logging

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

log = logging.getLogger(__name__)


class ErroApi(Exception):
    """Erro previsto, com código estável para o cliente e mensagem em pt-BR."""

    status = 400
    codigo = "erro"

    def __init__(self, mensagem: str, codigo: str | None = None, status: int | None = None):
        super().__init__(mensagem)
        self.mensagem = mensagem
        if codigo:
            self.codigo = codigo
        if status:
            self.status = status


class RequisicaoInvalida(ErroApi):
    status, codigo = 400, "requisicao_invalida"


class NaoAutenticado(ErroApi):
    status, codigo = 401, "nao_autenticado"


class SemPermissao(ErroApi):
    status, codigo = 403, "sem_permissao"


class NaoEncontrado(ErroApi):
    status, codigo = 404, "nao_encontrado"


class TransicaoInvalida(ErroApi):
    status, codigo = 409, "transicao_invalida"


class VeiculoOcupado(ErroApi):
    status, codigo = 409, "veiculo_ocupado"


class VeiculoIndisponivel(ErroApi):
    status, codigo = 409, "veiculo_indisponivel"


class RecursoDesligado(ErroApi):
    status, codigo = 404, "recurso_desligado"


class ChecklistObrigatorio(ErroApi):
    status, codigo = 409, "checklist_obrigatorio"


class ChecklistForaDaJanela(ErroApi):
    status, codigo = 409, "checklist_fora_da_janela"


class ErroInterno(ErroApi):
    status, codigo = 503, "indisponivel"


class Conflito(ErroApi):
    status, codigo = 409, "conflito"


class AutoAlteracaoProibida(ErroApi):
    status, codigo = 403, "auto_alteracao_proibida"


class UltimoAdmin(ErroApi):
    status, codigo = 409, "ultimo_admin"


class PlacaDuplicada(ErroApi):
    status, codigo = 409, "placa_duplicada"


class ConviteAbertoExistente(ErroApi):
    status, codigo = 409, "convite_aberto_existente"


class ConviteEncerrado(ErroApi):
    status, codigo = 409, "convite_encerrado"


class ConviteExpirado(ErroApi):
    status, codigo = 410, "convite_expirado"


class ConviteInvalido(ErroApi):
    status, codigo = 400, "convite_invalido"


class NomeObrigatorio(ErroApi):
    status, codigo = 400, "nome_obrigatorio"


class ContaExistente(ErroApi):
    status, codigo = 409, "conta_existente"


class DependenciaRecurso(ErroApi):
    status, codigo = 409, "dependencia_recurso"


class SistemaOcupado(ErroApi):
    """Lock do banco (55P03) ou deadlock (40P01) persistente após as novas tentativas."""

    status, codigo = 503, "ocupado"


def corpo_erro(codigo: str, mensagem: str) -> dict[str, dict[str, str]]:
    return {"erro": {"codigo": codigo, "mensagem": mensagem}}


_HTTP = {
    400: ("requisicao_invalida", "Requisição inválida."),
    404: ("nao_encontrado", "Recurso não encontrado."),
    405: ("metodo_nao_permitido", "Método não permitido."),
    413: ("payload_grande", "Corpo da requisição acima do limite permitido."),
    415: ("tipo_nao_suportado", "Envie o corpo em JSON (Content-Type: application/json)."),
    429: ("limite_excedido", "Muitas requisições. Tente novamente em instantes."),
}


def registrar_erros(app: Flask) -> None:
    @app.errorhandler(ErroApi)
    def _api(e: ErroApi):  # type: ignore[no-untyped-def]
        return jsonify(corpo_erro(e.codigo, e.mensagem)), e.status

    @app.errorhandler(HTTPException)
    def _http(e: HTTPException):  # type: ignore[no-untyped-def]
        status = e.code or 500
        codigo, msg = _HTTP.get(status, ("erro", "Não foi possível processar a requisição."))
        return jsonify(corpo_erro(codigo, msg)), status

    @app.errorhandler(Exception)
    def _generico(e: Exception):  # type: ignore[no-untyped-def]
        # Só o tipo da exceção vai ao log: a mensagem pode conter dados pessoais.
        log.error("erro_nao_tratado tipo=%s", type(e).__name__)
        return jsonify(corpo_erro("erro_interno", "Erro interno. Tente novamente.")), 500
