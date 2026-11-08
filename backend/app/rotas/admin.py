"""Rotas de administração sob /api/admin. Só traduzem HTTP <-> services.

Todas exigem role `admin` (lido de `perfis`, nunca do cliente). A checagem fica num
`before_request` do blueprint: uma rota nova não consegue esquecê-la.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify, request

from app.auth.decoradores import autenticar, tem_segundo_fator
from app.errors import ErroApi, RequisicaoInvalida, SemPermissao
from app.extensions import chave_usuario, deps, limiter
from app.services.acessos import ServicoAcessos
from app.services.admin_configuracoes import ServicoConfiguracoes
from app.services.admin_ia import ServicoIAAdmin
from app.services.admin_usuarios import ServicoUsuarios
from app.services.admin_veiculos import ServicoVeiculosAdmin
from app.services.convites import ServicoConvites
from app.services.foto_perfil import ServicoFotoPerfil
from app.services.fotos_exibicao import FotosExibicao
from app.services.localizacoes_admin import ServicoLocalizacoes

admin = Blueprint("admin", __name__, url_prefix="/api/admin")


@admin.before_request
def _exigir_admin() -> None:
    if request.method == "OPTIONS":  # preflight do CORS não leva Authorization
        return
    perfil = autenticar()
    if perfil.role != "admin":
        raise SemPermissao("Você não tem permissão para esta ação.")


def _limite_escrita() -> str:
    return deps().config.limite_escrita


def _limite_busca() -> str:
    return deps().config.limite_busca_usuarios


def _sem_busca() -> bool:
    return not (request.args.get("q") or "").strip()


def _auth_admin() -> Any:
    a = deps().auth_admin
    if a is None:
        raise ErroApi("Administração de contas indisponível.", "indisponivel", 503)
    return a


def _usuarios() -> ServicoUsuarios:
    d = deps()
    return ServicoUsuarios(
        d.repo,
        _auth_admin(),
        fotos=ServicoFotoPerfil(
            d.repo, d.armazenamento_avatares, max_emissoes=d.config.max_emissoes_foto_perfil
        ),
        exibicao=FotosExibicao(d.repo, d.armazenamento_avatares),
    )


def _convites() -> ServicoConvites:
    return ServicoConvites(deps().repo, _auth_admin(), url_aceite=deps().config.url_aceite_convite)


def _inteiro(nome: str, padrao: int) -> int:
    bruto = request.args.get(nome)
    if bruto is None:
        return padrao
    try:
        return int(bruto)
    except ValueError:
        raise RequisicaoInvalida(f"O parâmetro '{nome}' deve ser um número inteiro.") from None


def _corpo() -> Any:
    return request.get_json(silent=True)


# ------------------------------------------------------------------------- usuários
@admin.get("/usuarios")
@limiter.limit(_limite_busca, exempt_when=_sem_busca)  # só a busca dispara a varredura do Auth
def listar_usuarios() -> Any:
    return jsonify(
        _usuarios().listar(
            g.perfil,
            role=request.args.get("role"),
            ativo=request.args.get("ativo"),
            q=request.args.get("q"),
            limite=_inteiro("limite", 50),
            offset=_inteiro("offset", 0),
        )
    )


@admin.get("/usuarios/<usuario_id>")
def obter_usuario(usuario_id: str) -> Any:
    return jsonify(_usuarios().obter(g.perfil, usuario_id))


@admin.post("/usuarios")
@limiter.limit(_limite_escrita)
def convidar_usuario() -> Any:
    """Convida por e-mail: cria o convite (uso único, com validade) e dispara o e-mail."""
    return jsonify({"convite": _convites().criar(g.perfil, _corpo())}), 201


@admin.patch("/usuarios/<usuario_id>")
@limiter.limit(_limite_escrita)
def alterar_usuario(usuario_id: str) -> Any:
    return jsonify(_usuarios().alterar(g.perfil, usuario_id, _corpo()))


@admin.post("/usuarios/<usuario_id>/ativar")
@limiter.limit(_limite_escrita)
def ativar_usuario(usuario_id: str) -> Any:
    return jsonify(_usuarios().ativar(g.perfil, usuario_id))


@admin.post("/usuarios/<usuario_id>/desativar")
@limiter.limit(_limite_escrita)
def desativar_usuario(usuario_id: str) -> Any:
    return jsonify(_usuarios().desativar(g.perfil, usuario_id))


def _limite_foto() -> str:
    return deps().config.limite_foto_perfil


def _limite_foto_ip() -> str:
    return deps().config.limite_foto_perfil_ip


@admin.post("/usuarios/<usuario_id>/foto/upload-url")
@limiter.limit(_limite_foto_ip)
@limiter.limit(_limite_foto, key_func=chave_usuario)
def solicitar_upload_foto_usuario(usuario_id: str) -> Any:
    """URL assinada para o admin enviar a foto de outra conta (staging sob o id do admin)."""
    return jsonify(_usuarios().solicitar_upload_foto(g.perfil, usuario_id, _corpo())), 201


@admin.post("/usuarios/<usuario_id>/foto")
@limiter.limit(_limite_foto_ip)
@limiter.limit(_limite_foto, key_func=chave_usuario)
def trocar_foto_usuario(usuario_id: str) -> Any:
    """Confirma o upload e troca a foto da conta (ator = admin do JWT; auditado pelo banco)."""
    return jsonify(_usuarios().trocar_foto(g.perfil, usuario_id, _corpo()))


@admin.delete("/usuarios/<usuario_id>/foto")
@limiter.limit(_limite_escrita)
def remover_foto_usuario(usuario_id: str) -> Any:
    """Remove a foto de perfil de um usuário (ator = admin do JWT; auditado pelo banco)."""
    return jsonify(_usuarios().remover_foto(g.perfil, usuario_id))


# ------------------------------------------------------------------------- convites
@admin.get("/convites")
def listar_convites() -> Any:
    return jsonify(
        _convites().listar(
            g.perfil,
            estado=request.args.get("estado"),
            limite=_inteiro("limite", 50),
            offset=_inteiro("offset", 0),
        )
    )


@admin.post("/convites/<convite_id>/reenviar")
@limiter.limit(_limite_escrita)
def reenviar_convite(convite_id: str) -> Any:
    return jsonify({"convite": _convites().reenviar(g.perfil, convite_id)})


@admin.post("/convites/<convite_id>/revogar")
@limiter.limit(_limite_escrita)
def revogar_convite(convite_id: str) -> Any:
    return jsonify({"convite": _convites().revogar(g.perfil, convite_id)})


# ------------------------------------------------------------------------- veículos
def _veiculos() -> ServicoVeiculosAdmin:
    return ServicoVeiculosAdmin(deps().repo)


@admin.get("/veiculos")
def listar_veiculos() -> Any:
    return jsonify(_veiculos().listar(g.perfil, ativo=request.args.get("ativo")))


@admin.get("/veiculos/<veiculo_id>")
def obter_veiculo(veiculo_id: str) -> Any:
    return jsonify(_veiculos().obter(g.perfil, veiculo_id))


@admin.post("/veiculos")
@limiter.limit(_limite_escrita)
def criar_veiculo() -> Any:
    return jsonify(_veiculos().criar(g.perfil, _corpo())), 201


@admin.patch("/veiculos/<veiculo_id>")
@limiter.limit(_limite_escrita)
def editar_veiculo(veiculo_id: str) -> Any:
    return jsonify(_veiculos().editar(g.perfil, veiculo_id, _corpo()))


@admin.post("/veiculos/<veiculo_id>/ativar")
@limiter.limit(_limite_escrita)
def ativar_veiculo(veiculo_id: str) -> Any:
    return jsonify(_veiculos().ativar(g.perfil, veiculo_id))


@admin.post("/veiculos/<veiculo_id>/desativar")
@limiter.limit(_limite_escrita)
def desativar_veiculo(veiculo_id: str) -> Any:
    return jsonify(_veiculos().desativar(g.perfil, veiculo_id))


# ------------------------------------------------------------------------- acessos
@admin.get("/acessos")
@limiter.limit(_limite_busca)
def listar_acessos() -> Any:
    """Histórico de logins (só admin; consulta auditada). Filtros validados no service."""
    return jsonify(ServicoAcessos(deps().repo).listar(g.perfil, request.args.to_dict(flat=False)))


# ------------------------------------------------------------------------- localizações
@admin.get("/requisicoes/<requisicao_id>/localizacoes")
@limiter.limit(_limite_busca)
def listar_localizacoes(requisicao_id: str) -> Any:
    """Coordenadas de saída/chegada do motorista (dado sensível: só admin; leitura auditada)."""
    return jsonify(ServicoLocalizacoes(deps().repo).listar(g.perfil, requisicao_id))


# ------------------------------------------------------------------------- configurações
@admin.get("/configuracoes")
def obter_configuracoes() -> Any:
    return jsonify(
        ServicoConfiguracoes(deps().repo).obter(g.perfil, segundo_fator=tem_segundo_fator(g.claims))
    )


@admin.put("/configuracoes")
@limiter.limit(_limite_escrita)
def atualizar_configuracoes() -> Any:
    return jsonify(
        ServicoConfiguracoes(deps().repo).atualizar(
            g.perfil, _corpo(), segundo_fator=tem_segundo_fator(g.claims)
        )
    )


# ------------------------------------------------------------------------- IA (configurações)
def _ia_admin() -> ServicoIAAdmin:
    ia = deps().ia
    if ia is None:
        raise ErroApi("Configuração de IA indisponível.", "indisponivel", 503)
    return ServicoIAAdmin(ia)


@admin.get("/ia")
def obter_ia() -> Any:
    return jsonify(_ia_admin().obter(g.perfil))


@admin.post("/ia/testar")
@limiter.limit("5 per minute", key_func=chave_usuario)
def testar_ia() -> Any:
    corpo = _corpo()
    if corpo is None and request.get_data():  # corpo presente, mas não é JSON válido
        raise RequisicaoInvalida("O corpo deve ser um objeto JSON.")
    return jsonify(_ia_admin().testar(g.perfil, corpo))


@admin.put("/ia")
@limiter.limit(_limite_escrita)
def atualizar_ia() -> Any:
    return jsonify(_ia_admin().atualizar(g.perfil, _corpo()))
