from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from typing import Any

import httpx
import pytest

from app import create_app
from app.auth.jwt import VerificadorJWT
from app.config import Config
from app.extensions import limiter
from app.notificacoes import (
    EVENTOS,
    N8nWebhook,
    NotificadorNulo,
    criar_notificador,
    validar_url_webhook,
)
from app.notificacoes.n8n import montar_payload
from tests.conftest import _CHAVE, AUDIENCE, ISSUER
from tests.fakes import RepoMemoria
from tests.test_requisicoes import acao, criar

URL = "https://n8n.exemplo.gov.br/webhook/abc-segredo-url"
TOKEN = "token-super-secreto-123"  # noqa: S105
PROIBIDOS = ("Fulano de Tal", "AGU -> Fórum", "obs-sensivel", "Carro", "avaria-secreta")


def esperar(cond: Any, prazo: float = 3.0) -> bool:
    fim = time.time() + prazo
    while time.time() < fim:
        if cond():
            return True
        time.sleep(0.01)
    return bool(cond())


class Gravador:
    """Notificador de teste: guarda (evento, requisição) como o serviço entrega."""

    def __init__(self) -> None:
        self.chamadas: list[tuple[str, dict[str, Any]]] = []

    def notificar(self, evento: str, requisicao: dict[str, Any]) -> None:
        self.chamadas.append((evento, dict(requisicao)))

    def fechar(self) -> None:
        return None

    @property
    def eventos(self) -> list[str]:
        return [e for e, _ in self.chamadas]


class Quebrado:
    """Notificador que levanta: a API não pode depender dele (defesa em profundidade)."""

    def notificar(self, evento: str, requisicao: dict[str, Any]) -> None:
        raise RuntimeError("n8n fora")

    def fechar(self) -> None:
        return None


def montar_app(repo: RepoMemoria, notificador: Any, auth_admin: Any, storage: Any) -> Any:
    cfg = Config(jwt_issuer=ISSUER, jwt_audience=AUDIENCE, testing=True)
    ver = VerificadorJWT(
        issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
    )
    app = create_app(
        cfg,
        repo=repo,
        verificador=ver,
        auth_admin=auth_admin,
        armazenamento=storage,
        notificador=notificador,
    )
    limiter.reset()
    return app


@pytest.fixture
def grav() -> Gravador:
    return Gravador()


@pytest.fixture
def client_n(repo, auth_admin, storage, grav):  # type: ignore[no-untyped-def]
    return montar_app(repo, grav, auth_admin, storage).test_client()


@pytest.fixture
def cen(repo: RepoMemoria):  # type: ignore[no-untyped-def]
    class C:
        sol = repo.novo_perfil("solicitante")
        aut = repo.novo_perfil("autorizador")
        veiculo = repo.novo_veiculo()

    return C


# ------------------------------------------------------------------ desligado / config
def test_desligado_e_noop() -> None:
    assert isinstance(criar_notificador("", ""), NotificadorNulo)
    assert isinstance(criar_notificador(URL, ""), NotificadorNulo)
    assert isinstance(criar_notificador("", TOKEN), NotificadorNulo)
    n = criar_notificador("", "")
    n.notificar("requisicao.criada", {"id": "x"})
    n.fechar()


def test_ligado_cria_webhook() -> None:
    n = criar_notificador(URL, TOKEN)
    assert isinstance(n, N8nWebhook)
    assert URL not in repr(n) and TOKEN not in repr(n)
    n.fechar()


@pytest.mark.parametrize(
    "url",
    [
        "http://n8n.exemplo.gov.br/webhook",
        "https://localhost/webhook",
        "https://LOCALHOST./webhook",
        "https://127.0.0.1/webhook",
        "https://[::1]/webhook",
        "https://10.0.0.5/webhook",
        "https://192.168.1.10/webhook",
        "https://172.16.0.1/webhook",
        "https://169.254.169.254/latest",
        "https://0.0.0.0/webhook",
        "https://servidor-interno/webhook",
        "https://n8n.internal/webhook",
        "https://x.local/webhook",
        "https://user:senha@n8n.exemplo.gov.br/webhook",
        "ftp://n8n.exemplo.gov.br/",
        "n8n.exemplo.gov.br",
        "https://n8n.exemplo.gov.br:abc/webhook",
    ],
)
def test_url_insegura_e_recusada(url: str) -> None:
    with pytest.raises(RuntimeError) as exc:
        validar_url_webhook(url)
    assert "senha" not in str(exc.value) and url not in str(exc.value)


def test_url_publica_e_aceita() -> None:
    validar_url_webhook(URL)
    validar_url_webhook("https://8.8.8.8/webhook")


def test_validar_producao_recusa_url_http() -> None:
    cfg = Config(
        supabase_url="https://p.supabase.co",
        supabase_service_role_key="k",
        jwks_url="https://p.supabase.co/auth/v1/.well-known/jwks.json",
        jwt_issuer="https://p.supabase.co/auth/v1",
        n8n_webhook_url="http://10.0.0.1/x",
        n8n_webhook_token=TOKEN,
    )
    with pytest.raises(RuntimeError):
        cfg.validar_producao()
    assert TOKEN not in repr(cfg)


def test_config_le_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("N8N_WEBHOOK_URL", URL)
    monkeypatch.setenv("N8N_WEBHOOK_TOKEN", TOKEN)
    cfg = Config.from_env()
    assert cfg.n8n_webhook_url == URL and cfg.n8n_webhook_token == TOKEN
    monkeypatch.delenv("N8N_WEBHOOK_URL")
    assert Config.from_env().n8n_webhook_url == ""


# ------------------------------------------------------------------ payload
def req_exemplo() -> dict[str, Any]:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "status": "autorizada",
        "veiculo_id": "22222222-2222-2222-2222-222222222222",
        "unidade": "PU",
        "usuario_id": "pessoa-1",
        "autorizado_por": "pessoa-2",
        "nome_solicitante": "Fulano de Tal",
        "trajeto": "AGU -> Fórum",
        "observacao": "obs-sensivel",
        "placa": "ABC1D23",
        "km_saida": 10,
        "descricao_avarias": "avaria-secreta",
    }


def test_payload_minimo_por_allowlist() -> None:
    p = montar_payload("requisicao.autorizada", req_exemplo())
    assert set(p) == {
        "evento",
        "evento_id",
        "requisicao_id",
        "status",
        "veiculo_id",
        "unidade",
        "ocorrido_em",
        "versao",
    }
    assert p["versao"] == 1 and p["ocorrido_em"].endswith("+00:00")
    texto = json.dumps(p, ensure_ascii=False)
    for proibido in (*PROIBIDOS, "pessoa-1", "pessoa-2", "ABC1D23"):
        assert proibido not in texto


# ------------------------------------------------------------------ envio
def webhook(handler: Any, **kw: Any) -> N8nWebhook:
    cliente = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    kw.setdefault("backoff", 0.01)
    return N8nWebhook(URL, TOKEN, cliente=cliente, **kw)


def test_envio_assinatura_e_cabecalhos() -> None:
    recebidos: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        recebidos.append(req)
        return httpx.Response(200)

    n = webhook(h)
    n.notificar("requisicao.autorizada", req_exemplo())
    assert esperar(lambda: recebidos)
    n.fechar()
    r = recebidos[0]
    assert str(r.url) == URL and r.headers["Authorization"] == f"Bearer {TOKEN}"
    ts = r.headers["X-Timestamp"]
    assert abs(int(ts) - time.time()) < 10
    esperado = hmac.new(TOKEN.encode(), ts.encode() + b"." + r.content, hashlib.sha256).hexdigest()
    assert r.headers["X-Assinatura"] == "sha256=" + esperado
    dados = json.loads(r.content)
    assert dados["evento"] == "requisicao.autorizada"
    texto = r.content.decode()
    assert all(p not in texto for p in PROIBIDOS) and TOKEN not in texto


def test_retry_limitado_a_duas_tentativas(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    tentativas: list[int] = []

    def h(req: httpx.Request) -> httpx.Response:
        tentativas.append(1)
        return httpx.Response(503)

    n = webhook(h)
    n.notificar("requisicao.criada", req_exemplo())
    assert esperar(lambda: n.falhas == 1)
    time.sleep(0.1)
    n.fechar()
    assert len(tentativas) == 2
    log_txt = caplog.text
    for segredo in (URL, TOKEN, "abc-segredo-url", "n8n.exemplo.gov.br", "Fulano"):
        assert segredo not in log_txt


def test_sucesso_na_segunda_tentativa() -> None:
    chamadas: list[int] = []

    def h(req: httpx.Request) -> httpx.Response:
        chamadas.append(1)
        return httpx.Response(500 if len(chamadas) == 1 else 204)

    n = webhook(h)
    n.notificar("requisicao.criada", req_exemplo())
    assert esperar(lambda: len(chamadas) == 2)
    time.sleep(0.05)
    n.fechar()
    assert len(chamadas) == 2 and n.falhas == 0


def test_excecao_de_rede_nao_vaza_em_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    def h(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout(f"timeout {URL} {TOKEN}")

    n = webhook(h)
    n.notificar("requisicao.criada", req_exemplo())
    assert esperar(lambda: n.falhas == 1)
    n.fechar()
    assert "ConnectTimeout" in caplog.text
    assert URL not in caplog.text and TOKEN not in caplog.text


def test_nao_segue_redirect() -> None:
    n = N8nWebhook(URL, TOKEN)
    assert n._cliente.follow_redirects is False
    assert n._cliente.timeout.read == 3.0
    n.fechar()


def test_fila_cheia_descarta_o_mais_antigo_sem_excecao() -> None:
    liberar = threading.Event()
    corpos: list[bytes] = []

    def h(req: httpx.Request) -> httpx.Response:
        liberar.wait(5)
        corpos.append(req.content)
        return httpx.Response(200)

    n = webhook(h, max_fila=2)
    n.notificar("requisicao.criada", {**req_exemplo(), "id": "e0"})
    assert esperar(lambda: not n._fila)  # worker pegou o 0 e está bloqueado
    for i in (1, 2, 3, 4):
        n.notificar("requisicao.criada", {**req_exemplo(), "id": f"e{i}"})
    assert n.descartados == 2 and len(n._fila) == 2
    liberar.set()
    assert esperar(lambda: len(corpos) == 3)
    n.fechar()
    ids = [json.loads(c)["requisicao_id"] for c in corpos]
    assert ids == ["e0", "e3", "e4"]


def test_evento_desconhecido_e_ignorado() -> None:
    chamadas: list[int] = []
    n = webhook(lambda r: chamadas.append(1) or httpx.Response(200))
    n.notificar("requisicao.hackeada", req_exemplo())
    time.sleep(0.1)
    n.fechar()
    assert not chamadas
    assert "requisicao.criada" in EVENTOS


def test_notificar_nunca_lanca_e_fechar_e_limpo() -> None:
    n = webhook(lambda r: httpx.Response(200))
    n.notificar("requisicao.criada", object())  # type: ignore[arg-type]
    n.fechar()
    n.notificar("requisicao.criada", req_exemplo())  # após fechar: ignorado
    assert not n._fila
    assert n._thread is None or not n._thread.is_alive()


def test_envio_lento_nao_bloqueia_quem_notifica() -> None:
    liberar = threading.Event()
    n = webhook(lambda r: liberar.wait(5) and httpx.Response(200))
    t0 = time.time()
    for _ in range(5):
        n.notificar("requisicao.criada", req_exemplo())
    assert time.time() - t0 < 0.5
    liberar.set()
    n.fechar()


# ------------------------------------------------------------------ integração com a API
def test_fluxo_completo_dispara_eventos_na_ordem(client_n, auth, cen, grav):  # type: ignore[no-untyped-def]
    rid = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]
    acao(client_n, auth, cen.aut, rid, "autorizar")
    acao(client_n, auth, cen.aut, rid, "saida")
    acao(client_n, auth, cen.aut, rid, "chegada")
    assert grav.eventos == [
        "requisicao.criada",
        "requisicao.autorizada",
        "requisicao.saida",
        "requisicao.chegada",
    ]
    assert all(r["id"] == rid for _, r in grav.chamadas)


def test_negada_e_cancelada(client_n, auth, cen, grav, repo):  # type: ignore[no-untyped-def]
    r1 = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]
    acao(client_n, auth, cen.aut, r1, "negar")
    r2 = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]
    acao(client_n, auth, cen.sol, r2, "cancelar")
    assert grav.eventos == [
        "requisicao.criada",
        "requisicao.negada",
        "requisicao.criada",
        "requisicao.cancelada",
    ]


def test_transicao_que_falha_nao_notifica(client_n, auth, cen, grav):  # type: ignore[no-untyped-def]
    rid = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]
    antes = len(grav.chamadas)
    # estado inválido (saída sem autorizar), permissão negada e IDOR
    assert acao(client_n, auth, cen.aut, rid, "saida").status_code == 409
    assert acao(client_n, auth, cen.sol, rid, "autorizar").status_code == 403
    assert len(grav.chamadas) == antes


def test_criacao_recusada_nao_notifica(client_n, auth, cen, grav):  # type: ignore[no-untyped-def]
    criar(client_n, auth, cen.sol, cen.veiculo)
    n = len(grav.chamadas)
    r = criar(client_n, auth, cen.sol, cen.veiculo)  # veículo ocupado
    assert r.status_code == 409
    r = client_n.post("/api/requisicoes", headers=auth(cen.sol.id), json={"x": 1})
    assert r.status_code == 400
    assert len(grav.chamadas) == n


def test_falha_no_banco_nao_notifica(repo, auth_admin, storage, auth, cen):  # type: ignore[no-untyped-def]
    grav = Gravador()
    c = montar_app(repo, grav, auth_admin, storage).test_client()
    rid = criar(c, auth, cen.sol, cen.veiculo).get_json()["id"]
    repo.transicionar = lambda *a, **k: None  # type: ignore[method-assign]
    assert acao(c, auth, cen.aut, rid, "autorizar").status_code == 409
    assert grav.eventos == ["requisicao.criada"]


def test_payload_dos_eventos_reais_sem_dados_pessoais(client_n, auth, cen, grav):  # type: ignore[no-untyped-def]
    r = criar(client_n, auth, cen.sol, cen.veiculo, observacao="obs-sensivel")
    rid = r.get_json()["id"]
    acao(client_n, auth, cen.aut, rid, "autorizar")
    assert grav.chamadas
    for evento, req in grav.chamadas:
        texto = json.dumps(montar_payload(evento, req), ensure_ascii=False)
        for proibido in (*PROIBIDOS, cen.sol.id, cen.aut.id):
            assert proibido not in texto
        assert json.loads(texto)["unidade"] == "PU"


def test_api_nao_falha_se_n8n_estiver_fora(repo, auth_admin, storage, auth, cen):  # type: ignore[no-untyped-def]
    def h(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("fora")

    n = webhook(h)
    c = montar_app(repo, n, auth_admin, storage).test_client()
    r = criar(c, auth, cen.sol, cen.veiculo)
    assert r.status_code == 201 and r.get_json()["status"] == "solicitada"
    rid = r.get_json()["id"]
    r2 = acao(c, auth, cen.aut, rid, "autorizar")
    assert r2.status_code == 200 and r2.get_json()["status"] == "autorizada"
    assert esperar(lambda: n.falhas == 2)
    n.fechar()


def test_checklist_registrado_dispara_evento(client_n, auth, cen, grav, repo):  # type: ignore[no-untyped-def]
    from dataclasses import replace

    repo.config = replace(repo.ler_configuracoes(), checklist=True)
    rid = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]
    acao(client_n, auth, cen.aut, rid, "autorizar")
    r = client_n.post(
        f"/api/requisicoes/{rid}/checklist/saida",
        headers=auth(cen.aut.id),
        json={"combustivel": "cheio", "avarias": False, "fotos": []},
    )
    assert r.status_code == 201, r.get_json()
    assert grav.eventos[-1] == "checklist.registrado"
    texto = json.dumps(montar_payload(*grav.chamadas[-1]))
    assert "cheio" not in texto and 'avarias"' not in texto


# ------------------------------------------------------------------ evento_id
def test_evento_id_no_corpo_header_assinado_e_estavel_nas_retentativas() -> None:
    recebidos: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        recebidos.append(req)
        return httpx.Response(503 if len(recebidos) == 1 else 200)

    n = webhook(h)
    n.notificar("requisicao.criada", req_exemplo())
    assert esperar(lambda: len(recebidos) == 2)
    n.fechar()
    a, b = recebidos
    eid = json.loads(a.content)["evento_id"]
    assert uuid.UUID(eid).version == 4
    assert a.headers["X-Evento-Id"] == b.headers["X-Evento-Id"] == eid
    assert json.loads(b.content)["evento_id"] == eid
    assert a.headers["X-Entrega-Id"] == b.headers["X-Entrega-Id"]
    for r in (a, b):  # o evento_id está no corpo coberto pela assinatura
        ts = r.headers["X-Timestamp"]
        mac = hmac.new(TOKEN.encode(), ts.encode() + b"." + r.content, hashlib.sha256)
        assert r.headers["X-Assinatura"] == "sha256=" + mac.hexdigest()


def test_evento_id_difere_entre_eventos() -> None:
    recebidos: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        recebidos.append(req)
        return httpx.Response(200)

    n = webhook(h)
    n.notificar("requisicao.criada", req_exemplo())
    n.notificar("requisicao.criada", req_exemplo())
    assert esperar(lambda: len(recebidos) == 2)
    n.fechar()
    ids = {r.headers["X-Evento-Id"] for r in recebidos}
    assert len(ids) == 2


def test_desistencia_chama_callback_sem_pii() -> None:
    vistos: list[str] = []
    n = webhook(lambda req: httpx.Response(500), ao_desistir=vistos.append)
    n.notificar("requisicao.criada", req_exemplo())
    assert esperar(lambda: vistos)
    n.fechar()
    assert vistos == ["requisicao.criada"]


def test_create_app_liga_ao_desistir_ao_alerta_de_sistema(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem notificador injetado, o callback de desistência registra `n8n_falha` para os admins."""
    import app as pacote

    capturado: dict[str, Any] = {}

    def falso(url: str, token: str, ao_desistir: Any = None) -> NotificadorNulo:
        capturado["cb"] = ao_desistir
        return NotificadorNulo()

    monkeypatch.setattr(pacote, "criar_notificador", falso)
    repo = RepoMemoria()
    adm = repo.novo_perfil("admin")
    cfg = Config(jwt_issuer=ISSUER, jwt_audience=AUDIENCE, testing=True)
    ver = VerificadorJWT(
        issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
    )
    create_app(cfg, repo=repo, verificador=ver)
    assert callable(capturado["cb"])
    capturado["cb"]("requisicao.criada")
    assert ("sistema_falha_envio", "n8n_falha") in repo.chamadas_sistema
    assert [n["usuario_id"] for n in repo.notificacoes] == [adm.id]
    # um repositório que falha não derruba o worker (registrar_falha_sistema nunca propaga)
    repo.falha_sistema = RuntimeError("banco fora")
    capturado["cb"]("requisicao.criada")
