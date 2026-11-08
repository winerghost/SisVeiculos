"""Notificações: rotas (401, dono, IDOR, paginação), textos sem PII, destino e flag."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.services.notificacoes import (
    DESTINO_REQUISICAO,
    DESTINOS_FIXOS,
    TEXTOS,
    TIPOS_COM_REQUISICAO,
    apresentar,
    montar_destino,
    montar_texto,
)
from tests.fakes import RepoMemoria

TIPOS = sorted(TEXTOS)
PII = ("João da Silva", "ABC1D23", "Rua das Flores", "@agu.gov.br", "Pessoa ")


def _nova(repo: RepoMemoria, dono: Any, tipo: str, **kw: Any) -> dict[str, Any]:
    if tipo in TIPOS_COM_REQUISICAO:
        kw.setdefault("requisicao_id", str(uuid.uuid4()))
    if tipo == "sistema_falha_envio":
        kw.setdefault("codigo", "smtp_timeout")
    return repo.nova_notificacao(dono.id, tipo, **kw)


def get(client: Any, auth: Any, quem: Any, url: str) -> Any:
    return client.get(url, headers=auth(quem.id))


# -- autenticação --------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("metodo", "url"),
    [
        ("get", "/api/notificacoes"),
        ("get", "/api/notificacoes/contagem"),
        ("post", f"/api/notificacoes/{uuid.uuid4()}/lida"),
        ("post", "/api/notificacoes/lidas"),
    ],
)
def test_sem_token_401(client, metodo, url):  # type: ignore[no-untyped-def]
    r = getattr(client, metodo)(url)
    assert r.status_code == 401
    assert r.get_json()["erro"]["codigo"]


# -- dono e IDOR ---------------------------------------------------------------------------------
def test_cada_perfil_so_ve_as_proprias(client, auth, repo, c):  # type: ignore[no-untyped-def]
    for quem in (c.sol, c.outro, c.aut, c.adm):
        _nova(repo, quem, "requisicao_nova")
    mina = _nova(repo, c.sol, "requisicao_chegada")
    for quem in (c.sol, c.outro, c.aut, c.adm):
        j = get(client, auth, quem, "/api/notificacoes").get_json()
        ids = {i["id"] for i in j["itens"]}
        proprias = {n["id"] for n in repo.notificacoes if n["usuario_id"] == quem.id}
        assert ids == proprias
        assert j["total"] == len(proprias) == j["nao_lidas"]
    assert mina["id"] not in {
        i["id"] for i in get(client, auth, c.outro, "/api/notificacoes").get_json()["itens"]
    }


def test_marcar_lida_de_outro_ou_inexistente_da_o_mesmo_404(client, auth, repo, c):  # type: ignore[no-untyped-def]
    alheia = _nova(repo, c.sol, "requisicao_chegada")
    r1 = client.post(f"/api/notificacoes/{alheia['id']}/lida", headers=auth(c.outro.id))
    r2 = client.post(f"/api/notificacoes/{uuid.uuid4()}/lida", headers=auth(c.outro.id))
    r3 = client.post("/api/notificacoes/nao-e-uuid/lida", headers=auth(c.outro.id))
    assert r1.status_code == r2.status_code == r3.status_code == 404
    assert r1.get_json() == r2.get_json() == r3.get_json()
    assert r1.get_json()["erro"]["mensagem"] == "Notificação não encontrada."
    assert alheia["lida_em"] is None  # a alheia não foi tocada


def test_marcar_lida_e_idempotente(client, auth, repo, c):  # type: ignore[no-untyped-def]
    n = _nova(repo, c.sol, "requisicao_autorizada")
    url = f"/api/notificacoes/{n['id']}/lida"
    a = client.post(url, headers=auth(c.sol.id))
    b = client.post(url, headers=auth(c.sol.id))
    assert a.status_code == b.status_code == 200
    assert a.get_json()["id"] == n["id"]
    assert a.get_json()["lida_em"] == b.get_json()["lida_em"] is not None
    assert get(client, auth, c.sol, "/api/notificacoes/contagem").get_json() == {"nao_lidas": 0}


def test_marcar_todas_so_as_proprias(client, auth, repo, c):  # type: ignore[no-untyped-def]
    for _ in range(3):
        _nova(repo, c.sol, "requisicao_saida")
    alheia = _nova(repo, c.outro, "requisicao_saida")
    r = client.post("/api/notificacoes/lidas", headers=auth(c.sol.id))
    assert r.status_code == 200 and r.get_json() == {"marcadas": 3}
    assert alheia["lida_em"] is None
    assert client.post("/api/notificacoes/lidas", headers=auth(c.sol.id)).get_json() == {
        "marcadas": 0
    }


def test_dono_nunca_vem_do_cliente(client, auth, repo, c):  # type: ignore[no-untyped-def]
    _nova(repo, c.outro, "requisicao_nova")
    j = client.get(
        f"/api/notificacoes?usuario_id={c.outro.id}&usuario={c.outro.id}", headers=auth(c.sol.id)
    ).get_json()
    assert j["itens"] == [] and j["total"] == 0


# -- contrato, paginação e filtros ---------------------------------------------------------------
def test_contrato_da_listagem(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = str(uuid.uuid4())
    n = _nova(repo, c.sol, "requisicao_chegada", requisicao_id=rid, critica=True)
    r = get(client, auth, c.sol, "/api/notificacoes")
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "no-store"
    j = r.get_json()
    assert set(j) == {"itens", "total", "nao_lidas"}
    assert j["itens"][0] == {
        "id": n["id"],
        "tipo": "requisicao_chegada",
        "titulo": "Chegada registrada",
        "texto": (
            "A chegada do veículo da sua requisição foi registrada. Toque para ver o histórico."
        ),
        "critica": True,
        "criada_em": j["itens"][0]["criada_em"],
        "lida_em": None,
        "destino": f"/requisicoes/detalhe?id={rid}",
    }


def test_paginacao_ordem_e_filtro(client, auth, repo, c):  # type: ignore[no-untyped-def]
    for i in range(5):
        _nova(repo, c.sol, "requisicao_saida", minutos_atras=i, lida=i == 0)
    j = get(client, auth, c.sol, "/api/notificacoes?limite=2&offset=1").get_json()
    assert len(j["itens"]) == 2 and j["total"] == 5 and j["nao_lidas"] == 4
    datas = [i["criada_em"] for i in j["itens"]]
    assert datas == sorted(datas, reverse=True)
    j = get(client, auth, c.sol, "/api/notificacoes?somente_nao_lidas=true").get_json()
    assert j["total"] == 4 and all(i["lida_em"] is None for i in j["itens"])
    j = get(client, auth, c.sol, "/api/notificacoes?somente_nao_lidas=false").get_json()
    assert j["total"] == 5


@pytest.mark.parametrize(
    "qs",
    [
        "limite=0",
        "limite=101",
        "limite=-1",
        "limite=abc",
        "limite=1.5",
        "offset=-1",
        "offset=100001",
        "offset=x",
        "somente_nao_lidas=talvez",
        "somente_nao_lidas=1",
        "limite=%D9%A1",  # dígito árabe-índico: só ASCII vale
    ],
)
def test_parametros_invalidos_400(client, auth, c, qs):  # type: ignore[no-untyped-def]
    r = get(client, auth, c.sol, f"/api/notificacoes?{qs}")
    assert r.status_code == 400
    assert set(r.get_json()["erro"]) == {"codigo", "mensagem"}


def test_limite_maximo_aceito(client, auth, c):  # type: ignore[no-untyped-def]
    assert get(client, auth, c.sol, "/api/notificacoes?limite=100").status_code == 200


def test_contagem(client, auth, repo, c):  # type: ignore[no-untyped-def]
    _nova(repo, c.sol, "requisicao_saida")
    _nova(repo, c.sol, "requisicao_saida", lida=True)
    _nova(repo, c.outro, "requisicao_saida")
    r = get(client, auth, c.sol, "/api/notificacoes/contagem")
    assert r.get_json() == {"nao_lidas": 1} and r.headers["Cache-Control"] == "no-store"


def test_rate_limit_de_leitura_por_usuario(app, client, auth, c):  # type: ignore[no-untyped-def]
    app.extensions  # noqa: B018  (a app já usa o limiter global)
    codigos = [
        get(client, auth, c.sol, "/api/notificacoes/contagem").status_code for _ in range(121)
    ]
    assert codigos[:120] == [200] * 120 and codigos[120] == 429
    # outro usuário tem a própria cota
    assert get(client, auth, c.outro, "/api/notificacoes/contagem").status_code == 200


# -- textos sem PII e destino --------------------------------------------------------------------
@pytest.mark.parametrize("tipo", TIPOS)
def test_texto_sem_pii_em_todos_os_tipos(tipo):  # type: ignore[no-untyped-def]
    for codigo in (None, "saida", "chegada", "papel_alterado", "desativado", "mfa", "smtp_timeout"):
        item = {
            "id": str(uuid.uuid4()),
            "tipo": tipo,
            "codigo": codigo,
            "critica": False,
            "criada_em": "2026-11-08T12:00:00+00:00",
            "lida_em": None,
            "requisicao_id": str(uuid.uuid4()) if tipo in TIPOS_COM_REQUISICAO else None,
        }
        v = apresentar(item)
        texto = f"{v['titulo']} {v['texto']}"
        assert v["titulo"] and v["texto"] and len(v["texto"]) < 160
        assert "{" not in texto and "None" not in texto
        assert not any(p in texto for p in PII)
        assert "@" not in texto


def test_textos_fixos_do_enunciado():
    assert montar_texto("requisicao_nova", None)[0] == "Nova solicitação aguardando autorização"
    assert montar_texto("checklist_avaria", "saida")[0] == "Checklist com avaria registrado"
    assert "na saída" in montar_texto("checklist_avaria", "saida")[1]
    assert "alterado" in montar_texto("perfil_alterado_admin", "papel_alterado")[1]
    assert "desativado" in montar_texto("perfil_alterado_admin", "desativado")[1]
    assert "Quilometragem" in montar_texto("configuracao_alterada", "quilometragem")[1]
    assert (
        "E-mail para notificações críticas"
        in montar_texto("configuracao_alterada", "notificacao_email_criticos")[1]
    )
    # código de configuração desconhecido não é ecoado
    assert "zzz" not in montar_texto("configuracao_alterada", "zzz")[1]
    assert "demorou" in montar_texto("sistema_falha_envio", "smtp_timeout")[1]
    assert montar_texto("sistema_falha_envio", "n8n_falha")[1].startswith("A integração")
    assert "<script>" not in montar_texto("sistema_falha_envio", "<script>")[1]


def test_destino_so_da_allowlist():
    rid = str(uuid.uuid4())
    for tipo in TIPOS_COM_REQUISICAO:
        assert montar_destino(tipo, rid) == f"{DESTINO_REQUISICAO}{rid}"
        assert montar_destino(tipo, None) is None
        assert montar_destino(tipo, "nao-uuid") is None
        assert montar_destino(tipo, f"{rid}&x=//evil.com") is None
        assert montar_destino(tipo, 123) is None
    assert montar_destino("convite_aceito", None) == "/admin/convites"
    assert montar_destino("perfil_alterado_admin", None) == "/admin/usuarios"
    assert montar_destino("configuracao_alterada", None) == "/admin/configuracoes"
    assert montar_destino("sistema_falha_envio", None) == "/admin/configuracoes"
    permitidos = {"/admin/convites", "/admin/usuarios", "/admin/configuracoes"}
    assert set(DESTINOS_FIXOS.values()) <= permitidos
    assert montar_destino("tipo_inexistente", rid) is None


def test_destino_normaliza_uuid():
    rid = uuid.uuid4()
    assert montar_destino("requisicao_nova", str(rid).upper()) == f"{DESTINO_REQUISICAO}{rid}"


def test_tipo_desconhecido_e_seguro(client, auth, repo, c):  # type: ignore[no-untyped-def]
    # Um tipo novo no banco (versão futura) não derruba a listagem.
    n = repo.nova_notificacao(c.sol.id, "tipo_do_futuro", codigo="x", critica=True)
    j = get(client, auth, c.sol, "/api/notificacoes").get_json()
    assert j["itens"][0] == {
        "id": n["id"],
        "tipo": "tipo_do_futuro",
        "titulo": "Nova notificação",
        "texto": "Você tem uma nova notificação.",
        "critica": True,
        "criada_em": j["itens"][0]["criada_em"],
        "lida_em": None,
        "destino": None,
    }


def test_resposta_nao_vaza_colunas_internas(client, auth, repo, c):  # type: ignore[no-untyped-def]
    _nova(repo, c.sol, "requisicao_nova", email_status="pendente")
    item = get(client, auth, c.sol, "/api/notificacoes").get_json()["itens"][0]
    assert set(item) == {
        "id",
        "tipo",
        "titulo",
        "texto",
        "critica",
        "criada_em",
        "lida_em",
        "destino",
    }


# -- flag de e-mail dos críticos -----------------------------------------------------------------
def test_flag_em_eu_e_em_configuracoes(client, auth, repo, c):  # type: ignore[no-untyped-def]
    assert (
        get(client, auth, c.sol, "/api/eu").get_json()["recursos"]["notificacao_email_criticos"]
        is False
    )
    r = client.put(
        "/api/admin/configuracoes",
        headers=auth(c.adm.id),
        json={"notificacao_email_criticos": True},
    )
    assert r.status_code == 200
    item = {i["chave"]: i for i in r.get_json()["itens"]}["notificacao_email_criticos"]
    assert item["rotulo"] == "E-mail para notificações críticas"
    assert item["habilitado"] is True and item["acoes_permitidas"] == ["desligar"]
    assert repo.config.notificacao_email_criticos is True
    assert [e["detalhes"]["recurso"] for e in repo.eventos_de("configuracao_alterada")] == [
        "notificacao_email_criticos"
    ]
    assert (
        get(client, auth, c.sol, "/api/eu").get_json()["recursos"]["notificacao_email_criticos"]
        is True
    )


def test_flag_so_admin_altera(client, auth, c):  # type: ignore[no-untyped-def]
    r = client.put(
        "/api/admin/configuracoes",
        headers=auth(c.aut.id),
        json={"notificacao_email_criticos": True},
    )
    assert r.status_code == 403


# -- repositório real (PostgREST simulado) ---------------------------------------------------------
def _repo_real(dados: Any) -> Any:
    from unittest.mock import MagicMock

    from app.repositorios.supabase_repo import SupabaseRepositorio

    r = SupabaseRepositorio.__new__(SupabaseRepositorio)
    r._c = MagicMock()
    r._c.rpc.return_value.execute.return_value.data = dados
    return r


def test_repo_real_chama_as_rpcs_com_parametros_do_contrato():
    uid, nid = str(uuid.uuid4()), str(uuid.uuid4())
    r = _repo_real({"total": 0, "nao_lidas": 0, "limite": 5, "offset": 2, "itens": []})
    r.listar_notificacoes(uid, limite=5, offset=2, somente_nao_lidas=True)
    r._c.rpc.assert_called_with(
        "listar_notificacoes",
        {"p_usuario": uid, "p_limite": 5, "p_offset": 2, "p_somente_nao_lidas": True},
    )
    assert _repo_real({"nao_lidas": 3}).contar_notificacoes_nao_lidas(uid) == 3
    assert _repo_real({"marcadas": 2}).marcar_todas_lidas(uid) == 2
    assert _repo_real({"criadas": 1}).registrar_notificacao_sistema("sistema_falha_envio", "x") == 1
    r = _repo_real({"id": nid, "lida_em": "2026-11-08T00:00:00+00:00"})
    assert r.marcar_notificacao_lida(uid, nid)["id"] == nid
    r._c.rpc.assert_called_with("marcar_notificacao_lida", {"p_usuario": uid, "p_id": nid})
    r = _repo_real([{"id": nid, "usuario_id": uid, "tipo": "requisicao_nova"}])
    assert r.reservar_emails_notificacao(5)[0]["id"] == nid
    r.marcar_email_notificacao(nid, "falhou", "smtp_erro")
    r._c.rpc.assert_called_with(
        "marcar_email_notificacao",
        {"p_id": nid, "p_status": "falhou", "p_erro_codigo": "smtp_erro"},
    )


def test_repo_real_resposta_invalida_vira_erro_interno():
    from app.errors import ErroInterno

    with pytest.raises(ErroInterno):
        _repo_real(None).contar_notificacoes_nao_lidas(str(uuid.uuid4()))
    with pytest.raises(ErroInterno):
        _repo_real({"itens": "x"}).listar_notificacoes(
            str(uuid.uuid4()), limite=1, offset=0, somente_nao_lidas=False
        )


def test_traducao_ag029_ag030():
    from app.errors import NaoEncontrado, RequisicaoInvalida
    from tests.fakes_admin import erro_db

    e29, e30 = erro_db("AG029", "qualquer texto interno"), erro_db("AG030", "outro texto")
    assert isinstance(e29, NaoEncontrado) and e29.mensagem == "Notificação não encontrada."
    assert isinstance(e30, RequisicaoInvalida) and e30.codigo == "notificacao_invalida"
    assert "interno" not in e29.mensagem and "outro" not in e30.mensagem
