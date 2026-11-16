"""GET /api/atualizacoes: versões opacas por recurso e perfil (polling do tempo real).

A relevância por auditoria no banco é coberta por supabase/tests/test_atualizacoes_versao.py;
aqui: forma da resposta, anti-IDOR/canal lateral, ETag/304, 401, 429 e ausência de registro de
acesso.
"""

from __future__ import annotations

import re
import uuid

import pytest

from app.modelos import Configuracoes
from app.services.atualizacoes import (
    RECURSOS_COMUNS,
    CacheConfiguracoes,
    ServicoAtualizacoes,
    derivar_chave,
    etag_confere,
)
from tests.conftest import emitir_token
from tests.test_permissoes_arquivamento import levar_a
from tests.test_requisicoes import acao

URL = "/api/atualizacoes"
HEX16 = re.compile(r"^[0-9a-f]{16}$")
MOTORISTA = Configuracoes(motorista=True, motorista_localizacao=True, motorista_registra=True)


def versoes(client, auth, quem) -> dict:  # type: ignore[no-untyped-def]
    r = client.get(URL, headers=auth(quem.id))
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def recursos(client, auth, quem) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return versoes(client, auth, quem)["recursos"]  # type: ignore[no-any-return]


# -- forma da resposta ---------------------------------------------------------------------
@pytest.mark.parametrize("papel", ["solicitante", "autorizador", "motorista", "vigilante"])
def test_forma_sem_usuarios_para_nao_admin(client, auth, repo, papel):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil(papel)
    j = versoes(client, auth, p)
    assert set(j) == {"recursos", "nao_lidas"}
    assert set(j["recursos"]) == set(RECURSOS_COMUNS)
    assert all(HEX16.match(v) for v in j["recursos"].values())
    assert j["nao_lidas"] == 0


def test_forma_admin_tem_usuarios(client, auth, c):  # type: ignore[no-untyped-def]
    j = versoes(client, auth, c.adm)
    assert set(j["recursos"]) == {*RECURSOS_COMUNS, "usuarios"}
    assert all(HEX16.match(v) for v in j["recursos"].values())


def test_valor_e_opaco_e_por_perfil(client, auth, repo, c):  # type: ignore[no-untyped-def]
    """Mesmo estado, perfis diferentes: valores diferentes (o hash inclui o perfil)."""
    a = recursos(client, auth, c.sol)
    b = recursos(client, auth, c.outro)
    assert a["veiculos"] != b["veiculos"] and a["configuracoes"] != b["configuracoes"]
    # Estável entre chamadas sem mudança.
    assert recursos(client, auth, c.sol) == a


def test_sem_dado_pessoal_nem_id_cru(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "autorizada")
    texto = client.get(URL, headers=auth(c.adm.id)).get_data(as_text=True)
    for proibido in (rid, c.sol.id, c.sol.nome, c.adm.id, "@"):
        assert proibido not in texto


# -- requisições: anti-IDOR / canal lateral --------------------------------------------------
def test_solicitante_nao_ve_versao_mudar_por_requisicao_alheia(client, auth, repo, c):  # type: ignore[no-untyped-def]
    outro_veiculo = repo.novo_veiculo()
    antes_a = recursos(client, auth, c.sol)["requisicoes"]
    antes_aut = recursos(client, auth, c.aut)["requisicoes"]
    rid_b = levar_a(client, auth, repo, c, "solicitada", dono=c.outro, veiculo=outro_veiculo)
    assert recursos(client, auth, c.sol)["requisicoes"] == antes_a
    assert recursos(client, auth, c.aut)["requisicoes"] != antes_aut  # a equipe vê tudo
    for nome in ("autorizar", "saida", "chegada"):
        assert acao(client, auth, c.aut, rid_b, nome).status_code == 200
        assert recursos(client, auth, c.sol)["requisicoes"] == antes_a
    # B vê mudar a própria.
    antes_b = recursos(client, auth, c.outro)["requisicoes"]
    assert (
        client.post(f"/api/requisicoes/{rid_b}/arquivar", headers=auth(c.outro.id)).status_code
        == 200
    )
    assert recursos(client, auth, c.outro)["requisicoes"] != antes_b
    assert recursos(client, auth, c.sol)["requisicoes"] == antes_a  # arquivamento é pessoal


def test_solicitante_ve_mudar_a_propria(client, auth, repo, c):  # type: ignore[no-untyped-def]
    rid = levar_a(client, auth, repo, c, "solicitada")
    antes = recursos(client, auth, c.sol)["requisicoes"]
    assert acao(client, auth, c.aut, rid, "autorizar").status_code == 200
    assert recursos(client, auth, c.sol)["requisicoes"] != antes


def test_motorista_so_as_atribuidas(client, auth, repo, c):  # type: ignore[no-untyped-def]
    repo.config = MOTORISTA
    m1 = repo.novo_perfil("motorista")
    m2 = repo.novo_perfil("motorista")
    v2 = repo.novo_veiculo()
    antes_m1 = recursos(client, auth, m1)["requisicoes"]
    antes_m2 = recursos(client, auth, m2)["requisicoes"]
    rid = levar_a(client, auth, repo, c, "autorizada", motorista=m1)
    assert recursos(client, auth, m1)["requisicoes"] != antes_m1
    assert recursos(client, auth, m2)["requisicoes"] == antes_m2
    # Requisição sem motorista (outro veículo): nenhum dos dois vê mudar.
    meio_m1 = recursos(client, auth, m1)["requisicoes"]
    levar_a(client, auth, repo, c, "autorizada", dono=c.outro, veiculo=v2)
    assert recursos(client, auth, m1)["requisicoes"] == meio_m1
    assert recursos(client, auth, m2)["requisicoes"] == antes_m2
    assert rid


def test_vigilante_ve_autorizadas_mas_nao_solicitadas_alheias(client, auth, repo, c):  # type: ignore[no-untyped-def]
    vig = repo.novo_perfil("vigilante")
    antes = recursos(client, auth, vig)["requisicoes"]
    rid = levar_a(client, auth, repo, c, "solicitada")
    assert recursos(client, auth, vig)["requisicoes"] == antes
    assert acao(client, auth, c.aut, rid, "autorizar").status_code == 200
    assert recursos(client, auth, vig)["requisicoes"] != antes


# -- usuários: só admin ----------------------------------------------------------------------
def test_usuarios_so_admin_e_muda_com_perfil(client, auth, repo, c):  # type: ignore[no-untyped-def]
    antes = recursos(client, auth, c.adm)["usuarios"]
    repo.novo_perfil("solicitante")
    assert recursos(client, auth, c.adm)["usuarios"] != antes
    for p in (c.sol, c.aut):
        assert "usuarios" not in recursos(client, auth, p)


# -- leitura não muda; marcar lida muda ------------------------------------------------------
def test_leitura_de_localizacao_nao_muda_versao(client, auth, repo, c):  # type: ignore[no-untyped-def]
    repo.config = MOTORISTA
    m = repo.novo_perfil("motorista")
    rid = levar_a(client, auth, repo, c, "autorizada", motorista=m)
    corpo = {"latitude": -15.79, "longitude": -47.88}
    r = client.post(f"/api/requisicoes/{rid}/saida", headers=auth(m.id), json=corpo)
    assert r.status_code == 200, r.get_json()
    antes = {p.id: recursos(client, auth, p) for p in (c.adm, c.sol, m)}
    r = client.get(f"/api/admin/requisicoes/{rid}/localizacoes", headers=auth(c.adm.id))
    assert r.status_code == 200, r.get_json()
    assert {p.id: recursos(client, auth, p) for p in (c.adm, c.sol, m)} == antes


def test_marcar_notificacao_lida_muda_versao_e_nao_lidas(client, auth, repo, c):  # type: ignore[no-untyped-def]
    n = repo.nova_notificacao(c.sol.id)
    j = versoes(client, auth, c.sol)
    assert j["nao_lidas"] == 1
    outro_antes = recursos(client, auth, c.outro)["notificacoes"]
    r = client.post(f"/api/notificacoes/{n['id']}/lida", headers=auth(c.sol.id))
    assert r.status_code == 200
    depois = versoes(client, auth, c.sol)
    assert depois["nao_lidas"] == 0
    assert depois["recursos"]["notificacoes"] != j["recursos"]["notificacoes"]
    assert recursos(client, auth, c.outro)["notificacoes"] == outro_antes


def test_notificacao_nova_de_outro_usuario_nao_muda(client, auth, repo, c):  # type: ignore[no-untyped-def]
    antes = versoes(client, auth, c.sol)
    repo.nova_notificacao(c.outro.id)
    assert versoes(client, auth, c.sol) == antes


def test_configuracoes_e_veiculos_mudam(client, auth, repo, c):  # type: ignore[no-untyped-def]
    antes = recursos(client, auth, c.sol)
    r = client.put("/api/admin/configuracoes", headers=auth(c.adm.id), json={"checklist": True})
    assert r.status_code == 200, r.get_json()
    depois = recursos(client, auth, c.sol)
    assert depois["configuracoes"] != antes["configuracoes"]
    repo.novo_veiculo()
    assert recursos(client, auth, c.sol)["veiculos"] != depois["veiculos"]


# -- ETag / 304 -------------------------------------------------------------------------------
def test_etag_e_304(client, auth, repo, c):  # type: ignore[no-untyped-def]
    r = client.get(URL, headers=auth(c.sol.id))
    etag = r.headers["ETag"]
    assert etag.startswith('"') and r.headers["Cache-Control"] == "no-store"
    r2 = client.get(URL, headers={**auth(c.sol.id), "If-None-Match": etag})
    assert r2.status_code == 304 and r2.get_data() == b""
    assert r2.headers["ETag"] == etag and r2.headers["Cache-Control"] == "no-store"
    fraco = client.get(URL, headers={**auth(c.sol.id), "If-None-Match": f"W/{etag}"})
    assert fraco.status_code == 304
    levar_a(client, auth, repo, c, "solicitada")
    r3 = client.get(URL, headers={**auth(c.sol.id), "If-None-Match": etag})
    assert r3.status_code == 200 and r3.headers["ETag"] != etag


def test_etag_de_outro_perfil_nao_casa(client, auth, c):  # type: ignore[no-untyped-def]
    etag = client.get(URL, headers=auth(c.sol.id)).headers["ETag"]
    r = client.get(URL, headers={**auth(c.outro.id), "If-None-Match": etag})
    assert r.status_code == 200


def test_etag_confere_lista_e_curinga():
    assert etag_confere('"a", "b"', '"b"')
    assert etag_confere("*", '"x"')
    assert not etag_confere(None, '"x"') and not etag_confere('"y"', '"x"')


# -- autenticação, rate limit e registro de acesso -------------------------------------------
def test_401_sem_token(client):  # type: ignore[no-untyped-def]
    r = client.get(URL)
    assert r.status_code == 401
    assert r.get_json()["erro"]["codigo"]


def test_conta_inativa_403(client, auth, repo):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil("solicitante", ativo=False)
    r = client.get(URL, headers=auth(p.id))
    assert r.status_code == 403 and r.get_json()["erro"]["codigo"] == "conta_inativa"


def test_mfa_continua_exigido(client, auth, auth_aal2, repo, c):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(mfa=True)
    r = client.get(URL, headers=auth(c.aut.id))
    assert r.status_code == 403 and r.get_json()["erro"]["codigo"] == "mfa_requerido"
    assert client.get(URL, headers=auth_aal2(c.aut.id)).status_code == 200
    assert client.get(URL, headers=auth(c.sol.id)).status_code == 200  # solicitante só senha


def test_429_por_usuario(client, auth, c):  # type: ignore[no-untyped-def]
    for _ in range(30):
        assert client.get(URL, headers=auth(c.sol.id)).status_code == 200
    r = client.get(URL, headers=auth(c.sol.id))
    assert r.status_code == 429
    assert r.get_json()["erro"]["codigo"] == "limite_excedido"
    # Outro usuário (mesmo IP) não é afetado: a chave é o perfil.
    assert client.get(URL, headers=auth(c.outro.id)).status_code == 200


def test_nao_registra_acesso(client, repo, c):  # type: ignore[no-untyped-def]
    cab = {
        "Authorization": f"Bearer {emitir_token(c.sol.id, extra={'session_id': str(uuid.uuid4())})}"
    }
    for _ in range(3):
        assert client.get(URL, headers=cab).status_code == 200
    assert repo.chamadas_registro == 0
    # Controle: uma rota comum registra a mesma sessão.
    assert client.get("/api/eu", headers=cab).status_code == 200
    assert repo.chamadas_registro == 1


# -- unidades do serviço -----------------------------------------------------------------------
def test_chave_derivada_da_service_role_e_estavel():
    assert derivar_chave("abc") == derivar_chave("abc") != derivar_chave("abd")
    assert derivar_chave("") == derivar_chave("")  # fallback por processo
    assert derivar_chave("") != derivar_chave("abc")


def test_marcador_diferente_da_versao_e_hash(repo):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil("solicitante")
    s1 = ServicoAtualizacoes(repo, b"k1").versoes(p)
    s2 = ServicoAtualizacoes(repo, b"k2").versoes(p)
    assert s1["recursos"] != s2["recursos"]  # depende do segredo do servidor


def test_cache_configuracoes_ttl(repo):  # type: ignore[no-untyped-def]
    agora = [100.0]
    cache = CacheConfiguracoes(ttl=5, relogio=lambda: agora[0])
    leituras = []
    original = repo.ler_configuracoes

    def contar():  # type: ignore[no-untyped-def]
        leituras.append(1)
        return original()

    repo.ler_configuracoes = contar
    cache.ler(repo)
    cache.ler(repo)
    assert len(leituras) == 1
    agora[0] += 5.1
    cache.ler(repo)
    assert len(leituras) == 2
