"""Passkeys (WebAuthn, 1ª etapa) com criptografia real: cadastro, reautenticação, desafio, origem,
RP ID, assinatura, contador, IDOR, limite, flag, login recente e ausência de segredos em log."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app import create_app
from app.auth.jwt import VerificadorJWT
from app.config import Config, normalizar_origem_webauthn, webauthn_valido
from app.extensions import limiter
from app.modelos import Configuracoes
from app.services.passkeys import ServicoPasskeys
from tests.conftest import _CHAVE, AUDIENCE, ISSUER, emitir_token
from tests.fakes_passkeys import ORIGEM, RP_ID, Autenticador, RepoPasskeys, b64

SESSAO = "sessao-1"


def _cfg(**extra: Any) -> Config:
    base: dict[str, Any] = {
        "jwt_issuer": ISSUER,
        "jwt_audience": AUDIENCE,
        "testing": True,
        "webauthn_rp_id": RP_ID,
        "webauthn_rp_name": "SisVeiculos AGU",
        "webauthn_origins": [ORIGEM],
    }
    return Config(**(base | extra))


def _montar(repo: RepoPasskeys, auth_admin: Any, storage: Any, avatares: Any, cfg: Config) -> Any:
    ver = VerificadorJWT(
        issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
    )
    app = create_app(
        cfg,
        repo=repo,
        verificador=ver,
        auth_admin=auth_admin,
        armazenamento=storage,
        armazenamento_avatares=avatares,
    )
    limiter.reset()
    return app.test_client()


@pytest.fixture
def repo() -> RepoPasskeys:
    return RepoPasskeys()


@pytest.fixture
def cli(repo, auth_admin, storage, avatares):  # type: ignore[no-untyped-def]
    return _montar(repo, auth_admin, storage, avatares, _cfg(limite_passkeys="1000 per minute"))


@pytest.fixture
def cli_sem_env(repo, auth_admin, storage, avatares):  # type: ignore[no-untyped-def]
    """Flag ligada, mas sem WEBAUTHN_* no servidor."""
    return _montar(
        repo,
        auth_admin,
        storage,
        avatares,
        _cfg(webauthn_rp_id="", webauthn_rp_name="", webauthn_origins=[]),
    )


@pytest.fixture
def cli_limitado(repo, auth_admin, storage, avatares):  # type: ignore[no-untyped-def]
    return _montar(repo, auth_admin, storage, avatares, _cfg(limite_passkeys="3 per minute"))


@pytest.fixture
def ligado(repo: RepoPasskeys) -> None:
    repo.config = Configuracoes(passkeys=True)


@pytest.fixture
def ha() -> Callable[..., dict[str, str]]:
    """Cabeçalhos de um login recente (iat = agora) na sessão informada."""

    def _h(quem: Any, sessao: str = SESSAO, **extra: Any) -> dict[str, str]:
        # Como o Supabase: `amr` com o momento real do login (padrão: agora).
        claims = {
            "session_id": sessao,
            "amr": [{"method": "password", "timestamp": int(time.time())}],
        } | extra
        return {"Authorization": f"Bearer {emitir_token(quem.id, extra=claims)}"}

    return _h


def api(cli: Any, metodo: str, caminho: str, headers: Any, json: Any = None) -> Any:
    return getattr(cli, metodo)(f"/api/passkeys{caminho}", headers=headers, json=json)


def cadastrar(
    cli: Any, headers: Any, aut: Autenticador | None = None, apelido: str | None = None
) -> Autenticador:
    """Fluxo completo de cadastro (exige identidade já confirmada)."""
    aut = aut or Autenticador()
    corpo = {"apelido": apelido} if apelido else {}
    r = api(cli, "post", "/registro/opcoes", headers, corpo)
    assert r.status_code == 200, r.get_json()
    dados = r.get_json()
    conf: dict[str, Any] = {
        "id_desafio": dados["id_desafio"],
        "credencial": aut.criar(dados["opcoes"]),
    }
    if apelido:
        conf["apelido"] = apelido
    r = api(cli, "post", "/registro/confirmar", headers, conf)
    assert r.status_code == 201, r.get_json()
    return aut


def reautenticar(cli: Any, headers: Any, aut: Autenticador, **kw: Any) -> Any:
    r = api(cli, "post", "/autenticacao/opcoes", headers)
    assert r.status_code == 200, r.get_json()
    dados = r.get_json()
    return api(
        cli,
        "post",
        "/autenticacao/confirmar",
        headers,
        {"id_desafio": dados["id_desafio"], "credencial": aut.afirmar(dados["opcoes"], **kw)},
    )


def codigo(r: Any) -> str:
    return str(r.get_json()["erro"]["codigo"])


# -- autenticação e flag ---------------------------------------------------------------------
def test_exige_autenticacao(cli) -> None:  # type: ignore[no-untyped-def]
    for metodo, caminho in (
        ("get", ""),
        ("post", "/registro/opcoes"),
        ("post", "/registro/confirmar"),
        ("post", "/autenticacao/opcoes"),
        ("post", "/autenticacao/confirmar"),
        ("delete", "/00000000-0000-0000-0000-000000000000"),
    ):
        assert getattr(cli, metodo)(f"/api/passkeys{caminho}").status_code == 401


ROTAS = (
    ("get", ""),
    ("post", "/registro/opcoes"),
    ("post", "/registro/confirmar"),
    ("post", "/autenticacao/opcoes"),
    ("post", "/autenticacao/confirmar"),
    ("delete", "/00000000-0000-0000-0000-000000000000"),
)


def test_flag_desligada_recusa_tudo(cli, repo, c, ha) -> None:  # type: ignore[no-untyped-def]
    for metodo, caminho in ROTAS:
        r = api(cli, metodo, caminho, ha(c.sol), {} if metodo == "post" else None)
        assert r.status_code == 404 and codigo(r) == "recurso_desligado", caminho
    assert repo.desafios == {} and repo.credenciais == []


def test_flag_ligada_sem_webauthn_no_servidor_recusa(cli_sem_env, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    for metodo, caminho in ROTAS:
        r = api(cli_sem_env, metodo, caminho, ha(c.sol), {} if metodo == "post" else None)
        assert r.status_code == 404 and codigo(r) == "recurso_desligado", caminho


# -- cadastro ---------------------------------------------------------------------------------
def test_opcoes_de_registro_seguem_o_contrato(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    r = api(cli, "post", "/registro/opcoes", ha(c.sol), {"apelido": "Celular"})
    assert r.status_code == 200
    d = r.get_json()
    o = d["opcoes"]
    assert d["id_desafio"] in repo.desafios
    assert o["rp"] == {"id": RP_ID, "name": "SisVeiculos AGU"}
    assert o["user"]["id"] == b64(__import__("uuid").UUID(c.sol.id).bytes)
    assert o["user"]["name"] == "Celular"  # sem nome real do servidor
    assert c.sol.nome not in r.get_data(as_text=True)
    assert o["challenge"] == repo.desafios[d["id_desafio"]]["desafio"]
    assert [p["alg"] for p in o["pubKeyCredParams"]] == [-7, -257]
    assert o["attestation"] == "none"
    assert o["authenticatorSelection"]["userVerification"] == "required"
    assert o["authenticatorSelection"]["residentKey"] == "preferred"
    assert o["excludeCredentials"] == []
    assert o["timeout"] > 0


def test_cadastro_completo_e_listagem(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    aut = cadastrar(cli, ha(c.sol), apelido="Notebook")
    (linha,) = repo.credenciais
    assert linha["usuario_id"] == c.sol.id and linha["credential_id"] == aut.id
    assert linha["apelido"] == "Notebook" and linha["transportes"] == ["internal", "hybrid"]
    assert linha["contador"] == 0

    r = api(cli, "get", "", ha(c.sol))
    corpo = r.get_json()
    (item,) = corpo["itens"]
    assert set(item) == {
        "id",
        "apelido",
        "criada_em",
        "ultimo_uso_em",
        "transportes",
        "acoes_permitidas",
    }
    assert item["apelido"] == "Notebook" and item["acoes_permitidas"] == ["revogar"]
    texto = r.get_data(as_text=True)
    assert aut.id not in texto and linha["chave_publica"] not in texto  # nunca credential_id/chave
    assert corpo["exige_passkey"] is True and corpo["identidade_confirmada"] is False
    assert "cadastrar" in corpo["acoes_permitidas"] and "reautenticar" in corpo["acoes_permitidas"]


def test_listagem_so_do_proprio_usuario(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    cadastrar(cli, ha(c.sol))
    r = api(cli, "get", "", ha(c.outro))
    assert r.get_json()["itens"] == []
    assert r.get_json()["exige_passkey"] is False


def test_usuario_do_corpo_e_ignorado_ou_recusado(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    r = api(cli, "post", "/registro/opcoes", ha(c.sol), {"usuario_id": c.outro.id})
    assert r.status_code == 400 and codigo(r) == "passkey_invalida"
    assert c.outro.id not in r.get_data(as_text=True)


def test_apelido_invalido(cli, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    for apelido in ("x" * 41, 123, "a\nb"):
        r = api(cli, "post", "/registro/opcoes", ha(c.sol), {"apelido": apelido})
        assert r.status_code == 400 and codigo(r) == "passkey_apelido_invalido"


def test_cadastro_com_uv_ausente_e_recusado(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    d = api(cli, "post", "/registro/opcoes", ha(c.sol), {}).get_json()
    cred = Autenticador().criar(d["opcoes"], uv=False)
    r = api(
        cli,
        "post",
        "/registro/confirmar",
        ha(c.sol),
        {"id_desafio": d["id_desafio"], "credencial": cred},
    )
    assert r.status_code == 400 and codigo(r) == "passkey_invalida"
    assert repo.credenciais == []
    assert (c.sol.id, "passkey_falha", "uv_ausente") in repo.eventos


def test_cadastro_origem_e_rp_id_errados(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    casos = (
        ({"origem": "https://malicioso.exemplo.com"}, "origem_invalida"),
        ({"rp_id": "outro.gov.br"}, "rp_id_invalido"),
        ({"cross_origin": True}, "origem_cruzada"),
    )
    for kw, esperado in casos:
        d = api(cli, "post", "/registro/opcoes", ha(c.sol), {}).get_json()
        cred = Autenticador().criar(d["opcoes"], **kw)
        r = api(
            cli,
            "post",
            "/registro/confirmar",
            ha(c.sol),
            {"id_desafio": d["id_desafio"], "credencial": cred},
        )
        assert r.status_code == 400 and codigo(r) == "passkey_invalida", kw
        assert (c.sol.id, "passkey_falha", esperado) in repo.eventos, kw
    assert repo.credenciais == []


def test_cadastro_com_desafio_diferente_do_emitido(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    d = api(cli, "post", "/registro/opcoes", ha(c.sol), {}).get_json()
    cred = Autenticador().criar(d["opcoes"], desafio=b64(b"x" * 32))
    r = api(
        cli,
        "post",
        "/registro/confirmar",
        ha(c.sol),
        {"id_desafio": d["id_desafio"], "credencial": cred},
    )
    assert r.status_code == 400
    assert (c.sol.id, "passkey_falha", "desafio_divergente") in repo.eventos
    assert repo.credenciais == []


def test_credencial_duplicada(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    aut = cadastrar(cli, ha(c.sol))
    reautenticar(cli, ha(c.sol), aut)
    d = api(cli, "post", "/registro/opcoes", ha(c.sol), {}).get_json()
    r = api(
        cli,
        "post",
        "/registro/confirmar",
        ha(c.sol),
        {"id_desafio": d["id_desafio"], "credencial": aut.criar(d["opcoes"])},
    )
    assert r.status_code == 409 and codigo(r) == "passkey_ja_cadastrada"
    assert len(repo.credenciais) == 1


# -- login recente x reautenticação por passkey ----------------------------------------------
def test_primeiro_cadastro_exige_login_recente(cli, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    velho = ha(c.sol, iat=int(time.time()) - 11 * 60)
    r = api(cli, "post", "/registro/opcoes", velho, {})
    assert r.status_code == 403 and codigo(r) == "reautenticacao_requerida"
    quase = ha(c.sol, iat=int(time.time()) - 9 * 60)
    assert api(cli, "post", "/registro/opcoes", quase, {}).status_code == 200


def test_login_recente_nao_se_renova_com_refresh_do_token(cli, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    """`iat` novo, mas o `amr` mostra que a autenticação real foi há horas: não vale como login."""
    antigo = int(time.time()) - 3 * 3600
    h = ha(c.sol, amr=[{"method": "password", "timestamp": antigo}])
    r = api(cli, "post", "/registro/opcoes", h, {})
    assert r.status_code == 403 and codigo(r) == "reautenticacao_requerida"
    recente = ha(c.sol, amr=[{"method": "password", "timestamp": int(time.time()) - 30}])
    assert api(cli, "post", "/registro/opcoes", recente, {}).status_code == 200


def test_quem_tem_passkey_so_reautentica_por_passkey(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    aut = cadastrar(cli, ha(c.sol))
    # login recente NÃO basta quando já existe passkey
    r = api(cli, "post", "/registro/opcoes", ha(c.sol), {})
    assert r.status_code == 403 and codigo(r) == "reautenticacao_requerida"
    r = reautenticar(cli, ha(c.sol), aut)
    assert r.status_code == 200
    assert api(cli, "post", "/registro/opcoes", ha(c.sol), {}).status_code == 200
    # reautenticação vale só para a sessão do JWT em que foi feita
    outra = ha(c.sol, sessao="sessao-2")
    assert api(cli, "post", "/registro/opcoes", outra, {}).status_code == 403
    # e expira (5 min)
    repo.avancar(301)
    assert api(cli, "post", "/registro/opcoes", ha(c.sol), {}).status_code == 403


def test_reautenticacao_de_um_usuario_nao_vale_para_outro(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    aut = cadastrar(cli, ha(c.sol))
    reautenticar(cli, ha(c.sol), aut)
    cadastrar(cli, ha(c.outro))  # o outro usuário também tem passkey
    assert api(cli, "post", "/registro/opcoes", ha(c.outro), {}).status_code == 403


def test_multiplas_credenciais(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    a1 = cadastrar(cli, ha(c.sol))
    assert reautenticar(cli, ha(c.sol), a1).status_code == 200
    a2 = cadastrar(cli, ha(c.sol))
    assert len(repo.credenciais) == 2
    opc = api(cli, "post", "/registro/opcoes", ha(c.sol), {}).get_json()["opcoes"]
    assert {e["id"] for e in opc["excludeCredentials"]} == {a1.id, a2.id}
    d = api(cli, "post", "/autenticacao/opcoes", ha(c.sol)).get_json()
    assert {e["id"] for e in d["opcoes"]["allowCredentials"]} == {a1.id, a2.id}
    assert d["opcoes"]["userVerification"] == "required"
    for a in (a1, a2):
        assert reautenticar(cli, ha(c.sol, sessao="s-" + a.id[:6]), a).status_code == 200


def test_limite_de_dez_passkeys(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    primeira = cadastrar(cli, ha(c.sol))
    for _ in range(9):
        reautenticar(cli, ha(c.sol), primeira)
        cadastrar(cli, ha(c.sol))
    assert len(repo.credenciais) == 10
    reautenticar(cli, ha(c.sol), primeira)
    r = api(cli, "post", "/registro/opcoes", ha(c.sol), {})
    assert r.status_code == 409 and codigo(r) == "passkey_limite"
    assert api(cli, "get", "", ha(c.sol)).get_json()["acoes_permitidas"] == ["reautenticar"]


# -- autenticação (reautenticação) ------------------------------------------------------------
def test_autenticacao_ok_registra_reautenticacao(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    aut = cadastrar(cli, ha(c.sol))
    r = reautenticar(cli, ha(c.sol), aut)
    assert r.status_code == 200
    corpo = r.get_json()
    assert set(corpo) == {"reautenticado_ate"}
    assert repo.reautenticacoes[0]["usuario"] == c.sol.id
    assert repo.reautenticacoes[0]["sessao"] == SESSAO
    assert repo.credenciais[0]["contador"] == 1 and repo.credenciais[0]["ultimo_uso_em"]
    assert api(cli, "get", "", ha(c.sol)).get_json()["identidade_confirmada"] is True


def test_nenhuma_rota_devolve_jwt_ou_sessao(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    respostas = []
    h = ha(c.sol)
    d = api(cli, "post", "/registro/opcoes", h, {})
    respostas.append(d)
    aut = Autenticador()
    respostas.append(
        api(
            cli,
            "post",
            "/registro/confirmar",
            h,
            {
                "id_desafio": d.get_json()["id_desafio"],
                "credencial": aut.criar(d.get_json()["opcoes"]),
            },
        )
    )
    respostas.append(api(cli, "get", "", h))
    respostas.append(reautenticar(cli, h, aut))
    opc = api(cli, "post", "/autenticacao/opcoes", h)
    respostas.append(opc)
    respostas.append(api(cli, "delete", "/00000000-0000-0000-0000-000000000000", h))
    for r in respostas:
        texto = r.get_data(as_text=True).lower()
        for proibido in ("access_token", "refresh_token", "jwt", "bearer", "session", "eyj"):
            assert proibido not in texto, proibido
        assert "set-cookie" not in {k.lower() for k in r.headers.keys()}  # noqa: SIM118
        assert "authorization" not in {k.lower() for k in r.headers.keys()}  # noqa: SIM118


def test_usuario_sem_passkey(cli, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    r = api(cli, "post", "/autenticacao/opcoes", ha(c.sol))
    assert r.status_code == 404 and codigo(r) == "passkey_inexistente"


def test_desafio_expirado(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    aut = cadastrar(cli, ha(c.sol))
    d = api(cli, "post", "/autenticacao/opcoes", ha(c.sol)).get_json()
    repo.avancar(301)
    r = api(
        cli,
        "post",
        "/autenticacao/confirmar",
        ha(c.sol),
        {"id_desafio": d["id_desafio"], "credencial": aut.afirmar(d["opcoes"])},
    )
    assert r.status_code == 400 and codigo(r) == "desafio_invalido"
    assert repo.reautenticacoes == []
    assert (c.sol.id, "passkey_desafio_invalido", "desafio_invalido") in repo.eventos


def test_desafio_reutilizado(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    aut = cadastrar(cli, ha(c.sol))
    d = api(cli, "post", "/autenticacao/opcoes", ha(c.sol)).get_json()
    corpo = {"id_desafio": d["id_desafio"], "credencial": aut.afirmar(d["opcoes"])}
    assert api(cli, "post", "/autenticacao/confirmar", ha(c.sol), corpo).status_code == 200
    r = api(cli, "post", "/autenticacao/confirmar", ha(c.sol), corpo)  # replay
    assert r.status_code == 400 and codigo(r) == "desafio_invalido"
    assert len(repo.reautenticacoes) == 1


def test_desafio_consumido_mesmo_com_falha(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    aut = cadastrar(cli, ha(c.sol))
    d = api(cli, "post", "/autenticacao/opcoes", ha(c.sol)).get_json()
    ruim = {
        "id_desafio": d["id_desafio"],
        "credencial": aut.afirmar(d["opcoes"], chave=ec.generate_private_key(ec.SECP256R1())),
    }
    assert api(cli, "post", "/autenticacao/confirmar", ha(c.sol), ruim).status_code == 400
    bom = {"id_desafio": d["id_desafio"], "credencial": aut.afirmar(d["opcoes"])}
    r = api(cli, "post", "/autenticacao/confirmar", ha(c.sol), bom)
    assert r.status_code == 400 and codigo(r) == "desafio_invalido"


def test_desafio_de_outro_usuario_e_de_outro_tipo(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    a_sol = cadastrar(cli, ha(c.sol))
    cadastrar(cli, ha(c.outro))
    d = api(cli, "post", "/autenticacao/opcoes", ha(c.sol)).get_json()
    corpo = {"id_desafio": d["id_desafio"], "credencial": a_sol.afirmar(d["opcoes"])}
    r = api(cli, "post", "/autenticacao/confirmar", ha(c.outro), corpo)  # desafio do solicitante
    assert r.status_code == 400 and codigo(r) == "desafio_invalido"
    # desafio de registro não serve para autenticar
    reg = api(cli, "post", "/registro/opcoes", ha(c.outro, iat=int(time.time())), {})
    assert reg.status_code == 403  # (já tem passkey) -> sem desafio; usa o tipo por RPC direto
    did, valor = repo.criar_desafio_webauthn(c.sol.id, "registro")
    cred = a_sol.afirmar({"challenge": valor})
    r = api(
        cli, "post", "/autenticacao/confirmar", ha(c.sol), {"id_desafio": did, "credencial": cred}
    )
    assert r.status_code == 400 and codigo(r) == "desafio_invalido"
    assert repo.reautenticacoes == []


def test_assinatura_invalida(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    aut = cadastrar(cli, ha(c.sol))
    d = api(cli, "post", "/autenticacao/opcoes", ha(c.sol)).get_json()
    cred = aut.afirmar(d["opcoes"], chave=ec.generate_private_key(ec.SECP256R1()))
    r = api(
        cli,
        "post",
        "/autenticacao/confirmar",
        ha(c.sol),
        {"id_desafio": d["id_desafio"], "credencial": cred},
    )
    assert r.status_code == 400 and codigo(r) == "passkey_invalida"
    assert (c.sol.id, "passkey_falha", "assinatura_invalida") in repo.eventos
    assert repo.reautenticacoes == [] and repo.credenciais[0]["contador"] == 0


def test_origem_e_rp_id_errados_na_autenticacao(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    aut = cadastrar(cli, ha(c.sol))
    for kw, esperado in (
        ({"origem": "https://evil.exemplo.com"}, "origem_invalida"),
        ({"origem": "http://sisveiculos.agu.gov.br"}, "origem_invalida"),
        ({"rp_id": "outro.gov.br"}, "rp_id_invalido"),
        ({"uv": False}, "uv_ausente"),
        ({"desafio": b64(b"y" * 32)}, "desafio_divergente"),
    ):
        d = api(cli, "post", "/autenticacao/opcoes", ha(c.sol)).get_json()
        cred = aut.afirmar(d["opcoes"], **kw)
        r = api(
            cli,
            "post",
            "/autenticacao/confirmar",
            ha(c.sol),
            {"id_desafio": d["id_desafio"], "credencial": cred},
        )
        assert r.status_code == 400 and codigo(r) == "passkey_invalida", kw
        assert (c.sol.id, "passkey_falha", esperado) in repo.eventos, kw
    assert repo.reautenticacoes == []


def test_credencial_revogada_nao_autentica(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    a1 = cadastrar(cli, ha(c.sol))
    reautenticar(cli, ha(c.sol), a1)
    a2 = cadastrar(cli, ha(c.sol))
    rid = next(x["id"] for x in repo.credenciais if x["credential_id"] == a2.id)
    assert api(cli, "delete", f"/{rid}", ha(c.sol)).status_code == 204
    d = api(cli, "post", "/autenticacao/opcoes", ha(c.sol)).get_json()
    assert {e["id"] for e in d["opcoes"]["allowCredentials"]} == {a1.id}
    r = api(
        cli,
        "post",
        "/autenticacao/confirmar",
        ha(c.sol),
        {"id_desafio": d["id_desafio"], "credencial": a2.afirmar(d["opcoes"])},
    )
    assert r.status_code == 400 and codigo(r) == "passkey_invalida"
    assert (c.sol.id, "passkey_falha", "credencial_desconhecida") in repo.eventos


def test_idor_credencial_de_outro_usuario(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    a_sol = cadastrar(cli, ha(c.sol))
    a_out = cadastrar(cli, ha(c.outro))
    # o outro usuário apresenta a credencial do solicitante (desafio e JWT são dele)
    d = api(cli, "post", "/autenticacao/opcoes", ha(c.outro)).get_json()
    r = api(
        cli,
        "post",
        "/autenticacao/confirmar",
        ha(c.outro),
        {"id_desafio": d["id_desafio"], "credencial": a_sol.afirmar(d["opcoes"])},
    )
    assert r.status_code == 400 and codigo(r) == "passkey_invalida"
    assert repo.reautenticacoes == []
    # revogar credencial alheia: 404 idêntico ao de inexistente
    id_sol = next(x["id"] for x in repo.credenciais if x["credential_id"] == a_sol.id)
    reautenticar(cli, ha(c.outro), a_out)
    alheia = api(cli, "delete", f"/{id_sol}", ha(c.outro))
    inexistente = api(cli, "delete", "/11111111-1111-4111-8111-111111111111", ha(c.outro))
    lixo = api(cli, "delete", "/nao-e-uuid", ha(c.outro))
    assert alheia.status_code == inexistente.status_code == lixo.status_code == 404
    assert alheia.get_json() == inexistente.get_json() == lixo.get_json()
    assert not next(x for x in repo.credenciais if x["id"] == id_sol)["rev"]


def test_user_handle_de_outro_usuario_e_recusado(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    aut = cadastrar(cli, ha(c.sol))
    d = api(cli, "post", "/autenticacao/opcoes", ha(c.sol)).get_json()
    cred = aut.afirmar(d["opcoes"], user_handle=b"\x01" * 16)
    r = api(
        cli,
        "post",
        "/autenticacao/confirmar",
        ha(c.sol),
        {"id_desafio": d["id_desafio"], "credencial": cred},
    )
    assert r.status_code == 400
    assert (c.sol.id, "passkey_falha", "usuario_divergente") in repo.eventos


def test_user_handle_correto_e_aceito(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    import uuid

    aut = cadastrar(cli, ha(c.sol))
    r = reautenticar(cli, ha(c.sol), aut, user_handle=uuid.UUID(c.sol.id).bytes)
    assert r.status_code == 200


def test_contador_regressivo_rejeita_audita_e_nao_reautentica(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    aut = cadastrar(cli, ha(c.sol))
    assert reautenticar(cli, ha(c.sol), aut, contador=5).status_code == 200
    n = len(repo.reautenticacoes)
    for contador in (3, 5):  # menor e igual
        r = reautenticar(cli, ha(c.sol, sessao="outra"), aut, contador=contador)
        assert r.status_code == 400 and codigo(r) == "passkey_invalida"
    assert (c.sol.id, "passkey_regressao_contador", "contador_regressivo") in repo.eventos
    assert len(repo.reautenticacoes) == n
    assert repo.credenciais[0]["contador"] == 5
    assert reautenticar(cli, ha(c.sol), aut, contador=6).status_code == 200


def test_autenticador_sem_contador_zero_continua_valendo(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    aut = cadastrar(cli, ha(c.sol))  # passkeys sincronizadas mantêm o contador em 0
    assert reautenticar(cli, ha(c.sol), aut, contador=0).status_code == 200
    assert reautenticar(cli, ha(c.sol), aut, contador=0).status_code == 200
    # mas voltar a 0 depois de ter usado contador > 0 é regressão
    assert reautenticar(cli, ha(c.sol), aut, contador=4).status_code == 200
    assert reautenticar(cli, ha(c.sol), aut, contador=0).status_code == 400


def test_sessao_sem_session_id_nao_reautentica(cli, repo, c, ligado) -> None:  # type: ignore[no-untyped-def]
    amr = [{"method": "password", "timestamp": int(time.time())}]
    sem = {"Authorization": f"Bearer {emitir_token(c.sol.id, extra={'amr': amr})}"}
    aut = cadastrar(cli, sem)  # login recente basta para o 1º cadastro
    d = api(cli, "post", "/autenticacao/opcoes", sem).get_json()
    r = api(
        cli,
        "post",
        "/autenticacao/confirmar",
        sem,
        {"id_desafio": d["id_desafio"], "credencial": aut.afirmar(d["opcoes"])},
    )
    assert r.status_code == 401
    assert repo.reautenticacoes == []


# -- revogação --------------------------------------------------------------------------------
def test_revogacao_exige_reautenticacao_e_e_do_dono(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    aut = cadastrar(cli, ha(c.sol))
    rid = repo.credenciais[0]["id"]
    r = api(cli, "delete", f"/{rid}", ha(c.sol))
    assert r.status_code == 403 and codigo(r) == "reautenticacao_requerida"
    reautenticar(cli, ha(c.sol), aut)
    assert api(cli, "delete", f"/{rid}", ha(c.sol)).status_code == 204
    assert api(cli, "delete", f"/{rid}", ha(c.sol)).status_code == 404  # já revogada
    assert api(cli, "get", "", ha(c.sol)).get_json()["itens"] == []
    assert repo.credenciais[0]["rev"] is True  # nunca apagada


# -- validação de entrada ---------------------------------------------------------------------
def _confirmar(cli: Any, h: Any, did: str, cred: Any) -> Any:
    return api(cli, "post", "/registro/confirmar", h, {"id_desafio": did, "credencial": cred})


def test_entrada_malformada_tem_mensagem_fixa(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    d = api(cli, "post", "/registro/opcoes", ha(c.sol), {}).get_json()
    bom = Autenticador().criar(d["opcoes"])
    segredo = "SEGREDO-NO-CORPO"

    def com(**mud: Any) -> dict[str, Any]:
        cred = {**bom, "response": dict(bom["response"])}
        for k, v in mud.items():
            if k.startswith("r_"):
                cred["response"][k[2:]] = v
            else:
                cred[k] = v
        return cred

    ruins: list[Any] = [
        "texto",
        None,
        com(id="abc+/="),  # alfabeto padrão/padding
        com(id=bom["id"], rawId="x" * 20),
        com(id="a" * 1025, rawId="a" * 1025),
        com(type="outro"),
        com(r_clientDataJSON="não é base64"),
        com(r_clientDataJSON="A" * 3000),
        com(r_attestationObject="A" * 7000),
        com(r_attestationObject=123),
        {k: v for k, v in bom.items() if k != "response"},
        com(extra=segredo),  # campos extras são descartados, não ecoados
    ]
    for cred in ruins[:-1]:
        r = _confirmar(cli, ha(c.sol), d["id_desafio"], cred)
        assert r.status_code == 400, cred
        assert codigo(r) == "passkey_invalida"
        assert r.get_json()["erro"]["mensagem"] == "Dados da passkey inválidos."
    # envelope inválido não consome o desafio
    assert repo.desafios[d["id_desafio"]]["usado"] is False
    r = api(
        cli,
        "post",
        "/registro/confirmar",
        ha(c.sol),
        {"id_desafio": d["id_desafio"], "credencial": ruins[-1], "campo_extra": segredo},
    )
    assert r.status_code == 400 and segredo not in r.get_data(as_text=True)
    for corpo in ({"credencial": bom}, {"id_desafio": "x", "credencial": bom}, [], None):
        r = api(cli, "post", "/registro/confirmar", ha(c.sol), corpo)
        assert r.status_code == 400


def test_corpo_gigante_e_recusado(cli, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    r = api(
        cli,
        "post",
        "/registro/confirmar",
        ha(c.sol),
        {"id_desafio": "x", "credencial": {"a": "z" * 20000}},
    )
    assert r.status_code == 413 and codigo(r) == "payload_grande"


def test_base64_estrito_rejeita_nao_canonico() -> None:
    from app.services.passkeys import _b64url_estrito

    assert _b64url_estrito("AAAA", 10) == b"\x00\x00\x00"
    assert _b64url_estrito("AAAB=", 10) is None  # padding
    assert _b64url_estrito("AA", 10) == b"\x00"  # canônico
    assert _b64url_estrito("AB", 10) is None  # bits sobrando: não canônico
    assert _b64url_estrito("A", 10) is None  # comprimento impossível
    assert _b64url_estrito("AA+/", 10) is None
    assert _b64url_estrito("AAAA", 3) is None
    assert _b64url_estrito(b"AAAA", 10) is None


# -- rate limit -------------------------------------------------------------------------------
def test_rate_limit_por_usuario(cli_limitado, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    h = ha(c.sol)
    codigos = [api(cli_limitado, "post", "/autenticacao/opcoes", h).status_code for _ in range(5)]
    assert codigos[:3] == [404, 404, 404] and codigos[3:] == [429, 429]
    # outro usuário tem a própria cota
    assert api(cli_limitado, "post", "/autenticacao/opcoes", ha(c.outro)).status_code == 404


# -- concorrência -----------------------------------------------------------------------------
def test_confirmacao_concorrente_so_uma_vence(cli, repo, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    aut = cadastrar(cli, ha(c.sol))
    d = api(cli, "post", "/autenticacao/opcoes", ha(c.sol)).get_json()
    corpo = {"id_desafio": d["id_desafio"], "credencial": aut.afirmar(d["opcoes"])}
    h = ha(c.sol)
    status: list[int] = []
    barreira = threading.Barrier(6)

    def tarefa() -> None:
        barreira.wait()
        status.append(
            cli.post("/api/passkeys/autenticacao/confirmar", headers=h, json=corpo).status_code
        )

    ts = [threading.Thread(target=tarefa) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(status) == [200] + [400] * 5
    assert len(repo.reautenticacoes) == 1


# -- logs -------------------------------------------------------------------------------------
def test_nenhum_material_sensivel_em_logs(cli, repo, c, ha, ligado, caplog) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    h = ha(c.sol)
    apelido = "Apelido-Secreto-do-Aparelho"
    aut = cadastrar(cli, h, apelido=apelido)
    reautenticar(cli, h, aut)
    d = api(cli, "post", "/autenticacao/opcoes", h).get_json()
    reautenticar(cli, h, aut, contador=0)  # regressão
    api(
        cli,
        "post",
        "/autenticacao/confirmar",
        h,
        {
            "id_desafio": d["id_desafio"],
            "credencial": aut.afirmar(d["opcoes"], origem="https://evil.exemplo.com"),
        },
    )
    d2 = api(cli, "post", "/autenticacao/opcoes", h).get_json()
    api(
        cli,
        "post",
        "/autenticacao/confirmar",
        h,
        {
            "id_desafio": d2["id_desafio"],
            "credencial": aut.afirmar(d2["opcoes"], chave=ec.generate_private_key(ec.SECP256R1())),
        },
    )
    api(
        cli,
        "post",
        "/autenticacao/confirmar",
        h,
        {"id_desafio": d2["id_desafio"], "credencial": aut.afirmar(d2["opcoes"])},
    )
    segredos = [
        aut.id,
        repo.credenciais[0]["chave_publica"],
        d["opcoes"]["challenge"],
        d2["opcoes"]["challenge"],
        d["id_desafio"],
        apelido,
        "evil.exemplo.com",
        ORIGEM,
        c.sol.id,
        c.sol.nome,
    ]
    for segredo in segredos:
        assert segredo not in caplog.text, segredo


# -- config e utilitários ---------------------------------------------------------------------
def test_normalizar_origem_e_coerencia() -> None:
    assert normalizar_origem_webauthn("https://App.AGU.gov.br/") == "https://app.agu.gov.br"
    assert normalizar_origem_webauthn("https://app.agu.gov.br:443") == "https://app.agu.gov.br"
    assert (
        normalizar_origem_webauthn("https://app.agu.gov.br:8443") == "https://app.agu.gov.br:8443"
    )
    assert normalizar_origem_webauthn("http://localhost:3000") == "http://localhost:3000"
    for ruim in (
        "http://app.agu.gov.br",
        "https://app.agu.gov.br/caminho",
        "https://app.agu.gov.br?x=1",
        "https://user:pw@app.agu.gov.br",
        "app.agu.gov.br",
        "ftp://app.agu.gov.br",
        "https://",
        "https://*.agu.gov.br",
    ):
        assert normalizar_origem_webauthn(ruim) is None, ruim
    assert webauthn_valido("agu.gov.br", "AGU", ["https://app.agu.gov.br"])
    assert webauthn_valido("agu.gov.br", "AGU", ["https://agu.gov.br"])
    assert webauthn_valido("localhost", "Dev", ["http://localhost:3000"])
    assert not webauthn_valido("agu.gov.br", "AGU", ["https://outra.com.br"])
    assert not webauthn_valido("agu.gov.br", "AGU", ["https://xagu.gov.br"])  # sufixo sem ponto
    assert not webauthn_valido("https://agu.gov.br", "AGU", ["https://agu.gov.br"])
    assert not webauthn_valido("agu.gov.br:8080", "AGU", ["https://agu.gov.br"])
    assert not webauthn_valido("agu.gov.br", "", ["https://agu.gov.br"])
    assert not webauthn_valido("agu.gov.br", "AGU", [])
    assert not webauthn_valido("agu.gov.br", "AGU", ["https://agu.gov.br", "http://agu.gov.br"])


def test_validar_producao_recusa_webauthn_incoerente() -> None:
    base: dict[str, Any] = {
        "supabase_url": "https://p.supabase.co",
        "supabase_service_role_key": "k",
        "jwks_url": "https://p.supabase.co/auth/v1/.well-known/jwks.json",
        "jwt_issuer": "https://p.supabase.co/auth/v1",
    }
    Config(**base).validar_producao()  # sem WEBAUTHN_*: ok, recurso só fica indisponível
    Config(
        **base,
        webauthn_rp_id="agu.gov.br",
        webauthn_rp_name="AGU",
        webauthn_origins=["https://agu.gov.br"],
    ).validar_producao()
    with pytest.raises(RuntimeError, match="WebAuthn"):
        Config(**base, webauthn_rp_id="agu.gov.br").validar_producao()
    with pytest.raises(RuntimeError) as e:
        Config(
            **base,
            webauthn_rp_id="agu.gov.br",
            webauthn_rp_name="AGU",
            webauthn_origins=["http://segredo.exemplo.com"],
        ).validar_producao()
    assert "segredo.exemplo.com" not in str(e.value)


def test_login_recente_unitario() -> None:
    agora = 1_000_000.0
    svc = ServicoPasskeys(RepoPasskeys(), _cfg(), relogio=lambda: agora)
    assert svc.login_recente({"iat": agora - 599, "amr": [{"timestamp": agora - 599}]})
    assert not svc.login_recente({"iat": agora - 601, "amr": [{"timestamp": agora - 601}]})
    assert svc.login_recente({"iat": agora + 30, "amr": [{"timestamp": agora + 30}]})  # relógio
    assert not svc.login_recente({"iat": agora + 3600, "amr": [{"timestamp": agora}]})  # iat futuro
    assert not svc.login_recente({})
    assert not svc.login_recente({"iat": "agora"})
    assert not svc.login_recente({"iat": True})
    assert not svc.login_recente(
        {"iat": agora, "amr": [{"method": "password", "timestamp": agora - 7200}]}
    )
    assert svc.login_recente(
        {"iat": agora, "amr": [{"method": "password", "timestamp": agora - 20}]}
    )
    # Falha fechado: amr ausente, vazio ou malformado nunca é login recente.
    assert not svc.login_recente({"iat": agora})
    assert not svc.login_recente({"iat": agora, "amr": []})
    assert not svc.login_recente({"iat": agora, "amr": "lixo"})
    assert not svc.login_recente({"iat": agora, "amr": [{"method": "password"}]})
    assert not svc.login_recente({"iat": agora, "amr": [None, "x", {"timestamp": "agora"}]})
    assert not svc.login_recente({"iat": agora, "amr": [{"timestamp": True}]})
    assert svc.login_recente(  # a marca mais recente vale
        {"iat": agora, "amr": [{"timestamp": agora - 7200}, {"timestamp": agora - 5}]}
    )


@pytest.mark.parametrize("amr", [None, [], "lixo", [{"method": "password"}]])
def test_cadastro_recusa_sem_amr_valido(cli, c, ha, ligado, amr) -> None:  # type: ignore[no-untyped-def]
    extra = {} if amr is None else {"amr": amr}
    h = ha(c.sol, **extra)
    if amr is None:  # ha() injeta o padrão; remove a chave de verdade
        h = {"Authorization": "Bearer " + emitir_token(c.sol.id, extra={"session_id": SESSAO})}
    r = api(cli, "post", "/registro/opcoes", h, {})
    assert r.status_code == 403 and codigo(r) == "reautenticacao_requerida"


def test_confirmar_registro_repete_a_checagem_de_identidade(cli, c, ha, ligado) -> None:  # type: ignore[no-untyped-def]
    h = ha(c.sol)
    dados = api(cli, "post", "/registro/opcoes", h, {}).get_json()
    cred = Autenticador().criar(dados["opcoes"])
    velho = ha(c.sol, amr=[{"method": "password", "timestamp": int(time.time()) - 3 * 3600}])
    r = api(
        cli,
        "post",
        "/registro/confirmar",
        velho,
        {"id_desafio": dados["id_desafio"], "credencial": cred},
    )
    assert r.status_code == 403 and codigo(r) == "reautenticacao_requerida"


def test_validar_producao_recusa_webauthn_localhost_e_http() -> None:
    base: dict[str, Any] = {
        "supabase_url": "https://p.supabase.co",
        "supabase_service_role_key": "k",
        "jwks_url": "https://p.supabase.co/auth/v1/.well-known/jwks.json",
        "jwt_issuer": "https://p.supabase.co/auth/v1",
    }
    for rp, origens in (
        ("localhost", ["http://localhost:3000"]),
        ("localhost", ["https://localhost"]),
        ("agu.gov.br", ["https://agu.gov.br", "http://localhost:3000"]),
    ):
        with pytest.raises(RuntimeError, match="WebAuthn"):
            Config(
                **base, webauthn_rp_id=rp, webauthn_rp_name="AGU", webauthn_origins=origens
            ).validar_producao()
    # Fora de produção (sem validar_producao) a configuração local segue válida.
    assert Config(
        webauthn_rp_id="localhost",
        webauthn_rp_name="Dev",
        webauthn_origins=["http://localhost:3000"],
    ).webauthn_configurado()
