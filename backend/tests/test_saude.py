"""GET /api/saude: sonda de conectividade (Fase 1). Pública, mínima e sem tocar em nada externo."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from tests.fakes import RepoMemoria


@pytest.fixture
def banco_proibido(monkeypatch: pytest.MonkeyPatch, repo: RepoMemoria) -> list[str]:
    """Qualquer acesso a método público do repositório durante a sonda registra a chamada."""
    chamadas: list[str] = []
    original = type(repo).__getattribute__

    def espia(self: Any, nome: str) -> Any:
        valor = original(self, nome)
        if self is repo and not nome.startswith("_") and callable(valor):
            chamadas.append(nome)
        return valor

    monkeypatch.setattr(type(repo), "__getattribute__", espia)
    return chamadas


def test_publica_sem_authorization_e_corpo_minimo(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/saude")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}
    assert r.mimetype == "application/json"


def test_ignora_authorization_invalido_e_nao_exige_mfa(client, repo) -> None:  # type: ignore[no-untyped-def]
    repo.config = repo.config.__class__(mfa=True)
    r = client.get("/api/saude", headers={"Authorization": "Bearer lixo"})
    assert r.status_code == 200 and r.get_json() == {"status": "ok"}


def test_resposta_nao_vaza_informacao(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/saude", headers={"Host": "interno.exemplo"})
    assert set(r.get_json()) == {"status"}
    corpo = r.get_data(as_text=True).lower()
    for proibido in ("versao", "version", "env", "host", "banco", "supabase", "interno"):
        assert proibido not in corpo


def test_head_funciona_sem_corpo(client) -> None:  # type: ignore[no-untyped-def]
    r = client.head("/api/saude")
    assert r.status_code == 200
    assert r.get_data() == b""
    assert r.headers["Cache-Control"] == "no-store"


def test_cache_control_no_store(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/saude").headers["Cache-Control"] == "no-store"


def test_nao_consulta_banco_nem_registra_acesso(client, repo, banco_proibido) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/saude").status_code == 200
    assert client.head("/api/saude").status_code == 200
    assert banco_proibido == []
    assert repo.chamadas_registro == 0
    assert repo.acessos == {}


def test_fora_do_rate_limit(client, app) -> None:  # type: ignore[no-untyped-def]
    for _ in range(300):
        assert client.get("/api/saude").status_code == 200
    r = client.get("/api/saude")
    assert r.status_code == 200
    assert "X-RateLimit-Limit" not in r.headers


def test_nao_gera_log_do_app(client, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[no-untyped-def]
    with caplog.at_level(logging.DEBUG):
        client.get("/api/saude", headers={"CF-Connecting-IP": "203.0.113.9"})
    assert [r for r in caplog.records if r.name.startswith("app")] == []
    assert "203.0.113.9" not in caplog.text


def test_outros_metodos_nao_permitidos(client) -> None:  # type: ignore[no-untyped-def]
    assert client.post("/api/saude").status_code == 405
