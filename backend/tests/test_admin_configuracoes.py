"""Configurações (recursos opcionais): flags, dependências, auditoria e reflexo em /api/eu."""

from __future__ import annotations

import pytest

from app.modelos import Configuracoes


def _cod(r) -> str:  # type: ignore[no-untyped-def]
    return r.get_json()["erro"]["codigo"]


def put(client, headers, corpo):  # type: ignore[no-untyped-def]
    return client.put("/api/admin/configuracoes", headers=headers, json=corpo)


def test_padrao_tudo_desligado_com_acoes_por_item(client, auth, adm):  # type: ignore[no-untyped-def]
    j = client.get("/api/admin/configuracoes", headers=auth(adm.id)).get_json()
    assert j["recursos"] == {
        "quilometragem": False,
        "abastecimento_manutencao": False,
        "checklist": False,
        "mfa": False,
        "relatorio_email": False,
        "relatorio_whatsapp": False,
        "notificacao_email_criticos": False,
        "motorista": False,
        "motorista_registra": False,
        "motorista_localizacao": False,
        "vigilante_km": False,
    }
    assert j["acoes_permitidas"] == ["editar"]
    itens = {i["chave"]: i for i in j["itens"]}
    assert itens["quilometragem"]["acoes_permitidas"] == ["ligar"]
    # dependência explicada pelo servidor: a UI só mostra o que vier daqui
    ab = itens["abastecimento_manutencao"]
    assert ab["acoes_permitidas"] == [] and ab["exige"] == ["quilometragem"]
    assert "Ligue antes" in ab["motivo_indisponibilidade"]
    # MFA só pode ser ligado por quem já está com segundo fator (evita trancar o admin)
    assert itens["mfa"]["acoes_permitidas"] == [] and itens["mfa"]["motivo_indisponibilidade"]


def test_ligar_audita_e_reflete_em_eu(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    h = auth(adm.id)
    r = put(client, h, {"quilometragem": True, "checklist": True})
    assert r.status_code == 200
    assert r.get_json()["recursos"]["quilometragem"] is True
    eu = client.get("/api/eu", headers=h).get_json()
    assert eu["recursos"] == {
        "quilometragem": True,
        "abastecimento_manutencao": False,
        "checklist": True,
        "mfa": False,
        "relatorio_email": False,
        "relatorio_whatsapp": False,
        "notificacao_email_criticos": False,
        "motorista": False,
        "motorista_registra": False,
        "motorista_localizacao": False,
        "vigilante_km": False,
    }
    ev = repo.eventos_de("configuracao_alterada")
    assert sorted((e["detalhes"]["recurso"], e["de"], e["para"], e["ator_id"]) for e in ev) == [
        ("checklist", "false", "true", adm.id),
        ("quilometragem", "false", "true", adm.id),
    ]
    # outro perfil também enxerga o estado em /api/eu, mas não administra
    sol = repo.novo_perfil("solicitante")
    assert client.get("/api/eu", headers=auth(sol.id)).get_json()["recursos"]["checklist"] is True


def test_dependencia_abastecimento_exige_quilometragem(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    h = auth(adm.id)
    r = put(client, h, {"abastecimento_manutencao": True})
    assert r.status_code == 409 and _cod(r) == "dependencia_recurso"
    assert "Quilometragem" in r.get_json()["erro"]["mensagem"]
    assert repo.config == Configuracoes() and repo.eventos == []
    # juntos, na mesma chamada, vale
    assert (
        put(client, h, {"quilometragem": True, "abastecimento_manutencao": True}).status_code == 200
    )
    # desligar a quilometragem com abastecimento ligado também é recusado
    r = put(client, h, {"quilometragem": False})
    assert r.status_code == 409 and _cod(r) == "dependencia_recurso"
    assert repo.config.quilometragem is True
    j = client.get("/api/admin/configuracoes", headers=h).get_json()
    km = next(i for i in j["itens"] if i["chave"] == "quilometragem")
    assert km["acoes_permitidas"] == [] and "Desligue antes" in km["motivo_indisponibilidade"]
    # desligando na ordem certa passa
    assert (
        put(client, h, {"abastecimento_manutencao": False, "quilometragem": False}).status_code
        == 200
    )


def test_dependencia_no_banco_tambem_vira_mensagem_clara(client, auth, adm, repo, monkeypatch):  # type: ignore[no-untyped-def]
    """Corrida: o CHECK/AG010 do banco recusa mesmo com a pré-validação passando."""
    from tests.fakes_admin import erro_db

    def falha(*a, **k):  # type: ignore[no-untyped-def]
        raise erro_db("AG010")

    monkeypatch.setattr(repo, "atualizar_configuracoes", falha)
    r = put(client, auth(adm.id), {"checklist": True})
    assert r.status_code == 409 and _cod(r) == "dependencia_recurso"


def test_desligar_nao_apaga_dados(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    h = auth(adm.id)
    sol = repo.novo_perfil("solicitante")
    v = repo.novo_veiculo()
    client.post(
        "/api/requisicoes",
        headers=auth(sol.id),
        json={
            "nome_solicitante": "F",
            "unidade": "PU",
            "hora_saida": "2026-11-10T08:30:00-03:00",
            "trajeto": "A -> B",
            "veiculo_id": v,
        },
    )
    antes = {k: dict(x) for k, x in repo.requisicoes.items()}
    put(client, h, {"quilometragem": True})
    put(client, h, {"quilometragem": False})
    assert repo.requisicoes == antes and v in repo.veiculos and sol.id in repo.perfis
    assert [e["para"] for e in repo.eventos_de("configuracao_alterada")] == ["true", "false"]


def test_sem_mudanca_nao_audita(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    assert put(client, auth(adm.id), {"quilometragem": False, "mfa": False}).status_code == 200
    assert repo.eventos == []


@pytest.mark.parametrize(
    "corpo",
    [
        {},
        [],
        {"quilometragem": "sim"},
        {"quilometragem": 1},
        {"quilometragem": None},
        {"recurso_novo": True},
        {"id": False},
        {"alterado_por": "x"},
    ],
)
def test_validacao(client, auth, adm, repo, corpo):  # type: ignore[no-untyped-def]
    r = put(client, auth(adm.id), corpo)
    assert r.status_code == 400 and repo.eventos == [] and repo.config == Configuracoes()


def test_mfa_exige_segundo_fator_na_sessao_para_ligar(client, auth, auth_aal2, adm, repo):  # type: ignore[no-untyped-def]
    r = put(client, auth(adm.id), {"mfa": True})
    assert r.status_code == 403 and _cod(r) == "mfa_requerido"
    assert repo.config.mfa is False
    r = put(client, auth_aal2(adm.id), {"mfa": True})
    assert r.status_code == 200 and r.get_json()["recursos"]["mfa"] is True
    # ligado: admin sem segundo fator passa a ser barrado (regra do backend), com aal2 segue
    assert client.get("/api/admin/configuracoes", headers=auth(adm.id)).status_code == 403
    j = client.get("/api/admin/configuracoes", headers=auth_aal2(adm.id)).get_json()
    mfa = next(i for i in j["itens"] if i["chave"] == "mfa")
    assert mfa["acoes_permitidas"] == ["desligar"]
    assert put(client, auth_aal2(adm.id), {"mfa": False}).status_code == 200
