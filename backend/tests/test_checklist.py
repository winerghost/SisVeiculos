"""Recurso opcional Checklist: URL assinada, registro atômico, leitura, janela e anti-IDOR."""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

import pytest

from app.modelos import Configuracoes
from tests.test_requisicoes import acao, criar

LIGADO = Configuracoes(checklist=True)
SEGREDO = "Joao Silva tel 61 99999-0000"
BASE = {"combustivel": "meio", "avarias": False}


def nova(client, auth, c) -> str:  # type: ignore[no-untyped-def]
    """Requisição autorizada."""
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]
    assert acao(client, auth, c.aut, rid, "autorizar").status_code == 200
    return rid


def post(client, auth, perfil, rid, caminho, corpo: Any = None):  # type: ignore[no-untyped-def]
    return client.post(f"/api/requisicoes/{rid}/{caminho}", headers=auth(perfil.id), json=corpo)


def cod(r: Any) -> str:
    return r.get_json()["erro"]["codigo"]


def foto(client, auth, perfil, rid, storage, momento="saida", mime="image/jpeg", tam=1000):  # type: ignore[no-untyped-def]
    """Pede a URL, 'envia' o arquivo e devolve o item para o registro."""
    r = post(
        client, auth, perfil, rid, f"checklist/{momento}/fotos", {"mime": mime, "tamanho": tam}
    )
    assert r.status_code == 201, r.get_json()
    j = r.get_json()
    storage.enviar(j["caminho"], mime, tam)
    return {"id": j["foto_id"], "mime": mime, "tamanho": tam}


# ------------------------------------------------------------------ desligado
def test_desligado_recusa_endpoints(client, auth, c, repo):  # type: ignore[no-untyped-def]
    rid = nova(client, auth, c)
    for r in (
        post(
            client, auth, c.aut, rid, "checklist/saida/fotos", {"mime": "image/png", "tamanho": 1}
        ),
        post(client, auth, c.aut, rid, "checklist/saida", BASE),
        client.get(f"/api/requisicoes/{rid}/checklist", headers=auth(c.aut.id)),
    ):
        assert r.status_code == 404 and cod(r) == "recurso_desligado"
    assert repo.chamadas_rpc == []


def test_desligado_saida_e_chegada_nao_exigem_nem_anunciam(client, auth, c):  # type: ignore[no-untyped-def]
    rid = nova(client, auth, c)
    j = client.get(f"/api/requisicoes/{rid}", headers=auth(c.aut.id)).get_json()
    assert j["campos_acao"] == {}
    assert acao(client, auth, c.aut, rid, "saida").status_code == 200
    assert acao(client, auth, c.aut, rid, "chegada").status_code == 200


# ------------------------------------------------------------------ ligado: fluxo
def test_ligado_exige_checklist_e_ciclo_completo(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    j = client.get(f"/api/requisicoes/{rid}", headers=auth(c.aut.id)).get_json()
    assert j["campos_acao"] == {
        "registrar_saida": {"campos_obrigatorios": [], "checklist_pendente": "saida"}
    }
    # sem checklist: saída recusada com código claro (e o estado não muda)
    r = acao(client, auth, c.aut, rid, "saida")
    assert r.status_code == 409 and cod(r) == "checklist_obrigatorio"
    assert repo.requisicoes[rid]["status"] == "autorizada"

    f = foto(client, auth, c.aut, rid, storage)
    r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [f]})
    assert r.status_code == 201
    j = r.get_json()
    assert j["checklist"]["fotos"] == 1 and "descricao_avarias" not in j["checklist"]
    assert j["requisicao"]["campos_acao"] == {"registrar_saida": {"campos_obrigatorios": []}}
    assert acao(client, auth, c.aut, rid, "saida").status_code == 200

    j = client.get(f"/api/requisicoes/{rid}", headers=auth(c.aut.id)).get_json()
    assert j["campos_acao"] == {
        "registrar_chegada": {"campos_obrigatorios": [], "checklist_pendente": "chegada"}
    }
    r = acao(client, auth, c.aut, rid, "chegada")
    assert r.status_code == 409 and cod(r) == "checklist_obrigatorio"
    assert (
        post(client, auth, c.adm, rid, "checklist/chegada", {**BASE, "avarias": False}).status_code
        == 201
    )
    assert acao(client, auth, c.aut, rid, "chegada").status_code == 200
    # auditoria sem dado pessoal
    assert all(
        set(a["detalhes"]) == {"requisicao_id", "momento", "avarias", "fotos"}
        for a in repo.auditoria_checklist
    )


def test_borda_em_uso_sem_checklist_de_saida_so_exige_chegada(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    rid = nova(client, auth, c)
    assert acao(client, auth, c.aut, rid, "saida").status_code == 200  # recurso desligado
    repo.config = LIGADO
    j = client.get(f"/api/requisicoes/{rid}", headers=auth(c.aut.id)).get_json()
    assert j["campos_acao"]["registrar_chegada"]["checklist_pendente"] == "chegada"
    r = post(client, auth, c.aut, rid, "checklist/saida", BASE)  # retroativo: fora da janela
    assert r.status_code == 409 and cod(r) == "checklist_fora_da_janela"
    assert post(client, auth, c.aut, rid, "checklist/chegada", BASE).status_code == 201
    assert acao(client, auth, c.aut, rid, "chegada").status_code == 200


def test_lista_de_requisicoes_traz_checklist_pendente(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    nova(client, auth, c)
    itens = client.get("/api/requisicoes", headers=auth(c.aut.id)).get_json()["itens"]
    assert itens[0]["campos_acao"]["registrar_saida"]["checklist_pendente"] == "saida"


# ------------------------------------------------------------------ janela
def test_janela_saida_so_autorizada_chegada_so_em_uso(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = criar(client, auth, c.sol, c.veiculo).get_json()["id"]  # solicitada
    for caminho, corpo in (
        ("checklist/saida", BASE),
        ("checklist/saida/fotos", {"mime": "image/png", "tamanho": 5}),
    ):
        r = post(client, auth, c.aut, rid, caminho, corpo)
        assert r.status_code == 409 and cod(r) == "checklist_fora_da_janela"
    assert storage.urls_upload == []  # nenhuma URL emitida fora da janela
    acao(client, auth, c.aut, rid, "autorizar")
    r = post(client, auth, c.aut, rid, "checklist/chegada", BASE)  # autorizada: chegada não
    assert r.status_code == 409 and cod(r) == "checklist_fora_da_janela"


def test_checklist_duplicado(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    assert post(client, auth, c.aut, rid, "checklist/saida", BASE).status_code == 201
    r = post(client, auth, c.aut, rid, "checklist/saida", BASE)
    assert r.status_code == 409 and cod(r) == "checklist_ja_registrado"
    r = post(client, auth, c.aut, rid, "checklist/saida/fotos", {"mime": "image/png", "tamanho": 5})
    assert r.status_code == 409


def test_momento_invalido(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    assert post(client, auth, c.aut, rid, "checklist/meio", BASE).status_code == 400


# ------------------------------------------------------------------ upload: MIME, tamanho, limite
@pytest.mark.parametrize(
    "corpo",
    [
        {"mime": "image/gif", "tamanho": 10},
        {"mime": "text/html", "tamanho": 10},
        {"mime": "image/png", "tamanho": 5 * 1024 * 1024 + 1},
        {"mime": "image/png", "tamanho": 0},
        {"mime": "image/png", "tamanho": "10"},
        {"mime": "image/png", "tamanho": True},
        {"mime": "image/png"},
        {"mime": "image/png", "tamanho": 10, "caminho": "../x"},
        [1],
    ],
)
def test_upload_recusa_corpo_invalido(client, auth, c, repo, storage, corpo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    assert post(client, auth, c.aut, rid, "checklist/saida/fotos", corpo).status_code == 400
    assert storage.urls_upload == []


def test_upload_aceita_5mb_e_devolve_caminho_do_banco(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    r = post(
        client,
        auth,
        c.aut,
        rid,
        "checklist/saida/fotos",
        {"mime": "image/webp", "tamanho": 5 * 1024 * 1024},
    )
    j = r.get_json()
    assert r.status_code == 201
    assert j["caminho"] == f"{rid}/saida/{j['foto_id']}.webp"
    assert j["url_upload"].startswith("https://") and j["token"]


def test_upload_limita_objetos_na_pasta(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    for _ in range(12):
        storage.enviar(f"{rid}/saida/{uuid.uuid4()}.jpg", "image/jpeg", 10)
    r = post(client, auth, c.aut, rid, "checklist/saida/fotos", {"mime": "image/png", "tamanho": 5})
    assert r.status_code == 400 and cod(r) == "limite_fotos"


# ------------------------------------------------------------------ registro: validação
@pytest.mark.parametrize(
    "corpo",
    [
        {"avarias": False},
        {"combustivel": "cheio"},
        {"combustivel": "lotado", "avarias": False},
        {"combustivel": "meio", "avarias": "false"},
        {"combustivel": "meio", "avarias": 0},
        {"combustivel": "meio", "avarias": True},
        {"combustivel": "meio", "avarias": True, "descricao_avarias": "   "},
        {"combustivel": "meio", "avarias": True, "descricao_avarias": "x" * 501},
        {"combustivel": "meio", "avarias": False, "descricao_avarias": "arranhão"},
        {"combustivel": "meio", "avarias": False, "descricao_avarias": 5},
        {**BASE, "fotos": "abc"},
        {**BASE, "fotos": [{"id": "nao-uuid", "mime": "image/png", "tamanho": 1}]},
        {**BASE, "fotos": [{"id": str(uuid.uuid4()), "mime": "image/png", "tamanho": 1, "x": 1}]},
        {**BASE, "extra": 1},
        {**BASE, "registrado_por": "x"},
    ],
)
def test_registro_recusa_invalido(client, auth, c, repo, corpo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    assert post(client, auth, c.aut, rid, "checklist/saida", corpo).status_code == 400
    assert repo.chamadas_rpc == []


def test_registro_mais_de_6_fotos_e_duplicadas(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    fotos = [foto(client, auth, c.aut, rid, storage) for _ in range(7)]
    assert (
        post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": fotos}).status_code
        == 400
    )
    dup = fotos[:2] + fotos[:1]
    assert (
        post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": dup}).status_code == 400
    )
    r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": fotos[:6]})
    assert r.status_code == 201 and r.get_json()["checklist"]["fotos"] == 6


def test_descricao_normalizada_nfc_e_sem_controle(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    decomposto = "arranha" + chr(0x63) + chr(0x327) + chr(0xE3) + "o"  # c + cedilha combinante
    bruto = "  " + decomposto + chr(0) + chr(7) + " na porta  "
    corpo = {"combustivel": "cheio", "avarias": True, "descricao_avarias": bruto}
    assert post(client, auth, c.aut, rid, "checklist/saida", corpo).status_code == 201
    esperado = "arranha" + chr(0xE7) + chr(0xE3) + "o na porta"
    assert repo.checklists[(rid, "saida")]["descricao_avarias"] == esperado


# ------------------------------------------------------------------ fotos no Storage
def test_foto_inexistente_no_storage(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = {"id": str(uuid.uuid4()), "mime": "image/png", "tamanho": 10}
    r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [f]})
    assert r.status_code == 400 and cod(r) == "foto_nao_enviada"
    assert repo.chamadas_rpc == []


@pytest.mark.parametrize(
    ("enviado_mime", "enviado_tam"), [("image/png", 1000), ("image/jpeg", 999)]
)
def test_foto_com_metadados_divergentes_e_removida(  # type: ignore[no-untyped-def]
    client, auth, c, repo, storage, enviado_mime, enviado_tam
):
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    caminho = f"{rid}/saida/{f['id']}.jpg"
    storage.enviar(caminho, enviado_mime, enviado_tam)
    r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [f]})
    assert r.status_code == 400 and cod(r) == "foto_divergente"
    assert storage.removidos == [caminho] and repo.chamadas_rpc == []


def test_foto_com_assinatura_falsa_e_removida(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    caminho = f"{rid}/saida/{f['id']}.jpg"
    storage.enviar(caminho, "image/jpeg", 1000, inicio=b"<html><script>")
    r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [f]})
    assert r.status_code == 400 and cod(r) == "foto_invalida"
    assert storage.removidos == [caminho] and repo.chamadas_rpc == []


def test_foto_de_outra_requisicao_nao_vale(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    outro_id = str(uuid.uuid4())
    f = {"id": str(uuid.uuid4()), "mime": "image/jpeg", "tamanho": 10}
    storage.enviar(f"{outro_id}/saida/{f['id']}.jpg", "image/jpeg", 10)
    r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [f]})
    assert cod(r) == "foto_nao_enviada"


def test_storage_indisponivel_da_503(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    storage.indisponivel = True
    r = post(client, auth, c.aut, rid, "checklist/saida/fotos", {"mime": "image/png", "tamanho": 5})
    assert r.status_code == 503


# ------------------------------------------------------------------ permissões e IDOR
def test_solicitante_nao_registra_nem_pede_upload(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    for caminho, corpo in (
        ("checklist/saida", BASE),
        ("checklist/saida/fotos", {"mime": "image/png", "tamanho": 5}),
    ):
        assert post(client, auth, c.sol, rid, caminho, corpo).status_code == 403
        assert post(client, auth, c.outro, rid, caminho, corpo).status_code == 403
    assert repo.chamadas_rpc == []


def test_sem_token_401(client, c):  # type: ignore[no-untyped-def]
    assert client.get(f"/api/requisicoes/{uuid.uuid4()}/checklist").status_code == 401


def test_leitura_so_staff_dono_403_alheio_404(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    corpo = {"combustivel": "meio", "avarias": True, "descricao_avarias": SEGREDO, "fotos": [f]}
    assert post(client, auth, c.aut, rid, "checklist/saida", corpo).status_code == 201
    url = f"/api/requisicoes/{rid}/checklist"
    assert client.get(url, headers=auth(c.sol.id)).status_code == 403  # dono
    r = client.get(url, headers=auth(c.outro.id))  # alheio
    assert r.status_code == 404 and cod(r) == "nao_encontrado"
    assert SEGREDO not in r.get_data(as_text=True)
    assert storage.urls_leitura == []  # nenhuma URL assinada para quem não pode
    for quem in (c.aut, c.adm):
        r = client.get(url, headers=auth(quem.id))
        assert r.status_code == 200


def test_leitura_devolve_urls_curtas_e_dados(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    corpo = {"combustivel": "cheio", "avarias": True, "descricao_avarias": "risco", "fotos": [f]}
    post(client, auth, c.aut, rid, "checklist/saida", corpo)
    j = client.get(f"/api/requisicoes/{rid}/checklist", headers=auth(c.adm.id)).get_json()
    assert j["checklists"]["chegada"] is None and j["url_expira_em_segundos"] == 300
    s = j["checklists"]["saida"]
    assert (s["combustivel"], s["avarias"], s["descricao_avarias"]) == ("cheio", True, "risco")
    assert s["registrado_por_nome"] == c.aut.nome
    assert s["fotos"][0]["url"].startswith("https://") and s["fotos"][0]["posicao"] == 1
    assert storage.urls_leitura == [(f"{rid}/saida/{f['id']}.jpg", 300)]


def test_requisicao_inexistente_ou_id_invalido(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    assert (
        client.get(f"/api/requisicoes/{uuid.uuid4()}/checklist", headers=auth(c.aut.id)).status_code
        == 404
    )
    assert client.get("/api/requisicoes/xx/checklist", headers=auth(c.aut.id)).status_code == 400


# ------------------------------------------------------------------ tradução de erros do banco
def test_ag013_ag014_traduzidos_sem_detalhes(client, auth, c, repo, monkeypatch):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    # força a corrida: o banco recusa por AG013 mesmo com o service achando que há checklist
    monkeypatch.setattr(repo, "momentos_checklist", lambda ids: {i: {"saida"} for i in ids})
    r = acao(client, auth, c.aut, rid, "saida")
    assert r.status_code == 409 and cod(r) == "checklist_obrigatorio"
    assert "saída" in r.get_json()["erro"]["mensagem"]

    from postgrest.exceptions import APIError

    from app.repositorios.supabase_repo import traduzir_erro_banco

    def tr(codigo: str, texto: str):  # type: ignore[no-untyped-def]
        return traduzir_erro_banco(APIError({"message": texto, "code": codigo, "details": SEGREDO}))

    e = tr("AG013", "Checklist de chegada é obrigatório.")
    assert (e.codigo, e.status) == ("checklist_obrigatorio", 409) and "chegada" in e.mensagem
    e = tr("AG014", "Checklist de saida não pode ser registrado")
    assert e.codigo == "checklist_fora_da_janela"
    for codigo, texto, esperado in (
        (
            "23505",
            'unique constraint "uq_checklists_requisicao_momento"',
            "checklist_ja_registrado",
        ),
        (
            "23505",
            'unique constraint "uq_checklist_fotos_posicao" checklist_fotos',
            "checklist_invalido",
        ),
        ("23514", 'check "ck_checklists_avarias_descricao"', "checklist_invalido"),
        ("23514", 'check "ck_checklist_fotos_posicao"', "checklist_invalido"),
        ("23514", 'check "ck_checklist_fotos_mime"', "checklist_invalido"),
        ("22P02", "invalid input value for enum nivel_combustivel", "checklist_invalido"),
        ("AG003", "checklists é imutável", "checklist_imutavel"),
    ):
        e = tr(codigo, texto)
        assert e.codigo == esperado
        assert SEGREDO not in e.mensagem and "constraint" not in e.mensagem


def test_registro_ag014_pelo_banco_sem_vazar(client, auth, c, repo, monkeypatch):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    # a requisição muda de estado entre a checagem do service e a RPC
    original = repo.registrar_checklist

    def corrida(**kw):  # type: ignore[no-untyped-def]
        repo.requisicoes[rid]["status"] = "cancelada"
        return original(**kw)

    monkeypatch.setattr(repo, "registrar_checklist", corrida)
    r = post(client, auth, c.aut, rid, "checklist/saida", BASE)
    assert r.status_code == 409 and cod(r) == "checklist_fora_da_janela"


# ------------------------------------------------------------------ concorrência
def test_concorrencia_dois_registros_so_um_persiste(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    codigos: list[int] = []
    barreira = threading.Barrier(2)

    def tenta() -> None:
        cli = client.application.test_client()
        barreira.wait()
        codigos.append(post(cli, auth, c.aut, rid, "checklist/saida", BASE).status_code)

    ts = [threading.Thread(target=tenta) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(codigos)[0] == 201 and len(repo.checklists) == 1
    assert sorted(codigos)[1] == 409


# ------------------------------------------------------------------ LGPD: sem PII em log
def test_descricao_e_urls_nunca_vao_para_log(client, auth, c, repo, storage, caplog):  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    corpo = {"combustivel": "meio", "avarias": True, "descricao_avarias": SEGREDO, "fotos": [f]}
    assert post(client, auth, c.aut, rid, "checklist/saida", corpo).status_code == 201
    client.get(f"/api/requisicoes/{rid}/checklist", headers=auth(c.aut.id))
    storage.indisponivel = True
    client.get(f"/api/requisicoes/{rid}/checklist", headers=auth(c.aut.id))
    assert SEGREDO not in caplog.text and "tok-" not in caplog.text
    assert "storage.exemplo" not in caplog.text
    assert SEGREDO not in str(repo.auditoria_checklist) and SEGREDO not in str(repo.auditoria)
