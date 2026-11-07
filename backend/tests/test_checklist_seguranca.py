"""Checklist: corrida na remoção de fotos (M1), teto de emissões (M3), rate limit por usuário
(M3) e mensagem de campos extras (B5)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from app import create_app
from app.auth.jwt import VerificadorJWT
from app.config import Config
from app.extensions import limiter
from app.services.checklist import LimiteEmissao
from tests.conftest import _CHAVE, AUDIENCE, ISSUER, emitir_token
from tests.test_checklist import BASE, LIGADO, cod, foto, nova, post
from tests.test_requisicoes import acao, criar

UPLOAD = {"mime": "image/png", "tamanho": 5}


def _cliente(repo, storage, **cfg):  # type: ignore[no-untyped-def]
    """App com limites customizados; devolve (client, função de cabeçalho de auth)."""
    config = Config(jwt_issuer=ISSUER, jwt_audience=AUDIENCE, testing=True, **cfg)
    ver = VerificadorJWT(
        issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
    )
    app = create_app(config, repo=repo, verificador=ver, armazenamento=storage)
    limiter.reset()

    def hdr(perfil: Any) -> dict[str, str]:
        return {"Authorization": f"Bearer {emitir_token(perfil.id)}"}

    return app.test_client(), hdr


# ------------------------------------------------------------------ M1: remoção na corrida
def test_perdedor_da_corrida_nao_apaga_foto_do_vencedor(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    """A registra (válido) enquanto B, com os MESMOS ids mas tamanho divergente, ainda confere.

    B reprovaria o objeto e o removeria; como A já o registrou, o objeto deve permanecer.
    """
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    caminho = f"{rid}/saida/{f['id']}.jpg"
    divergente = {**f, "tamanho": f["tamanho"] + 1}
    resultados: dict[str, int] = {}
    original = storage.metadados
    disparado = threading.Event()

    def metadados_com_corrida(cam: str) -> Any:
        if not disparado.is_set():  # 1ª conferência é a de B: A registra antes de B seguir
            disparado.set()

            def registra_a() -> None:
                cli = client.application.test_client()
                r = post(cli, auth, c.adm, rid, "checklist/saida", {**BASE, "fotos": [f]})
                resultados["a"] = r.status_code

            t = threading.Thread(target=registra_a)
            t.start()
            t.join()
        return original(cam)

    storage.metadados = metadados_com_corrida  # type: ignore[method-assign]
    r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, "fotos": [divergente]})
    assert resultados["a"] == 201
    assert r.status_code == 400 and cod(r) == "foto_divergente"
    assert storage.removidos == [] and caminho in storage.objetos
    assert [x["id"] for x in repo.checklists[(rid, "saida")]["fotos"]] == [f["id"]]


def test_foto_reprovada_sem_checklist_continua_sendo_removida(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    corpo = {**BASE, "fotos": [{**f, "tamanho": 1}]}
    r = post(client, auth, c.aut, rid, "checklist/saida", corpo)
    assert cod(r) == "foto_divergente" and storage.removidos == [f"{rid}/saida/{f['id']}.jpg"]


def test_nao_remove_foto_que_ja_consta_em_checklist_fotos(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    """Mesmo que o momento não conste (leitura defasada), o id em checklist_fotos protege."""
    repo.config = LIGADO
    rid = nova(client, auth, c)
    f = foto(client, auth, c.aut, rid, storage)
    repo.momentos_checklist = lambda ids: {}  # type: ignore[method-assign]
    repo.obter_checklists = lambda _r: [{"momento": "chegada", "fotos": [{"id": f["id"]}]}]  # type: ignore[method-assign]
    corpo = {**BASE, "fotos": [{**f, "tamanho": 1}]}
    r = post(client, auth, c.aut, rid, "checklist/saida", corpo)
    assert cod(r) == "foto_divergente" and storage.removidos == []


# ------------------------------------------------------------------ M3: teto de emissões
def test_teto_de_urls_emitidas_por_momento(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    for _ in range(12):  # nenhuma foto é enviada: só a contagem de emissões segura
        assert post(client, auth, c.aut, rid, "checklist/saida/fotos", UPLOAD).status_code == 201
    r = post(client, auth, c.aut, rid, "checklist/saida/fotos", UPLOAD)
    assert r.status_code == 400 and cod(r) == "limite_fotos"
    assert len(storage.urls_upload) == 12


def test_teto_e_por_requisicao_e_momento(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    for _ in range(12):
        post(client, auth, c.aut, rid, "checklist/saida/fotos", UPLOAD)
    # outra requisição (outro veículo): cota própria
    veiculo2 = repo.novo_veiculo()
    outra = criar(client, auth, c.sol, veiculo2).get_json()["id"]
    assert acao(client, auth, c.aut, outra, "autorizar").status_code == 200
    assert post(client, auth, c.aut, outra, "checklist/saida/fotos", UPLOAD).status_code == 201


def test_emissao_com_falha_no_storage_nao_consome_cota(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    storage.indisponivel = True
    for _ in range(15):
        assert post(client, auth, c.aut, rid, "checklist/saida/fotos", UPLOAD).status_code == 503
    storage.indisponivel = False
    assert post(client, auth, c.aut, rid, "checklist/saida/fotos", UPLOAD).status_code == 201


def test_teto_configuravel(repo, storage, c):  # type: ignore[no-untyped-def]
    cli, hdr = _cliente(repo, storage, max_urls_upload_por_momento=2)
    repo.config = LIGADO
    rid = nova(cli, hdr_fn(hdr), c)
    codigos = [
        post(cli, hdr_fn(hdr), c.aut, rid, "checklist/saida/fotos", UPLOAD).status_code
        for _ in range(3)
    ]
    assert codigos == [201, 201, 400]


def hdr_fn(hdr: Callable[[Any], dict[str, str]]) -> Callable[[str], dict[str, str]]:
    """Adapta `hdr(perfil)` à assinatura `auth(id)` usada pelos helpers de test_checklist."""
    return lambda sub: {"Authorization": f"Bearer {emitir_token(sub)}"}


def test_limite_emissao_expira_com_ttl() -> None:
    lim = LimiteEmissao(ttl=0)  # tudo expira de imediato
    for _ in range(5):
        assert lim.reservar(("r", "saida"), 1)
    lim = LimiteEmissao(ttl=3600)
    assert lim.reservar(("r", "saida"), 1) and not lim.reservar(("r", "saida"), 1)
    assert lim.reservar(("r", "chegada"), 1)


# ------------------------------------------------------------------ M3: rate limit por usuário
def test_rate_limit_de_upload_e_por_usuario_nao_por_ip(repo, storage, c):  # type: ignore[no-untyped-def]
    cli, hdr = _cliente(repo, storage, limite_upload_fotos="2 per minute")
    repo.config = LIGADO
    rid = nova(cli, hdr_fn(hdr), c)
    outro_aut = repo.novo_perfil("autorizador")
    url = f"/api/requisicoes/{rid}/checklist/saida/fotos"
    # mesmo IP para todos: quem corta é o limite por usuário
    r = [cli.post(url, headers=hdr(c.aut), json=UPLOAD).status_code for _ in range(3)]
    assert r == [201, 201, 429]
    assert cli.post(url, headers=hdr(outro_aut), json=UPLOAD).status_code == 201


def test_rate_limit_de_upload_por_ip_como_segunda_camada(repo, storage, c):  # type: ignore[no-untyped-def]
    cli, hdr = _cliente(repo, storage, limite_upload_fotos_ip="2 per minute")
    repo.config = LIGADO
    rid = nova(cli, hdr_fn(hdr), c)
    outro_aut = repo.novo_perfil("autorizador")
    url = f"/api/requisicoes/{rid}/checklist/saida/fotos"
    assert cli.post(url, headers=hdr(c.aut), json=UPLOAD).status_code == 201
    assert cli.post(url, headers=hdr(outro_aut), json=UPLOAD).status_code == 201
    assert cli.post(url, headers=hdr(c.adm), json=UPLOAD).status_code == 429


def test_rate_limit_de_leitura_por_usuario(repo, storage, c):  # type: ignore[no-untyped-def]
    cli, hdr = _cliente(repo, storage, limite_leitura_fotos="2 per minute")
    repo.config = LIGADO
    rid = nova(cli, hdr_fn(hdr), c)
    url = f"/api/requisicoes/{rid}/checklist"
    codigos = [cli.get(url, headers=hdr(c.aut)).status_code for _ in range(3)]
    assert codigos == [200, 200, 429]
    assert cli.get(url, headers=hdr(c.adm)).status_code == 200


# ------------------------------------------------------------------ B5: campos extras
def test_campos_extras_nao_ecoam_chaves_do_cliente(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    chave = "Joao_Silva_" + "x" * 5000
    r = post(client, auth, c.aut, rid, "checklist/saida", {**BASE, chave: 1})
    assert r.status_code == 400
    texto = r.get_data(as_text=True)
    assert "Joao_Silva" not in texto and len(texto) < 400
    assert "1 campo(s)" in r.get_json()["erro"]["mensagem"]


# ------------------------------------------------------------------ B4: 23502 (not null)
def test_23502_traduzido_sem_detalhes(caplog) -> None:  # type: ignore[no-untyped-def]
    from postgrest.exceptions import APIError

    from app.repositorios.supabase_repo import traduzir_erro_banco

    segredo = "Joao Silva 61 99999-0000"
    e = traduzir_erro_banco(
        APIError({"message": 'null value in column "x"', "code": "23502", "details": segredo})
    )
    assert e.status == 400 and segredo not in e.mensagem and "column" not in e.mensagem
    assert segredo not in caplog.text


def test_storage_indisponivel_responde_503_indisponivel(client, auth, c, repo, storage):  # type: ignore[no-untyped-def]
    repo.config = LIGADO
    rid = nova(client, auth, c)
    storage.indisponivel = True
    r = post(client, auth, c.aut, rid, "checklist/saida/fotos", UPLOAD)
    assert r.status_code == 503 and cod(r) == "indisponivel"
