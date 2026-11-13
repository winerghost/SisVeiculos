"""Perfil Motorista (recurso opcional): permissões, anti-IDOR, recurso desligado, atribuição
inválida e registro de saída/chegada pelo motorista (com e sem `motorista_registra`)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.modelos import Configuracoes
from tests.test_requisicoes import acao, criar

LIGADO = Configuracoes(motorista=True)
LIGADO_REGISTRA = Configuracoes(motorista=True, motorista_registra=True)


def cod(r: Any) -> str:
    return r.get_json()["erro"]["codigo"]


@pytest.fixture
def m(repo, c):  # type: ignore[no-untyped-def]
    class M:
        a = repo.novo_perfil("motorista")
        b = repo.novo_perfil("motorista")

    return M


def autorizar(client, auth, quem, rid, **corpo):  # type: ignore[no-untyped-def]
    return client.post(f"/api/requisicoes/{rid}/autorizar", headers=auth(quem.id), json=corpo)


def atribuir(client, auth, quem, rid, motorista_id):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/requisicoes/{rid}/motorista",
        headers=auth(quem.id),
        json={"motorista_id": motorista_id},
    )


def viagem_autorizada(client, auth, repo, c, motorista=None, veiculo=None):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, c.sol, veiculo or c.veiculo).get_json()["id"]
    corpo = {"motorista_id": motorista.id} if motorista else {}
    r = autorizar(client, auth, c.aut, rid, **corpo)
    assert r.status_code == 200, r.get_json()
    return rid


def get(client, auth, quem, caminho):  # type: ignore[no-untyped-def]
    return client.get(caminho, headers=auth(quem.id))


# ------------------------------------------------------------------ configuração (admin)
def test_admin_liga_e_audita_motorista(client, auth, c, repo):  # type: ignore[no-untyped-def]
    r = client.put("/api/admin/configuracoes", headers=auth(c.adm.id), json={"motorista": True})
    assert r.status_code == 200 and r.get_json()["recursos"]["motorista"] is True
    assert repo.config.motorista is True
    assert any(
        e["acao"] == "configuracao_alterada" and e["detalhes"] == {"recurso": "motorista"}
        for e in repo.eventos
    )


def test_motorista_registra_exige_motorista(client, auth, c, repo):  # type: ignore[no-untyped-def]
    h = auth(c.adm.id)
    r = client.put("/api/admin/configuracoes", headers=h, json={"motorista_registra": True})
    assert r.status_code == 409 and cod(r) == "dependencia_recurso"
    r = client.put(
        "/api/admin/configuracoes",
        headers=h,
        json={"motorista": True, "motorista_registra": True},
    )
    assert r.status_code == 200
    # desligar o recurso-base com o dependente ligado também é recusado
    r = client.put("/api/admin/configuracoes", headers=h, json={"motorista": False})
    assert r.status_code == 409 and cod(r) == "dependencia_recurso"


def test_so_admin_altera_flags_do_motorista(client, auth, c, m):  # type: ignore[no-untyped-def]
    for quem in (c.sol, c.aut, m.a):
        r = client.put("/api/admin/configuracoes", headers=auth(quem.id), json={"motorista": True})
        assert r.status_code == 403


def test_admin_convida_conta_motorista_e_altera_role(client, auth, c, repo):  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/admin/usuarios",
        headers=auth(c.adm.id),
        json={
            "email": "dirigente@agu.gov.br",
            "nome": "Dirigente",
            "unidade": "UEA",
            "role": "motorista",
        },
    )
    assert r.status_code == 201, r.get_json()
    r = client.patch(
        f"/api/admin/usuarios/{c.sol.id}", headers=auth(c.adm.id), json={"role": "motorista"}
    )
    assert r.status_code == 200 and repo.perfis[c.sol.id].role == "motorista"
    # continua valendo: o admin não rebaixa a si mesmo
    r = client.patch(
        f"/api/admin/usuarios/{c.adm.id}", headers=auth(c.adm.id), json={"role": "motorista"}
    )
    assert r.status_code == 403 and cod(r) == "auto_alteracao_proibida"


def test_role_invalida_continua_recusada(client, auth, c):  # type: ignore[no-untyped-def]
    r = client.patch(
        f"/api/admin/usuarios/{c.sol.id}", headers=auth(c.adm.id), json={"role": "dono"}
    )
    assert r.status_code == 400


def test_motorista_nao_exige_mfa(client, auth, m, repo):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(mfa=True, motorista=True)
    assert get(client, auth, m.a, "/api/eu").status_code == 200


# ------------------------------------------------------------------ recurso desligado
def test_desligado_recusa_endpoints_do_motorista(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    rid = viagem_autorizada(client, auth, repo, c)
    for r in (
        get(client, auth, m.a, "/api/motorista/viagens"),
        get(client, auth, m.a, f"/api/motorista/viagens/{rid}"),
        get(client, auth, c.aut, "/api/motoristas"),
        atribuir(client, auth, c.aut, rid, m.a.id),
    ):
        assert r.status_code == 404 and cod(r) == "recurso_desligado"


def test_desligado_autorizar_ignora_motorista_id(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    rid = viagem_autorizada(client, auth, repo, c, motorista=m.a)
    assert repo.requisicoes[rid]["motorista_id"] is None
    j = get(client, auth, c.aut, f"/api/requisicoes/{rid}").get_json()
    assert j["motorista"] is None and "atribuir_motorista" not in j["acoes_permitidas"]
    # nem o lixo do corpo derruba a autorização com o recurso desligado
    rid2 = criar(client, auth, c.sol, repo.novo_veiculo()).get_json()["id"]
    assert autorizar(client, auth, c.aut, rid2, motorista_id="lixo").status_code == 200


def test_desligado_motorista_nao_registra_nada(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    rid = viagem_autorizada(client, auth, repo, c)
    repo.requisicoes[rid]["motorista_id"] = m.a.id  # dado antigo, de quando esteve ligado
    r = acao(client, auth, m.a, rid, "saida")
    assert r.status_code == 404 and cod(r) == "recurso_desligado"
    assert repo.requisicoes[rid]["status"] == "autorizada"


# ------------------------------------------------------------------ atribuição
def test_autorizar_com_motorista_e_payload(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    j = get(client, auth, c.aut, f"/api/requisicoes/{rid}").get_json()
    assert j["campos_acao"]["autorizar"]["campos_opcionais"] == ["motorista_id"]
    j = autorizar(client, auth, c.aut, rid, motorista_id=m.a.id).get_json()
    assert j["motorista"] == {"id": m.a.id, "nome": m.a.nome}
    assert j["motorista_id"] == m.a.id and "atribuir_motorista" in j["acoes_permitidas"]
    # o solicitante dono vê quem dirige, mas não ganha ação nenhuma
    dono = get(client, auth, c.sol, f"/api/requisicoes/{rid}").get_json()
    assert dono["motorista"]["nome"] == m.a.nome and dono["acoes_permitidas"] == []


def test_autorizar_sem_motorista_continua_valendo(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = viagem_autorizada(client, auth, repo, c)
    j = get(client, auth, c.aut, f"/api/requisicoes/{rid}").get_json()
    assert j["motorista"] is None and j["status"] == "autorizada"


def test_atribuicao_invalida_na_autorizacao_nao_autoriza(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    inativo = repo.novo_perfil("motorista", ativo=False)
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    for ruim in (c.outro.id, c.aut.id, inativo.id, str(uuid.uuid4()), "nao-e-uuid", 7):
        r = autorizar(client, auth, c.aut, rid, motorista_id=ruim)
        assert r.status_code == 400, ruim
        assert cod(r) in {"motorista_invalido", "requisicao_invalida"}
    assert repo.requisicoes[rid]["status"] == "solicitada"
    assert repo.requisicoes[rid]["motorista_id"] is None


def test_atribuir_trocar_e_remover(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = viagem_autorizada(client, auth, repo, c)
    for quem, alvo in ((c.aut, m.a), (c.adm, m.b)):
        r = atribuir(client, auth, quem, rid, alvo.id)
        assert r.status_code == 200 and r.get_json()["motorista"]["id"] == alvo.id
    assert acao(client, auth, c.aut, rid, "saida").status_code == 200  # em_uso: ainda troca
    r = atribuir(client, auth, c.aut, rid, m.a.id)
    assert r.status_code == 200 and r.get_json()["status"] == "em_uso"
    r = atribuir(client, auth, c.aut, rid, None)
    assert r.status_code == 200 and r.get_json()["motorista"] is None


def test_atribuir_fora_da_janela_e_dados_invalidos(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    solicitada = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    r = atribuir(client, auth, c.aut, solicitada, m.a.id)
    assert r.status_code == 409 and cod(r) == "transicao_invalida"
    rid = viagem_autorizada(client, auth, repo, c, veiculo=repo.novo_veiculo())
    inativo = repo.novo_perfil("motorista", ativo=False)
    for ruim in (c.sol.id, inativo.id, str(uuid.uuid4()), "x"):
        assert atribuir(client, auth, c.aut, rid, ruim).status_code == 400
    corpo_ruim = client.post(
        f"/api/requisicoes/{rid}/motorista", headers=auth(c.aut.id), json={"outro": 1}
    )
    assert corpo_ruim.status_code == 400
    assert repo.requisicoes[rid]["motorista_id"] is None
    # concluída: não troca mais
    assert acao(client, auth, c.aut, rid, "saida").status_code == 200
    assert acao(client, auth, c.aut, rid, "chegada").status_code == 200
    assert atribuir(client, auth, c.aut, rid, m.a.id).status_code == 409


def test_atribuir_so_staff(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO_REGISTRA
    rid = viagem_autorizada(client, auth, repo, c, motorista=m.a)
    for quem in (c.sol, m.a, m.b):
        assert atribuir(client, auth, quem, rid, m.b.id).status_code == 403
    assert repo.requisicoes[rid]["motorista_id"] == m.a.id


# ------------------------------------------------------------------ leitura e anti-IDOR
def test_motorista_so_ve_as_proprias_viagens(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    v2 = repo.novo_veiculo()
    ra = viagem_autorizada(client, auth, repo, c, motorista=m.a)
    rb = viagem_autorizada(client, auth, repo, c, motorista=m.b, veiculo=v2)
    j = get(client, auth, m.a, "/api/motorista/viagens").get_json()
    assert [i["id"] for i in j["itens"]] == [ra] and j["total"] == 1
    assert rb not in str(j)
    j = get(client, auth, m.b, "/api/motorista/viagens").get_json()
    assert [i["id"] for i in j["itens"]] == [rb]
    # detalhe: a viagem do outro responde 404, igual a inexistente (não revela existência)
    r = get(client, auth, m.a, f"/api/motorista/viagens/{rb}")
    inexistente = get(client, auth, m.a, f"/api/motorista/viagens/{uuid.uuid4()}")
    assert r.status_code == inexistente.status_code == 404
    assert r.get_json() == inexistente.get_json()
    ok = get(client, auth, m.a, f"/api/motorista/viagens/{ra}")
    assert ok.status_code == 200 and ok.get_json()["motorista"]["id"] == m.a.id


def test_motorista_nao_acessa_requisicao_alheia_pelas_rotas_gerais(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO_REGISTRA
    rb = viagem_autorizada(client, auth, repo, c, motorista=m.b)
    assert get(client, auth, m.a, f"/api/requisicoes/{rb}").status_code == 404
    assert get(client, auth, m.a, "/api/requisicoes").get_json()["itens"] == []
    assert acao(client, auth, m.a, rb, "saida").status_code == 404
    assert get(client, auth, m.a, f"/api/requisicoes/{rb}/checklist").status_code == 404
    assert repo.requisicoes[rb]["status"] == "autorizada"


def test_filtro_por_status_e_status_invalido(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    viagem_autorizada(client, auth, repo, c, motorista=m.a)
    base = "/api/motorista/viagens"
    assert len(get(client, auth, m.a, f"{base}?status=autorizada").get_json()["itens"]) == 1
    assert get(client, auth, m.a, f"{base}?status=em_uso").get_json() == {"itens": [], "total": 0}
    assert get(client, auth, m.a, f"{base}?status=xyz").status_code == 400


def test_payload_do_motorista_nao_expoe_a_equipe(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = viagem_autorizada(client, auth, repo, c, motorista=m.a)
    j = get(client, auth, m.a, f"/api/motorista/viagens/{rid}").get_json()
    assert "autorizado_por" not in j and c.aut.id not in str(j) and c.aut.nome not in str(j)
    assert j["acoes_permitidas"] == []


# ------------------------------------------------------------------ permissões por perfil
def test_outros_perfis_recebem_403_nas_rotas_do_motorista(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = viagem_autorizada(client, auth, repo, c, motorista=m.a)
    for quem in (c.sol, c.aut, c.adm):
        assert get(client, auth, quem, "/api/motorista/viagens").status_code == 403
        assert get(client, auth, quem, f"/api/motorista/viagens/{rid}").status_code == 403
    for quem in (c.sol, m.a):
        r = get(client, auth, quem, "/api/motoristas")
        assert r.status_code == 403
    assert client.get("/api/motorista/viagens").status_code == 401


def test_seletor_lista_so_ativos_com_id_e_nome(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    repo.novo_perfil("motorista", ativo=False)
    for quem in (c.aut, c.adm):
        j = get(client, auth, quem, "/api/motoristas").get_json()
        assert sorted(i["id"] for i in j["itens"]) == sorted([m.a.id, m.b.id])
        assert all(set(i) == {"id", "nome"} for i in j["itens"])


def test_motorista_nao_cria_autoriza_nem_cancela(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO_REGISTRA
    assert criar(client, auth, m.a, c.veiculo).status_code == 403
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    assert acao(client, auth, m.a, rid, "autorizar").status_code == 403
    assert acao(client, auth, m.a, rid, "negar").status_code == 403
    assert acao(client, auth, m.a, rid, "cancelar").status_code in {403, 404}
    assert repo.requisicoes[rid]["status"] == "solicitada"
    rid2 = viagem_autorizada(client, auth, repo, c, motorista=m.a, veiculo=repo.novo_veiculo())
    assert acao(client, auth, m.a, rid2, "cancelar").status_code == 403
    assert repo.requisicoes[rid2]["status"] == "autorizada"


def test_eu_informa_permissoes_do_motorista(client, auth, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    assert get(client, auth, m.a, "/api/eu").get_json()["permissoes"] == ["ver_viagens_atribuidas"]
    repo.config = LIGADO_REGISTRA
    j = get(client, auth, m.a, "/api/eu").get_json()
    assert j["role"] == "motorista" and "registrar_saida" in j["permissoes"]


def test_solicitante_e_dashboards_continuam_fechados_ao_motorista(client, auth, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO_REGISTRA
    r = get(client, auth, m.a, "/api/dashboards/operacao-agora")
    assert r.status_code in {403, 404}


# ------------------------------------------------------------------ registro pelo motorista
def test_sem_motorista_registra_so_visualiza(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = viagem_autorizada(client, auth, repo, c, motorista=m.a)
    r = acao(client, auth, m.a, rid, "saida")
    assert r.status_code == 403 and cod(r) == "sem_permissao"
    assert repo.requisicoes[rid]["status"] == "autorizada"
    assert get(client, auth, m.a, f"/api/motorista/viagens/{rid}").get_json()["campos_acao"] == {}


def test_com_motorista_registra_ciclo_completo_so_nas_proprias(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO_REGISTRA
    rid = viagem_autorizada(client, auth, repo, c, motorista=m.a)
    j = get(client, auth, m.a, f"/api/motorista/viagens/{rid}").get_json()
    assert j["acoes_permitidas"] == ["registrar_saida"]
    assert acao(client, auth, m.b, rid, "saida").status_code == 404  # outro motorista
    r = acao(client, auth, m.a, rid, "saida")
    assert r.status_code == 200 and r.get_json()["status"] == "em_uso"
    assert repo.requisicoes[rid]["saida_registrada_por"] == m.a.id
    assert r.get_json()["acoes_permitidas"] == ["registrar_chegada"]
    assert acao(client, auth, m.a, rid, "saida").status_code == 409  # máquina de estados
    r = acao(client, auth, m.a, rid, "chegada")
    assert r.status_code == 200 and r.get_json()["status"] == "concluida"
    assert repo.requisicoes[rid]["chegada_registrada_por"] == m.a.id
    assert r.get_json()["acoes_permitidas"] == ["arquivar"]  # concluída: só organizar a lista


def test_motorista_sem_atribuicao_nao_registra(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO_REGISTRA
    rid = viagem_autorizada(client, auth, repo, c)
    r = acao(client, auth, m.a, rid, "saida")
    assert r.status_code == 404
    assert repo.requisicoes[rid]["status"] == "autorizada"


def test_troca_de_motorista_tira_o_poder_do_anterior(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO_REGISTRA
    rid = viagem_autorizada(client, auth, repo, c, motorista=m.a)
    assert atribuir(client, auth, c.aut, rid, m.b.id).status_code == 200
    assert acao(client, auth, m.a, rid, "saida").status_code == 404
    assert acao(client, auth, m.b, rid, "saida").status_code == 200


def test_motorista_nunca_lanca_km(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(
        motorista=True, motorista_registra=True, quilometragem=True, autorizador_km=True
    )
    rid = viagem_autorizada(client, auth, repo, c, motorista=m.a)
    j = get(client, auth, m.a, f"/api/motorista/viagens/{rid}").get_json()
    assert "registrar_saida" not in j["campos_acao"]  # nenhum campo de km é pedido
    h = auth(m.a.id)
    # km enviado por engano é descartado (sem erro), e o banco também não o exige.
    r = client.post(f"/api/requisicoes/{rid}/saida", headers=h, json={"km_saida": 100})
    assert r.status_code == 200 and repo.requisicoes[rid]["km_saida"] is None
    r = client.post(f"/api/requisicoes/{rid}/chegada", headers=h, json={"km_chegada": 150})
    assert r.status_code == 200 and repo.requisicoes[rid]["km_chegada"] is None


def test_checklist_continua_obrigatorio_e_motorista_registra_o_proprio(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True, motorista_registra=True, checklist=True)
    rid = viagem_autorizada(client, auth, repo, c, motorista=m.a)
    r = acao(client, auth, m.a, rid, "saida")
    assert r.status_code == 409 and cod(r) == "checklist_obrigatorio"
    corpo = {"combustivel": "meio", "avarias": False}
    url = f"/api/requisicoes/{rid}/checklist/saida"
    assert client.post(url, headers=auth(m.b.id), json=corpo).status_code == 404  # outro
    r = client.post(url, headers=auth(m.a.id), json=corpo)
    assert r.status_code == 201, r.get_json()
    assert acao(client, auth, m.a, rid, "saida").status_code == 200
    assert get(client, auth, m.a, f"/api/requisicoes/{rid}/checklist").status_code == 200


def test_checklist_sem_flag_registra_o_motorista_recusado(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True, checklist=True)
    rid = viagem_autorizada(client, auth, repo, c, motorista=m.a)
    corpo = {"combustivel": "meio", "avarias": False}
    r = client.post(f"/api/requisicoes/{rid}/checklist/saida", headers=auth(m.a.id), json=corpo)
    assert r.status_code == 403
    # leitura da própria viagem segue permitida; a de outro motorista não
    assert get(client, auth, m.a, f"/api/requisicoes/{rid}/checklist").status_code == 200
    assert get(client, auth, m.b, f"/api/requisicoes/{rid}/checklist").status_code == 404
    assert get(client, auth, c.sol, f"/api/requisicoes/{rid}/checklist").status_code == 403


def test_solicitante_nao_ganha_poder_de_motorista(client, auth, c, m, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO_REGISTRA
    rid = viagem_autorizada(client, auth, repo, c, motorista=m.a)
    assert acao(client, auth, c.sol, rid, "saida").status_code == 403
    assert acao(client, auth, c.outro, rid, "saida").status_code == 403
    assert repo.requisicoes[rid]["status"] == "autorizada"
