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


def _inteiro(valor: str | None, padrao: int, minimo: int, maximo: int) -> int:
    """Inteiro do ambiente, limitado a [minimo, maximo]; vazio ou inválido = padrão."""
    try:
        n = int((valor or "").strip())
    except ValueError:
        return padrao
    return max(minimo, min(maximo, n))


SEGURANCAS_EMAIL = ("starttls", "ssl", "none")


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
    limite_dashboards: str = "60 per minute"  # GET /api/dashboards*: RPCs agregadas, por usuário
    limite_busca_usuarios: str = "30 per minute"  # GET /api/admin/usuarios com `q` (varredura)
    # Decisão pendente do usuário: autorizador pode autorizar a própria requisição?
    permitir_autoautorizacao: bool = False
    # Relatórios: teto de linhas por relatório (o banco aceita até 5001 por consulta: limite + 1).
    relatorio_max_linhas: int = 5000
    limite_relatorios: str = "60 per minute"  # prévia/catálogo/destinatários/consulta de envio
    limite_relatorios_arquivo: str = "10 per minute"  # geração de CSV/XLSX (CPU e memória)
    limite_relatorios_email: str = "5 per minute"  # envio por e-mail (além da cota por hora)
    # E-mail (SMTP) dos relatórios. Desligado sem EMAIL_ENABLED=true. Credenciais só no ambiente.
    email_enabled: bool = False
    email_host: str = ""
    email_port: int = 587
    email_user: str = ""
    email_password: str = field(default="", repr=False)
    email_security: str = "starttls"  # starttls | ssl | none
    email_from: str = ""
    email_from_name: str = ""
    email_timeout_seconds: int = 15
    email_max_per_hour: int = 30  # cota global por hora (o banco aceita de 1 a 1000)
    email_max_per_user_hour: int = 5  # cota por usuário por hora (todos os perfis)
    # Notificações: leitura/escrita por usuário; despachante de e-mail dos críticos (1 worker).
    limite_notificacoes: str = "120 per minute"  # GET /api/notificacoes*, por usuário
    limite_notificacoes_escrita: str = "60 per minute"  # POST .../lida e .../lidas, por usuário
    email_notif_intervalo_segundos: int = 30  # pausa entre ciclos do despachante
    email_notif_max_por_hora: int = 20  # teto de e-mails de notificação por hora (processo)
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
            email_enabled=_bool(os.environ.get("EMAIL_ENABLED")),
            email_host=os.environ.get("EMAIL_HOST", "").strip(),
            email_port=_inteiro(os.environ.get("EMAIL_PORT"), 587, 1, 65535),
            email_user=os.environ.get("EMAIL_USER", "").strip(),
            email_password=os.environ.get("EMAIL_PASSWORD", ""),
            email_security=(os.environ.get("EMAIL_SECURITY") or "starttls").strip().lower(),
            email_from=os.environ.get("EMAIL_FROM", "").strip(),
            email_from_name=os.environ.get("EMAIL_FROM_NAME", "").strip(),
            email_timeout_seconds=_inteiro(os.environ.get("EMAIL_TIMEOUT_SECONDS"), 15, 1, 120),
            email_max_per_hour=_inteiro(os.environ.get("EMAIL_MAX_PER_HOUR"), 30, 1, 1000),
            email_max_per_user_hour=_inteiro(os.environ.get("EMAIL_MAX_PER_USER_HOUR"), 5, 1, 1000),
            email_notif_intervalo_segundos=_inteiro(
                os.environ.get("EMAIL_NOTIF_INTERVALO_SEGUNDOS"), 30, 5, 3600
            ),
            email_notif_max_por_hora=_inteiro(
                os.environ.get("EMAIL_NOTIF_MAX_POR_HORA"), 20, 1, 1000
            ),
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
        if self.email_enabled:
            if self.email_security not in SEGURANCAS_EMAIL:
                raise RuntimeError("EMAIL_SECURITY deve ser starttls, ssl ou none.")
            if self.email_security == "none" and (self.email_user or self.email_password):
                raise RuntimeError(
                    "EMAIL_SECURITY=none não pode ser usado com EMAIL_USER/EMAIL_PASSWORD: "
                    "a credencial trafegaria sem TLS."
                )
            ausentes = [
                nome
                for nome, valor in {
                    "EMAIL_HOST": self.email_host,
                    "EMAIL_FROM": self.email_from,
                }.items()
                if not valor
            ]
            if ausentes:
                raise RuntimeError("Variáveis de e-mail ausentes: " + ", ".join(ausentes))
        if any(o.strip() == "*" for o in self.cors_origins):
            raise RuntimeError("CORS_ORIGINS não pode conter '*'.")
