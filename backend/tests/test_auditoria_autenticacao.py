"""Auditoria de segurança (A-01, A-02, A-03, A-06, A-07, S-04, S-05, S-08, I-07)."""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Any

import httpx
import pytest

from app.auth.jwt import JWKSIndisponivel, ResolvedorJWKS
from app.config import Config
from app.errors import ErroInterno, UltimoAdmin
from app.extensions import ip_cliente
from app.modelos import Configuracoes
from app.notificacoes.n8n import (
    N8nWebhook,
    exigir_destino_publico,
    validar_url_webhook,
)
from app.repositorios.supabase_repo import SupabaseRepositorio
from app.services.admin_usuarios import ServicoUsuarios
from tests.fakes_admin import AuthAdminMemoria, erro_db
from tests.test_seguranca import _claims, _ClienteJWKS, _jwks, _Relogio, _tok

URL = "https://n8n.exemplo.gov.br/webhook/abc"
TOKEN = "token-super-secreto-123"  # noqa: S105
HMAC = "h" * 40
BASE_PROD: dict[str, Any] = {
    "supabase_url": "https://x.supabase.co",
    "supabase_service_role_key": "k",
    "jwks_url": "https://x.supabase.co/auth/v1/.well-known/jwks.json",
    "jwt_issuer": "https://x.supabase.co/auth/v1",
}


def _cod(r) -> str:  # type: ignore[no-untyped-def]
    return r.get_json()["erro"]["codigo"]


# ======================================================================= A-01 fail-closed
class _Tabela:
    def __init__(self, dados: list[dict[str, Any]]) -> None:
        self.dados = dados

    def select(self, *_a: Any, **_k: Any) -> _Tabela:
        return self

    def limit(self, *_a: Any) -> _Tabela:
        return self

    def execute(self) -> Any:
        return type("R", (), {"data": self.dados})()


class _Cliente:
    def __init__(self, dados: list[dict[str, Any]]) -> None:
        self.dados = dados

    def table(self, _nome: str) -> _Tabela:
        return _Tabela(self.dados)


def test_ler_configuracoes_sem_linha_levanta_503() -> None:
    repo = SupabaseRepositorio.__new__(SupabaseRepositorio)
    repo._c = _Cliente([])  # type: ignore[attr-defined]
    with pytest.raises(ErroInterno) as e:
        repo.ler_configuracoes()
    assert e.value.status == 503


def test_mfa_nao_e_dispensado_sem_linha_de_configuracoes(app, client, repo, auth):  # type: ignore[no-untyped-def]
    """Sem a linha, autenticar() devolve 503 (nunca deixa passar sem segundo fator)."""
    p = repo.novo_perfil("admin")

    def sem_linha() -> Configuracoes:
        raise ErroInterno("Configurações indisponíveis. Tente novamente.")

    repo.ler_configuracoes = sem_linha  # type: ignore[method-assign]
    r = client.get("/api/admin/usuarios", headers=auth(p.id))
    assert r.status_code == 503 and _cod(r) == "indisponivel"


# ======================================================================= A-02 / A-03 JWKS
def test_jwks_busca_roda_fora_do_lock_de_estado() -> None:
    """Quem tem a chave em cache não espera uma busca lenta de outro kid."""
    entrou, liberar = threading.Event(), threading.Event()

    class Lento(_ClienteJWKS):
        def fetch_data(self) -> dict[str, Any]:
            if self.chamadas >= 1:
                entrou.set()
                liberar.wait(5)
            return super().fetch_data()

    cliente = Lento(_jwks("k1"))
    r = ResolvedorJWKS("https://x.example/jwks", cliente=cliente, cooldown=0)  # type: ignore[arg-type]
    r(_tok(_claims("a"), kid="k1"))  # popula o cache
    t = threading.Thread(target=lambda: _ignora(r, _tok(_claims("a"), kid="novo")))
    t.start()
    assert entrou.wait(5)
    inicio = time.monotonic()
    assert r(_tok(_claims("a"), kid="k1"))  # cache hit durante a busca
    assert time.monotonic() - inicio < 1
    liberar.set()
    t.join(5)


def _ignora(r: ResolvedorJWKS, tok: str) -> None:
    with contextlib.suppress(Exception):
        r(tok)


def test_jwks_buscas_concorrentes_do_mesmo_kid_fazem_uma_so_chamada() -> None:
    cliente = _ClienteJWKS(_jwks("k1"))
    r = ResolvedorJWKS("https://x.example/jwks", cliente=cliente)  # type: ignore[arg-type]
    barreira = threading.Barrier(6)

    def agir() -> None:
        barreira.wait()
        r(_tok(_claims("a"), kid="k1"))

    ts = [threading.Thread(target=agir) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert cliente.chamadas == 1  # double-check: as demais acham a chave no cache


def test_jwks_cooldown_menor_apos_falha_e_cheio_apos_sucesso() -> None:
    rel = _Relogio()
    cliente = _ClienteJWKS(_jwks("k1"), falha=True)
    r = ResolvedorJWKS(  # type: ignore[arg-type]
        "https://x.example/jwks",
        cliente=cliente,
        cooldown=60,
        cooldown_falha=5,
        relogio=rel,
    )
    tok = _tok(_claims("a"), kid="k1")
    with pytest.raises(JWKSIndisponivel):
        r(tok)
    with pytest.raises(JWKSIndisponivel):  # dentro do cooldown curto: sem nova chamada
        r(tok)
    assert cliente.chamadas == 1
    rel.t += 6  # passou o cooldown curto (e não o de 60 s)
    cliente.falha = False
    assert r(tok)  # recupera logo, sem esperar 60 s
    assert cliente.chamadas == 2
    with pytest.raises(Exception, match="kid desconhecido"):  # sucesso => cooldown cheio
        r(_tok(_claims("a"), kid="outro"))
    rel.t += 10
    with pytest.raises(Exception, match="kid desconhecido"):
        r(_tok(_claims("a"), kid="outro"))
    assert cliente.chamadas == 2


def test_falha_de_infra_do_jwks_e_503_e_token_invalido_continua_401(repo):  # type: ignore[no-untyped-def]
    from tests.test_seguranca import _app_com_resolvedor, _bearer, _resolvedor

    app = _app_com_resolvedor(repo, _resolvedor(_ClienteJWKS(_jwks("k1"), falha=True)))
    p = repo.novo_perfil()
    r = app.test_client().get("/api/eu", headers=_bearer(_tok(_claims(p.id))))
    assert r.status_code == 503 and _cod(r) == "indisponivel"
    assert "jwks" not in r.get_data(as_text=True).lower()

    ok = _app_com_resolvedor(repo, _resolvedor(_ClienteJWKS(_jwks("k1"))))
    r2 = ok.test_client().get("/api/eu", headers=_bearer("lixo.lixo.lixo"))
    assert r2.status_code == 401 and _cod(r2) == "nao_autenticado"
    r3 = ok.test_client().get("/api/eu", headers=_bearer(_tok(_claims(p.id), kid="outro")))
    assert r3.status_code == 401


# ======================================================================= A-06 role antes
def test_role_e_checado_antes_de_mfa_e_do_registro_de_acesso(app, client, repo, auth):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(mfa=True)
    registros: list[str] = []

    class Espiao:
        def registrar(self, usuario_id: str, *_a: Any, **_k: Any) -> None:
            registros.append(usuario_id)

    app.extensions["sisveiculos"].registrador_acessos = Espiao()
    leituras: list[int] = []
    original = repo.ler_configuracoes
    repo.ler_configuracoes = lambda: (leituras.append(1), original())[1]  # type: ignore[method-assign]
    sol = repo.novo_perfil("solicitante")
    aut = repo.novo_perfil("autorizador")
    for quem in (sol, aut):
        r = client.get("/api/admin/usuarios", headers=auth(quem.id))
        assert r.status_code == 403 and _cod(r) == "sem_permissao"
    assert registros == [] and leituras == []  # sem consulta extra nem rastro de acesso

    adm = repo.novo_perfil("admin")  # admin sem aal2 continua barrado pelo MFA
    r = client.get("/api/admin/usuarios", headers=auth(adm.id))
    assert r.status_code == 403 and _cod(r) == "mfa_requerido"
    assert registros == []


def test_admin_com_aal2_e_registrado(client, repo, auth_aal2, app):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(mfa=True)
    registros: list[str] = []

    class Espiao:
        def registrar(self, usuario_id: str, *_a: Any, **_k: Any) -> None:
            registros.append(usuario_id)

    app.extensions["sisveiculos"].registrador_acessos = Espiao()
    adm = repo.novo_perfil("admin")
    r = client.get("/api/admin/usuarios", headers=auth_aal2(adm.id))
    assert r.status_code == 200 and registros == [adm.id]


# ======================================================================= A-07 último admin
def _servico(repo: Any) -> ServicoUsuarios:
    return ServicoUsuarios(repo, AuthAdminMemoria(repo))


@pytest.mark.parametrize("operacao", ["desativar", "rebaixar"])
def test_ultimo_admin_corrida_no_servico_sobra_um(repo, operacao):  # type: ignore[no-untyped-def]
    """Os dois passam da pré-checagem do app (barreira) e só o gatilho do banco (fake com o
    mesmo contrato AG009) impede zerar os admins."""
    a = repo.novo_perfil("admin")
    b = repo.novo_perfil("admin")
    svc = _servico(repo)
    barreira = threading.Barrier(2)
    contar = repo.contar_admins_ativos

    def contar_sincronizado() -> int:
        n = contar()
        with contextlib.suppress(threading.BrokenBarrierError):
            barreira.wait(2)  # ambos já leram "2 admins" antes de qualquer escrita
        return n

    repo.contar_admins_ativos = contar_sincronizado
    resultados: list[str] = []

    def agir(ator: Any, alvo: Any) -> None:
        try:
            if operacao == "desativar":
                svc.desativar(ator, alvo.id)
            else:
                svc.alterar(ator, alvo.id, {"role": "solicitante"})
            resultados.append("ok")
        except Exception as e:  # noqa: BLE001
            resultados.append(type(e).__name__)

    ts = [
        threading.Thread(target=agir, args=(a, b)),
        threading.Thread(target=agir, args=(b, a)),
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert resultados.count("ok") == 1, resultados
    assert sum(1 for p in repo.perfis.values() if p.role == "admin" and p.ativo) == 1


def test_ag009_do_banco_vira_409_ultimo_admin() -> None:
    e = erro_db("AG009")
    assert isinstance(e, UltimoAdmin) and e.status == 409 and e.codigo == "ultimo_admin"
    assert e.mensagem


def test_ag009_sobe_como_409_pelo_servico(repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    adm = repo.novo_perfil("admin")
    outro = repo.novo_perfil("admin")

    def falha(*_a: Any, **_k: Any) -> None:
        raise erro_db("AG009")

    monkeypatch.setattr(repo, "atualizar_perfil", falha)
    with pytest.raises(UltimoAdmin) as e:
        _servico(repo).desativar(adm, outro.id)
    assert e.value.status == 409


# ======================================================================= S-04 anti-SSRF
@pytest.mark.parametrize(
    "ips",
    [
        ["127.0.0.1"],
        ["10.0.0.5"],
        ["169.254.169.254"],
        ["192.168.1.1"],
        ["::1"],
        ["fe80::1"],
        ["::ffff:127.0.0.1"],
        ["93.184.216.34", "10.0.0.1"],  # um IP privado entre os resolvidos basta para recusar
        [],
    ],
)
def test_destino_nao_publico_recusado(ips: list[str]) -> None:
    with pytest.raises(RuntimeError):
        exigir_destino_publico("n8n.exemplo.gov.br", 443, lambda _h, _p: ips)


def test_destino_publico_aceito_e_falha_de_dns_recusada() -> None:
    exigir_destino_publico("n8n.exemplo.gov.br", 443, lambda _h, _p: ["93.184.216.34"])
    exigir_destino_publico("n8n.exemplo.gov.br", 443, lambda _h, _p: ["2606:4700::1111"])

    def falha(_h: str, _p: int) -> list[str]:
        raise OSError("dns")

    with pytest.raises(RuntimeError):
        exigir_destino_publico("n8n.exemplo.gov.br", 443, falha)


def test_entrega_bloqueada_quando_dns_aponta_para_ip_interno() -> None:
    chamadas: list[Any] = []
    cliente = httpx.Client(
        transport=httpx.MockTransport(lambda req: (chamadas.append(req), httpx.Response(200))[1])
    )
    n = N8nWebhook(
        URL,
        TOKEN,
        cliente=cliente,
        backoff=0.01,
        resolvedor=lambda _h, _p: ["169.254.169.254"],  # rebinding para o metadata
    )
    n.notificar("requisicao.autorizada", {"id": "1", "status": "autorizada"})
    for _ in range(100):
        if n.falhas:
            break
        time.sleep(0.02)
    n.fechar()
    assert n.falhas == 1 and chamadas == []  # nada saiu


def test_entrega_com_dns_publico_funciona() -> None:
    chamadas: list[Any] = []
    cliente = httpx.Client(
        transport=httpx.MockTransport(lambda req: (chamadas.append(req), httpx.Response(200))[1])
    )
    n = N8nWebhook(URL, TOKEN, cliente=cliente, resolvedor=lambda _h, _p: ["93.184.216.34"])
    n.notificar("requisicao.autorizada", {"id": "1", "status": "autorizada"})
    for _ in range(100):
        if chamadas:
            break
        time.sleep(0.02)
    n.fechar()
    assert len(chamadas) == 1


def test_allowlist_de_hosts() -> None:
    validar_url_webhook(URL, ["n8n.exemplo.gov.br"])
    validar_url_webhook(URL, ["N8N.Exemplo.gov.br"])
    validar_url_webhook(URL, [])  # vazia = sem restrição além do host público
    with pytest.raises(RuntimeError, match="N8N_ALLOWED_HOSTS"):
        validar_url_webhook(URL, ["outro.exemplo.gov.br"])


def test_producao_valida_n8n_allowed_hosts() -> None:
    base = {
        **BASE_PROD,
        "n8n_webhook_url": URL,
        "n8n_webhook_token": TOKEN,
        "n8n_hmac_secret": HMAC,
    }
    Config(**base, n8n_allowed_hosts=["n8n.exemplo.gov.br"]).validar_producao()
    with pytest.raises(RuntimeError, match="N8N_ALLOWED_HOSTS"):
        Config(**base, n8n_allowed_hosts=["outro.exemplo.gov.br"]).validar_producao()
    for ruim in ("https://n8n.exemplo.gov.br", "n8n.exemplo.gov.br/x", "*.exemplo.gov.br", "a b"):
        with pytest.raises(RuntimeError, match="N8N_ALLOWED_HOSTS"):
            Config(**base, n8n_allowed_hosts=[ruim]).validar_producao()


def test_config_le_n8n_allowed_hosts_do_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("N8N_ALLOWED_HOSTS", " N8N.exemplo.gov.br , outro.gov.br ")
    assert Config.from_env().n8n_allowed_hosts == ["n8n.exemplo.gov.br", "outro.gov.br"]


# ======================================================================= S-05 HMAC obrigatório
def test_producao_exige_hmac_quando_n8n_ligado() -> None:
    Config(**BASE_PROD).validar_producao()  # n8n desligado: ok sem segredo
    base = {**BASE_PROD, "n8n_webhook_url": URL, "n8n_webhook_token": TOKEN}
    with pytest.raises(RuntimeError, match="N8N_HMAC_SECRET"):
        Config(**base).validar_producao()
    with pytest.raises(RuntimeError, match="distinto"):
        Config(**{**base, "n8n_webhook_token": HMAC}, n8n_hmac_secret=HMAC).validar_producao()
    Config(**base, n8n_hmac_secret=HMAC).validar_producao()


def test_producao_exige_hmac_mesmo_com_so_o_token() -> None:
    with pytest.raises(RuntimeError, match="N8N_HMAC_SECRET"):
        Config(**BASE_PROD, n8n_webhook_token=TOKEN).validar_producao()


# ======================================================================= S-08 e-mail sem TLS
@pytest.mark.parametrize("extra", [{}, {"email_user": "u"}, {"email_password": "p"}])
def test_producao_recusa_email_security_none(extra: dict[str, str]) -> None:
    base = {
        **BASE_PROD,
        "email_enabled": True,
        "email_host": "h",
        "email_from": "a@b.gov.br",
        "email_security": "none",
    }
    with pytest.raises(RuntimeError, match="EMAIL_SECURITY=none"):
        Config(**base, **extra).validar_producao()


def test_none_aceito_com_email_desligado_e_starttls_ok() -> None:
    Config(**BASE_PROD, email_security="none").validar_producao()  # e-mail desligado
    Config(
        **BASE_PROD, email_enabled=True, email_host="h", email_from="a@b.gov.br"
    ).validar_producao()


# ======================================================================= I-07 IP do cliente
@pytest.fixture
def app_ip(app):  # type: ignore[no-untyped-def]
    return app


def _ip(app: Any, remoto: str | None, cabecalho: str | None) -> str:
    headers = {"CF-Connecting-IP": cabecalho} if cabecalho else {}
    with app.test_request_context("/", headers=headers, environ_base={"REMOTE_ADDR": remoto or ""}):
        return ip_cliente()


def test_cabecalho_confiado_so_do_proxy_do_compose(app_ip) -> None:  # type: ignore[no-untyped-def]
    assert _ip(app_ip, "10.213.88.2", "203.0.113.7") == "203.0.113.7"  # nginx do frontend
    assert _ip(app_ip, "127.0.0.1", "203.0.113.7") == "203.0.113.7"  # loopback do container


@pytest.mark.parametrize("remoto", ["203.0.113.50", "10.213.89.2", "172.18.0.5", "2001:db8::1"])
def test_cabecalho_forjado_fora_do_proxy_e_ignorado(app_ip, remoto) -> None:  # type: ignore[no-untyped-def]
    assert _ip(app_ip, remoto, "1.2.3.4") == remoto


def test_sem_cabecalho_ou_sem_remote_addr(app_ip) -> None:  # type: ignore[no-untyped-def]
    assert _ip(app_ip, "10.213.88.2", None) == "10.213.88.2"
    assert _ip(app_ip, None, "1.2.3.4") == "desconhecido"


def test_cidrs_configuraveis_e_validados() -> None:
    cfg = Config(**BASE_PROD, trusted_proxy_cidrs=["172.30.0.0/16"])
    cfg.validar_producao()
    with pytest.raises(RuntimeError, match="TRUSTED_PROXY_CIDRS"):
        Config(**BASE_PROD, trusted_proxy_cidrs=["nao-e-cidr"]).validar_producao()


def test_config_le_trusted_proxy_cidrs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRUSTED_PROXY_CIDRS", raising=False)
    assert "10.213.88.0/24" in Config.from_env().trusted_proxy_cidrs
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "172.30.0.0/16, 10.0.0.0/8")
    assert Config.from_env().trusted_proxy_cidrs == ["172.30.0.0/16", "10.0.0.0/8"]
