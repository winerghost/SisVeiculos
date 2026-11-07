"""Notificações externas (n8n). Best-effort, assíncronas e sem dados pessoais."""

from app.notificacoes.n8n import (
    EVENTOS,
    N8nWebhook,
    Notificador,
    NotificadorNulo,
    criar_notificador,
    validar_url_webhook,
)

__all__ = [
    "EVENTOS",
    "N8nWebhook",
    "Notificador",
    "NotificadorNulo",
    "criar_notificador",
    "validar_url_webhook",
]
