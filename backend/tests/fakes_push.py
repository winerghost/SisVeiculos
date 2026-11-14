"""Fake das inscrições de Web Push (espelha as RPCs: um endpoint pertence a um usuário)."""

from __future__ import annotations

import threading
import uuid
from typing import Any


class PushMemoria:
    def _init_push(self) -> None:
        self._lock_push = threading.Lock()
        self.inscricoes_push: dict[str, dict[str, Any]] = {}  # endpoint -> linha

    def registrar_inscricao_push(
        self, usuario_id: str, endpoint: str, p256dh: str, auth: str
    ) -> None:
        with self._lock_push:
            atual = self.inscricoes_push.get(endpoint)
            self.inscricoes_push[endpoint] = {
                "id": atual["id"] if atual else str(uuid.uuid4()),
                "usuario_id": usuario_id,
                "endpoint": endpoint,
                "p256dh": p256dh,
                "auth": auth,
                "revogada": False,
            }

    def revogar_inscricao_push(self, usuario_id: str, endpoint: str) -> None:
        with self._lock_push:
            linha = self.inscricoes_push.get(endpoint)
            if linha and linha["usuario_id"] == usuario_id:
                linha["revogada"] = True

    def listar_inscricoes_push(self, usuario_id: str) -> list[dict[str, Any]]:
        with self._lock_push:
            return [
                dict(i)
                for i in self.inscricoes_push.values()
                if i["usuario_id"] == usuario_id and not i["revogada"]
            ]

    def marcar_resultado_inscricao_push(self, inscricao_id: str, ok: bool, expirada: bool) -> None:
        return None

    def reservar_push_notificacao(self, limite: int) -> list[dict[str, Any]]:
        return []

    def marcar_push_notificacao(
        self, notificacao_id: str, status: str, codigo: str | None = None
    ) -> None:
        return None

    def purgar_inscricoes_push(self) -> int:
        return 0
