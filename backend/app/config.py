"""Configuração do backend, lida de variáveis de ambiente (nunca de arquivo versionado)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def _bool(valor: str | None, padrao: bool = False) -> bool:
    if valor is None or valor.strip() == "":
        return padrao
    return valor.strip().lower() in {"1", "true", "sim", "yes"}


def _lista(valor: str | None) -> list[str]:
    return [item.strip() for item in (valor or "").split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    """Valores de execução. `from_env` nunca registra nem imprime os segredos."""

    supabase_url: str = ""
    supabase_service_role_key: str = field(default="", repr=False)
    jwks_url: str = ""
    jwt_issuer: str = ""
    jwt_audience: str = "authenticated"
    cors_origins: list[str] = field(default_factory=list)
    max_content_length: int = 16 * 1024
    limite_padrao: str = "120 per minute"
    limite_escrita: str = "20 per minute"
    # Fotos do checklist: o limite principal é por usuário autenticado (perfil.id); o limite por
    # IP é a segunda camada e é mais folgado porque vários servidores podem compartilhar o IP.
    limite_upload_fotos: str = "30 per minute"  # por usuário: URLs assinadas de upload
    limite_leitura_fotos: str = "30 per minute"  # por usuário: GET do checklist (URLs assinadas)
    limite_upload_fotos_ip: str = "90 per minute"
    limite_leitura_fotos_ip: str = "90 per minute"
    # Teto de URLs de upload emitidas por requisição/momento (vale por ~2 h, o prazo do token).
    max_urls_upload_por_momento: int = 12
    # Foto de perfil: upload-url e confirmação (recodificação usa CPU) limitados por usuário e IP;
    # a cota de emissões de URL de upload vale por hora (LimiteEmissao).
    limite_foto_perfil: str = "10 per minute"
    limite_foto_perfil_ip: str = "30 per minute"
    max_emissoes_foto_perfil: int = 10
    limite_busca_usuarios: str = "30 per minute"  # GET /api/admin/usuarios com `q` (varredura)
    # Decisão pendente do usuário: autorizador pode autorizar a própria requisição?
    permitir_autoautorizacao: bool = False
    testing: bool = False
    # Página do frontend que recebe o convite (o token vai na query `convite`). Vazio = convites
    # desativados: o admin não consegue convidar até a variável ser configurada.
    url_aceite_convite: str = ""
    # n8n (só notifica). Vazios = integração desligada. A URL vem só do ambiente.
    n8n_webhook_url: str = ""
    n8n_webhook_token: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            supabase_url=os.environ.get("SUPABASE_URL", ""),
            supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            jwks_url=os.environ.get("SUPABASE_JWKS_URL", ""),
            jwt_issuer=os.environ.get("SUPABASE_JWT_ISSUER", ""),
            jwt_audience=os.environ.get("SUPABASE_JWT_AUDIENCE") or "authenticated",
            cors_origins=_lista(os.environ.get("CORS_ORIGINS")),
            permitir_autoautorizacao=_bool(os.environ.get("PERMITIR_AUTOAUTORIZACAO")),
            url_aceite_convite=os.environ.get("URL_ACEITE_CONVITE", "").strip(),
            n8n_webhook_url=os.environ.get("N8N_WEBHOOK_URL", "").strip(),
            n8n_webhook_token=os.environ.get("N8N_WEBHOOK_TOKEN", "").strip(),
        )

    def validar_producao(self) -> None:
        """Falha cedo se faltar configuração obrigatória (sem revelar valores)."""
        faltando = [
            nome
            for nome, valor in {
                "SUPABASE_URL": self.supabase_url,
                "SUPABASE_SERVICE_ROLE_KEY": self.supabase_service_role_key,
                "SUPABASE_JWKS_URL": self.jwks_url,
                "SUPABASE_JWT_ISSUER": self.jwt_issuer,
            }.items()
            if not valor
        ]
        if faltando:
            raise RuntimeError("Variáveis de ambiente ausentes: " + ", ".join(faltando))

        # Só nomes de variáveis nas mensagens, nunca valores.
        sem_https = [
            nome
            for nome, valor in {
                "SUPABASE_URL": self.supabase_url,
                "SUPABASE_JWKS_URL": self.jwks_url,
                "SUPABASE_JWT_ISSUER": self.jwt_issuer,
            }.items()
            if not valor.lower().startswith("https://")
        ]
        if sem_https:
            raise RuntimeError("Estas variáveis devem usar https://: " + ", ".join(sem_https))
        host_jwks = urlsplit(self.jwks_url).hostname
        host_issuer = urlsplit(self.jwt_issuer).hostname
        if not host_jwks or host_jwks != host_issuer:
            raise RuntimeError("SUPABASE_JWKS_URL e SUPABASE_JWT_ISSUER devem ter o mesmo host.")
        if self.url_aceite_convite and not self.url_aceite_convite.lower().startswith("https://"):
            raise RuntimeError("URL_ACEITE_CONVITE deve usar https://.")
        if self.n8n_webhook_url:
            from app.notificacoes.n8n import validar_url_webhook

            validar_url_webhook(self.n8n_webhook_url)
        if any(o.strip() == "*" for o in self.cors_origins):
            raise RuntimeError("CORS_ORIGINS não pode conter '*'.")
