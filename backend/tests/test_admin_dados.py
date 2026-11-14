"""Exportar/importar dados (admin): permissão, arquivo inválido/adulterado, prévia, auditoria."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.services.admin_dados import hash_tabelas

URL_EXP = "/api/admin/dados/exportar"
URL_IMP = "/api/admin/dados/importar"
TABELAS = {"veiculos", "requisicoes", "localizacoes_viagem", "checklists", "configuracoes"}


def _cod(r) -> str:  # type: ignore[no-untyped-def]
    return r.get_json()["erro"]["codigo"]


def _veiculo(**extra: Any) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "descricao": "Fiat Argo",
        "placa": "ABC1D23",
        "ativo": True,
        **extra,
    }


def _requisicao(**extra: Any) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "usuario_id": str(uuid.uuid4()),
        "nome_solicitante": "Fulano de Tal",
        "unidade": "PU",
        "hora_saida": "2026-11-14T10:00:00-03:00",
        "trajeto": "AGU - Fórum",
        "veiculo_id": str(uuid.uuid4()),
        "status": "solicitada",
        **extra,
    }


def arquivo(tabelas: dict[str, Any] | None = None, **troca: Any) -> dict[str, Any]:
    tabelas = tabelas if tabelas is not None else {"veiculos": [_veiculo()]}
    base = {"versao": 1, "gerado_em": "2026-11-14T10:00:00-03:00", "tabelas": tabelas}
    base["sha256"] = hash_tabelas(tabelas)
    return {**base, **troca}


def enviar(client, auth, quem, corpo: Any, modo: str | None = "previa"):  # type: ignore[no-untyped-def]
    dados = corpo if isinstance(corpo, bytes) else json.dumps(corpo).encode()
    url = URL_IMP + (f"?modo={modo}" if modo is not None else "")
    return client.post(url, headers=auth(quem.id), data=dados, content_type="application/json")


# ------------------------------------------------------------------------- permissão
@pytest.mark.parametrize("role", ["solicitante", "autorizador", "vigilante"])
def test_nao_admin_recebe_403_e_nada_acontece(client, auth, repo, role):  # type: ignore[no-untyped-def]
    quem = repo.novo_perfil(role)
    assert client.get(URL_EXP, headers=auth(quem.id)).status_code == 403
    r = enviar(client, auth, quem, arquivo(), "confirmar")
    assert r.status_code == 403 and _cod(r) == "sem_permissao"
    assert not repo.chamadas_importar and not repo.eventos_de("dados_exportados")


def test_sem_token_401(client):  # type: ignore[no-untyped-def]
    assert client.get(URL_EXP).status_code == 401
    assert client.post(URL_IMP + "?modo=previa", json=arquivo()).status_code == 401


def test_admin_inativo_nao_exporta(client, auth, repo):  # type: ignore[no-untyped-def]
    inativo = repo.novo_perfil("admin", ativo=False)
    assert client.get(URL_EXP, headers=auth(inativo.id)).status_code in (401, 403)
    assert not repo.eventos_de("dados_exportados")


# ------------------------------------------------------------------------- exportar
def test_exportar_formato_hash_e_auditoria(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    v = _veiculo()
    repo.dados["veiculos"][v["id"]] = {**v, "coluna_nova_do_banco": "x"}
    r = client.get(URL_EXP, headers=auth(adm.id))
    assert r.status_code == 200 and r.mimetype == "application/json"
    disp = r.headers["Content-Disposition"]
    assert "attachment" in disp and ".json" in disp
    assert r.headers["Cache-Control"] == "no-store"
    arq = json.loads(r.data)
    assert arq["versao"] == 1 and arq["sha256"] == hash_tabelas(arq["tabelas"])
    assert set(arq["tabelas"]) == TABELAS  # sem perfis, auditoria nem checklist_fotos
    assert isinstance(arq["tabelas"]["configuracoes"], dict)
    assert (
        "mfa" not in arq["tabelas"]["configuracoes"]
        and "passkeys" not in arq["tabelas"]["configuracoes"]
    )
    assert "coluna_nova_do_banco" not in arq["tabelas"]["veiculos"][0]  # só colunas do esquema
    ev = repo.eventos_de("dados_exportados")
    assert len(ev) == 1 and ev[0]["ator_id"] == adm.id
    assert ev[0]["detalhes"]["contagens"]["veiculos"] == 1
    assert "Fiat" not in json.dumps(ev[0]["detalhes"])  # auditoria só com contagens


def test_exportar_falha_fechada_sem_auditoria(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    repo.dados["veiculos"]["x"] = _veiculo(descricao="Fiat Sigiloso")
    assert client.get(URL_EXP, headers=auth(adm.id)).status_code == 200
    repo.falha_auditoria = RuntimeError("boom")
    r = client.get(URL_EXP, headers=auth(adm.id))
    assert r.status_code >= 500 and b"Sigiloso" not in r.data
    assert len(repo.eventos_de("dados_exportados")) == 1  # só a primeira (com trilha)


def test_exportar_e_reimportar_fecha_o_ciclo(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    v = _veiculo()
    repo.dados["veiculos"][v["id"]] = v
    exportado = client.get(URL_EXP, headers=auth(adm.id)).data
    r = enviar(client, auth, adm, exportado, "previa")
    assert r.status_code == 200 and r.get_json()["erros"] == []
    assert r.get_json()["resumo"]["veiculos"] == {"novos": 0, "existentes": 1, "conflitos": 0}


# ------------------------------------------------------------------------- arquivo inválido
@pytest.mark.parametrize(
    "corpo", [b"nao e json", b"[]", b'"x"', b"{}", json.dumps({"versao": 1}).encode()]
)
def test_formato_invalido(client, auth, adm, repo, corpo):  # type: ignore[no-untyped-def]
    r = enviar(client, auth, adm, corpo, "confirmar")
    assert r.status_code == 400 and _cod(r) == "requisicao_invalida"
    assert not repo.chamadas_importar


@pytest.mark.parametrize("versao", [2, 0, "1", True, None])
def test_versao_nao_suportada(client, auth, adm, repo, versao):  # type: ignore[no-untyped-def]
    assert enviar(client, auth, adm, arquivo(versao=versao)).status_code == 400
    assert not repo.chamadas_importar


def test_arquivo_adulterado(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    arq = arquivo()
    arq["tabelas"]["veiculos"][0]["descricao"] = "Editado à mão"
    r = enviar(client, auth, adm, arq, "confirmar")
    assert r.status_code == 400 and _cod(r) == "arquivo_adulterado"
    r = enviar(client, auth, adm, arquivo(sha256="0" * 64), "confirmar")
    assert _cod(r) == "arquivo_adulterado"
    assert enviar(client, auth, adm, arquivo(sha256=123), "confirmar").status_code == 400
    assert not repo.chamadas_importar and not repo.dados["veiculos"]


@pytest.mark.parametrize("tabela", ["perfis", "auditoria", "checklist_fotos", "outra"])
def test_tabela_fora_do_escopo_recusada(client, auth, adm, repo, tabela):  # type: ignore[no-untyped-def]
    r = enviar(client, auth, adm, arquivo({tabela: [{"id": str(uuid.uuid4())}]}), "confirmar")
    assert r.status_code == 400 and not repo.chamadas_importar


def test_tabela_que_nao_e_lista(client, auth, adm):  # type: ignore[no-untyped-def]
    assert enviar(client, auth, adm, arquivo({"veiculos": {"a": 1}})).status_code == 400


@pytest.mark.parametrize("modo", [None, "", "apagar", "CONFIRMAR"])
def test_modo_invalido(client, auth, adm, repo, modo):  # type: ignore[no-untyped-def]
    r = enviar(client, auth, adm, arquivo(), modo)
    assert r.status_code == 400 and not repo.chamadas_importar


def test_arquivo_acima_do_limite_413(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    r = enviar(client, auth, adm, b" " * (10 * 1024 * 1024 + 1), "confirmar")
    assert r.status_code == 413 and not repo.chamadas_importar


def test_arquivo_maior_que_16kb_mas_dentro_do_limite_passa(client, auth, adm):  # type: ignore[no-untyped-def]
    vs = [_veiculo(placa=None, descricao=f"Carro {i}") for i in range(300)]
    corpo = json.dumps(arquivo({"veiculos": vs})).encode()
    assert len(corpo) > 16 * 1024
    r = enviar(client, auth, adm, corpo)
    assert r.status_code == 200 and r.get_json()["resumo"]["veiculos"]["novos"] == 300


def test_limite_global_continua_pequeno_nas_outras_rotas(client, auth, adm):  # type: ignore[no-untyped-def]
    r = client.post("/api/admin/veiculos", headers=auth(adm.id), json={"descricao": "x" * 20000})
    assert r.status_code == 413


# ------------------------------------------------------------------------- validação de linhas
def _um_erro(tabelas: dict[str, Any], tabela: str, indice: int, client, auth, adm, repo):  # type: ignore[no-untyped-def]
    for modo in ("previa", "confirmar"):
        r = enviar(client, auth, adm, arquivo(tabelas), modo)
        j = r.get_json()
        assert r.status_code == 200 and j["total_erros"] == 1
        assert (j["erros"][0]["tabela"], j["erros"][0]["indice"]) == (tabela, indice)
        assert j["acoes_permitidas"] == []
    assert not repo.chamadas_importar and not repo.eventos_de("dados_importados")


@pytest.mark.parametrize(
    "linha",
    [
        {"id": "nao-uuid", "descricao": "x", "ativo": True},
        {"id": str(uuid.uuid4()), "descricao": "", "ativo": True},
        {"id": str(uuid.uuid4()), "descricao": "x", "ativo": "sim"},
        {"id": str(uuid.uuid4()), "descricao": "x", "ativo": True, "placa": "abc"},
        {"id": str(uuid.uuid4()), "descricao": "x", "ativo": True, "extra": 1},
        {"descricao": "x", "ativo": True},
        "texto",
    ],
)
def test_veiculo_invalido(client, auth, adm, repo, linha):  # type: ignore[no-untyped-def]
    _um_erro({"veiculos": [_veiculo(), linha]}, "veiculos", 1, client, auth, adm, repo)


@pytest.mark.parametrize(
    "troca",
    [
        {"status": "inventado"},
        {"hora_saida": "2026-11-14T10:00:00"},  # timestamptz exige fuso
        {"hora_saida": "ontem"},
        {"km_saida": -1},
        {"km_saida": True},
        {"unidade": "XX"},
        {"trajeto": "x" * 501},
        {"usuario_id": None},
    ],
)
def test_requisicao_invalida(client, auth, adm, repo, troca):  # type: ignore[no-untyped-def]
    _um_erro({"requisicoes": [_requisicao(**troca)]}, "requisicoes", 0, client, auth, adm, repo)


def test_localizacao_fora_da_faixa(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    loc = {
        "id": str(uuid.uuid4()),
        "requisicao_id": str(uuid.uuid4()),
        "momento": "saida",
        "latitude": 91,
        "longitude": 0,
        "registrado_por": str(uuid.uuid4()),
    }
    _um_erro({"localizacoes_viagem": [loc]}, "localizacoes_viagem", 0, client, auth, adm, repo)


def test_mensagem_de_erro_nao_ecoa_valor(client, auth, adm):  # type: ignore[no-untyped-def]
    linha = _veiculo(descricao="SEGREDO-JOAO", ativo="SEGREDO-X", extra_SEGREDO=1)
    for ruim in (linha, _veiculo(ativo="SEGREDO-X")):
        r = enviar(client, auth, adm, arquivo({"veiculos": [ruim]}))
        assert "SEGREDO-X" not in json.dumps(r.get_json()) and "JOAO" not in json.dumps(
            r.get_json()
        )


def test_ids_e_momentos_repetidos(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    v = _veiculo()
    _um_erro({"veiculos": [v, dict(v)]}, "veiculos", 1, client, auth, adm, repo)
    rid = str(uuid.uuid4())
    base = {
        "requisicao_id": rid,
        "momento": "saida",
        "combustivel": "meio",
        "avarias": False,
        "registrado_por": rid,
    }
    cks = [{"id": str(uuid.uuid4()), **base}, {"id": str(uuid.uuid4()), **base}]
    _um_erro({"checklists": cks}, "checklists", 1, client, auth, adm, repo)


def test_configuracoes_aceita_no_maximo_uma_linha(client, auth, adm):  # type: ignore[no-untyped-def]
    r = enviar(client, auth, adm, arquivo({"configuracoes": [{"motorista": True}, {}]}))
    assert r.status_code == 400


@pytest.mark.parametrize("flag", ["mfa", "passkeys", "notificacao_push", "relatorio_email"])
def test_flags_de_seguranca_nunca_entram_por_importacao(client, auth, adm, repo, flag):  # type: ignore[no-untyped-def]
    for cfg in ({flag: True}, [{flag: True}]):
        j = enviar(client, auth, adm, arquivo({"configuracoes": cfg}), "confirmar").get_json()
        assert j["total_erros"] == 1 and j["erros"][0]["tabela"] == "configuracoes"
    assert not repo.chamadas_importar


def test_configuracoes_como_objeto_chega_ao_banco(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    r = enviar(client, auth, adm, arquivo({"configuracoes": {"motorista": True}}), "confirmar")
    assert r.status_code == 200 and r.get_json()["total_erros"] == 0
    assert repo.payloads_importar[0] == {
        "versao": 1,
        "tabelas": {"configuracoes": {"motorista": True}},
    }


# ------------------------------------------------------------------------- prévia e confirmar
def test_previa_nao_grava_e_oferece_confirmar(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    existente = _veiculo()
    repo.dados["veiculos"][existente["id"]] = existente
    corpo = arquivo({"veiculos": [existente, _veiculo(placa=None)]})
    r = enviar(client, auth, adm, corpo, "previa")
    j = r.get_json()
    assert r.status_code == 200 and j["erros"] == []
    assert j["resumo"]["veiculos"] == {"novos": 1, "existentes": 1, "conflitos": 0}
    assert set(j["resumo"]) == TABELAS and j["acoes_permitidas"] == ["confirmar"]
    assert repo.chamadas_importar == [{"admin_id": adm.id, "confirmar": False}]
    assert set(repo.payloads_importar[0]) == {"versao", "tabelas"}  # envelope, sem sha256
    assert len(repo.dados["veiculos"]) == 1  # nada gravado
    assert not repo.eventos_de("dados_importados")


def test_previa_sem_novidade_nao_oferece_confirmar(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    v = _veiculo()
    repo.dados["veiculos"][v["id"]] = v
    assert (
        enviar(client, auth, adm, arquivo({"veiculos": [v]})).get_json()["acoes_permitidas"] == []
    )


def test_confirmar_grava_mescla_e_audita_so_contagens(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    antigo = _veiculo()
    repo.dados["veiculos"][antigo["id"]] = antigo
    novo = _veiculo(placa=None, descricao="Nome Pessoal Sensível")
    r = enviar(client, auth, adm, arquivo({"veiculos": [novo]}), "confirmar")
    assert r.status_code == 200 and r.get_json()["resumo"]["veiculos"]["novos"] == 1
    assert set(repo.dados["veiculos"]) == {antigo["id"], novo["id"]}  # mescla: nunca apaga
    assert repo.chamadas_importar == [{"admin_id": adm.id, "confirmar": True}]
    assert r.get_json()["aplicado"] is True and r.get_json()["acoes_permitidas"] == []
    ev = repo.eventos_de("dados_importados")  # auditado pela RPC, uma única vez
    assert len(ev) == 1 and ev[0]["ator_id"] == adm.id
    assert ev[0]["detalhes"]["resumo"]["veiculos"]["novos"] == 1
    assert "Sensível" not in json.dumps(ev[0]["detalhes"])


def test_ator_vem_do_jwt_nunca_do_corpo(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    outro = repo.novo_perfil("admin")
    enviar(client, auth, adm, arquivo(admin_id=outro.id, p_admin=outro.id), "previa")
    assert repo.chamadas_importar == []  # campos extras no envelope são recusados
    enviar(client, auth, adm, arquivo(), "confirmar")
    assert repo.chamadas_importar[0]["admin_id"] == adm.id
    assert repo.eventos_de("dados_importados")[0]["ator_id"] == adm.id


def test_erro_de_linha_do_banco_nao_grava_nem_audita(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    repo.erros_importar = [
        {"tabela": "requisicoes", "indice": 0, "mensagem": "Veículo já ocupado."}
    ]
    r = enviar(client, auth, adm, arquivo({"veiculos": [_veiculo()]}), "confirmar")
    j = r.get_json()
    assert r.status_code == 200 and j["erros"][0]["mensagem"] == "Veículo já ocupado."
    assert j["acoes_permitidas"] == [] and not repo.dados["veiculos"]
    assert not repo.eventos_de("dados_importados")


@pytest.mark.parametrize("sqlstate", ["AG060", "AG061"])
def test_erro_de_arquivo_do_banco_vira_400_com_texto_fixo(client, auth, adm, repo, sqlstate):  # type: ignore[no-untyped-def]
    from tests.fakes_admin import erro_db

    def falha(*_a, **_k):  # type: ignore[no-untyped-def]
        raise erro_db(sqlstate, "Arquivo de dados inválido: tabela não reconhecida.")

    repo.importar_dados = falha  # type: ignore[method-assign]
    r = enviar(client, auth, adm, arquivo(), "previa")
    assert r.status_code == 400 and _cod(r) == "arquivo_invalido"
    assert "tabela não reconhecida" in r.get_json()["erro"]["mensagem"]


# ------------------------------------------------------------------------- revisão de segurança
@pytest.mark.parametrize(
    ("tabela", "limite"),
    [
        ("veiculos", 500),
        ("requisicoes", 20000),
        ("checklists", 40000),
        ("localizacoes_viagem", 40000),
    ],
)
def test_limite_por_tabela_recusado_antes_do_banco(client, auth, adm, repo, tabela, limite):  # type: ignore[no-untyped-def]
    r = enviar(client, auth, adm, arquivo({tabela: [{}] * (limite + 1)}), "previa")
    assert r.status_code == 400 and tabela in r.get_json()["erro"]["mensagem"]
    assert repo.chamadas_importar == []


def test_limite_exato_da_tabela_nao_e_recusado_pelo_limite(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    r = enviar(client, auth, adm, arquivo({"veiculos": [{}] * 500}), "previa")
    j = r.get_json()
    assert r.status_code == 200 and j["total_erros"] == 500  # erro de linha, não de limite


@pytest.mark.parametrize("texto", ["a\x00b", "a\x01b", "a\x7fb"])
def test_texto_com_controle_e_hash_errado_recusado(client, auth, adm, repo, texto):  # type: ignore[no-untyped-def]
    corpo = arquivo({"veiculos": [_veiculo(descricao=texto)]})
    corpo["sha256"] = "0" * 64
    r = enviar(client, auth, adm, corpo, "previa")
    assert r.status_code == 400  # sha não confere
    assert repo.chamadas_importar == []


@pytest.mark.parametrize("texto", ["a\x00b", "a\x01b", "a\x7fb"])
def test_controle_em_texto_com_hash_valido_e_erro_de_linha(client, auth, adm, repo, texto):  # type: ignore[no-untyped-def]
    _um_erro(
        {"veiculos": [_veiculo(), _veiculo(descricao=texto)]},
        "veiculos",
        1,
        client,
        auth,
        adm,
        repo,
    )


def test_substituto_solto_vira_400_e_nao_500(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    bruto = (
        '{"versao":1,"gerado_em":"x","sha256":"' + "0" * 64 + '","tabelas":'
        '{"veiculos":[{"id":"' + str(uuid.uuid4()) + '","descricao":"\\ud800","ativo":true}]}}'
    ).encode()
    r = enviar(client, auth, adm, bruto, "previa")
    assert r.status_code == 400 and _cod(r) == "arquivo_invalido"


def test_tab_e_quebra_de_linha_continuam_permitidos(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    r = enviar(client, auth, adm, arquivo({"veiculos": [_veiculo(descricao="a\tb\nc")]}), "previa")
    assert r.status_code == 200 and r.get_json()["total_erros"] == 0


@pytest.mark.parametrize(
    "codigo", ["ABC", "A" * 32, "g" * 32, "a" * 31, "a" * 33, "a" * 32 + "\n", "-" * 32]
)
def test_codigo_publico_invalido_e_erro_de_linha(client, auth, adm, repo, codigo):  # type: ignore[no-untyped-def]
    _um_erro(
        {"veiculos": [_veiculo(codigo_publico=codigo)]}, "veiculos", 0, client, auth, adm, repo
    )


def test_codigo_publico_valido_ou_nulo_passa(client, auth, adm):  # type: ignore[no-untyped-def]
    for cod in ("0123456789abcdef0123456789abcdef", None):
        r = enviar(client, auth, adm, arquivo({"veiculos": [_veiculo(codigo_publico=cod)]}))
        assert r.status_code == 200 and r.get_json()["total_erros"] == 0


def test_configuracoes_alteradas_repassadas_na_previa_e_confirmar(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    repo.dados["configuracoes"]["unica"] = {"quilometragem": False, "checklist": True}
    corpo = arquivo({"configuracoes": {"quilometragem": True, "checklist": True}})
    for modo in ("previa", "confirmar"):
        j = enviar(client, auth, adm, corpo, modo).get_json()
        assert j["configuracoes_alteradas"] == [
            {"chave": "quilometragem", "rotulo": "Quilometragem", "de": False, "para": True}
        ]


def test_notificacao_email_criticos_fora_da_importacao(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    repo.dados["configuracoes"]["unica"] = {"notificacao_email_criticos": False}
    j = enviar(
        client, auth, adm, arquivo({"configuracoes": {"notificacao_email_criticos": True}})
    ).get_json()
    assert j["configuracoes_alteradas"] == []


def test_uuid_maiusculo_conta_como_duplicata(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    v = _veiculo()
    _um_erro(
        {"veiculos": [v, {**v, "id": v["id"].upper()}]}, "veiculos", 1, client, auth, adm, repo
    )
    rid = str(uuid.uuid4())
    base = {"momento": "saida", "combustivel": "meio", "avarias": False, "registrado_por": rid}
    cks = [
        {"id": str(uuid.uuid4()), "requisicao_id": rid, **base},
        {"id": str(uuid.uuid4()), "requisicao_id": rid.upper(), **base},
    ]
    _um_erro({"checklists": cks}, "checklists", 1, client, auth, adm, repo)


def test_uuids_vao_em_minusculas_para_o_banco(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    v = _veiculo()
    r = _requisicao(veiculo_id=v["id"].upper())
    r["id"] = r["id"].upper()
    r["usuario_id"] = r["usuario_id"].upper()
    j = enviar(client, auth, adm, arquivo({"veiculos": [v], "requisicoes": [r]}), "confirmar")
    assert j.get_json()["aplicado"] is True
    enviado = repo.payloads_importar[-1]["tabelas"]["requisicoes"][0]
    assert enviado["id"] == r["id"].lower()
    assert enviado["usuario_id"] == r["usuario_id"].lower()
    assert enviado["veiculo_id"] == v["id"]
    assert enviado["nome_solicitante"] == r["nome_solicitante"]  # texto intacto


def test_configuracoes_alteradas_traz_rotulo_pt_br(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    repo.dados["configuracoes"]["unica"] = {"quilometragem": False}
    j = enviar(client, auth, adm, arquivo({"configuracoes": {"quilometragem": True}})).get_json()
    assert j["configuracoes_alteradas"][0]["rotulo"] == "Quilometragem"


def test_configuracoes_alteradas_vazia_quando_ha_erro_de_linha(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    j = enviar(client, auth, adm, arquivo({"veiculos": [{"id": "x"}]})).get_json()
    assert j["configuracoes_alteradas"] == []


def test_configuracoes_alteradas_descarta_lixo_do_banco(client, auth, adm, repo):  # type: ignore[no-untyped-def]
    original = repo.importar_dados

    def com_lixo(*a, **k):  # type: ignore[no-untyped-def]
        r = original(*a, **k)
        r["configuracoes_alteradas"] = [
            {"chave": "mfa", "de": False, "para": True},
            {"chave": "voz_campos", "de": "x", "para": True},
            {"chave": "voz_campos", "de": False, "para": True, "extra": "x"},
            "lixo",
        ]
        return r

    repo.importar_dados = com_lixo  # type: ignore[method-assign]
    j = enviar(client, auth, adm, arquivo()).get_json()
    assert j["configuracoes_alteradas"] == [
        {"chave": "voz_campos", "rotulo": "Ditado por voz", "de": False, "para": True}
    ]
