"""Guarda técnica dos testes que GRAVAM no Supabase (contrato e integração).

Estes testes criam usuários, veículos e requisições e não apagam nada (as migrations proíbem
DELETE físico e a auditoria é append-only). Por isso só rodam contra um Supabase LOCAL e
DESCARTÁVEL (`supabase start` / `supabase db reset`). A execução só é liberada quando TODAS as
condições valem; senão o teste é PULADO com o motivo (que nunca inclui URL nem chave):

  1. a URL e a service role key estão definidas;
  2. CONTRATO_CONFIRM=descartavel (segunda chave, sempre exigida);
  3. o host da URL é exatamente 127.0.0.1 ou localhost (sem subdomínios, sem credenciais
     embutidas na URL, esquema http/https) E o DNS resolve apenas para endereços de loopback
     (um /etc/hosts ou DNS desviado para outro IP é recusado).

Risco residual: um túnel ou port-forward local para um projeto real passa na condição 3; só a
confirmação explícita (2) protege. Nunca defina CONTRATO_CONFIRM em ambiente com banco real.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import pytest

CONFIRMACAO_ESPERADA = "descartavel"
HOSTS_LOCAIS = frozenset({"127.0.0.1", "localhost"})


@dataclass(frozen=True)
class Liberacao:
    """Resultado da avaliação. `motivo` é None quando liberado."""

    motivo: str | None
    url: str = ""
    chave: str = field(default="", repr=False)

    @property
    def liberado(self) -> bool:
        return self.motivo is None


def host_local(url: str) -> bool:
    """True só para http(s) com host local explícito e sem credenciais na URL."""
    try:
        partes = urlsplit(url)
        host = partes.hostname
        _ = partes.port  # valida a porta (levanta ValueError se inválida)
    except ValueError:
        return False
    if partes.scheme not in {"http", "https"}:
        return False
    if partes.username is not None or partes.password is not None:
        return False
    if host not in HOSTS_LOCAIS:
        return False
    return _resolve_so_loopback(host)


def _resolve_so_loopback(host: str) -> bool:
    """True só se TODOS os IPs do host resolvem para loopback (falha fechada)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    if not infos:
        return False
    try:
        return all(ipaddress.ip_address(i[4][0]).is_loopback for i in infos)
    except ValueError:
        return False


def avaliar(var_url: str, var_chave: str, env: Mapping[str, str] | None = None) -> Liberacao:
    """Avalia as condições usando as variáveis `var_url` e `var_chave` do ambiente."""
    amb = os.environ if env is None else env
    url = amb.get(var_url, "").strip()
    chave = amb.get(var_chave, "").strip()
    if not url or not chave:
        return Liberacao(f"{var_url} e {var_chave} não definidas")
    if amb.get("CONTRATO_CONFIRM") != CONFIRMACAO_ESPERADA:
        return Liberacao(
            "CONTRATO_CONFIRM=descartavel não definida: confirme que o Supabase é LOCAL e "
            "DESCARTÁVEL (os testes gravam e não apagam)"
        )
    if not host_local(url):
        return Liberacao(
            f"o host de {var_url} não é local explícito (127.0.0.1 ou localhost, "
            "resolvendo só para loopback): recusado para não gravar em projeto real"
        )
    return Liberacao(None, url, chave)


def marca_pular(liberacao: Liberacao) -> pytest.MarkDecorator:
    """Marker `skipif` com o motivo da guarda (aplicar em `pytestmark`)."""
    return pytest.mark.skipif(not liberacao.liberado, reason=liberacao.motivo or "")
