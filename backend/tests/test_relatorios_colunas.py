# ruff: noqa: F811
"""Relatórios com colunas selecionáveis: lista branca, 400, permissões, IDOR, LGPD e ordem."""

from __future__ import annotations

import csv
import io
from typing import Any

import pytest

from app.modelos import Configuracoes
from app.services.relatorios import ServicoRelatorios
from tests.test_relatorios import BASE, app, get, mk, nova_req  # noqa: F401  (fixtures)
from tests.test_relatorios_email import (  # noqa: F401  (fixtures)
    SmtpFalso,
    _dominios,
    enviar,
    ligado,
    smtp,
)

PREVIA = f"{BASE}/movimentacao/previa"
ARQUIVO = f"{BASE}/movimentacao/arquivo"
OBS = "Levar documento sigiloso de Fulano"


def chaves(j: dict[str, Any]) -> list[str]:
    return [x["chave"] for x in j["colunas"]]


def csv_de(resp: Any) -> list[list[str]]:
    texto = resp.data.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(texto), delimiter=";"))


# ---------------------------------------------------------------------------- catálogo
def test_catalogo_expoe_colunas_disponiveis_por_perfil(client, auth, c, repo):  # type: ignore[no-untyped-def]
    sol = get(client, auth, c.sol, BASE).get_json()
    mov = next(r for r in sol["relatorios"] if r["tipo"] == "movimentacao")
    disp = {x["chave"]: x for x in mov["colunas_disponiveis"]}
    assert set(disp["codigo"]) == {"chave", "rotulo", "exige_flag"}
    assert disp["codigo"]["exige_flag"] is None
    # solicitante não tem as flags: as colunas dependentes nem são oferecidas
    assert not {"solicitante", "trajeto", "observacao"} & set(disp)
    assert "km_saida" not in disp  # recurso desligado

    aut = get(client, auth, c.aut, BASE).get_json()
    mov = next(r for r in aut["relatorios"] if r["tipo"] == "movimentacao")
    disp = {x["chave"]: x for x in mov["colunas_disponiveis"]}
    assert disp["solicitante"]["exige_flag"] == "incluir_nomes"
    assert disp["trajeto"]["exige_flag"] == "incluir_trajeto"
    assert disp["observacao"]["exige_flag"] == "incluir_observacao"
    assert disp["observacao"]["rotulo"] == "Observação"
    assert {f["nome"] for f in mov["filtros"]} >= {"incluir_observacao"}

    repo.config = Configuracoes(quilometragem=True)
    mov = next(
        r
        for r in get(client, auth, c.aut, BASE).get_json()["relatorios"]
        if r["tipo"] == "movimentacao"
    )
    assert {"km_saida", "km_chegada"} <= {x["chave"] for x in mov["colunas_disponiveis"]}


def test_catalogo_so_tres_relatorios_aceitam_colunas(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(quilometragem=True, checklist=True)
    itens = get(client, auth, c.adm, BASE).get_json()["relatorios"]
    com = {r["tipo"] for r in itens if r["colunas_disponiveis"]}
    assert com == {"movimentacao", "pendencias", "opcionais_km"}
    assert all(r["colunas_disponiveis"] == [] for r in itens if r["tipo"] not in com)


def test_solicitante_nao_ve_flag_de_observacao(client, auth, c):  # type: ignore[no-untyped-def]
    mov = next(
        r
        for r in get(client, auth, c.sol, BASE).get_json()["relatorios"]
        if r["tipo"] == "movimentacao"
    )
    assert "incluir_observacao" not in {f["nome"] for f in mov["filtros"]}


# ------------------------------------------------------------------- seleção e ordem
def test_selecao_preserva_a_ordem_pedida(client, auth, c, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol)
    j = get(client, auth, c.aut, PREVIA, colunas="status,codigo,unidade").get_json()
    assert chaves(j) == ["status", "codigo", "unidade"]
    assert j["filtros"]["colunas"] == ["status", "codigo", "unidade"]
    assert set(j["linhas"][0]) == {"status", "codigo", "unidade"}
    j = get(client, auth, c.aut, PREVIA, colunas="unidade,status,codigo").get_json()
    assert chaves(j) == ["unidade", "status", "codigo"]


def test_parametro_repetido_equivale_a_csv(client, auth, c, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol)
    r = client.get(
        f"{PREVIA}?colunas=veiculo&colunas=codigo,status", headers=auth(c.aut.id)
    ).get_json()
    assert chaves(r) == ["veiculo", "codigo", "status"]


def test_sem_colunas_mantem_o_padrao(client, auth, c, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol)
    j = get(client, auth, c.aut, PREVIA).get_json()
    assert "colunas" not in j["filtros"]
    assert chaves(j)[:3] == ["codigo", "criada_em", "unidade"] and "observacao" not in chaves(j)
    assert get(client, auth, c.aut, PREVIA, colunas="").get_json()["filtros"].get("colunas") is None


def test_arquivo_csv_respeita_selecao_e_ordem(client, auth, c, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol)
    linhas = csv_de(get(client, auth, c.aut, ARQUIVO, colunas="status,codigo"))
    assert linhas[0] == ["Status", "Código"] and len(linhas[0]) == 2
    assert len(linhas[1]) == 2


def test_pendencias_e_opcionais_km_aceitam_colunas(client, auth, c, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol)
    j = get(client, auth, c.aut, f"{BASE}/pendencias/previa", colunas="status,codigo").get_json()
    assert chaves(j) == ["status", "codigo"]
    repo.config = Configuracoes(quilometragem=True)
    j = get(client, auth, c.aut, f"{BASE}/opcionais_km/previa", colunas="km_rodado,codigo")
    assert chaves(j.get_json()) == ["km_rodado", "codigo"]


@pytest.mark.parametrize("tipo", ["resumo", "tempos", "auditoria"])
def test_tipos_de_colunas_fixas_ignoram_colunas(client, auth, c, tipo):  # type: ignore[no-untyped-def]
    limpo = get(client, auth, c.adm, f"{BASE}/{tipo}/previa")
    com = get(client, auth, c.adm, f"{BASE}/{tipo}/previa", colunas="inexistente,x")
    assert com.status_code == 200
    assert chaves(com.get_json()) == chaves(limpo.get_json())
    assert "colunas" not in com.get_json()["filtros"]


# --------------------------------------------------------------------------- 400
@pytest.mark.parametrize(
    "colunas",
    [
        "inexistente",
        "veiculo_id",  # vem do banco, mas não é da lista branca
        "codigo,codigo",  # repetida
        "codigo,,status",  # item vazio
        "codigo;DROP TABLE x",
        "../etc/passwd",
        "km_rodado",  # existe no catálogo de outro relatório, não neste
        "x" * 1001,
    ],
)
def test_coluna_invalida_400(client, auth, c, colunas):  # type: ignore[no-untyped-def]
    for url in (PREVIA, ARQUIVO):
        r = get(client, auth, c.adm, url, colunas=colunas)
        assert r.status_code == 400
        assert r.get_json()["erro"]["codigo"] == "requisicao_invalida"
        assert colunas[:20] not in r.get_json()["erro"]["mensagem"] or colunas == "codigo,codigo"


@pytest.mark.parametrize(
    ("coluna", "flag"),
    [
        ("solicitante", "incluir_nomes"),
        ("trajeto", "incluir_trajeto"),
        ("observacao", "incluir_observacao"),
    ],
)
def test_coluna_que_exige_flag_desligada_400(client, auth, c, repo, coluna, flag):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol)
    r = get(client, auth, c.adm, ARQUIVO, colunas=f"codigo,{coluna}")
    assert r.status_code == 400 and flag in r.get_json()["erro"]["mensagem"]
    assert repo.chamadas_de("movimentacao") == []  # falhou antes de consultar o banco
    assert get(client, auth, c.adm, ARQUIVO, colunas=coluna, **{flag: "true"}).status_code == 200


def test_coluna_de_recurso_desligado_400(client, auth, c):  # type: ignore[no-untyped-def]
    r = get(client, auth, c.adm, PREVIA, colunas="km_saida")
    assert r.status_code == 400 and "quilometragem" in r.get_json()["erro"]["mensagem"]


def test_nome_da_flag_continua_sendo_validado(client, auth, c):  # type: ignore[no-untyped-def]
    r = get(client, auth, c.adm, PREVIA, incluir_observacao="talvez")
    assert r.status_code == 400


# --------------------------------------------------------------------- permissões / IDOR
def test_solicitante_nao_liga_flags_nem_pede_coluna_dependente(client, auth, c, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol)
    for coluna in ("solicitante", "trajeto", "observacao"):
        r = get(client, auth, c.sol, PREVIA, colunas=coluna, incluir_nomes="true",
                incluir_trajeto="true", incluir_observacao="true")  # fmt: skip
        assert r.status_code == 400
    ok = get(client, auth, c.sol, PREVIA, colunas="codigo", incluir_observacao="true")
    assert ok.status_code == 200
    assert repo.chamadas_de("movimentacao")[-1].get("incluir_observacao") is None
    assert ok.get_json()["colunas_omitidas"] == []


def test_selecao_nao_altera_o_escopo_do_solicitante(client, auth, c, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol)
    nova_req(repo, c.outro)
    j = get(client, auth, c.sol, PREVIA, colunas="codigo", usuario=c.outro.id).get_json()
    assert j["total_linhas"] == 1
    assert repo.chamadas_de("movimentacao")[-1]["usuario"] == c.sol.id


def test_permissao_do_relatorio_vale_com_colunas(client, auth, c):  # type: ignore[no-untyped-def]
    assert get(client, auth, c.sol, f"{BASE}/resumo/previa", colunas="codigo").status_code == 403
    assert get(client, auth, c.aut, f"{BASE}/auditoria/previa", colunas="x").status_code == 403


# ---------------------------------------------------------------------------- LGPD
def test_observacao_fora_da_previa_e_nunca_pedida_ao_banco(client, auth, c, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol, observacao=OBS)
    j = get(client, auth, c.aut, PREVIA, incluir_observacao="true").get_json()
    assert "observacao" not in chaves(j) and OBS not in str(j)
    assert j["colunas_omitidas"] == ["observacao"]
    assert "incluir_observacao" not in repo.chamadas_de("movimentacao")[-1]
    j = get(client, auth, c.aut, PREVIA, incluir_observacao="true", colunas="codigo,observacao")
    assert chaves(j.get_json()) == ["codigo"] and OBS not in j.get_data(as_text=True)
    só_obs = get(client, auth, c.aut, PREVIA, incluir_observacao="true", colunas="observacao")
    assert só_obs.status_code == 400 and OBS not in só_obs.get_data(as_text=True)


def test_observacao_so_vai_no_arquivo_quando_a_flag_esta_ligada(client, auth, c, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol, observacao=OBS)
    padrao = get(client, auth, c.aut, ARQUIVO)
    assert OBS.encode() not in padrao.data and "Observação" not in csv_de(padrao)[0]
    assert "incluir_observacao" not in repo.chamadas_de("movimentacao")[-1]

    com_flag = get(client, auth, c.aut, ARQUIVO, incluir_observacao="true")
    assert csv_de(com_flag)[0][-1] == "Observação" and OBS.encode() in com_flag.data
    assert repo.chamadas_de("movimentacao")[-1]["incluir_observacao"] is True

    so_colunas = get(
        client, auth, c.aut, ARQUIVO, incluir_observacao="true", colunas="observacao,codigo"
    )
    assert csv_de(so_colunas)[0] == ["Observação", "Código"]
    # a flag ligada mas a coluna não escolhida: o texto não sai no arquivo
    sem_col = get(client, auth, c.aut, ARQUIVO, incluir_observacao="true", colunas="codigo")
    assert OBS.encode() not in sem_col.data


def test_auditoria_do_arquivo_nao_guarda_o_texto_da_observacao(client, auth, c, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol, observacao=OBS)
    get(client, auth, c.aut, ARQUIVO, incluir_observacao="true", colunas="codigo,observacao")
    detalhes = str(repo.auditoria_relatorios if hasattr(repo, "auditoria_relatorios") else "")
    assert OBS not in detalhes


def test_resumo_ia_ignora_colunas_e_observacao(client, auth, c, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol, observacao=OBS)
    d = client.application.extensions["sisveiculos"]
    svc = ServicoRelatorios(d.repo, d.config)
    conjunto, ef = svc.conjunto_para_resumo(
        c.aut, "movimentacao", {"colunas": "lixo", "incluir_observacao": "true"}
    )
    assert "colunas" not in ef and ef.get("incluir_observacao") is False
    p = repo.chamadas_de("movimentacao")[-1]
    assert "incluir_observacao" not in p and p["incluir_nomes"] is False
    assert OBS not in str(conjunto.linhas)


# ----------------------------------------------------------------------------- e-mail
def test_email_respeita_selecao_e_ordem(client, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.adm)
    r = enviar(
        client,
        auth,
        c.aut,
        "movimentacao",
        destinatario_id=c.adm.id,
        filtros={"colunas": ["status", "codigo"]},
    )
    assert r.status_code == 202
    (inst,) = SmtpFalso.instancias
    anexo = next(inst.mensagens[0].iter_attachments()).get_payload(decode=True)
    cabecalho = anexo.decode("utf-8-sig").splitlines()[0]
    assert cabecalho == "Status;Código"


def test_email_coluna_invalida_400_e_sem_envio(client, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    for filtros in ({"colunas": ["nao_existe"]}, {"colunas": "observacao"}, {"colunas": [1]}):
        r = enviar(client, auth, c.aut, "movimentacao", destinatario_id=c.adm.id, filtros=filtros)
        assert r.status_code == 400
    assert SmtpFalso.instancias == [] and repo.envios == {}


def test_email_com_observacao_exige_dominio_institucional(  # type: ignore[no-untyped-def]
    client, auth, c, ligado, repo, monkeypatch
):
    nova_req(repo, c.adm, observacao=OBS)
    _dominios(client, monkeypatch, "institucional.gov.br")
    repo.contas_auth[c.adm.id]["email"] = "alguem@gmail.com"
    corpo = {"destinatario_id": c.adm.id, "filtros": {"incluir_observacao": True}}
    r = enviar(client, auth, c.aut, "movimentacao", **corpo)
    assert r.status_code == 422 and r.get_json()["erro"]["codigo"] == "destinatario_externo_pii"
    assert SmtpFalso.instancias == []
    repo.contas_auth[c.adm.id]["email"] = "alguem@institucional.gov.br"
    assert enviar(client, auth, c.aut, "movimentacao", **corpo).status_code == 202


def test_email_da_trilha_nao_leva_observacao_sem_flag(client, auth, c, ligado, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.adm, observacao=OBS)
    r = enviar(client, auth, c.aut, "movimentacao", destinatario_id=c.adm.id)
    assert r.status_code == 202
    (inst,) = SmtpFalso.instancias
    assert OBS.encode() not in next(inst.mensagens[0].iter_attachments()).get_payload(decode=True)


def test_repositorio_so_envia_p_incluir_observacao_quando_pedido():  # type: ignore[no-untyped-def]
    from unittest.mock import MagicMock

    from app.repositorios.supabase_repo import SupabaseRepositorio

    repo = SupabaseRepositorio.__new__(SupabaseRepositorio)
    repo._rpc_relatorio = MagicMock(return_value={})  # type: ignore[method-assign]
    base: dict[str, Any] = {
        "inicio": "a", "fim": "b", "campo_data": "criacao", "unidade": None,
        "veiculo": None, "usuario": None, "incluir_nomes": False,
        "incluir_trajeto": False, "limite": 1,
    }  # fmt: skip
    repo.rel_movimentacao(**base)
    assert "p_incluir_observacao" not in repo._rpc_relatorio.call_args.args[1]
    repo.rel_movimentacao(**base, incluir_observacao=True)
    assert repo._rpc_relatorio.call_args.args[1]["p_incluir_observacao"] is True


def test_flag_sem_a_coluna_nao_busca_nem_audita_nem_exige_dominio(  # type: ignore[no-untyped-def]
    client, auth, c, repo, ligado, monkeypatch
):
    nova_req(repo, c.adm, observacao=OBS)
    r = get(client, auth, c.aut, ARQUIVO, incluir_observacao="true", colunas="codigo")
    assert r.status_code == 200 and OBS.encode() not in r.data
    assert "incluir_observacao" not in repo.chamadas_de("movimentacao")[-1]
    assert "'incluir_observacao': True" not in str(repo.eventos)  # nada na auditoria
    # e-mail para domínio externo não é bloqueado, pois nada sensível sai
    _dominios(client, monkeypatch, "institucional.gov.br")
    repo.contas_auth[c.adm.id]["email"] = "alguem@gmail.com"
    corpo = {"incluir_observacao": True, "colunas": ["codigo"]}
    assert (
        enviar(
            client, auth, c.aut, "movimentacao", destinatario_id=c.adm.id, filtros=corpo
        ).status_code
        == 202
    )
    # com a coluna escolhida a flag continua valendo
    get(client, auth, c.aut, ARQUIVO, incluir_observacao="true", colunas="codigo,observacao")
    assert repo.chamadas_de("movimentacao")[-1]["incluir_observacao"] is True
    assert "'incluir_observacao': True" in str(repo.eventos)
