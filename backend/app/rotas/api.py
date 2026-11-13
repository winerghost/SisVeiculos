"""Rotas sob /api. Só traduzem HTTP <-> services; nenhuma regra de negócio aqui."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify, request

from app.auth.decoradores import requer_auth
from app.errors import RequisicaoInvalida
from app.extensions import chave_usuario, deps, limiter
from app.services.checklist import ServicoChecklist
from app.services.foto_perfil import ServicoFotoPerfil
from app.services.fotos_exibicao import FotosExibicao
from app.services.motorista import ServicoMotorista
from app.services.perfis import montar_eu
from app.services.requisicoes import ServicoRequisicoes
from app.services.veiculos import ServicoVeiculos

api = Blueprint("api", __name__, url_prefix="/api")


def _limite_escrita() -> str:
    return deps().config.limite_escrita


def _req() -> ServicoRequisicoes:
    return ServicoRequisicoes(
        deps().repo,
        notificador=deps().notificador,
        fotos=_exibicao(),
    )


def _exibicao() -> FotosExibicao:
    return FotosExibicao(deps().repo, deps().armazenamento_avatares)


def _foto_perfil() -> ServicoFotoPerfil:
    d = deps()
    return ServicoFotoPerfil(
        d.repo, d.armazenamento_avatares, max_emissoes=d.config.max_emissoes_foto_perfil
    )


def _checklist() -> ServicoChecklist:
    d = deps()
    return ServicoChecklist(
        d.repo,
        d.armazenamento,
        _req(),
        max_urls_upload=d.config.max_urls_upload_por_momento,
        notificador=d.notificador,
    )


def _inteiro(nome: str, padrao: int) -> int:
    bruto = request.args.get(nome)
    if bruto is None:
        return padrao
    try:
        return int(bruto)
    except ValueError:
        raise RequisicaoInvalida(f"O parâmetro '{nome}' deve ser um número inteiro.") from None


def _booleano(nome: str, padrao: bool) -> bool:
    """Parâmetro de consulta `true`/`false` (qualquer outro valor é 400)."""
    bruto = request.args.get(nome)
    if bruto is None:
        return padrao
    valor = bruto.strip().lower()
    if valor not in {"true", "false"}:
        raise RequisicaoInvalida(f"O parâmetro '{nome}' deve ser 'true' ou 'false'.")
    return valor == "true"


@api.get("/saude")
@limiter.exempt
def saude() -> Any:
    return jsonify({"status": "ok"})


@api.get("/eu")
@requer_auth()
def eu() -> Any:
    foto_url = _exibicao().urls([g.perfil.id], ativos={g.perfil.id}).get(g.perfil.id)
    return jsonify(montar_eu(g.perfil, deps().repo.ler_configuracoes(), foto_url))


# -- Foto de perfil (sempre ligada; regra em services/foto_perfil.py) ----------------------
def _limite_foto() -> str:
    return deps().config.limite_foto_perfil


def _limite_foto_ip() -> str:
    return deps().config.limite_foto_perfil_ip


# O dono é sempre o usuário do JWT (g.perfil): nenhuma destas rotas aceita id de usuário.
@api.post("/eu/foto/upload-url")
@requer_auth()
@limiter.limit(_limite_foto_ip)
@limiter.limit(_limite_foto, key_func=chave_usuario)
def solicitar_upload_foto_perfil() -> Any:
    return jsonify(_foto_perfil().solicitar_upload(g.perfil, request.get_json(silent=True))), 201


@api.post("/eu/foto")
@requer_auth()
@limiter.limit(_limite_foto_ip)
@limiter.limit(_limite_foto, key_func=chave_usuario)
def registrar_foto_perfil() -> Any:
    return jsonify(_foto_perfil().registrar(g.perfil, request.get_json(silent=True)))


@api.delete("/eu/foto")
@requer_auth()
@limiter.limit(_limite_escrita)
def remover_foto_perfil() -> Any:
    return jsonify(_foto_perfil().remover(g.perfil))


@api.get("/veiculos")
@requer_auth()
def listar_veiculos() -> Any:
    return jsonify({"itens": ServicoVeiculos(deps().repo).listar()})


@api.get("/requisicoes")
@requer_auth()
def listar_requisicoes() -> Any:
    resultado = _req().listar(
        g.perfil,
        status=request.args.get("status"),
        limite=_inteiro("limite", 50),
        offset=_inteiro("offset", 0),
        arquivadas=_booleano("arquivadas", False),
    )
    return jsonify(resultado)


@api.get("/requisicoes/horario-referencia")
@requer_auth("solicitante", "autorizador", "admin", "vigilante")
def horario_referencia() -> Any:
    resposta = jsonify(_req().horario_referencia())
    resposta.headers["Cache-Control"] = "no-store"  # service worker é network-first para a API
    return resposta


@api.post("/requisicoes/<requisicao_id>/arquivar")
@requer_auth()  # o service decide por papel, dono/atribuição e estado
@limiter.limit(_limite_escrita)
def arquivar(requisicao_id: str) -> Any:
    return jsonify(_req().arquivar(g.perfil, requisicao_id))


@api.post("/requisicoes/<requisicao_id>/desarquivar")
@requer_auth()
@limiter.limit(_limite_escrita)
def desarquivar(requisicao_id: str) -> Any:
    return jsonify(_req().desarquivar(g.perfil, requisicao_id))


@api.post("/requisicoes")
@requer_auth("solicitante", "autorizador", "admin", "vigilante")
@limiter.limit(_limite_escrita)
def criar_requisicao() -> Any:
    corpo = request.get_json(silent=True)
    return jsonify(_req().criar(g.perfil, corpo)), 201


@api.get("/requisicoes/<requisicao_id>")
@requer_auth()
def obter_requisicao(requisicao_id: str) -> Any:
    svc = _req()
    return jsonify(svc.apresentar(g.perfil, svc.obter(g.perfil, requisicao_id)))


def _acao(acao: str, requisicao_id: str, *, com_corpo: bool = False) -> Any:
    corpo = request.get_json(silent=True) if com_corpo else None
    return jsonify(_req().executar(acao, g.perfil, requisicao_id, corpo))


@api.post("/requisicoes/<requisicao_id>/autorizar")
@requer_auth("autorizador", "admin")
@limiter.limit(_limite_escrita)
def autorizar(requisicao_id: str) -> Any:
    return _acao("autorizar", requisicao_id, com_corpo=True)  # corpo opcional: motorista_id


@api.post("/requisicoes/<requisicao_id>/negar")
@requer_auth("autorizador", "admin")
@limiter.limit(_limite_escrita)
def negar(requisicao_id: str) -> Any:
    return _acao("negar", requisicao_id)


@api.post("/requisicoes/<requisicao_id>/saida")
@requer_auth("autorizador", "admin", "motorista", "vigilante")  # service confere flag/dono
@limiter.limit(_limite_escrita)
def registrar_saida(requisicao_id: str) -> Any:
    return _acao("registrar_saida", requisicao_id, com_corpo=True)


@api.post("/requisicoes/<requisicao_id>/chegada")
@requer_auth("autorizador", "admin", "motorista", "vigilante")  # service confere flag/dono
@limiter.limit(_limite_escrita)
def registrar_chegada(requisicao_id: str) -> Any:
    return _acao("registrar_chegada", requisicao_id, com_corpo=True)


@api.post("/requisicoes/<requisicao_id>/motorista")
@requer_auth("autorizador", "admin")
@limiter.limit(_limite_escrita)
def atribuir_motorista(requisicao_id: str) -> Any:
    corpo = request.get_json(silent=True)
    return jsonify(_req().atribuir_motorista(g.perfil, requisicao_id, corpo))


@api.get("/motoristas")
@requer_auth("autorizador", "admin")
def listar_motoristas() -> Any:
    return jsonify(ServicoMotorista(deps().repo, _req()).listar_motoristas())


@api.get("/motorista/viagens")
@requer_auth("motorista")
def listar_viagens_motorista() -> Any:
    resultado = ServicoMotorista(deps().repo, _req()).listar_viagens(
        g.perfil,
        status=request.args.get("status"),
        limite=_inteiro("limite", 50),
        offset=_inteiro("offset", 0),
        arquivadas=_booleano("arquivadas", False),
    )
    return jsonify(resultado)


@api.post("/motorista/viagens/<requisicao_id>/arquivar")
@requer_auth("motorista")
@limiter.limit(_limite_escrita)
def arquivar_viagem_motorista(requisicao_id: str) -> Any:
    return jsonify(_req().arquivar(g.perfil, requisicao_id))


@api.post("/motorista/viagens/<requisicao_id>/desarquivar")
@requer_auth("motorista")
@limiter.limit(_limite_escrita)
def desarquivar_viagem_motorista(requisicao_id: str) -> Any:
    return jsonify(_req().desarquivar(g.perfil, requisicao_id))


@api.get("/motorista/viagens/<requisicao_id>")
@requer_auth("motorista")
def obter_viagem_motorista(requisicao_id: str) -> Any:
    return jsonify(ServicoMotorista(deps().repo, _req()).obter_viagem(g.perfil, requisicao_id))


@api.post("/requisicoes/<requisicao_id>/cancelar")
@requer_auth()  # solicitante cancela a própria; o service decide pelo dono e pelo estado
@limiter.limit(_limite_escrita)
def cancelar(requisicao_id: str) -> Any:
    return _acao("cancelar", requisicao_id)


# -- Checklist (recurso opcional; regra em services/checklist.py) ------------------------
def _limite_upload() -> str:
    return deps().config.limite_upload_fotos


def _limite_upload_ip() -> str:
    return deps().config.limite_upload_fotos_ip


def _limite_leitura_fotos() -> str:
    return deps().config.limite_leitura_fotos


def _limite_leitura_fotos_ip() -> str:
    return deps().config.limite_leitura_fotos_ip


# Rotas de fotos: limite por usuário autenticado (chave `usuario:<id>`) + limite por IP como
# segunda camada. A autenticação (`requer_auth`) é o decorator mais externo, então `g.perfil`
# já existe quando o limite decorado é avaliado.
@api.post("/requisicoes/<requisicao_id>/checklist/<momento>/fotos")
@requer_auth("autorizador", "admin", "motorista", "vigilante")
@limiter.limit(_limite_upload_ip)
@limiter.limit(_limite_upload, key_func=chave_usuario)
def solicitar_upload_foto(requisicao_id: str, momento: str) -> Any:
    corpo = request.get_json(silent=True)
    return jsonify(_checklist().solicitar_upload(g.perfil, requisicao_id, momento, corpo)), 201


def _limite_registro() -> str:
    return deps().config.limite_registro_checklist


# O registro baixa e recodifica até 6 fotos (CPU e memória, atrás do semáforo de sanitização):
# limite por usuário para um só não monopolizar as vagas; os demais recebem 503 se faltar vaga.
# O contador é em memória (flask-limiter memory://), por processo: a produção usa 1 worker.
@api.post("/requisicoes/<requisicao_id>/checklist/<momento>")
@requer_auth("autorizador", "admin", "motorista", "vigilante")
@limiter.limit(_limite_escrita)
@limiter.limit(_limite_registro, key_func=chave_usuario)
def registrar_checklist(requisicao_id: str, momento: str) -> Any:
    corpo = request.get_json(silent=True)
    return jsonify(_checklist().registrar(g.perfil, requisicao_id, momento, corpo)), 201


@api.get("/requisicoes/<requisicao_id>/checklist")
@requer_auth()  # o service decide: solicitante alheio 404, dono 403, staff vê
@limiter.limit(_limite_leitura_fotos_ip)
@limiter.limit(_limite_leitura_fotos, key_func=chave_usuario)
def obter_checklist(requisicao_id: str) -> Any:
    return jsonify(_checklist().listar(g.perfil, requisicao_id))
