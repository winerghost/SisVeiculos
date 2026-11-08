from __future__ import annotations

import time
import uuid

import jwt

from app import create_app
from app.auth.jwt import VerificadorJWT
from app.config import Config
from tests.conftest import _CHAVE, AUDIENCE, ISSUER, emitir_token


def _erro(resp):  # type: ignore[no-untyped-def]
    return resp.get_json()["erro"]


def test_saude_publica(client):  # type: ignore[no-untyped-def]
    r = client.get("/api/saude")
    assert r.status_code == 200 and r.get_json() == {"status": "ok"}


def test_sem_token_401_formato_padrao(client):  # type: ignore[no-untyped-def]
    r = client.get("/api/eu")
    assert r.status_code == 401
    assert set(_erro(r)) == {"codigo", "mensagem"}


def test_token_valido_carrega_role_do_banco(client, repo, auth):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil("autorizador")
    r = client.get("/api/eu", headers=auth(p.id))
    assert r.status_code == 200
    corpo = r.get_json()
    assert corpo["role"] == "autorizador" and "autorizar" in corpo["permissoes"]
    assert corpo["recursos"] == {
        "quilometragem": False,
        "abastecimento_manutencao": False,
        "checklist": False,
        "mfa": False,
        "relatorio_email": False,
        "relatorio_whatsapp": False,
        "notificacao_email_criticos": False,
    }


def test_role_no_token_e_ignorada(client, repo):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil("solicitante")
    agora = int(time.time())
    tok = jwt.encode(
        {"sub": p.id, "iss": ISSUER, "aud": AUDIENCE, "iat": agora, "exp": agora + 60,
         "role": "admin", "app_metadata": {"role": "admin"}},
        _CHAVE, algorithm="ES256", headers={"kid": "k1"},
    )  # fmt: skip
    r = client.get("/api/eu", headers={"Authorization": f"Bearer {tok}"})
    assert r.get_json()["role"] == "solicitante"


def test_conta_inativa_recusada(client, repo, auth):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil(ativo=False)
    r = client.get("/api/eu", headers=auth(p.id))
    assert r.status_code == 403 and _erro(r)["codigo"] == "conta_inativa"


def test_usuario_sem_perfil_recusado(client, auth):  # type: ignore[no-untyped-def]
    r = client.get("/api/eu", headers=auth(str(uuid.uuid4())))
    assert r.status_code == 403


def test_jwt_invalidos(client, repo, chave_errada):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil()
    casos = {
        "assinatura": emitir_token(p.id, chave=chave_errada),
        "expirado": emitir_token(p.id, exp_em=-120),
        "audience": emitir_token(p.id, aud="outra"),
        "issuer": emitir_token(p.id, iss="https://evil.example/auth/v1"),
        "lixo": "abc.def.ghi",
    }
    for nome, tok in casos.items():
        r = client.get("/api/eu", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401, nome


def test_hs256_recusado(client, repo):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil()
    agora = int(time.time())
    tok = jwt.encode(
        {"sub": p.id, "iss": ISSUER, "aud": AUDIENCE, "iat": agora, "exp": agora + 60},
        "x" * 40, algorithm="HS256",
    )  # fmt: skip
    r = client.get("/api/eu", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401


def test_verificador_exige_issuer_e_audience():  # type: ignore[no-untyped-def]
    import pytest

    with pytest.raises(RuntimeError):
        VerificadorJWT(issuer="", audience="x", resolvedor_chave=lambda _t: None)


def test_payload_grande_413(client, repo, auth):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil()
    r = client.post(
        "/api/requisicoes", headers=auth(p.id), data=b"x" * 20_000,
        content_type="application/json",
    )  # fmt: skip
    assert r.status_code == 413 and _erro(r)["codigo"] == "payload_grande"


def test_rate_limit_na_criacao(repo, auth):  # type: ignore[no-untyped-def]
    from app.extensions import limiter

    app = create_app(
        Config(
            jwt_issuer=ISSUER, jwt_audience=AUDIENCE, testing=True, limite_escrita="2 per minute"
        ),
        repo=repo,
        verificador=VerificadorJWT(
            issuer=ISSUER, audience=AUDIENCE,
            resolvedor_chave=lambda _t: _CHAVE.public_key(),
        ),
    )  # fmt: skip
    limiter.reset()
    p = repo.novo_perfil()
    c = app.test_client()
    codigos = [
        c.post("/api/requisicoes", headers=auth(p.id), json={}).status_code for _ in range(3)
    ]
    assert codigos == [400, 400, 429]
    assert (
        _erro(c.post("/api/requisicoes", headers=auth(p.id), json={}))["codigo"]
        == "limite_excedido"
    )


def test_cors_restrito(repo):  # type: ignore[no-untyped-def]
    app = create_app(
        Config(
            jwt_issuer=ISSUER,
            jwt_audience=AUDIENCE,
            testing=True,
            cors_origins=["https://ok.gov.br"],
        ),
        repo=repo,
        verificador=VerificadorJWT(
            issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: None
        ),
    )  # fmt: skip
    c = app.test_client()
    ok = c.get("/api/saude", headers={"Origin": "https://ok.gov.br"})
    mau = c.get("/api/saude", headers={"Origin": "https://evil.example"})
    assert ok.headers.get("Access-Control-Allow-Origin") == "https://ok.gov.br"
    assert "Access-Control-Allow-Origin" not in mau.headers
