"""Web Push: cadastro/revogação, flag, IDOR, anti-SSRF, chaves e ausência de segredos em log."""

from __future__ import annotations

import base64
import logging
from typing import Any

import pytest

from app import create_app
from app.auth.jwt import VerificadorJWT
from app.config import Config
from app.errors import RequisicaoInvalida
from app.extensions import limiter
from app.modelos import Configuracoes
from app.services.push import ServicoPush, validar_endpoint
from tests.conftest import _CHAVE, AUDIENCE, ISSUER
from tests.test_push_vapid import PRIVADA, PUBLICA


def b64(n: int, fill: int = 7) -> str:
    return base64.urlsafe_b64encode(bytes([fill]) * n).rstrip(b"=").decode()


P256DH, AUTH = b64(65), b64(16)
EP = "https://fcm.googleapis.com/fcm/send/abc123-segredo"
EP2 = "https://updates.push.services.mozilla.com/wpush/v2/xyz"


def corpo(endpoint: Any = EP, p256dh: Any = P256DH, auth: Any = AUTH) -> dict[str, Any]:
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}


def _publico(host: str, porta: int) -> list[str]:
    return ["8.8.8.8"]


@pytest.fixture(autouse=True)
def _dns_publico(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.push.resolver_dns", _publico)
    # o valor padrão do parâmetro `resolvedor` é capturado na definição: força o fake nas rotas
    monkeypatch.setattr("app.notificacoes.n8n.resolver_dns", _publico)
    monkeypatch.setattr("app.rotas.push.ServicoPush", _servico_com_dns_falso)


def _servico_com_dns_falso(repo: Any, config: Config) -> ServicoPush:
    return ServicoPush(repo, config, _publico)


@pytest.fixture
def cli(repo, auth_admin, storage, avatares):  # type: ignore[no-untyped-def]
    cfg = Config(
        jwt_issuer=ISSUER,
        jwt_audience=AUDIENCE,
        testing=True,
        vapid_public_key=PUBLICA,
        vapid_private_key=PRIVADA,
        vapid_subject="mailto:ti@exemplo.gov.br",
    )
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
def ligado(repo):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(notificacao_push=True)


def post(cli: Any, auth: Any, quem: Any, json: Any) -> Any:
    return cli.post("/api/push/inscricoes", headers=auth(quem.id), json=json)


def delete(cli: Any, auth: Any, quem: Any, json: Any) -> Any:
    return cli.delete("/api/push/inscricoes", headers=auth(quem.id), json=json)


def test_exige_autenticacao(cli) -> None:  # type: ignore[no-untyped-def]
    assert cli.get("/api/push/config").status_code == 401
    assert cli.post("/api/push/inscricoes", json=corpo()).status_code == 401
    assert cli.delete("/api/push/inscricoes", json={"endpoint": EP}).status_code == 401


def test_config_desligada_e_ligada(cli, auth, repo, c) -> None:  # type: ignore[no-untyped-def]
    r = cli.get("/api/push/config", headers=auth(c.sol.id))
    assert r.get_json() == {"habilitado": False, "chave_publica": None}
    repo.config = Configuracoes(notificacao_push=True)
    r = cli.get("/api/push/config", headers=auth(c.sol.id))
    assert r.get_json() == {"habilitado": True, "chave_publica": PUBLICA}
    assert PRIVADA not in r.get_data(as_text=True)


def test_sem_vapid_nao_habilita(client, auth, repo, c) -> None:  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(notificacao_push=True)  # app padrão: sem VAPID
    r = client.get("/api/push/config", headers=auth(c.sol.id))
    assert r.get_json()["habilitado"] is False
    assert post(client, auth, c.sol, corpo()).status_code == 404


def test_flag_desligada_recusa(cli, auth, repo, c) -> None:  # type: ignore[no-untyped-def]
    r = post(cli, auth, c.sol, corpo())
    assert r.status_code == 404
    assert r.get_json()["erro"]["codigo"] == "recurso_desligado"
    assert repo.inscricoes_push == {}


def test_cadastro_e_revogacao(cli, auth, repo, c, ligado) -> None:  # type: ignore[no-untyped-def]
    assert post(cli, auth, c.sol, corpo()).status_code == 201
    assert [i["endpoint"] for i in repo.listar_inscricoes_push(c.sol.id)] == [EP]
    assert delete(cli, auth, c.sol, {"endpoint": EP}).status_code == 204
    assert repo.listar_inscricoes_push(c.sol.id) == []


def test_multiplos_dispositivos(cli, auth, repo, c, ligado) -> None:  # type: ignore[no-untyped-def]
    assert post(cli, auth, c.sol, corpo(EP)).status_code == 201
    assert post(cli, auth, c.sol, corpo(EP2)).status_code == 201
    assert len(repo.listar_inscricoes_push(c.sol.id)) == 2


def test_usuario_vem_do_jwt_nunca_do_body(cli, auth, repo, c, ligado) -> None:  # type: ignore[no-untyped-def]
    assert post(cli, auth, c.sol, corpo() | {"usuario_id": c.outro.id}).status_code == 400
    assert post(cli, auth, c.sol, corpo()).status_code == 201
    assert repo.inscricoes_push[EP]["usuario_id"] == c.sol.id


def test_idor_revogar_endpoint_de_outro_nao_afeta(cli, auth, repo, c, ligado) -> None:  # type: ignore[no-untyped-def]
    post(cli, auth, c.sol, corpo())
    r = delete(cli, auth, c.outro, {"endpoint": EP})
    assert r.status_code == 204  # resposta igual: não revela de quem é o endpoint
    assert [i["endpoint"] for i in repo.listar_inscricoes_push(c.sol.id)] == [EP]


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/fcm/send/a",
        "https://10.0.0.1/push",
        "https://127.0.0.1/push",
        "https://169.254.169.254/latest",
        "https://evil.example.com/push-segredo",
        "https://fcm.googleapis.com.evil.com/x",
        "https://evilnotify.windows.com/x",
        "https://user:senha@fcm.googleapis.com/x",
        "https://fcm.googleapis.com:8443/x",
        "ftp://fcm.googleapis.com/x",
        "https:///x",
        "",
        "https://fcm.googleapis.com/" + "a" * 3000,
    ],
)
def test_ssrf_recusado(cli, auth, repo, c, ligado, endpoint) -> None:  # type: ignore[no-untyped-def]
    r = post(cli, auth, c.sol, corpo(endpoint))
    assert r.status_code == 400
    assert r.get_json()["erro"]["codigo"] == "push_endpoint_invalido"
    assert "segredo" not in r.get_data(as_text=True) and "evil" not in r.get_data(as_text=True)
    assert repo.inscricoes_push == {}


def test_endpoint_nao_string(cli, auth, c, ligado) -> None:  # type: ignore[no-untyped-def]
    for valor in (None, 1, ["x"], {"a": 1}):
        assert post(cli, auth, c.sol, corpo(valor)).status_code == 400


def test_sufixos_permitidos_e_dns_privado() -> None:
    hosts = Config().push_endpoint_hosts
    assert validar_endpoint("https://wns2-par02p.notify.windows.com/w", hosts, _publico)
    assert validar_endpoint("https://api.push.apple.com/3/device/x", hosts, _publico)
    assert validar_endpoint("https://web.push.apple.com/x", hosts, _publico)
    # nome permitido que resolve para IP interno (DNS rebinding) é recusado
    for ip in ("10.1.2.3", "127.0.0.1", "169.254.169.254", "::1"):
        with pytest.raises(RequisicaoInvalida) as e:
            validar_endpoint("https://fcm.googleapis.com/x", hosts, lambda h, p, ip=ip: [ip])
        assert "fcm" not in str(e.value)


@pytest.mark.parametrize(
    ("p256dh", "auth_"),
    [
        (b64(64), AUTH),
        (b64(66), AUTH),
        (P256DH, b64(15)),
        (P256DH, b64(17)),
        ("não é base64!", AUTH),
        (P256DH, "a b"),
        ("", AUTH),
        (None, AUTH),
        (P256DH, 123),
    ],
)
def test_chaves_invalidas(cli, auth, repo, c, ligado, p256dh, auth_) -> None:  # type: ignore[no-untyped-def]
    r = post(cli, auth, c.sol, corpo(p256dh=p256dh, auth=auth_))
    assert r.status_code == 400
    assert r.get_json()["erro"]["codigo"] == "push_chaves_invalidas"
    assert repo.inscricoes_push == {}


def test_chaves_ausentes_ou_extras(cli, auth, repo, c, ligado) -> None:  # type: ignore[no-untyped-def]
    for b in (
        {"endpoint": EP},
        {"endpoint": EP, "keys": "x"},
        {"endpoint": EP, "keys": {"p256dh": P256DH}},
        {"endpoint": EP, "keys": {"p256dh": P256DH, "auth": AUTH, "x": "1"}},
        [],
        "texto",
    ):
        r = post(cli, auth, c.sol, b)
        assert r.status_code == 400
        assert r.get_json()["erro"]["codigo"] in {"push_chaves_invalidas", "requisicao_invalida"}
    assert repo.inscricoes_push == {}


def test_chaves_com_padding_sao_normalizadas(cli, auth, repo, c, ligado) -> None:  # type: ignore[no-untyped-def]
    com_pad = base64.urlsafe_b64encode(b"\x07" * 16).decode()
    assert com_pad.endswith("==")
    assert post(cli, auth, c.sol, corpo(auth=com_pad)).status_code == 201
    assert repo.inscricoes_push[EP]["auth"] == AUTH


def test_rate_limit(cli, auth, c, ligado) -> None:  # type: ignore[no-untyped-def]
    codigos = [post(cli, auth, c.sol, corpo()).status_code for _ in range(12)]
    assert codigos[:10] == [201] * 10
    assert 429 in codigos[10:]


def test_payload_grande_recusado(cli, auth, c, ligado) -> None:  # type: ignore[no-untyped-def]
    r = post(cli, auth, c.sol, {"endpoint": "https://fcm.googleapis.com/" + "a" * 40000})
    assert r.status_code == 413


def test_sem_endpoint_nem_chaves_em_logs(cli, auth, repo, c, ligado, caplog) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    post(cli, auth, c.sol, corpo())
    post(cli, auth, c.sol, corpo("https://evil.example.com/segredo-xyz"))
    post(cli, auth, c.sol, corpo(auth="curta"))
    delete(cli, auth, c.sol, {"endpoint": EP})
    for segredo in (EP, "abc123-segredo", "segredo-xyz", P256DH, AUTH, PRIVADA):
        assert segredo not in caplog.text


def test_config_nao_expoe_chave_privada() -> None:
    cfg = Config(vapid_public_key="PUB", vapid_private_key="PRIV-SECRETA")
    assert "PRIV-SECRETA" not in repr(cfg) and "PRIV-SECRETA" not in str(cfg)


def test_config_from_env_push(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUSH_ENDPOINT_HOSTS", "Fcm.Googleapis.com, .notify.windows.com")
    monkeypatch.setenv("PUSH_INTERVALO_SEGUNDOS", "5")
    cfg = Config.from_env()
    assert cfg.push_endpoint_hosts == ["fcm.googleapis.com", ".notify.windows.com"]
    assert cfg.push_intervalo_segundos == 5
    monkeypatch.delenv("PUSH_ENDPOINT_HOSTS")
    assert ".push.apple.com" in Config.from_env().push_endpoint_hosts


def test_admin_liga_flag(client, auth, repo, c) -> None:  # type: ignore[no-untyped-def]
    r = client.put(
        "/api/admin/configuracoes", headers=auth(c.adm.id), json={"notificacao_push": True}
    )
    assert r.status_code == 200
    item = {i["chave"]: i for i in r.get_json()["itens"]}["notificacao_push"]
    assert item["rotulo"] == "Notificações push" and item["habilitado"] is True
    assert item["exige"] == []
    assert repo.config.notificacao_push is True
