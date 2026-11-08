"""IA opcional (Fase 0): config, circuito, provedores, serviço de execução e tela de admin.

Garantias testadas: AI_ENABLED=false vence o banco; qualquer falha devolve None (texto padrão);
cota por usuário/unidade/mês; failover; circuito; auditoria do ator; chaves nunca expostas.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any

import httpx
import pytest

from app import create_app
from app.auth.jwt import VerificadorJWT
from app.config import Config
from app.extensions import limiter
from app.ia.circuito import Circuito
from app.ia.config import ConfigIA
from app.ia.provedores import (
    URL_ANTHROPIC,
    URL_OPENAI,
    ErroProvedor,
    ProvedorAnthropic,
    ProvedorOpenAI,
    Resultado,
)
from app.ia.repositorio import ConfigIABanco, Consumo
from app.ia.servico import ServicoIA, inicio_do_mes, teto_efetivo_tokens
from app.modelos import Perfil
from tests.conftest import _CHAVE, AUDIENCE, ISSUER, URL_ACEITE
from tests.fakes_ia import ProvedorRoteirizado, RepoIAMemoria, erro

CHAVE_OPENAI = "sk-openai-SEGREDO-123"
CHAVE_ANTHROPIC = "sk-ant-SEGREDO-456"


def cfg_ia(**kw: Any) -> ConfigIA:
    base: dict[str, Any] = {
        "habilitado": True,
        "openai_api_key": CHAVE_OPENAI,
        "anthropic_api_key": CHAVE_ANTHROPIC,
    }
    return ConfigIA(**{**base, **kw})


LIGADO = ConfigIABanco(mestre=True, saudacao=True, provedor="openai", modelo="gpt-5.4-mini")
PERFIL = Perfil("u1", "Fulano de Tal", "PU", "solicitante", True)


def servico(
    cfg: ConfigIA | None = None,
    banco: ConfigIABanco | None = LIGADO,
    **provedores: ProvedorRoteirizado,
) -> tuple[ServicoIA, RepoIAMemoria]:
    repo = RepoIAMemoria(banco)
    return ServicoIA(cfg or cfg_ia(), repo, provedores=provedores), repo


def gerar(s: ServicoIA, perfil: Perfil | None = PERFIL):  # type: ignore[no-untyped-def]
    return s.gerar("saudacao", sistema="s", usuario="u", perfil=perfil)


# ------------------------------------------------------------------------------ config
def test_config_padrao_desligada_e_sem_chaves(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("AI_ENABLED", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AI_MAX_TOKENS"):
        monkeypatch.delenv(v, raising=False)
    c = ConfigIA.from_env()
    assert c.habilitado is False and not c.tem_chave("openai") and not c.tem_chave("anthropic")
    assert c.max_tokens == 800 and c.teto_tokens_mes == 200_000


def test_config_le_ambiente_e_ignora_valor_invalido(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", f"  {CHAVE_OPENAI}  ")
    monkeypatch.setenv("AI_MAX_TOKENS", "abc")
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("AI_MODELOS_OPENAI", "modelo-a, modelo-b")
    monkeypatch.setenv("AI_MODELOS_ANTHROPIC", "modelo com espaço!")
    c = ConfigIA.from_env()
    assert c.habilitado and c.openai_api_key == CHAVE_OPENAI
    assert c.max_tokens == 800 and c.timeout_s == 20.0  # inválidos voltam ao padrão
    assert c.modelos_de("openai") == ("modelo-a", "modelo-b")
    assert c.modelos_de("anthropic") == ("claude-haiku-5-5", "claude-sonnet-5-5")


def test_config_nao_expoe_chaves_no_repr() -> None:
    texto = repr(cfg_ia()) + repr(ServicoIA(cfg_ia(), None))
    assert CHAVE_OPENAI not in texto and CHAVE_ANTHROPIC not in texto


# ----------------------------------------------------------------------------- circuito
def test_circuito_abre_apos_falhas_e_fecha_com_sucesso() -> None:
    t = [0.0]
    c = Circuito(3, 300, lambda: t[0])
    for _ in range(2):
        assert c.permite()
        c.falha()
    assert c.permite()
    c.falha()  # terceira falha consecutiva
    assert c.aberto and not c.permite()
    t[0] = 299
    assert not c.permite()
    t[0] = 301
    assert c.permite()  # meio-aberto: uma tentativa
    assert not c.permite()  # só uma por vez
    c.sucesso()
    assert not c.aberto and c.permite()


def test_circuito_meio_aberto_reabre_na_falha() -> None:
    t = [0.0]
    c = Circuito(1, 10, lambda: t[0])
    c.falha()
    t[0] = 11
    assert c.permite()
    c.falha()
    assert c.aberto and not c.permite()


# --------------------------------------------------------------------------- provedores
def _cliente(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_openai_envia_chave_no_cabecalho_e_le_resposta() -> None:
    visto: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        visto["url"] = str(req.url)
        visto["auth"] = req.headers["authorization"]
        visto["corpo"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Feliz Natal!"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        )

    r = ProvedorOpenAI(CHAVE_OPENAI, _cliente(h)).gerar(
        modelo="m", sistema="sys", usuario="usr", max_tokens=50, timeout=5
    )
    assert r == Resultado("Feliz Natal!", 12, 7)
    assert visto["url"] == URL_OPENAI and visto["auth"] == f"Bearer {CHAVE_OPENAI}"
    assert visto["corpo"]["model"] == "m" and visto["corpo"]["max_completion_tokens"] == 50
    assert [m["role"] for m in visto["corpo"]["messages"]] == ["system", "user"]


def test_anthropic_envia_chave_e_junta_blocos_de_texto() -> None:
    visto: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        visto["url"] = str(req.url)
        visto["chave"] = req.headers["x-api-key"]
        visto["versao"] = req.headers["anthropic-version"]
        visto["corpo"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "Bom "},
                    {"type": "tool_use", "id": "x"},
                    {"type": "text", "text": "dia!"},
                ],
                "usage": {"input_tokens": 9, "output_tokens": 3},
            },
        )

    r = ProvedorAnthropic(CHAVE_ANTHROPIC, _cliente(h)).gerar(
        modelo="m", sistema="sys", usuario="usr", max_tokens=50, timeout=5
    )
    assert r == Resultado("Bom dia!", 9, 3)
    assert visto["url"] == URL_ANTHROPIC and visto["chave"] == CHAVE_ANTHROPIC
    assert visto["corpo"]["system"] == "sys" and visto["corpo"]["max_tokens"] == 50
    assert visto["corpo"]["messages"] == [{"role": "user", "content": "usr"}]


@pytest.mark.parametrize(
    ("status", "tipo"),
    [(401, "auth"), (403, "auth"), (429, "limite"), (500, "http"), (400, "http")],
)
@pytest.mark.parametrize("classe", [ProvedorOpenAI, ProvedorAnthropic])
def test_status_http_vira_tipo_fixo_sem_eco_do_corpo(classe, status: int, tipo: str) -> None:  # type: ignore[no-untyped-def]
    def h(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "ECO-DO-PROMPT"}})

    with pytest.raises(ErroProvedor) as e:
        classe("k", _cliente(h)).gerar(
            modelo="m", sistema="s", usuario="u", max_tokens=5, timeout=1
        )
    assert e.value.tipo == tipo and "ECO-DO-PROMPT" not in str(e.value)


@pytest.mark.parametrize("classe", [ProvedorOpenAI, ProvedorAnthropic])
def test_timeout_rede_e_formato(classe) -> None:  # type: ignore[no-untyped-def]
    def lento(_r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("t")

    def fora(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("c")

    def lixo(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"nao e json")

    def vazio(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [], "content": []})

    for handler, tipo in (
        (lento, "timeout"),
        (fora, "rede"),
        (lixo, "formato"),
        (vazio, "formato"),
    ):
        with pytest.raises(ErroProvedor) as e:
            classe("k", _cliente(handler)).gerar(
                modelo="m", sistema="s", usuario="u", max_tokens=5, timeout=1
            )
        assert e.value.tipo == tipo


def test_provedores_nao_expoem_chave_no_repr() -> None:
    assert CHAVE_OPENAI not in repr(ProvedorOpenAI(CHAVE_OPENAI))
    assert CHAVE_ANTHROPIC not in repr(ProvedorAnthropic(CHAVE_ANTHROPIC))


# ------------------------------------------------------------------------------ serviço
def test_env_desligado_vence_o_banco_e_nao_chama_provedor() -> None:
    p = ProvedorRoteirizado("openai")
    s, repo = servico(cfg_ia(habilitado=False), openai=p)
    assert gerar(s) is None and p.chamadas == [] and repo.usos == []
    assert s.disponivel("saudacao") is False


@pytest.mark.parametrize(
    "banco",
    [
        ConfigIABanco(),  # padrão: tudo desligado
        replace(LIGADO, mestre=False),
        replace(LIGADO, saudacao=False),
        replace(LIGADO, provedor=None),
    ],
)
def test_banco_desligado_nao_chama_provedor(banco: ConfigIABanco) -> None:
    p = ProvedorRoteirizado("openai")
    s, _ = servico(banco=banco, openai=p)
    assert gerar(s) is None and p.chamadas == []


def test_funcao_desconhecida_e_sem_repositorio() -> None:
    s, _ = servico(openai=ProvedorRoteirizado("openai"))
    assert s.gerar("inexistente", sistema="s", usuario="u") is None
    assert ServicoIA(cfg_ia(), None).gerar("saudacao", sistema="s", usuario="u") is None


def test_sem_chave_do_provedor_nao_chama() -> None:
    p = ProvedorRoteirizado("openai")
    s, _ = servico(cfg_ia(openai_api_key=""), openai=p)
    assert gerar(s) is None and p.chamadas == []


def test_sucesso_registra_uso_sem_conteudo() -> None:
    p = ProvedorRoteirizado("openai", [Resultado("Feliz Natal, {nome}!", 20, 8)])
    s, repo = servico(openai=p)
    r = s.gerar("saudacao", sistema="SISTEMA", usuario="PROMPT-SECRETO", perfil=PERFIL)
    assert r is not None and r.texto == "Feliz Natal, {nome}!" and r.provedor == "openai"
    assert p.chamadas[0]["modelo"] == "gpt-5.4-mini" and p.chamadas[0]["max_tokens"] == 800
    (uso,) = repo.usos
    assert uso["status"] == "ok" and uso["tokens_entrada"] == 20 and uso["tokens_saida"] == 8
    assert uso["usuario_id"] == "u1" and uso["unidade"] == "PU"
    assert "PROMPT-SECRETO" not in repr(uso) and "Feliz Natal" not in repr(uso)


def test_modelo_fora_da_lista_cai_no_padrao_do_provedor() -> None:
    p = ProvedorRoteirizado("openai")
    s, _ = servico(banco=replace(LIGADO, modelo="modelo-removido"), openai=p)
    gerar(s)
    assert p.chamadas[0]["modelo"] == "gpt-5.4-mini"


def test_falha_do_ativo_usa_reserva_com_modelo_padrao_dela() -> None:
    ativo = ProvedorRoteirizado("openai", [erro("http")])
    reserva = ProvedorRoteirizado("anthropic", [Resultado("Olá!", 5, 2)])
    s, repo = servico(
        banco=replace(LIGADO, provedor_reserva="anthropic"), openai=ativo, anthropic=reserva
    )
    r = gerar(s)
    assert r is not None and r.provedor == "anthropic"
    assert reserva.chamadas[0]["modelo"] == "claude-haiku-5-5"
    assert [(u["provedor"], u["status"]) for u in repo.usos] == [
        ("openai", "erro"),
        ("anthropic", "ok"),
    ]


def test_todos_falham_devolve_none_e_registra_timeout() -> None:
    p = ProvedorRoteirizado("openai", [erro("timeout")])
    s, repo = servico(openai=p)
    assert gerar(s) is None
    assert repo.usos[0]["status"] == "timeout" and repo.usos[0]["tokens_entrada"] == 0


def test_circuito_abre_e_para_de_chamar_o_provedor() -> None:
    p = ProvedorRoteirizado("openai", [erro(), erro(), erro()])
    s, _ = servico(openai=p)
    for _ in range(3):
        assert gerar(s) is None
    assert s.circuito_aberto("openai")
    antes = len(p.chamadas)
    assert gerar(s) is None
    assert len(p.chamadas) == antes  # circuito aberto: nenhuma chamada nova


def test_circuito_aberto_no_ativo_vai_direto_para_a_reserva() -> None:
    ativo = ProvedorRoteirizado("openai", [erro()] * 3)
    reserva = ProvedorRoteirizado("anthropic")
    s, _ = servico(
        cfg_ia(circuito_falhas=3),
        replace(LIGADO, provedor_reserva="anthropic"),
        openai=ativo,
        anthropic=reserva,
    )
    for _ in range(3):
        gerar(s)
    assert s.circuito_aberto("openai")
    n = len(ativo.chamadas)
    assert gerar(s) is not None and len(ativo.chamadas) == n


@pytest.mark.parametrize(
    "consumo",
    [
        Consumo(usuario_hora=20),  # limite por usuário (padrão 20)
        Consumo(unidade_hora=100),  # limite por unidade (padrão 100)
        Consumo(tokens_mes={"openai": 150_000, "anthropic": 50_000}),  # teto global (200 mil)
    ],
)
def test_cota_excedida_nao_chama(consumo: Consumo) -> None:
    p = ProvedorRoteirizado("openai")
    s, repo = servico(openai=p)
    repo.consumo_fixo = consumo
    assert gerar(s) is None and p.chamadas == []


def test_cota_abaixo_do_limite_chama() -> None:
    p = ProvedorRoteirizado("openai")
    s, repo = servico(openai=p)
    repo.consumo_fixo = Consumo(usuario_hora=19, unidade_hora=99, tokens_mes={"openai": 199_999})
    assert gerar(s) is not None


def test_teto_efetivo_e_o_menor_entre_ambiente_e_tela() -> None:
    c = cfg_ia(teto_tokens_mes=200_000)
    assert teto_efetivo_tokens(c, ConfigIABanco()) == 200_000
    assert teto_efetivo_tokens(c, ConfigIABanco(teto_tokens_mes=50_000)) == 50_000
    assert teto_efetivo_tokens(c, ConfigIABanco(teto_tokens_mes=900_000)) == 200_000
    p = ProvedorRoteirizado("openai")
    s, repo = servico(c, replace(LIGADO, teto_tokens_mes=1_000), openai=p)
    repo.consumo_fixo = Consumo(tokens_mes={"openai": 1_000})
    assert gerar(s) is None and p.chamadas == []


def test_sem_perfil_nao_aplica_limite_por_usuario_mas_aplica_teto_mensal() -> None:
    p = ProvedorRoteirizado("openai")
    s, repo = servico(openai=p)
    repo.consumo_fixo = Consumo(usuario_hora=999, unidade_hora=999)
    assert gerar(s, perfil=None) is not None
    repo.consumo_fixo = Consumo(tokens_mes={"openai": 200_000})
    assert gerar(s, perfil=None) is None


def test_nunca_lanca_mesmo_com_banco_ou_provedor_quebrado(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    s, repo = servico(openai=ProvedorRoteirizado("openai", [RuntimeError("PROMPT-SECRETO")]))
    assert gerar(s) is None  # exceção inesperada do provedor
    repo.falhar_leitura = True
    assert gerar(s) is None and s.disponivel("saudacao") is False
    repo.falhar_leitura = False
    repo.falhar_registro = True
    assert gerar(s) is not None  # falha ao registrar uso não derruba a função
    assert "PROMPT-SECRETO" not in caplog.text and CHAVE_OPENAI not in caplog.text


def test_inicio_do_mes_usa_fuso_de_sao_paulo() -> None:
    from datetime import UTC, datetime

    # 2026-12-01 02:00 UTC ainda é 30/11 em São Paulo (-03): o mês corrente é novembro.
    assert inicio_do_mes(datetime(2026, 12, 1, 2, 0, tzinfo=UTC)) == datetime(
        2026, 11, 1, 3, 0, tzinfo=UTC
    )


# ----------------------------------------------------------------------------- tela admin
@pytest.fixture
def repo_ia() -> RepoIAMemoria:
    return RepoIAMemoria()


@pytest.fixture
def cfg_servidor() -> ConfigIA:
    return cfg_ia()


@pytest.fixture
def servico_ia(repo_ia, cfg_servidor) -> ServicoIA:  # type: ignore[no-untyped-def]
    return ServicoIA(cfg_servidor, repo_ia)


@pytest.fixture
def app(repo, auth_admin, storage, avatares, repo_ia, servico_ia):  # type: ignore[no-untyped-def]
    verificador = VerificadorJWT(
        issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
    )
    aplicacao = create_app(
        Config(
            jwt_issuer=ISSUER, jwt_audience=AUDIENCE, testing=True, url_aceite_convite=URL_ACEITE
        ),
        repo=repo,
        verificador=verificador,
        auth_admin=auth_admin,
        armazenamento=storage,
        armazenamento_avatares=avatares,
        servico_ia=servico_ia,
    )
    limiter.reset()
    return aplicacao


def get(client, h):  # type: ignore[no-untyped-def]
    return client.get("/api/admin/ia", headers=h)


def put(client, h, corpo):  # type: ignore[no-untyped-def]
    return client.put("/api/admin/ia", headers=h, json=corpo)


def _cod(r) -> str:  # type: ignore[no-untyped-def]
    return r.get_json()["erro"]["codigo"]


def test_somente_admin_e_autenticado(client, auth, repo) -> None:  # type: ignore[no-untyped-def]
    assert get(client, {}).status_code == 401
    for role in ("solicitante", "autorizador"):
        h = auth(repo.novo_perfil(role).id)
        assert get(client, h).status_code == 403
        assert put(client, h, {"mestre": True}).status_code == 403


def test_padrao_desligado_mostra_provedores_sem_vazar_chaves(client, auth, adm) -> None:  # type: ignore[no-untyped-def]
    r = get(client, auth(adm.id))
    assert r.status_code == 200
    j = r.get_json()
    assert j["servidor_habilitado"] is True and j["acoes_permitidas"] == ["editar"]
    itens = {i["chave"]: i for i in j["itens"]}
    assert itens["mestre"]["habilitado"] is False
    assert itens["mestre"]["acoes_permitidas"] == []  # falta escolher provedor
    assert "provedor" in itens["mestre"]["motivo_indisponibilidade"]
    assert "chave geral" in itens["saudacao"]["motivo_indisponibilidade"]
    provs = {p["chave"]: p for p in j["provedores"]}
    assert provs["openai"]["chave_configurada"] is True
    assert provs["anthropic"]["modelos"] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    bruto = r.get_data(as_text=True)
    assert CHAVE_OPENAI not in bruto and CHAVE_ANTHROPIC not in bruto
    assert j["teto_efetivo_tokens_mes"] == 200_000


def test_ia_desligada_no_servidor_vence(  # type: ignore[no-untyped-def]
    repo, auth_admin, storage, avatares, repo_ia
) -> None:
    from tests.conftest import emitir_token

    verificador = VerificadorJWT(
        issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
    )
    ap = create_app(
        Config(
            jwt_issuer=ISSUER, jwt_audience=AUDIENCE, testing=True, url_aceite_convite=URL_ACEITE
        ),
        repo=repo,
        verificador=verificador,
        auth_admin=auth_admin,
        armazenamento=storage,
        armazenamento_avatares=avatares,
        servico_ia=ServicoIA(cfg_ia(habilitado=False), repo_ia),
    )
    adm = repo.novo_perfil("admin")
    h = {"Authorization": f"Bearer {emitir_token(adm.id)}"}
    c = ap.test_client()
    j = get(c, h).get_json()
    assert j["servidor_habilitado"] is False and j["acoes_permitidas"] == []
    assert "AI_ENABLED" in j["motivo_indisponibilidade"]
    assert all(i["acoes_permitidas"] == [] for i in j["itens"])
    r = put(c, h, {"provedor": "openai"})
    assert r.status_code == 404 and _cod(r) == "recurso_desligado"
    assert repo_ia.atualizacoes == []


def test_fluxo_completo_liga_com_ator_do_jwt(client, auth, adm, repo_ia) -> None:  # type: ignore[no-untyped-def]
    h = auth(adm.id)
    r = put(client, h, {"provedor": "openai"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["provedor"] == "openai" and j["modelo"] == "gpt-5.4-mini"  # modelo padrão do provedor
    r = put(client, h, {"mestre": True, "saudacao": True, "teto_tokens_mes": 50_000})
    assert r.status_code == 200
    j = r.get_json()
    itens = {i["chave"]: i for i in j["itens"]}
    assert itens["mestre"]["habilitado"] and itens["saudacao"]["habilitado"]
    assert itens["mestre"]["acoes_permitidas"] == ["desligar"]
    assert j["teto_efetivo_tokens_mes"] == 50_000
    assert {ator for _, ator in repo_ia.atualizacoes} == {adm.id}  # ator vem do JWT


def test_corpo_nao_define_o_ator(client, auth, adm, repo_ia) -> None:  # type: ignore[no-untyped-def]
    r = put(client, auth(adm.id), {"provedor": "openai", "alterado_por": "outro"})
    assert r.status_code == 400 and _cod(r) == "requisicao_invalida"
    assert repo_ia.atualizacoes == []


def test_sem_mudanca_nao_grava(client, auth, adm, repo_ia) -> None:  # type: ignore[no-untyped-def]
    h = auth(adm.id)
    put(client, h, {"provedor": "openai"})
    n = len(repo_ia.atualizacoes)
    assert put(client, h, {"provedor": "openai"}).status_code == 200
    assert len(repo_ia.atualizacoes) == n


@pytest.mark.parametrize(
    ("corpo", "status", "codigo"),
    [
        ({"mestre": True}, 409, "dependencia_recurso"),  # sem provedor escolhido
        ({"saudacao": True}, 409, "dependencia_recurso"),  # sem chave geral
        ({"provedor": "deepseek"}, 400, "requisicao_invalida"),
        ({"provedor": "openai", "modelo": "gpt-inexistente"}, 400, "requisicao_invalida"),
        ({"modelo": "gpt-5.4-mini"}, 409, "dependencia_recurso"),  # modelo sem provedor
        ({"provedor_reserva": "anthropic"}, 409, "dependencia_recurso"),  # reserva sem ativo
        ({"provedor": "openai", "provedor_reserva": "openai"}, 400, "requisicao_invalida"),
        ({"mestre": "sim"}, 400, "requisicao_invalida"),
        ({"teto_tokens_mes": 0}, 400, "requisicao_invalida"),
        ({"teto_tokens_mes": True}, 400, "requisicao_invalida"),
        ({"teto_tokens_mes": "100"}, 400, "requisicao_invalida"),
        ({"chave_api": "sk-x"}, 400, "requisicao_invalida"),
        ({}, 400, "requisicao_invalida"),
    ],
)
def test_validacoes(client, auth, adm, repo_ia, corpo, status, codigo) -> None:  # type: ignore[no-untyped-def]
    r = put(client, auth(adm.id), corpo)
    assert r.status_code == status and _cod(r) == codigo
    assert repo_ia.atualizacoes == []


def test_nao_aceita_corpo_que_nao_e_objeto(client, auth, adm) -> None:  # type: ignore[no-untyped-def]
    r = client.put("/api/admin/ia", headers=auth(adm.id), json=["mestre"])
    assert r.status_code == 400


@pytest.mark.parametrize("cfg_servidor", [cfg_ia(anthropic_api_key="")])
def test_provedor_sem_chave_no_servidor_e_recusado(client, auth, adm, repo_ia) -> None:  # type: ignore[no-untyped-def]
    h = auth(adm.id)
    r = put(client, h, {"provedor": "anthropic"})
    assert r.status_code == 409 and "Anthropic" in r.get_json()["erro"]["mensagem"]
    assert (
        put(client, h, {"provedor": "openai", "provedor_reserva": "anthropic"}).status_code == 409
    )
    provs = {p["chave"]: p for p in get(client, h).get_json()["provedores"]}
    assert provs["anthropic"]["chave_configurada"] is False


def test_trocar_provedor_reseta_modelo_e_reserva_precisa_ser_diferente(  # type: ignore[no-untyped-def]
    client, auth, adm, repo_ia
) -> None:
    h = auth(adm.id)
    put(client, h, {"provedor": "anthropic", "modelo": "claude-sonnet-5-5"})
    j = put(client, h, {"provedor": "openai"}).get_json()
    assert j["modelo"] == "gpt-5.4-mini"
    j = put(client, h, {"provedor_reserva": "anthropic"}).get_json()
    assert j["provedor_reserva"] == "anthropic" and j["provedor"] == "openai"
    assert put(client, h, {"provedor": "anthropic"}).status_code == 400  # igual à reserva


def test_nao_desligar_provedor_com_chave_geral_ligada(client, auth, adm) -> None:  # type: ignore[no-untyped-def]
    h = auth(adm.id)
    put(client, h, {"provedor": "openai"})
    put(client, h, {"mestre": True})
    r = put(client, h, {"provedor": None})
    assert r.status_code == 409 and _cod(r) == "dependencia_recurso"


def test_desligar_mestre_mantem_flags_mas_efetivo_e_desligado(  # type: ignore[no-untyped-def]
    client, auth, adm, repo_ia
) -> None:
    h = auth(adm.id)
    put(client, h, {"provedor": "openai"})
    put(client, h, {"mestre": True, "saudacao": True})
    j = put(client, h, {"mestre": False}).get_json()
    itens = {i["chave"]: i for i in j["itens"]}
    assert itens["saudacao"]["habilitado"] is True  # configuração preservada
    ia = ServicoIA(cfg_ia(), repo_ia)
    assert ia.disponivel("saudacao") is False  # mas efetivamente desligada


def test_respostas_de_erro_nao_vazam_chaves(client, auth, adm) -> None:  # type: ignore[no-untyped-def]
    r = put(client, auth(adm.id), {"chave_api": CHAVE_OPENAI})
    corpo = r.get_data(as_text=True)
    assert CHAVE_OPENAI not in corpo and CHAVE_ANTHROPIC not in corpo


# ------------------------------------------------------- correções da revisão de segurança
def test_excecao_inesperada_do_provedor_conta_falha_e_nao_trava_o_circuito() -> None:
    inesperado = [UnicodeEncodeError("ascii", "é", 0, 1, "x")] * 3
    s, repo = servico(openai=ProvedorRoteirizado("openai", inesperado))
    for _ in range(3):
        assert gerar(s) is None
    assert s.circuito_aberto("openai")  # exceção qualquer também conta como falha
    assert [u["status"] for u in repo.usos] == ["erro"] * 3


def test_meio_aberto_com_excecao_inesperada_reabre_em_vez_de_travar() -> None:
    t = [0.0]
    p = ProvedorRoteirizado("openai", [erro(), RuntimeError("x"), Resultado("Oi", 1, 1)])
    repo = RepoIAMemoria(replace(LIGADO))
    s = ServicoIA(
        cfg_ia(circuito_falhas=1, circuito_abertura_s=10),
        repo,
        provedores={"openai": p},
        relogio=lambda: t[0],
    )
    assert gerar(s) is None and s.circuito_aberto("openai")
    t[0] = 11
    assert gerar(s) is None  # tentativa de teste falha com exceção inesperada
    assert s.circuito_aberto("openai")  # reabriu (não ficou preso em "tentativa em curso")
    t[0] = 22
    assert gerar(s) is not None  # e depois do prazo volta a funcionar


def test_provedores_nao_confiam_em_proxy_do_ambiente() -> None:
    assert ProvedorOpenAI("k")._cliente._trust_env is False  # noqa: SLF001
    assert ProvedorAnthropic("k")._cliente._trust_env is False  # noqa: SLF001


def test_servidor_desligado_permite_so_desligar(repo, auth_admin, storage, avatares) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import emitir_token

    banco = RepoIAMemoria(ConfigIABanco(mestre=True, saudacao=True, provedor="openai"))
    verificador = VerificadorJWT(
        issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
    )
    ap = create_app(
        Config(
            jwt_issuer=ISSUER, jwt_audience=AUDIENCE, testing=True, url_aceite_convite=URL_ACEITE
        ),
        repo=repo,
        verificador=verificador,
        auth_admin=auth_admin,
        armazenamento=storage,
        armazenamento_avatares=avatares,
        servico_ia=ServicoIA(cfg_ia(habilitado=False), banco),
    )
    adm = repo.novo_perfil("admin")
    h = {"Authorization": f"Bearer {emitir_token(adm.id)}"}
    c = ap.test_client()
    itens = {i["chave"]: i for i in get(c, h).get_json()["itens"]}
    assert itens["mestre"]["acoes_permitidas"] == ["desligar"]
    assert put(c, h, {"mestre": True}).status_code == 404  # ligar/alterar continua recusado
    assert put(c, h, {"provedor": "anthropic"}).status_code == 404
    assert put(c, h, {"saudacao": False, "mestre": False}).status_code == 200
    assert banco.config.mestre is False and banco.config.saudacao is False


# ------------------------------------------------------------------- teste de conexão
TEXTO_MODELO = "ok-TEXTO-SECRETO-DO-MODELO"
ATIVO = ConfigIABanco(provedor="openai", modelo="gpt-5.4-mini", provedor_reserva="anthropic")


def _testar(client, h, corpo=None):  # type: ignore[no-untyped-def]
    if corpo is None:
        return client.post("/api/admin/ia/testar", headers=h)
    return client.post("/api/admin/ia/testar", headers=h, json=corpo)


def _com(servico_ia, repo_ia, banco=ATIVO, **provs):  # type: ignore[no-untyped-def]
    repo_ia.config = banco
    for nome, p in provs.items():
        servico_ia._provedores[nome] = p  # noqa: SLF001


def test_testar_sucesso_usa_provedor_ativo_e_registra_uso(  # type: ignore[no-untyped-def]
    client, auth, adm, repo_ia, servico_ia
) -> None:
    p = ProvedorRoteirizado("openai", [Resultado(TEXTO_MODELO, 12, 3)])
    _com(servico_ia, repo_ia, openai=p)  # chave geral e função desligadas: não importa
    r = _testar(client, auth(adm.id))
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True and j["tipo"] is None
    assert (j["provedor"], j["modelo"]) == ("openai", "gpt-5.4-mini")
    assert (j["tokens_entrada"], j["tokens_saida"]) == (12, 3)
    assert isinstance(j["latencia_ms"], int) and j["mensagem"]
    assert TEXTO_MODELO not in r.get_data(as_text=True)
    (uso,) = repo_ia.usos
    assert uso["funcao"] == "teste" and uso["status"] == "ok" and uso["usuario_id"] == adm.id
    assert (uso["provedor"], uso["modelo"]) == ("openai", "gpt-5.4-mini")
    assert uso["tokens_entrada"] == 12 and uso["unidade"] == adm.unidade
    assert p.chamadas[0]["usuario"] == "Responda apenas com a palavra: ok"


def test_testar_provedor_reserva_explicito(client, auth, adm, repo_ia, servico_ia) -> None:  # type: ignore[no-untyped-def]
    p = ProvedorRoteirizado("anthropic")
    _com(servico_ia, repo_ia, anthropic=p)
    j = _testar(client, auth(adm.id), {"provedor": "anthropic"}).get_json()
    assert j["ok"] is True and j["provedor"] == "anthropic" and j["modelo"] == "claude-haiku-5-5"


@pytest.mark.parametrize(
    ("erro_tipo", "trecho"),
    [
        ("auth", "chave"),
        ("limite", "limitou"),
        ("timeout", "Tempo esgotado"),
        ("rede", "Sem conexão"),
        ("http", "modelo"),
        ("formato", "AI_MAX_TOKENS"),
    ],
)
def test_testar_falhas_mapeadas_com_200(  # type: ignore[no-untyped-def]
    client, auth, adm, repo_ia, servico_ia, erro_tipo, trecho
) -> None:
    _com(servico_ia, repo_ia, openai=ProvedorRoteirizado("openai", [erro(erro_tipo)]))
    r = _testar(client, auth(adm.id))
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is False and j["tipo"] == erro_tipo and trecho in j["mensagem"]
    assert j["tokens_entrada"] == 0 and j["tokens_saida"] == 0
    esperado = "timeout" if erro_tipo == "timeout" else "erro"
    assert repo_ia.usos[0]["status"] == esperado and repo_ia.usos[0]["funcao"] == "teste"


def test_testar_excecao_inesperada_vira_tipo_erro(client, auth, adm, repo_ia, servico_ia) -> None:  # type: ignore[no-untyped-def]
    _com(servico_ia, repo_ia, openai=ProvedorRoteirizado("openai", [RuntimeError(CHAVE_OPENAI)]))
    r = _testar(client, auth(adm.id))
    assert r.status_code == 200 and r.get_json()["tipo"] == "erro"
    assert CHAVE_OPENAI not in r.get_data(as_text=True)


def test_testar_cota_excedida_nao_chama_provedor(client, auth, adm, repo_ia, servico_ia) -> None:  # type: ignore[no-untyped-def]
    p = ProvedorRoteirizado("openai")
    _com(servico_ia, repo_ia, openai=p)
    repo_ia.consumo_fixo = Consumo(usuario_hora=999, unidade_hora=0, tokens_mes={})
    j = _testar(client, auth(adm.id)).get_json()
    assert j["ok"] is False and j["tipo"] == "cota"
    assert p.chamadas == [] and repo_ia.usos == []


def test_testar_ignora_circuito_aberto_e_sucesso_fecha() -> None:
    p = ProvedorRoteirizado("openai", [erro(), Resultado(TEXTO_MODELO, 1, 1)])
    s, repo = servico(cfg_ia(circuito_falhas=1, circuito_abertura_s=1000), openai=p)
    assert gerar(s) is None and s.circuito_aberto("openai")
    r = s.testar("openai", banco=LIGADO, perfil=PERFIL)  # circuito aberto não bloqueia
    assert r.ok is True and not s.circuito_aberto("openai")
    assert [u["funcao"] for u in repo.usos] == ["saudacao", "teste"]


def test_testar_falha_conta_no_circuito() -> None:
    p = ProvedorRoteirizado("openai", [erro("rede")])
    s, _ = servico(cfg_ia(circuito_falhas=1), openai=p)
    assert s.testar("openai", banco=LIGADO, perfil=PERFIL).tipo == "rede"
    assert s.circuito_aberto("openai")


@pytest.mark.parametrize("cfg_servidor", [cfg_ia(habilitado=False)])
def test_testar_servidor_desligado_404_e_testes_vazios(  # type: ignore[no-untyped-def]
    client, auth, adm, repo_ia, servico_ia
) -> None:
    _com(servico_ia, repo_ia)
    r = _testar(client, auth(adm.id))
    assert r.status_code == 404 and _cod(r) == "recurso_desligado"
    assert get(client, auth(adm.id)).get_json()["testes_disponiveis"] == []


def test_testar_provedor_nao_configurado_409(client, auth, adm, repo_ia, servico_ia) -> None:  # type: ignore[no-untyped-def]
    _com(servico_ia, repo_ia, banco=ConfigIABanco())  # sem provedor ativo
    r = _testar(client, auth(adm.id))
    assert r.status_code == 409 and _cod(r) == "dependencia_recurso"
    _com(
        servico_ia, repo_ia, banco=ConfigIABanco(provedor="openai")
    )  # anthropic não é ativo/reserva
    r = _testar(client, auth(adm.id), {"provedor": "anthropic"})
    assert r.status_code == 409 and _cod(r) == "dependencia_recurso"


@pytest.mark.parametrize("cfg_servidor", [cfg_ia(anthropic_api_key="")])
def test_testar_provedor_sem_chave_409(client, auth, adm, repo_ia, servico_ia) -> None:  # type: ignore[no-untyped-def]
    _com(servico_ia, repo_ia)
    r = _testar(client, auth(adm.id), {"provedor": "anthropic"})
    assert r.status_code == 409 and _cod(r) == "dependencia_recurso"


@pytest.mark.parametrize(
    "corpo",
    [{"provedor": "gemini"}, {"provedor": 1}, {"outro": 1}, {"provedor": "openai", "x": 1}, [1]],
)
def test_testar_corpo_invalido_400(client, auth, adm, repo_ia, servico_ia, corpo) -> None:  # type: ignore[no-untyped-def]
    _com(servico_ia, repo_ia)
    r = _testar(client, auth(adm.id), corpo)
    assert r.status_code == 400 and _cod(r) == "requisicao_invalida"


def test_testar_json_malformado_400(client, auth, adm, repo_ia, servico_ia) -> None:  # type: ignore[no-untyped-def]
    _com(servico_ia, repo_ia)
    r = client.post(
        "/api/admin/ia/testar",
        headers={**auth(adm.id), "Content-Type": "application/json"},
        data="{nao e json",
    )
    assert r.status_code == 400 and _cod(r) == "requisicao_invalida"


def test_testar_exige_admin_e_token(client, auth, repo) -> None:  # type: ignore[no-untyped-def]
    assert _testar(client, {}).status_code == 401
    for role in ("solicitante", "autorizador"):
        assert _testar(client, auth(repo.novo_perfil(role).id)).status_code == 403


def test_testes_disponiveis_ativo_e_reserva(client, auth, adm, repo_ia, servico_ia) -> None:  # type: ignore[no-untyped-def]
    _com(servico_ia, repo_ia)
    assert get(client, auth(adm.id)).get_json()["testes_disponiveis"] == ["openai", "anthropic"]
    _com(servico_ia, repo_ia, banco=ConfigIABanco(provedor="openai"))
    assert get(client, auth(adm.id)).get_json()["testes_disponiveis"] == ["openai"]
    _com(servico_ia, repo_ia, banco=ConfigIABanco())
    assert get(client, auth(adm.id)).get_json()["testes_disponiveis"] == []


@pytest.mark.parametrize("cfg_servidor", [cfg_ia(anthropic_api_key="")])
def test_testes_disponiveis_sem_chave(client, auth, adm, repo_ia, servico_ia) -> None:  # type: ignore[no-untyped-def]
    _com(servico_ia, repo_ia)
    assert get(client, auth(adm.id)).get_json()["testes_disponiveis"] == ["openai"]


def test_testar_nao_vaza_chave_nem_texto_do_modelo_em_logs(  # type: ignore[no-untyped-def]
    client, auth, adm, repo_ia, servico_ia, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    respostas: list[Resultado | Exception] = [
        Resultado(TEXTO_MODELO, 1, 1),
        RuntimeError(CHAVE_OPENAI),
    ]
    _com(servico_ia, repo_ia, openai=ProvedorRoteirizado("openai", respostas))
    corpos = [_testar(client, auth(adm.id)).get_data(as_text=True) for _ in range(2)]
    visto = "".join(corpos) + caplog.text + repr(repo_ia.usos)
    for segredo in (CHAVE_OPENAI, CHAVE_ANTHROPIC, TEXTO_MODELO, "verificador de conectividade"):
        assert segredo not in visto


def test_testar_limite_5_por_minuto_por_usuario(client, auth, adm, repo_ia, servico_ia) -> None:  # type: ignore[no-untyped-def]
    _com(servico_ia, repo_ia, openai=ProvedorRoteirizado("openai", [Resultado("ok", 1, 1)] * 6))
    h = auth(adm.id)
    assert [_testar(client, h).status_code for _ in range(5)] == [200] * 5
    assert _testar(client, h).status_code == 429
