"""Adaptadores dos provedores (OpenAI e Anthropic) sobre httpx.

- URLs base FIXAS no código (anti-SSRF): nada vem de entrada de usuário nem da tela.
- Sem redirecionamento. Timeout por chamada.
- Nenhum adaptador registra prompt, resposta, cabeçalhos ou mensagem de erro: falhas viram
  `ErroProvedor(tipo)` com um tipo fixo (sem texto do provedor, que poderia ecoar conteúdo).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

URL_OPENAI = "https://api.openai.com/v1/chat/completions"
URL_ANTHROPIC = "https://api.anthropic.com/v1/messages"
VERSAO_ANTHROPIC = "2023-06-01"
MAX_CHARS_RESPOSTA = 20_000  # teto defensivo antes de qualquer validação do chamador


class ErroProvedor(Exception):  # noqa: N818
    """Falha ao chamar o provedor. `tipo` é um de: timeout, rede, auth, limite, http, formato."""

    def __init__(self, tipo: str) -> None:
        super().__init__(tipo)
        self.tipo = tipo


@dataclass(frozen=True)
class Resultado:
    texto: str
    tokens_entrada: int
    tokens_saida: int


class Provedor(Protocol):
    nome: str

    def gerar(
        self, *, modelo: str, sistema: str, usuario: str, max_tokens: int, timeout: float
    ) -> Resultado:
        """Levanta ErroProvedor em qualquer falha. Nunca registra o conteúdo."""
        ...


def _inteiro(valor: Any) -> int:
    return valor if isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0 else 0


def _enviar(
    cliente: httpx.Client,
    url: str,
    cabecalhos: dict[str, str],
    corpo: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    try:
        resp = cliente.post(url, headers=cabecalhos, json=corpo, timeout=timeout)
    except httpx.TimeoutException:
        raise ErroProvedor("timeout") from None
    except httpx.HTTPError:
        raise ErroProvedor("rede") from None
    if resp.status_code in {401, 403}:
        raise ErroProvedor("auth")
    if resp.status_code == 429:
        raise ErroProvedor("limite")
    if resp.status_code >= 400:
        raise ErroProvedor("http")
    try:
        dados = resp.json()
    except ValueError:
        raise ErroProvedor("formato") from None
    if not isinstance(dados, dict):
        raise ErroProvedor("formato")
    return dados


def _texto_valido(texto: Any) -> str:
    if not isinstance(texto, str) or not texto.strip():
        raise ErroProvedor("formato")
    return texto[:MAX_CHARS_RESPOSTA]


class ProvedorOpenAI:
    nome = "openai"

    def __init__(self, chave: str, cliente: httpx.Client | None = None) -> None:
        self._chave = chave
        self._cliente = cliente or httpx.Client(follow_redirects=False, trust_env=False)

    def __repr__(self) -> str:  # nunca expõe a chave
        return "ProvedorOpenAI()"

    def gerar(
        self, *, modelo: str, sistema: str, usuario: str, max_tokens: int, timeout: float
    ) -> Resultado:
        dados = _enviar(
            self._cliente,
            URL_OPENAI,
            {"Authorization": f"Bearer {self._chave}"},
            {
                "model": modelo,
                "messages": [
                    {"role": "system", "content": sistema},
                    {"role": "user", "content": usuario},
                ],
                "max_completion_tokens": max_tokens,
            },
            timeout,
        )
        try:
            texto = dados["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ErroProvedor("formato") from None
        uso = dados.get("usage") if isinstance(dados.get("usage"), dict) else {}
        return Resultado(
            _texto_valido(texto),
            _inteiro(uso.get("prompt_tokens")),
            _inteiro(uso.get("completion_tokens")),
        )


class ProvedorAnthropic:
    nome = "anthropic"

    def __init__(self, chave: str, cliente: httpx.Client | None = None) -> None:
        self._chave = chave
        self._cliente = cliente or httpx.Client(follow_redirects=False, trust_env=False)

    def __repr__(self) -> str:  # nunca expõe a chave
        return "ProvedorAnthropic()"

    def gerar(
        self, *, modelo: str, sistema: str, usuario: str, max_tokens: int, timeout: float
    ) -> Resultado:
        dados = _enviar(
            self._cliente,
            URL_ANTHROPIC,
            {"x-api-key": self._chave, "anthropic-version": VERSAO_ANTHROPIC},
            {
                "model": modelo,
                "max_tokens": max_tokens,
                "system": sistema,
                "messages": [{"role": "user", "content": usuario}],
            },
            timeout,
        )
        blocos = dados.get("content")
        if not isinstance(blocos, list):
            raise ErroProvedor("formato")
        texto = "".join(
            b["text"]
            for b in blocos
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        )
        uso = dados.get("usage") if isinstance(dados.get("usage"), dict) else {}
        return Resultado(
            _texto_valido(texto),
            _inteiro(uso.get("input_tokens")),
            _inteiro(uso.get("output_tokens")),
        )


def criar_provedor(nome: str, chave: str) -> Provedor:
    if nome == "openai":
        return ProvedorOpenAI(chave)
    if nome == "anthropic":
        return ProvedorAnthropic(chave)
    raise ValueError("provedor desconhecido")
