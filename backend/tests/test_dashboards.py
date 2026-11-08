"""Dashboards: permissões por painel, filtros (fuso, fim inclusivo), operação com itens."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest
from postgrest.exceptions import APIError

from app.errors import RequisicaoInvalida
from app.repositorios.supabase_repo import traduzir_erro_banco
from app.services.dashboards import ServicoDashboards, validar_filtros
from app.services.requisicoes import ServicoRequisicoes
from tests.fakes_admin import erro_db
from tests.test_requisicoes import acao, criar

PAINEIS_STAFF = ["operacao", "volume", "tempos", "unidades", "veiculos"]
PAINEIS_JANELA = ["volume", "tempos", "unidades", "veiculos", "opcionais"]


def get(client, auth, perfil, caminho="/api/dashboards"):  # type: ignore[no-untyped-def]
    return client.get(caminho, headers=auth(perfil.id))


def chaves(resp: Any) -> list[str]:
    return [p["chave"] for p in resp.get_json()["paineis"]]


# ---------------------------------------------------------------- autenticação e permissão
@pytest.mark.parametrize("caminho", ["/api/dashboards", "/api/dashboards/operacao"])
def test_sem_token_401(client, caminho):  # type: ignore[no-untyped-def]
    r = client.get(caminho)
    assert r.status_code == 401
    assert set(r.get_json()["erro"]) == {"codigo", "mensagem"}


@pytest.mark.parametrize("caminho", ["/api/dashboards", "/api/dashboards/operacao"])
def test_solicitante_403(client, auth, c, caminho):  # type: ignore[no-untyped-def]
    r = get(client, auth, c.sol, caminho)
    assert r.status_code == 403 and r.get_json()["erro"]["codigo"] == "sem_permissao"


def test_perfil_inativo_nao_acessa(client, auth, repo):  # type: ignore[no-untyped-def]
    inativo = repo.novo_perfil("admin", ativo=False)
    assert get(client, auth, inativo).status_code == 403


def test_autorizador_lista_sem_governanca_e_admin_com(client, auth, c):  # type: ignore[no-untyped-def]
    assert chaves(get(client, auth, c.aut)) == PAINEIS_STAFF
    assert chaves(get(client, auth, c.adm)) == [*PAINEIS_STAFF, "governanca"]


def test_autorizador_nao_ve_governanca_e_admin_ve(client, auth, c):  # type: ignore[no-untyped-def]
    r = get(client, auth, c.aut, "/api/dashboards/governanca")
    assert r.status_code == 403
    r = get(client, auth, c.adm, "/api/dashboards/governanca")
    assert r.status_code == 200 and r.get_json()["painel"] == "governanca"
    assert r.get_json()["filtros"] == {}


@pytest.mark.parametrize("ligado", ["quilometragem", "checklist"])
def test_opcionais_so_com_recurso_ligado(client, auth, c, repo, ligado):  # type: ignore[no-untyped-def]
    assert "opcionais" not in chaves(get(client, auth, c.adm))
    r = get(client, auth, c.adm, "/api/dashboards/opcionais")
    assert r.status_code == 404 and r.get_json()["erro"]["codigo"] == "recurso_desligado"
    assert not [n for n, _ in repo.chamadas_dash if n == "opcionais"]  # nem chega ao banco
    repo.config = replace(repo.config, **{ligado: True})
    assert "opcionais" in chaves(get(client, auth, c.aut))
    assert get(client, auth, c.aut, "/api/dashboards/opcionais").status_code == 200


def test_painel_inexistente_404(client, auth, c):  # type: ignore[no-untyped-def]
    r = get(client, auth, c.adm, "/api/dashboards/inexistente")
    assert r.status_code == 404 and r.get_json()["erro"]["codigo"] == "nao_encontrado"


def test_lista_informa_fuso_e_limites(client, auth, c):  # type: ignore[no-untyped-def]
    j = get(client, auth, c.aut).get_json()
    assert j["fuso"] == "America/Sao_Paulo"
    assert (j["periodo_padrao_dias"], j["periodo_maximo_dias"]) == (30, 366)


# ---------------------------------------------------------------- filtros
def test_padrao_ultimos_30_dias_no_fuso_local() -> None:
    f = validar_filtros({}, date(2026, 11, 8))
    assert (f.inicio, f.fim) == (date(2026, 10, 10), date(2026, 11, 8))
    assert (f.unidade, f.veiculo, f.granularidade) == (None, None, "dia")


def test_fim_inclusivo_vira_exclusivo_no_dia_seguinte_local() -> None:
    f = validar_filtros({"inicio": "2026-11-01", "fim": "2026-11-03"}, date(2026, 11, 8))
    assert f.inicio_utc == "2026-11-01T03:00:00+00:00"  # 00:00 em -03:00
    assert f.fim_exclusivo_utc == "2026-11-04T03:00:00+00:00"


def test_so_inicio_ou_so_fim() -> None:
    hoje = date(2026, 11, 8)
    assert validar_filtros({"inicio": "2026-11-01"}, hoje).fim == hoje
    f = validar_filtros({"fim": "2026-11-30"}, hoje)
    assert (f.inicio, f.fim) == (date(2026, 11, 1), date(2026, 11, 30))


def test_hoje_usa_fuso_de_sao_paulo_e_nao_utc(repo, c) -> None:  # type: ignore[no-untyped-def]
    """02:00 UTC de 9/11 ainda é 8/11 em São Paulo (e 00:30 UTC de 1/1 é 31/12)."""
    svc = ServicoDashboards(
        repo, ServicoRequisicoes(repo), relogio=lambda: datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    )
    j = svc.obter(c.aut, "tempos", {})
    assert j["filtros"]["fim"] == "2026-11-08" and j["filtros"]["inicio"] == "2026-10-10"
    assert repo.chamadas_dash[-1][1]["fim"] == "2026-11-09T03:00:00+00:00"


@pytest.mark.parametrize(
    "query",
    [
        {"inicio": "2026-13-01"},
        {"inicio": "20261101"},
        {"inicio": "01/11/2026"},
        {"fim": "ontem"},
        {"inicio": "2026-11-05", "fim": "2026-11-04"},
        {"inicio": "2025-01-01", "fim": "2026-11-01"},  # > 366 dias
        {"inicio": "1999-01-01"},
        {"inicio": "2018-12-31"},  # fuso fixo só vale a partir de 2019
        {"unidade": "XX"},
        {"unidade": "pu"},
        {"veiculo": "nao-e-uuid"},
        {"granularidade": "ano"},
    ],
)
def test_filtros_invalidos_400(client, auth, c, query):  # type: ignore[no-untyped-def]
    r = client.get("/api/dashboards/volume", headers=auth(c.aut.id), query_string=query)
    assert r.status_code == 400
    assert r.get_json()["erro"]["codigo"] == "requisicao_invalida"


def test_janela_de_366_dias_e_aceita(client, auth, c):  # type: ignore[no-untyped-def]
    q = {"inicio": "2025-11-01", "fim": "2026-10-31"}  # 365 dias
    assert (
        client.get("/api/dashboards/tempos", headers=auth(c.aut.id), query_string=q).status_code
        == 200
    )
    q = {"inicio": "2026-01-01", "fim": "2027-01-01"}  # 366 dias
    assert (
        client.get("/api/dashboards/tempos", headers=auth(c.aut.id), query_string=q).status_code
        == 200
    )


def test_campos_vazios_sao_ignorados(client, auth, c):  # type: ignore[no-untyped-def]
    q = {"inicio": "", "fim": "", "unidade": "", "veiculo": "", "granularidade": ""}
    r = client.get("/api/dashboards/volume", headers=auth(c.aut.id), query_string=q)
    assert r.status_code == 200 and r.get_json()["filtros"]["granularidade"] == "dia"


@pytest.mark.parametrize("painel", PAINEIS_JANELA[:-1])
def test_resposta_traz_filtros_efetivos_e_envia_janela_ao_banco(client, auth, c, repo, painel):  # type: ignore[no-untyped-def]
    q = {"inicio": "2026-11-01", "fim": "2026-11-03", "unidade": "PF", "veiculo": c.veiculo}
    r = client.get(f"/api/dashboards/{painel}", headers=auth(c.aut.id), query_string=q)
    j = r.get_json()
    assert r.status_code == 200 and j["painel"] == painel and "dados" in j
    esperado = {
        "inicio": "2026-11-01",
        "fim": "2026-11-03",
        "fuso": "America/Sao_Paulo",
        "unidade": "PF",
        "veiculo": c.veiculo,
    }
    if painel == "volume":
        esperado["granularidade"] = "dia"
    assert j["filtros"] == esperado
    nome, params = repo.chamadas_dash[-1]
    assert nome == painel
    assert params["inicio"] == "2026-11-01T03:00:00+00:00"
    assert params["fim"] == "2026-11-04T03:00:00+00:00"
    assert params["unidade"] == "PF" and params["veiculo"] == c.veiculo


def test_padrao_30_dias_na_resposta(client, auth, c):  # type: ignore[no-untyped-def]
    j = client.get("/api/dashboards/unidades", headers=auth(c.aut.id)).get_json()
    ini, fim = date.fromisoformat(j["filtros"]["inicio"]), date.fromisoformat(j["filtros"]["fim"])
    assert (fim - ini).days == 29


def test_granularidade_repassada_ao_volume(client, auth, c, repo):  # type: ignore[no-untyped-def]
    r = client.get(
        "/api/dashboards/volume", headers=auth(c.aut.id), query_string={"granularidade": "mes"}
    )
    assert r.get_json()["filtros"]["granularidade"] == "mes"
    assert repo.chamadas_dash[-1][1]["granularidade"] == "mes"


# ---------------------------------------------------------------- operação: itens
def test_operacao_devolve_itens_com_linha_do_tempo_e_acoes(client, auth, c, repo):  # type: ignore[no-untyped-def]
    v2, v3 = repo.novo_veiculo(), repo.novo_veiculo()
    a = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    b = criar(client, auth, c.outro, v2).get_json()["id"]
    d = criar(client, auth, c.outro, v3).get_json()["id"]
    acao(client, auth, c.aut, d, "autorizar")
    acao(client, auth, c.aut, d, "saida")
    acao(client, auth, c.aut, b, "autorizar")
    r = get(client, auth, c.aut, "/api/dashboards/operacao")
    j = r.get_json()
    assert r.status_code == 200
    ids = [x["id"] for x in j["dados"]["abertas"]]
    assert [i["id"] for i in j["itens"]] == ids == [a, b, d]  # solicitada, autorizada, em_uso
    for item in j["itens"]:
        assert "acoes_permitidas" in item and item["linha_do_tempo"]["etapas"]
    assert j["itens"][0]["acoes_permitidas"] == ["autorizar", "negar", "cancelar"]
    assert j["itens"][2]["acoes_permitidas"] == ["registrar_chegada"]
    assert j["filtros"] == {"unidade": None, "veiculo": None}
    assert len(repo.chamadas_por_ids) == 1  # uma consulta em lote, sem N+1


def test_operacao_filtra_por_unidade_e_ignora_encerradas(client, auth, c, repo):  # type: ignore[no-untyped-def]
    a = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    acao(client, auth, c.aut, a, "negar")
    r = client.get(
        "/api/dashboards/operacao", headers=auth(c.adm.id), query_string={"unidade": "PU"}
    )
    assert r.get_json()["itens"] == []
    assert repo.chamadas_dash[-1] == ("operacao", {"unidade": "PU", "veiculo": None})


def test_operacao_sem_abertas(client, auth, c, repo):  # type: ignore[no-untyped-def]
    j = get(client, auth, c.aut, "/api/dashboards/operacao").get_json()
    assert j["itens"] == [] and j["dados"]["abertas"] == []
    assert repo.chamadas_por_ids == [[]] or repo.chamadas_por_ids == []


def test_operacao_staff_ve_nomes_na_linha_do_tempo(client, auth, c):  # type: ignore[no-untyped-def]
    criar(client, auth, c.sol, c.veiculo)
    item = get(client, auth, c.aut, "/api/dashboards/operacao").get_json()["itens"][0]
    assert item["linha_do_tempo"]["etapas"][0]["por"] == {"nome": c.sol.nome}


# ---------------------------------------------------------------- AG023 e logs
def test_ag023_vira_400_com_mensagem_clara() -> None:
    e = traduzir_erro_banco(
        APIError({"message": "Período máximo do painel: 366 dias.", "code": "AG023", "details": ""})
    )
    assert isinstance(e, RequisicaoInvalida) and e.status == 400
    assert "366 dias" in e.mensagem and e.codigo == "painel_parametros_invalidos"
    assert isinstance(erro_db("AG023"), RequisicaoInvalida)


def test_ag023_do_banco_chega_como_400(client, auth, c, repo, monkeypatch):  # type: ignore[no-untyped-def]
    def falha(**_: Any) -> dict[str, Any]:
        raise erro_db("AG023", "Granularidade inválida.")

    monkeypatch.setattr(repo, "dash_volume_desfecho", falha)
    r = get(client, auth, c.aut, "/api/dashboards/volume")
    assert r.status_code == 400
    assert r.get_json()["erro"]["codigo"] == "painel_parametros_invalidos"


def test_rate_limit_de_leitura(client, auth, c, app):  # type: ignore[no-untyped-def]
    from app.extensions import deps

    with app.app_context():
        assert deps().config.limite_dashboards == "60 per minute"
    codigos = {get(client, auth, c.aut).status_code for _ in range(61)}
    assert codigos == {200, 429}


def test_ano_minimo_2019_e_aceito(client, auth, c):  # type: ignore[no-untyped-def]
    q = {"inicio": "2019-01-01", "fim": "2019-01-31"}
    assert (
        client.get("/api/dashboards/tempos", headers=auth(c.aut.id), query_string=q).status_code
        == 200
    )


def test_operacao_corta_em_200_preservando_a_ordem(client, auth, c, repo, monkeypatch):  # type: ignore[no-untyped-def]
    import app.services.dashboards as mod

    monkeypatch.setattr(mod, "MAX_ITENS_OPERACAO", 2)
    ids = []
    for _ in range(3):
        ids.append(criar(client, auth, c.sol, repo.novo_veiculo()).get_json()["id"])
    j = get(client, auth, c.aut, "/api/dashboards/operacao").get_json()
    completo = [a["id"] for a in repo.dash_operacao_agora(unidade=None, veiculo=None)["abertas"]]
    assert [a["id"] for a in j["dados"]["abertas"]] == completo[:2]
    assert [i["id"] for i in j["itens"]] == completo[:2]
    assert j["itens_truncados"] is True and set(completo) == set(ids)


def test_operacao_limite_padrao_e_200() -> None:
    from app.services.dashboards import MAX_ITENS_OPERACAO

    assert MAX_ITENS_OPERACAO == 200
