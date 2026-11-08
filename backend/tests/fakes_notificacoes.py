"""Fake das RPCs de notificações (migration 20261110120000), sobre o RepoMemoria.

Reproduz o contrato: o dono vem sempre do parâmetro `usuario` (AG029 para alheia/inexistente,
AG030 para parâmetro inválido) e a reserva de e-mails move `pendente` -> `enviando`.
"""

from __future__ import annotations

import re
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.fakes_admin import erro_db

_CODIGO = re.compile(r"^[a-z0-9_.-]{1,40}$")
_ERRO = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
_CHAVES_ITEM = (
    "id",
    "tipo",
    "requisicao_id",
    "referencia_id",
    "codigo",
    "critica",
    "criada_em",
    "lida_em",
)


class NotificacoesMemoria:
    """Mixin do RepoMemoria."""

    def _init_notificacoes(self) -> None:
        self.notificacoes: list[dict[str, Any]] = []
        self.chamadas_sistema: list[tuple[str, str]] = []
        self.falha_sistema: Exception | None = None  # injeta falha em registrar_notificacao_sistema
        self.falha_reserva: Exception | None = None
        self._lock_notif = threading.Lock()

    def nova_notificacao(
        self,
        usuario: str,
        tipo: str = "requisicao_chegada",
        *,
        requisicao_id: str | None = None,
        codigo: str | None = None,
        critica: bool = False,
        lida: bool = False,
        email_status: str | None = None,
        minutos_atras: int = 0,
    ) -> dict[str, Any]:
        n = {
            "id": str(uuid.uuid4()),
            "usuario_id": usuario,
            "tipo": tipo,
            "requisicao_id": requisicao_id,
            "referencia_id": None,
            "codigo": codigo,
            "critica": critica,
            "criada_em": datetime.now(UTC) - timedelta(minutes=minutos_atras),
            "lida_em": datetime.now(UTC) if lida else None,
            "email_status": email_status,
            "email_erro_codigo": None,
            "email_tentativas": 0,
        }
        self.notificacoes.append(n)
        return n

    # -- leitura -----------------------------------------------------------------------------
    def listar_notificacoes(
        self, usuario: str, *, limite: int, offset: int, somente_nao_lidas: bool
    ) -> dict[str, Any]:
        if offset > 100000:
            raise erro_db("AG030")
        limite = min(max(limite, 1), 100)
        offset = max(offset, 0)
        minhas = sorted(
            (n for n in self.notificacoes if n["usuario_id"] == usuario),
            key=lambda n: (n["criada_em"], n["id"]),
            reverse=True,
        )
        filtradas = [n for n in minhas if not somente_nao_lidas or n["lida_em"] is None]
        itens = [
            {k: (n[k].isoformat() if isinstance(n[k], datetime) else n[k]) for k in _CHAVES_ITEM}
            for n in filtradas[offset : offset + limite]
        ]
        return {
            "total": len(filtradas),
            "nao_lidas": sum(1 for n in minhas if n["lida_em"] is None),
            "limite": limite,
            "offset": offset,
            "itens": itens,
        }

    def contar_notificacoes_nao_lidas(self, usuario: str) -> int:
        return sum(1 for n in self.notificacoes if n["usuario_id"] == usuario and not n["lida_em"])

    def marcar_notificacao_lida(self, usuario: str, notificacao_id: str) -> dict[str, Any]:
        with self._lock_notif:
            n = next(
                (
                    x
                    for x in self.notificacoes
                    if x["id"] == notificacao_id and x["usuario_id"] == usuario
                ),
                None,
            )
            if n is None:
                raise erro_db("AG029")
            if n["lida_em"] is None:
                n["lida_em"] = datetime.now(UTC)
            return {"id": n["id"], "lida_em": n["lida_em"].isoformat()}

    def marcar_todas_lidas(self, usuario: str) -> int:
        with self._lock_notif:
            alvo = [n for n in self.notificacoes if n["usuario_id"] == usuario and not n["lida_em"]]
            for n in alvo:
                n["lida_em"] = datetime.now(UTC)
            return len(alvo)

    # -- sistema e e-mail ----------------------------------------------------------------------
    def registrar_notificacao_sistema(self, tipo: str, detalhe_codigo: str) -> int:
        self.chamadas_sistema.append((tipo, detalhe_codigo))
        if self.falha_sistema is not None:
            raise self.falha_sistema
        if tipo != "sistema_falha_envio" or not _CODIGO.fullmatch(detalhe_codigo or ""):
            raise erro_db("AG030")
        admins = [p for p in self.perfis.values() if p.role == "admin" and p.ativo]
        for p in admins:
            self.nova_notificacao(
                p.id,
                "sistema_falha_envio",
                codigo=detalhe_codigo,
                critica=True,
                email_status="pendente" if self.config.notificacao_email_criticos else None,
            )
        return len(admins)

    def reservar_emails_notificacao(self, limite: int) -> list[dict[str, Any]]:
        if self.falha_reserva is not None:
            raise self.falha_reserva
        with self._lock_notif:
            if not self.config.notificacao_email_criticos:
                for n in self.notificacoes:
                    if n["email_status"] == "pendente":
                        n["email_status"], n["email_erro_codigo"] = "ignorado", "inelegivel"
                return []
            lote = [n for n in self.notificacoes if n["email_status"] == "pendente"][:limite]
            for n in lote:
                n["email_status"] = "enviando"
                n["email_tentativas"] += 1
            return [
                {
                    "id": n["id"],
                    "usuario_id": n["usuario_id"],
                    "tipo": n["tipo"],
                    "requisicao_id": n["requisicao_id"],
                    "referencia_id": n["referencia_id"],
                    "codigo": n["codigo"],
                }
                for n in lote
            ]

    def marcar_email_notificacao(
        self, notificacao_id: str, status: str, erro_codigo: str | None = None
    ) -> None:
        with self._lock_notif:
            if status not in {"enviado", "falhou", "ignorado"}:
                raise erro_db("AG030")
            if erro_codigo is not None and not _ERRO.fullmatch(erro_codigo):
                raise erro_db("AG030")
            n = next((x for x in self.notificacoes if x["id"] == notificacao_id), None)
            if n is None or n["email_status"] is None:
                raise erro_db("AG029")
            if n["email_status"] != "enviando":
                raise erro_db("AG001")
            n["email_status"] = status
            n["email_erro_codigo"] = (
                (erro_codigo or "desconhecido") if status == "falhou" else erro_codigo
            )

    def purgar_notificacoes(self, dias: int = 90) -> int:
        if dias < 30 or dias > 3650:
            raise erro_db("AG030")
        corte = datetime.now(UTC) - timedelta(days=dias)
        antes = len(self.notificacoes)
        self.notificacoes = [n for n in self.notificacoes if n["criada_em"] >= corte]
        return antes - len(self.notificacoes)
