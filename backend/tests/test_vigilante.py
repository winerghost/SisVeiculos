"""Perfil Vigilante: registra saída/chegada de qualquer requisição autorizada/em uso e NUNCA
autoriza, nega ou cancela. KM só com `vigilante_km`. Motorista padrão e linha do tempo do motorista.
Relatórios: origem do lançamento de km (vigilante)."""

from __future__ import annotations

from typing import Any

import pytest

from app.modelos import Configuracoes
from tests.fakes_relatorios import SAIDA_REL, VEICULO_REL
from tests.test_checklist import BASE, foto
from tests.test_checklist import post as post_checklist
from tests.test_motorista import atribuir, autorizar, cod, get, viagem_autorizada
from tests.test_requisicoes import acao, criar

KM = Configuracoes(quilometragem=True)
KM_VIG = Configuracoes(quilometragem=True, vigilante_km=True)
MOTORISTA = Configuracoes(motorista=True)


@pytest.fixture
def v(repo):  # type: ignore[no-untyped-def]
    class V:
        vig = repo.novo_perfil("vigilante")
        outro_vig = repo.novo_perfil("vigilante")

    return V


def post(client, auth, quem, rid, nome, **corpo):  # type: ignore[no-untyped-def]
    return client.post(f"/api/requisicoes/{rid}/{nome}", headers=auth(quem.id), json=corpo)


# ------------------------------------------------------------- permissões e máquina de estados
def test_vigilante_nunca_autoriza_nega_ou_cancela(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    for nome in ("autorizar", "negar", "cancelar"):
        r = acao(client, auth, v.vig, rid, nome)
        # autorizar/negar: bloqueado pela rota (403); cancelar: solicitada alheia é invisível (404)
        assert r.status_code == (404 if nome == "cancelar" else 403), nome
    assert repo.requisicoes[rid]["status"] == "solicitada"


def test_vigilante_nao_cancela_nem_a_propria(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    j = criar(client, auth, v.vig, c.veiculo).get_json()
    assert j["acoes_permitidas"] == []  # solicitante cancelaria; vigilante não
    r = acao(client, auth, v.vig, j["id"], "cancelar")
    assert r.status_code == 403 and cod(r) == "sem_permissao"
    assert repo.requisicoes[j["id"]]["status"] == "solicitada"


def test_vigilante_nao_cancela_autorizada_nem_decide(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    rid = viagem_autorizada(client, auth, repo, c)
    assert acao(client, auth, v.vig, rid, "cancelar").status_code == 403
    assert acao(client, auth, v.vig, rid, "autorizar").status_code == 403
    assert repo.requisicoes[rid]["status"] == "autorizada"


def test_vigilante_registra_saida_e_chegada_de_qualquer_requisicao(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    rid = viagem_autorizada(client, auth, repo, c)  # de outro usuário
    j = get(client, auth, v.vig, f"/api/requisicoes/{rid}").get_json()
    assert j["acoes_permitidas"] == ["registrar_saida"]
    r = acao(client, auth, v.vig, rid, "saida")
    assert r.status_code == 200 and r.get_json()["status"] == "em_uso"
    assert repo.requisicoes[rid]["saida_registrada_por"] == v.vig.id
    assert r.get_json()["acoes_permitidas"] == ["registrar_chegada"]
    r = acao(client, auth, v.outro_vig, rid, "chegada")
    assert r.status_code == 200 and r.get_json()["status"] == "concluida"
    assert repo.requisicoes[rid]["chegada_registrada_por"] == v.outro_vig.id


@pytest.mark.parametrize("nome", ["saida", "chegada"])
def test_estado_errado_e_422(client, auth, repo, c, v, nome):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]  # solicitada
    r = acao(client, auth, v.vig, rid, nome)
    assert r.status_code == 404  # solicitada alheia: invisível ao vigilante (anti-IDOR)
    rid2 = viagem_autorizada(client, auth, repo, c, veiculo=repo.novo_veiculo())
    r = acao(client, auth, v.vig, rid2, "chegada")  # autorizada: chegada exige em_uso
    assert r.status_code in {409, 422} and cod(r) == "transicao_invalida"


# --------------------------------------------------------------------------- IDOR / visibilidade
def test_vigilante_nao_ve_solicitada_nem_encerrada_alheia(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    sol = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    assert get(client, auth, v.vig, f"/api/requisicoes/{sol}").status_code == 404
    assert acao(client, auth, v.vig, sol, "saida").status_code == 404
    repo.requisicoes[sol]["status"] = "cancelada"
    assert get(client, auth, v.vig, f"/api/requisicoes/{sol}").status_code == 404
    repo.requisicoes[sol]["status"] = "concluida"
    assert get(client, auth, v.vig, f"/api/requisicoes/{sol}").status_code == 404


def test_listagem_do_vigilante_so_autorizadas_em_uso_e_as_proprias(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    sol_alheia = criar(client, auth, c.sol, c.veiculo).get_json()["id"]  # solicitada alheia
    ve2, ve3 = repo.novo_veiculo(), repo.novo_veiculo()
    aut = viagem_autorizada(client, auth, repo, c, veiculo=ve2)
    propria = criar(client, auth, v.vig, ve3).get_json()["id"]  # solicitada própria
    j = get(client, auth, v.vig, "/api/requisicoes").get_json()
    ids = {i["id"] for i in j["itens"]}
    assert ids == {aut, propria} and sol_alheia not in ids and j["total"] == 2
    # o filtro de status continua valendo
    j = get(client, auth, v.vig, "/api/requisicoes?status=autorizada").get_json()
    assert [i["id"] for i in j["itens"]] == [aut] and j["total"] == 1


def test_vigilante_nao_ve_observacao_nem_foto_nem_ator_de_alheia(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    rid = viagem_autorizada(client, auth, repo, c)
    repo.requisicoes[rid]["observacao"] = "texto livre sensível"
    j = get(client, auth, v.vig, f"/api/requisicoes/{rid}").get_json()
    assert "observacao" not in j and j["solicitante_foto_url"] is None
    assert "autorizado_por" not in j and "autorizado_por_nome" not in j
    assert all("por" not in h for h in j["historico"])


def test_solicitante_e_motorista_nao_ganham_poder_do_vigilante(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = viagem_autorizada(client, auth, repo, c)
    assert acao(client, auth, c.outro, rid, "saida").status_code == 403
    assert acao(client, auth, c.sol, rid, "saida").status_code == 403


def test_vigilante_cria_requisicao_como_solicitante(client, auth, c, v):  # type: ignore[no-untyped-def]
    r = criar(client, auth, v.vig, c.veiculo)
    assert r.status_code == 201 and r.get_json()["usuario_id"] == v.vig.id


def test_eu_informa_permissoes_do_vigilante(client, auth, repo, v):  # type: ignore[no-untyped-def]
    repo.config = KM_VIG
    j = client.get("/api/eu", headers=auth(v.vig.id)).get_json()
    assert j["role"] == "vigilante" and j["rotulo_perfil"].endswith("Vigilante")
    assert "registrar_saida" in j["permissoes"] and "autorizar" not in j["permissoes"]
    assert j["recursos"]["vigilante_km"] is True


# ------------------------------------------------------------------------------- KM do vigilante
def test_vigilante_km_desligada_nao_pede_nem_grava_km(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    repo.config = KM
    rid = viagem_autorizada(client, auth, repo, c)
    j = get(client, auth, v.vig, f"/api/requisicoes/{rid}").get_json()
    assert j["campos_acao"] == {}
    r = post(client, auth, v.vig, rid, "saida", km_saida=100)  # ignorado
    assert r.status_code == 200 and r.get_json()["km_saida"] is None
    r = post(client, auth, v.vig, rid, "chegada", km_chegada=150)
    assert r.status_code == 200 and r.get_json()["km_chegada"] is None


def test_vigilante_km_ligada_exige_e_valida_km(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    repo.config = KM_VIG
    rid = viagem_autorizada(client, auth, repo, c)
    j = get(client, auth, v.vig, f"/api/requisicoes/{rid}").get_json()
    assert j["campos_acao"]["registrar_saida"]["campos_obrigatorios"] == ["km_saida"]
    assert post(client, auth, v.vig, rid, "saida").status_code == 400  # sem km
    assert post(client, auth, v.vig, rid, "saida", km_saida=100).status_code == 200
    assert post(client, auth, v.vig, rid, "chegada").status_code == 400
    r = post(client, auth, v.vig, rid, "chegada", km_chegada=99)  # menor que a saída
    assert r.status_code == 400 and "menor" in r.get_json()["erro"]["mensagem"]
    r = post(client, auth, v.vig, rid, "chegada", km_chegada=100)  # igual é aceito
    assert r.status_code == 200 and r.get_json()["km_chegada"] == 100


def test_vigilante_km_ligada_sem_quilometragem_nao_pede_km(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(vigilante_km=True)  # estado que o banco recusa (CK)
    rid = viagem_autorizada(client, auth, repo, c)
    assert post(client, auth, v.vig, rid, "saida").status_code == 200


def test_staff_continua_pedindo_km_com_vigilante_km_desligada(client, auth, repo, c):  # type: ignore[no-untyped-def]
    repo.config = KM
    rid = viagem_autorizada(client, auth, repo, c)
    assert post(client, auth, c.aut, rid, "saida").status_code == 400


# ----------------------------------------------------------------- admin: usuários e configuração
def test_convite_e_edicao_aceitam_vigilante(client, auth, c, repo):  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/admin/usuarios",
        headers=auth(c.adm.id),
        json={"email": "vig@agu.gov.br", "nome": "Vig", "unidade": "PU", "role": "vigilante"},
    )
    assert r.status_code in {200, 201}, r.get_json()
    alvo = repo.novo_perfil("solicitante")
    r = client.patch(
        f"/api/admin/usuarios/{alvo.id}", headers=auth(c.adm.id), json={"role": "vigilante"}
    )
    assert r.status_code == 200 and r.get_json()["role"] == "vigilante"
    r = client.patch(f"/api/admin/usuarios/{alvo.id}", headers=auth(c.adm.id), json={"role": "xyz"})
    assert r.status_code == 400 and "vigilante" in r.get_json()["erro"]["mensagem"]


def test_vigilante_km_config_exige_quilometragem_e_audita(client, auth, c, repo):  # type: ignore[no-untyped-def]
    def put(corpo: dict[str, Any]) -> Any:
        return client.put("/api/admin/configuracoes", headers=auth(c.adm.id), json=corpo)

    r = put({"vigilante_km": True})  # quilometragem desligada
    assert r.status_code == 409 and "Quilometragem" in r.get_json()["erro"]["mensagem"]
    r = put({"quilometragem": True, "vigilante_km": True})
    assert r.status_code == 200 and r.get_json()["recursos"]["vigilante_km"] is True
    assert any(e.get("acao") == "configuracao_alterada" for e in repo.eventos)
    r = put({"quilometragem": False})  # dependente ligado
    assert r.status_code == 409


def test_nao_admin_nao_altera_vigilante_km(client, auth, v):  # type: ignore[no-untyped-def]
    r = client.put("/api/admin/configuracoes", headers=auth(v.vig.id), json={"vigilante_km": True})
    assert r.status_code == 403


def test_vigilante_nao_acessa_rotas_de_equipe(client, auth, v):  # type: ignore[no-untyped-def]
    assert client.get("/api/motoristas", headers=auth(v.vig.id)).status_code == 403
    assert client.get("/api/admin/usuarios", headers=auth(v.vig.id)).status_code == 403
    assert client.get("/api/motorista/viagens", headers=auth(v.vig.id)).status_code == 403
    assert client.get("/api/relatorios", headers=auth(v.vig.id)).status_code in {200, 403}


# --------------------------------------------------------------------------- motorista padrão
def test_autorizar_designa_o_unico_motorista_ativo(client, auth, repo, c):  # type: ignore[no-untyped-def]
    repo.config = MOTORISTA
    m1 = repo.novo_perfil("motorista")
    repo.novo_perfil("motorista", ativo=False)  # inativo não conta
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    r = autorizar(client, auth, c.aut, rid)
    assert r.status_code == 200 and r.get_json()["motorista"]["id"] == m1.id
    (ev,) = repo.eventos_de("motorista_designado")
    assert ev["ator_id"] == c.aut.id and ev["para"] == m1.id


def test_autorizar_com_dois_motoristas_nao_designa(client, auth, repo, c):  # type: ignore[no-untyped-def]
    repo.config = MOTORISTA
    repo.novo_perfil("motorista")
    repo.novo_perfil("motorista")
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    r = autorizar(client, auth, c.aut, rid)
    assert r.status_code == 200 and r.get_json()["motorista"] is None


def test_autorizar_sem_motorista_cadastrado_ou_recurso_desligado(client, auth, repo, c):  # type: ignore[no-untyped-def]
    repo.config = MOTORISTA
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    assert autorizar(client, auth, c.aut, rid).get_json()["motorista"] is None
    repo.config = Configuracoes()  # recurso desligado: nada é designado
    repo.novo_perfil("motorista")
    rid2 = criar(client, auth, c.sol, repo.novo_veiculo()).get_json()["id"]
    assert autorizar(client, auth, c.aut, rid2).get_json()["motorista"] is None


def test_escolha_explicita_vence_o_padrao(client, auth, repo, c):  # type: ignore[no-untyped-def]
    repo.config = MOTORISTA
    repo.novo_perfil("motorista")
    outro = repo.novo_perfil("motorista")
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    r = autorizar(client, auth, c.aut, rid, motorista_id=outro.id)
    assert r.get_json()["motorista"]["id"] == outro.id


def test_seletor_informa_padrao(client, auth, repo, c):  # type: ignore[no-untyped-def]
    repo.config = MOTORISTA
    j = client.get("/api/motoristas", headers=auth(c.aut.id)).get_json()
    assert j == {"itens": [], "padrao": None}
    m1 = repo.novo_perfil("motorista")
    j = client.get("/api/motoristas", headers=auth(c.aut.id)).get_json()
    assert j["padrao"] == m1.id and j["itens"] == [{"id": m1.id, "nome": m1.nome}]
    repo.novo_perfil("motorista")
    assert client.get("/api/motoristas", headers=auth(c.aut.id)).get_json()["padrao"] is None


# ------------------------------------------------------------- motorista: linha do tempo completa
def test_motorista_ve_linha_do_tempo_completa_sem_dados_da_equipe(client, auth, repo, c):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True, motorista_registra=True)
    m1 = repo.novo_perfil("motorista")
    rid = viagem_autorizada(client, auth, repo, c)  # padrão: m1
    assert acao(client, auth, m1, rid, "saida").status_code == 200
    assert acao(client, auth, m1, rid, "chegada").status_code == 200
    j = get(client, auth, m1, f"/api/motorista/viagens/{rid}").get_json()
    lt = j["linha_do_tempo"]
    assert [e["chave"] for e in lt["etapas"]] == ["solicitada", "autorizada", "em_uso", "concluida"]
    assert all(e["em"] for e in lt["etapas"]) and lt["desfecho"] == "concluida"
    assert lt["etapas"][1]["por"] == {"papel": "Autorizador"}  # sem o nome do autorizador
    assert lt["etapas"][2]["por"] == {"papel": "Motorista"}
    assert c.aut.nome not in str(j) and j["solicitante_foto_url"] is None
    assert "autorizado_por" not in j


def test_motorista_continua_sem_ver_viagem_de_outro(client, auth, repo, c):  # type: ignore[no-untyped-def]
    repo.config = MOTORISTA
    m1 = repo.novo_perfil("motorista")
    repo.novo_perfil("motorista")  # com 2 motoristas nada é designado por padrão
    rid = viagem_autorizada(client, auth, repo, c)
    assert get(client, auth, m1, f"/api/motorista/viagens/{rid}").status_code == 404
    assert atribuir(client, auth, c.aut, rid, m1.id).status_code == 200
    assert get(client, auth, m1, f"/api/motorista/viagens/{rid}").status_code == 200


# -------------------------------------------------------------------------------- relatórios
def km_previa(client, auth, quem, **params: Any) -> dict[str, Any]:
    r = client.get(
        "/api/relatorios/opcionais_km/previa", headers=auth(quem.id), query_string=params
    )
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_relatorio_km_marca_lancamento_do_vigilante(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    repo.config = KM
    repo.lancamentos_vigilante = {"abcd1234": v.vig.nome}
    j = km_previa(client, auth, c.aut)
    chaves = [x["chave"] for x in j["colunas"]]
    assert "km_lancado_por_vigilante" in chaves and "lancado_por" not in chaves  # sem nomes
    assert j["linhas"][0]["km_lancado_por_vigilante"] == "Sim"
    assert v.vig.nome not in str(j)
    j = km_previa(client, auth, c.aut, incluir_nomes="true")
    assert j["linhas"][0]["lancado_por"] == v.vig.nome


def test_relatorio_km_filtro_lancado_por_vigilante(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    repo.config = KM
    j = km_previa(client, auth, c.aut)  # ninguém lançou: Não
    assert j["linhas"][0]["km_lancado_por_vigilante"] == "Não"
    j = km_previa(client, auth, c.aut, lancado_por_vigilante="true")
    assert j["linhas"] == [] and j["total_linhas"] == 0
    repo.lancamentos_vigilante = {"abcd1234": v.vig.nome}
    j = km_previa(client, auth, c.aut, lancado_por_vigilante="true")
    assert j["total_linhas"] == 1


def test_relatorio_km_filtro_invalido_e_catalogo(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    repo.config = KM
    r = client.get(
        "/api/relatorios/opcionais_km/previa",
        headers=auth(c.aut.id),
        query_string={"lancado_por_vigilante": "talvez"},
    )
    assert r.status_code == 400
    cat = client.get("/api/relatorios", headers=auth(c.aut.id)).get_json()
    km = next(x for x in cat["relatorios"] if x["tipo"] == "opcionais_km")
    assert "lancado_por_vigilante" in [f["nome"] for f in km["filtros"]]


def test_relatorio_km_arquivo_e_vigilante_sem_acesso(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    repo.config = KM
    repo.lancamentos_vigilante = {"abcd1234": v.vig.nome}
    r = client.get(
        "/api/relatorios/opcionais_km/arquivo",
        headers=auth(c.aut.id),
        query_string={"incluir_nomes": "true"},
    )
    assert r.status_code == 200 and v.vig.nome.encode() in r.data
    r = client.get("/api/relatorios/opcionais_km/previa", headers=auth(v.vig.id))
    assert r.status_code == 403


# ------------------------------------------------------------------ M1: checklist pelo vigilante
def test_vigilante_registra_checklist_e_depois_saida_e_chegada(client, auth, repo, c, v, storage):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(checklist=True)
    rid = viagem_autorizada(client, auth, repo, c)
    r = acao(client, auth, v.vig, rid, "saida")
    assert r.status_code == 409 and cod(r) == "checklist_obrigatorio"
    f = foto(client, auth, v.vig, rid, storage)  # URL assinada de upload também
    r = post_checklist(client, auth, v.vig, rid, "checklist/saida", {**BASE, "fotos": [f]})
    assert r.status_code == 201, r.get_json()
    assert acao(client, auth, v.vig, rid, "saida").status_code == 200
    assert post_checklist(client, auth, v.vig, rid, "checklist/chegada", BASE).status_code == 201
    assert acao(client, auth, v.vig, rid, "chegada").status_code == 200


def test_vigilante_nao_registra_checklist_de_requisicao_nao_autorizada(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(checklist=True)
    sol = criar(client, auth, c.sol, c.veiculo).get_json()["id"]  # solicitada alheia
    r = post_checklist(client, auth, v.vig, sol, "checklist/saida", BASE)
    assert r.status_code == 404  # anti-IDOR
    assert client.get(f"/api/requisicoes/{sol}/checklist", headers=auth(v.vig.id)).status_code in {
        403,
        404,
    }


def test_vigilante_nao_le_checklist(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(checklist=True)
    rid = viagem_autorizada(client, auth, repo, c)
    r = client.get(f"/api/requisicoes/{rid}/checklist", headers=auth(v.vig.id))
    assert r.status_code == 403


# ------------------------------------------------------------------- M2 / B2: relatório de KM
class _Consulta:
    """Stub encadeável do PostgREST: filtros devolvem a si mesmo; `execute` entrega os dados."""

    def __init__(self, dados: list[dict[str, Any]]) -> None:
        self._dados = dados
        self.not_ = self

    def __getattr__(self, _nome: str) -> Any:
        return lambda *_a, **_k: self

    def execute(self) -> Any:
        return type("R", (), {"data": self._dados})()


def test_repositorio_so_conta_o_campo_com_km_do_vigilante():  # type: ignore[no-untyped-def]
    from app.repositorios.supabase_repo import SupabaseRepositorio

    vig, equipe = "v-1", "e-1"
    base = {"veiculo_id": "vv", "saida_real_em": "2026-11-01T10:00:00+00:00"}

    def req(
        letra: str, km_s: int | None, km_c: int | None, s_por: str, c_por: str
    ) -> dict[str, Any]:
        return {
            "id": letra * 36, **base, "km_saida": km_s, "km_chegada": km_c,
            "saida_registrada_por": s_por, "chegada_registrada_por": c_por,
        }  # fmt: skip

    reqs = [
        req("a", None, 20, vig, equipe),  # misto: saída do vigilante SEM km; equipe lançou os km
        req("b", 10, 20, vig, equipe),  # km_saida lançado pelo vigilante
        req("c", 10, 20, equipe, vig),  # km_chegada lançado pelo vigilante
    ]
    tabelas = {"perfis": [{"id": vig, "nome": "Vig"}], "requisicoes": reqs}
    repo = SupabaseRepositorio.__new__(SupabaseRepositorio)
    repo._c = type("C", (), {"table": lambda _s, nome: _Consulta(tabelas[nome])})()
    saida = repo.lancamentos_vigilante_km(
        inicio="i", fim="f", unidade=None, veiculo=None, usuario=None, incluir_nomes=True
    )
    assert set(saida) == {"b" * 36, "c" * 36}  # "a" não é lançamento do vigilante
    assert saida["b" * 36]["lancado_por"] == "Vig"


def test_relatorio_nao_atribui_por_colisao_de_prefixo(client, auth, repo, c, v):  # type: ignore[no-untyped-def]
    repo.config = KM
    # Outro lançamento de vigilante com o MESMO prefixo, mas outro veículo: não é desta linha.
    repo.lancamentos_extra = {
        "abcd1234-ffff-4000-8000-000000000001": {
            "veiculo_id": "22222222-2222-4222-8222-222222222222",
            "saida_real_em": SAIDA_REL,
            "lancado_por": "Outro Vig",
        }
    }
    j = km_previa(client, auth, c.aut, incluir_nomes="true")
    assert j["linhas"][0]["km_lancado_por_vigilante"] == "Não"
    assert "Outro Vig" not in str(j)
    repo.lancamentos_vigilante = {"abcd1234": v.vig.nome}  # o verdadeiro (mesmo veículo e saída)
    j = km_previa(client, auth, c.aut, incluir_nomes="true")
    assert j["linhas"][0]["lancado_por"] == v.vig.nome and "Outro Vig" not in str(j)
    assert VEICULO_REL  # contrato do fake
