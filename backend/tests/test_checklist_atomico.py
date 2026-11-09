"""N-01: registro de várias fotos atômico no Storage (valida tudo antes de regravar), retentativa
idempotente após falha do banco e proteção contra remoção de foto com registro em andamento."""

from __future__ import annotations

import threading
from typing import Any

from app.services.checklist import EM_REGISTRO
from tests.fakes_admin import erro_db
from tests.test_checklist import BASE, LIGADO, cod, foto, nova, post


def _caminho(rid: str, f: dict[str, Any]) -> str:
    return f"{rid}/saida/{f['id']}.jpg"


def _seis(client, auth, c, rid, storage) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return [foto(client, auth, c.aut, rid, storage) for _ in range(6)]


def test_quarta_invalida_nao_regrava_nenhuma_e_retentativa_sem_ela_funciona(  # type: ignore[no-untyped-def]
    client, auth, c, repo, storage
):
    repo.config = LIGADO
    rid = nova(client, auth, c)
    fotos = _seis(client, auth, c, rid, storage)
    ruim = fotos[3]
    storage.enviar(
        _caminho(rid, ruim), "image/jpeg", ruim["tamanho"], dados=b"\xff\xd8\xff" + b"x" * 40
    )
    r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": fotos})
    assert r.status_code == 400 and cod(r) == "foto_invalida"
    assert "A foto 4 " in r.get_json()["erro"]["mensagem"]
    assert storage.sobrescritos == []  # nada foi regravado
    assert storage.removidos == [_caminho(rid, ruim)]  # só a ruim saiu
    assert repo.chamadas_rpc == []
    # retentativa sem a 4ª: as 5 válidas ainda têm o tamanho declarado original
    boas = [f for f in fotos if f is not ruim]
    r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": boas})
    assert r.status_code == 201, r.get_json()
    assert sorted(storage.sobrescritos) == sorted(_caminho(rid, f) for f in boas)
    assert len(repo.checklists[(rid, "saida")]["fotos"]) == 5


def test_divergente_na_ultima_tambem_nao_regrava_as_anteriores(  # type: ignore[no-untyped-def]
    client, auth, c, repo, storage
):
    repo.config = LIGADO
    rid = nova(client, auth, c)
    fotos = _seis(client, auth, c, rid, storage)
    fotos[5] = {**fotos[5], "tamanho": fotos[5]["tamanho"] + 1}
    r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": fotos})
    assert cod(r) == "foto_divergente" and storage.sobrescritos == []


def test_retentativa_apos_falha_do_banco_e_idempotente(  # type: ignore[no-untyped-def]
    client, auth, c, repo, storage, monkeypatch
):
    repo.config = LIGADO
    rid = nova(client, auth, c)
    fotos = [foto(client, auth, c.aut, rid, storage) for _ in range(2)]
    real = repo.registrar_checklist
    falhas = {"n": 0}

    def banco_cai_uma_vez(**kw: Any) -> str:
        if falhas["n"] == 0:
            falhas["n"] += 1
            raise erro_db("XX000", "falha transitória")
        return real(**kw)

    monkeypatch.setattr(repo, "registrar_checklist", banco_cai_uma_vez)
    corpo = {**BASE, "fotos": fotos}
    r1 = post(client, auth, c.aut, rid, "checklist/saida", corpo)
    assert r1.status_code >= 400 and (rid, "saida") not in repo.checklists
    assert len(storage.sobrescritos) == 2  # sanitizadas antes do insert
    gravados = {k: len(v) for k, v in storage.conteudos.items()}
    r2 = post(client, auth, c.aut, rid, "checklist/saida", corpo)  # mesmo corpo (tamanho original)
    assert r2.status_code == 201, r2.get_json()
    assert len(storage.sobrescritos) == 2  # não recodificou de novo
    assert storage.removidos == []
    registradas = {f["id"]: f["tamanho_bytes"] for f in repo.checklists[(rid, "saida")]["fotos"]}
    assert registradas == {f["id"]: gravados[_caminho(rid, f)] for f in fotos}


def test_retentativa_aceita_tambem_o_tamanho_sanitizado_declarado(  # type: ignore[no-untyped-def]
    client, auth, c, repo, storage, monkeypatch
):
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    real = repo.registrar_checklist
    estado = {"cai": True}

    def banco(**kw: Any) -> str:
        if estado["cai"]:
            estado["cai"] = False
            raise erro_db("XX000", "falha")
        return real(**kw)

    monkeypatch.setattr(repo, "registrar_checklist", banco)
    assert (
        post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [f]}).status_code >= 400
    )
    novo = storage.objetos[_caminho(rid, f)]["tamanho"]
    r = post(
        client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [{**f, "tamanho": novo}]}
    )
    assert r.status_code == 201


def test_tamanho_arbitrario_continua_divergente_apos_sanitizar(  # type: ignore[no-untyped-def]
    client, auth, c, repo, storage, monkeypatch
):
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    estado = {"cai": True}
    real = repo.registrar_checklist

    def banco(**kw: Any) -> str:
        if estado["cai"]:
            estado["cai"] = False
            raise erro_db("XX000", "falha")
        return real(**kw)

    monkeypatch.setattr(repo, "registrar_checklist", banco)
    post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [f]})
    r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [{**f, "tamanho": 7}]})
    assert cod(r) == "foto_divergente"


def test_registro_em_andamento_protege_foto_da_remocao_por_outro_pedido(  # type: ignore[no-untyped-def]
    client, auth, c, repo, storage
):
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    caminho = _caminho(rid, f)
    divergente = {**f, "tamanho": f["tamanho"] + 1}
    corpo = {**BASE, "fotos": [divergente]}
    with EM_REGISTRO.reivindicar([caminho]):  # outro pedido já está registrando esta foto
        r = post(client, auth, c.aut, rid, "checklist/saida", corpo)
        assert cod(r) == "foto_divergente"
        assert storage.removidos == [] and caminho in storage.objetos
    r = post(client, auth, c.aut, rid, "checklist/saida", corpo)  # sem registro em andamento
    assert cod(r) == "foto_divergente" and storage.removidos == [caminho]


def test_claims_sao_liberadas_apos_o_pedido(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [{**f, "tamanho": 1}]})
    post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [f]})
    assert EM_REGISTRO._contagem == {}


def test_concorrencia_mesmo_foto_id_um_registra_e_objeto_permanece(  # type: ignore[no-untyped-def]
    client, auth, c, repo, storage
):
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    caminho = _caminho(rid, f)
    corpos = [{**BASE, "fotos": [f]}, {**BASE, "fotos": [f]}]  # mesmo foto_id nos dois
    resultados: list[tuple[int, str | None]] = []
    barreira = threading.Barrier(2)

    def tenta(corpo: dict[str, Any]) -> None:
        cli = client.application.test_client()
        barreira.wait()
        r = post(cli, auth, c.aut, rid, "checklist/saida", corpo)
        resultados.append((r.status_code, None if r.status_code == 201 else cod(r)))

    ts = [threading.Thread(target=tenta, args=(b,)) for b in corpos]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(repo.checklists) == 1 and len(repo.checklists[(rid, "saida")]["fotos"]) == 1
    assert sorted(s for s, _ in resultados) == [201, 409]
    assert caminho in storage.objetos and storage.removidos == []  # nunca apaga o registrado
