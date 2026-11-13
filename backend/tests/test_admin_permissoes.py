"""Permissões da API de administração: só admin, role sempre vindo de `perfis`."""

from __future__ import annotations

import re
import uuid

import pytest

from app.modelos import Configuracoes
from tests.conftest import emitir_token
from tests.horarios import hora_saida_futura


def _rotas_admin(app):  # type: ignore[no-untyped-def]
    for regra in app.url_map.iter_rules():
        if regra.rule.startswith("/api/admin"):
            for metodo in sorted(regra.methods - {"HEAD", "OPTIONS"}):
                caminho = re.sub(r"<[^>]+>", str(uuid.uuid4()), regra.rule)
                yield metodo, caminho


def test_existem_rotas_admin(app):  # type: ignore[no-untyped-def]
    assert len(list(_rotas_admin(app))) >= 17


@pytest.mark.parametrize("role", ["solicitante", "autorizador"])
def test_nao_admin_recebe_403_em_toda_rota_admin(client, auth, repo, app, role):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil(role)
    for metodo, caminho in _rotas_admin(app):
        r = client.open(caminho, method=metodo, headers=auth(p.id), json={})
        assert r.status_code == 403, (metodo, caminho)
        assert r.get_json()["erro"]["codigo"] == "sem_permissao"


def test_sem_token_recebe_401_em_toda_rota_admin(client, app):  # type: ignore[no-untyped-def]
    for metodo, caminho in _rotas_admin(app):
        assert client.open(caminho, method=metodo, json={}).status_code == 401, (metodo, caminho)


def test_admin_inativo_nao_acessa(client, auth, repo):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil("admin", ativo=False)
    r = client.get("/api/admin/usuarios", headers=auth(p.id))
    assert r.status_code == 403 and r.get_json()["erro"]["codigo"] == "conta_inativa"


def test_admin_exige_mfa_quando_ligado(client, auth, auth_aal2, repo, adm):  # type: ignore[no-untyped-def]
    repo.config = Configuracoes(mfa=True)
    r = client.get("/api/admin/usuarios", headers=auth(adm.id))
    assert r.status_code == 403 and r.get_json()["erro"]["codigo"] == "mfa_requerido"
    assert client.get("/api/admin/usuarios", headers=auth_aal2(adm.id)).status_code == 200


def test_role_do_jwt_ou_da_query_nao_vale(client, repo):  # type: ignore[no-untyped-def]
    sol = repo.novo_perfil("solicitante")
    extra = {"role": "admin", "app_metadata": {"role": "admin"}, "user_metadata": {"role": "admin"}}
    h = {"Authorization": f"Bearer {emitir_token(sol.id, extra=extra)}"}
    assert client.get("/api/admin/usuarios", headers=h).status_code == 403
    r = client.get("/api/eu?role=admin", headers=h, json={"role": "admin"})
    assert r.get_json()["role"] == "solicitante"


def test_permissao_administrar_so_para_admin(client, auth, repo):  # type: ignore[no-untyped-def]
    for role, esperado in (("solicitante", False), ("autorizador", False), ("admin", True)):
        p = repo.novo_perfil(role)
        j = client.get("/api/eu", headers=auth(p.id)).get_json()
        assert ("administrar" in j["permissoes"]) is esperado, role


def test_rotas_nao_admin_recusam_campo_de_papel(client, auth, repo):  # type: ignore[no-untyped-def]
    """Escalonamento: nenhum campo role/ativo/usuario_id vindo do cliente em rota não-admin."""
    sol = repo.novo_perfil("solicitante")
    v = repo.novo_veiculo()
    base = {
        "nome_solicitante": "Fulano",
        "unidade": "PU",
        "hora_saida": hora_saida_futura(),
        "trajeto": "AGU -> Fórum",
        "veiculo_id": v,
    }
    for extra in (
        {"role": "admin"},
        {"ativo": True},
        {"usuario_id": sol.id},
        {"perfil": {"role": "admin"}},
    ):
        r = client.post("/api/requisicoes", headers=auth(sol.id), json={**base, **extra})
        assert r.status_code == 400, extra
    assert repo.perfis[sol.id].role == "solicitante"
    assert not repo.requisicoes


def test_solicitante_nao_se_promove_pela_rota_admin(client, auth, repo):  # type: ignore[no-untyped-def]
    sol = repo.novo_perfil("solicitante")
    r = client.patch(f"/api/admin/usuarios/{sol.id}", headers=auth(sol.id), json={"role": "admin"})
    assert r.status_code == 403 and repo.perfis[sol.id].role == "solicitante"
