"""Segurança: JWT (casos de borda), MFA, cooldown do JWKS, config de produção, logs, textos."""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import uuid
from dataclasses import replace
from typing import Any

import jwt
import pytest
from jwt.algorithms import ECAlgorithm

from app import create_app
from app.auth.jwt import ResolvedorJWKS, VerificadorJWT
from app.config import Config
from app.errors import RequisicaoInvalida
from app.modelos import Configuracoes
from app.repositorios.supabase_repo import COLUNAS_REQ, filtrar_colunas
from app.services.validacao import validar_criacao
from tests.conftest import _CHAVE, AUDIENCE, ISSUER
from tests.test_requisicoes import corpo


def _claims(sub: Any, **extra: Any) -> dict[str, Any]:
    agora = int(time.time())
    base = {"sub": sub, "iss": ISSUER, "aud": AUDIENCE, "iat": agora, "exp": agora + 600}
    return {**base, **extra}


def _tok(claims: dict[str, Any], kid: str = "k1") -> str:
    return jwt.encode(claims, _CHAVE, algorithm="ES256", headers={"kid": kid})


def _bearer(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def _codigo(resp) -> str:  # type: ignore[no-untyped-def]
    return resp.get_json()["erro"]["codigo"]


# ------------------------------------------------------------------ B10: JWT de borda
def _b64(d: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def test_alg_none_recusado(client, repo):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil()
    tok = f"{_b64({'alg': 'none', 'typ': 'JWT', 'kid': 'k1'})}.{_b64(_claims(p.id))}."
    assert client.get("/api/eu", headers=_bearer(tok)).status_code == 401


@pytest.mark.parametrize("faltando", ["exp", "iat", "sub"])
def test_claim_obrigatoria_ausente_401(client, repo, faltando):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil()
    c = _claims(p.id)
    del c[faltando]
    r = client.get("/api/eu", headers=_bearer(_tok(c)))
    assert r.status_code == 401 and _codigo(r) == "nao_autenticado"


@pytest.mark.parametrize("sub", ["nao-e-uuid", "", "1; drop table perfis"])
def test_sub_nao_uuid_401(client, sub):  # type: ignore[no-untyped-def]
    r = client.get("/api/eu", headers=_bearer(_tok(_claims(sub))))
    assert r.status_code == 401


def test_sub_nao_string_401(client):  # type: ignore[no-untyped-def]
    assert client.get("/api/eu", headers=_bearer(_tok(_claims(123)))).status_code == 401


@pytest.mark.parametrize(
    "cabecalho",
    ["", "Bearer", "Bearer ", "Bearer    ", "Basic abc", "Token abc", "abc.def.ghi"],
)
def test_authorization_malformado_401(client, cabecalho):  # type: ignore[no-untyped-def]
    r = client.get("/api/eu", headers={"Authorization": cabecalho})
    assert r.status_code == 401 and _codigo(r) == "nao_autenticado"


def test_usuario_inativado_apos_emissao_do_token(client, repo, auth):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil("autorizador")
    assert client.get("/api/eu", headers=auth(p.id)).status_code == 200
    repo.perfis[p.id] = replace(p, ativo=False)  # token continua válido; o banco manda
    r = client.get("/api/eu", headers=auth(p.id))
    assert r.status_code == 403 and _codigo(r) == "conta_inativa"


def _app_com_resolvedor(repo, resolvedor):  # type: ignore[no-untyped-def]
    return create_app(
        Config(jwt_issuer=ISSUER, jwt_audience=AUDIENCE, testing=True),
        repo=repo,
        verificador=VerificadorJWT(issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=resolvedor),
    )


def _jwks(*kids: str) -> dict[str, Any]:
    chaves = []
    for kid in kids:
        j = ECAlgorithm.to_jwk(_CHAVE.public_key(), as_dict=True)
        chaves.append({**j, "kid": kid, "alg": "ES256", "use": "sig"})
    return {"keys": chaves}


class _ClienteJWKS:
    def __init__(self, dados: dict[str, Any], falha: bool = False) -> None:
        self.dados, self.falha, self.chamadas = dados, falha, 0

    def fetch_data(self) -> dict[str, Any]:
        self.chamadas += 1
        if self.falha:
            raise OSError("rede")
        return self.dados


class _Relogio:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _resolvedor(cliente: _ClienteJWKS, **kw: Any) -> ResolvedorJWKS:
    return ResolvedorJWKS("https://x.example/jwks", cliente=cliente, **kw)  # type: ignore[arg-type]


def test_kid_desconhecido_401_e_nao_500(repo):  # type: ignore[no-untyped-def]
    app = _app_com_resolvedor(repo, _resolvedor(_ClienteJWKS(_jwks("k1"))))
    p = repo.novo_perfil()
    r = app.test_client().get("/api/eu", headers=_bearer(_tok(_claims(p.id), kid="outro")))
    assert r.status_code == 401


def test_token_sem_kid_401(repo):  # type: ignore[no-untyped-def]
    app = _app_com_resolvedor(repo, _resolvedor(_ClienteJWKS(_jwks("k1"))))
    p = repo.novo_perfil()
    tok = jwt.encode(_claims(p.id), _CHAVE, algorithm="ES256")
    assert app.test_client().get("/api/eu", headers=_bearer(tok)).status_code == 401


def test_falha_do_jwks_401_e_nao_500(repo):  # type: ignore[no-untyped-def]
    app = _app_com_resolvedor(repo, _resolvedor(_ClienteJWKS(_jwks("k1"), falha=True)))
    p = repo.novo_perfil()
    r = app.test_client().get("/api/eu", headers=_bearer(_tok(_claims(p.id))))
    assert r.status_code == 401 and _codigo(r) == "nao_autenticado"


def test_jwks_valido_autentica(repo):  # type: ignore[no-untyped-def]
    app = _app_com_resolvedor(repo, _resolvedor(_ClienteJWKS(_jwks("k1"))))
    p = repo.novo_perfil()
    r = app.test_client().get("/api/eu", headers=_bearer(_tok(_claims(p.id))))
    assert r.status_code == 200


# ------------------------------------------------------------------ B2: cooldown do JWKS
def test_kid_desconhecido_respeita_cooldown():
    cliente, relogio = _ClienteJWKS(_jwks("k1")), _Relogio()
    res = _resolvedor(cliente, cooldown=60, relogio=relogio)
    tok_ruim = _tok(_claims("a"), kid="falso")
    for _ in range(20):
        with pytest.raises(jwt.PyJWTError):
            res(tok_ruim)
    assert cliente.chamadas == 1  # uma busca; as demais caíram no cooldown
    relogio.t += 61
    with pytest.raises(jwt.PyJWTError):
        res(tok_ruim)
    assert cliente.chamadas == 2


def test_falha_do_jwks_tambem_respeita_cooldown():
    cliente, relogio = _ClienteJWKS(_jwks("k1"), falha=True), _Relogio()
    res = _resolvedor(cliente, cooldown=60, relogio=relogio)
    for _ in range(10):
        with pytest.raises(Exception):  # noqa: B017, PT011
            res(_tok(_claims("a")))
    assert cliente.chamadas == 1


def test_kid_conhecido_usa_cache_sem_buscar():
    cliente = _ClienteJWKS(_jwks("k1"))
    res = _resolvedor(cliente, relogio=_Relogio())
    for _ in range(5):
        res(_tok(_claims("a")))
    assert cliente.chamadas == 1


def test_rotacao_de_chave_apos_cooldown():
    cliente, relogio = _ClienteJWKS(_jwks("k1")), _Relogio()
    res = _resolvedor(cliente, cooldown=60, relogio=relogio)
    res(_tok(_claims("a")))
    cliente.dados = _jwks("k1", "k2")
    with pytest.raises(jwt.PyJWTError):  # ainda no cooldown
        res(_tok(_claims("a"), kid="k2"))
    relogio.t += 61
    assert res(_tok(_claims("a"), kid="k2")) is not None


# ------------------------------------------------------------------ M1: MFA
@pytest.mark.parametrize("role", ["admin", "autorizador"])
def test_mfa_ligado_exige_aal2_para_staff(client, repo, role):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(mfa=True)
    p = repo.novo_perfil(role)
    r = client.get("/api/eu", headers=_bearer(_tok(_claims(p.id))))
    assert r.status_code == 403 and _codigo(r) == "mfa_requerido"
    r = client.get("/api/eu", headers=_bearer(_tok(_claims(p.id, aal="aal1"))))
    assert r.status_code == 403 and _codigo(r) == "mfa_requerido"


@pytest.mark.parametrize("role", ["admin", "autorizador"])
def test_mfa_ligado_aceita_aal2_com_totp(client, repo, role):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(mfa=True)
    p = repo.novo_perfil(role)
    amr = [{"method": "password", "timestamp": 1}, {"method": "totp", "timestamp": 2}]
    r = client.get("/api/eu", headers=_bearer(_tok(_claims(p.id, aal="aal2", amr=amr))))
    assert r.status_code == 200


@pytest.mark.parametrize(
    "extra",
    [
        {"aal": "aal2"},  # sem amr
        {"aal": "aal2", "amr": []},
        {"aal": "aal2", "amr": [{"method": "password"}]},
        {"aal": "aal2", "amr": "totp"},
        {"aal": "aal1", "amr": [{"method": "totp"}]},
    ],
)
def test_mfa_aal2_sem_totp_recusado(client, repo, extra):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(mfa=True)
    p = repo.novo_perfil("admin")
    r = client.get("/api/eu", headers=_bearer(_tok(_claims(p.id, **extra))))
    assert r.status_code == 403 and _codigo(r) == "mfa_requerido"


def test_mfa_ligado_solicitante_segue_so_com_senha(client, repo, auth):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(mfa=True)
    p = repo.novo_perfil("solicitante")
    assert client.get("/api/eu", headers=auth(p.id)).status_code == 200


def test_mfa_desligado_vale_so_senha(client, repo, auth):  # type: ignore[no-untyped-def]
    for role in ("admin", "autorizador"):
        p = repo.novo_perfil(role)
        assert client.get("/api/eu", headers=auth(p.id)).status_code == 200


def test_mfa_bloqueia_acao_de_autorizador(client, repo, auth):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(mfa=True)
    sol, aut, vid = repo.novo_perfil(), repo.novo_perfil("autorizador"), repo.novo_veiculo()
    rid = client.post("/api/requisicoes", headers=auth(sol.id), json=corpo(vid)).get_json()["id"]
    r = client.post(f"/api/requisicoes/{rid}/autorizar", headers=auth(aut.id))
    assert r.status_code == 403 and _codigo(r) == "mfa_requerido"


# ------------------------------------------------------------------ B1: config de produção
def _cfg(**kw: Any) -> Config:
    base: dict[str, Any] = {
        "supabase_url": "https://abc.supabase.co",
        "supabase_service_role_key": "k",
        "jwks_url": "https://abc.supabase.co/auth/v1/.well-known/jwks.json",
        "jwt_issuer": "https://abc.supabase.co/auth/v1",
        "cors_origins": ["https://app.agu.gov.br"],
    }
    return Config(**{**base, **kw})


def test_config_valida_passa():
    _cfg().validar_producao()


@pytest.mark.parametrize("campo", ["supabase_url", "jwks_url", "jwt_issuer"])
def test_config_exige_https(campo):
    cfg = _cfg(**{campo: "http://abc.supabase.co/x"})
    with pytest.raises(RuntimeError, match="https://"):
        cfg.validar_producao()


def test_config_host_jwks_diferente_do_issuer():
    cfg = _cfg(jwks_url="https://evil.example/.well-known/jwks.json")
    with pytest.raises(RuntimeError, match="mesmo host"):
        cfg.validar_producao()


@pytest.mark.parametrize("origens", [["*"], ["https://a.gov.br", "*"], [" * "]])
def test_config_recusa_cors_curinga(origens):
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        _cfg(cors_origins=origens).validar_producao()


def test_config_nao_vaza_valores_na_mensagem():
    with pytest.raises(RuntimeError) as e:
        _cfg(supabase_url="http://segredo-host.example").validar_producao()
    assert "segredo-host" not in str(e.value)


def test_cors_lista_vazia_nao_libera_nenhuma_origem(repo):  # type: ignore[no-untyped-def]
    app = create_app(
        Config(jwt_issuer=ISSUER, jwt_audience=AUDIENCE, testing=True, cors_origins=[]),
        repo=repo,
        verificador=VerificadorJWT(
            issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: None
        ),
    )
    c = app.test_client()
    r = c.get("/api/saude", headers={"Origin": "https://qualquer.example"})
    assert "Access-Control-Allow-Origin" not in r.headers
    pre = c.options(
        "/api/requisicoes",
        headers={"Origin": "https://qualquer.example", "Access-Control-Request-Method": "POST"},
    )
    assert "Access-Control-Allow-Origin" not in pre.headers


# ------------------------------------------------------------------ M3: logs sem dados pessoais
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
LOGGERS_RUIDOSOS = ("httpx", "httpcore", "hpack", "postgrest", "werkzeug")


def test_loggers_de_terceiros_em_warning(app):  # type: ignore[no-untyped-def]
    for nome in LOGGERS_RUIDOSOS:
        assert logging.getLogger(nome).getEffectiveLevel() >= logging.WARNING, nome


def test_logs_sem_uuid_nem_email(app, client, repo, auth, caplog):  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    sol, vid = repo.novo_perfil(), repo.novo_veiculo()
    email = "fulano.silva@agu.gov.br"
    # Emula o que as bibliotecas logariam em INFO ao consultar perfis por id/e-mail.
    for nome in LOGGERS_RUIDOSOS:
        logging.getLogger(nome).info("GET /perfis?id=eq.%s&email=eq.%s", sol.id, email)
    client.get("/api/eu", headers=auth(sol.id))
    client.get("/api/eu", headers=_bearer("lixo"))
    client.post("/api/requisicoes", headers=auth(sol.id), json=corpo(vid))
    client.post("/api/requisicoes", headers=auth(sol.id), json=corpo(vid))  # veículo ocupado
    texto = "\n".join(r.getMessage() for r in caplog.records)
    assert not UUID_RE.search(texto) and email not in texto


# ------------------------------------------------------------------ B3 / B4: validação
@pytest.mark.parametrize(
    "valor",
    [
        "9999-12-31T23:59:59-03:00",  # estoura no astimezone(UTC)
        "0001-01-01T00:00:00+14:00",
        "1999-12-31T23:59:59+00:00",
        "2101-01-01T00:00:00+00:00",
        "2026-13-45T99:00:00+00:00",
    ],
)
def test_hora_saida_absurda_400(client, auth, repo, valor):  # type: ignore[no-untyped-def]
    sol, vid = repo.novo_perfil(), repo.novo_veiculo()
    r = client.post("/api/requisicoes", headers=auth(sol.id), json=corpo(vid, hora_saida=valor))
    assert r.status_code == 400 and _codigo(r) == "requisicao_invalida"


def test_texto_remove_controle_e_normaliza_nfc():
    esc, nul, bel = chr(27), chr(0), chr(7)
    d = validar_criacao(
        corpo(
            str(uuid.uuid4()),
            nome_solicitante=f"José{nul} {bel}Silva‮",
            trajeto=f"A{chr(9)}B{esc}[31m​C",
            observacao=f"linha1{chr(13)}{chr(10)}linha2{chr(13)}linha3{nul}{chr(9)}tab",
        )
    )
    assert d["nome_solicitante"] == "José Silva"
    assert d["trajeto"] == "AB[31mC"
    assert d["observacao"] == f"linha1{chr(10)}linha2{chr(10)}linha3tab"


def test_texto_so_controle_vira_vazio():
    with pytest.raises(RequisicaoInvalida):
        validar_criacao(corpo(str(uuid.uuid4()), trajeto=f"{chr(0)}{chr(1)} "))
    d = validar_criacao(corpo(str(uuid.uuid4()), observacao=f"{chr(0)}{chr(10)} "))
    assert d["observacao"] is None


def test_limite_conta_apos_limpeza():
    d = validar_criacao(corpo(str(uuid.uuid4()), observacao="a" * 500 + chr(0) * 10))
    assert len(d["observacao"]) == 500


# ------------------------------------------------------------------ B6: colunas do retorno
def test_filtrar_colunas_descarta_extras():
    linha = {c: 1 for c in COLUNAS_REQ.split(",")} | {"coluna_interna": "x", "segredo": "y"}
    assert set(filtrar_colunas(linha)) == set(COLUNAS_REQ.split(","))
