"""Usuários: listar, obter, alterar, ativar/desativar, auto-alteração e último admin."""

from __future__ import annotations

import json
import threading
import uuid

import pytest

from tests.fakes_admin import erro_db


def _cod(r) -> str:  # type: ignore[no-untyped-def]
    return r.get_json()["erro"]["codigo"]


@pytest.fixture
def cen(repo, adm):  # type: ignore[no-untyped-def]
    class C:
        admin = adm
        outro_admin = repo.novo_perfil("admin")
        sol = repo.novo_perfil("solicitante")
        aut = repo.novo_perfil("autorizador")

    return C


# ------------------------------------------------------------------------------ leitura
def test_listar_com_email_total_e_acoes(client, auth, cen):  # type: ignore[no-untyped-def]
    j = client.get("/api/admin/usuarios", headers=auth(cen.admin.id)).get_json()
    assert j["total"] == 4 and len(j["itens"]) == 4
    por_id = {i["id"]: i for i in j["itens"]}
    sol = por_id[cen.sol.id]
    assert sol["email"].endswith("@agu.gov.br") and sol["rotulo_perfil"] == "PU · Solicitante"
    assert sol["acoes_permitidas"] == ["editar", "alterar_role", "desativar", "trocar_foto"]
    assert por_id[cen.admin.id]["acoes_permitidas"] == [
        "editar",
        "trocar_foto",
    ]  # a si mesmo: sem role/desativar
    assert "token_hash" not in json.dumps(j)


def test_listar_filtros_e_paginacao(client, auth, cen, repo):  # type: ignore[no-untyped-def]
    inativo = repo.novo_perfil("solicitante", ativo=False)
    h = auth(cen.admin.id)
    admins = client.get("/api/admin/usuarios?role=admin", headers=h).get_json()
    assert {i["id"] for i in admins["itens"]} == {cen.admin.id, cen.outro_admin.id}
    inativos = client.get("/api/admin/usuarios?ativo=false", headers=h).get_json()
    assert [i["id"] for i in inativos["itens"]] == [inativo.id]
    assert inativos["itens"][0]["acoes_permitidas"] == [
        "editar",
        "alterar_role",
        "ativar",
        "desativar",
    ]
    pag = client.get("/api/admin/usuarios?limite=2&offset=1", headers=h).get_json()
    assert len(pag["itens"]) == 2 and pag["total"] == 5
    assert client.get("/api/admin/usuarios?role=chefe", headers=h).status_code == 400
    assert client.get("/api/admin/usuarios?ativo=talvez", headers=h).status_code == 400
    assert client.get("/api/admin/usuarios?limite=x", headers=h).status_code == 400


def test_obter_e_404_400(client, auth, cen):  # type: ignore[no-untyped-def]
    h = auth(cen.admin.id)
    r = client.get(f"/api/admin/usuarios/{cen.sol.id}", headers=h)
    assert r.status_code == 200 and r.get_json()["id"] == cen.sol.id
    assert client.get(f"/api/admin/usuarios/{uuid.uuid4()}", headers=h).status_code == 404
    assert client.get("/api/admin/usuarios/nao-e-uuid", headers=h).status_code == 400


# ------------------------------------------------------------------------------ alterar
def test_alterar_nome_unidade_e_role_audita_sem_duplicar(client, auth, cen, repo):  # type: ignore[no-untyped-def]
    r = client.patch(
        f"/api/admin/usuarios/{cen.sol.id}",
        headers=auth(cen.admin.id),
        json={"nome": "Nome Novo", "unidade": "CJU", "role": "autorizador"},
    )
    assert r.status_code == 200
    j = r.get_json()
    assert (j["nome"], j["unidade"], j["role"]) == ("Nome Novo", "CJU", "autorizador")
    assert j["acoes_permitidas"] == ["editar", "alterar_role", "desativar", "trocar_foto"]
    # role e unidade: uma linha cada, gravadas pelo "trigger" com o admin como ator
    papel = repo.eventos_de("perfil_alterado")
    assert [(e["de"], e["para"], e["ator_id"]) for e in papel] == [
        ("solicitante/ativo", "autorizador/ativo", cen.admin.id)
    ]
    uni = repo.eventos_de("perfil_unidade_alterada")
    assert [(e["de"], e["para"], e["ator_id"]) for e in uni] == [("PU", "CJU", cen.admin.id)]
    # nome: nenhum trigger cobre -> uma linha acao_administrativa, sem o valor (LGPD)
    adm_ev = repo.eventos_de("acao_administrativa")
    assert len(adm_ev) == 1 and adm_ev[0]["detalhes"] == {"campos": ["nome"]}
    assert adm_ev[0]["ator_id"] == cen.admin.id and "Nome Novo" not in json.dumps(repo.eventos)


def test_alterar_sem_mudanca_nao_audita(client, auth, cen, repo):  # type: ignore[no-untyped-def]
    r = client.patch(
        f"/api/admin/usuarios/{cen.sol.id}",
        headers=auth(cen.admin.id),
        json={"nome": cen.sol.nome, "role": "solicitante"},
    )
    assert r.status_code == 200 and repo.eventos == []


@pytest.mark.parametrize(
    "corpo",
    [
        {},
        {"ativo": False},  # tem rota própria
        {"email": "x@gmail.com"},
        {"id": str(uuid.uuid4())},
        {"nome": ""},
        {"nome": "x" * 121},
        {"nome": 5},
        {"unidade": "XX"},
        {"role": "root"},
        {"role": None},
    ],
)
def test_alterar_validacao(client, auth, cen, repo, corpo):  # type: ignore[no-untyped-def]
    r = client.patch(f"/api/admin/usuarios/{cen.sol.id}", headers=auth(cen.admin.id), json=corpo)
    assert r.status_code == 400 and _cod(r) == "requisicao_invalida"
    assert repo.eventos == []


def test_alterar_corpo_nao_json(client, auth, cen):  # type: ignore[no-untyped-def]
    r = client.patch(f"/api/admin/usuarios/{cen.sol.id}", headers=auth(cen.admin.id), data="x")
    assert r.status_code == 400


def test_admin_nao_rebaixa_a_si_mesmo(client, auth, cen, repo):  # type: ignore[no-untyped-def]
    r = client.patch(
        f"/api/admin/usuarios/{cen.admin.id}",
        headers=auth(cen.admin.id),
        json={"role": "solicitante"},
    )
    assert r.status_code == 403 and _cod(r) == "auto_alteracao_proibida"
    assert repo.perfis[cen.admin.id].role == "admin" and repo.eventos == []


def test_admin_pode_editar_a_propria_unidade(client, auth, cen, repo):  # type: ignore[no-untyped-def]
    r = client.patch(
        f"/api/admin/usuarios/{cen.admin.id}", headers=auth(cen.admin.id), json={"unidade": "UEA"}
    )
    assert r.status_code == 200 and repo.perfis[cen.admin.id].unidade == "UEA"


def test_admin_rebaixa_outro_admin_quando_ha_outro(client, auth, cen, repo):  # type: ignore[no-untyped-def]
    r = client.patch(
        f"/api/admin/usuarios/{cen.outro_admin.id}",
        headers=auth(cen.admin.id),
        json={"role": "autorizador"},
    )
    assert r.status_code == 200 and repo.contar_admins_ativos() == 1


def test_ultimo_admin_pre_checagem_do_servico(client, auth, cen, repo, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(repo, "contar_admins_ativos", lambda: 1)
    h = auth(cen.admin.id)
    alvo = cen.outro_admin.id
    r = client.patch(f"/api/admin/usuarios/{alvo}", headers=h, json={"role": "solicitante"})
    assert r.status_code == 409 and _cod(r) == "ultimo_admin"
    r = client.post(f"/api/admin/usuarios/{alvo}/desativar", headers=h)
    assert r.status_code == 409 and _cod(r) == "ultimo_admin"
    assert repo.perfis[alvo].role == "admin" and repo.perfis[alvo].ativo
    lista = client.get("/api/admin/usuarios", headers=h).get_json()["itens"]
    ultimo = next(i for i in lista if i["id"] == alvo)
    assert ultimo["acoes_permitidas"] == [
        "editar",
        "trocar_foto",
    ]  # a UI nem oferece rebaixar/desativar


@pytest.mark.parametrize(
    ("codigo", "status", "codigo_api"),
    [
        ("AG009", 409, "ultimo_admin"),
        ("AG008", 403, "auto_alteracao_proibida"),
        ("AG002", 403, "sem_permissao"),
    ],
)
def test_erro_do_banco_vira_mensagem_clara(  # type: ignore[no-untyped-def]
    client, auth, cen, repo, monkeypatch, codigo, status, codigo_api
):
    """Corrida: o banco recusa mesmo depois de a pré-checagem passar."""

    def falha(*a, **k):  # type: ignore[no-untyped-def]
        raise erro_db(codigo)

    monkeypatch.setattr(repo, "atualizar_perfil", falha)
    r = client.post(f"/api/admin/usuarios/{cen.sol.id}/desativar", headers=auth(cen.admin.id))
    assert r.status_code == status and _cod(r) == codigo_api
    assert r.get_json()["erro"]["mensagem"]


def test_dois_admins_se_desativando_ao_mesmo_tempo_sobra_um(client, auth, cen, repo):  # type: ignore[no-untyped-def]
    """Concorrência: A desativa B e B desativa A. O banco serializa; nunca zero admins."""
    a, b = cen.admin, cen.outro_admin
    barreira = threading.Barrier(2)
    resultados: list[int] = []

    def agir(ator, alvo):  # type: ignore[no-untyped-def]
        barreira.wait()
        r = client.post(f"/api/admin/usuarios/{alvo.id}/desativar", headers=auth(ator.id))
        resultados.append(r.status_code)

    ts = [threading.Thread(target=agir, args=(a, b)), threading.Thread(target=agir, args=(b, a))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert resultados.count(200) == 1
    assert repo.contar_admins_ativos() == 1


# ------------------------------------------------------------------------------ ativar / desativar
def test_desativar_bloqueia_login_mantem_historico_e_audita(client, auth, auth_admin, cen, repo):  # type: ignore[no-untyped-def]
    v = repo.novo_veiculo()
    rid = client.post(
        "/api/requisicoes",
        headers=auth(cen.sol.id),
        json={
            "nome_solicitante": "Fulano",
            "unidade": "PU",
            "hora_saida": "2026-11-10T08:30:00-03:00",
            "trajeto": "A -> B",
            "veiculo_id": v,
        },
    ).get_json()["id"]
    r = client.post(f"/api/admin/usuarios/{cen.sol.id}/desativar", headers=auth(cen.admin.id))
    assert r.status_code == 200
    j = r.get_json()
    assert j["ativo"] is False and j["acoes_permitidas"] == [
        "editar",
        "alterar_role",
        "ativar",
        "desativar",
    ]
    assert auth_admin.contas[cen.sol.id]["banido"] is True
    assert [(e["de"], e["para"], e["ator_id"]) for e in repo.eventos_de("perfil_alterado")] == [
        ("solicitante/ativo", "solicitante/inativo", cen.admin.id)
    ]
    # conta inativa não usa a API, mas o perfil e o histórico seguem (nunca DELETE)
    r = client.get("/api/eu", headers=auth(cen.sol.id))
    assert r.status_code == 403 and _cod(r) == "conta_inativa"
    assert cen.sol.id in repo.perfis and rid in repo.requisicoes
    assert (
        client.delete(f"/api/admin/usuarios/{cen.sol.id}", headers=auth(cen.admin.id)).status_code
        == 405
    )


def test_desativar_repetido_nao_audita_de_novo(client, auth, cen, repo):  # type: ignore[no-untyped-def]
    h = auth(cen.admin.id)
    client.post(f"/api/admin/usuarios/{cen.sol.id}/desativar", headers=h)
    assert client.post(f"/api/admin/usuarios/{cen.sol.id}/desativar", headers=h).status_code == 200
    assert len(repo.eventos_de("perfil_alterado")) == 1


def test_admin_nao_desativa_a_si_mesmo(client, auth, cen, repo):  # type: ignore[no-untyped-def]
    r = client.post(f"/api/admin/usuarios/{cen.admin.id}/desativar", headers=auth(cen.admin.id))
    assert r.status_code == 403 and _cod(r) == "auto_alteracao_proibida"
    assert repo.perfis[cen.admin.id].ativo and repo.eventos == []


def test_reativar_desbloqueia_e_audita(client, auth, auth_admin, cen, repo):  # type: ignore[no-untyped-def]
    h = auth(cen.admin.id)
    client.post(f"/api/admin/usuarios/{cen.sol.id}/desativar", headers=h)
    r = client.post(f"/api/admin/usuarios/{cen.sol.id}/ativar", headers=h)
    assert r.status_code == 200 and r.get_json()["ativo"] is True
    assert auth_admin.contas[cen.sol.id]["banido"] is False
    assert [e["para"] for e in repo.eventos_de("perfil_alterado")] == [
        "solicitante/inativo",
        "solicitante/ativo",
    ]
    assert client.get("/api/eu", headers=auth(cen.sol.id)).status_code == 200


def test_falha_no_auth_ao_desativar_deixa_perfil_inativo_e_repetir_refaz(  # type: ignore[no-untyped-def]
    client, auth, auth_admin, cen, repo
):
    h = auth(cen.admin.id)
    auth_admin.falhar_bloqueio = True
    r = client.post(f"/api/admin/usuarios/{cen.sol.id}/desativar", headers=h)
    assert r.status_code == 503 and repo.perfis[cen.sol.id].ativo is False  # lado seguro
    auth_admin.falhar_bloqueio = False
    assert client.post(f"/api/admin/usuarios/{cen.sol.id}/desativar", headers=h).status_code == 200
    assert auth_admin.contas[cen.sol.id]["banido"] is True


def test_acoes_em_usuario_inexistente(client, auth, cen):  # type: ignore[no-untyped-def]
    h = auth(cen.admin.id)
    for acao in ("ativar", "desativar"):
        r = client.post(f"/api/admin/usuarios/{uuid.uuid4()}/{acao}", headers=h)
        assert r.status_code == 404


# ------------------------------------------------------------------------------ B4 / B5 / B6
def test_inativo_expoe_desativar_e_repetir_refaz_o_ban(client, auth, auth_admin, cen):  # type: ignore[no-untyped-def]
    h = auth(cen.admin.id)
    auth_admin.falhar_bloqueio = True
    assert client.post(f"/api/admin/usuarios/{cen.sol.id}/desativar", headers=h).status_code == 503
    item = client.get(f"/api/admin/usuarios/{cen.sol.id}", headers=h).get_json()
    assert item["ativo"] is False and "desativar" in item["acoes_permitidas"]
    auth_admin.falhar_bloqueio = False
    assert client.post(f"/api/admin/usuarios/{cen.sol.id}/desativar", headers=h).status_code == 200
    assert auth_admin.contas[cen.sol.id]["banido"] is True


def test_listagem_consulta_emails_so_da_pagina(client, auth, cen, auth_admin, repo):  # type: ignore[no-untyped-def]
    for _ in range(5):
        repo.novo_perfil("solicitante")
    client.get("/api/admin/usuarios?limite=2", headers=auth(cen.admin.id))
    assert len(auth_admin.consultas_email[-1]) == 2 and auth_admin.varreduras == 0


def test_busca_q_por_nome_e_email_com_filtros_e_total(client, auth, cen, repo, auth_admin):  # type: ignore[no-untyped-def]
    h = auth(cen.admin.id)
    alvo = repo.novo_perfil("solicitante")
    repo.perfis[alvo.id] = type(alvo)(alvo.id, "Maria Silva", "PF", "solicitante", True)
    auth_admin.contas[alvo.id]["email"] = "joana.souza@gmail.com"
    por_nome = client.get("/api/admin/usuarios?q=maria", headers=h).get_json()
    assert [i["id"] for i in por_nome["itens"]] == [alvo.id] and por_nome["total"] == 1
    por_email = client.get("/api/admin/usuarios?q=JOANA.sou", headers=h).get_json()
    assert [i["id"] for i in por_email["itens"]] == [alvo.id] and por_email["total"] == 1
    # combina com role/ativo e paginacao: total coerente, itens limitados
    assert client.get("/api/admin/usuarios?q=maria&role=admin", headers=h).get_json()["total"] == 0
    assert client.get("/api/admin/usuarios?q=maria&ativo=false", headers=h).get_json()["total"] == 0
    todos = client.get("/api/admin/usuarios?q=%40agu.gov.br&limite=1", headers=h).get_json()
    assert todos["total"] == 4 and len(todos["itens"]) == 1
    assert client.get("/api/admin/usuarios?q=%20%20", headers=h).get_json()["total"] == 5


@pytest.mark.parametrize("q", ["a" * 81, "x,id.eq.1", "a)b", 'a"b', "a*b", "a%b", "a\\b", "a\x00b"])
def test_busca_q_invalida(client, auth, cen, q):  # type: ignore[no-untyped-def]
    r = client.get("/api/admin/usuarios", query_string={"q": q}, headers=auth(cen.admin.id))
    assert r.status_code == 400 and _cod(r) == "requisicao_invalida"


def test_busca_q_nao_vai_para_o_log(client, auth, cen, caplog):  # type: ignore[no-untyped-def]
    caplog.set_level("DEBUG")
    client.get("/api/admin/usuarios?q=segredo-termo", headers=auth(cen.admin.id))
    assert "segredo-termo" not in caplog.text


def test_busca_q_e_filtro_postgrest_parametrizado():  # type: ignore[no-untyped-def]
    from unittest.mock import MagicMock

    from app.repositorios.supabase_repo import SupabaseRepositorio

    q = MagicMock()
    SupabaseRepositorio._filtrar_perfis(q, None, None, "maria", frozenset({"b", "a"}))
    q.or_.assert_called_once_with("nome.ilike.*maria*,id.in.(a,b)")
    q2 = MagicMock()
    SupabaseRepositorio._filtrar_perfis(q2, None, None, None, frozenset())
    q2.or_.assert_not_called()


def test_colecoes_devolvem_acoes_permitidas(client, auth, cen):  # type: ignore[no-untyped-def]
    h = auth(cen.admin.id)
    assert client.get("/api/admin/usuarios", headers=h).get_json()["acoes_permitidas"] == ["criar"]
    assert client.get("/api/admin/veiculos", headers=h).get_json()["acoes_permitidas"] == ["criar"]


# ------------------------------------------------------------------------------ B4: bordas de `q`
@pytest.mark.parametrize(
    ("q", "esperado"),
    [
        ("joao.silva", "joao.silva"),
        ("a:b", "a:b"),
        ("fulano&sicrano", "fulano&sicrano"),
        ("n#1", "n#1"),
        ("a+b", "a+b"),
        ("  maria   da   silva ", "maria da silva"),  # espaços internos colapsados
        ("José Ção", "José Ção"),
        ("日本語", "日本語"),
    ],
)
def test_validar_busca_aceita_caracteres_de_borda(q, esperado):  # type: ignore[no-untyped-def]
    from app.services.validacao_admin import validar_busca

    assert validar_busca(q) == esperado


def test_busca_com_caracteres_de_borda_chega_ao_filtro_e_ao_http_intactos():  # type: ignore[no-untyped-def]
    from unittest.mock import MagicMock

    import httpx

    from app.repositorios.supabase_repo import SupabaseRepositorio

    for termo in ("a.b", "a:b", "a&b", "a#b", "a+b", "a b", "joão"):
        q = MagicMock()
        SupabaseRepositorio._filtrar_perfis(q, None, None, termo, frozenset())
        (filtro,) = q.or_.call_args.args
        assert filtro == f"nome.ilike.*{termo}*"
        # o cliente HTTP codifica a query: &, # e + não quebram nem viram outro parâmetro
        req = httpx.Client().build_request(
            "GET", "https://x/rest/v1/perfis", params={"or": f"({filtro})"}
        )
        assert httpx.QueryParams(req.url.query.decode()).multi_items() == [("or", f"({filtro})")]
        assert len(req.url.params) == 1


def test_busca_unicode_e_pontuacao_por_nome_e_email(client, auth, cen, repo, auth_admin):  # type: ignore[no-untyped-def]
    from app.modelos import Perfil

    alvo = repo.novo_perfil("solicitante")
    repo.perfis[alvo.id] = Perfil(alvo.id, "José Ção", "PF", "solicitante", True)
    auth_admin.contas[alvo.id]["email"] = "jose+ti.sec@gmail.com"
    h = auth(cen.admin.id)
    for q, ok in [
        ("josé", True),
        ("ção", True),
        ("+ti.", True),
        ("ti.sec@", True),
        ("jose&", False),
    ]:
        j = client.get("/api/admin/usuarios", query_string={"q": q}, headers=h).get_json()
        assert (alvo.id in [i["id"] for i in j["itens"]]) is ok, q


# ------------------------------------------------------------------------------ M4
def test_busca_usa_cache_do_indice_de_emails(client, auth, cen, auth_admin, monkeypatch):  # type: ignore[no-untyped-def]
    from app.services import admin_usuarios as mod

    relogio = [1000.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: relogio[0])
    h = auth(cen.admin.id)
    for _ in range(3):
        client.get("/api/admin/usuarios?q=agu", headers=h)
    assert auth_admin.varreduras == 1  # as demais saíram do cache
    relogio[0] += mod.TTL_INDICE_S - 1
    client.get("/api/admin/usuarios?q=agu", headers=h)
    assert auth_admin.varreduras == 1
    relogio[0] += 2  # expirou
    client.get("/api/admin/usuarios?q=agu", headers=h)
    assert auth_admin.varreduras == 2
    client.get("/api/admin/usuarios", headers=h)  # sem q: nunca varre
    assert auth_admin.varreduras == 2


def test_busca_parcial_falso_por_padrao_e_true_se_truncou(client, auth, cen, auth_admin):  # type: ignore[no-untyped-def]
    h = auth(cen.admin.id)
    assert client.get("/api/admin/usuarios", headers=h).get_json()["busca_parcial"] is False
    j = client.get("/api/admin/usuarios?q=agu", headers=h).get_json()
    assert j["busca_parcial"] is False and j["total"] == 4
    from app.services.admin_usuarios import INDICE_EMAILS

    INDICE_EMAILS.reiniciar()
    auth_admin.indice_truncado = True
    j = client.get("/api/admin/usuarios?q=agu", headers=h).get_json()
    assert j["busca_parcial"] is True and "itens" in j and "total" in j


def test_busca_parcial_quando_passa_de_150_ids(client, auth, cen, repo, auth_admin):  # type: ignore[no-untyped-def]
    from app.services.admin_usuarios import MAX_IDS_BUSCA

    for _ in range(MAX_IDS_BUSCA + 5):
        repo.novo_perfil("solicitante")
    h = auth(cen.admin.id)
    j = client.get("/api/admin/usuarios?q=%40agu.gov.br&limite=100", headers=h).get_json()
    assert j["busca_parcial"] is True and j["total"] == MAX_IDS_BUSCA
    exato = client.get("/api/admin/usuarios?q=pessoa1%40", headers=h).get_json()
    assert exato["busca_parcial"] is False


def test_busca_tem_limite_proprio_e_listagem_sem_q_nao(client, auth, cen):  # type: ignore[no-untyped-def]
    h = auth(cen.admin.id)
    codigos = [client.get("/api/admin/usuarios?q=a", headers=h).status_code for _ in range(31)]
    assert codigos[:30] == [200] * 30 and codigos[30] == 429
    r = client.get("/api/admin/usuarios?q=a", headers=h)
    assert r.get_json()["erro"]["codigo"] == "limite_excedido"
    assert client.get("/api/admin/usuarios", headers=h).status_code == 200  # sem q: só o padrão


def test_cache_nao_loga_email_nem_termo(client, auth, cen, caplog):  # type: ignore[no-untyped-def]
    caplog.set_level("DEBUG")
    client.get("/api/admin/usuarios?q=pessoa", headers=auth(cen.admin.id))
    assert "@agu.gov.br" not in caplog.text and "pessoa" not in caplog.text
