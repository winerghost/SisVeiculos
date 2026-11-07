"""Convites: criar/reenviar/revogar (admin) e aceitar (uso único, validade, sem escalonamento)."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Config
from tests.conftest import URL_ACEITE

EMAIL = "novo.servidor@agu.gov.br"


def _cod(r) -> str:  # type: ignore[no-untyped-def]
    return r.get_json()["erro"]["codigo"]


def convidar(client, auth, adm, **extra):  # type: ignore[no-untyped-def]
    corpo = {"email": EMAIL, "nome": "Novo Servidor", "unidade": "PF", **extra}
    return client.post("/api/admin/usuarios", headers=auth(adm.id), json=corpo)


def envelhecer(repo, convite_id, segundos=300):  # type: ignore[no-untyped-def]
    """Recua `criado_em` para o convite sair do cooldown de reenvio."""
    repo.convites[convite_id]["criado_em"] = (
        datetime.now(UTC) - timedelta(seconds=segundos)
    ).isoformat()


def reenviar(client, auth, adm, convite_id):  # type: ignore[no-untyped-def]
    return client.post(f"/api/admin/convites/{convite_id}/reenviar", headers=auth(adm.id))


def confirmar_conta(auth_admin, email=EMAIL) -> str:  # type: ignore[no-untyped-def]
    """O convidado clicou no e-mail do Supabase e definiu a senha: conta confirmada."""
    uid = auth_admin.id_de(email)
    auth_admin.contas[uid]["confirmado"] = True
    return uid


def aceitar(client, auth, uid, token, **extra):  # type: ignore[no-untyped-def]
    return client.post("/api/convites/aceitar", headers=auth(uid), json={"token": token, **extra})


# ------------------------------------------------------------------------------ criar
def test_convidar_cria_convite_de_uso_unico_e_envia_email(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    r = convidar(client, auth, adm, role="autorizador", validade_dias=3)
    assert r.status_code == 201
    c = r.get_json()["convite"]
    assert c["email"] == EMAIL and c["role"] == "autorizador" and c["unidade"] == "PF"
    assert c["estado"] == "aberto" and c["acoes_permitidas"] == ["reenviar", "revogar"]
    # token só no link do e-mail; no banco só o SHA-256; nunca na resposta
    token = auth_admin.ultimo_token()
    assert auth_admin.envios[-1][1].startswith(URL_ACEITE + "?convite=")
    guardado = repo.convites[c["id"]]
    assert guardado["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in json.dumps(c) and "token_hash" not in json.dumps(c)
    expira = datetime.fromisoformat(c["expira_em"])
    assert timedelta(days=2, hours=23) < expira - datetime.now(UTC) <= timedelta(days=3)
    # auditoria com o admin como autor (gravada pelo trigger; sem e-mail nem hash)
    ev = repo.eventos_de("convite_criado")
    assert len(ev) == 1 and ev[0]["ator_id"] == adm.id
    assert EMAIL not in json.dumps(repo.eventos) and token not in json.dumps(repo.eventos)
    # nome vai para o cadastro do Auth (para o aceite usar)
    assert auth_admin.contas[auth_admin.id_de(EMAIL)]["nome"] == "Novo Servidor"


@pytest.mark.parametrize(
    "email",
    [
        "alguem@outlook.com",
        "alguem@agu.gov.br.evil.com",
        "alguem@evilagu.gov.br",
        "alguem@@gmail.com",
        "ALGUEM @gmail.com",
        "",
        None,
        123,
        "a" * 250 + "@gmail.com",
    ],
)
def test_convidar_email_invalido(client, auth, auth_admin, adm, repo, email):  # type: ignore[no-untyped-def]
    r = convidar(client, auth, adm, email=email)
    assert r.status_code == 400 and _cod(r) == "requisicao_invalida"
    assert not repo.convites and not auth_admin.envios


def test_convidar_aceita_gmail_e_subdominio_em_maiusculas(client, auth, adm):  # type: ignore[no-untyped-def]
    assert convidar(client, auth, adm, email="Fulano@Gmail.com").status_code == 201
    assert convidar(client, auth, adm, email="x@pu.agu.gov.br").status_code == 201


@pytest.mark.parametrize(
    "extra",
    [
        {"unidade": "XX"},
        {"role": "chefe"},
        {"nome": ""},
        {"validade_dias": 0},
        {"validade_dias": 31},
        {"validade_dias": "7"},
        {"validade_dias": True},
        {"ativo": True},
        {"usado_em": "2026-01-01"},
    ],
)
def test_convidar_validacao(client, auth, adm, extra):  # type: ignore[no-untyped-def]
    assert convidar(client, auth, adm, **extra).status_code == 400


def test_convite_aberto_duplicado(client, auth, adm):  # type: ignore[no-untyped-def]
    assert convidar(client, auth, adm).status_code == 201
    r = convidar(client, auth, adm)
    assert r.status_code == 409 and _cod(r) == "convite_aberto_existente"
    assert "Reenvie ou revogue" in r.get_json()["erro"]["mensagem"]


def test_conta_existente_com_perfil_revoga_o_convite_criado(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    existente = repo.novo_perfil("solicitante")
    auth_admin.contas[existente.id]["email"] = EMAIL
    r = convidar(client, auth, adm)
    assert r.status_code == 409 and _cod(r) == "conta_existente"
    (c,) = repo.convites.values()
    assert c["revogado_em"] is not None  # nao fica convite orfao bloqueando o e-mail
    assert not auth_admin.envios
    del repo.perfis[existente.id], auth_admin.contas[existente.id]
    assert convidar(client, auth, adm).status_code == 201


def test_conta_sem_perfil_recebe_acesso(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    uid = auth_admin.nova_conta(EMAIL, "Novo Servidor")  # confirmada, sem perfil
    r = convidar(client, auth, adm)
    assert r.status_code == 201 and r.get_json()["convite"]["estado"] == "aberto"
    assert auth_admin.envios[-1][1].startswith(URL_ACEITE + "?convite=")
    assert len(repo.convites) == 1 and uid not in repo.perfis
    # o link reenviado destrava a conta: aceite cria o perfil
    assert aceitar(client, auth, uid, auth_admin.ultimo_token()).status_code == 200
    assert uid in repo.perfis


def test_conta_sem_perfil_falha_no_acesso_revoga(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    auth_admin.nova_conta(EMAIL, "Novo Servidor")
    auth_admin.falhar_reenvio_acesso = True
    assert convidar(client, auth, adm).status_code == 503
    (c,) = repo.convites.values()
    assert c["revogado_em"] is not None
    auth_admin.falhar_reenvio_acesso = False
    assert convidar(client, auth, adm).status_code == 201


def test_falha_do_auth_revoga_o_convite(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    auth_admin.falhar_convite = True
    assert convidar(client, auth, adm).status_code == 503
    (c,) = repo.convites.values()
    assert c["revogado_em"] is not None
    auth_admin.falhar_convite = False
    assert convidar(client, auth, adm).status_code == 201


def test_convites_nao_configurados(repo, auth_admin, auth, adm):  # type: ignore[no-untyped-def]
    from app import create_app
    from app.auth.jwt import VerificadorJWT
    from tests.conftest import _CHAVE, AUDIENCE, ISSUER

    app = create_app(
        Config(jwt_issuer=ISSUER, jwt_audience=AUDIENCE, testing=True),
        repo=repo,
        verificador=VerificadorJWT(
            issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
        ),
        auth_admin=auth_admin,
    )
    r = convidar(app.test_client(), auth, adm)
    assert r.status_code == 503 and _cod(r) == "convites_nao_configurados"
    assert not repo.convites


def test_email_nunca_vai_para_o_log(client, auth, auth_admin, adm, caplog):  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    convidar(client, auth, adm)
    convidar(client, auth, adm)  # 409
    uid = confirmar_conta(auth_admin)
    aceitar(client, auth, uid, auth_admin.ultimo_token())
    assert EMAIL not in caplog.text and "Novo Servidor" not in caplog.text


# ------------------------------------------------------------------------------ aceitar
def test_fluxo_completo_convite_aceite_e_uso(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    c = convidar(client, auth, adm, role="autorizador").get_json()["convite"]
    uid = confirmar_conta(auth_admin)
    r = aceitar(client, auth, uid, auth_admin.ultimo_token())
    assert r.status_code == 200
    j = r.get_json()
    # papel e unidade vieram do convite (banco), o nome do cadastro feito pelo admin
    assert (j["id"], j["role"], j["unidade"], j["nome"]) == (
        uid,
        "autorizador",
        "PF",
        "Novo Servidor",
    )
    assert "administrar" not in j["permissoes"]
    assert repo.convites[c["id"]]["usado_por"] == uid
    assert [e["ator_id"] for e in repo.eventos_de("convite_usado")] == [uid]
    assert client.get("/api/eu", headers=auth(uid)).status_code == 200


def test_convite_de_uso_unico(client, auth, auth_admin, adm):  # type: ignore[no-untyped-def]
    convidar(client, auth, adm)
    uid = confirmar_conta(auth_admin)
    token = auth_admin.ultimo_token()
    assert aceitar(client, auth, uid, token).status_code == 200
    r = aceitar(client, auth, uid, token)
    assert r.status_code == 409 and _cod(r) == "convite_encerrado"
    outra = auth_admin.nova_conta(
        "outra@gmail.com", "Outra"
    )  # outra conta tentando reaproveitar o token
    assert aceitar(client, auth, outra, token).status_code == 409


def test_convite_expirado(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    c = convidar(client, auth, adm).get_json()["convite"]
    uid = confirmar_conta(auth_admin)
    repo.convites[c["id"]]["expira_em"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    r = aceitar(client, auth, uid, auth_admin.ultimo_token())
    assert r.status_code == 410 and _cod(r) == "convite_expirado"
    assert uid not in repo.perfis
    h = auth(adm.id)
    lista = client.get("/api/admin/convites?estado=expirado", headers=h).get_json()
    assert [i["id"] for i in lista["itens"]] == [c["id"]] and lista["itens"][0][
        "estado"
    ] == "expirado"
    assert client.get("/api/admin/convites?estado=aberto", headers=h).get_json()["total"] == 0


def test_convite_revogado_nao_vale(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    c = convidar(client, auth, adm).get_json()["convite"]
    uid = confirmar_conta(auth_admin)
    r = client.post(f"/api/admin/convites/{c['id']}/revogar", headers=auth(adm.id))
    assert r.status_code == 200 and r.get_json()["convite"]["estado"] == "revogado"
    assert r.get_json()["convite"]["acoes_permitidas"] == []
    assert [e["ator_id"] for e in repo.eventos_de("convite_revogado")] == [adm.id]
    r = aceitar(client, auth, uid, auth_admin.ultimo_token())
    assert r.status_code == 409 and _cod(r) == "convite_encerrado"
    again = client.post(f"/api/admin/convites/{c['id']}/revogar", headers=auth(adm.id))
    assert again.status_code == 409


def test_aceite_recusa_conta_errada_ou_token_falso(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    c = convidar(client, auth, adm).get_json()["convite"]
    token = auth_admin.ultimo_token()
    intruso = auth_admin.nova_conta("intruso@gmail.com", "Intruso")
    r = aceitar(client, auth, intruso, token)
    assert r.status_code == 400 and _cod(r) == "convite_invalido"
    uid = auth_admin.id_de(EMAIL)  # ainda não confirmou o e-mail
    assert _cod(aceitar(client, auth, uid, token)) == "convite_invalido"
    confirmar_conta(auth_admin)
    for ruim in ("x" * 40, "curto", "ç" * 30):
        assert aceitar(client, auth, uid, ruim).status_code == 400
    assert repo.convites[c["id"]]["usado_em"] is None  # nenhuma tentativa consumiu o convite
    assert aceitar(client, auth, uid, token).status_code == 200


def test_aceite_exige_jwt_valido(client, auth_admin, adm, auth):  # type: ignore[no-untyped-def]
    convidar(client, auth, adm)
    token = auth_admin.ultimo_token()
    assert client.post("/api/convites/aceitar", json={"token": token}).status_code == 401
    r = client.post(
        "/api/convites/aceitar", headers={"Authorization": "Bearer lixo"}, json={"token": token}
    )
    assert r.status_code == 401


def test_aceite_nao_aceita_campo_de_papel_do_cliente(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    convidar(client, auth, adm, role="solicitante")
    uid = confirmar_conta(auth_admin)
    token = auth_admin.ultimo_token()
    for extra in ({"role": "admin"}, {"unidade": "UEA"}, {"ativo": True}, {"usuario_id": adm.id}):
        assert aceitar(client, auth, uid, token, **extra).status_code == 400, extra
    assert uid not in repo.perfis
    r = aceitar(client, auth, uid, token)
    assert r.get_json()["role"] == "solicitante"


def test_aceite_nome_de_reserva(client, auth, auth_admin, adm):  # type: ignore[no-untyped-def]
    convidar(client, auth, adm)
    uid = confirmar_conta(auth_admin)
    auth_admin.contas[uid]["nome"] = None  # ex.: conta recriada no Auth sem o metadado
    token = auth_admin.ultimo_token()
    r = aceitar(client, auth, uid, token)
    assert r.status_code == 400 and _cod(r) == "nome_obrigatorio"
    assert "nome" in r.get_json()["erro"]["mensagem"]
    r = aceitar(client, auth, uid, token, nome="Nome Informado")
    assert r.status_code == 200 and r.get_json()["nome"] == "Nome Informado"


def test_usuario_com_perfil_nao_reaceita(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    convidar(client, auth, adm)
    confirmar_conta(auth_admin)
    existente = repo.novo_perfil("solicitante")
    r = aceitar(client, auth, existente.id, auth_admin.ultimo_token())
    assert r.status_code == 400 and _cod(r) == "convite_invalido"  # e-mail não confere


def test_convite_de_admin_exige_criador_ainda_admin(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    outro = repo.novo_perfil("admin")
    convidar(client, auth, outro, role="admin")
    uid = confirmar_conta(auth_admin)
    client.post(f"/api/admin/usuarios/{outro.id}/desativar", headers=auth(adm.id))
    r = aceitar(client, auth, uid, auth_admin.ultimo_token())
    assert r.status_code == 403 and uid not in repo.perfis


def test_dois_aceites_simultaneos_so_um_persiste(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    convidar(client, auth, adm)
    uid = confirmar_conta(auth_admin)
    token = auth_admin.ultimo_token()
    barreira = threading.Barrier(2)
    status: list[int] = []

    def agir() -> None:
        barreira.wait()
        status.append(aceitar(client, auth, uid, token).status_code)

    ts = [threading.Thread(target=agir) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(status) == [200, 409]
    assert len(repo.eventos_de("convite_usado")) == 1


# ------------------------------------------------------------------------------ reenviar / listar
def test_reenviar_invalida_o_token_antigo(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    c = convidar(client, auth, adm, role="autorizador").get_json()["convite"]
    antigo = auth_admin.ultimo_token()
    envelhecer(repo, c["id"])
    r = client.post(f"/api/admin/convites/{c['id']}/reenviar", headers=auth(adm.id))
    assert r.status_code == 200
    novo = r.get_json()["convite"]
    assert novo["id"] != c["id"] and novo["estado"] == "aberto" and novo["role"] == "autorizador"
    assert len(auth_admin.envios) == 2 and auth_admin.ultimo_token() != antigo
    assert repo.convites[c["id"]]["revogado_por"] == adm.id
    uid = confirmar_conta(auth_admin)
    assert aceitar(client, auth, uid, antigo).status_code == 409
    assert aceitar(client, auth, uid, auth_admin.ultimo_token()).status_code == 200
    # o nome do cadastro não foi sobrescrito no reenvio
    assert auth_admin.contas[uid]["nome"] == "Novo Servidor"


def test_reenviar_expirado_e_reenviar_usado(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    c = convidar(client, auth, adm).get_json()["convite"]
    repo.convites[c["id"]]["expira_em"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    envelhecer(repo, c["id"])
    r = client.post(f"/api/admin/convites/{c['id']}/reenviar", headers=auth(adm.id))
    assert r.status_code == 200
    uid = confirmar_conta(auth_admin)
    aceitar(client, auth, uid, auth_admin.ultimo_token())
    novo = r.get_json()["convite"]["id"]
    r = client.post(f"/api/admin/convites/{novo}/reenviar", headers=auth(adm.id))
    assert r.status_code == 409 and _cod(r) == "convite_encerrado"


def test_convite_inexistente_ou_id_invalido(client, auth, adm):  # type: ignore[no-untyped-def]
    h = auth(adm.id)
    for acao in ("reenviar", "revogar"):
        assert (
            client.post(f"/api/admin/convites/{uuid.uuid4()}/{acao}", headers=h).status_code == 404
        )
        assert client.post(f"/api/admin/convites/xyz/{acao}", headers=h).status_code == 400


def test_listar_convites_sem_hash_e_com_filtro(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    convidar(client, auth, adm)
    convidar(client, auth, adm, email="b@gmail.com")
    h = auth(adm.id)
    j = client.get("/api/admin/convites", headers=h).get_json()
    assert j["total"] == 2 and "token" not in json.dumps(j)
    assert client.get("/api/admin/convites?estado=revogado", headers=h).get_json()["total"] == 0
    assert client.get("/api/admin/convites?estado=zzz", headers=h).status_code == 400


def test_reenviar_falha_mantem_antigo_valido(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    c = convidar(client, auth, adm).get_json()["convite"]
    antigo = auth_admin.ultimo_token()
    auth_admin.falhar_convite = True
    envelhecer(repo, c["id"])
    r = client.post(f"/api/admin/convites/{c['id']}/reenviar", headers=auth(adm.id))
    assert r.status_code == 503
    assert len(repo.convites) == 1 and repo.convites[c["id"]]["revogado_em"] is None
    auth_admin.falhar_convite = False
    uid = confirmar_conta(auth_admin)
    assert aceitar(client, auth, uid, antigo).status_code == 200  # token antigo ainda vale


def test_reenviar_conta_sem_perfil_usa_acesso(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    c = convidar(client, auth, adm).get_json()["convite"]
    uid = confirmar_conta(auth_admin)  # definiu a senha, mas ainda nao aceitou
    envelhecer(repo, c["id"])
    r = client.post(f"/api/admin/convites/{c['id']}/reenviar", headers=auth(adm.id))
    assert r.status_code == 200
    assert repo.convites[c["id"]]["revogado_em"] is not None
    assert aceitar(client, auth, uid, auth_admin.ultimo_token()).status_code == 200


def test_reenviar_perde_corrida_com_revogacao_nao_cria_convite(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    c = convidar(client, auth, adm).get_json()["convite"]
    original = repo.revogar_convite
    envelhecer(repo, c["id"])

    def revoga_antes(convite_id, *, ator_id):  # type: ignore[no-untyped-def]
        original(convite_id, ator_id=ator_id)  # outra aba revogou entre a leitura e o reenvio
        return original(convite_id, ator_id=ator_id)

    repo.revogar_convite = revoga_antes
    r = client.post(f"/api/admin/convites/{c['id']}/reenviar", headers=auth(adm.id))
    assert r.status_code == 409 and _cod(r) == "convite_encerrado"
    assert len(repo.convites) == 1


# ----------------------------------------------------------------------- M2: cooldown e limites
def test_reenviar_dentro_do_cooldown_da_429(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    c = convidar(client, auth, adm).get_json()["convite"]
    r = reenviar(client, auth, adm, c["id"])
    assert r.status_code == 429 and _cod(r) == "reenvio_muito_recente"
    assert "1 minuto" in r.get_json()["erro"]["mensagem"]
    assert len(auth_admin.envios) == 1 and repo.convites[c["id"]]["revogado_em"] is None
    envelhecer(repo, c["id"], segundos=59)
    assert reenviar(client, auth, adm, c["id"]).status_code == 429
    envelhecer(repo, c["id"], segundos=61)
    assert reenviar(client, auth, adm, c["id"]).status_code == 200


def test_cooldown_vale_tambem_para_o_convite_recem_reenviado(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    c = convidar(client, auth, adm).get_json()["convite"]
    envelhecer(repo, c["id"])
    novo = reenviar(client, auth, adm, c["id"]).get_json()["convite"]
    assert _cod(reenviar(client, auth, adm, novo["id"])) == "reenvio_muito_recente"


def test_limite_de_reenvios_por_destino_vale_mesmo_trocando_de_convite(  # type: ignore[no-untyped-def]
    client, auth, auth_admin, adm, repo
):
    from app.services.convites import MAX_REENVIOS_POR_DESTINO

    atual = convidar(client, auth, adm).get_json()["convite"]
    for _ in range(MAX_REENVIOS_POR_DESTINO):
        envelhecer(repo, atual["id"])
        atual = reenviar(client, auth, adm, atual["id"]).get_json()["convite"]
    envelhecer(repo, atual["id"])
    n = len(auth_admin.envios)
    r = reenviar(client, auth, adm, atual["id"])  # id novo a cada reenvio: o limite é por e-mail
    assert r.status_code == 429 and _cod(r) == "limite_excedido"
    assert len(auth_admin.envios) == n and repo.convites[atual["id"]]["revogado_em"] is None
    outro = convidar(client, auth, adm, email="outro@gmail.com").get_json()["convite"]
    envelhecer(repo, outro["id"])
    assert reenviar(client, auth, adm, outro["id"]).status_code == 200  # outro destino livre


def test_dois_reenvios_simultaneos_enviam_um_so_email(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    c = convidar(client, auth, adm).get_json()["convite"]
    envelhecer(repo, c["id"])
    liberar = threading.Event()
    auth_admin.atraso_convidar = liberar
    status: list[int] = []

    def agir() -> None:
        status.append(reenviar(client, auth, adm, c["id"]).status_code)

    t1 = threading.Thread(target=agir)
    t1.start()
    assert auth_admin.dentro_de_convidar.wait(5)  # o 1º está enviando
    t2 = threading.Thread(target=agir)
    t2.start()
    t2.join(5)  # o 2º é recusado sem enviar
    liberar.set()
    t1.join(5)
    assert sorted(status) == [200, 409]
    assert len(auth_admin.envios) == 2  # o original + um único reenvio
    assert len([x for x in repo.convites.values() if x["revogado_em"] is None]) == 1


def test_reenvio_que_perdeu_corrida_antes_da_reserva_nao_envia(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    """O convite foi revogado por outro reenvio entre a 1ª leitura e a reserva."""
    c = convidar(client, auth, adm).get_json()["convite"]
    envelhecer(repo, c["id"])
    from app.services import convites as mod

    original = mod.CONTROLE.reservar

    def reserva_apos_revogacao(convite_id, email):  # type: ignore[no-untyped-def]
        repo.revogar_convite(convite_id, ator_id=adm.id)
        original(convite_id, email)

    mod.CONTROLE.reservar = reserva_apos_revogacao  # type: ignore[method-assign]
    try:
        r = reenviar(client, auth, adm, c["id"])
    finally:
        del mod.CONTROLE.reservar
    assert r.status_code == 409 and _cod(r) == "convite_encerrado"
    assert len(auth_admin.envios) == 1


# ----------------------------------------------------------------------- B2: janelas do reenvio
def test_reenviar_insercao_falha_apos_revogar_libera_email(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    """Janela residual documentada: o e-mail saiu, o antigo foi revogado e o novo não gravou.
    O admin cria um convite novo (o índice de convite aberto está livre)."""
    from app.errors import ErroInterno

    c = convidar(client, auth, adm).get_json()["convite"]
    envelhecer(repo, c["id"])

    def falha(_dados):  # type: ignore[no-untyped-def]
        raise ErroInterno("Falha ao gravar.")

    original = repo.inserir_convite
    repo.inserir_convite = falha  # type: ignore[method-assign]
    r = reenviar(client, auth, adm, c["id"])
    assert r.status_code == 503
    assert len(auth_admin.envios) == 2 and repo.convites[c["id"]]["revogado_em"] is not None
    assert not [x for x in repo.convites.values() if x["revogado_em"] is None]
    repo.inserir_convite = original  # type: ignore[method-assign]
    assert convidar(client, auth, adm).status_code == 201


def test_reenviar_revogar_devolve_none_com_email_ja_entregue(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    c = convidar(client, auth, adm).get_json()["convite"]
    envelhecer(repo, c["id"])
    repo.revogar_convite = lambda *a, **k: None  # type: ignore[method-assign,assignment]
    r = reenviar(client, auth, adm, c["id"])
    assert r.status_code == 409 and _cod(r) == "convite_encerrado"
    assert len(auth_admin.envios) == 2  # entregue antes da revogação (ordem documentada)
    assert len(repo.convites) == 1  # e nenhum convite novo foi gravado


def test_entregar_conta_existente_sem_id_nao_reenvia_acesso(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    auth_admin.nova_conta(EMAIL, "Novo Servidor")  # confirmada: convidar() levanta ContaExistente
    auth_admin.id_por_email = lambda _e: None  # type: ignore[method-assign]
    r = convidar(client, auth, adm)
    assert r.status_code == 409 and _cod(r) == "conta_existente"
    assert not auth_admin.envios
    (c,) = repo.convites.values()
    assert c["revogado_em"] is not None


def test_reenviar_conta_com_perfil_mantem_o_antigo_valido(client, auth, auth_admin, adm, repo):  # type: ignore[no-untyped-def]
    c = convidar(client, auth, adm).get_json()["convite"]
    antigo = auth_admin.ultimo_token()
    envelhecer(repo, c["id"])
    uid = confirmar_conta(auth_admin)
    from app.modelos import Perfil

    repo.perfis[uid] = Perfil(uid, "Novo Servidor", "PF", "solicitante", True)
    r = reenviar(client, auth, adm, c["id"])
    assert r.status_code == 409 and _cod(r) == "conta_existente"
    assert len(auth_admin.envios) == 1
    assert repo.convites[c["id"]]["revogado_em"] is None
    assert len(repo.convites) == 1
    del repo.perfis[uid]  # sem perfil o token antigo ainda é aceito
    assert aceitar(client, auth, uid, antigo).status_code == 200
