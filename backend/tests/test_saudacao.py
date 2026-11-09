"""Saudação sazonal (IA, Fase 1): calendário, catálogo, escolha estável, gate, rotas e geração."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import pytest

from app import create_app
from app.auth.jwt import VerificadorJWT
from app.config import Config
from app.errors import ErroApi
from app.extensions import limiter
from app.ia.config import ConfigIA
from app.ia.provedores import Resultado
from app.ia.repositorio import ConfigIABanco
from app.ia.servico import ServicoIA
from app.modelos import Perfil
from app.saudacao.calendario import OCASIOES, PERIODOS, pascoa, resolver
from app.saudacao.catalogo import CATALOGO
from app.saudacao.servico import (
    CACHE,
    DESCRICOES,
    ServicoSaudacao,
    escolher_texto,
    extrair_frases,
    primeiro_nome,
)
from app.saudacao.validacao import motivo_invalido, normalizar, sem_placeholder
from app.services.dashboards import FUSO
from tests.conftest import _CHAVE, AUDIENCE, ISSUER, URL_ACEITE
from tests.fakes import RepoMemoria
from tests.fakes_admin import AuthAdminMemoria
from tests.fakes_checklist import ArmazenamentoMemoria
from tests.fakes_foto import ArmazenamentoAvataresMemoria
from tests.fakes_ia import ProvedorRoteirizado, RepoIAMemoria
from tests.fakes_saudacao import RepoSaudacoesMemoria

LIGADO = ConfigIABanco(mestre=True, saudacao=True, provedor="openai", modelo="gpt-5.4-mini")


@pytest.fixture(autouse=True)
def _limpa_cache() -> None:
    CACHE.limpar()


def em(ano: int, mes: int, dia: int, h: int = 10, m: int = 0) -> datetime:
    return datetime(ano, mes, dia, h, m, tzinfo=FUSO)


# ------------------------------------------------------------------------------ calendário
def test_pascoa_anos_conhecidos() -> None:
    assert pascoa(2024) == date(2024, 3, 31)
    assert pascoa(2025) == date(2025, 4, 20)
    assert pascoa(2026) == date(2026, 4, 5)
    assert pascoa(2027) == date(2027, 3, 28)
    assert pascoa(2038) == date(2038, 4, 25)  # extremo tardio


@pytest.mark.parametrize(
    ("dia", "esperado"),
    [
        (date(2025, 3, 3), "carnaval"),
        (date(2025, 3, 4), "carnaval"),
        (date(2026, 2, 16), "carnaval"),
        (date(2026, 2, 17), "carnaval"),
        (date(2024, 2, 13), "carnaval"),
        (date(2025, 4, 18), "sexta_santa"),
        (date(2026, 4, 3), "sexta_santa"),
        (date(2025, 4, 20), "pascoa"),
        (date(2025, 6, 19), "corpus_christi"),
        (date(2026, 6, 4), "corpus_christi"),
        (date(2026, 1, 1), "ano_novo"),
        (date(2025, 12, 31), "vespera_ano_novo"),
        (date(2026, 4, 21), "tiradentes"),
        (date(2026, 5, 1), "trabalho"),
        (date(2026, 9, 7), "independencia"),
        (date(2026, 10, 12), "padroeira"),
        (date(2026, 10, 28), "dia_servidor"),
        (date(2026, 11, 2), "finados"),
        (date(2026, 11, 15), "republica"),
        (date(2026, 11, 20), "consciencia_negra"),
        (date(2026, 12, 24), "vespera_natal"),
        (date(2026, 12, 25), "natal"),
    ],
)
def test_datas_comemorativas(dia: date, esperado: str) -> None:
    assert resolver(em(dia.year, dia.month, dia.day)).principal == esperado


def test_comemorativa_vence_dia_especial_e_periodo() -> None:
    assert resolver(em(2026, 5, 1, 3)).principal == "trabalho"  # sexta + madrugada
    assert resolver(em(2026, 11, 2, 9)).principal == "finados"  # segunda
    assert resolver(em(2026, 11, 15, 9)).principal == "republica"  # domingo
    assert resolver(em(2026, 4, 3, 20)).principal == "sexta_santa"  # sexta
    assert resolver(em(2026, 4, 5, 20)).principal == "pascoa"  # domingo


def test_dia_especial_vence_periodo() -> None:
    assert resolver(em(2026, 11, 9, 9)).principal == "segunda"
    assert resolver(em(2026, 11, 13, 22)).principal == "sexta"
    assert resolver(em(2026, 11, 14, 9)).principal == "fim_de_semana"
    assert resolver(em(2026, 11, 8, 9)).principal == "fim_de_semana"


@pytest.mark.parametrize(
    ("h", "m", "esperado"),
    [
        (0, 0, "madrugada"),
        (4, 59, "madrugada"),
        (5, 0, "manha"),
        (11, 59, "manha"),
        (12, 0, "tarde"),
        (17, 59, "tarde"),
        (18, 0, "noite"),
        (23, 59, "noite"),
    ],
)
def test_periodos_e_fronteiras(h: int, m: int, esperado: str) -> None:
    o = resolver(em(2026, 11, 10, h, m))  # terça comum
    assert o.principal == esperado and o.apoio == (esperado,)


def test_apoio_e_o_periodo_mesmo_com_data_especial() -> None:
    o = resolver(em(2026, 12, 25, 3))
    assert o.principal == "natal" and o.apoio == ("madrugada",)


def test_virada_de_dia_usa_o_fuso_do_projeto() -> None:
    # 02:30 UTC de terça = 23:30 de segunda em São Paulo
    o = resolver(datetime(2026, 11, 10, 2, 30, tzinfo=UTC))
    assert o.data == date(2026, 11, 9) and o.principal == "segunda"
    # 01:30 UTC de quarta = 22:30 de terça local: noite, não "quarta"
    o2 = resolver(datetime(2026, 11, 11, 1, 30, tzinfo=UTC))
    assert o2.data == date(2026, 11, 10) and o2.principal == "noite"
    # 03:00 UTC = 00:00 local: já é o dia novo
    assert resolver(datetime(2026, 11, 10, 3, 0, tzinfo=UTC)).data == date(2026, 11, 10)


def test_instante_sem_fuso_vale_como_fuso_do_projeto() -> None:
    assert resolver(datetime(2026, 11, 10, 4, 59)).principal == "madrugada"


# ------------------------------------------------------------------------------ catálogo
def test_catalogo_cobre_todas_as_ocasioes_e_so_elas() -> None:
    assert set(CATALOGO) == set(OCASIOES) == set(DESCRICOES)
    assert len(OCASIOES) == 23


def test_catalogo_frases_validas_e_quantidades() -> None:
    for oc, frases in CATALOGO.items():
        assert len(frases) >= (6 if oc in PERIODOS else 3), oc
        assert len({normalizar(f) for f in frases}) == len(frases), oc
        assert any("{nome}" not in f for f in frases), f"{oc} sem frase sem marcador"
        assert any("{nome}" in f for f in frases), oc
        for f in frases:
            assert motivo_invalido(f) is None, f
            assert len(f) <= 80, f
        assert all(sem_placeholder(f) for f in frases)


def test_catalogo_inclui_estilo_coruja_e_cafe() -> None:
    assert "Acordado, coruja noturna?" in CATALOGO["madrugada"]
    assert "Madrugada de plantão, {nome}?" in CATALOGO["madrugada"]
    assert "Bom dia, {nome}! Café na mão e bora." in CATALOGO["manha"]


@pytest.mark.parametrize(
    "texto",
    [
        "ab",
        "x" * 81,
        " olá",
        "linha\nquebrada",
        "a<b>c",
        "veja http://x.com",
        "www.x.com ok",
        "olá {outro}",
        "olá {nome",
        "tab\taqui",
    ],
)
def test_validacao_rejeita(texto: str) -> None:
    assert motivo_invalido(texto) is not None


def test_validacao_aceita_nome_e_limites() -> None:
    assert motivo_invalido("Olá, {nome}!") is None
    assert motivo_invalido("abc") is None
    assert motivo_invalido("x" * 80) is None


# ------------------------------------------------------------------------------ escolha
def _escolher(o: Any, uid: str = "u1", nome: str | None = "Maria Silva", aprov: Any = None) -> str:
    return escolher_texto(o, usuario_id=uid, nome=nome, aprovadas=aprov or (lambda _o: []))


def test_escolha_estavel_no_mesmo_dia_e_ocasiao() -> None:
    o = resolver(em(2026, 11, 10, 9))
    assert len({_escolher(o) for _ in range(20)}) == 1
    # outra hora do mesmo período/dia: mesma frase
    assert _escolher(resolver(em(2026, 11, 10, 11, 59))) == _escolher(o)


def test_escolha_varia_entre_dias_e_usuarios() -> None:
    dias = {_escolher(resolver(em(2026, 11, d, 9)), uid="u1") for d in (10, 11, 12, 17, 18, 19, 24)}
    usuarios = {_escolher(resolver(em(2026, 11, 10, 9)), uid=f"u{i}") for i in range(30)}
    assert len(dias) > 1 and len(usuarios) > 1


def test_nome_substituido_pelo_primeiro_nome() -> None:
    o = resolver(em(2026, 11, 10, 9))
    textos = {_escolher(o, uid=f"u{i}", nome="  Maria da Silva ") for i in range(40)}
    assert any("Maria" in t for t in textos)
    assert all("{nome}" not in t and "Silva" not in t for t in textos)


@pytest.mark.parametrize("nome", [None, "", "   ", "<>{}"])
def test_sem_nome_nunca_usa_marcador(nome: str | None) -> None:
    for d in range(1, 29):
        for h in (2, 9, 14, 21):
            t = _escolher(resolver(em(2026, 11, d, h)), uid=f"u{d}", nome=nome)
            assert t and "{nome}" not in t and "None" not in t


def test_primeiro_nome_sanitiza() -> None:
    assert primeiro_nome("  Ana\nMaria ") == "Ana"
    assert primeiro_nome("<b>Ana</b> Souza") == "bAna/b"  # sem '<' nem '>'
    assert primeiro_nome("{nome} X") == "nome"
    assert len(primeiro_nome("A" * 200) or "") == 30
    assert primeiro_nome(None) is None


def test_frases_aprovadas_entram_no_pool() -> None:
    o = resolver(em(2026, 11, 10, 9))
    nova = "Frase aprovada exclusiva do banco"
    vistos = {
        _escolher(o, uid=f"u{i}", aprov=lambda _o: [nova, "frase invalida <x>"]) for i in range(300)
    }
    assert nova in vistos
    assert "frase invalida <x>" not in vistos


def test_aprovada_sem_marcador_serve_quem_nao_tem_nome() -> None:
    o = resolver(em(2026, 11, 10, 9))
    nova = "Só para quem está sem nome"
    vistos = {_escolher(o, uid=f"u{i}", nome=None, aprov=lambda _o: [nova]) for i in range(300)}
    assert nova in vistos


# ------------------------------------------------------------------------------ serviço / gate
def _servico_ia(banco: ConfigIABanco | None = LIGADO) -> tuple[ServicoIA, RepoIAMemoria]:
    repo_ia = RepoIAMemoria(banco)
    return ServicoIA(ConfigIA(habilitado=False), repo_ia), repo_ia  # sem chave nem AI_ENABLED


def _perfil(nome: str = "Joana Prado") -> Perfil:
    return Perfil("11111111-1111-4111-8111-111111111111", nome, "PU", "solicitante", True)


def test_gate_ligado_independe_de_chave_de_provedor() -> None:
    ia, _ = _servico_ia()
    s = ServicoSaudacao(ia, RepoSaudacoesMemoria(), agora=lambda: em(2026, 11, 10, 9))
    texto = s.para_usuario(_perfil())
    assert texto and "{nome}" not in texto


@pytest.mark.parametrize(
    "banco",
    [
        None,
        ConfigIABanco(),
        ConfigIABanco(mestre=True, saudacao=False),
        ConfigIABanco(mestre=False, saudacao=True),
    ],
)
def test_gate_desligado_devolve_none(banco: ConfigIABanco | None) -> None:
    ia, _ = _servico_ia(banco)
    s = ServicoSaudacao(ia, RepoSaudacoesMemoria())
    assert s.para_usuario(_perfil()) is None


def test_gate_sem_servico_ia_ou_banco_fora_devolve_none() -> None:
    assert ServicoSaudacao(None, None).para_usuario(_perfil()) is None
    ia, repo_ia = _servico_ia()
    repo_ia.falhar_leitura = True
    assert ServicoSaudacao(ia, None).para_usuario(_perfil()) is None


def test_banco_de_saudacoes_fora_cai_no_catalogo() -> None:
    ia, _ = _servico_ia()
    fake = RepoSaudacoesMemoria()
    fake.falhar_aprovadas = True
    s = ServicoSaudacao(ia, fake, agora=lambda: em(2026, 11, 10, 3))
    assert s.para_usuario(_perfil()) in {
        f.replace("{nome}", "Joana") for f in CATALOGO["madrugada"]
    }


def test_servico_sem_nome_no_perfil() -> None:
    ia, _ = _servico_ia()
    s = ServicoSaudacao(ia, None, agora=lambda: em(2026, 11, 10, 9))
    t = s.para_usuario(_perfil(""))
    assert t and "{nome}" not in t


# ------------------------------------------------------------------------------ app / rotas
@pytest.fixture
def saud() -> RepoSaudacoesMemoria:
    return RepoSaudacoesMemoria()


@pytest.fixture
def ia_env() -> tuple[ServicoIA, RepoIAMemoria, ProvedorRoteirizado]:
    prov = ProvedorRoteirizado("openai")
    repo_ia = RepoIAMemoria(LIGADO)
    cfg = ConfigIA(habilitado=True, openai_api_key="sk-teste-fake")
    return ServicoIA(cfg, repo_ia, provedores={"openai": prov}), repo_ia, prov


@pytest.fixture
def app_s(  # type: ignore[no-untyped-def]
    repo: RepoMemoria,
    auth_admin: AuthAdminMemoria,
    storage: ArmazenamentoMemoria,
    avatares: ArmazenamentoAvataresMemoria,
    saud: RepoSaudacoesMemoria,
    ia_env,
):
    cfg = Config(
        jwt_issuer=ISSUER, jwt_audience=AUDIENCE, testing=True, url_aceite_convite=URL_ACEITE
    )
    verificador = VerificadorJWT(
        issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
    )
    a = create_app(
        cfg,
        repo=repo,
        verificador=verificador,
        auth_admin=auth_admin,
        armazenamento=storage,
        armazenamento_avatares=avatares,
        servico_ia=ia_env[0],
        repo_saudacoes=saud,
    )
    limiter.reset()
    return a


@pytest.fixture
def cli(app_s):  # type: ignore[no-untyped-def]
    return app_s.test_client()


def test_get_saudacao_exige_jwt(cli) -> None:  # type: ignore[no-untyped-def]
    r = cli.get("/api/saudacao")
    assert r.status_code == 401
    assert set(r.get_json()["erro"]) == {"codigo", "mensagem"}


def test_get_saudacao_token_invalido(cli) -> None:  # type: ignore[no-untyped-def]
    assert cli.get("/api/saudacao", headers={"Authorization": "Bearer lixo"}).status_code == 401


@pytest.mark.parametrize("role", ["solicitante", "autorizador", "admin"])
def test_get_saudacao_qualquer_perfil_ativo(cli, repo, auth, role) -> None:  # type: ignore[no-untyped-def]
    p = repo.novo_perfil(role)
    r = cli.get("/api/saudacao", headers=auth(p.id))
    assert r.status_code == 200
    texto = r.get_json()["texto"]
    assert isinstance(texto, str) and 3 <= len(texto) <= 80 and "{nome}" not in texto
    # estável entre recargas
    assert cli.get("/api/saudacao", headers=auth(p.id)).get_json()["texto"] == texto


def test_get_saudacao_usa_o_nome_do_perfil_nao_do_cliente(cli, repo, auth) -> None:  # type: ignore[no-untyped-def]
    p = repo.novo_perfil("solicitante")
    repo.perfis[p.id] = replace(p, nome="Zuleica Alves")
    vistos = set()
    for i in range(1, 8):
        # query/headers com outro "nome" são ignorados
        r = cli.get(f"/api/saudacao?nome=Hacker{i}", headers=auth(p.id))
        vistos.add(r.get_json()["texto"])
    assert not any("Hacker" in t for t in vistos)


def test_get_saudacao_perfil_inativo_403(cli, repo, auth) -> None:  # type: ignore[no-untyped-def]
    p = repo.novo_perfil("solicitante", ativo=False)
    assert cli.get("/api/saudacao", headers=auth(p.id)).status_code == 403


def test_get_saudacao_desligada_devolve_texto_null(cli, repo, auth, ia_env) -> None:  # type: ignore[no-untyped-def]
    p = repo.novo_perfil("solicitante")
    ia_env[1].config = ConfigIABanco(mestre=True, saudacao=False)
    CACHE.limpar()
    r = cli.get("/api/saudacao", headers=auth(p.id))
    assert r.status_code == 200 and r.get_json() == {"texto": None}
    ia_env[1].config = ConfigIABanco(mestre=False, saudacao=True)
    CACHE.limpar()
    assert cli.get("/api/saudacao", headers=auth(p.id)).get_json() == {"texto": None}


ROTAS_ADMIN: list[tuple[str, str]] = [
    ("get", "/api/admin/ia/saudacoes"),
    ("post", "/api/admin/ia/saudacoes"),
    ("post", "/api/admin/ia/saudacoes/gerar"),
    ("post", "/api/admin/ia/saudacoes/11111111-1111-4111-8111-111111111111/aprovar"),
    ("post", "/api/admin/ia/saudacoes/11111111-1111-4111-8111-111111111111/rejeitar"),
]


@pytest.mark.parametrize(("metodo", "url"), ROTAS_ADMIN)
def test_admin_sem_jwt_401(cli, metodo, url) -> None:  # type: ignore[no-untyped-def]
    r = getattr(cli, metodo)(url, json={})
    assert r.status_code == 401 and "erro" in r.get_json()


@pytest.mark.parametrize("role", ["solicitante", "autorizador", "motorista"])
@pytest.mark.parametrize(("metodo", "url"), ROTAS_ADMIN)
def test_admin_nao_admin_403(cli, repo, auth, saud, role, metodo, url) -> None:  # type: ignore[no-untyped-def]
    p = repo.novo_perfil(role)
    r = getattr(cli, metodo)(
        url, json={"ocasiao": "natal", "texto": "Texto válido"}, headers=auth(p.id)
    )
    assert r.status_code == 403 and r.get_json()["erro"]["codigo"] == "sem_permissao"
    assert saud.linhas == []


@pytest.fixture
def adm_h(repo, auth):  # type: ignore[no-untyped-def]
    p = repo.novo_perfil("admin")
    return p, auth(p.id)


def test_admin_cria_manual_ja_aprovada(cli, adm_h, saud) -> None:  # type: ignore[no-untyped-def]
    p, h = adm_h
    r = cli.post(
        "/api/admin/ia/saudacoes",
        json={"ocasiao": "natal", "texto": "Boas festas, {nome}!"},
        headers=h,
    )
    assert r.status_code == 201
    j = r.get_json()
    assert (
        j["status"] == "aprovada"
        and j["origem"] == "manual"
        and j["acoes_permitidas"] == ["rejeitar"]
    )
    assert saud.linhas[0]["criado_por"] == p.id  # ator vem do JWT
    assert saud.linhas[0]["decidido_por"] == p.id  # aprovada pelo mesmo admin (insert + update)


def test_admin_cria_ignora_ator_do_corpo(cli, adm_h) -> None:  # type: ignore[no-untyped-def]
    _, h = adm_h
    r = cli.post(
        "/api/admin/ia/saudacoes",
        json={"ocasiao": "natal", "texto": "Texto válido", "criado_por": "x", "status": "pendente"},
        headers=h,
    )
    assert r.status_code == 400  # campos desconhecidos recusados


@pytest.mark.parametrize(
    "corpo",
    [
        {"ocasiao": "inexistente", "texto": "Texto válido"},
        {"ocasiao": "natal", "texto": "ab"},
        {"ocasiao": "natal", "texto": "x" * 81},
        {"ocasiao": "natal", "texto": "<b>oi</b> tudo"},
        {"ocasiao": "natal", "texto": "veja https://x.gov.br"},
        {"ocasiao": "natal", "texto": "linha 1\nlinha 2"},
        {"ocasiao": "natal", "texto": "oi {unidade}"},
        {"ocasiao": "natal", "texto": 123},
        {"ocasiao": 1, "texto": "Texto válido"},
        {"texto": "Texto válido"},
    ],
)
def test_admin_cria_valida(cli, adm_h, saud, corpo) -> None:  # type: ignore[no-untyped-def]
    r = cli.post("/api/admin/ia/saudacoes", json=corpo, headers=adm_h[1])
    assert r.status_code == 400 and set(r.get_json()["erro"]) == {"codigo", "mensagem"}
    assert saud.linhas == []


def test_admin_cria_corpo_nao_json_ou_grande(cli, adm_h) -> None:  # type: ignore[no-untyped-def]
    h = adm_h[1]
    assert cli.post("/api/admin/ia/saudacoes", data="x", headers=h).status_code == 400
    r = cli.post(
        "/api/admin/ia/saudacoes", json={"ocasiao": "natal", "texto": "a" * 40000}, headers=h
    )
    assert r.status_code == 413 and "erro" in r.get_json()


def test_admin_duplicada_do_catalogo_ou_do_banco_409(cli, adm_h) -> None:  # type: ignore[no-untyped-def]
    h = adm_h[1]
    r = cli.post(
        "/api/admin/ia/saudacoes",
        json={"ocasiao": "natal", "texto": "FELIZ NATAL, {nome}!"},
        headers=h,
    )
    assert r.status_code == 409 and r.get_json()["erro"]["codigo"] == "saudacao_duplicada"
    corpo = {"ocasiao": "natal", "texto": "Uma frase nova"}
    assert cli.post("/api/admin/ia/saudacoes", json=corpo, headers=h).status_code == 201
    assert cli.post("/api/admin/ia/saudacoes", json=corpo, headers=h).status_code == 409


def test_admin_lista_com_filtros(cli, adm_h, saud) -> None:  # type: ignore[no-untyped-def]
    p, h = adm_h
    saud.semear(ocasiao="natal", texto="Natal A", origem="ia", status="pendente", criado_por=p.id)
    saud.semear(
        ocasiao="pascoa", texto="Páscoa A", origem="manual", status="aprovada", criado_por=p.id
    )
    j = cli.get("/api/admin/ia/saudacoes", headers=h).get_json()
    assert len(j["itens"]) == 2 and j["ocasioes"] == list(OCASIOES)
    assert set(j["itens"][0]) >= {"id", "ocasiao", "texto", "origem", "status", "criado_em"}
    assert "criado_por" not in j["itens"][0]
    pend = cli.get("/api/admin/ia/saudacoes?status=pendente", headers=h).get_json()["itens"]
    assert [i["texto"] for i in pend] == ["Natal A"] and pend[0]["acoes_permitidas"] == [
        "aprovar",
        "rejeitar",
    ]
    so_pascoa = cli.get("/api/admin/ia/saudacoes?ocasiao=pascoa", headers=h).get_json()["itens"]
    assert [i["texto"] for i in so_pascoa] == ["Páscoa A"]
    assert cli.get("/api/admin/ia/saudacoes?status=xx", headers=h).status_code == 400
    assert cli.get("/api/admin/ia/saudacoes?ocasiao=xx", headers=h).status_code == 400


def test_frase_aprovada_aparece_na_saudacao(cli, repo, auth, saud, adm_h, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _, h = adm_h
    # força a ocasião: o serviço usa o relógio do sistema, então fixamos via ServicoSaudacao
    import app.rotas.saudacao as rotas

    original = rotas.ServicoSaudacao
    monkeypatch.setattr(
        rotas,
        "ServicoSaudacao",
        lambda ia, r: original(ia, r, agora=lambda: em(2026, 12, 25, 9)),
    )
    for i in range(60):
        saud.semear(
            ocasiao="natal",
            texto=f"Natal exclusivo {i}",
            origem="manual",
            status="aprovada",
            criado_por="x",
        )
    saud.semear(
        ocasiao="natal", texto="Natal rejeitado", origem="ia", status="rejeitada", criado_por="x"
    )
    saud.semear(
        ocasiao="natal", texto="Natal pendente", origem="ia", status="pendente", criado_por="x"
    )
    vistos = set()
    for _ in range(80):
        p = repo.novo_perfil("solicitante")
        vistos.add(cli.get("/api/saudacao", headers=auth(p.id)).get_json()["texto"])
    assert any(t.startswith("Natal exclusivo") for t in vistos)
    assert "Natal rejeitado" not in vistos and "Natal pendente" not in vistos


# ------------------------------------------------------------------------------ aprovar / rejeitar
def _pendente(saud: RepoSaudacoesMemoria, texto: str = "Frase pendente") -> str:
    return saud.semear(
        ocasiao="natal", texto=texto, origem="ia", status="pendente", criado_por="x"
    )["id"]


def test_aprovar_e_rejeitar_pendente(cli, adm_h, saud) -> None:  # type: ignore[no-untyped-def]
    p, h = adm_h
    a, b = _pendente(saud, "Frase A"), _pendente(saud, "Frase B")
    r = cli.post(f"/api/admin/ia/saudacoes/{a}/aprovar", headers=h)
    assert r.status_code == 200 and r.get_json()["status"] == "aprovada"
    assert r.get_json()["acoes_permitidas"] == ["rejeitar"]
    r = cli.post(f"/api/admin/ia/saudacoes/{b}/rejeitar", headers=h)
    assert r.status_code == 200 and r.get_json()["status"] == "rejeitada"
    assert all(x["decidido_por"] == p.id for x in saud.linhas)


def test_transicoes_permitidas_e_repeticao_recusada(cli, adm_h, saud) -> None:  # type: ignore[no-untyped-def]
    h = adm_h[1]
    sid = _pendente(saud)
    url = f"/api/admin/ia/saudacoes/{sid}"
    assert cli.post(f"{url}/aprovar", headers=h).get_json()["acoes_permitidas"] == ["rejeitar"]
    r = cli.post(f"{url}/aprovar", headers=h)  # mesmo estado de novo
    assert r.status_code == 409 and r.get_json()["erro"]["codigo"] == "transicao_invalida"
    r = cli.post(f"{url}/rejeitar", headers=h)  # aprovada -> rejeitada é permitido
    assert r.status_code == 200 and r.get_json()["acoes_permitidas"] == [
        "aprovar",
        "excluir",
    ]
    assert cli.post(f"{url}/rejeitar", headers=h).status_code == 409
    assert cli.post(f"{url}/aprovar", headers=h).status_code == 200  # rejeitada -> aprovada
    assert saud.linhas[0]["status"] == "aprovada"


def test_nunca_volta_a_pendente(cli, adm_h, saud) -> None:  # type: ignore[no-untyped-def]
    sid = _pendente(saud)
    cli.post(f"/api/admin/ia/saudacoes/{sid}/aprovar", headers=adm_h[1])
    # a API não expõe ação de voltar a pendente
    assert cli.post(f"/api/admin/ia/saudacoes/{sid}/pendente", headers=adm_h[1]).status_code == 404


def test_decidir_inexistente_ou_id_invalido(cli, adm_h) -> None:  # type: ignore[no-untyped-def]
    h = adm_h[1]
    r = cli.post("/api/admin/ia/saudacoes/11111111-1111-4111-8111-111111111111/aprovar", headers=h)
    assert r.status_code == 404
    assert cli.post("/api/admin/ia/saudacoes/nao-e-uuid/aprovar", headers=h).status_code == 400


# ------------------------------------------------------------------------------ geração
def test_extrair_frases_limpa_numeracao_e_aspas() -> None:
    bruto = '1. "Primeira frase"\n- Segunda frase\n\n• Terceira\n  3) Quarta ok\n'
    assert extrair_frases(bruto) == ["Primeira frase", "Segunda frase", "Terceira", "Quarta ok"]


def test_gerar_insere_pendentes_origem_ia(cli, adm_h, saud, ia_env) -> None:  # type: ignore[no-untyped-def]
    p, h = adm_h
    _, repo_ia, prov = ia_env
    prov.respostas = [Resultado("1. Boas festas, {nome}!\n2. Natal de paz para todos.\n", 30, 20)]
    r = cli.post(
        "/api/admin/ia/saudacoes/gerar", json={"ocasiao": "natal", "quantidade": 5}, headers=h
    )
    assert r.status_code == 201
    j = r.get_json()
    assert [i["texto"] for i in j["itens"]] == ["Boas festas, {nome}!", "Natal de paz para todos."]
    assert all(i["status"] == "pendente" and i["origem"] == "ia" for i in j["itens"])
    assert all(x["criado_por"] == p.id and x["status"] == "pendente" for x in saud.linhas)
    # uso registrado com a função e o ator
    assert repo_ia.usos[-1]["funcao"] == "saudacao" and repo_ia.usos[-1]["usuario_id"] == p.id
    # pendente não vira saudação do usuário
    assert not any(x["status"] == "aprovada" for x in saud.linhas)


def test_gerar_descarta_saidas_ruins(cli, adm_h, saud, ia_env) -> None:  # type: ignore[no-untyped-def]
    _, h = adm_h
    ruim_longo = "x" * 81
    prov = ia_env[2]
    prov.respostas = [
        Resultado(
            "\n".join(
                [
                    "ok",  # curta demais
                    ruim_longo,
                    "Veja <b>isso</b> agora",
                    "Acesse https://golpe.com já",
                    "Olá {unidade} tudo bem",
                    "Feliz Natal, {nome}!",  # duplica o catálogo
                    "FELIZ NATAL, {nome}!",  # idem (normalizada)
                    "Natal de luz e paz",
                    "natal de luz e paz",  # duplicada no lote
                    "Final de ano leve, {nome}.",
                ]
            ),
            10,
            10,
        )
    ]
    r = cli.post(
        "/api/admin/ia/saudacoes/gerar", json={"ocasiao": "natal", "quantidade": 10}, headers=h
    )
    j = r.get_json()
    assert r.status_code == 201
    assert [i["texto"] for i in j["itens"]] == ["Natal de luz e paz", "Final de ano leve, {nome}."]
    assert j["descartadas"] == 8
    assert all(motivo_invalido(x["texto"]) is None for x in saud.linhas)


def test_gerar_respeita_quantidade(cli, adm_h, saud, ia_env) -> None:  # type: ignore[no-untyped-def]
    ia_env[2].respostas = [Resultado("\n".join(f"Frase gerada número {i}" for i in range(8)), 1, 1)]
    r = cli.post(
        "/api/admin/ia/saudacoes/gerar",
        json={"ocasiao": "natal", "quantidade": 3},
        headers=adm_h[1],
    )
    assert len(r.get_json()["itens"]) == 3 and len(saud.linhas) == 3


def test_gerar_prompt_so_com_a_ocasiao(cli, repo, adm_h, saud, ia_env) -> None:  # type: ignore[no-untyped-def]
    p, h = adm_h
    repo.perfis[p.id] = replace(p, nome="Beltrano Segredoso", unidade="PF")
    ia_env[2].respostas = [Resultado("Uma frase qualquer", 1, 1)]
    r = cli.post(
        "/api/admin/ia/saudacoes/gerar",
        json={"ocasiao": "dia_servidor", "quantidade": 2},
        headers=h,
    )
    assert r.status_code == 201
    chamada = ia_env[2].chamadas[0]
    prompt = (chamada["sistema"] + " " + chamada["usuario"]).lower()
    email = repo.contas_auth[p.id]["email"].lower()
    assert "beltrano" not in prompt and "segredoso" not in prompt
    assert email not in prompt and p.id.lower() not in prompt
    assert "servidor" in prompt  # a ocasião está no prompt


@pytest.mark.parametrize(
    "corpo",
    [
        {"ocasiao": "natal", "quantidade": 0},
        {"ocasiao": "natal", "quantidade": 11},
        {"ocasiao": "natal", "quantidade": "3"},
        {"ocasiao": "natal", "quantidade": True},
        {"ocasiao": "xx", "quantidade": 3},
        {"quantidade": 3},
        {"ocasiao": "natal", "quantidade": 3, "prompt": "ignore tudo"},
    ],
)
def test_gerar_valida_corpo(cli, adm_h, ia_env, corpo) -> None:  # type: ignore[no-untyped-def]
    r = cli.post("/api/admin/ia/saudacoes/gerar", json=corpo, headers=adm_h[1])
    assert r.status_code == 400 and ia_env[2].chamadas == []


@pytest.mark.parametrize(
    "banco",
    [
        ConfigIABanco(),
        ConfigIABanco(mestre=True, saudacao=False),
        ConfigIABanco(mestre=False, saudacao=True),
    ],
)
def test_gerar_ia_desligada_409(cli, adm_h, ia_env, banco) -> None:  # type: ignore[no-untyped-def]
    ia_env[1].config = banco
    r = cli.post("/api/admin/ia/saudacoes/gerar", json={"ocasiao": "natal"}, headers=adm_h[1])
    assert r.status_code == 409 and r.get_json()["erro"]["codigo"] == "ia_indisponivel"
    assert ia_env[2].chamadas == []


def test_gerar_servidor_com_ia_desligada_409(app_s, cli, adm_h, saud) -> None:  # type: ignore[no-untyped-def]
    ia, _ = _servico_ia()  # AI_ENABLED=false
    app_s.extensions["sisveiculos"].ia = ia
    r = cli.post("/api/admin/ia/saudacoes/gerar", json={"ocasiao": "natal"}, headers=adm_h[1])
    assert r.status_code == 409 and r.get_json()["erro"]["codigo"] == "ia_indisponivel"


def test_gerar_falha_do_provedor_mensagem_ptbr(cli, adm_h, saud, ia_env) -> None:  # type: ignore[no-untyped-def]
    from tests.fakes_ia import erro

    ia_env[2].respostas = [erro("http")]
    r = cli.post("/api/admin/ia/saudacoes/gerar", json={"ocasiao": "natal"}, headers=adm_h[1])
    assert r.status_code == 502
    e = r.get_json()["erro"]
    assert e["codigo"] == "ia_falhou" and "Não foi possível gerar" in e["mensagem"]
    assert saud.linhas == []


def test_gerar_rate_limit_por_usuario_e_nao_por_ip(cli, repo, auth, adm_h, ia_env) -> None:  # type: ignore[no-untyped-def]
    _, h = adm_h
    for _ in range(5):
        ia_env[2].respostas.append(Resultado("Frase única " + str(_), 1, 1))
    codigos = [
        cli.post(
            "/api/admin/ia/saudacoes/gerar", json={"ocasiao": "natal", "quantidade": 1}, headers=h
        ).status_code
        for _ in range(6)
    ]
    assert codigos[:5] == [201] * 5 and codigos[5] == 429
    # outro admin não é afetado (limite por usuário)
    outro = repo.novo_perfil("admin")
    ia_env[2].respostas.append(Resultado("Outra frase única", 1, 1))
    r = cli.post(
        "/api/admin/ia/saudacoes/gerar",
        json={"ocasiao": "natal", "quantidade": 1},
        headers=auth(outro.id),
    )
    assert r.status_code == 201


def test_gerar_nao_loga_prompt_nem_resposta(cli, adm_h, ia_env, caplog) -> None:  # type: ignore[no-untyped-def]
    ia_env[2].respostas = [Resultado("Frase secreta de teste", 1, 1)]
    with caplog.at_level(logging.DEBUG):
        cli.post(
            "/api/admin/ia/saudacoes/gerar",
            json={"ocasiao": "natal", "quantidade": 1},
            headers=adm_h[1],
        )
    assert "Frase secreta" not in caplog.text


def test_sem_repo_de_saudacoes_admin_503(  # type: ignore[no-untyped-def]
    repo, auth_admin, storage, avatares, ia_env, auth
) -> None:
    cfg = Config(
        jwt_issuer=ISSUER, jwt_audience=AUDIENCE, testing=True, url_aceite_convite=URL_ACEITE
    )
    verificador = VerificadorJWT(
        issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
    )
    a = create_app(
        cfg,
        repo=repo,
        verificador=verificador,
        auth_admin=auth_admin,
        armazenamento=storage,
        armazenamento_avatares=avatares,
        servico_ia=ia_env[0],
    )
    limiter.reset()
    adm = repo.novo_perfil("admin")
    c = a.test_client()
    assert c.get("/api/admin/ia/saudacoes", headers=auth(adm.id)).status_code == 503
    sol = repo.novo_perfil("solicitante")
    assert c.get("/api/saudacao", headers=auth(sol.id)).get_json()["texto"]  # catálogo basta


def test_validacao_rejeita_arroba_e_dominio_mas_aceita_pontuacao_comum() -> None:
    for ruim in ("escreva a@b.gov", "@fulano", "acesse exemplo.com", "ver agu.gov.br", "ok.ORG"):
        assert motivo_invalido(ruim) is not None, ruim
    for bom in (
        "Bom dia. Tudo bem?",
        "Dia da Independência. Bom feriado!",
        "Olá, {nome}.Bem-vindo",
    ):
        assert motivo_invalido(bom) is None, bom


def test_gerar_descarta_email_e_dominio(cli, adm_h, saud, ia_env) -> None:  # type: ignore[no-untyped-def]
    ia_env[2].respostas = [
        Resultado(
            "Fale com a@b.com" + chr(10) + "Visite agu.gov.br" + chr(10) + "Natal de paz e luz",
            1,
            1,
        )
    ]
    r = cli.post(
        "/api/admin/ia/saudacoes/gerar",
        json={"ocasiao": "natal", "quantidade": 5},
        headers=adm_h[1],
    )
    assert [i["texto"] for i in r.get_json()["itens"]] == ["Natal de paz e luz"]
    assert r.get_json()["descartadas"] == 2


def test_get_saudacao_rate_limit_por_usuario(cli, repo, auth) -> None:  # type: ignore[no-untyped-def]
    a, b = repo.novo_perfil("solicitante"), repo.novo_perfil("solicitante")
    codigos = [cli.get("/api/saudacao", headers=auth(a.id)).status_code for _ in range(31)]
    assert codigos[:30] == [200] * 30 and codigos[30] == 429
    assert "erro" in cli.get("/api/saudacao", headers=auth(a.id)).get_json()
    assert (
        cli.get("/api/saudacao", headers=auth(b.id)).status_code == 200
    )  # mesmo IP, outro usuário


def test_cache_evita_reler_o_banco_e_expira(ia_env, saud) -> None:  # type: ignore[no-untyped-def]
    from app.saudacao.servico import CacheTTL

    relogio = [0.0]
    c = CacheTTL(ttl=45, relogio=lambda: relogio[0])
    chamadas: list[int] = []
    assert c.obter("k", lambda: chamadas.append(1) or "v") == "v"
    assert c.obter("k", lambda: chamadas.append(1) or "v") == "v"
    assert len(chamadas) == 1
    relogio[0] = 46
    c.obter("k", lambda: chamadas.append(1) or "v")
    assert len(chamadas) == 2
    c.limpar()
    c.obter("k", lambda: chamadas.append(1) or "v")
    assert len(chamadas) == 3


def test_servico_usa_cache_do_gate_e_aprovacao_limpa_aprovadas() -> None:
    ia, repo_ia = _servico_ia()
    leituras = []
    original = repo_ia.ler_config
    repo_ia.ler_config = lambda: leituras.append(1) or original()  # type: ignore[method-assign]
    s = ServicoSaudacao(ia, RepoSaudacoesMemoria(), agora=lambda: em(2026, 11, 10, 9))
    for _ in range(5):
        s.para_usuario(_perfil())
    assert len(leituras) == 1


def test_manual_com_falha_na_aprovacao_devolve_pendente_com_aviso(  # type: ignore[no-untyped-def]
    cli, adm_h, saud, monkeypatch
) -> None:
    monkeypatch.setattr(
        saud, "decidir", lambda *a, **k: (_ for _ in ()).throw(ErroApi("x", "indisponivel", 503))
    )
    r = cli.post(
        "/api/admin/ia/saudacoes",
        json={"ocasiao": "natal", "texto": "Frase manual nova"},
        headers=adm_h[1],
    )
    assert r.status_code == 201
    j = r.get_json()
    assert (
        j["status"] == "pendente"
        and "pendente" in j["aviso"]
        and j["acoes_permitidas"] == ["aprovar", "rejeitar"]
    )
    assert saud.linhas[0]["status"] == "pendente"


# ------------------------------------------------------------------------------ excluir rejeitada
_URL = "/api/admin/ia/saudacoes"
_INEXISTENTE = "22222222-2222-4222-8222-222222222222"


def _semear(saud, adm_id: str, status: str, texto: str = "Boa tarde") -> str:  # type: ignore[no-untyped-def]
    linha = saud.semear(
        status=status, ocasiao="tarde", texto=texto, origem="manual", criado_por=adm_id
    )
    return str(linha["id"])


def test_admin_exclui_rejeitada(cli, adm_h, saud) -> None:  # type: ignore[no-untyped-def]
    p, h = adm_h
    sid = _semear(saud, p.id, "rejeitada")
    r = cli.delete(f"{_URL}/{sid}", headers=h)
    assert r.status_code == 200 and r.get_json() == {"id": sid, "excluida": True}
    assert saud.obter(sid) is None
    assert cli.delete(f"{_URL}/{sid}", headers=h).status_code == 404  # já excluída


@pytest.mark.parametrize("status", ["pendente", "aprovada"])
def test_exclusao_recusa_pendente_e_aprovada(cli, adm_h, saud, status) -> None:  # type: ignore[no-untyped-def]
    p, h = adm_h
    sid = _semear(saud, p.id, status)
    r = cli.delete(f"{_URL}/{sid}", headers=h)
    assert r.status_code == 409
    assert set(r.get_json()["erro"]) == {"codigo", "mensagem"}
    assert saud.obter(sid) is not None  # nada foi apagado


def test_exclusao_inexistente_404_e_id_invalido(cli, adm_h) -> None:  # type: ignore[no-untyped-def]
    _, h = adm_h
    r = cli.delete(f"{_URL}/{_INEXISTENTE}", headers=h)
    assert r.status_code == 404 and set(r.get_json()["erro"]) == {"codigo", "mensagem"}
    assert cli.delete(f"{_URL}/nao-e-uuid", headers=h).status_code in (400, 404)


@pytest.mark.parametrize("role", ["solicitante", "autorizador"])
def test_exclusao_nao_admin_403(cli, repo, auth, saud, adm_h, role) -> None:  # type: ignore[no-untyped-def]
    sid = _semear(saud, adm_h[0].id, "rejeitada")
    u = repo.novo_perfil(role)
    assert cli.delete(f"{_URL}/{sid}", headers=auth(u.id)).status_code == 403
    assert saud.obter(sid) is not None


def test_exclusao_sem_jwt_401(cli) -> None:  # type: ignore[no-untyped-def]
    assert cli.delete(f"{_URL}/{_INEXISTENTE}").status_code == 401


def test_acoes_permitidas_por_status_com_excluir(cli, adm_h, saud) -> None:  # type: ignore[no-untyped-def]
    p, h = adm_h
    for i, status in enumerate(("pendente", "aprovada", "rejeitada")):
        _semear(saud, p.id, status, f"Frase {i}")
    itens = {
        i["status"]: i["acoes_permitidas"] for i in cli.get(_URL, headers=h).get_json()["itens"]
    }
    assert itens == {
        "pendente": ["aprovar", "rejeitar"],
        "aprovada": ["rejeitar"],
        "rejeitada": ["aprovar", "excluir"],
    }


def test_exclusao_grava_auditoria_sem_texto(cli, adm_h, saud) -> None:  # type: ignore[no-untyped-def]
    p, h = adm_h
    sid = _semear(saud, p.id, "rejeitada", "Frase sigilosa")
    assert cli.delete(f"{_URL}/{sid}", headers=h).status_code == 200
    assert saud.auditoria == [{"acao": "saudacao_excluida", "registro_id": sid, "ator_id": p.id}]


def test_exclusao_corrida_ag003_vira_409(cli, adm_h, saud, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A frase mudou de status entre a leitura e o RPC: o AG003 traduzido vira 409."""
    from app.errors import TransicaoInvalida

    p, h = adm_h
    sid = _semear(saud, p.id, "rejeitada")

    def _recusa(*_a, **_k):  # type: ignore[no-untyped-def]
        raise TransicaoInvalida("Só frases rejeitadas podem ser excluídas.")

    monkeypatch.setattr(saud, "excluir_rejeitada", _recusa)
    r = cli.delete(f"{_URL}/{sid}", headers=h)
    assert r.status_code == 409 and r.get_json()["erro"]["codigo"] == "transicao_invalida"


def test_exclusao_ator_invalido_ag002_vira_403(cli, adm_h, saud) -> None:  # type: ignore[no-untyped-def]
    p, h = adm_h
    sid = _semear(saud, p.id, "rejeitada")
    saud.atores_invalidos.add(p.id)  # o banco já não o vê como admin ativo
    r = cli.delete(f"{_URL}/{sid}", headers=h)
    assert r.status_code == 403 and set(r.get_json()["erro"]) == {"codigo", "mensagem"}
    assert saud.obter(sid) is not None


def test_exclusao_corrida_id_some_vira_404(cli, adm_h, saud, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    p, h = adm_h
    sid = _semear(saud, p.id, "rejeitada")
    monkeypatch.setattr(saud, "excluir_rejeitada", lambda *_a, **_k: False)
    assert cli.delete(f"{_URL}/{sid}", headers=h).status_code == 404
