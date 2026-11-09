"""Resumo narrativo por IA (Fase 2): gate, permissão, prompt sem dado pessoal, saída e cache."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import pytest

from app import create_app
from app.auth.jwt import VerificadorJWT
from app.config import Config
from app.extensions import limiter
from app.ia.config import ConfigIA
from app.ia.provedores import Resultado
from app.ia.repositorio import ConfigIABanco
from app.ia.servico import ServicoIA
from app.services.relatorios import Conjunto
from app.services.resumo_ia import (
    CACHE_RESUMO,
    PROMPT_SISTEMA,
    prompt_painel,
    prompt_relatorio,
    validar_saida,
)
from tests.conftest import _CHAVE, AUDIENCE, ISSUER, URL_ACEITE
from tests.fakes_ia import ProvedorRoteirizado, RepoIAMemoria, erro

TEXTO_OK = "No período foram registradas 5 requisições, a maioria autorizada."
LIGADO = ConfigIABanco(
    mestre=True, saudacao=False, resumo=True, provedor="openai", modelo="gpt-5.4-mini"
)

NOME_SECRETO = "Beltrano Segredoso"
TRAJETO_SECRETO = "Rota Secretíssima XYZ"
OBS_SECRETA = "Observação confidencial 123"
PLACA = "ABC1D23"
EMAIL = "beltrano.segredoso@agu.gov.br"


class ProvedorFixo(ProvedorRoteirizado):
    """Responde TEXTO_OK quando a fila de respostas esvazia (em vez de 'Olá!')."""

    def gerar(self, **kw: Any):  # type: ignore[no-untyped-def]
        if not self.respostas:
            self.respostas.append(Resultado(TEXTO_OK, 10, 5))
        return super().gerar(**kw)


@pytest.fixture(autouse=True)
def _limpa() -> None:
    CACHE_RESUMO.limpar()


@pytest.fixture
def ia_env() -> tuple[ServicoIA, RepoIAMemoria, ProvedorFixo]:
    prov = ProvedorFixo("openai")
    repo_ia = RepoIAMemoria(LIGADO)
    cfg = ConfigIA(habilitado=True, openai_api_key="sk-teste-fake")
    return ServicoIA(cfg, repo_ia, provedores={"openai": prov}), repo_ia, prov


@pytest.fixture
def cli(repo, auth_admin, storage, avatares, ia_env):  # type: ignore[no-untyped-def]
    cfg = Config(
        jwt_issuer=ISSUER, jwt_audience=AUDIENCE, testing=True, url_aceite_convite=URL_ACEITE
    )
    verificador = VerificadorJWT(
        issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
    )
    a = create_app(
        cfg,
        repo=repo,
        verificador=verificador,
        auth_admin=auth_admin,
        armazenamento=storage,
        armazenamento_avatares=avatares,
        servico_ia=ia_env[0],
    )
    limiter.reset()
    return a.test_client()


def painel(cli, auth, perfil, nome="operacao", **q):  # type: ignore[no-untyped-def]
    return cli.post(
        f"/api/dashboards/{nome}/resumo", headers=auth(perfil.id), query_string=q or None
    )


def previa(cli, auth, perfil, tipo="movimentacao", **q):  # type: ignore[no-untyped-def]
    return cli.post(
        f"/api/relatorios/{tipo}/previa/resumo", headers=auth(perfil.id), query_string=q or None
    )


def nova_req(repo, sol, **extra):  # type: ignore[no-untyped-def]
    dados = {
        "usuario_id": sol.id,
        "nome_solicitante": NOME_SECRETO,
        "unidade": sol.unidade,
        "hora_saida": "2026-11-08T12:00:00+00:00",
        "trajeto": TRAJETO_SECRETO,
        "observacao": OBS_SECRETA,
        "veiculo_id": repo.novo_veiculo(),
        **extra,
    }
    return repo.inserir_requisicao(dados)


def prompt_enviado(prov: ProvedorRoteirizado, i: int = 0) -> str:
    ch = prov.chamadas[i]
    return ch["sistema"] + "\n" + ch["usuario"]


# ------------------------------------------------------------------ configuração (admin)
def put(cli, auth, adm, corpo):  # type: ignore[no-untyped-def]
    return cli.put("/api/admin/ia", headers=auth(adm.id), json=corpo)


def item(resp: Any, chave: str) -> dict[str, Any]:
    return {i["chave"]: i for i in resp.get_json()["itens"]}[chave]


def test_config_expoe_item_resumo(cli, auth, c) -> None:  # type: ignore[no-untyped-def]
    r = cli.get("/api/admin/ia", headers=auth(c.adm.id))
    it = item(r, "resumo")
    assert it["rotulo"] == "Resumo narrativo em dashboards e relatórios"
    assert "agregadas" in it["descricao"] and "nomes" in it["descricao"]
    assert it["habilitado"] is True and it["acoes_permitidas"] == ["desligar"]


def test_admin_liga_e_desliga_resumo(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    r = put(cli, auth, c.adm, {"resumo": False})
    assert r.status_code == 200
    assert item(r, "resumo")["habilitado"] is False
    assert item(r, "resumo")["acoes_permitidas"] == ["ligar"]
    assert ia_env[1].config.resumo is False and ia_env[1].atualizacoes[-1][1] == c.adm.id
    r = put(cli, auth, c.adm, {"resumo": True})
    assert item(r, "resumo")["habilitado"] is True and ia_env[1].config.resumo is True


def test_resumo_independe_da_saudacao(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    put(cli, auth, c.adm, {"saudacao": True})
    put(cli, auth, c.adm, {"resumo": False})
    assert ia_env[1].config.saudacao is True and ia_env[1].config.resumo is False


def test_ligar_resumo_exige_chave_geral(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    ia_env[1].config = ConfigIABanco(mestre=False, resumo=False, provedor="openai")
    r = put(cli, auth, c.adm, {"resumo": True})
    assert r.status_code == 409 and r.get_json()["erro"]["codigo"] == "dependencia_recurso"
    assert ia_env[1].config.resumo is False


def test_resumo_so_aceita_booleano(cli, auth, c) -> None:  # type: ignore[no-untyped-def]
    assert put(cli, auth, c.adm, {"resumo": "sim"}).status_code == 400


def test_servidor_desligado_so_permite_desligar_resumo(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    ia_env[0].cfg = ConfigIA(habilitado=False)
    assert put(cli, auth, c.adm, {"resumo": True}).status_code == 404
    r = put(cli, auth, c.adm, {"resumo": False, "mestre": False})
    assert r.status_code == 200 and ia_env[1].config.resumo is False


# ------------------------------------------------------------------ resumo_ia nos GETs
def test_get_painel_informa_resumo_ia(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    for perfil in (c.aut, c.adm):
        r = cli.get("/api/dashboards/operacao", headers=auth(perfil.id))
        assert r.status_code == 200 and r.get_json()["resumo_ia"] is True


@pytest.mark.parametrize(
    "banco",
    [
        replace(LIGADO, resumo=False),
        replace(LIGADO, mestre=False),
        ConfigIABanco(),
    ],
)
def test_get_painel_resumo_ia_falso_quando_desligado(cli, auth, c, ia_env, banco) -> None:  # type: ignore[no-untyped-def]
    ia_env[1].config = banco
    r = cli.get("/api/dashboards/volume", headers=auth(c.adm.id))
    assert r.status_code == 200 and r.get_json()["resumo_ia"] is False


def test_get_painel_resumo_ia_falso_com_ia_desligada_no_servidor(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    ia_env[0].cfg = ConfigIA(habilitado=False)
    assert (
        cli.get("/api/dashboards/volume", headers=auth(c.adm.id)).get_json()["resumo_ia"] is False
    )


def test_get_painel_resumo_ia_falso_sem_servico_ia(repo, auth_admin, auth, c) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import emitir_token

    cfg = Config(jwt_issuer=ISSUER, jwt_audience=AUDIENCE, testing=True)
    ver = VerificadorJWT(
        issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
    )
    a = create_app(cfg, repo=repo, verificador=ver, auth_admin=auth_admin)
    limiter.reset()
    h = {"Authorization": f"Bearer {emitir_token(c.adm.id)}"}
    assert a.test_client().get("/api/dashboards/volume", headers=h).get_json()["resumo_ia"] is False


def test_get_previa_informa_resumo_ia(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    r = cli.get("/api/relatorios/movimentacao/previa", headers=auth(c.sol.id))
    assert r.status_code == 200 and r.get_json()["resumo_ia"] is True
    # trilha de auditoria nunca tem resumo, mesmo com a IA ligada
    r = cli.get("/api/relatorios/auditoria/previa", headers=auth(c.adm.id))
    assert r.status_code == 200 and r.get_json()["resumo_ia"] is False
    ia_env[1].config = replace(LIGADO, resumo=False)
    r = cli.get("/api/relatorios/movimentacao/previa", headers=auth(c.sol.id))
    assert r.get_json()["resumo_ia"] is False


# ------------------------------------------------------------------ POST painel
def test_painel_sem_token_401(cli) -> None:  # type: ignore[no-untyped-def]
    r = cli.post("/api/dashboards/operacao/resumo")
    assert r.status_code == 401 and set(r.get_json()["erro"]) == {"codigo", "mensagem"}


def test_painel_solicitante_403_e_sem_chamar_ia(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    r = painel(cli, auth, c.sol)
    assert r.status_code == 403 and r.get_json()["erro"]["codigo"] == "sem_permissao"
    assert ia_env[2].chamadas == []


def test_painel_mesmo_escopo_do_get(cli, auth, c, repo, ia_env) -> None:  # type: ignore[no-untyped-def]
    # governança: só admin; opcionais: só com recurso ligado; inexistente: 404
    assert painel(cli, auth, c.aut, "governanca").status_code == 403
    assert painel(cli, auth, c.adm, "opcionais").status_code == 404  # recurso desligado
    assert painel(cli, auth, c.adm, "inexistente").status_code == 404
    assert ia_env[2].chamadas == []
    assert painel(cli, auth, c.adm, "governanca").status_code == 200
    repo.config = replace(repo.config, quilometragem=True)
    assert painel(cli, auth, c.aut, "opcionais").status_code == 200


def test_painel_filtros_invalidos_400(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    for q in ({"inicio": "ontem"}, {"unidade": "XX"}, {"granularidade": "ano"}):
        r = painel(cli, auth, c.adm, "volume", **q)
        assert r.status_code == 400, q
    assert ia_env[2].chamadas == []


def test_painel_resumo_ok(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    r = painel(cli, auth, c.aut, "volume", unidade="PU")
    assert r.status_code == 200
    j = r.get_json()
    assert set(j) == {"texto", "gerado_por_ia", "gerado_em", "escopo"}
    assert j["escopo"] == "Resumo dos números do painel com os filtros atuais"
    assert j["texto"] == TEXTO_OK and j["gerado_por_ia"] is True
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T.*", j["gerado_em"])
    uso = ia_env[1].usos[-1]
    assert uso["funcao"] == "resumo" and uso["usuario_id"] == c.aut.id and uso["status"] == "ok"
    prompt = ia_env[2].chamadas[0]["usuario"]
    assert "Painel: Volume e desfecho" in prompt and "Filtro de unidade: PU" in prompt


def test_painel_usa_filtros_da_query(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    painel(cli, auth, c.adm, "volume", inicio="2026-10-01", fim="2026-10-31")
    assert "Período: 2026-10-01 a 2026-10-31" in ia_env[2].chamadas[0]["usuario"]


def test_painel_gate_desligado_409(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    for banco in (replace(LIGADO, resumo=False), replace(LIGADO, mestre=False)):
        ia_env[1].config = banco
        r = painel(cli, auth, c.adm, "volume")
        assert r.status_code == 409 and r.get_json()["erro"]["codigo"] == "ia_indisponivel"
    ia_env[0].cfg = ConfigIA(habilitado=False)
    ia_env[1].config = LIGADO
    assert painel(cli, auth, c.adm, "volume").status_code == 409
    assert ia_env[2].chamadas == []


def test_painel_falha_do_provedor_502(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    ia_env[2].respostas = [erro("http")]
    r = painel(cli, auth, c.adm, "volume")
    assert r.status_code == 502
    e = r.get_json()["erro"]
    assert e["codigo"] == "ia_falhou" and "Não foi possível gerar o resumo" in e["mensagem"]


@pytest.mark.parametrize(
    "saida",
    [
        "x" * 601,
        "curto",
        "   ",
        "Veja <b>isso</b> agora mesmo, pessoal",
        "Acesse https://exemplo.org para ver detalhes",
        "Mais informações em agu.gov.br hoje mesmo",
        "Fale com fulano@agu.gov.br sobre os números",
        "Total {nome} requisições registradas no período",
    ],
)
def test_painel_saida_invalida_502_e_nao_fica_em_cache(cli, auth, c, ia_env, saida) -> None:  # type: ignore[no-untyped-def]
    ia_env[2].respostas = [Resultado(saida, 5, 5)]
    r = painel(cli, auth, c.adm, "volume")
    assert r.status_code == 502 and r.get_json()["erro"]["codigo"] == "ia_falhou"
    assert saida.strip()[:20] not in r.get_data(as_text=True) or not saida.strip()
    # nova tentativa gera de novo (a saída ruim não foi guardada)
    r2 = painel(cli, auth, c.adm, "volume")
    assert r2.status_code == 200 and len(ia_env[2].chamadas) == 2


def test_painel_saida_multilinha_vira_uma_linha(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    ia_env[2].respostas = [
        Resultado("Primeira frase do resumo.\n\nSegunda frase  do resumo.", 5, 5)
    ]
    r = painel(cli, auth, c.adm, "volume")
    assert r.get_json()["texto"] == "Primeira frase do resumo. Segunda frase do resumo."


def test_painel_rate_limit_por_usuario(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    codigos = [painel(cli, auth, c.adm, "volume").status_code for _ in range(11)]
    assert codigos[:10] == [200] * 10 and codigos[10] == 429
    assert "erro" in painel(cli, auth, c.adm, "volume").get_json()
    assert painel(cli, auth, c.aut, "volume").status_code == 200  # outro usuário, mesmo IP


def test_painel_cache_evita_nova_chamada(cli, auth, c, repo, ia_env) -> None:  # type: ignore[no-untyped-def]
    a = painel(cli, auth, c.adm, "volume").get_json()
    b = painel(cli, auth, c.adm, "volume").get_json()
    assert a == b and len(ia_env[2].chamadas) == 1 and len(ia_env[1].usos) == 1
    # outros filtros: outra chave
    painel(cli, auth, c.adm, "volume", unidade="PF")
    assert len(ia_env[2].chamadas) == 2
    # outro usuário: não reaproveita o texto de terceiros
    painel(cli, auth, c.aut, "volume")
    assert len(ia_env[2].chamadas) == 3


def test_painel_cache_muda_quando_os_numeros_mudam(cli, auth, c, repo, ia_env) -> None:  # type: ignore[no-untyped-def]
    painel(cli, auth, c.adm, "operacao")
    nova_req(repo, c.sol)
    painel(cli, auth, c.adm, "operacao")
    assert len(ia_env[2].chamadas) == 2
    assert prompt_enviado(ia_env[2], 0) != prompt_enviado(ia_env[2], 1)


def test_painel_cache_nao_burla_o_gate(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    assert painel(cli, auth, c.adm, "volume").status_code == 200
    ia_env[1].config = replace(LIGADO, resumo=False)
    assert painel(cli, auth, c.adm, "volume").status_code == 409


# ------------------------------------------------------------------ LGPD: o prompt
PROIBIDOS = [
    NOME_SECRETO,
    "Beltrano",
    "Segredoso",
    TRAJETO_SECRETO,
    "Secretíssima",
    OBS_SECRETA,
    PLACA,
    EMAIL,
    "agu.gov.br",
]
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _sem_dado_pessoal(texto: str, repo: Any) -> None:
    for proibido in PROIBIDOS:
        assert proibido.lower() not in texto.lower(), proibido
    assert not UUID.search(texto)
    for rid in repo.requisicoes:
        assert rid not in texto and rid[:8] not in texto
    for vid in repo.veiculos:
        assert vid not in texto
    for pid in repo.perfis:
        assert pid not in texto


@pytest.mark.parametrize("nome", ["operacao", "volume", "tempos", "unidades", "veiculos"])
def test_prompt_de_painel_sem_dado_pessoal(cli, auth, c, repo, ia_env, nome) -> None:  # type: ignore[no-untyped-def]
    for _ in range(4):
        nova_req(repo, c.sol)
    for v in repo.veiculos.values():
        v["placa"] = PLACA
    repo.contas_auth[c.sol.id]["email"] = EMAIL
    assert painel(cli, auth, c.adm, nome).status_code == 200
    _sem_dado_pessoal(prompt_enviado(ia_env[2]), repo)


def test_prompt_de_painel_nao_leva_a_lista_de_abertas(cli, auth, c, repo, ia_env) -> None:  # type: ignore[no-untyped-def]
    for _ in range(5):
        nova_req(repo, c.sol)
    painel(cli, auth, c.adm, "operacao")
    prompt = ia_env[2].chamadas[0]["usuario"]
    assert "Requisições aguardando autorização: 5" in prompt
    assert "Requisições abertas da unidade PU: 5" in prompt
    _sem_dado_pessoal(prompt, repo)


def test_prompt_de_governanca_sem_auditoria_nem_ids(cli, auth, c, repo, ia_env) -> None:  # type: ignore[no-untyped-def]
    repo.dash_governanca = lambda: {  # type: ignore[method-assign]
        "perfis": {"admin": {"ativos": 1, "inativos": 0}, "solicitante": {"ativos": 8}},
        "convites": {"pendentes": 2, "usados": 7},
        "auditoria_admin": [{"id": 1, "registro_id": c.sol.id, "ator_id": c.adm.id}],
        "configuracoes": {"mfa": True, "quilometragem": False},
    }
    assert painel(cli, auth, c.adm, "governanca").status_code == 200
    prompt = ia_env[2].chamadas[0]["usuario"]
    assert c.sol.id not in prompt and c.adm.id not in prompt and "auditoria" not in prompt.lower()
    assert "administradores: menos de 3" in prompt and "solicitantes: 8" in prompt
    assert "Recurso Verificação em duas etapas: ligado" in prompt


def test_prompt_de_relatorio_nunca_pede_nomes_nem_trajeto(cli, auth, c, repo, ia_env) -> None:  # type: ignore[no-untyped-def]
    for _ in range(4):
        nova_req(repo, c.sol)
    repo.rel_extra_colunas = {
        "placa": PLACA,
        "solicitante": NOME_SECRETO,
        "trajeto": TRAJETO_SECRETO,
    }
    r = previa(cli, auth, c.adm, "movimentacao", incluir_nomes="true", incluir_trajeto="true")
    assert r.status_code == 200
    chamada = repo.chamadas_de("movimentacao")[-1]
    assert chamada["incluir_nomes"] is False and chamada["incluir_trajeto"] is False
    _sem_dado_pessoal(prompt_enviado(ia_env[2]), repo)
    assert "Registros no relatório: 4" in ia_env[2].chamadas[0]["usuario"]


@pytest.mark.parametrize("tipo", ["resumo", "tempos", "pendencias", "movimentacao"])
def test_prompt_de_cada_relatorio_sem_dado_pessoal(cli, auth, c, repo, ia_env, tipo) -> None:  # type: ignore[no-untyped-def]
    for _ in range(4):
        nova_req(repo, c.sol)
    assert previa(cli, auth, c.adm, tipo, agrupar="veiculo").status_code in (200, 400)
    if ia_env[2].chamadas:
        _sem_dado_pessoal(prompt_enviado(ia_env[2]), repo)


def test_prompt_pede_so_descrever_sem_inventar(ia_env) -> None:  # type: ignore[no-untyped-def]
    s = PROMPT_SISTEMA.lower()
    assert "somente os números" in s and "não invente" in s and "recomendações" in s
    assert "2 a 4 frases" in s


# ------------------------------------------------------------------ POST relatórios
def test_previa_sem_token_401(cli) -> None:  # type: ignore[no-untyped-def]
    assert cli.post("/api/relatorios/movimentacao/previa/resumo").status_code == 401


def test_previa_mesma_permissao_da_previa(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    assert previa(cli, auth, c.sol, "resumo").status_code == 403  # relatório só de staff
    assert previa(cli, auth, c.aut, "auditoria").status_code == 403
    assert previa(cli, auth, c.sol, "inexistente").status_code == 404
    assert previa(cli, auth, c.adm, "opcionais_km").status_code == 404  # recurso desligado
    assert ia_env[2].chamadas == []


def test_previa_auditoria_nao_tem_resumo_nem_para_admin(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    r = previa(cli, auth, c.adm, "auditoria")
    assert r.status_code == 400 and "erro" in r.get_json()
    assert ia_env[2].chamadas == []


def test_previa_solicitante_so_resume_o_proprio_escopo(cli, auth, c, repo, ia_env) -> None:  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol)
    for _ in range(3):
        nova_req(repo, c.outro)
    r = previa(cli, auth, c.sol)
    assert r.status_code == 200
    assert repo.chamadas_de("movimentacao")[-1]["usuario"] == c.sol.id  # escopo do JWT
    assert "Registros no relatório: menos de 3" in ia_env[2].chamadas[0]["usuario"]
    assert r.get_json()["gerado_por_ia"] is True


def test_previa_staff_resume_tudo(cli, auth, c, repo, ia_env) -> None:  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol)
    for _ in range(3):
        nova_req(repo, c.outro)
    assert previa(cli, auth, c.aut).status_code == 200
    assert repo.chamadas_de("movimentacao")[-1]["usuario"] is None
    assert "Registros no relatório: 4" in ia_env[2].chamadas[0]["usuario"]


def test_previa_filtros_invalidos_400(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    assert previa(cli, auth, c.adm, "movimentacao", unidade="XX").status_code == 400
    assert previa(cli, auth, c.adm, "movimentacao", inicio="ontem").status_code == 400
    assert ia_env[2].chamadas == []


def test_previa_gate_falha_saida_cache_e_limite(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    ia_env[1].config = replace(LIGADO, resumo=False)
    r = previa(cli, auth, c.adm)
    assert r.status_code == 409 and r.get_json()["erro"]["codigo"] == "ia_indisponivel"
    ia_env[1].config = LIGADO
    ia_env[2].respostas = [erro("timeout")]
    assert previa(cli, auth, c.adm).status_code == 502
    ia_env[2].respostas = [Resultado("Veja http://x.com para detalhes aqui", 1, 1)]
    assert previa(cli, auth, c.adm).status_code == 502
    n = len(ia_env[2].chamadas)
    assert previa(cli, auth, c.adm).status_code == 200
    assert previa(cli, auth, c.adm).status_code == 200  # cache
    assert len(ia_env[2].chamadas) == n + 1
    codigos = [previa(cli, auth, c.adm).status_code for _ in range(10)]
    assert codigos[-1] == 429
    assert previa(cli, auth, c.aut).status_code == 200  # limite por usuário


# ------------------------------------------------------------------ construtores (unidade)
def agreg(
    chave: str, dados: dict[str, Any], rotulo: str = "Painel", **filtros: Any
) -> dict[str, Any]:
    return {"painel": chave, "rotulo": rotulo, "filtros": filtros, "dados": dados}


def test_celulas_pequenas_por_unidade_sao_generalizadas() -> None:
    p = prompt_painel(
        agreg(
            "unidades",
            {
                "itens": [
                    {"unidade": "PU", "requisicoes": 1, "usos": 1, "horas_uso": 2.5},
                    {"unidade": "PF", "requisicoes": 12, "usos": 9, "horas_uso": 30.5},
                    {"unidade": "CJU", "requisicoes": 0, "usos": 0, "horas_uso": 0},
                ]
            },
        )
    )
    assert "Unidade PU: requisições: menos de 3" in p
    assert "Unidade PU: horas de uso" not in p  # 1 uso: omitido
    assert "Unidade PF: requisições: 12" in p and "Unidade PF: horas de uso: 30,5" in p
    assert "Unidade CJU: requisições: 0" in p


def test_tempos_com_poucos_casos_omitem_valores() -> None:
    p = prompt_painel(
        agreg(
            "tempos",
            {
                "espera_autorizacao": {"n": 2, "media_s": 100, "mediana_s": 90, "max_s": 150},
                "duracao_uso": {"n": 5, "media_s": 3700, "mediana_s": 3600, "max_s": 7300},
                "saida_vs_prevista": {"n": 0},
            },
        )
    )
    assert "Espera pela autorização: menos de 3 casos (valores omitidos)" in p
    assert "100" not in p and "150" not in p
    assert "Duração do uso: 5 casos, média 1 h 01 min, mediana 1 h 00 min, máxima 2 h 01 min" in p


def test_operacao_omite_tempo_em_uso_de_caso_unico() -> None:
    p = prompt_painel(
        agreg(
            "operacao",
            {
                "veiculos": {"ativos": 1, "livres": 0, "reservados": 0, "em_uso": 1},
                "em_uso": {"total": 1, "decorrido_medio_s": 5000, "decorrido_max_s": 5000},
                "abertas": [{"id": "x", "unidade": "PU", "status": "em_uso", "veiculo_id": "v"}],
            },
        )
    )
    assert "Requisições em uso: menos de 3" in p and "decorrido" not in p.lower()
    assert "unidade PU: menos de 3" in p


def test_veiculos_agregado_sem_ids() -> None:
    p = prompt_painel(
        agreg(
            "veiculos",
            {
                "horas_periodo": 720,
                "itens": [
                    {"veiculo_id": "aaaa", "usos": 2, "horas_uso": 4, "taxa_ocupacao": 0.1},
                    {"veiculo_id": "bbbb", "usos": 3, "horas_uso": 6, "taxa_ocupacao": 0.3},
                ],
            },
            veiculo="11111111-1111-4111-8111-111111111111",
        )
    )
    assert "Veículos considerados: 2" in p and "Usos concluídos (soma): 5" in p
    assert "aaaa" not in p and "11111111" not in p
    assert "Filtro por um veículo específico: sim" in p


def test_opcionais_km_so_com_viagens_suficientes() -> None:
    dados = {
        "quilometragem": {"habilitado": True, "viagens": 2, "km_rodado": 120},
        "checklist": {
            "habilitado": True,
            "total": 10,
            "com_avaria": 1,
            "requisicoes_com_avaria": [{"requisicao_id": "r1"}],
        },
    }
    p = prompt_painel(agreg("opcionais", dados))
    assert "Viagens com quilometragem: menos de 3" in p and "Quilômetros" not in p
    assert "Checklists com avaria: menos de 3" in p and "r1" not in p


def test_volume_resume_serie_sem_listar_todos_os_pontos() -> None:
    serie = [{"periodo": f"2026-10-{d:02d}", "total": d} for d in range(1, 29)]
    p = prompt_painel(
        agreg(
            "volume",
            {
                "totais": {
                    "total": 406,
                    "autorizadas": 300,
                    "negadas": 6,
                    "canceladas": 100,
                    "solicitadas_pendentes": 0,
                    "pct_autorizadas": 73.9,
                },
                "serie": serie,
            },
            granularidade="dia",
        )
    )
    assert "Período com mais requisições: 2026-10-28 (28 requisições)" in p
    assert "Percentual autorizado: 73,9%" in p and p.count("2026-10-") == 1


def test_painel_desconhecido_ou_dados_estranhos_nao_quebram() -> None:
    assert "sem dados no recorte" in prompt_painel(agreg("nada", {"x": "y"}))
    assert "sem dados no recorte" in prompt_painel(
        agreg("volume", {"totais": "?", "serie": [1, None]})
    )
    p = prompt_painel(agreg("unidades", {"itens": [{"unidade": PLACA, "requisicoes": 9}]}))
    assert PLACA not in p  # unidade fora da lista é ignorada


def conjunto(tipo: str, linhas: list[dict[str, Any]], truncado: bool = False) -> Conjunto:
    return Conjunto(tipo, tipo, {}, [], linhas, truncado)


def test_relatorio_resumo_por_veiculo_nao_envia_rotulos() -> None:
    linhas = [
        {"rotulo": f"{PLACA} Carro {i}", "requisicoes": 4, "autorizadas": 3, "horas_uso": 1.5}
        for i in range(3)
    ]
    p = prompt_relatorio(conjunto("resumo", linhas), {"agrupar": "veiculo"})
    assert PLACA not in p and "Carro" not in p
    assert "Grupos no relatório: 3" in p and "Requisições (soma): 12" in p


def test_relatorio_resumo_por_unidade_generaliza() -> None:
    linhas = [
        {"rotulo": "PU", "requisicoes": 1},
        {"rotulo": "PF", "requisicoes": 8},
        {"rotulo": "Fulano de Tal", "requisicoes": 5},  # rótulo fora do padrão: descartado
    ]
    p = prompt_relatorio(conjunto("resumo", linhas), {"agrupar": "unidade"})
    assert "Unidade PU: requisições: menos de 3" in p and "Unidade PF: requisições: 8" in p
    assert "Fulano" not in p


def test_relatorio_resumo_por_dia_mostra_pico() -> None:
    linhas = [
        {"rotulo": "2026-11-01", "requisicoes": 2},
        {"rotulo": "2026-11-02", "requisicoes": 9},
    ]
    p = prompt_relatorio(conjunto("resumo", linhas), {"agrupar": "dia"})
    assert "Grupo com mais requisições: 2026-11-02 (9)" in p


def test_relatorio_tempos_por_unidade_e_veiculo() -> None:
    linhas = [
        {
            "rotulo": "PU",
            "espera_n": 2,
            "espera_media_s": 100,
            "uso_n": 5,
            "uso_media_s": 600,
            "uso_max_s": 900,
            "saida_dif_n": 0,
        },
        {
            "rotulo": "PF",
            "espera_n": 6,
            "espera_media_s": 120,
            "uso_n": 7,
            "uso_media_s": 300,
            "uso_max_s": 1200,
            "saida_dif_n": 0,
        },
    ]
    p = prompt_relatorio(conjunto("tempos", linhas), {"agrupar": "unidade"})
    assert "Espera pela autorização em PU: menos de 3 casos" in p and "100 s" not in p
    assert "Duração do uso em PF: 7 casos, média 5 min" in p
    por_veiculo = [{**ln, "rotulo": f"{PLACA}-{i}"} for i, ln in enumerate(linhas)]
    p2 = prompt_relatorio(conjunto("tempos", por_veiculo), {"agrupar": "veiculo"})
    assert PLACA not in p2 and "Duração do uso: casos: 12" in p2
    assert "Duração do uso: maior valor: 20 min" in p2


def test_relatorio_por_linha_conta_categorias_e_omite_poucos() -> None:
    linhas = [
        {
            "status": "concluida",
            "unidade": "PU",
            "duracao_uso_s": 3600,
            "veiculo": PLACA,
            "codigo": "ab12",
        },
        {"status": "concluida", "unidade": "PU", "duracao_uso_s": 1800},
        {"status": "negada", "unidade": "PF", "duracao_uso_s": None},
        {"status": "concluida", "unidade": "PU", "duracao_uso_s": 7200, "km_rodado": 10},
    ]
    p = prompt_relatorio(conjunto("movimentacao", linhas, truncado=True), {})
    assert "Registros no relatório: 4" in p and "Situação concluida: 3" in p
    assert "Situação negada: menos de 3" in p and "Unidade PU: 3" in p
    assert "Duração do uso: 3 casos, média 1 h 10 min, máxima 2 h 00 min" in p
    assert "Km rodado: menos de 3 viagens (valores omitidos)" in p
    assert "mais registros que o limite" in p
    assert PLACA not in p and "ab12" not in p


def test_relatorio_de_auditoria_e_recusado_no_construtor() -> None:
    from app.errors import ErroApi

    with pytest.raises(ErroApi):
        prompt_relatorio(conjunto("auditoria", [{"ator_nome": "Fulano"}]), {})


# ------------------------------------------------------------------ validação da saída
@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        (TEXTO_OK, TEXTO_OK),
        ("  Duas   frases.\nSegunda linha aqui.  ", "Duas frases. Segunda linha aqui."),
        ("x" * 600, "x" * 600),
        ("x" * 601, None),
        ("curto", None),
        ("<p>texto com html</p>", None),
        ("veja http://x.com agora", None),
        ("veja HTTPS://X.COM agora", None),
        ("escreva para a@b.co hoje", None),
        ("acesse agu.gov.br hoje", None),
        ("modelo {nome} sem preencher", None),
        ("controle\x07 invisível aqui", None),
        ("Bom dia. Os números seguem abaixo.", "Bom dia. Os números seguem abaixo."),
        ("Fim do período.Outra frase colada", None),  # "periodo.Outra" parece domínio
        ("Veja exemplo.io para detalhes aqui", None),
        ("Foram 1.200 requisições no período", "Foram 1.200 requisições no período"),
    ],
)
def test_validar_saida(bruto: str, esperado: str | None) -> None:
    assert validar_saida(bruto) == esperado


def test_nao_loga_prompt_nem_resposta(cli, auth, c, ia_env, caplog) -> None:  # type: ignore[no-untyped-def]
    import logging

    ia_env[2].respostas = [Resultado("Texto secreto do resumo gerado aqui.", 1, 1)]
    with caplog.at_level(logging.DEBUG):
        painel(cli, auth, c.adm, "volume")
    assert "Texto secreto" not in caplog.text and "Painel:" not in caplog.text


# ------------------------------------------------------------------ revisão: células pequenas
def test_operacao_totais_pequenos_viram_menos_de_3() -> None:
    p = prompt_painel(
        agreg(
            "operacao",
            {
                "veiculos": {"ativos": 2, "livres": 0, "reservados": 2, "em_uso": 0},
                "solicitadas": {"total": 2},
                "autorizadas_sem_saida": {"total": 7},
                "em_uso": {"total": 0},
            },
        )
    )
    assert "Requisições aguardando autorização: menos de 3" in p
    assert "Requisições autorizadas ainda sem saída: 7" in p
    assert "Veículos reservados (pedido solicitado ou autorizado): menos de 3" in p
    assert "Requisições em uso: 0" in p and "Veículos ativos: 2" in p


def test_volume_totais_pequenos_e_percentuais_omitidos() -> None:
    totais = {
        "total": 20,
        "autorizadas": 17,
        "negadas": 1,
        "canceladas": 2,
        "solicitadas_pendentes": 0,
        "pct_autorizadas": 85.0,
        "pct_negadas": 5.0,
        "pct_canceladas": 10.0,
    }
    serie = [{"periodo": "2026-10-01", "total": 2}, {"periodo": "2026-10-02", "total": 1}]
    p = prompt_painel(agreg("volume", {"totais": totais, "serie": serie}))
    assert "Negadas: menos de 3" in p and "Canceladas: menos de 3" in p
    assert "Percentual autorizado: 85%" in p
    assert "Percentual negado" not in p and "Percentual cancelado" not in p
    # série com 3 requisições no total: o pico (2) é pequeno demais para citar
    assert "Período com mais requisições: 2026-10-01 (menos de 3 requisições)" in p


def test_volume_total_pequeno_omite_percentuais_e_serie() -> None:
    totais = {
        "total": 2,
        "autorizadas": 2,
        "negadas": 0,
        "canceladas": 0,
        "solicitadas_pendentes": 0,
        "pct_autorizadas": 100.0,
        "pct_negadas": 0.0,
    }
    serie = [{"periodo": "2026-10-01", "total": 2}]
    p = prompt_painel(agreg("volume", {"totais": totais, "serie": serie}))
    assert "Requisições criadas: menos de 3" in p and "Percentual" not in p
    assert "Período com mais requisições" not in p and "Média de requisições" not in p


def test_checklists_e_veiculos_pequenos() -> None:
    ck = {"checklist": {"habilitado": True, "total": 2, "com_avaria": 0}}
    p = prompt_painel(agreg("opcionais", ck))
    assert "Checklists registrados: menos de 3" in p and "Checklists com avaria: 0" in p
    v = prompt_painel(
        agreg("veiculos", {"horas_periodo": 720, "itens": [{"usos": 2, "horas_uso": 3.5}]})
    )
    assert "Usos concluídos (soma): menos de 3" in v
    assert "Horas de uso" not in v.replace("Horas do período", "")


def test_relatorio_resumo_somas_pequenas_e_horas_omitidas() -> None:
    linhas = [{"rotulo": "2026-11-01", "requisicoes": 2, "autorizadas": 1, "horas_uso": 3.5}]
    p = prompt_relatorio(conjunto("resumo", linhas), {"agrupar": "dia"})
    assert "Requisições (soma): menos de 3" in p and "Autorizadas (soma): menos de 3" in p
    assert "Horas de uso" not in p
    assert "Grupo com mais requisições: 2026-11-01 (menos de 3)" in p


def test_relatorio_tempos_casos_pequenos() -> None:
    linhas = [{"rotulo": "PU", "espera_n": 2, "uso_n": 0, "saida_dif_n": 1}]
    p = prompt_relatorio(conjunto("tempos", linhas), {"agrupar": "unidade"})
    assert "Espera pela autorização: casos: menos de 3" in p
    assert "Duração do uso: casos: 0" in p


# ------------------------------------------------------------------ revisão: escopo do relatório
def test_resumo_de_relatorio_cobre_o_conjunto_completo(cli, auth, c, repo, ia_env) -> None:  # type: ignore[no-untyped-def]
    for _ in range(60):  # mais que as 50 linhas da prévia
        nova_req(repo, c.sol)
    r = previa(cli, auth, c.adm)
    assert r.status_code == 200
    assert r.get_json()["escopo"] == "Resumo do relatório completo (60 registros)"
    prompt = ia_env[2].chamadas[0]["usuario"]
    assert "Escopo: conjunto completo do relatório, 60 registros" in prompt
    assert "Registros no relatório: 60" in prompt
    # a consulta ao banco usou o teto do relatório, não o da prévia (50)
    assert repo.chamadas_de("movimentacao")[-1]["limite"] > 1000


def test_escopo_parcial_quando_passa_do_limite() -> None:
    from app.services.resumo_ia import escopo_relatorio

    c = conjunto("movimentacao", [{"status": "concluida"}] * 5, truncado=True)
    p = prompt_relatorio(c, {})
    assert "Escopo: parcial, os primeiros 5 registros" in p and "limite de processamento" in p
    assert escopo_relatorio(c).startswith("Resumo parcial: primeiros 5 registros")
    assert escopo_relatorio(conjunto("resumo", [{}] * 4)) == (
        "Resumo do relatório completo (4 grupos)"
    )
    assert escopo_relatorio(conjunto("movimentacao", [{}])) == (
        "Resumo do relatório completo (menos de 3 registros)"
    )


def test_get_previa_traz_escopo_so_com_resumo_ligado(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    j = cli.get("/api/relatorios/movimentacao/previa", headers=auth(c.adm.id)).get_json()
    assert j["resumo_ia"] is True and "relatório completo" in j["escopo"]
    ia_env[1].config = replace(LIGADO, resumo=False)
    j = cli.get("/api/relatorios/movimentacao/previa", headers=auth(c.adm.id)).get_json()
    assert j["resumo_ia"] is False and j["escopo"] is None


# ------------------------------------------------------------------ revisão: números e domínios
def test_numeros_inventados_sao_recusados() -> None:
    prompt = "Painel: X\n- Requisições criadas: 406\n- Percentual autorizado: 73,9%\n- Total: 1200"
    assert validar_saida("Foram 406 requisições, 73,9% autorizadas.", prompt) is not None
    assert validar_saida("Foram 1.200 no total, em 3 a 4 grupos.", prompt) is not None
    assert validar_saida("Foram 407 requisições no período.", prompt) is None
    assert validar_saida("Cerca de 74% foram autorizadas no período.", prompt) is None
    assert validar_saida("Foram 5 requisições no período filtrado.", prompt) is not None
    assert validar_saida("Foram 5 requisições, 999 delas autorizadas.", None) is not None


def test_saida_com_numero_inventado_502(cli, auth, c, ia_env) -> None:  # type: ignore[no-untyped-def]
    ia_env[2].respostas = [Resultado("Foram registradas 9999 requisições no período.", 5, 5)]
    r = painel(cli, auth, c.adm, "volume")
    assert r.status_code == 502 and r.get_json()["erro"]["codigo"] == "ia_falhou"


# ------------------------------------------------------------------ revisão: cache limitado
def test_cache_poda_vencidos_e_limita_tamanho() -> None:
    from app.saudacao.servico import CacheTTL

    t = [0.0]
    cache = CacheTTL(ttl=10, relogio=lambda: t[0], max_entradas=3)
    for k in "abc":
        cache.obter(k, lambda k=k: k)
    assert len(cache) == 3
    cache.obter("d", lambda: "d")  # estourou o limite: sai a mais antiga ("a")
    assert len(cache) == 3
    chamadas: list[str] = []
    assert cache.obter("a", lambda: chamadas.append("a") or "a") == "a" and chamadas == ["a"]
    t[0] = 11  # tudo venceu; a próxima gravação poda tudo, menos a nova
    cache.obter("z", lambda: "z")
    assert len(cache) == 1
    cache.limpar()
    assert len(cache) == 0


def test_cache_resumo_tem_limite_padrao() -> None:
    assert CACHE_RESUMO.max_entradas == 512
