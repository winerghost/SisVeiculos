"""Configuração do backend, lida de variáveis de ambiente (nunca de arquivo versionado)."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import os
import re
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


# Proxies de que o backend aceita o CF-Connecting-IP: a sub-rede fixa do docker-compose.yml (onde
# está o nginx do frontend; mesma de set_real_ip_from em frontend/nginx.conf) e o loopback.
PROXIES_CONFIAVEIS_PADRAO = ["10.213.88.0/24", "127.0.0.1/32", "::1/128"]
SEGURANCAS_EMAIL = ("starttls", "ssl", "none")
N8N_HMAC_MIN_CARACTERES = 32
# Prefixo mínimo aceito em TRUSTED_PROXY_CIDRS (redes mais largas confiariam em hosts demais).
PREFIXO_MINIMO_PROXY = {4: 16, 6: 48}
# Serviços de push conhecidos (anti-SSRF: o servidor faz POST no endpoint da inscrição). Itens
# iniciados por "." valem como sufixo de domínio; os demais, como host exato.
PUSH_HOSTS_PADRAO = [
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
    ".push.apple.com",
    ".notify.windows.com",
]
# Provedores públicos: qualquer pessoa abre conta, então não são "institucionais".
DOMINIOS_PUBLICOS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "outlook.com",
        "yahoo.com",
        "yahoo.com.br",
        "live.com",
        "icloud.com",
        "me.com",
        "msn.com",
        "uol.com.br",
        "bol.com.br",
        "terra.com.br",
        "proton.me",
        "protonmail.com",
    }
)
# Nome de domínio (DNS) com ao menos um ponto; sem curinga, esquema ou caminho.
RE_DOMINIO = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _b64url_bytes(valor: str, tamanho: int) -> bytes | None:
    try:
        bruto = base64.urlsafe_b64decode(valor.rstrip("=") + "=" * (-len(valor.rstrip("=")) % 4))
    except (binascii.Error, ValueError):
        return None
    return bruto if len(bruto) == tamanho else None


def vapid_valido(publica: str, privada: str, subject: str) -> bool:
    """Par VAPID P-256 (pública 65 bytes não comprimida, privada 32 bytes) e subject
    `mailto:`/`https://`. Só devolve bool: nunca ecoa valores."""
    if not (publica and privada and subject):
        return False
    pub = _b64url_bytes(publica, 65)
    if pub is None or pub[0] != 4 or _b64url_bytes(privada, 32) is None:
        return False
    s = subject.strip()
    corpo = (
        s[7:]
        if s.lower().startswith("mailto:")
        else s[8:] if s.lower().startswith("https://") else ""
    )
    return bool(corpo) and not any(c.isspace() for c in s)


def normalizar_origem_webauthn(origem: str) -> str | None:
    """Origem canônica (`esquema://host[:porta]`, minúsculas, sem porta padrão) ou None se inválida.

    Só `https`; `http` apenas para `localhost` (desenvolvimento). Sem credenciais, caminho,
    query nem fragmento. Nunca ecoa o valor."""
    try:
        partes = urlsplit(origem.strip())
        host = (partes.hostname or "").lower()
        porta = partes.port
    except ValueError:
        return None
    if (
        not host
        or partes.username is not None
        or partes.password is not None
        or partes.path not in ("", "/")
        or partes.query
        or partes.fragment
        or origem.strip().endswith("?")
        or origem.strip().endswith("#")
    ):
        return None
    esquema = partes.scheme.lower()
    if esquema == "http" and host == "localhost":
        padrao = 80
    elif esquema == "https":
        padrao = 443
    else:
        return None
    if host != "localhost" and not RE_DOMINIO.fullmatch(host):
        return None
    sufixo = f":{porta}" if porta not in (None, padrao) else ""
    return f"{esquema}://{host}{sufixo}"


def webauthn_valido(rp_id: str, rp_name: str, origens: list[str]) -> bool:
    """RP ID (domínio, sem esquema/porta/caminho) e nome presentes, ao menos uma origem e todas
    coerentes: https (http só em localhost) e host igual ao RP ID ou subdomínio dele."""
    if not (rp_id and rp_name.strip() and origens):
        return False
    if rp_id != rp_id.lower() or (rp_id != "localhost" and not RE_DOMINIO.fullmatch(rp_id)):
        return False
    for origem in origens:
        norma = normalizar_origem_webauthn(origem)
        if norma is None:
            return False
        host = urlsplit(norma).hostname or ""
        if host != rp_id and not host.endswith("." + rp_id):
            return False
    return True


def validar_dominios_relatorio(dominios: list[str]) -> None:
    """Falha cedo (sem ecoar valores) se EMAIL_DOMINIOS_RELATORIO tiver item perigoso."""
    for d in dominios:
        if "*" in d or "?" in d:
            raise RuntimeError("EMAIL_DOMINIOS_RELATORIO não aceita curingas.")
        if not RE_DOMINIO.fullmatch(d):
            raise RuntimeError("EMAIL_DOMINIOS_RELATORIO contém um domínio em formato inválido.")
        if d in DOMINIOS_PUBLICOS:
            raise RuntimeError(
                "EMAIL_DOMINIOS_RELATORIO não pode listar provedores públicos de e-mail."
            )


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
    limite_registro_checklist: str = "10 per minute"  # por usuário: registro (sanitiza fotos)
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
    # Web Push (VAPID). A chave privada só vem do ambiente e nunca aparece em repr/log.
    vapid_public_key: str = ""
    vapid_private_key: str = field(default="", repr=False)
    vapid_subject: str = ""
    push_endpoint_hosts: list[str] = field(default_factory=lambda: list(PUSH_HOSTS_PADRAO))
    push_intervalo_segundos: int = 10  # pausa entre ciclos do despachante de push
    limite_push: str = "10 per minute"  # POST/DELETE /api/push/inscricoes, por usuário
    # Passkeys (WebAuthn). Sem as três variáveis coerentes o recurso fica indisponível mesmo ligado.
    # O RP ID não pode mudar sem invalidar as passkeys já cadastradas. Nada aqui é segredo.
    webauthn_rp_id: str = ""
    webauthn_rp_name: str = ""
    webauthn_origins: list[str] = field(default_factory=list)
    limite_passkeys: str = "10 per minute"  # opções/confirmação/revogação, por usuário
    testing: bool = False
    # Página do frontend que recebe o convite (o token vai na query `convite`). Vazio = convites
    # desativados: o admin não consegue convidar até a variável ser configurada.
    url_aceite_convite: str = ""
    # n8n (só notifica). Vazios = integração desligada. A URL vem só do ambiente.
    n8n_webhook_url: str = ""
    n8n_webhook_token: str = field(default="", repr=False)
    # Chave da assinatura HMAC (opcional). Vazia = a assinatura usa n8n_webhook_token.
    n8n_hmac_secret: str = field(default="", repr=False)
    # Allowlist opcional de hosts do webhook (vazia = qualquer host público).
    n8n_allowed_hosts: list[str] = field(default_factory=list)
    # Redes (CIDR) dos proxies cujo CF-Connecting-IP é confiável (ver extensions.ip_cliente).
    trusted_proxy_cidrs: list[str] = field(default_factory=lambda: list(PROXIES_CONFIAVEIS_PADRAO))
    # Domínios (minúsculas, comparação exata) que podem receber relatório com dado pessoal.
    # Vazio = nenhum destinatário é institucional (falha fechada).
    email_dominios_relatorio: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            supabase_url=os.environ.get("SUPABASE_URL", ""),
            supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            jwks_url=os.environ.get("SUPABASE_JWKS_URL", ""),
            jwt_issuer=os.environ.get("SUPABASE_JWT_ISSUER", ""),
            jwt_audience=os.environ.get("SUPABASE_JWT_AUDIENCE") or "authenticated",
            cors_origins=_lista(os.environ.get("CORS_ORIGINS")),
            url_aceite_convite=os.environ.get("URL_ACEITE_CONVITE", "").strip(),
            n8n_webhook_url=os.environ.get("N8N_WEBHOOK_URL", "").strip(),
            n8n_webhook_token=os.environ.get("N8N_WEBHOOK_TOKEN", "").strip(),
            n8n_hmac_secret=os.environ.get("N8N_HMAC_SECRET", "").strip(),
            n8n_allowed_hosts=[h.lower() for h in _lista(os.environ.get("N8N_ALLOWED_HOSTS"))],
            trusted_proxy_cidrs=_lista(os.environ.get("TRUSTED_PROXY_CIDRS"))
            or list(PROXIES_CONFIAVEIS_PADRAO),
            vapid_public_key=os.environ.get("VAPID_PUBLIC_KEY", "").strip(),
            vapid_private_key=os.environ.get("VAPID_PRIVATE_KEY", "").strip(),
            vapid_subject=os.environ.get("VAPID_SUBJECT", "").strip(),
            push_endpoint_hosts=[h.lower() for h in _lista(os.environ.get("PUSH_ENDPOINT_HOSTS"))]
            or list(PUSH_HOSTS_PADRAO),
            push_intervalo_segundos=_inteiro(
                os.environ.get("PUSH_INTERVALO_SEGUNDOS"), 10, 1, 3600
            ),
            webauthn_rp_id=os.environ.get("WEBAUTHN_RP_ID", "").strip().lower(),
            webauthn_rp_name=os.environ.get("WEBAUTHN_RP_NAME", "").strip(),
            webauthn_origins=_lista(os.environ.get("WEBAUTHN_ORIGINS")),
            email_dominios_relatorio=[
                d.lstrip("@").lower() for d in _lista(os.environ.get("EMAIL_DOMINIOS_RELATORIO"))
            ],
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

    def vapid_valido(self) -> bool:
        """Chaves e subject completos e bem formados (sem eco de valores)."""
        return vapid_valido(self.vapid_public_key, self.vapid_private_key, self.vapid_subject)

    def webauthn_configurado(self) -> bool:
        """As três variáveis WEBAUTHN_* presentes e coerentes (sem eco de valores)."""
        return webauthn_valido(self.webauthn_rp_id, self.webauthn_rp_name, self.webauthn_origins)

    def origens_webauthn(self) -> list[str]:
        """Origens canônicas aceitas (a comparação com o clientDataJSON é por igualdade)."""
        norm = (normalizar_origem_webauthn(o) for o in self.webauthn_origins)
        return [o for o in norm if o]

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
        for cidr in self.trusted_proxy_cidrs:
            try:
                rede = ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                raise RuntimeError("TRUSTED_PROXY_CIDRS contém um CIDR inválido.") from None
            minimo = PREFIXO_MINIMO_PROXY[rede.version]
            if rede.prefixlen < minimo or not rede.is_private:
                raise RuntimeError(
                    "TRUSTED_PROXY_CIDRS aceita só redes privadas e estreitas "
                    f"(prefixo mínimo /{PREFIXO_MINIMO_PROXY[4]} em IPv4 e "
                    f"/{PREFIXO_MINIMO_PROXY[6]} em IPv6): liste a sub-rede do proxy."
                )
        validar_dominios_relatorio(self.email_dominios_relatorio)
        if self.n8n_allowed_hosts:
            from app.notificacoes.n8n import host_valido

            if not all(host_valido(h.strip().lower()) for h in self.n8n_allowed_hosts):
                raise RuntimeError("N8N_ALLOWED_HOSTS deve listar só nomes de host (sem esquema).")
        if self.n8n_webhook_url:
            from app.notificacoes.n8n import validar_url_webhook

            validar_url_webhook(self.n8n_webhook_url, self.n8n_allowed_hosts)
        if self.n8n_webhook_url or self.n8n_webhook_token:  # integração ligada
            if not self.n8n_hmac_secret:
                raise RuntimeError("N8N_HMAC_SECRET é obrigatório quando o n8n está ligado.")
            if self.n8n_hmac_secret == self.n8n_webhook_token:
                raise RuntimeError("N8N_HMAC_SECRET deve ser distinto de N8N_WEBHOOK_TOKEN.")
        if self.n8n_hmac_secret and len(self.n8n_hmac_secret) < N8N_HMAC_MIN_CARACTERES:
            raise RuntimeError(
                f"N8N_HMAC_SECRET deve ter ao menos {N8N_HMAC_MIN_CARACTERES} caracteres."
            )
        if self.email_enabled:
            if self.email_security not in SEGURANCAS_EMAIL:
                raise RuntimeError("EMAIL_SECURITY deve ser starttls, ssl ou none.")
            if self.email_security == "none":
                raise RuntimeError(
                    "EMAIL_SECURITY=none não é aceito em produção: use starttls ou ssl."
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
        for h in self.push_endpoint_hosts:
            rotulos = h.lstrip(".").split(".")
            if len(rotulos) < 2 or not all(rotulos) or "*" in h or "/" in h or ":" in h:
                raise RuntimeError(
                    "PUSH_ENDPOINT_HOSTS deve listar nomes de host ou sufixos com 2+ rótulos."
                )
        if (self.vapid_public_key or self.vapid_private_key or self.vapid_subject) and (
            not self.vapid_valido()
        ):
            raise RuntimeError(
                "Configuração VAPID inválida: confira VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY "
                "(base64url, gerados por scripts/gerar-vapid.py) e VAPID_SUBJECT "
                "(mailto: ou https://)."
            )
        if (self.webauthn_rp_id or self.webauthn_rp_name or self.webauthn_origins) and (
            not self.webauthn_configurado()
        ):
            raise RuntimeError(
                "Configuração WebAuthn inválida: defina WEBAUTHN_RP_ID (domínio, sem esquema), "
                "WEBAUTHN_RP_NAME e WEBAUTHN_ORIGINS (https://, host igual ou subdomínio do RP ID)."
            )
        # localhost e http só valem fora de produção (validar_producao não roda em dev/teste).
        origens_norm = self.origens_webauthn()
        if self.webauthn_rp_id == "localhost" or any(
            o.startswith("http://") or urlsplit(o).hostname == "localhost" for o in origens_norm
        ):
            raise RuntimeError(
                "Configuração WebAuthn inválida em produção: WEBAUTHN_RP_ID não pode ser "
                "localhost e WEBAUTHN_ORIGINS deve usar https:// com domínio real."
            )
        if any(o.strip() == "*" for o in self.cors_origins):
            raise RuntimeError("CORS_ORIGINS não pode conter '*'.")
