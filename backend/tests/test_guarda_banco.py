"""A guarda técnica recusa qualquer banco que não seja local e confirmado como descartável."""

from __future__ import annotations

import pytest

from tests.guarda_banco import avaliar, host_local

CHAVE = "chave-de-teste"


def _env(url: str, confirma: str | None = "descartavel") -> dict[str, str]:
    env = {"U": url, "K": CHAVE}
    if confirma is not None:
        env["CONTRATO_CONFIRM"] = confirma
    return env


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:54321", "http://localhost:54321"],
)
def test_libera_host_local_confirmado(url: str) -> None:
    lib = avaliar("U", "K", _env(url))
    assert lib.liberado and lib.url == url and lib.chave == CHAVE


@pytest.mark.parametrize(
    "url",
    [
        "https://abcdefgh.supabase.co",
        "http://127.0.0.1.evil.com:54321",
        "http://localhost.evil.com",
        "http://127.0.0.1@evil.com",
        "http://user:senha@127.0.0.1:54321",
        "ftp://127.0.0.1",
        "http://0.0.0.0:54321",
        "http://[::1]:54321",
        "127.0.0.1:54321",
        "",
    ],
)
def test_recusa_host_nao_local(url: str) -> None:
    assert not host_local(url)
    assert not avaliar("U", "K", _env(url)).liberado


@pytest.mark.parametrize("confirma", [None, "", "sim", "DESCARTAVEL"])
def test_exige_confirmacao_explicita(confirma: str | None) -> None:
    lib = avaliar("U", "K", _env("http://127.0.0.1:54321", confirma))
    assert not lib.liberado and "CONTRATO_CONFIRM" in (lib.motivo or "")


def test_sem_variaveis_pula_sem_vazar_valores() -> None:
    lib = avaliar("U", "K", {})
    assert not lib.liberado and CHAVE not in (lib.motivo or "")
    remoto = avaliar("U", "K", _env("https://abcdefgh.supabase.co"))
    assert "abcdefgh" not in (remoto.motivo or "") and CHAVE not in (remoto.motivo or "")


def test_recusa_localhost_que_resolve_para_fora(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def falso(*_a: object, **_k: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", falso)
    lib = avaliar("U", "K", _env("http://localhost:54321"))
    assert not lib.liberado
