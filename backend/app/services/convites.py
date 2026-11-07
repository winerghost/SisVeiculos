"""Convites de conta (tabela `convites`, migration 6): criar, reenviar, revogar e aceitar.

O token em claro só existe aqui, por instantes: vai no link do e-mail (query `convite` da
página de aceite) e o banco guarda apenas o SHA-256. Auditoria de criação/revogação/uso sai
dos triggers do banco. E-mail nunca é logado nem devolvido a quem não é admin.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from app.errors import (
    ContaExistente,
    ConviteAbertoExistente,
    ConviteEncerrado,
    ConviteInvalido,
    ErroApi,
    NaoAutenticado,
    NaoEncontrado,
    NomeObrigatorio,
    RequisicaoInvalida,
)
from app.modelos import Configuracoes, Perfil
from app.repositorios.auth_admin import AuthAdmin
from app.repositorios.base import Repositorio
from app.services.admin_usuarios import exigir_admin, limitar
from app.services.perfis import montar_eu
from app.services.validacao import validar_texto, validar_uuid
from app.services.validacao_admin import (
    MAX_NOME,
    VALIDADE_PADRAO_DIAS,
    validar_aceite,
    validar_convite,
)

ESTADOS = ("aberto", "expirado", "usado", "revogado")


COOLDOWN_REENVIO_S = 60  # um convite recém-emitido não é reenviado antes disso
MAX_REENVIOS_POR_DESTINO = 5  # por e-mail, em JANELA_REENVIO_S (além do limite por IP da rota)
JANELA_REENVIO_S = 3600


class _Controle:
    """Estado em memória do processo (como o storage do rate limit): limite por destino e
    reenvios em andamento. A chave do destino é o hash do e-mail (nada de PII em memória
    longa nem em log). Com vários workers cada processo tem o seu; o cooldown por `criado_em`
    (vem do banco) continua valendo entre processos."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._envios: dict[str, list[float]] = {}
        self._em_andamento: set[str] = set()

    def reiniciar(self) -> None:
        with self._lock:
            self._envios.clear()
            self._em_andamento.clear()

    def reservar(self, convite_id: str, email: str) -> None:
        """Marca o convite como em reenvio e conta o envio para o destino, ou recusa."""
        chave = hashlib.sha256(email.encode()).hexdigest()
        agora = time.monotonic()
        with self._lock:
            if convite_id in self._em_andamento:
                raise ErroApi(
                    "Já existe um reenvio em andamento para este convite. Aguarde um instante.",
                    "reenvio_em_andamento",
                    409,
                )
            recentes = [t for t in self._envios.get(chave, []) if agora - t < JANELA_REENVIO_S]
            if len(recentes) >= MAX_REENVIOS_POR_DESTINO:
                self._envios[chave] = recentes
                raise ErroApi(
                    "Muitos reenvios para este e-mail. Tente novamente mais tarde.",
                    "limite_excedido",
                    429,
                )
            self._envios[chave] = [*recentes, agora]
            self._em_andamento.add(convite_id)

    def liberar(self, convite_id: str) -> None:
        with self._lock:
            self._em_andamento.discard(convite_id)


CONTROLE = _Controle()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def estado_convite(c: dict[str, Any], agora: datetime) -> str:
    if c.get("usado_em"):
        return "usado"
    if c.get("revogado_em"):
        return "revogado"
    return "expirado" if datetime.fromisoformat(c["expira_em"]) <= agora else "aberto"


class ServicoConvites:
    def __init__(self, repo: Repositorio, auth: AuthAdmin, *, url_aceite: str) -> None:
        self._repo = repo
        self._auth = auth
        self._url = url_aceite

    # -- apresentação ------------------------------------------------------------------
    def _apresentar(self, c: dict[str, Any]) -> dict[str, Any]:
        estado = estado_convite(c, datetime.now(UTC))
        return {
            "id": c["id"],
            "email": c["email"],
            "role": c["role"],
            "unidade": c["unidade"],
            "estado": estado,
            "criado_em": c["criado_em"],
            "expira_em": c["expira_em"],
            "usado_em": c.get("usado_em"),
            "revogado_em": c.get("revogado_em"),
            "acoes_permitidas": ["reenviar", "revogar"] if estado in {"aberto", "expirado"} else [],
        }

    def _carregar(self, convite_id: str) -> dict[str, Any]:
        c = self._repo.obter_convite(validar_uuid(convite_id))
        if c is None:
            raise NaoEncontrado("Convite não encontrado.")
        return c

    # -- leitura -----------------------------------------------------------------------
    def listar(
        self, admin: Perfil, *, estado: str | None, limite: int, offset: int
    ) -> dict[str, Any]:
        exigir_admin(admin)
        if estado is not None and estado not in ESTADOS:
            raise RequisicaoInvalida("Estado inválido. Use aberto, expirado, usado ou revogado.")
        limite, offset = limitar(limite, offset)
        agora = datetime.now(UTC).isoformat()
        itens = self._repo.listar_convites(estado=estado, agora=agora, limite=limite, offset=offset)
        return {
            "itens": [self._apresentar(c) for c in itens],
            "total": self._repo.contar_convites(estado=estado, agora=agora),
        }

    # -- escrita (admin) ---------------------------------------------------------------
    def _exigir_configurado(self) -> None:
        if not self._url:
            raise ErroApi(
                "Convites ainda não configurados no servidor. Avise a equipe técnica.",
                "convites_nao_configurados",
                503,
            )

    def _entregar(self, email: str, nome: str | None, token: str) -> None:
        """Entrega o link do convite pelo Auth. O link sempre leva `?convite=<token>`.

        Convidado novo (ou ainda não confirmado): e-mail de convite do Supabase. Conta já
        confirmada: só se ainda NÃO tiver perfil (ficou travada, ex.: convite anterior revogado
        depois de ela definir a senha) o acesso é reenviado pelo canal de recuperação de senha
        com o mesmo redirect. Com perfil, é `conta_existente` (409): não há o que convidar.

        Nota (B3): o nome do convidado vive em `user_metadata.nome` do Auth, não na tabela
        `convites`. Gravá-lo na coluna do convite exigiria uma migration (db-supabase).
        """
        redirect = f"{self._url}?{urlencode({'convite': token})}"
        try:
            self._auth.convidar(email, nome=nome, redirect_to=redirect)
        except ContaExistente:
            conta_id = self._auth.id_por_email(email)
            if conta_id is None or self._repo.obter_perfil_admin(conta_id) is not None:
                raise
            self._auth.reenviar_acesso(email, redirect_to=redirect)

    def _emitir(
        self,
        admin: Perfil,
        *,
        email: str,
        role: str,
        unidade: str,
        dias: int,
        nome: str | None,
        substitui: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Cria o convite novo e entrega o link.

        Sem `substitui` (criação): grava e entrega; se a entrega falhar, revoga o recém-criado
        para não bloquear novo convite ao mesmo e-mail (índice de convite aberto).

        Com `substitui` (reenvio): o índice único impede dois convites abertos para o e-mail,
        então a ordem é entregar primeiro, depois revogar o antigo e gravar o novo. Se a
        entrega falhar nada mudou e o convite antigo continua válido. Janela residual: se a
        gravação falhar depois da revogação, o link entregue não vale e o admin cria um
        convite novo (o e-mail está livre). Um atomic revoga+insere exigiria RPC (migration).
        """
        token = secrets.token_urlsafe(32)
        dados = {
            "email": email,
            "role": role,
            "unidade": unidade,
            "token_hash": hash_token(token),
            "criado_por": admin.id,
            "expira_em": (datetime.now(UTC) + timedelta(days=dias)).isoformat(),
        }
        if substitui is not None:
            self._entregar(email, nome, token)
            if self._repo.revogar_convite(substitui["id"], ator_id=admin.id) is None:
                raise ConviteEncerrado("Este convite já foi utilizado ou revogado.")
            return self._repo.inserir_convite(dados)
        convite = self._repo.inserir_convite(dados)
        try:
            self._entregar(email, nome, token)
        except Exception:
            with suppress(ErroApi):  # a falha original é a que importa ao admin
                self._repo.revogar_convite(convite["id"], ator_id=admin.id)
            raise
        return convite

    def criar(self, admin: Perfil, corpo: Any) -> dict[str, Any]:
        exigir_admin(admin)
        self._exigir_configurado()
        d = validar_convite(corpo)
        if self._repo.obter_convite_aberto_por_email(d["email"]) is not None:
            raise ConviteAbertoExistente(
                "Já existe um convite em aberto para este e-mail. "
                "Reenvie ou revogue o convite atual antes de criar outro."
            )
        convite = self._emitir(
            admin,
            email=d["email"],
            role=d["role"],
            unidade=d["unidade"],
            dias=d["validade_dias"],
            nome=d["nome"],
        )
        return self._apresentar(convite)

    def reenviar(self, admin: Perfil, convite_id: str) -> dict[str, Any]:
        """Emite um novo convite (mesmo e-mail, papel e unidade) e só então revoga o atual
        (token antigo deixa de valer). Se a entrega falhar, o antigo continua válido.
        Transações curtas e separadas, como pede a migration 15."""
        exigir_admin(admin)
        self._exigir_configurado()
        antigo = self._exigir_reenviavel(self._carregar(convite_id))
        CONTROLE.reservar(antigo["id"], antigo["email"])
        try:
            # Relê sob a reserva: um reenvio que terminou entre a 1ª leitura e a reserva já
            # revogou este convite, e o e-mail não pode sair de novo.
            antigo = self._exigir_reenviavel(self._carregar(convite_id))
            novo = self._emitir(
                admin,
                email=antigo["email"],
                role=antigo["role"],
                unidade=antigo["unidade"],
                dias=VALIDADE_PADRAO_DIAS,
                nome=None,  # o nome já está no cadastro do Auth; não sobrescrever
                substitui=antigo,
            )
        finally:
            CONTROLE.liberar(antigo["id"])
        return self._apresentar(novo)

    def _exigir_reenviavel(self, c: dict[str, Any]) -> dict[str, Any]:
        if estado_convite(c, datetime.now(UTC)) not in {"aberto", "expirado"}:
            raise ConviteEncerrado("Este convite já foi utilizado ou revogado.")
        decorrido = (datetime.now(UTC) - datetime.fromisoformat(c["criado_em"])).total_seconds()
        if decorrido < COOLDOWN_REENVIO_S:
            raise ErroApi(
                "Este convite foi enviado há pouco. Aguarde 1 minuto para reenviar.",
                "reenvio_muito_recente",
                429,
            )
        return c

    def revogar(self, admin: Perfil, convite_id: str) -> dict[str, Any]:
        exigir_admin(admin)
        c = self._carregar(convite_id)
        if estado_convite(c, datetime.now(UTC)) in {"usado", "revogado"}:
            raise ConviteEncerrado("Este convite já foi utilizado ou revogado.")
        revogado = self._repo.revogar_convite(c["id"], ator_id=admin.id)
        if revogado is None:  # outra pessoa usou/revogou entre a leitura e a escrita
            raise ConviteEncerrado("Este convite já foi utilizado ou revogado.")
        return self._apresentar(revogado)

    # -- aceite (o próprio convidado, ainda sem perfil) -------------------------------
    def aceitar(self, usuario_id: str, corpo: Any, config: Configuracoes) -> dict[str, Any]:
        """O JWT já foi validado pela rota; `usuario_id` vem dele, nunca do corpo. Role e unidade
        vêm do convite no banco: nenhum campo de papel é aceito do cliente."""
        token, nome_reserva = validar_aceite(corpo)
        conta = self._auth.obter_conta(usuario_id)
        if conta is None:
            raise NaoAutenticado("Sessão inválida ou expirada. Faça login novamente.")
        nome = _nome_valido(conta.nome) or nome_reserva
        if not nome:
            raise NomeObrigatorio(
                "O convite não traz o seu nome. Informe o campo 'nome' para concluir o cadastro."
            )
        perfil = self._repo.consumir_convite(hash_token(token), usuario_id, nome)
        # Convite usado/expirado/de outro e-mail: o banco recusa (AG005/AG006/AG007, traduzidos).
        if perfil is None:  # pragma: no cover
            raise ConviteInvalido("Convite inválido.")
        return montar_eu(perfil, config)


def _nome_valido(nome: str | None) -> str | None:
    if not nome:
        return None
    try:
        return validar_texto({"nome": nome}, "nome", MAX_NOME, False)
    except RequisicaoInvalida:
        return None
