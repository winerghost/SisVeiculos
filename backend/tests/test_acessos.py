"""Histórico de acessos: hook de registro (best-effort), minimização, endpoint do admin e
último acesso. O fake emula as RPCs; o último bloco usa o repositório REAL com um cliente
PostgREST simulado, para o fake não mascarar erros reais.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

import pytest
from postgrest.exceptions import APIError

from app.errors import ErroInterno, FalhaRegistroAcesso
from app.repositorios.supabase_repo import SupabaseRepositorio
from app.services.acessos_minimizacao import (
    FAMILIAS,
    SISTEMAS,
    mascarar_ip,
    metodo_autenticacao,
    reduzir_user_agent,
)
from app.services.registro_acessos import RegistradorAcessos, _Vistos
from tests.conftest import emitir_token

IP = "203.0.113.57"
UA_CHROME_WIN = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 SEGREDO-UA-123"
)


def _cod(r) -> str:  # type: ignore[no-untyped-def]
    return r.get_json()["erro"]["codigo"]


def _cab(sub: str, sid: str | None = None, **extra: Any) -> dict[str, str]:
    claims: dict[str, Any] = {
        "aal": "aal1",
        "amr": [{"method": "password", "timestamp": int(time.time())}],
        **extra,
    }
    if sid is not None:
        claims["session_id"] = sid
    return {
        "Authorization": f"Bearer {emitir_token(sub, extra=claims)}",
        "CF-Connecting-IP": IP,
        "User-Agent": UA_CHROME_WIN,
    }


def _sid() -> str:
    return str(uuid.uuid4())


# =============================================================================== minimização
@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("203.0.113.57", "203.0.113.0/24"),
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1::/48"),
        ("::ffff:198.51.100.9", "198.51.100.0/24"),  # mapeado -> IPv4 antes de mascarar
        ("::ffff:c633:6409", "198.51.100.0/24"),
        ("fe80::1%eth0", "fe80::/48"),
        ("desconhecido", None),
        ("999.1.1.1", None),
        ("203.0.113.57/24", None),
        ("", None),
        (None, None),
        ("1" * 100, None),
    ],
)
def test_mascara_ip(entrada, esperado):  # type: ignore[no-untyped-def]
    assert mascarar_ip(entrada) == esperado


@pytest.mark.parametrize(
    ("ua", "esperado"),
    [
        (UA_CHROME_WIN, "Chrome/Windows"),
        (UA_CHROME_WIN.replace("Safari/537.36", "Safari/537.36 Edg/120.0.0.0"), "Edge/Windows"),
        (UA_CHROME_WIN.replace("Safari/537.36", "Safari/537.36 OPR/105.0.0.0"), "Opera/Windows"),
        ("Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0", "Firefox/Linux"),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.1 Safari/605.1.15",
            "Safari/macOS",
        ),
        (
            "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) "
            "SamsungBrowser/23.0 Chrome/115.0.0.0 Mobile Safari/537.36",
            "Samsung Internet/Android",
        ),
        (
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Mobile Safari/537.36",
            "Chrome/Android",
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
            "Mobile Safari/iOS",
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) CriOS/120.0.0.0 Mobile/15E148 Safari/604.1",
            "Mobile Safari/iOS",
        ),
        (
            "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) Chrome/120.0.0.0 Safari/537.36",
            "Chrome/ChromeOS",
        ),
        ("curl/8.4.0", "Outro/Desconhecido"),
        ("", "Outro/Desconhecido"),
        (None, "Outro/Desconhecido"),
    ],
)
def test_reduz_user_agent(ua, esperado):  # type: ignore[no-untyped-def]
    assert reduzir_user_agent(ua) == esperado


def test_user_agent_nunca_devolve_o_bruto_e_respeita_listas_fechadas():
    for ua in (UA_CHROME_WIN, "Mozilla/5.0 SEGREDO", "x" * 5000, "Chrome/1 Android; ; DROP TABLE"):
        r = reduzir_user_agent(ua)
        familia, sistema = r.split("/")
        assert familia in FAMILIAS and sistema in SISTEMAS
        assert "SEGREDO" not in r and any(c.isdigit() for c in r) is False


@pytest.mark.parametrize(
    ("amr", "esperado"),
    [
        ([{"method": "password", "timestamp": 1}], "password"),
        ([{"method": "password", "timestamp": 1}, {"method": "totp", "timestamp": 2}], "totp"),
        ([{"method": "totp", "timestamp": 5}, {"method": "password", "timestamp": 2}], "totp"),
        ([{"method": "otp", "timestamp": 1}], "outro"),
        (["password"], "password"),
        ([], "outro"),
        (None, "outro"),
        ("totp", "outro"),
        ([{"timestamp": 1}], "outro"),
    ],
)
def test_metodo_autenticacao(amr, esperado):  # type: ignore[no-untyped-def]
    assert metodo_autenticacao(amr) == esperado


# =============================================================================== hook
def test_grava_uma_vez_por_sessao_com_dados_minimizados(client, repo, c):  # type: ignore[no-untyped-def]
    sid = _sid()
    for _ in range(3):
        assert client.get("/api/eu", headers=_cab(c.sol.id, sid)).status_code == 200
    assert repo.chamadas_registro == 1  # o cache evita a RPC repetida
    linha = repo.acessos[sid]
    assert linha["usuario_id"] == c.sol.id
    assert linha["metodo"] == "password" and linha["aal"] == "aal1"
    assert linha["ip_mascarado"] == "203.0.113.0/24"
    assert linha["ua_familia"] == "Chrome/Windows"
    assert IP not in str(linha) and "SEGREDO" not in str(linha)


def test_idempotente_sem_cache(client, app, repo, c):  # type: ignore[no-untyped-def]
    sid = _sid()
    client.get("/api/eu", headers=_cab(c.sol.id, sid))
    # Outro processo (cache vazio): a RPC devolve false e não duplica.
    app.extensions["sisveiculos"].registrador_acessos = RegistradorAcessos(repo, assincrono=False)
    client.get("/api/eu", headers=_cab(c.sol.id, sid))
    assert repo.chamadas_registro == 2 and len(repo.acessos) == 1


def test_sessoes_diferentes_geram_linhas_diferentes(client, repo, c):  # type: ignore[no-untyped-def]
    client.get("/api/eu", headers=_cab(c.sol.id, _sid()))
    client.get("/api/eu", headers=_cab(c.sol.id, _sid()))
    assert len(repo.acessos) == 2


@pytest.mark.parametrize("sid", [None, "nao-e-uuid", "", "1234"])
def test_sem_session_id_valido_nao_registra_e_nao_falha(client, repo, c, sid):  # type: ignore[no-untyped-def]
    r = client.get("/api/eu", headers=_cab(c.sol.id, sid))
    assert r.status_code == 200
    assert repo.chamadas_registro == 0


def test_session_id_nao_string_nao_registra(client, repo, c):  # type: ignore[no-untyped-def]
    h = _cab(c.sol.id, None, session_id=12345)
    assert client.get("/api/eu", headers=h).status_code == 200
    assert repo.chamadas_registro == 0


def test_jwt_invalido_nao_registra(client, repo, c, chave_errada):  # type: ignore[no-untyped-def]
    tok = emitir_token(c.sol.id, chave=chave_errada, extra={"session_id": _sid()})
    r = client.get("/api/eu", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401 and repo.chamadas_registro == 0
    assert client.get("/api/eu").status_code == 401 and repo.chamadas_registro == 0


def test_conta_inativa_nao_registra(client, repo):  # type: ignore[no-untyped-def]
    inativo = repo.novo_perfil("solicitante", ativo=False)
    r = client.get("/api/eu", headers=_cab(inativo.id, _sid()))
    assert r.status_code == 403 and repo.chamadas_registro == 0


def test_mfa_requerido_nao_grava_e_aal2_grava_com_totp(client, repo, c):  # type: ignore[no-untyped-def]
    repo.config = repo.config.__class__(mfa=True)
    sid1 = _sid()
    r = client.get("/api/eu", headers=_cab(c.aut.id, sid1))  # aal1
    assert r.status_code == 403 and _cod(r) == "mfa_requerido"
    assert repo.chamadas_registro == 0
    sid2 = _sid()
    h = _cab(
        c.aut.id,
        sid2,
        aal="aal2",
        amr=[{"method": "password", "timestamp": 1}, {"method": "totp", "timestamp": 2}],
    )
    assert client.get("/api/eu", headers=h).status_code == 200
    assert repo.acessos[sid2]["aal"] == "aal2" and repo.acessos[sid2]["metodo"] == "totp"


def test_rotas_publicas_e_aceite_de_convite_nao_registram(client, repo, c):  # type: ignore[no-untyped-def]
    assert client.get("/api/saude").status_code == 200
    novo = str(uuid.uuid4())  # conta ainda sem perfil
    h = _cab(novo, _sid())
    client.post(
        "/api/convites/aceitar", headers=h, json={"nome": "Fulano"}
    )  # resultado irrelevante
    assert repo.chamadas_registro == 0


@pytest.mark.parametrize("codigo", ["AG019", "AG020", "55P03", "RuntimeError", "ConnectError"])
def test_falha_do_rpc_nao_afeta_resposta_nem_vaza_no_log(client, repo, c, codigo, caplog):  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    repo.falha_registro = codigo
    sid = _sid()
    r = client.get("/api/eu", headers=_cab(c.sol.id, sid))
    assert r.status_code == 200 and r.get_json()["id"] == c.sol.id
    assert repo.acessos == {}
    texto = caplog.text
    assert f"codigo={codigo}" in texto
    for proibido in (IP, "203.0.113", "SEGREDO", "Windows", "Mozilla", sid, sid[:8], c.sol.id):
        assert proibido not in texto
    # Falha não marca como visto: a próxima requisição tenta de novo; sucesso passa a cachear.
    repo.falha_registro = None
    client.get("/api/eu", headers=_cab(c.sol.id, sid))
    client.get("/api/eu", headers=_cab(c.sol.id, sid))
    assert repo.chamadas_registro == 2 and sid in repo.acessos


def test_excecao_inesperada_do_registrador_nao_derruba_a_requisicao(client, app, c):  # type: ignore[no-untyped-def]
    class Quebrado:
        def registrar(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("falha qualquer")

    app.extensions["sisveiculos"].registrador_acessos = Quebrado()
    assert client.get("/api/eu", headers=_cab(c.sol.id, _sid())).status_code == 200


def test_contadores_e_log_agregado(repo, c, caplog):  # type: ignore[no-untyped-def]
    t = [1000.0]
    reg = RegistradorAcessos(repo, assincrono=False, relogio=lambda: t[0], intervalo_agregado=60)
    repo.falha_registro = "AG019"
    caplog.set_level(logging.DEBUG)
    claims = {"session_id": _sid()}
    reg.registrar(c.sol.id, claims, ip=IP, user_agent=UA_CHROME_WIN)  # 1ª falha avisa na hora
    assert "registro_acesso_falhas_agregado AG019=1" in caplog.text
    for _ in range(3):
        reg.registrar(c.sol.id, claims, ip=IP, user_agent=UA_CHROME_WIN)
    assert reg.contadores() == {"AG019": 3}
    t[0] += 61
    repo.falha_registro = "AG020"
    reg.registrar(c.sol.id, claims, ip=IP, user_agent=UA_CHROME_WIN)
    assert "AG019=3 AG020=1" in caplog.text
    assert reg.contadores() == {}
    assert IP not in caplog.text and "SEGREDO" not in caplog.text


def test_cache_com_ttl_e_limite_de_tamanho():
    t = [0.0]
    v = _Vistos(maximo=2, ttl=10, relogio=lambda: t[0])
    v.marcar("a")
    v.marcar("b")
    v.marcar("c")  # expulsa o mais antigo
    assert "a" not in v and "b" in v and "c" in v and len(v) == 2
    t[0] = 11
    assert "b" not in v and "c" not in v


def test_gravacao_assincrona_nao_bloqueia_a_resposta(repo, c):  # type: ignore[no-untyped-def]
    liberar = threading.Event()
    original = repo.registrar_acesso_login

    def lento(**kw: Any) -> bool:
        liberar.wait(5)
        return original(**kw)

    repo.registrar_acesso_login = lento  # type: ignore[method-assign]
    reg = RegistradorAcessos(repo, assincrono=True)
    sid = _sid()
    ini = time.monotonic()
    reg.registrar(c.sol.id, {"session_id": sid}, ip=IP, user_agent=UA_CHROME_WIN)
    reg.registrar(c.sol.id, {"session_id": sid}, ip=IP, user_agent=UA_CHROME_WIN)  # em voo: ignora
    assert time.monotonic() - ini < 1
    assert repo.acessos == {}
    liberar.set()
    reg.aguardar()
    assert list(repo.acessos) == [sid]
    reg.fechar()


def test_fila_cheia_descarta_sem_erro(repo, c):  # type: ignore[no-untyped-def]
    liberar = threading.Event()
    repo.registrar_acesso_login = lambda **kw: liberar.wait(5) or True  # type: ignore[method-assign]
    reg = RegistradorAcessos(repo, assincrono=True, fila_max=1, intervalo_agregado=10**9)
    for _ in range(6):
        reg.registrar(c.sol.id, {"session_id": _sid()}, ip=None, user_agent=None)
    assert reg.contadores().get("fila_cheia", 0) >= 1
    liberar.set()
    reg.aguardar()
    reg.fechar()


def test_ip_invalido_ou_ausente_grava_sem_ip(repo, c):  # type: ignore[no-untyped-def]
    reg = RegistradorAcessos(repo, assincrono=False)
    for i, ip in enumerate(("desconhecido", None, "lixo")):
        sid = _sid()
        reg.registrar(c.sol.id, {"session_id": sid}, ip=ip, user_agent=None)
        assert repo.acessos[sid]["ip_mascarado"] is None, i
        assert repo.acessos[sid]["ua_familia"] == "Outro/Desconhecido"


# =============================================================================== endpoint
def _h(p) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return _cab(p.id, _sid())


def _semear(repo, c, n: int = 3) -> None:  # type: ignore[no-untyped-def]
    for i in range(n):
        repo.registrar_acesso_login(
            usuario_id=c.sol.id,
            session_id=_sid(),
            ocorrido_em=f"2026-03-0{i + 1}T12:00:00+00:00",
            metodo="password",
            aal="aal1",
            ip_mascarado="203.0.113.0/24",
            ua_familia="Chrome/Windows",
        )


def test_endpoint_exige_autenticacao(client):  # type: ignore[no-untyped-def]
    r = client.get("/api/admin/acessos")
    assert r.status_code == 401 and _cod(r) == "nao_autenticado"


@pytest.mark.parametrize("quem", ["sol", "aut", "outro"])
def test_endpoint_recusa_nao_admin_e_nao_audita(client, repo, c, quem):  # type: ignore[no-untyped-def]
    perfil = getattr(c, quem)
    for url in (
        "/api/admin/acessos",
        f"/api/admin/acessos?usuario_id={perfil.id}",  # nem o próprio histórico
    ):
        r = client.get(url, headers=_h(perfil))
        assert r.status_code == 403 and _cod(r) == "sem_permissao"
    assert repo.eventos == []


def test_nao_existe_rota_de_acessos_fora_do_admin(client, c):  # type: ignore[no-untyped-def]
    for url in ("/api/acessos", "/api/eu/acessos", f"/api/usuarios/{c.sol.id}/acessos"):
        assert client.get(url, headers=_h(c.adm)).status_code == 404


def test_admin_lista_acessos(client, repo, c):  # type: ignore[no-untyped-def]
    _semear(repo, c)
    r = client.get("/api/admin/acessos", headers=_h(c.adm))
    assert r.status_code == 200
    j = r.get_json()
    assert set(j) == {"itens", "total", "limite", "offset"}
    assert j["total"] >= 3 and j["limite"] == 50 and j["offset"] == 0
    item = next(i for i in j["itens"] if i["usuario_id"] == c.sol.id)
    assert set(item) == {
        "id",
        "usuario_id",
        "nome",
        "ocorrido_em",
        "metodo",
        "aal",
        "ip_mascarado",
        "ua_familia",
        "sessao_ref",
    }
    assert item["ip_mascarado"] == "203.0.113.0/24" and item["ua_familia"] == "Chrome/Windows"
    assert r.headers["Cache-Control"] == "no-store"


def test_sessao_ref_nunca_e_o_session_id(client, repo, c):  # type: ignore[no-untyped-def]
    _semear(repo, c)
    corpo = client.get("/api/admin/acessos", headers=_h(c.adm)).get_data(as_text=True)
    for sid in repo.acessos:
        assert sid not in corpo and sid[:8] not in corpo
    assert "session_id" not in corpo
    for i in client.get("/api/admin/acessos", headers=_h(c.adm)).get_json()["itens"]:
        assert len(i["sessao_ref"]) == 8


def test_filtros_usuario_periodo_limite_offset(client, repo, c):  # type: ignore[no-untyped-def]
    _semear(repo, c, 3)  # 01, 02, 03 de março às 12h UTC
    h = _h(c.adm)
    base = "/api/admin/acessos"
    j = client.get(f"{base}?usuario_id={c.sol.id}", headers=h).get_json()
    assert j["total"] == 3 and [i["ocorrido_em"][:10] for i in j["itens"]][0] == "2026-03-03"
    assert client.get(f"{base}?usuario_id={c.outro.id}", headers=h).get_json()["total"] == 0
    # de inclusivo, ate exclusivo; '+' do fuso sem escape (vira espaço na query) é tolerado
    q = f"{base}?usuario_id={c.sol.id}&de=2026-03-02T12:00:00+00:00&ate=2026-03-03T12:00:00%2B00:00"
    assert client.get(q, headers=h).get_json()["total"] == 1
    z = f"{base}?usuario_id={c.sol.id}&de=2026-03-02T09:00:00-03:00&ate=2026-03-03T12:00:00Z"
    assert client.get(z, headers=h).get_json()["total"] == 1
    j = client.get(f"{base}?usuario_id={c.sol.id}&limite=2&offset=1", headers=h).get_json()
    assert j["total"] == 3 and j["limite"] == 2 and j["offset"] == 1 and len(j["itens"]) == 2
    assert j["itens"][0]["ocorrido_em"][:10] == "2026-03-02"


@pytest.mark.parametrize(
    "query",
    [
        "usuario_id=nao-e-uuid",
        "de=2026-03-01",  # sem hora/fuso
        "de=2026-03-01T00:00:00",  # sem fuso
        "de=lixo",
        "ate=" + "9" * 80,
        "de=2026-03-02T00:00:00Z&ate=2026-03-01T00:00:00Z",  # de > ate
        "de=2024-01-01T00:00:00Z&ate=2026-01-01T00:00:00Z",  # intervalo grande demais
        "limite=0",
        "limite=101",
        "limite=abc",
        "offset=-1",
        "offset=100001",
        "offset=1.5",
        "foo=bar",
        "usuario_id=" + str(uuid.uuid4()) + "&q=nome",
        "limite=1&limite=2",
    ],
)
def test_validacao_400(client, repo, c, query):  # type: ignore[no-untyped-def]
    r = client.get(f"/api/admin/acessos?{query}", headers=_h(c.adm))
    assert r.status_code == 400 and _cod(r) == "requisicao_invalida"
    assert repo.eventos_de("acao_administrativa") == []  # inválida: nem audita


def test_limites_extremos_validos(client, c):  # type: ignore[no-untyped-def]
    h = _h(c.adm)
    assert client.get("/api/admin/acessos?limite=100&offset=100000", headers=h).status_code == 200
    assert client.get("/api/admin/acessos?limite=1&offset=0", headers=h).status_code == 200


def test_consulta_e_auditada_sem_pii(client, repo, c):  # type: ignore[no-untyped-def]
    _semear(repo, c)
    url = f"/api/admin/acessos?usuario_id={c.sol.id}&de=2026-03-01T00:00:00Z"
    assert client.get(url, headers=_h(c.adm)).status_code == 200
    ev = repo.eventos_de("acao_administrativa")
    assert len(ev) == 1
    assert ev[0]["ator_id"] == c.adm.id and ev[0]["detalhes"] == {"recurso": "acessos"}
    assert c.sol.id not in str(ev) and "203.0.113" not in str(ev) and "2026-03" not in str(ev)
    client.get("/api/admin/acessos", headers=_h(c.adm))
    assert len(repo.eventos_de("acao_administrativa")) == 2


def test_falha_da_auditoria_nao_expoe_dados(client, repo, c):  # type: ignore[no-untyped-def]
    _semear(repo, c)
    repo.falha_auditoria = ErroInterno("Serviço de dados indisponível. Tente novamente.")
    r = client.get("/api/admin/acessos", headers=_h(c.adm))
    assert r.status_code == 503 and "203.0.113" not in r.get_data(as_text=True)
    assert "itens" not in r.get_json()


def test_erro_real_de_rpc_na_listagem_vira_503_sem_vazar(client, repo, c):  # type: ignore[no-untyped-def]
    repo.falha_listagem = ErroInterno("Serviço de dados indisponível. Tente novamente.")
    r = client.get("/api/admin/acessos", headers=_h(c.adm))
    assert r.status_code == 503 and _cod(r) == "indisponivel"


def test_rate_limit_proprio(app, client, repo, c):  # type: ignore[no-untyped-def]
    app.extensions["sisveiculos"].config = app.extensions["sisveiculos"].config.__class__(
        **{**app.extensions["sisveiculos"].config.__dict__, "limite_busca_usuarios": "2 per minute"}
    )
    h = _h(c.adm)
    assert client.get("/api/admin/acessos", headers=h).status_code == 200
    assert client.get("/api/admin/acessos", headers=h).status_code == 200
    r = client.get("/api/admin/acessos", headers=h)
    assert r.status_code == 429 and _cod(r) == "limite_excedido"


# =============================================================================== /api/eu e usuários
def test_permissoes_ver_acessos_so_admin(client, c):  # type: ignore[no-untyped-def]
    assert "ver_acessos" in client.get("/api/eu", headers=_h(c.adm)).get_json()["permissoes"]
    for p in (c.sol, c.aut):
        assert "ver_acessos" not in client.get("/api/eu", headers=_h(p)).get_json()["permissoes"]


def test_ultimo_acesso_nos_usuarios(client, repo, c):  # type: ignore[no-untyped-def]
    _semear(repo, c, 2)
    j = client.get("/api/admin/usuarios", headers=_cab(c.adm.id)).get_json()  # sem session_id
    por_id = {i["id"]: i for i in j["itens"]}
    assert por_id[c.sol.id]["ultimo_acesso_em"] == "2026-03-02T12:00:00+00:00"
    assert por_id[c.outro.id]["ultimo_acesso_em"] is None  # nunca acessou
    d = client.get(f"/api/admin/usuarios/{c.sol.id}", headers=_cab(c.adm.id)).get_json()
    assert d["ultimo_acesso_em"] == "2026-03-02T12:00:00+00:00"


def test_ultimo_acesso_falha_nao_derruba_a_lista(client, repo, c):  # type: ignore[no-untyped-def]
    def quebra(ids):  # type: ignore[no-untyped-def]
        raise ErroInterno("Serviço de dados indisponível. Tente novamente.")

    repo.ultimo_acesso_por_usuario = quebra  # type: ignore[method-assign]
    r = client.get("/api/admin/usuarios", headers=_cab(c.adm.id))
    assert r.status_code == 200
    assert all(i["ultimo_acesso_em"] is None for i in r.get_json()["itens"])


def test_ultimo_acesso_em_lote_de_ate_100(client, repo, c):  # type: ignore[no-untyped-def]
    for _ in range(105):
        repo.novo_perfil("solicitante")
    j = client.get("/api/admin/usuarios?limite=100", headers=_cab(c.adm.id)).get_json()
    assert len(j["itens"]) == 100


# =============================================================================== repositório REAL
class _Exec:
    def __init__(self, dados: Any = None, erro: Exception | None = None) -> None:
        self.dados, self.erro = dados, erro

    def execute(self) -> Any:
        if self.erro:
            raise self.erro
        return type("R", (), {"data": self.dados, "count": None})()


class _ClienteRpc:
    def __init__(self, dados: Any = None, erro: Exception | None = None) -> None:
        self.dados, self.erro, self.chamadas = dados, erro, []

    def rpc(self, nome: str, params: dict[str, Any]) -> _Exec:
        self.chamadas.append((nome, params))
        return _Exec(self.dados, self.erro)


def _repo_real(cliente: _ClienteRpc) -> SupabaseRepositorio:
    r = SupabaseRepositorio.__new__(SupabaseRepositorio)
    r._c = cliente  # type: ignore[assignment]
    return r


def _erro_pg(codigo: str, msg: str = "") -> APIError:
    return APIError({"message": msg, "code": codigo, "details": "", "hint": ""})


_ARGS = {
    "usuario_id": "u",
    "session_id": "s",
    "ocorrido_em": "2026-01-01T00:00:00+00:00",
    "metodo": "password",
    "aal": "aal1",
    "ip_mascarado": "203.0.113.0/24",
    "ua_familia": "Chrome/Windows",
}


def test_repo_real_registrar_envia_so_dados_minimizados_e_le_bool():
    cli = _ClienteRpc(True)
    assert _repo_real(cli).registrar_acesso_login(**_ARGS) is True
    nome, params = cli.chamadas[0]
    assert nome == "registrar_acesso_login"
    assert set(params) == {
        "p_usuario",
        "p_session",
        "p_ocorrido_em",
        "p_metodo",
        "p_aal",
        "p_ip",
        "p_ua",
    }
    assert _repo_real(_ClienteRpc(False)).registrar_acesso_login(**_ARGS) is False


@pytest.mark.parametrize("codigo", ["AG019", "AG020", "55P03"])
def test_repo_real_registrar_traduz_erro_do_postgrest_so_com_codigo(codigo):  # type: ignore[no-untyped-def]
    cli = _ClienteRpc(erro=_erro_pg(codigo, "IP 203.0.113.57 inválido SEGREDO"))
    with pytest.raises(FalhaRegistroAcesso) as e:
        _repo_real(cli).registrar_acesso_login(**_ARGS)
    assert e.value.codigo == codigo and "SEGREDO" not in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__


def test_repo_real_registrar_erro_de_rede_vira_tipo_da_excecao():
    with pytest.raises(FalhaRegistroAcesso) as e:
        _repo_real(_ClienteRpc(erro=TimeoutError("segredo"))).registrar_acesso_login(**_ARGS)
    assert e.value.codigo == "TimeoutError" and "segredo" not in str(e.value)


def test_registrador_com_repo_real_e_erro_real_do_postgrest(c, caplog):  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    repo = _repo_real(_ClienteRpc(erro=_erro_pg("AG019", "Usuário inexistente ou inativo.")))
    reg = RegistradorAcessos(repo, assincrono=False)  # type: ignore[arg-type]
    reg.registrar(c.sol.id, {"session_id": _sid()}, ip=IP, user_agent=UA_CHROME_WIN)
    assert "registro_acesso_falhas_agregado AG019=1" in caplog.text
    assert IP not in caplog.text


def test_repo_real_listar_erro_do_postgrest_vira_erro_de_api():
    repo = _repo_real(_ClienteRpc(erro=_erro_pg("AG020", "x")))
    with pytest.raises(ErroInterno):
        repo.listar_acessos_login(usuario_id=None, de=None, ate=None, limite=50, offset=0)


def test_repo_real_listar_filtra_colunas_e_descarta_session_id():
    dados = {
        "total": 1,
        "limite": 50,
        "offset": 0,
        "itens": [
            {
                "id": "i",
                "usuario_id": "u",
                "nome": "N",
                "ocorrido_em": "t",
                "metodo": "totp",
                "aal": "aal2",
                "ip_mascarado": "203.0.113.0/24",
                "ua_familia": "Chrome/Windows",
                "sessao_ref": "abcd1234",
                "session_id": "VAZOU",
            }
        ],
    }
    res = _repo_real(_ClienteRpc(dados)).listar_acessos_login(
        usuario_id="u", de=None, ate=None, limite=50, offset=0
    )
    assert "session_id" not in res["itens"][0] and res["total"] == 1


def test_repo_real_listar_resposta_malformada_vira_503():
    with pytest.raises(ErroInterno):
        _repo_real(_ClienteRpc("lixo")).listar_acessos_login(
            usuario_id=None, de=None, ate=None, limite=50, offset=0
        )


def test_repo_real_ultimo_acesso_mapeia_e_trata_vazio():
    cli = _ClienteRpc([{"usuario_id": "u1", "ultimo_acesso_em": "2026-01-01T00:00:00+00:00"}])
    r = _repo_real(cli)
    assert r.ultimo_acesso_por_usuario(["u1", "u2"]) == {"u1": "2026-01-01T00:00:00+00:00"}
    assert r.ultimo_acesso_por_usuario([]) == {}
    assert len(cli.chamadas) == 1  # lista vazia nem chama o banco
    with pytest.raises(ErroInterno):
        _repo_real(_ClienteRpc(erro=_erro_pg("AG020"))).ultimo_acesso_por_usuario(["u1"])
