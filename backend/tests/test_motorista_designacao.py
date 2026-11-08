"""Designação de motorista: o trigger do banco exige `motorista_alterado_por` (quem designa) no
MESMO UPDATE que muda `motorista_id`, senão AG002. Os fakes reproduzem a exigência; estes testes
pegam a regressão de qualquer caminho do backend que esqueça o campo."""

from __future__ import annotations

from typing import Any

import pytest

from app.errors import SemPermissao, TransicaoInvalida
from app.modelos import Configuracoes
from tests.test_motorista import atribuir, autorizar, cod, viagem_autorizada
from tests.test_requisicoes import criar

LIGADO = Configuracoes(motorista=True)


@pytest.fixture
def m(repo):  # type: ignore[no-untyped-def]
    class M:
        a = repo.novo_perfil("motorista")
        b = repo.novo_perfil("motorista")

    return M


def designacoes(repo: Any) -> list[dict[str, Any]]:
    return repo.eventos_de("motorista_designado")


# ------------------------------------------------------------ o fake exige o ator (AG002)
def test_fake_recusa_troca_sem_ator_no_mesmo_update(repo, client, auth, c, m):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = viagem_autorizada(client, auth, repo, c)
    with pytest.raises(SemPermissao):  # AG002 traduzido: ator ausente
        repo.transicionar(rid, "autorizada", {"status": "em_uso", "motorista_id": m.a.id})
    with pytest.raises(SemPermissao):  # ator sem papel de equipe
        repo.atribuir_motorista(rid, m.a.id, ["autorizada"], ator_id=c.sol.id)
    with pytest.raises(SemPermissao):  # o próprio motorista não designa
        repo.atribuir_motorista(rid, m.a.id, ["autorizada"], ator_id=m.a.id)
    assert repo.requisicoes[rid]["motorista_id"] is None
    assert designacoes(repo) == []


def test_fake_recusa_ator_sem_mudanca_de_motorista(repo, client, auth, c, m):  # type: ignore[no-untyped-def]
    """AG004 do banco: `motorista_alterado_por` sem mudar `motorista_id`."""
    repo.config = LIGADO
    rid = viagem_autorizada(client, auth, repo, c, motorista=m.a)
    with pytest.raises(Exception, match="não são válidos"):
        repo.atribuir_motorista(rid, m.a.id, ["autorizada"], ator_id=c.aut.id)


def test_fake_exige_requisicao_autorizada_ou_em_uso(repo, client, auth, c, m):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]  # solicitada
    with pytest.raises(TransicaoInvalida):  # AG032
        repo.transicionar(
            rid,
            "solicitada",
            {"status": "negada", "motorista_id": m.a.id, "motorista_alterado_por": c.aut.id},
        )


# ------------------------------------------------------------ caminhos do backend
def test_autorizar_com_motorista_envia_o_autorizador_do_jwt(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    # o body tenta impor outro responsável: é ignorado, vale o JWT
    r = autorizar(client, auth, c.aut, rid, motorista_id=m.a.id, motorista_alterado_por=c.adm.id)
    assert r.status_code == 200, r.get_json()
    assert repo.requisicoes[rid]["motorista_id"] == m.a.id
    assert "motorista_alterado_por" not in repo.requisicoes[rid]  # nunca persiste
    assert "motorista_alterado_por" not in r.get_json()
    (ev,) = designacoes(repo)
    assert ev["ator_id"] == c.aut.id and ev["para"] == m.a.id and ev["de"] is None


def test_autorizar_sem_motorista_nao_envia_o_campo(client, auth, repo, c):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    assert autorizar(client, auth, c.aut, rid).status_code == 200  # AG004 se enviasse sem mudança
    assert designacoes(repo) == []


def test_atribuir_trocar_e_remover_levam_o_ator_do_jwt(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = viagem_autorizada(client, auth, repo, c)
    assert atribuir(client, auth, c.aut, rid, m.a.id).status_code == 200  # atribuir
    assert atribuir(client, auth, c.adm, rid, m.b.id).status_code == 200  # trocar (outro ator)
    assert atribuir(client, auth, c.aut, rid, None).status_code == 200  # remover
    assert repo.requisicoes[rid]["motorista_id"] is None
    evs = designacoes(repo)
    assert [(e["ator_id"], e["de"], e["para"]) for e in evs] == [
        (c.aut.id, None, m.a.id),
        (c.adm.id, m.a.id, m.b.id),
        (c.aut.id, m.b.id, None),
    ]


def test_atribuir_em_uso_tambem_exige_e_envia_o_ator(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(motorista=True)
    rid = viagem_autorizada(client, auth, repo, c)
    assert client.post(f"/api/requisicoes/{rid}/saida", headers=auth(c.aut.id)).status_code == 200
    assert atribuir(client, auth, c.aut, rid, m.a.id).status_code == 200
    assert designacoes(repo)[-1]["ator_id"] == c.aut.id


def test_atribuir_o_mesmo_motorista_e_idempotente_sem_ator_orfao(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    """Sem mudança de `motorista_id` o banco recusaria o ator (AG004): o service não escreve."""
    repo.config = LIGADO
    rid = viagem_autorizada(client, auth, repo, c, motorista=m.a)
    n = len(designacoes(repo))
    r = atribuir(client, auth, c.aut, rid, m.a.id)
    assert r.status_code == 200 and r.get_json()["motorista"]["id"] == m.a.id
    assert len(designacoes(repo)) == n
    sem = viagem_autorizada(client, auth, repo, c, veiculo=repo.novo_veiculo())
    assert atribuir(client, auth, c.aut, sem, None).status_code == 200  # None -> None


def test_body_nao_define_o_responsavel_da_designacao(client, auth, repo, c, m):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = viagem_autorizada(client, auth, repo, c)
    r = client.post(
        f"/api/requisicoes/{rid}/motorista",
        headers=auth(c.aut.id),
        json={"motorista_id": m.a.id, "motorista_alterado_por": c.adm.id},
    )
    assert r.status_code == 400 and cod(r) == "requisicao_invalida"
    assert repo.requisicoes[rid]["motorista_id"] is None


def test_traducao_dos_erros_novos_de_designacao(repo):  # type: ignore[no-untyped-def]
    from tests.fakes_admin import erro_db

    assert erro_db("AG031").codigo == "motorista_invalido"
    assert erro_db("AG033").codigo == "recurso_desligado"
    assert erro_db("AG032").codigo == "transicao_invalida"
    assert erro_db("AG002").codigo == "sem_permissao"
