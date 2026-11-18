"""Conexão com o Supabase: HTTP/1.1, keep-alive curto, sem redirect e repetição só sem envio.

Sintoma em produção: "Serviço de dados indisponível" intermitente na primeira requisição após
ócio (conexão HTTP/2 do pool já fechada pelo servidor); o F5 resolvia.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from app.errors import ErroInterno
from app.repositorios import cliente_http, supabase_repo
from app.repositorios.supabase_repo import _exec

MSG = "Serviço de dados indisponível. Tente novamente."


class _Falha:
    """fn() que levanta os erros dados em ordem e depois devolve 'ok'."""

    def __init__(self, *erros: Exception) -> None:
        self.erros = list(erros)
        self.chamadas = 0

    def __call__(self) -> str:
        self.chamadas += 1
        if self.erros:
            raise self.erros.pop(0)
        return "ok"


def _desconectado() -> httpx.RemoteProtocolError:
    return httpx.RemoteProtocolError("Server disconnected without sending a response.")


# --- helper do cliente HTTP -------------------------------------------------------------


def test_cliente_http_desliga_http2_e_usa_keepalive_curto(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    capturado: dict[str, Any] = {}
    original = httpx.Client

    def espiao(**kw: Any) -> httpx.Client:
        capturado.update(kw)
        return original(**kw)

    monkeypatch.setattr(cliente_http.httpx, "Client", espiao)
    c = cliente_http.cliente_http(20)
    try:
        assert capturado["http2"] is False
        assert capturado["timeout"] == 20
        assert capturado["limits"].keepalive_expiry == cliente_http.KEEPALIVE_EXPIRY
        # Pool limitado como no padrão do httpx (Limits sem eles seria ilimitado).
        assert capturado["limits"].max_connections == 100
        assert capturado["limits"].max_keepalive_connections == 20
        assert cliente_http.KEEPALIVE_EXPIRY <= 30
        assert c.timeout.read == 20
        assert capturado["follow_redirects"] is False
    finally:
        c.close()


def test_cliente_http_real_nao_negocia_http2() -> None:
    c = cliente_http.cliente_http(5)
    try:
        assert c._transport._pool._http2 is False  # type: ignore[attr-defined]
        assert c._transport._pool._http1 is True  # type: ignore[attr-defined]
    finally:
        c.close()


def test_criar_cliente_supabase_passa_o_httpx_proprio(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    capturado: dict[str, Any] = {}

    def falso_create_client(url: str, chave: str, options: Any = None) -> str:
        capturado.update(url=url, chave=chave, options=options)
        return "cliente"

    class Opcoes:
        def __init__(self, **kw: Any) -> None:
            self.kw = kw

    monkeypatch.setattr(cliente_http, "create_client", falso_create_client)
    monkeypatch.setattr(cliente_http, "ClientOptions", Opcoes)
    assert cliente_http.criar_cliente_supabase("https://x.supabase.co", "k", timeout=7) == "cliente"
    http = capturado["options"].kw["httpx_client"]
    try:
        assert isinstance(http, httpx.Client)
        assert http._transport._pool._http2 is False  # type: ignore[attr-defined]
        assert http.timeout.read == 7
    finally:
        http.close()


# --- rede de segurança em _exec ---------------------------------------------------------


@pytest.mark.parametrize("erro", [httpx.ConnectError("falhou"), httpx.ConnectTimeout("lento")])
def test_exec_repete_uma_vez_quando_o_pedido_nem_saiu(erro: Exception, caplog) -> None:  # type: ignore[no-untyped-def]
    fn = _Falha(erro)
    with caplog.at_level(logging.WARNING):
        assert _exec(fn, repetir_lock=True) == "ok"
    assert fn.chamadas == 2
    assert f"conexao_repetindo tipo={type(erro).__name__}" in caplog.text


@pytest.mark.parametrize(
    "erro",
    [
        _desconectado(),  # pedido já enviado: a escrita pode ter sido aplicada
        httpx.ReadTimeout("lento"),
        httpx.ReadError("reset"),
        httpx.RemoteProtocolError("peer closed connection without sending complete message body"),
        RuntimeError("outro"),
    ],
)
def test_exec_nao_repete_quando_o_pedido_pode_ter_executado(erro: Exception) -> None:
    fn = _Falha(erro)
    with pytest.raises(ErroInterno) as exc:
        _exec(fn, repetir_lock=True)
    assert fn.chamadas == 1
    assert exc.value.mensagem == MSG


def test_exec_escrita_com_servidor_desconectado_nao_repete(caplog) -> None:  # type: ignore[no-untyped-def]
    """Regressão (revisão M1): POST/RPC de escrita que caiu após o envio não é repetido."""
    fn = _Falha(_desconectado())
    with caplog.at_level(logging.WARNING), pytest.raises(ErroInterno):
        _exec(fn, repetir_lock=True)
    assert fn.chamadas == 1
    assert "conexao_repetindo" not in caplog.text
    assert "erro_acesso_dados tipo=RemoteProtocolError" in caplog.text
    assert "without sending" not in caplog.text  # só o tipo vai ao log


def test_exec_esgota_e_devolve_a_mensagem_atual(caplog) -> None:  # type: ignore[no-untyped-def]
    fn = _Falha(httpx.ConnectError("a"), httpx.ConnectError("b"), httpx.ConnectError("c"))
    with caplog.at_level(logging.WARNING), pytest.raises(ErroInterno) as exc:
        _exec(fn, repetir_lock=True)
    assert fn.chamadas == 2  # uma repetição só, mesmo com repetir_lock
    assert exc.value.mensagem == MSG
    assert "erro_acesso_dados tipo=ConnectError" in caplog.text


def test_exec_lock_e_conexao_combinados(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from postgrest.exceptions import APIError

    monkeypatch.setattr(supabase_repo, "_dormir", lambda _s: None)
    lock = APIError({"code": "55P03", "message": "lock"})
    fn = _Falha(httpx.ConnectError("x"), lock, lock)
    assert _exec(fn, repetir_lock=True) == "ok"
    assert fn.chamadas == 4
