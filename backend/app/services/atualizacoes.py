"""Atualização em tempo real (Fase 1, polling por versão). Regras aqui; a rota só traduz HTTP.

`GET /api/atualizacoes` devolve, por recurso, um valor OPACO que muda quando algo relevante PARA
AQUELE PERFIL muda. A relevância (dono, atribuição, papel) é calculada no banco
(`atualizacoes_versao`, migration 20261202120000) e chega aqui como marcadores crus; antes de sair,
cada marcador vira HMAC-SHA256("<perfil_id>:<recurso>:<marcador>") truncado. Assim o cliente só
compara strings: nunca vê id de auditoria nem volume global (anti canal lateral), e o mesmo
estado dá valores diferentes para perfis diferentes.

Segredo do HMAC: derivado da `SUPABASE_SERVICE_ROLE_KEY` (sempre presente em produção, nunca sai
do backend) com rótulo próprio; sem ela (testes/dev), um segredo aleatório por processo. Trocar o
segredo (rotação da chave ou reinício sem ela) só faz cada cliente recarregar as telas uma vez.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Callable
from typing import Any

from app.modelos import Configuracoes, Perfil
from app.repositorios.base import Repositorio

RECURSOS_COMUNS = ("requisicoes", "notificacoes", "veiculos", "configuracoes")
RECURSO_ADMIN = "usuarios"
TAMANHO_VERSAO = 16  # hex (64 bits): suficiente para detectar mudança, curto no fio
ROTULO_CHAVE = b"sisveiculos/atualizacoes/v1"
TTL_CONFIGURACOES = 5.0  # segundos (cache só desta rota; ver CacheConfiguracoes)

_SEGREDO_PROCESSO = secrets.token_bytes(32)


def derivar_chave(service_role_key: str) -> bytes:
    if service_role_key:
        return hmac.new(service_role_key.encode("utf-8"), ROTULO_CHAVE, hashlib.sha256).digest()
    return _SEGREDO_PROCESSO


def _opaco(chave: bytes, perfil_id: str, recurso: str, marcador: str) -> str:
    msg = f"{perfil_id}:{recurso}:{marcador}".encode()
    return hmac.new(chave, msg, hashlib.sha256).hexdigest()[:TAMANHO_VERSAO]


def etag_de(corpo: dict[str, Any]) -> str:
    """ETag forte do corpo (JSON canônico). Já é derivado de valores opacos: não revela nada."""
    bruto = json.dumps(corpo, sort_keys=True, separators=(",", ":")).encode()
    return '"' + hashlib.sha256(bruto).hexdigest()[:32] + '"'


def etag_confere(if_none_match: str | None, etag: str) -> bool:
    """Comparação fraca (RFC 9110): aceita `W/` (proxy que comprime) e lista com vírgula."""
    if not if_none_match:
        return False
    alvo = etag.removeprefix("W/")
    for item in if_none_match.split(","):
        candidato = item.strip()
        if candidato == "*" or candidato.removeprefix("W/") == alvo:
            return True
    return False


class CacheConfiguracoes:
    """Cache curto (TTL) de `ler_configuracoes` só para o polling: a rota roda a cada 10 s por aba
    e a leitura só serve para decidir o MFA. Ligar o MFA vale para o polling em até TTL segundos;
    a resposta só tem valores opacos, então o atraso não expõe dado. Demais rotas leem sempre."""

    def __init__(
        self, ttl: float = TTL_CONFIGURACOES, relogio: Callable[[], float] = time.monotonic
    ):
        self._ttl = ttl
        self._relogio = relogio
        self._lock = threading.Lock()
        self._valor: Configuracoes | None = None
        self._expira = 0.0

    def ler(self, repo: Repositorio) -> Configuracoes:
        agora = self._relogio()
        with self._lock:
            if self._valor is not None and agora < self._expira:
                return self._valor
        valor = repo.ler_configuracoes()  # fora do lock: não serializa threads no banco
        with self._lock:
            self._valor, self._expira = valor, agora + self._ttl
        return valor


class ServicoAtualizacoes:
    def __init__(self, repo: Repositorio, chave: bytes) -> None:
        self._repo = repo
        self._chave = chave

    def versoes(self, perfil: Perfil) -> dict[str, Any]:
        """Uma chamada ao banco. O dono é sempre o perfil autenticado (nunca vem do cliente)."""
        crus = self._repo.versoes_atualizacao(perfil.id)
        recursos = {r: _opaco(self._chave, perfil.id, r, str(crus[r])) for r in RECURSOS_COMUNS}
        # `usuarios` só para admin: decidido pelo papel vindo do banco (g.perfil), não do cliente.
        if perfil.role == "admin":
            recursos[RECURSO_ADMIN] = _opaco(
                self._chave, perfil.id, RECURSO_ADMIN, str(crus.get(RECURSO_ADMIN) or "0")
            )
        return {"recursos": recursos, "nao_lidas": int(crus.get("nao_lidas") or 0)}
