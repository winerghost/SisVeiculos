"""Administração de usuários (somente admin; a rota já exige o role, o service reconfere).

A auditoria de role/ativo/unidade sai dos triggers do banco (migrations 7, 13 e 14) desde que
`alterado_por` seja o admin: aqui nada é duplicado. Só a troca de nome, que nenhum trigger
cobre, é registrada via `registrar_auditoria`. Cada escrita é uma transação única e curta.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from app.errors import (
    AutoAlteracaoProibida,
    NaoEncontrado,
    RequisicaoInvalida,
    SemPermissao,
    UltimoAdmin,
)
from app.modelos import Perfil
from app.repositorios.auth_admin import AuthAdmin, IndiceEmails
from app.repositorios.base import Repositorio
from app.services.perfis import rotulo_perfil
from app.services.validacao import validar_uuid
from app.services.validacao_admin import ROLES, validar_busca, validar_edicao_usuario

log = logging.getLogger(__name__)

# Teto de ids de e-mail na cláusula OR da busca (evita URL gigante no PostgREST).
MAX_IDS_BUSCA = 150

# Cache curto do índice id -> e-mail que alimenta a busca (a varredura do Auth é cara).
# Em memória do processo; guarda e-mails (PII) só aqui, nunca em log. Contas novas podem
# demorar até TTL_INDICE_S para aparecer na busca.
TTL_INDICE_S = 45.0


class _CacheIndice:
    def __init__(self) -> None:
        self._lock = threading.Lock()  # também evita várias varreduras simultâneas
        self._valor: IndiceEmails | None = None
        self._expira = 0.0

    def reiniciar(self) -> None:
        with self._lock:
            self._valor, self._expira = None, 0.0

    def obter(self, auth: AuthAdmin) -> IndiceEmails:
        with self._lock:
            agora = time.monotonic()
            if self._valor is None or agora >= self._expira:
                self._valor = auth.indice_emails()
                self._expira = agora + TTL_INDICE_S
            return self._valor


INDICE_EMAILS = _CacheIndice()

MSG_AUTO_PAPEL = "Você não pode alterar o seu próprio papel."
MSG_AUTO_DESATIVAR = "Você não pode desativar a si mesmo."
MSG_ULTIMO_ADMIN = "Deve existir ao menos um administrador ativo."


def exigir_admin(perfil: Perfil) -> None:
    """Defesa em profundidade: a rota já filtra, mas o service nunca presume."""
    if perfil.role != "admin" or not perfil.ativo:
        raise SemPermissao("Você não tem permissão para esta ação.")


def parse_ativo(valor: str | None) -> bool | None:
    if valor is None or valor == "":
        return None
    if valor.lower() in {"true", "1"}:
        return True
    if valor.lower() in {"false", "0"}:
        return False
    raise RequisicaoInvalida("O parâmetro 'ativo' deve ser true ou false.")


def limitar(limite: int, offset: int) -> tuple[int, int]:
    return max(1, min(limite, 100)), max(0, offset)


class ServicoUsuarios:
    def __init__(self, repo: Repositorio, auth: AuthAdmin) -> None:
        self._repo = repo
        self._auth = auth

    # -- apresentação ------------------------------------------------------------------
    def acoes_permitidas(
        self, admin: Perfil, alvo: dict[str, Any], admins_ativos: int
    ) -> list[str]:
        """Ações que o admin logado pode exibir para este usuário (o banco reforça tudo)."""
        eu = alvo["id"] == admin.id
        ultimo = alvo["role"] == "admin" and alvo["ativo"] and admins_ativos <= 1
        acoes = ["editar"]
        if not eu and not ultimo:
            acoes.append("alterar_role")
        if alvo["ativo"]:
            if not eu and not ultimo:
                acoes.append("desativar")
        else:
            acoes.append("ativar")
            # O ban no Auth pode ter falhado (503) depois de o perfil ficar inativo:
            # "desativar" é idempotente e refaz o bloqueio, então segue disponível.
            if not eu:
                acoes.append("desativar")
        return acoes

    def _apresentar(
        self,
        admin: Perfil,
        alvos: list[dict[str, Any]],
        emails: dict[str, str],
        admins_ativos: int,
    ) -> list[dict[str, Any]]:
        return [
            {
                **a,
                "email": emails.get(a["id"]),
                "rotulo_perfil": rotulo_perfil(
                    Perfil(a["id"], a["nome"], a["unidade"], a["role"], a["ativo"])
                ),
                "acoes_permitidas": self.acoes_permitidas(admin, a, admins_ativos),
            }
            for a in alvos
        ]

    # -- leitura -----------------------------------------------------------------------
    def listar(
        self,
        admin: Perfil,
        *,
        role: str | None,
        ativo: str | None,
        q: str | None = None,
        limite: int,
        offset: int,
    ) -> dict[str, Any]:
        exigir_admin(admin)
        if role is not None and role not in ROLES:
            raise RequisicaoInvalida("Role inválido.")
        filtro_ativo = parse_ativo(ativo)
        busca = validar_busca(q)  # o termo nunca é logado
        limite, offset = limitar(limite, offset)
        ids_email, parcial = self._ids_por_email(busca)
        itens = self._repo.listar_perfis(
            role=role,
            ativo=filtro_ativo,
            busca=busca,
            ids_email=ids_email,
            limite=limite,
            offset=offset,
        )
        total = self._repo.contar_perfis(
            role=role, ativo=filtro_ativo, busca=busca, ids_email=ids_email
        )
        emails = self._auth.emails_por_id({i["id"] for i in itens})
        return {
            "itens": self._apresentar(admin, itens, emails, self._repo.contar_admins_ativos()),
            "total": total,
            # True quando a busca por e-mail não cobriu todas as contas (varredura truncada
            # ou mais de MAX_IDS_BUSCA achados): o front avisa para refinar o termo.
            "busca_parcial": parcial,
            # Ações de coleção: o front exibe "Convidar" por aqui, sem olhar permissões.
            "acoes_permitidas": ["criar"],
        }

    def _ids_por_email(self, busca: str | None) -> tuple[frozenset[str], bool]:
        """E-mail só existe no Auth: a busca por e-mail resolve ids no índice (cacheado,
        varredura limitada, só quando há `q`) e o banco une com a busca por nome."""
        if busca is None:
            return frozenset(), False
        indice = INDICE_EMAILS.obter(self._auth)
        termo = busca.lower()
        ids = sorted(i for i, email in indice.emails.items() if termo in email)
        parcial = indice.truncado
        if len(ids) > MAX_IDS_BUSCA:
            log.warning("busca_email_truncada achados=%d limite=%d", len(ids), MAX_IDS_BUSCA)
            ids, parcial = ids[:MAX_IDS_BUSCA], True
        return frozenset(ids), parcial

    def obter(self, admin: Perfil, usuario_id: str) -> dict[str, Any]:
        exigir_admin(admin)
        alvo = self._carregar(usuario_id)
        emails = self._auth.emails_por_id({alvo["id"]})
        return self._apresentar(admin, [alvo], emails, self._repo.contar_admins_ativos())[0]

    def _carregar(self, usuario_id: str) -> dict[str, Any]:
        alvo = self._repo.obter_perfil_admin(validar_uuid(usuario_id))
        if alvo is None:
            raise NaoEncontrado("Usuário não encontrado.")
        return alvo

    # -- escrita -----------------------------------------------------------------------
    def _checar_ultimo_admin(self, alvo: dict[str, Any]) -> None:
        if alvo["role"] == "admin" and alvo["ativo"] and self._repo.contar_admins_ativos() <= 1:
            raise UltimoAdmin(MSG_ULTIMO_ADMIN)

    def alterar(self, admin: Perfil, usuario_id: str, corpo: Any) -> dict[str, Any]:
        exigir_admin(admin)
        campos = validar_edicao_usuario(corpo)
        alvo = self._carregar(usuario_id)
        mudou = {k: v for k, v in campos.items() if alvo[k] != v}
        if "role" in mudou:
            if alvo["id"] == admin.id:
                raise AutoAlteracaoProibida(MSG_AUTO_PAPEL)
            self._checar_ultimo_admin(alvo)
        if mudou:
            atualizado = self._repo.atualizar_perfil(alvo["id"], mudou, ator_id=admin.id)
            if atualizado is None:
                raise NaoEncontrado("Usuário não encontrado.")
            alvo = atualizado
            if "nome" in mudou:  # nenhum trigger audita o nome; sem o valor (LGPD)
                self._repo.registrar_auditoria(
                    tabela="perfis",
                    registro_id=alvo["id"],
                    ator_id=admin.id,
                    detalhes={"campos": ["nome"]},
                )
        return self.obter(admin, alvo["id"])

    def desativar(self, admin: Perfil, usuario_id: str) -> dict[str, Any]:
        """Perfil primeiro, bloqueio no Auth depois: se o 2º passo falhar a conta já está
        inativa (a API recusa), e repetir a chamada refaz o bloqueio."""
        exigir_admin(admin)
        alvo = self._carregar(usuario_id)
        if alvo["id"] == admin.id:
            raise AutoAlteracaoProibida(MSG_AUTO_DESATIVAR)
        if alvo["ativo"]:
            self._checar_ultimo_admin(alvo)
            if self._repo.atualizar_perfil(alvo["id"], {"ativo": False}, ator_id=admin.id) is None:
                raise NaoEncontrado("Usuário não encontrado.")
        self._auth.bloquear(alvo["id"])
        return self.obter(admin, alvo["id"])

    def ativar(self, admin: Perfil, usuario_id: str) -> dict[str, Any]:
        """Desbloqueia no Auth primeiro e só então ativa o perfil: falha no meio deixa a
        conta inativa (lado seguro)."""
        exigir_admin(admin)
        alvo = self._carregar(usuario_id)
        self._auth.desbloquear(alvo["id"])
        reativado = (
            self._repo.atualizar_perfil(alvo["id"], {"ativo": True}, ator_id=admin.id)
            if not alvo["ativo"]
            else alvo
        )
        if reativado is None:
            raise NaoEncontrado("Usuário não encontrado.")
        return self.obter(admin, alvo["id"])
