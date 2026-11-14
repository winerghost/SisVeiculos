"""Web Push: redirect, allowlist no envio, IP fixo, erros definitivos x transitórios, VAPID."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.config import Config, vapid_valido
from app.modelos import Configuracoes
from app.notificacoes import push as modulo_push
from app.notificacoes.despachante import Despachante
from app.notificacoes.push import (
    EnviadorWebPush,
    FalhaPush,
    _AdaptadorIpFixo,
    _SessaoSemRedirect,
    endpoint_publico,
)
from app.services.push import ServicoPush


def _b64(x: bytes) -> str:
    return base64.urlsafe_b64encode(x).rstrip(b"=").decode()


def par_vapid() -> tuple[str, str]:
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64(pub), _b64(k.private_numbers().private_value.to_bytes(32, "big"))


PUBLICA, PRIVADA = par_vapid()  # gerado a cada execução: nada fixo no repositório
SUBJECT = "mailto:ti@exemplo.gov.br"
EP = "https://push.exemplo-servico.com/send/ABC"


def _publico(host: str, porta: int) -> list[str]:
    return ["8.8.8.8"]


def _cfg() -> Any:
    return SimpleNamespace(
        vapid_private_key=PRIVADA,
        vapid_subject=SUBJECT,
        push_endpoint_hosts=[".exemplo-servico.com"],
    )


def _insc(endpoint: str = EP) -> dict[str, str]:
    return {"endpoint": endpoint, "p256dh": PUBLICA, "auth": _b64(b"a" * 16)}


# -- M1: redirect nunca é seguido -------------------------------------------------------------
def test_302_vira_falha_redirect_e_nao_vai_ao_location(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("pywebpush")
    visitados: list[str] = []

    def envio_falso(self: Any, request: Any, *a: Any, **kw: Any) -> requests.Response:
        visitados.append(request.url)
        r = requests.Response()
        r.status_code = 302
        r.headers["Location"] = "http://169.254.169.254/latest"
        r.url = request.url
        r.request = request
        return r

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", envio_falso)
    with pytest.raises(FalhaPush) as e:
        EnviadorWebPush(_cfg(), _publico).enviar(_insc(), {"titulo": "t"})
    assert e.value.codigo == "redirect" and not e.value.definitiva
    assert len(visitados) == 1 and "169.254" not in visitados[0]


def test_sessao_forca_sem_redirect() -> None:
    s = _SessaoSemRedirect()
    assert s.max_redirects == 0 and s.trust_env is False


# -- B1: conecta ao IP validado, mantendo o Host ---------------------------------------------
def test_adaptador_fixa_ip_e_host(monkeypatch: pytest.MonkeyPatch) -> None:
    vistos: list[Any] = []
    monkeypatch.setattr(
        requests.adapters.HTTPAdapter, "send", lambda self, req, *a, **k: vistos.append(req)
    )
    req = requests.Request("POST", EP, data=b"x").prepare()
    _AdaptadorIpFixo("push.exemplo-servico.com", "8.8.8.8").send(req)
    assert vistos[0].url == "https://8.8.8.8/send/ABC"
    assert vistos[0].headers["Host"] == "push.exemplo-servico.com"
    ad = _AdaptadorIpFixo("push.exemplo-servico.com", "2001:4860::1")
    ad.send(requests.Request("POST", EP).prepare())
    assert vistos[1].url.startswith("https://[2001:4860::1]/")
    kw = ad.poolmanager.connection_pool_kw
    assert kw["server_hostname"] == kw["assert_hostname"] == "push.exemplo-servico.com"


def test_envio_usa_o_ip_resolvido(monkeypatch: pytest.MonkeyPatch) -> None:
    pywebpush = pytest.importorskip("pywebpush")
    vistos: dict[str, Any] = {}

    def falso(**kw: Any) -> None:
        vistos["adaptador"] = kw["requests_session"].get_adapter("https://x")

    monkeypatch.setattr(pywebpush, "webpush", falso)
    EnviadorWebPush(_cfg(), _publico).enviar(_insc(), {"titulo": "t"})
    assert vistos["adaptador"]._ip == "8.8.8.8"


# -- B2: allowlist reaplicada no envio -------------------------------------------------------
def test_allowlist_no_envio() -> None:
    hosts = [".exemplo-servico.com"]
    assert endpoint_publico(EP, hosts, _publico)
    assert not endpoint_publico("https://evil.example.org/x", hosts, _publico)
    assert not endpoint_publico("http://push.exemplo-servico.com/x", hosts, _publico)
    assert not endpoint_publico("https://push.exemplo-servico.com:8443/x", hosts, _publico)
    assert not endpoint_publico(EP, hosts, lambda h, p: ["10.0.0.1"])


def test_enviador_recusa_host_fora_da_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    pywebpush = pytest.importorskip("pywebpush")
    monkeypatch.setattr(pywebpush, "webpush", lambda **k: pytest.fail("não deveria enviar"))
    with pytest.raises(FalhaPush) as e:
        EnviadorWebPush(_cfg(), _publico).enviar(_insc("https://evil.example.org/x"), {})
    assert e.value.codigo == "destino_invalido" and not e.value.definitiva


# -- M2: só erros definitivos contam falha ----------------------------------------------------
@pytest.mark.parametrize(
    ("status", "definitiva"),
    [(400, True), (401, True), (403, True), (413, True), (429, False), (500, False), (503, False)],
)
def test_classificacao_http(monkeypatch: pytest.MonkeyPatch, status: int, definitiva: bool) -> None:
    pywebpush = pytest.importorskip("pywebpush")

    def falso(**kw: Any) -> None:
        raise pywebpush.WebPushException(EP, response=SimpleNamespace(status_code=status))

    monkeypatch.setattr(pywebpush, "webpush", falso)
    with pytest.raises(FalhaPush) as e:
        EnviadorWebPush(_cfg(), _publico).enviar(_insc(), {})
    assert e.value.definitiva is definitiva and not e.value.expirada


@pytest.mark.parametrize("status", [404, 410])
def test_404_410_expiram(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    pywebpush = pytest.importorskip("pywebpush")

    def falso(**kw: Any) -> None:
        raise pywebpush.WebPushException(EP, response=SimpleNamespace(status_code=status))

    monkeypatch.setattr(pywebpush, "webpush", falso)
    with pytest.raises(FalhaPush) as e:
        EnviadorWebPush(_cfg(), _publico).enviar(_insc(), {})
    assert e.value.expirada and e.value.definitiva


def test_timeout_nao_e_definitivo(monkeypatch: pytest.MonkeyPatch) -> None:
    pywebpush = pytest.importorskip("pywebpush")

    def falso(**kw: Any) -> None:
        raise requests.Timeout(EP)

    monkeypatch.setattr(pywebpush, "webpush", falso)
    with pytest.raises(FalhaPush) as e:
        EnviadorWebPush(_cfg(), _publico).enviar(_insc(), {})
    assert not e.value.definitiva and EP not in str(e.value)


def test_despachante_410_expira_e_503_nao_conta(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.notificacoes import despachante
    from tests.test_push_envio import EnviadorFalso, RepoPush, insc, montar

    monkeypatch.setattr(despachante, "endpoint_publico", lambda e, h=None: True)

    repo = RepoPush()
    repo.nova(usuario="u1")
    repo.nova(usuario="u2")
    repo.nova(usuario="u3")
    repo.inscricoes = {"u1": [insc("i1")], "u2": [insc("i2")], "u3": [insc("i3")]}
    env = EnviadorFalso(
        {
            "i1": FalhaPush("inscricao_expirada", expirada=True),
            "i2": FalhaPush("http_503"),
            "i3": FalhaPush("http_403", definitiva=True),
        }
    )
    montar(repo, env).processar_push_uma_vez()
    assert ("i1", False, True) in repo.resultados
    assert all(r[0] != "i2" for r in repo.resultados)
    assert ("i3", False, False) in repo.resultados
    assert repo.status["id-2"] == ("falhou", "http_503")


# -- B3: allowlist exige 2+ rótulos ------------------------------------------------------------
def _validar(**kw: Any) -> None:
    Config(
        supabase_url="https://x.supabase.co",
        supabase_service_role_key="k",
        jwks_url="https://x.supabase.co/j",
        jwt_issuer="https://x.supabase.co/auth/v1",
        **kw,
    ).validar_producao()


@pytest.mark.parametrize("host", [".com", ".net", "com", "..", ".a..b", "a.", "*.x.com"])
def test_hosts_com_menos_de_dois_rotulos_recusados(host: str) -> None:
    with pytest.raises(RuntimeError):
        _validar(push_endpoint_hosts=[host])


def test_hosts_validos_aceitos() -> None:
    _validar(push_endpoint_hosts=[".push.apple.com", "fcm.googleapis.com"])


# -- B4: VAPID válido -------------------------------------------------------------------------
def test_vapid_valido() -> None:
    assert vapid_valido(PUBLICA, PRIVADA, SUBJECT)
    assert vapid_valido(PUBLICA, PRIVADA, "https://agu.gov.br/contato")


@pytest.mark.parametrize(
    "subject", ["", "ti@exemplo.gov.br", "http://x.com", "mailto:", "https://", "mailto:a b"]
)
def test_vapid_subject_invalido(subject: str) -> None:
    assert not vapid_valido(PUBLICA, PRIVADA, subject)


def test_vapid_chaves_invalidas() -> None:
    assert not vapid_valido("curta", PRIVADA, SUBJECT)
    assert not vapid_valido(PUBLICA, "curta", SUBJECT)
    assert not vapid_valido(_b64(b"\x05" + b"\x01" * 64), PRIVADA, SUBJECT)
    assert not vapid_valido("", PRIVADA, SUBJECT)


def test_validar_producao_vapid_invalida_sem_eco() -> None:
    with pytest.raises(RuntimeError) as e:
        _validar(vapid_public_key="SEGREDO-PUB", vapid_private_key="SEGREDO-PRIV")
    assert "SEGREDO" not in str(e.value)
    _validar(vapid_public_key=PUBLICA, vapid_private_key=PRIVADA, vapid_subject=SUBJECT)
    _validar()  # push não configurado continua permitido


def test_config_so_habilita_com_subject_valido(repo: Any, c: Any) -> None:
    repo.config = Configuracoes(notificacao_push=True)
    sem_subject = Config(vapid_public_key=PUBLICA, vapid_private_key=PRIVADA)
    assert ServicoPush(repo, sem_subject).configuracao(c.sol)["habilitado"] is False
    ok = Config(vapid_public_key=PUBLICA, vapid_private_key=PRIVADA, vapid_subject=SUBJECT)
    assert ServicoPush(repo, ok).configuracao(c.sol) == {
        "habilitado": True,
        "chave_publica": PUBLICA,
    }


def test_despachante_alinhado_ao_servico() -> None:
    from tests.test_push_envio import EnviadorFalso, RepoPush, cfg

    conf = cfg()
    conf.vapid_subject = "sem-esquema"
    d = Despachante(RepoPush(), None, conf, enviador_push=EnviadorFalso())  # type: ignore[arg-type]
    assert not d.push_configurado()


def test_constantes_definitivas() -> None:
    assert {400, 401, 403, 404, 410, 413} == modulo_push.HTTP_DEFINITIVOS
