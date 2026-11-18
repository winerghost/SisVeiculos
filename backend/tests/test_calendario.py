"""GET /api/calendario: permissão ver_calendario, visibilidade por papel (IDOR), janela
(fim inclusivo, fuso de São Paulo, máx. 62 dias), itens mínimos (LGPD) e truncamento."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from postgrest import SyncPostgrestClient

from app.repositorios.supabase_repo import _expr_janela_calendario, _filtros_lista
from app.services.calendario import MAX_ITENS
from tests.semear import semear

URL = "/api/calendario"
JANELA = {"inicio": "2026-11-10", "fim": "2026-11-12"}
CHAVES_ITEM = {
    "id",
    "status",
    "veiculo_id",
    "veiculo_rotulo",
    "unidade",
    "inicio",
    "fim",
    "hora_saida",
    "saida_real_em",
    "chegada_real_em",
}


def cal(client, auth, perfil, **params: Any):  # type: ignore[no-untyped-def]
    return client.get(URL, headers=auth(perfil.id), query_string=params or JANELA)


def ids(resp: Any) -> set[str]:
    return {i["id"] for i in resp.get_json()["itens"]}


def eu(client, auth, perfil) -> list[str]:  # type: ignore[no-untyped-def]
    return client.get("/api/eu", headers=auth(perfil.id)).get_json()["permissoes"]


# ---------------------------------------------------------------- autenticação e permissão
def test_sem_token_401(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get(URL, query_string=JANELA).status_code == 401


@pytest.mark.parametrize("role", ["solicitante", "autorizador", "admin", "vigilante"])
def test_papeis_tem_ver_calendario(client, auth, repo, role):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil(role)
    assert "ver_calendario" in eu(client, auth, p)
    assert cal(client, auth, p).status_code == 200


def test_motorista_so_com_recurso_ligado(client, auth, repo, c):  # type: ignore[no-untyped-def]
    mot = repo.novo_perfil("motorista")
    assert "ver_calendario" not in eu(client, auth, mot)
    r = cal(client, auth, mot)
    assert r.status_code == 403 and r.get_json()["erro"]["codigo"] == "sem_permissao"

    repo.config = replace(repo.config, motorista=True)
    minha = semear(repo, c.sol, c.veiculo, status="autorizada", motorista_id=mot.id)
    semear(repo, c.sol, repo.novo_veiculo(), status="autorizada")  # de outro motorista/nenhum
    assert "ver_calendario" in eu(client, auth, mot)
    r = cal(client, auth, mot)
    assert r.status_code == 200 and ids(r) == {minha}


def test_perfil_inativo_nao_acessa(client, auth, repo):  # type: ignore[no-untyped-def]
    assert cal(client, auth, repo.novo_perfil("admin", ativo=False)).status_code == 403


# ---------------------------------------------------------------- visibilidade (IDOR)
@pytest.fixture
def mix(repo, c):  # type: ignore[no-untyped-def]
    vig = repo.novo_perfil("vigilante")
    ref = {
        "sol_solicitada": semear(repo, c.sol, c.veiculo),
        "outro_autorizada": semear(repo, c.outro, repo.novo_veiculo(), status="autorizada"),
        "outro_solicitada": semear(repo, c.outro, repo.novo_veiculo()),
        "outro_concluida_vig": semear(
            repo,
            c.outro,
            repo.novo_veiculo(),
            status="concluida",
            saida_real_em="2026-11-10T12:00:00+00:00",
            chegada_real_em="2026-11-10T14:00:00+00:00",
            saida_registrada_por=vig.id,
        ),
        "outro_concluida": semear(
            repo,
            c.outro,
            repo.novo_veiculo(),
            status="concluida",
            saida_real_em="2026-11-10T12:00:00+00:00",
            chegada_real_em="2026-11-10T14:00:00+00:00",
        ),
        "outro_negada": semear(repo, c.outro, repo.novo_veiculo(), status="negada"),
        "outro_cancelada": semear(repo, c.outro, repo.novo_veiculo(), status="cancelada"),
    }
    return vig, ref


def test_solicitante_ve_so_as_proprias(client, auth, c, mix):  # type: ignore[no-untyped-def]
    _, ref = mix
    assert ids(cal(client, auth, c.sol)) == {ref["sol_solicitada"]}


@pytest.mark.parametrize("quem", ["aut", "adm"])
def test_equipe_ve_todas_sem_negada_cancelada(client, auth, c, mix, quem):  # type: ignore[no-untyped-def]
    _, ref = mix
    esperado = {v for k, v in ref.items() if k not in {"outro_negada", "outro_cancelada"}}
    assert ids(cal(client, auth, getattr(c, quem))) == esperado


def test_vigilante_mesma_visibilidade_da_listagem(client, auth, c, mix):  # type: ignore[no-untyped-def]
    vig, ref = mix
    assert ids(cal(client, auth, vig)) == {ref["outro_autorizada"], ref["outro_concluida_vig"]}


def test_arquivada_aparece_no_calendario(client, auth, c, repo):  # type: ignore[no-untyped-def]
    rid = semear(
        repo,
        c.sol,
        c.veiculo,
        status="concluida",
        saida_real_em="2026-11-10T12:00:00+00:00",
        chegada_real_em="2026-11-10T14:00:00+00:00",
    )
    repo.arquivar_requisicao(c.sol.id, rid)
    assert ids(cal(client, auth, c.sol)) == {rid}


# ---------------------------------------------------------------- janela
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"inicio": "2026-11-10"},
        {"fim": "2026-11-10"},
        {"inicio": "10/11/2026", "fim": "2026-11-12"},
        {"inicio": "2026-02-30", "fim": "2026-03-01"},
        {"inicio": "2026-11-12", "fim": "2026-11-10"},
        {"inicio": "2026-01-01", "fim": "2026-03-04"},  # 63 dias
        {"inicio": "1900-01-01", "fim": "1900-01-02"},
        {"inicio": "2026-11-10T00:00", "fim": "2026-11-12"},
    ],
)
def test_janela_invalida_400(client, auth, c, params):  # type: ignore[no-untyped-def]
    r = client.get(URL, headers=auth(c.aut.id), query_string=params)
    assert r.status_code == 400
    assert r.get_json()["erro"]["codigo"] == "filtro_invalido"


def test_janela_de_62_dias_aceita(client, auth, c, repo):  # type: ignore[no-untyped-def]
    r = cal(client, auth, c.aut, inicio="2026-01-01", fim="2026-03-03")
    assert r.status_code == 200
    assert repo.chamadas_calendario[-1] == {
        "inicio": "2026-01-01T03:00:00Z",  # 00:00 em São Paulo
        "fim": "2026-03-04T03:00:00Z",  # fim inclusivo -> 00:00 do dia seguinte
    }


def test_intervalo_que_toca_a_janela(client, auth, c, repo):  # type: ignore[no-untyped-def]
    def nova(**campos: Any) -> str:
        return semear(repo, c.sol, repo.novo_veiculo(), **campos)

    dentro = nova(hora_saida="2026-11-12T23:00:00-03:00")  # último dia, ainda local
    fora_fuso = nova(hora_saida="2026-11-13T02:30:00+00:00")  # 23:30 do dia 12 local -> dentro
    depois = nova(hora_saida="2026-11-13T03:00:00+00:00")  # 00:00 do dia 13 local -> fora
    antes = nova(status="autorizada", hora_saida="2026-11-09T12:00:00+00:00")
    em_uso_antigo = nova(
        status="em_uso",
        hora_saida="2026-11-01T12:00:00+00:00",
        saida_real_em="2026-11-01T12:00:00+00:00",
    )
    em_uso_futuro = nova(
        status="em_uso",
        hora_saida="2026-11-20T12:00:00+00:00",
        saida_real_em="2026-11-20T12:00:00+00:00",
    )
    atravessa = nova(
        status="concluida",
        saida_real_em="2026-11-08T12:00:00+00:00",
        chegada_real_em="2026-11-10T04:00:00+00:00",
    )
    terminou_antes = nova(
        status="concluida",
        saida_real_em="2026-11-08T12:00:00+00:00",
        chegada_real_em="2026-11-10T02:00:00+00:00",  # 23:00 do dia 9 local
    )
    vistos = ids(cal(client, auth, c.aut))
    assert {dentro, fora_fuso, em_uso_antigo, atravessa} <= vistos
    assert not {depois, antes, em_uso_futuro, terminou_antes} & vistos


# ---------------------------------------------------------------- LGPD e formato
def test_item_tem_so_dados_minimos(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.veiculos[c.veiculo]["placa"] = "ABC1D23"
    rid = semear(
        repo,
        c.sol,
        c.veiculo,
        status="em_uso",
        observacao="segredo",
        km_saida=100,
        hora_saida="2026-11-10T12:00:00+00:00",
        saida_real_em="2026-11-10T12:30:00+00:00",
    )
    r = cal(client, auth, c.aut)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    j = r.get_json()
    assert j["truncado"] is False
    (item,) = j["itens"]
    assert set(item) == CHAVES_ITEM
    assert item["id"] == rid and item["veiculo_rotulo"] == "Carro · ABC1D23"
    assert item["inicio"] == "2026-11-10T12:30:00+00:00"  # saída real tem precedência
    assert item["fim"] is None
    texto = r.get_data(as_text=True)
    for proibido in ("Fulano", "Fórum", "segredo", "agu.gov.br", c.sol.id):
        assert proibido not in texto


def test_inicio_previsto_e_fim_real(client, auth, c, repo):  # type: ignore[no-untyped-def]
    semear(repo, c.sol, c.veiculo, hora_saida="2026-11-11T12:00:00+00:00")
    semear(
        repo,
        c.sol,
        repo.novo_veiculo(),
        status="concluida",
        saida_real_em="2026-11-10T12:00:00+00:00",
        chegada_real_em="2026-11-10T14:00:00+00:00",
    )
    por_status = {i["status"]: i for i in cal(client, auth, c.sol).get_json()["itens"]}
    assert por_status["solicitada"]["inicio"] == "2026-11-11T12:00:00+00:00"
    assert por_status["solicitada"]["fim"] is None
    assert por_status["solicitada"]["veiculo_rotulo"] == "Carro"
    assert por_status["concluida"]["fim"] == "2026-11-10T14:00:00+00:00"


def test_truncado_acima_de_500(client, auth, c, repo):  # type: ignore[no-untyped-def]
    v = repo.novo_veiculo()
    for _ in range(MAX_ITENS + 1):
        semear(repo, c.sol, v)
    j = cal(client, auth, c.aut).get_json()
    assert j["truncado"] is True and len(j["itens"]) == MAX_ITENS


@pytest.mark.parametrize(
    "params",
    [
        {"inicio": "２０２６-１１-１０", "fim": "2026-11-12"},  # dígitos de largura total
        {"inicio": "2026-11-10", "fim": "2026-11-１２"},
        {"inicio": "٢٠٢٦-١١-١٠", "fim": "2026-11-12"},  # dígitos arábicos
    ],
)
def test_data_com_digitos_nao_ascii_400(client, auth, c, params):  # type: ignore[no-untyped-def]
    r = client.get(URL, headers=auth(c.aut.id), query_string=params)
    assert r.status_code == 400
    assert r.get_json()["erro"]["codigo"] == "filtro_invalido"


# ---------------------------------------------------------------- estrutura do filtro PostgREST
@pytest.mark.parametrize(
    "papel, usuario_id, ou_status, ou_registrada_por",
    [
        ("solicitante", "u1", None, None),
        ("vigilante", "u1", ("autorizada", "em_uso"), "u1"),
        ("staff", None, None, None),
    ],
)
def test_janela_nao_altera_estrutura_do_filtro(
    papel: str,
    usuario_id: str | None,
    ou_status: tuple[str, ...] | None,
    ou_registrada_por: str | None,
) -> None:
    janela = _expr_janela_calendario("2026-11-10T03:00:00Z", "2026-11-13T03:00:00Z")
    q = SyncPostgrestClient("http://localhost").from_("requisicoes").select("id")
    q = _filtros_lista(
        q,
        usuario_id=usuario_id,
        status=None,
        motorista_id=None,
        ou_status=ou_status,
        ou_registrada_por=ou_registrada_por,
        extra_ou=janela,
    )
    params = list(q.params.multi_items())
    # Um ÚNICO parâmetro `or`: visibilidade E janela, nunca um OR solto que amplie.
    assert [k for k, _ in params].count("or") == 1
    valor = dict(params)["or"]
    assert valor.count("(") == valor.count(")")
    assert janela in valor
    if papel == "vigilante":
        assert valor.startswith("(and(or(usuario_id.eq.u1,")
        assert "saida_registrada_por.eq.u1" in valor and "chegada_registrada_por.eq.u1" in valor
        assert "usuario_id" not in dict(params)
    elif papel == "solicitante":
        assert dict(params)["usuario_id"] == "eq.u1"  # dono por igualdade, fora do OR
        assert valor == f"({janela})"
    else:
        assert "usuario_id" not in dict(params) and "usuario_id" not in valor
        assert valor == f"({janela})"
