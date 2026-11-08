"""Minimização (LGPD) dos dados do histórico de acessos, feita no backend antes de gravar.

Funções puras: IP mascarado (IPv4 /24, IPv6 /48), user-agent reduzido a `Família/Sistema` de
listas FECHADAS e método de autenticação normalizado. O IP completo e o user-agent bruto nunca
saem daqui, nem para log.
"""

from __future__ import annotations

import ipaddress
from typing import Any

PREFIXO_V4 = 24
PREFIXO_V6 = 48

FAMILIAS = frozenset(
    {"Chrome", "Firefox", "Safari", "Edge", "Opera", "Samsung Internet", "Mobile Safari", "Outro"}
)
SISTEMAS = frozenset({"Windows", "macOS", "Linux", "Android", "iOS", "ChromeOS", "Desconhecido"})
PADRAO_UA = "Outro/Desconhecido"
METODOS = frozenset({"password", "totp"})
_MAX_UA = 512  # só o começo do cabeçalho é examinado


def mascarar_ip(ip: str | None) -> str | None:
    """CIDR mascarado ('203.0.113.0/24', '2001:db8:1::/48') ou None se ausente/inválido.

    IPv4 mapeado em IPv6 (::ffff:a.b.c.d) vira IPv4 antes de mascarar. Nunca devolve o IP
    completo.
    """
    if not isinstance(ip, str) or not ip.strip() or len(ip) > 64:
        return None
    try:
        endereco = ipaddress.ip_address(ip.strip())
    except ValueError:
        return None
    if isinstance(endereco, ipaddress.IPv6Address) and endereco.ipv4_mapped is not None:
        endereco = endereco.ipv4_mapped
    if isinstance(endereco, ipaddress.IPv4Address):
        return str(ipaddress.IPv4Network((int(endereco), PREFIXO_V4), strict=False))
    return str(ipaddress.IPv6Network((int(endereco), PREFIXO_V6), strict=False))


def _sistema(ua: str) -> str:
    if "Windows" in ua:
        return "Windows"
    if "iPhone" in ua or "iPad" in ua or "iPod" in ua:  # antes de macOS ("like Mac OS X")
        return "iOS"
    if "Android" in ua:  # antes de Linux
        return "Android"
    if "CrOS" in ua:
        return "ChromeOS"
    if "Macintosh" in ua or "Mac OS X" in ua:
        return "macOS"
    if "Linux" in ua or "X11" in ua:
        return "Linux"
    return "Desconhecido"


def _familia(ua: str, sistema: str) -> str:
    # Ordem importa: Edge/Opera/Samsung incluem "Chrome" no UA; Chrome inclui "Safari".
    if any(t in ua for t in ("Edg/", "EdgA/", "EdgiOS/", "Edge/")):
        return "Edge"
    if "OPR/" in ua or "Opera" in ua or "OPT/" in ua:
        return "Opera"
    if "SamsungBrowser" in ua:
        return "Samsung Internet"
    if sistema == "iOS":  # no iOS todo navegador usa o WebKit da Apple
        return "Mobile Safari" if any(t in ua for t in ("CriOS", "FxiOS", "Safari/")) else "Outro"
    if "Firefox/" in ua:
        return "Firefox"
    if "Chrome/" in ua or "CriOS" in ua:
        return "Chrome"
    if "Safari/" in ua and "Version/" in ua:
        return "Safari"
    return "Outro"


def reduzir_user_agent(ua: str | None) -> str:
    """`Família/Sistema` de listas fechadas; qualquer coisa fora delas vira 'Outro/Desconhecido'."""
    if not isinstance(ua, str) or not ua.strip():
        return PADRAO_UA
    texto = ua[:_MAX_UA]
    sistema = _sistema(texto)
    familia = _familia(texto, sistema)
    if familia not in FAMILIAS or sistema not in SISTEMAS:  # defesa em profundidade
        return PADRAO_UA
    return f"{familia}/{sistema}"


def metodo_autenticacao(amr: Any) -> str:
    """Último método de `amr` (Supabase: lista de {method, timestamp}) em
    'password' | 'totp' | 'outro'."""
    if not isinstance(amr, list) or not amr:
        return "outro"
    ultimo: tuple[float, int, str] | None = None
    for posicao, item in enumerate(amr):
        metodo = item.get("method") if isinstance(item, dict) else item
        carimbo = item.get("timestamp") if isinstance(item, dict) else None
        ordem = (
            (
                float(carimbo)
                if isinstance(carimbo, int | float) and not isinstance(carimbo, bool)
                else float("-inf")
            ),
            posicao,
            metodo if isinstance(metodo, str) else "",
        )
        if ultimo is None or ordem[:2] >= ultimo[:2]:
            ultimo = ordem
    if ultimo is None:
        return "outro"
    return ultimo[2] if ultimo[2] in METODOS else "outro"
