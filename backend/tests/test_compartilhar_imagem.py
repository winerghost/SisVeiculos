"""Recurso opcional `compartilhar_imagem` (decisão do dono 2026-11-17, alternativa D de LGPD).

Ligado, a API inclui `compartilhar_imagem` em `acoes_permitidas` (grupo `consultar`) para quem
já pode ver a requisição; desligado (padrão), nunca. A flag não muda quem vê: os filtros de
dono/papel seguem iguais (404 para requisição alheia). Só o admin liga/desliga, com auditoria.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.modelos import Configuracoes
from app.services.admin_configuracoes import CATALOGO
from app.services.requisicoes import CATEGORIA_ACAO
from tests.test_permissoes_arquivamento import levar_a

ACAO = "compartilhar_imagem"
ESTADOS = ("solicitada", "autorizada", "em_uso", "concluida", "negada", "cancelada")


def _ligar(repo: Any, **extra: bool) -> None:
    repo.config = replace(repo.config, compartilhar_imagem=True, **extra)


def _detalhe(client: Any, auth: Any, quem: Any, rid: str) -> Any:
    return client.get(f"/api/requisicoes/{rid}", headers=auth(quem.id))


def _acoes(client: Any, auth: Any, quem: Any, rid: str) -> list[str]:
    r = _detalhe(client, auth, quem, rid)
    assert r.status_code == 200, r.get_json()
    return list(r.get_json()["acoes_permitidas"])


@pytest.fixture
def mot(repo):  # type: ignore[no-untyped-def]
    return repo.novo_perfil("motorista")


@pytest.fixture
def vig(repo):  # type: ignore[no-untyped-def]
    return repo.novo_perfil("vigilante")


# ------------------------------------------------------------------ padrão: desligado
def test_padrao_desligado() -> None:
    assert Configuracoes().compartilhar_imagem is False
    assert Configuracoes().como_dict()[ACAO] is False


@pytest.mark.parametrize("estado", ESTADOS)
def test_desligado_nenhum_papel_recebe_a_acao(client, auth, repo, c, estado):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, estado)
    for quem in (c.sol, c.aut, c.adm):
        assert ACAO not in _acoes(client, auth, quem, rid)


def test_desligado_motorista_e_vigilante_nao_recebem(client, auth, repo, c, mot, vig):  # type: ignore[no-untyped-def]
    repo.config = replace(repo.config, motorista=True)
    rid = levar_a(client, auth, repo, c, "autorizada", motorista=mot)
    assert ACAO not in _acoes(client, auth, mot, rid)
    assert ACAO not in _acoes(client, auth, vig, rid)


# ------------------------------------------------------------------ ligado
@pytest.mark.parametrize("estado", ESTADOS)
def test_ligado_dono_e_equipe_recebem_em_todo_estado(client, auth, repo, c, estado):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, estado)
    _ligar(repo)
    for quem in (c.sol, c.aut, c.adm):
        j = _detalhe(client, auth, quem, rid).get_json()
        assert ACAO in j["acoes_permitidas"]
        assert ACAO in j["grupos_acao"]["consultar"]


def test_ligado_motorista_atribuido_e_vigilante_recebem(client, auth, repo, c, mot, vig):  # type: ignore[no-untyped-def]
    repo.config = replace(repo.config, motorista=True)
    rid = levar_a(client, auth, repo, c, "autorizada", motorista=mot)
    _ligar(repo)
    assert ACAO in _acoes(client, auth, mot, rid)
    assert ACAO in _acoes(client, auth, vig, rid)


def test_ligado_tambem_na_listagem(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "solicitada")
    _ligar(repo)
    itens = client.get("/api/requisicoes", headers=auth(c.sol.id)).get_json()["itens"]
    alvo = next(i for i in itens if i["id"] == rid)
    assert ACAO in alvo["acoes_permitidas"]


def test_ligado_nao_altera_outras_acoes(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "solicitada")
    antes = _acoes(client, auth, c.sol, rid)
    _ligar(repo)
    depois = _acoes(client, auth, c.sol, rid)
    assert [a for a in depois if a != ACAO] == antes
    assert depois.count(ACAO) == 1


# ------------------------------------------------------------------ não muda quem vê (anti-IDOR)
def test_ligado_solicitante_alheio_continua_sem_ver(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "solicitada")
    _ligar(repo)
    assert _detalhe(client, auth, c.outro, rid).status_code == 404
    itens = client.get("/api/requisicoes", headers=auth(c.outro.id)).get_json()["itens"]
    assert all(i["id"] != rid for i in itens)


def test_ligado_motorista_nao_atribuido_continua_sem_ver(client, auth, repo, c, mot):  # type: ignore[no-untyped-def]
    outro_mot = repo.novo_perfil("motorista")
    repo.config = replace(repo.config, motorista=True)
    rid = levar_a(client, auth, repo, c, "autorizada", motorista=mot)
    _ligar(repo)
    assert _detalhe(client, auth, outro_mot, rid).status_code == 404


def test_ligado_vigilante_nao_ve_concluida_alheia(client, auth, repo, c, vig):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "concluida")  # saída/chegada registradas pelo autorizador
    _ligar(repo)
    assert _detalhe(client, auth, vig, rid).status_code == 404


# ------------------------------------------------------------------ catálogo e administração
def test_catalogo_e_categoria() -> None:
    rotulo, descricao, exige = CATALOGO[ACAO]
    assert rotulo == "Compartilhar requisição como imagem"
    assert exige == ()
    assert "fotos" in descricao and "observação" in descricao
    assert CATEGORIA_ACAO[ACAO] == "consultar"


def test_tela_de_configuracoes_lista_o_item(client, auth, adm):  # type: ignore[no-untyped-def]
    j = client.get("/api/admin/configuracoes", headers=auth(adm.id)).get_json()
    item = next(i for i in j["itens"] if i["chave"] == ACAO)
    assert item["habilitado"] is False and item["acoes_permitidas"] == ["ligar"]
    assert item["exige"] == [] and item["motivo_indisponibilidade"] is None


def test_admin_liga_e_desliga_com_auditoria(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    h = auth(adm.id)
    r = client.put("/api/admin/configuracoes", headers=h, json={ACAO: True})
    assert r.status_code == 200 and r.get_json()["recursos"][ACAO] is True
    assert repo.config.compartilhar_imagem is True
    r = client.put("/api/admin/configuracoes", headers=h, json={ACAO: False})
    assert r.status_code == 200 and repo.config.compartilhar_imagem is False
    ev = repo.eventos_de("configuracao_alterada")
    assert [(e["detalhes"]["recurso"], e["para"], e["ator_id"]) for e in ev] == [
        (ACAO, "true", adm.id),
        (ACAO, "false", adm.id),
    ]


@pytest.mark.parametrize("papel", ["solicitante", "autorizador", "motorista", "vigilante"])
def test_nao_admin_nao_liga(client, auth, repo, papel):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil(papel)
    r = client.put("/api/admin/configuracoes", headers=auth(p.id), json={ACAO: True})
    assert r.status_code == 403
    assert repo.config == Configuracoes() and repo.eventos == []


@pytest.mark.parametrize("valor", ["sim", 1, None])
def test_exige_booleano(client, auth, adm, repo, valor):  # type: ignore[no-untyped-def]
    r = client.put("/api/admin/configuracoes", headers=auth(adm.id), json={ACAO: valor})
    assert r.status_code == 400 and repo.config == Configuracoes()


def test_flag_fora_da_exportacao_de_dados() -> None:
    from app.services.admin_dados import ESQUEMA

    assert ACAO not in ESQUEMA["configuracoes"]
