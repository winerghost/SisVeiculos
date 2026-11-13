from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest

from app.errors import SemPermissao, TransicaoInvalida, VeiculoOcupado
from app.modelos import Configuracoes
from app.services.requisicoes import ServicoRequisicoes
from tests.fakes import RepoMemoria


def corpo(veiculo_id: str, /, **extra: Any) -> dict[str, Any]:
    base = {
        "nome_solicitante": "Fulano de Tal",
        "unidade": "PU",
        "hora_saida": "2026-11-10T08:30:00-03:00",
        "trajeto": "AGU -> Fórum",
        "veiculo_id": veiculo_id,
    }
    return {**base, **extra}


@pytest.fixture
def cenario(repo: RepoMemoria):  # type: ignore[no-untyped-def]
    class C:
        sol = repo.novo_perfil("solicitante")
        outro = repo.novo_perfil("solicitante")
        aut = repo.novo_perfil("autorizador")
        adm = repo.novo_perfil("admin")
        veiculo = repo.novo_veiculo()

    return C


def criar(client, auth, perfil, veiculo, **extra):  # type: ignore[no-untyped-def]
    return client.post("/api/requisicoes", headers=auth(perfil.id), json=corpo(veiculo, **extra))


def acao(client, auth, perfil, rid, nome):  # type: ignore[no-untyped-def]
    return client.post(f"/api/requisicoes/{rid}/{nome}", headers=auth(perfil.id))


# ---------------------------------------------------------------- criação / validação
def test_criar_usa_usuario_do_jwt_e_devolve_acoes(client, auth, cenario):  # type: ignore[no-untyped-def]
    r = criar(client, auth, cenario.sol, cenario.veiculo)
    assert r.status_code == 201
    j = r.get_json()
    assert j["usuario_id"] == cenario.sol.id and j["status"] == "solicitada"
    assert j["acoes_permitidas"] == ["cancelar"]


def test_usuario_id_no_body_e_recusado(client, auth, cenario):  # type: ignore[no-untyped-def]
    r = criar(client, auth, cenario.sol, cenario.veiculo, usuario_id=cenario.outro.id)
    assert r.status_code == 400


@pytest.mark.parametrize(
    "extra",
    [
        {"trajeto": ""},
        {"nome_solicitante": None},
        {"unidade": "XX"},
        {"hora_saida": "amanhã"},
        {"hora_saida": "2026-11-10T08:30:00"},  # sem fuso
        {"observacao": "x" * 501},
        {"veiculo_id": "nao-e-uuid"},
        {"status": "autorizada"},
    ],
)
def test_validacao(client, auth, cenario, extra):  # type: ignore[no-untyped-def]
    r = criar(client, auth, cenario.sol, cenario.veiculo, **extra)
    assert r.status_code == 400 and r.get_json()["erro"]["codigo"] == "requisicao_invalida"


def test_veiculo_inexistente_ou_inativo(client, auth, cenario, repo):  # type: ignore[no-untyped-def]
    inativo = repo.novo_veiculo(ativo=False)
    for vid in (inativo, str(uuid.uuid4())):
        r = criar(client, auth, cenario.sol, vid)
        assert r.status_code == 409 and r.get_json()["erro"]["codigo"] == "veiculo_indisponivel"


# ---------------------------------------------------------------- veículo ocupado
def test_veiculo_ocupado_mensagem_clara_e_disponibilidade(client, auth, cenario):  # type: ignore[no-untyped-def]
    assert criar(client, auth, cenario.sol, cenario.veiculo).status_code == 201
    r = criar(client, auth, cenario.outro, cenario.veiculo)
    assert r.status_code == 409
    e = r.get_json()["erro"]
    assert e["codigo"] == "veiculo_ocupado" and "requisição em aberto" in e["mensagem"]

    v = client.get("/api/veiculos", headers=auth(cenario.outro.id)).get_json()["itens"][0]
    assert v["disponivel"] is False and v["acoes_permitidas"] == []


@pytest.mark.parametrize("final", ["negar", "cancelar"])
def test_encerrar_libera_veiculo(client, auth, cenario, final):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cenario.sol, cenario.veiculo).get_json()["id"]
    quem = cenario.aut if final == "negar" else cenario.sol
    assert acao(client, auth, quem, rid, final).status_code == 200
    assert criar(client, auth, cenario.outro, cenario.veiculo).status_code == 201


def test_concluir_libera_veiculo(client, auth, cenario):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cenario.sol, cenario.veiculo).get_json()["id"]
    for n in ("autorizar", "saida", "chegada"):
        assert acao(client, auth, cenario.aut, rid, n).status_code == 200
    v = client.get("/api/veiculos", headers=auth(cenario.sol.id)).get_json()["itens"][0]
    assert v["disponivel"] is True and v["acoes_permitidas"] == ["solicitar"]


class SemPreChecagem:
    """Simula a janela entre 'checar se está livre' e 'inserir' (a corrida real)."""

    def __init__(self, repo: RepoMemoria) -> None:
        self._repo = repo

    def __getattr__(self, nome: str) -> Any:
        return getattr(self._repo, nome)

    def veiculo_ocupado(self, veiculo_id: str) -> bool:
        return False


def test_sobreposicao_concorrente_so_uma_persiste(repo, cenario):  # type: ignore[no-untyped-def]
    """Criações simultâneas passam pela pré-checagem; o 'índice' (atômico) decide."""
    svc = ServicoRequisicoes(SemPreChecagem(repo))  # type: ignore[arg-type]
    barreira = threading.Barrier(8)
    resultados: list[str] = []

    def tentar(perfil) -> None:  # type: ignore[no-untyped-def]
        barreira.wait()
        try:
            svc.criar(perfil, corpo(cenario.veiculo))
            resultados.append("ok")
        except VeiculoOcupado:
            resultados.append("ocupado")

    perfis = [repo.novo_perfil() for _ in range(8)]
    ts = [threading.Thread(target=tentar, args=(p,)) for p in perfis]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert resultados.count("ok") == 1 and resultados.count("ocupado") == 7
    assert len(repo.requisicoes) == 1


# ---------------------------------------------------------------- máquina de estados
def test_fluxo_completo_e_auditoria(client, auth, cenario, repo):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cenario.sol, cenario.veiculo).get_json()["id"]
    j = acao(client, auth, cenario.aut, rid, "autorizar").get_json()
    assert j["status"] == "autorizada" and j["autorizado_por"] == cenario.aut.id
    assert set(j["acoes_permitidas"]) == {"registrar_saida", "cancelar"}
    j = acao(client, auth, cenario.aut, rid, "saida").get_json()
    assert j["status"] == "em_uso" and j["saida_registrada_por"] == cenario.aut.id
    assert j["acoes_permitidas"] == ["registrar_chegada"]
    j = acao(client, auth, cenario.adm, rid, "chegada").get_json()
    assert j["status"] == "concluida"
    assert j["acoes_permitidas"] == ["ver_localizacao", "arquivar"]  # admin: sem transições
    assert [(a, b) for _, a, b in repo.auditoria] == [
        (None, "solicitada"), ("solicitada", "autorizada"),
        ("autorizada", "em_uso"), ("em_uso", "concluida"),
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("preparo", "acao_invalida"),
    [
        ([], "saida"),
        ([], "chegada"),
        (["autorizar"], "chegada"),
        (["autorizar"], "autorizar"),
        (["autorizar"], "negar"),
        (["negar"], "autorizar"),
        (["negar"], "cancelar"),
        (["autorizar", "saida"], "cancelar"),
        (["autorizar", "saida"], "saida"),
        (["autorizar", "saida", "chegada"], "cancelar"),
        (["autorizar", "saida", "chegada"], "chegada"),
    ],
)
def test_transicoes_invalidas(client, auth, cenario, preparo, acao_invalida):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cenario.sol, cenario.veiculo).get_json()["id"]
    for n in preparo:
        assert acao(client, auth, cenario.aut, rid, n).status_code == 200
    r = acao(client, auth, cenario.aut, rid, acao_invalida)
    assert r.status_code == 409 and r.get_json()["erro"]["codigo"] == "transicao_invalida"


def test_transicao_condicional_perdida(repo, cenario):  # type: ignore[no-untyped-def]
    """Estado mudou entre a leitura e o UPDATE: 409, nunca sobrescrita."""
    svc = ServicoRequisicoes(repo)
    req = svc.criar(cenario.sol, corpo(cenario.veiculo))
    foto_antiga = repo.obter_requisicao(req["id"])
    repo.requisicoes[req["id"]]["status"] = "cancelada"  # outra pessoa cancelou
    repo.obter_requisicao = lambda _rid: foto_antiga  # type: ignore[method-assign]
    with pytest.raises(TransicaoInvalida):
        svc.executar("autorizar", cenario.aut, req["id"])
    assert repo.requisicoes[req["id"]]["status"] == "cancelada"


# ---------------------------------------------------------------- permissões
@pytest.mark.parametrize("nome", ["autorizar", "negar", "saida", "chegada"])
def test_solicitante_nao_decide(client, auth, cenario, nome):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cenario.sol, cenario.veiculo).get_json()["id"]
    r = acao(client, auth, cenario.sol, rid, nome)
    assert r.status_code == 403 and r.get_json()["erro"]["codigo"] == "sem_permissao"


def test_solicitante_nao_cancela_autorizada(client, auth, cenario):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cenario.sol, cenario.veiculo).get_json()["id"]
    acao(client, auth, cenario.aut, rid, "autorizar")
    assert acao(client, auth, cenario.sol, rid, "cancelar").status_code == 403


def test_autorizador_cancela_autorizada(client, auth, cenario):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cenario.sol, cenario.veiculo).get_json()["id"]
    acao(client, auth, cenario.aut, rid, "autorizar")
    assert acao(client, auth, cenario.aut, rid, "cancelar").status_code == 200


def test_autoautorizacao_bloqueada_por_padrao(client, auth, cenario):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cenario.aut, cenario.veiculo).get_json()["id"]
    r = acao(client, auth, cenario.aut, rid, "autorizar")
    assert r.status_code == 403
    assert acao(client, auth, cenario.adm, rid, "autorizar").status_code == 200


def test_autoautorizacao_quando_permitida(repo, cenario):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(autorizador_decide_propria=True)
    svc = ServicoRequisicoes(repo)
    req = svc.criar(cenario.aut, corpo(cenario.veiculo))
    assert svc.executar("autorizar", cenario.aut, req["id"])["status"] == "autorizada"


def test_service_recusa_mesmo_sem_decorator(repo, cenario):  # type: ignore[no-untyped-def]
    svc = ServicoRequisicoes(repo)
    req = svc.criar(cenario.sol, corpo(cenario.veiculo))
    with pytest.raises(SemPermissao):
        svc.executar("autorizar", cenario.sol, req["id"])


# ---------------------------------------------------------------- IDOR
def test_idor_listagem_so_proprias(client, auth, cenario, repo):  # type: ignore[no-untyped-def]
    outro_v = repo.novo_veiculo()
    criar(client, auth, cenario.sol, cenario.veiculo)
    criar(client, auth, cenario.outro, outro_v)
    itens = client.get("/api/requisicoes", headers=auth(cenario.sol.id)).get_json()["itens"]
    assert [i["usuario_id"] for i in itens] == [cenario.sol.id]
    assert (
        len(client.get("/api/requisicoes", headers=auth(cenario.aut.id)).get_json()["itens"]) == 2
    )


def test_idor_obter_e_agir_em_alheia_da_404(client, auth, cenario):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cenario.sol, cenario.veiculo).get_json()["id"]
    assert client.get(f"/api/requisicoes/{rid}", headers=auth(cenario.outro.id)).status_code == 404
    assert acao(client, auth, cenario.outro, rid, "cancelar").status_code == 404
    inexistente = client.get(f"/api/requisicoes/{uuid.uuid4()}", headers=auth(cenario.outro.id))
    assert inexistente.status_code == 404  # indistinguível de "alheia"
    assert client.get(f"/api/requisicoes/{rid}", headers=auth(cenario.aut.id)).status_code == 200
    assert client.get(f"/api/requisicoes/{rid}", headers=auth(cenario.sol.id)).status_code == 200


def test_filtro_status_invalido(client, auth, cenario):  # type: ignore[no-untyped-def]
    r = client.get("/api/requisicoes?status=xyz", headers=auth(cenario.sol.id))
    assert r.status_code == 400


def test_id_malformado(client, auth, cenario):  # type: ignore[no-untyped-def]
    assert (
        client.get("/api/requisicoes/1' or '1'='1", headers=auth(cenario.sol.id)).status_code == 400
    )


def test_conta_inativa_nao_age(client, auth, cenario, repo):  # type: ignore[no-untyped-def]
    rid = criar(client, auth, cenario.sol, cenario.veiculo).get_json()["id"]
    repo.perfis[cenario.aut.id] = type(cenario.aut)(
        cenario.aut.id, cenario.aut.nome, "PU", "autorizador", False
    )
    assert acao(client, auth, cenario.aut, rid, "autorizar").status_code == 403
