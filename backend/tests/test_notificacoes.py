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
        self.detalhes: list[tuple[str, dict[str, Any] | None]] = []

    def notificar(
        self, evento: str, requisicao: dict[str, Any], detalhes: dict[str, Any] | None = None
    ) -> None:
        self.chamadas.append((evento, dict(requisicao)))
        self.detalhes.append((evento, detalhes))

    def fechar(self) -> None:
        return None

    @property
    def eventos(self) -> list[str]:
        return [e for e, _ in self.chamadas]


class Quebrado:
    """Notificador que levanta: a API não pode depender dele (defesa em profundidade)."""

    def notificar(
        self, evento: str, requisicao: dict[str, Any], detalhes: dict[str, Any] | None = None
    ) -> None:
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
    kw.setdefault("resolvedor", lambda _h, _p: ["93.184.216.34"])  # DNS público, sem rede
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


HMAC_SEGREDO = "h" * 40  # noqa: S105


def _enviar_um(**kw: Any) -> httpx.Request:
    recebidos: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        recebidos.append(req)
        return httpx.Response(200)

    n = webhook(h, **kw)
    n.notificar("requisicao.autorizada", req_exemplo())
    assert esperar(lambda: recebidos)
    n.fechar()
    return recebidos[0]


def _assin(chave: str, r: httpx.Request) -> str:
    ts = r.headers["X-Timestamp"]
    mac = hmac.new(chave.encode(), ts.encode() + b"." + r.content, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def test_assinatura_usa_segredo_hmac_e_bearer_segue_token() -> None:
    r = _enviar_um(hmac_secret=HMAC_SEGREDO)
    assert r.headers["Authorization"] == f"Bearer {TOKEN}"
    assert r.headers["X-Assinatura"] == _assin(HMAC_SEGREDO, r)
    assert r.headers["X-Assinatura"] != _assin(TOKEN, r)
    assert HMAC_SEGREDO not in r.content.decode()


def test_assinatura_cai_no_token_sem_segredo_hmac() -> None:
    r = _enviar_um()
    assert r.headers["X-Assinatura"] == _assin(TOKEN, r)
    assert r.headers["Authorization"] == f"Bearer {TOKEN}"


def test_aviso_unico_sem_segredo_hmac_e_sem_valores(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    criar_notificador(URL, TOKEN).fechar()
    avisos = [x for x in caplog.records if "N8N_HMAC_SECRET" in x.getMessage()]
    assert len(avisos) == 1
    assert TOKEN not in caplog.text and URL not in caplog.text


def test_sem_aviso_com_segredo_distinto_e_aviso_se_igual(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    criar_notificador(URL, TOKEN, hmac_secret=HMAC_SEGREDO).fechar()
    assert "N8N_HMAC_SECRET" not in caplog.text
    n = criar_notificador(URL, TOKEN, hmac_secret=TOKEN)  # só avisa, não falha
    n.fechar()
    assert "igual" in caplog.text and TOKEN not in caplog.text


def test_segredo_hmac_fora_de_repr() -> None:
    n = criar_notificador(URL, TOKEN, hmac_secret=HMAC_SEGREDO)
    assert HMAC_SEGREDO not in repr(n) and HMAC_SEGREDO not in str(vars(n).get("_url", ""))
    n.fechar()
    cfg = Config(n8n_webhook_token=TOKEN, n8n_hmac_secret=HMAC_SEGREDO)
    assert HMAC_SEGREDO not in repr(cfg) and TOKEN not in repr(cfg)


def test_config_hmac_le_ambiente_e_valida_tamanho(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("N8N_HMAC_SECRET", f" {HMAC_SEGREDO} ")
    assert Config.from_env().n8n_hmac_secret == HMAC_SEGREDO
    base = {
        "supabase_url": "https://p.supabase.co",
        "supabase_service_role_key": "k",
        "jwks_url": "https://p.supabase.co/auth/v1/.well-known/jwks.json",
        "jwt_issuer": "https://p.supabase.co/auth/v1",
    }
    Config(**base, n8n_hmac_secret=HMAC_SEGREDO).validar_producao()
    Config(**base).validar_producao()  # opcional
    with pytest.raises(RuntimeError) as exc:
        Config(**base, n8n_hmac_secret="curto").validar_producao()  # noqa: S106
    assert "curto" not in str(exc.value)


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


def test_payload_dos_demais_eventos_continua_minimo_sem_dados_pessoais(client_n, auth, cen, grav):  # type: ignore[no-untyped-def]
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

    def falso(
        url: str,
        token: str,
        ao_desistir: Any = None,
        hmac_secret: str = "",
        hosts_permitidos: Any = (),
    ) -> NotificadorNulo:
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


# ------------------------------------------------------------------ detalhes de saída/chegada
def _fluxo_ate_chegada(client_n, auth, cen):  # type: ignore[no-untyped-def]
    rid = criar(client_n, auth, cen.sol, cen.veiculo, observacao="obs-sensivel").get_json()["id"]
    acao(client_n, auth, cen.aut, rid, "autorizar")
    assert acao(client_n, auth, cen.aut, rid, "saida").status_code == 200
    assert acao(client_n, auth, cen.aut, rid, "chegada").status_code == 200
    return rid


def test_saida_e_chegada_levam_detalhes_completos_e_demais_eventos_nao(client_n, auth, cen, grav):  # type: ignore[no-untyped-def]
    rid = _fluxo_ate_chegada(client_n, auth, cen)
    por_evento = dict(grav.detalhes)
    assert por_evento["requisicao.criada"] is None
    assert por_evento["requisicao.autorizada"] is None
    for evento, momento in (("requisicao.saida", "saida"), ("requisicao.chegada", "chegada")):
        d = por_evento[evento]
        assert d is not None and d["momento"] == momento
        assert d["requisicao"]["id"] == rid and d["requisicao"]["observacao"] == "obs-sensivel"
        assert d["requisicao"]["trajeto"] == "AGU -> Fórum"
        assert d["veiculo"]["id"] == cen.veiculo and "descricao" in d["veiculo"]
        assert d["solicitante"]["id"] == cen.sol.id and d["solicitante"]["nome"]
        assert d["autorizado_por"]["id"] == cen.aut.id
        assert d["saida_registrada_por"]["id"] == cen.aut.id
        assert "saida_real_em" in d and "chegada_real_em" in d
        payload = montar_payload(evento, {"id": rid}, detalhes=d)
        assert payload["versao"] == 2 and payload["detalhes"] == d
        assert TOKEN not in json.dumps(payload, default=str)
    assert por_evento["requisicao.chegada"]["chegada_real_em"] is not None
    assert por_evento["requisicao.chegada"]["chegada_registrada_por"]["id"] == cen.aut.id
    d = por_evento["requisicao.saida"]  # recursos desligados => campos ausentes
    for ausente in ("km_saida", "km_chegada", "localizacao", "checklist", "motorista"):
        assert ausente not in d


def test_detalhes_respeitam_recursos_ligados(client_n, auth, cen, grav, repo):  # type: ignore[no-untyped-def]
    from dataclasses import replace

    repo.config = replace(
        repo.ler_configuracoes(), checklist=True, quilometragem=True, autorizador_km=True
    )
    rid = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]
    acao(client_n, auth, cen.aut, rid, "autorizar")
    r = client_n.post(
        f"/api/requisicoes/{rid}/checklist/saida",
        headers=auth(cen.aut.id),
        json={
            "combustivel": "cheio",
            "avarias": True,
            "descricao_avarias": "risco-na-porta",
            "fotos": [],
        },
    )
    assert r.status_code == 201, r.get_json()
    r = client_n.post(
        f"/api/requisicoes/{rid}/saida", headers=auth(cen.aut.id), json={"km_saida": 100}
    )
    assert r.status_code == 200, r.get_json()
    d = dict(grav.detalhes)["requisicao.saida"]
    assert d["km_saida"] == 100 and d["km_chegada"] is None
    ck = d["checklist"]
    assert ck["combustivel"] == "cheio" and ck["avarias"] is True
    assert ck["descricao_avarias"] == "risco-na-porta" and ck["quantidade_fotos"] == 0
    assert "fotos" not in ck and "caminho" not in json.dumps(d)


def test_toda_chave_da_requisicao_vai_em_detalhes_menos_denylist(client_n, auth, cen, grav):  # type: ignore[no-untyped-def]
    from app.services.requisicoes import DENYLIST_WEBHOOK_REQ

    # Sem recursos opcionais ligados, km e motorista saem da linha por regra de flag (ver abaixo).
    por_flag = {"km_saida", "km_chegada", "motorista_id"}

    _fluxo_ate_chegada(client_n, auth, cen)
    vistos = 0
    for (evento, req), (_, d) in zip(grav.chamadas, grav.detalhes, strict=True):
        if evento not in {"requisicao.saida", "requisicao.chegada"}:
            continue
        vistos += 1
        esperado = {k for k in req if not DENYLIST_WEBHOOK_REQ.search(k)} - por_flag
        assert esperado <= set(d["requisicao"]), esperado - set(d["requisicao"])
    assert vistos == 2


def test_coluna_nova_passa_sozinha_ao_webhook(client_n, auth, cen, grav, repo):  # type: ignore[no-untyped-def]
    rid = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]
    acao(client_n, auth, cen.aut, rid, "autorizar")
    repo.requisicoes[rid]["coluna_futura"] = "valor-novo"
    acao(client_n, auth, cen.aut, rid, "saida")
    assert dict(grav.detalhes)["requisicao.saida"]["requisicao"]["coluna_futura"] == "valor-novo"


def test_falha_ao_montar_detalhes_nao_quebra_resposta(client_n, auth, cen, grav, repo):  # type: ignore[no-untyped-def]
    rid = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]
    acao(client_n, auth, cen.aut, rid, "autorizar")
    original = repo.nomes_perfis
    chamadas = {"n": 0}

    def uma_vez(ids: set[str]) -> dict[str, str]:
        chamadas["n"] += 1
        if chamadas["n"] == 1:  # 1ª chamada = montagem dos detalhes; a apresentação segue normal
            raise RuntimeError("Fulano de Tal quebrou")
        return original(ids)

    repo.nomes_perfis = uma_vez  # type: ignore[method-assign]
    r = acao(client_n, auth, cen.aut, rid, "saida")
    assert r.status_code == 200 and r.get_json()["status"] == "em_uso"
    assert grav.eventos[-1] == "requisicao.saida" and grav.detalhes[-1][1] is None


def test_assinatura_hmac_cobre_corpo_com_detalhes_e_reentrega_mantem_evento_id() -> None:
    recebidos: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        recebidos.append(req)
        return httpx.Response(503 if len(recebidos) == 1 else 200)

    n = webhook(h)
    detalhes = {"momento": "saida", "requisicao": {"trajeto": "AGU -> Fórum"}}
    n.notificar("requisicao.saida", req_exemplo(), detalhes)
    assert esperar(lambda: len(recebidos) == 2)
    n.fechar()
    a, b = recebidos
    corpo = json.loads(a.content)
    assert corpo["versao"] == 2 and corpo["detalhes"] == detalhes
    assert a.content == b.content
    assert json.loads(b.content)["evento_id"] == corpo["evento_id"] == a.headers["X-Evento-Id"]
    for r in (a, b):
        assert r.headers["X-Assinatura"] == _assin(TOKEN, r)
    assert TOKEN not in a.content.decode()


def test_payload_sem_detalhes_segue_versao_1() -> None:
    p = montar_payload("requisicao.saida", req_exemplo())
    assert p["versao"] == 1 and "detalhes" not in p


def test_denylist_por_substring_recursiva_e_chave_inofensiva_passa(client_n, auth, cen, grav, repo):  # type: ignore[no-untyped-def]
    rid = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]
    acao(client_n, auth, cen.aut, rid, "autorizar")
    repo.requisicoes[rid].update(
        {
            "email_solicitante": "x@y.gov.br",
            "foto_url": "https://x/f.png",
            "token_convite": "abc",
            "Telefone_Contato": "999",
            "extra": {"cpf_motorista": "1", "ok": [{"api_key": "k", "valor": 2}]},
            "campo_novo_inofensivo": "passa",
        }
    )
    acao(client_n, auth, cen.aut, rid, "saida")
    r = dict(grav.detalhes)["requisicao.saida"]["requisicao"]
    for removida in ("email_solicitante", "foto_url", "token_convite", "Telefone_Contato"):
        assert removida not in r
    assert r["campo_novo_inofensivo"] == "passa"
    assert r["extra"] == {"ok": [{"valor": 2}]}


def test_flags_desligadas_removem_km_e_motorista_da_linha_mesmo_gravados(  # type: ignore[no-untyped-def]
    client_n, auth, cen, grav, repo
):
    rid = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]
    acao(client_n, auth, cen.aut, rid, "autorizar")
    repo.requisicoes[rid].update({"km_saida": 10, "km_chegada": 20, "motorista_id": cen.aut.id})
    assert not repo.ler_configuracoes().quilometragem and not repo.ler_configuracoes().motorista
    acao(client_n, auth, cen.aut, rid, "saida")
    r = dict(grav.detalhes)["requisicao.saida"]["requisicao"]
    assert not {"km_saida", "km_chegada", "motorista_id"} & set(r)


def test_flags_ligadas_mantem_km_e_motorista_na_linha(client_n, auth, cen, grav, repo):  # type: ignore[no-untyped-def]
    from dataclasses import replace

    repo.config = replace(repo.ler_configuracoes(), quilometragem=True, motorista=True)
    rid = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]
    acao(client_n, auth, cen.aut, rid, "autorizar")
    repo.requisicoes[rid].update({"km_saida": 10, "motorista_id": None})
    acao(client_n, auth, cen.aut, rid, "saida")
    r = dict(grav.detalhes)["requisicao.saida"]["requisicao"]
    assert "km_saida" in r and "km_chegada" in r and "motorista_id" in r


def test_log_de_detalhes_indisponiveis_sem_pii(  # type: ignore[no-untyped-def]
    client_n, auth, cen, grav, repo, caplog
):
    caplog.set_level(logging.DEBUG)
    rid = criar(client_n, auth, cen.sol, cen.veiculo).get_json()["id"]
    acao(client_n, auth, cen.aut, rid, "autorizar")
    original = repo.nomes_perfis
    chamadas = {"n": 0}

    def uma_vez(ids: set[str]) -> dict[str, str]:
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise RuntimeError("Fulano de Tal AGU -> Fórum obs-sensivel")
        return original(ids)

    repo.nomes_perfis = uma_vez  # type: ignore[method-assign]
    assert acao(client_n, auth, cen.aut, rid, "saida").status_code == 200
    avisos = [x for x in caplog.records if "detalhes indisponiveis" in x.getMessage()]
    assert len(avisos) == 1 and avisos[0].exc_info is None
    assert "evento=requisicao.saida (RuntimeError)" in avisos[0].getMessage()
    for pii in (*PROIBIDOS, cen.sol.id, cen.aut.id, "Traceback"):
        assert pii not in caplog.text
