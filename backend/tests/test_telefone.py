"""Telefone (WhatsApp) do usuário: só o admin cadastra; vai ao webhook n8n (exceção aprovada).

Cobre: normalização e validação, admin-only (role/IDOR), auditoria sem o valor, convite levando o
telefone ao perfil, nenhuma rota não-admin devolvendo telefone e o bloco `contatos` em todo
evento de requisição (e `telefone` nas pessoas de `detalhes` em saída/chegada).
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any

import pytest

from app.services.validacao_admin import MSG_TELEFONE, normalizar_telefone
from tests.fakes import RepoMemoria
from tests.test_admin_convites import EMAIL, aceitar, confirmar_conta, convidar
from tests.test_notificacoes import Gravador, montar_app
from tests.test_relatorios import mk  # noqa: F401 - fixture (app com e-mail de relatórios)
from tests.test_requisicoes import acao, criar

TEL = "5561999998888"


def _erro(r) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return r.get_json()["erro"]


def _patch(client, auth, ator, alvo, corpo):  # type: ignore[no-untyped-def]
    return client.patch(f"/api/admin/usuarios/{alvo}", headers=auth(ator.id), json=corpo)


# ------------------------------------------------------------------ normalização
@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("5561999998888", "5561999998888"),
        ("+55 (61) 99999-8888", "5561999998888"),
        ("55 61 3333-4444", "556133334444"),  # fixo: 12 dígitos
        ("", None),
        ("  ", None),
        (None, None),
    ],
)
def test_normalizar_telefone_aceita(bruto: Any, esperado: str | None) -> None:
    assert normalizar_telefone(bruto) == esperado


@pytest.mark.parametrize(
    "bruto",
    [
        "61999998888",  # sem DDI
        "1561999998888",  # DDI errado
        "5501999998888",  # DDD com zero
        "55619999988881",  # longo demais
        "55619999",  # curto
        "5561a99998888",
        "5561９99998888",  # dígito unicode
        "5561.99999.8888",
        5561999998888,
        "5" * 41,
    ],
)
def test_normalizar_telefone_recusa(bruto: Any) -> None:
    from app.errors import RequisicaoInvalida

    with pytest.raises(RequisicaoInvalida) as exc:
        normalizar_telefone(bruto)
    assert exc.value.mensagem == MSG_TELEFONE  # mensagem fixa: o valor nunca ecoa


# ------------------------------------------------------------------ PATCH (admin)
def test_admin_define_normaliza_e_limpa_telefone(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    sol = repo.novo_perfil("solicitante")
    r = _patch(client, auth, adm, sol.id, {"telefone": "+55 (61) 99999-8888"})
    assert r.status_code == 200 and r.get_json()["telefone"] == TEL
    det = client.get(f"/api/admin/usuarios/{sol.id}", headers=auth(adm.id)).get_json()
    assert det["telefone"] == TEL
    lista = client.get("/api/admin/usuarios", headers=auth(adm.id)).get_json()
    assert {i["id"]: i["telefone"] for i in lista["itens"]}[sol.id] == TEL
    assert _patch(client, auth, adm, sol.id, {"telefone": ""}).get_json()["telefone"] is None
    _patch(client, auth, adm, sol.id, {"telefone": TEL})
    assert _patch(client, auth, adm, sol.id, {"telefone": None}).get_json()["telefone"] is None


def test_telefone_invalido_mensagem_do_contrato(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    sol = repo.novo_perfil("solicitante")
    r = _patch(client, auth, adm, sol.id, {"telefone": "61999998888"})
    assert r.status_code == 400
    assert _erro(r)["codigo"] == "requisicao_invalida" and _erro(r)["mensagem"] == MSG_TELEFONE
    assert repo.telefones.get(sol.id) is None


def test_admin_pode_alterar_o_proprio_telefone(client, auth, adm):  # type: ignore[no-untyped-def]
    r = _patch(client, auth, adm, adm.id, {"telefone": TEL})
    assert r.status_code == 200 and r.get_json()["telefone"] == TEL


@pytest.mark.parametrize("role", ["solicitante", "autorizador", "motorista", "vigilante"])
def test_nao_admin_nao_altera_telefone_nem_o_proprio(client, auth, repo, role):  # type: ignore[no-untyped-def]
    ator = repo.novo_perfil(role)
    outro = repo.novo_perfil("solicitante")
    for alvo in (ator.id, outro.id):  # o próprio e o de outra pessoa (IDOR)
        r = _patch(client, auth, ator, alvo, {"telefone": TEL})
        assert r.status_code == 403
        assert repo.telefones.get(alvo) is None
        assert client.get(f"/api/admin/usuarios/{alvo}", headers=auth(ator.id)).status_code == 403


def test_auditoria_do_telefone_sem_o_valor(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    sol = repo.novo_perfil("solicitante")
    _patch(client, auth, adm, sol.id, {"telefone": TEL})
    _patch(client, auth, adm, sol.id, {"telefone": TEL})  # sem mudança: nada novo
    ev = repo.eventos_de("perfil_telefone_alterado")
    assert len(ev) == 1
    assert ev[0]["ator_id"] == adm.id and ev[0]["detalhes"] == {"telefone_alterado": True}
    assert ev[0]["de"] is None and ev[0]["para"] is None
    _patch(client, auth, adm, sol.id, {"telefone": None})
    assert len(repo.eventos_de("perfil_telefone_alterado")) == 2
    assert "99999" not in json.dumps(repo.eventos)
    assert repo.eventos_de("acao_administrativa") == []  # sem auditoria duplicada no backend


def test_log_nunca_contem_telefone(client, auth, adm, repo, caplog):  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    sol = repo.novo_perfil("solicitante")
    _patch(client, auth, adm, sol.id, {"telefone": TEL})
    _patch(client, auth, adm, sol.id, {"telefone": "5561 9999 88"})
    assert "99999" not in caplog.text and "9999 88" not in caplog.text


# ------------------------------------------------------------------ convite
def test_convite_leva_telefone_ao_perfil_no_aceite(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    r = convidar(client, auth, adm, telefone="+55 61 99999-8888")
    assert r.status_code == 201
    assert "telefone" not in r.get_json()["convite"]  # o convite não expõe o número
    cid = r.get_json()["convite"]["id"]
    assert repo.convites[cid]["telefone"] == TEL
    uid = confirmar_conta(auth_admin)
    eu = aceitar(client, auth, uid, auth_admin.ultimo_token())
    assert eu.status_code == 200 and "telefone" not in json.dumps(eu.get_json())
    assert repo.telefones[uid] == TEL
    det = client.get(f"/api/admin/usuarios/{uid}", headers=auth(adm.id)).get_json()
    assert det["telefone"] == TEL


def test_convite_sem_telefone_nao_grava_a_coluna(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    for valor in ({}, {"telefone": ""}, {"telefone": None}):
        repo.convites.clear()
        r = convidar(client, auth, adm, **valor)
        assert r.status_code == 201
        assert "telefone" not in repo.convites[r.get_json()["convite"]["id"]]


def test_convite_telefone_invalido(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    r = convidar(client, auth, adm, telefone="123")
    assert r.status_code == 400 and _erro(r)["mensagem"] == MSG_TELEFONE
    assert repo.convites == {} and auth_admin.envios == []


def test_convite_por_nao_admin_recusado(client, auth, repo):  # type: ignore[no-untyped-def]
    aut = repo.novo_perfil("autorizador")
    r = client.post(
        "/api/admin/usuarios",
        headers=auth(aut.id),
        json={"email": EMAIL, "nome": "X", "unidade": "PU", "telefone": TEL},
    )
    assert r.status_code == 403 and repo.convites == {}


def test_erro_de_telefone_indica_o_campo(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    """Acessibilidade: `campo` permite ao front ligar a mensagem ao input (aria-invalid)."""
    sol = repo.novo_perfil("solicitante")
    r = _patch(client, auth, adm, sol.id, {"telefone": "61999998888"})
    assert _erro(r) == {
        "codigo": "requisicao_invalida",
        "mensagem": MSG_TELEFONE,
        "campo": "telefone",
    }
    r = convidar(client, auth, adm, telefone="123")
    assert r.status_code == 400 and _erro(r)["campo"] == "telefone"
    # Demais erros seguem sem `campo` (formato inalterado).
    r = _patch(client, auth, adm, sol.id, {"unidade": "XYZ"})
    assert r.status_code == 400 and "campo" not in _erro(r)


@pytest.mark.parametrize("ck", ["ck_perfis_telefone", "ck_convites_telefone"])
def test_check_do_banco_traduzido_com_campo(ck: str) -> None:
    from app.errors import RequisicaoInvalida
    from tests.fakes_admin import erro_db

    erro = erro_db("23514", f'new row violates check constraint "{ck}"')
    assert isinstance(erro, RequisicaoInvalida)
    assert erro.mensagem == MSG_TELEFONE and erro.campo == "telefone"


# ------------------------------------------------------------------ rotas não-admin
ROTAS_POR_PAPEL = {
    "solicitante": ("/api/requisicoes", "/api/requisicoes/{rid}"),
    "autorizador": ("/api/requisicoes", "/api/requisicoes/{rid}", "/api/motoristas"),
    "motorista": ("/api/motorista/viagens", "/api/motorista/viagens/{rid}"),
    "vigilante": ("/api/requisicoes",),
}


@pytest.mark.parametrize("papel", sorted(ROTAS_POR_PAPEL))
def test_nenhuma_rota_nao_admin_devolve_telefone(  # type: ignore[no-untyped-def]
    mk, auth, repo, papel  # noqa: F811 - fixture importada de test_relatorios
):
    """Com o recurso Motorista ligado (seletor de motoristas e rotas do motorista ativos) e o
    e-mail de relatórios ligado (`/api/relatorios/destinatarios`)."""
    repo.config = replace(repo.ler_configuracoes(), motorista=True, relatorio_email=True)
    client = mk().test_client()
    sol = repo.novo_perfil("solicitante")
    aut = repo.novo_perfil("autorizador")
    mot = repo.novo_perfil("motorista")
    vig = repo.novo_perfil("vigilante")
    atores = {"solicitante": sol, "autorizador": aut, "motorista": mot, "vigilante": vig}
    for p in atores.values():
        repo.telefones[p.id] = TEL
    veiculo = repo.novo_veiculo()
    rid = criar(client, auth, sol, veiculo).get_json()["id"]
    assert acao(client, auth, aut, rid, "autorizar").status_code == 200  # motorista padrão
    h = auth(atores[papel].id)
    for url in ("/api/eu", "/api/relatorios/destinatarios", *ROTAS_POR_PAPEL[papel]):
        r = client.get(url.format(rid=rid), headers=h)
        assert r.status_code == 200, url
        assert TEL not in r.get_data(as_text=True), url


# ------------------------------------------------------------------ webhook
@pytest.fixture
def grav() -> Gravador:
    return Gravador()


@pytest.fixture
def client_n(repo, auth_admin, storage, grav):  # type: ignore[no-untyped-def]
    return montar_app(repo, grav, auth_admin, storage).test_client()


@pytest.fixture
def pessoas(repo: RepoMemoria):  # type: ignore[no-untyped-def]
    class C:
        sol = repo.novo_perfil("solicitante")
        aut = repo.novo_perfil("autorizador")
        mot = repo.novo_perfil("motorista")
        veiculo = repo.novo_veiculo()

    repo.telefones[C.sol.id] = "5561911112222"
    repo.telefones[C.aut.id] = "5561933334444"
    repo.telefones[C.mot.id] = "556155556666"
    return C


def _p(repo, perfil, *, com_email: bool = True):  # type: ignore[no-untyped-def]
    """Pessoa como o webhook monta: e-mail de login (auth.users) junto do telefone."""
    email = repo.contas_auth[perfil.id]["email"] if com_email else None
    return {
        "id": perfil.id,
        "nome": perfil.nome,
        "telefone": repo.telefones.get(perfil.id),
        "email": email,
    }


def test_contatos_em_todos_os_eventos_com_motorista(client_n, auth, grav, repo, pessoas):  # type: ignore[no-untyped-def]
    repo.config = replace(repo.ler_configuracoes(), motorista=True)
    c = pessoas
    rid = criar(client_n, auth, c.sol, c.veiculo).get_json()["id"]
    acao(client_n, auth, c.aut, rid, "autorizar")  # motorista padrão (único ativo)
    acao(client_n, auth, c.aut, rid, "saida")
    acao(client_n, auth, c.aut, rid, "chegada")
    assert grav.eventos == [
        "requisicao.criada",
        "requisicao.autorizada",
        "requisicao.saida",
        "requisicao.chegada",
    ]
    criada, *depois = grav.contatos
    assert criada == {"solicitante": _p(repo, c.sol), "autorizador": None, "motorista": None}
    for ct in depois:
        assert ct == {
            "solicitante": _p(repo, c.sol),
            "autorizador": _p(repo, c.aut),
            "motorista": _p(repo, c.mot),
        }
    for evento in ("requisicao.saida", "requisicao.chegada"):
        d = dict(grav.detalhes)[evento]
        assert d["solicitante"] == _p(repo, c.sol)
        assert d["autorizado_por"] == _p(repo, c.aut)
        assert d["motorista"] == _p(repo, c.mot)


def test_contatos_sem_motorista_quando_recurso_desligado(client_n, auth, grav, repo, pessoas):  # type: ignore[no-untyped-def]
    c = pessoas
    assert not repo.ler_configuracoes().motorista
    r1 = criar(client_n, auth, c.sol, c.veiculo).get_json()["id"]
    acao(client_n, auth, c.aut, r1, "negar")
    r2 = criar(client_n, auth, c.sol, c.veiculo).get_json()["id"]
    acao(client_n, auth, c.sol, r2, "cancelar")
    assert grav.eventos == [
        "requisicao.criada",
        "requisicao.negada",
        "requisicao.criada",
        "requisicao.cancelada",
    ]
    for ct in grav.contatos:
        assert ct is not None and "motorista" not in ct
        assert ct["solicitante"] == _p(repo, c.sol)
    assert grav.contatos[1]["autorizador"] == _p(repo, c.aut)  # negada: quem negou
    assert grav.contatos[3]["autorizador"] is None  # cancelada antes de decidir


def test_pessoa_sem_telefone_sai_com_null(client_n, auth, grav, repo, pessoas):  # type: ignore[no-untyped-def]
    repo.telefones.pop(pessoas.sol.id)
    criar(client_n, auth, pessoas.sol, pessoas.veiculo)
    assert grav.contatos[-1]["solicitante"]["telefone"] is None


@pytest.mark.parametrize("motorista", [False, True])
def test_webhook_reaproveita_config_e_le_perfis_uma_vez(  # type: ignore[no-untyped-def]
    client_n, auth, grav, repo, pessoas, motorista, monkeypatch
):
    """Entre o commit da transição e a notificação: nenhuma leitura de `configuracoes` (usa a
    da ação) e uma só de `perfis` (em saída/chegada, `contatos` reaproveita a de `detalhes`)."""
    repo.config = replace(repo.ler_configuracoes(), motorista=motorista)
    c = pessoas
    rid = criar(client_n, auth, c.sol, c.veiculo).get_json()["id"]
    janela = {"aberta": False, "config": 0, "perfis": 0}

    def contar(nome: str, chave: str) -> None:
        original = getattr(repo, nome)

        def contado(*a, **kw):  # type: ignore[no-untyped-def]
            if janela["aberta"]:
                janela[chave] += 1
            return original(*a, **kw)

        monkeypatch.setattr(repo, nome, contado)

    contar("ler_configuracoes", "config")
    contar("contatos_perfis", "perfis")
    transicionar, notificar = repo.transicionar, grav.notificar

    def abre(*a, **kw):  # type: ignore[no-untyped-def]
        r = transicionar(*a, **kw)
        janela["aberta"] = True
        return r

    def fecha(*a, **kw):  # type: ignore[no-untyped-def]
        janela["aberta"] = False
        return notificar(*a, **kw)

    monkeypatch.setattr(repo, "transicionar", abre)
    monkeypatch.setattr(grav, "notificar", fecha)
    for nome in ("autorizar", "saida", "chegada"):
        janela.update(config=0, perfis=0)
        assert acao(client_n, auth, c.aut, rid, nome).status_code == 200
        assert (janela["config"], janela["perfis"]) == (0, 1), nome
    assert grav.contatos[-1]["solicitante"] == _p(repo, c.sol)
    assert grav.contatos[-1]["autorizador"] == _p(repo, c.aut)
    assert ("motorista" in grav.contatos[-1]) is motorista
    assert dict(grav.detalhes)["requisicao.chegada"]["solicitante"] == _p(repo, c.sol)


def test_log_de_contatos_indisponiveis_traz_o_evento_sem_pii(  # type: ignore[no-untyped-def]
    client_n, auth, grav, repo, pessoas, caplog
):
    caplog.set_level(logging.WARNING)

    def quebra(ids: set[str]) -> dict[str, Any]:
        raise RuntimeError(f"Fulano {TEL}")

    repo.contatos_perfis = quebra  # type: ignore[method-assign]
    rid = criar(client_n, auth, pessoas.sol, pessoas.veiculo).get_json()["id"]
    acao(client_n, auth, pessoas.aut, rid, "autorizar")
    assert grav.contatos == [None, None]
    assert "n8n: contatos indisponiveis evento=requisicao.criada (RuntimeError)" in caplog.text
    assert "n8n: contatos indisponiveis evento=requisicao.autorizada (RuntimeError)" in caplog.text
    assert TEL not in caplog.text and "Fulano" not in caplog.text


# ------------------------------------------------------------------ e-mail de login no webhook
def _emails(repo, *perfis) -> set[str]:  # type: ignore[no-untyped-def]
    return {repo.contas_auth[p.id]["email"] for p in perfis}


def test_email_nas_pessoas_de_saida_e_chegada_inclusive_checklist_e_atores(  # type: ignore[no-untyped-def]
    client_n, auth, grav, repo, pessoas
):
    repo.config = replace(repo.ler_configuracoes(), motorista=True)
    c = pessoas
    rid = criar(client_n, auth, c.sol, c.veiculo).get_json()["id"]
    acao(client_n, auth, c.aut, rid, "autorizar")
    acao(client_n, auth, c.aut, rid, "saida")
    acao(client_n, auth, c.aut, rid, "chegada")
    d = dict(grav.detalhes)["requisicao.chegada"]
    for papel in ("autorizado_por", "saida_registrada_por", "chegada_registrada_por"):
        assert d[papel] == _p(repo, c.aut), papel
    assert d["motorista"]["email"] == repo.contas_auth[c.mot.id]["email"]
    s = dict(grav.detalhes)["requisicao.saida"]
    vazia = {"id": None, "nome": None, "telefone": None, "email": None}
    assert s["chegada_registrada_por"] == vazia


def test_email_no_checklist_registrado_por(client_n, auth, grav, repo, pessoas):  # type: ignore[no-untyped-def]
    from tests.test_checklist import BASE, post

    repo.config = replace(repo.ler_configuracoes(), checklist=True)
    c = pessoas
    rid = criar(client_n, auth, c.sol, c.veiculo).get_json()["id"]
    acao(client_n, auth, c.aut, rid, "autorizar")
    assert post(client_n, auth, c.aut, rid, "checklist/saida", BASE).status_code == 201
    assert acao(client_n, auth, c.aut, rid, "saida").status_code == 200
    ck = dict(grav.detalhes)["requisicao.saida"]["checklist"]
    assert ck["registrado_por"] == _p(repo, c.aut)


def test_conta_inativa_tambem_envia_email(client_n, auth, grav, repo, pessoas):  # type: ignore[no-untyped-def]
    c = pessoas
    rid = criar(client_n, auth, c.sol, c.veiculo).get_json()["id"]
    repo.perfis[c.sol.id] = replace(repo.perfis[c.sol.id], ativo=False)
    acao(client_n, auth, c.aut, rid, "negar")
    assert grav.contatos[-1]["solicitante"] == _p(repo, c.sol)


def test_uma_so_chamada_a_admin_api_por_acao(client_n, auth, auth_admin, grav, repo, pessoas):  # type: ignore[no-untyped-def]
    repo.config = replace(repo.ler_configuracoes(), motorista=True)
    c = pessoas
    rid = criar(client_n, auth, c.sol, c.veiculo).get_json()["id"]
    assert len(auth_admin.consultas_email) == 1
    for nome in ("autorizar", "saida", "chegada"):
        antes = len(auth_admin.consultas_email)
        assert acao(client_n, auth, c.aut, rid, nome).status_code == 200
        assert len(auth_admin.consultas_email) == antes + 1, nome
    assert auth_admin.consultas_email[-1] == {c.sol.id, c.aut.id, c.mot.id}


def test_falha_da_admin_api_email_none_acao_segue_e_log_sem_pii(  # type: ignore[no-untyped-def]
    client_n, auth, auth_admin, grav, repo, pessoas, caplog
):
    caplog.set_level(logging.DEBUG)
    auth_admin.falhar_emails = True
    c = pessoas
    r = criar(client_n, auth, c.sol, c.veiculo)
    assert r.status_code == 201
    rid = r.get_json()["id"]
    for nome in ("autorizar", "saida", "chegada"):
        assert acao(client_n, auth, c.aut, rid, nome).status_code == 200, nome
    for ct in grav.contatos:
        assert ct is not None and ct["solicitante"] == _p(repo, c.sol, com_email=False)
    for evento in ("requisicao.saida", "requisicao.chegada"):
        d = dict(grav.detalhes)[evento]
        assert d is not None and d["solicitante"] == _p(repo, c.sol, com_email=False)
    assert "n8n: emails indisponiveis evento=requisicao.criada (ErroInterno)" in caplog.text
    assert "n8n: emails indisponiveis evento=requisicao.saida (ErroInterno)" in caplog.text
    assert "@" not in caplog.text


def test_nenhum_email_no_log_no_caminho_feliz(client_n, auth, grav, repo, pessoas, caplog):  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    c = pessoas
    rid = criar(client_n, auth, c.sol, c.veiculo).get_json()["id"]
    for nome in ("autorizar", "saida", "chegada"):
        acao(client_n, auth, c.aut, rid, nome)
    for email in _emails(repo, c.sol, c.aut):
        assert email not in caplog.text


def test_email_nao_sai_nas_respostas_da_api(client_n, auth, grav, repo, pessoas):  # type: ignore[no-untyped-def]
    c = pessoas
    r = criar(client_n, auth, c.sol, c.veiculo)
    rid = r.get_json()["id"]
    respostas = [r, *(acao(client_n, auth, c.aut, rid, n) for n in ("autorizar", "saida"))]
    respostas.append(client_n.get(f"/api/requisicoes/{rid}", headers=auth(c.sol.id)))
    for resp in respostas:
        texto = resp.get_data(as_text=True)
        for email in _emails(repo, c.sol, c.aut):
            assert email not in texto


def test_timeout_da_admin_api_email_none_e_acao_2xx(  # type: ignore[no-untyped-def]
    client_n, auth, auth_admin, grav, repo, pessoas, caplog
):
    caplog.set_level(logging.DEBUG)
    auth_admin.erro_emails_webhook = TimeoutError("emails_para_webhook")
    c = pessoas
    r = criar(client_n, auth, c.sol, c.veiculo)
    assert r.status_code == 201
    rid = r.get_json()["id"]
    for nome in ("autorizar", "saida"):
        assert acao(client_n, auth, c.aut, rid, nome).status_code == 200, nome
    assert all(ct["solicitante"] == _p(repo, c.sol, com_email=False) for ct in grav.contatos)
    assert dict(grav.detalhes)["requisicao.saida"]["solicitante"]["email"] is None
    assert "n8n: emails indisponiveis evento=requisicao.saida (TimeoutError)" in caplog.text
    assert "erro_auth_admin" not in caplog.text and "@" not in caplog.text


def test_n8n_desligado_nao_monta_nem_le_pessoas(  # type: ignore[no-untyped-def]
    client, auth, auth_admin, repo, pessoas, monkeypatch
):
    """Fixture `client` sem notificador (NotificadorNulo): nada de Admin API nem `perfis`."""
    repo.config = replace(repo.ler_configuracoes(), motorista=True)
    leituras: list[set[str]] = []
    original = repo.contatos_perfis

    def contado(ids: set[str]) -> dict[str, Any]:
        leituras.append(set(ids))
        return original(ids)

    monkeypatch.setattr(repo, "contatos_perfis", contado)
    c = pessoas
    r = criar(client, auth, c.sol, c.veiculo)
    assert r.status_code == 201
    rid = r.get_json()["id"]
    for nome in ("autorizar", "saida", "chegada"):
        assert acao(client, auth, c.aut, rid, nome).status_code == 200, nome
    r2 = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    assert acao(client, auth, c.sol, r2, "cancelar").status_code == 200
    assert auth_admin.consultas_email == [] and leituras == []


def test_detalhes_falha_apos_ler_pessoas_nao_rele(  # type: ignore[no-untyped-def]
    client_n, auth, auth_admin, grav, repo, pessoas, monkeypatch
):
    """M2: `obter_veiculo_completo` quebra depois de `_pessoas_webhook`; `contatos` reaproveita."""
    c = pessoas
    rid = criar(client_n, auth, c.sol, c.veiculo).get_json()["id"]
    acao(client_n, auth, c.aut, rid, "autorizar")

    def quebra(veiculo_id: str) -> dict[str, Any]:
        raise RuntimeError("fora")

    monkeypatch.setattr(repo, "obter_veiculo_completo", quebra)
    leituras = []
    original = repo.contatos_perfis
    monkeypatch.setattr(repo, "contatos_perfis", lambda ids: leituras.append(ids) or original(ids))
    antes = len(auth_admin.consultas_email)
    assert acao(client_n, auth, c.aut, rid, "saida").status_code == 200
    assert len(auth_admin.consultas_email) == antes + 1 and len(leituras) == 1
    assert grav.detalhes[-1] == ("requisicao.saida", None)
    assert grav.contatos[-1]["solicitante"] == _p(repo, c.sol)
    assert grav.contatos[-1]["autorizador"] == _p(repo, c.aut)


def test_sem_auth_admin_email_none() -> None:
    from app.services.requisicoes import ServicoRequisicoes

    repo = RepoMemoria()
    sol = repo.novo_perfil("solicitante")
    svc = ServicoRequisicoes(repo)
    ct = svc._contatos_webhook({"usuario_id": sol.id}, "requisicao.criada")
    assert ct is not None and ct["solicitante"]["email"] is None


def test_payload_http_leva_contatos_e_e_assinado() -> None:
    from app.notificacoes.n8n import montar_payload

    ct = {"solicitante": {"id": "u1", "nome": "A", "telefone": TEL}, "autorizador": None}
    p = montar_payload("requisicao.autorizada", {"id": "r1", "status": "autorizada"}, contatos=ct)
    assert p["contatos"] == ct and p["versao"] == 1
    assert "contatos" not in montar_payload("requisicao.criada", {"id": "r1"})


def test_cancelada_leva_motorista_designado_so_se_autorizada(client_n, auth, grav, repo, pessoas):  # type: ignore[no-untyped-def]
    """Contrato do n8n: o motorista só é avisado depois de autorizada. Cancelar uma autorizada
    leva `contatos.motorista` (designado na autorização); cancelar uma solicitada leva None."""
    repo.config = replace(repo.ler_configuracoes(), motorista=True)
    c = pessoas
    r1 = criar(client_n, auth, c.sol, c.veiculo).get_json()["id"]
    acao(client_n, auth, c.aut, r1, "autorizar")  # motorista padrão (único ativo)
    acao(client_n, auth, c.aut, r1, "cancelar")
    r2 = criar(client_n, auth, c.sol, c.veiculo).get_json()["id"]
    acao(client_n, auth, c.sol, r2, "cancelar")
    assert grav.eventos == [
        "requisicao.criada",
        "requisicao.autorizada",
        "requisicao.cancelada",
        "requisicao.criada",
        "requisicao.cancelada",
    ]
    cancelada_autorizada, cancelada_solicitada = grav.contatos[2], grav.contatos[4]
    assert cancelada_autorizada["motorista"] == _p(repo, c.mot)
    assert set(cancelada_autorizada["motorista"]) == {"id", "nome", "telefone", "email"}
    assert cancelada_solicitada["motorista"] is None
    for ct in grav.contatos:
        assert "motorista_previsto" not in ct
