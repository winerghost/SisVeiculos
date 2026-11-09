"""Teste de CONTRATO: rota Flask real -> ServicoRequisicoes -> SupabaseRepositorio -> Postgres.

Roda a API de verdade (create_app com `SupabaseRepositorio`) contra um Supabase LOCAL e
DESCARTÁVEL (`supabase start`, migrations aplicadas e primeiro admin semeado). Sem as variáveis
abaixo, TUDO é pulado (nunca falha a suíte normal):

    CONTRATO_SUPABASE_URL=http://127.0.0.1:54321
    CONTRATO_SERVICE_ROLE_KEY=<service_role do `supabase status`>
    CONTRATO_CONFIRM=descartavel          # guarda técnica (tests/guarda_banco.py)

    cd backend && python -m pytest -m contrato

Pré-requisitos do banco: migrations aplicadas, ao menos UM admin ativo (o primeiro admin nasce por
seed, `scripts/seed-admin.sh`; não há como criá-lo pela API) e recursos opcionais desligados
(quilometragem, checklist, motorista: estado padrão após `supabase db reset`).

Dados: cada teste cria suas contas (Auth admin API + `perfis`) e seu veículo, com identificadores
aleatórios, e ao final cancela o que ficou aberto e DESATIVA veículo e perfis (não há DELETE
físico; a auditoria é append-only). Nenhum ID fixo.

JWT: o app aceita o verificador injetado. Aqui o token é assinado por uma chave local (a mesma
estratégia de conftest.py); a verificação de JWKS/assinatura do GoTrue e de TOTP/AAL2 reais NÃO é
coberta por este arquivo.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import suppress
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from flask import Flask
from flask.testing import FlaskClient
from postgrest.exceptions import APIError

from app import create_app
from app.auth.jwt import VerificadorJWT
from app.config import Config
from app.errors import VeiculoOcupado
from app.extensions import limiter
from app.modelos import Perfil
from app.repositorios import supabase_repo
from app.repositorios.supabase_repo import INDICE_VEICULO_ABERTO, SupabaseRepositorio
from app.services.requisicoes import MSG_OCUPADO
from tests.guarda_banco import avaliar, marca_pular

_LIBERACAO = avaliar("CONTRATO_SUPABASE_URL", "CONTRATO_SERVICE_ROLE_KEY")

pytestmark = [pytest.mark.contrato, marca_pular(_LIBERACAO)]

ISSUER = "http://127.0.0.1:54321/auth/v1"
AUDIENCE = "authenticated"
_CHAVE = ec.generate_private_key(ec.SECP256R1())
ESTADOS_ABERTOS = ("solicitada", "autorizada")


def _token(sub: str) -> str:
    agora = int(time.time())
    claims = {
        "sub": sub,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": agora,
        "exp": agora + 600,
        # Se o banco tiver MFA ligado, o token já carrega o segundo fator (como o Supabase emite).
        "aal": "aal2",
        "amr": [{"method": "totp", "timestamp": agora}],
    }
    return jwt.encode(claims, _CHAVE, algorithm="ES256", headers={"kid": "contrato"})


def _corpo(veiculo_id: str, **extra: Any) -> dict[str, Any]:
    base = {
        "nome_solicitante": "Contrato Automatizado",
        "unidade": "PU",
        "hora_saida": "2026-12-01T08:00:00-03:00",
        "trajeto": "AGU -> Fórum (teste de contrato)",
        "veiculo_id": veiculo_id,
    }
    return {**base, **extra}


def _dados_repo(veiculo_id: str, usuario_id: str) -> dict[str, Any]:
    return {**_corpo(veiculo_id), "usuario_id": usuario_id}


class Fabrica:
    """Cria contas, perfis e veículos únicos e desfaz (desativa) tudo ao final."""

    def __init__(self, repo: SupabaseRepositorio, admin_id: str) -> None:
        self.repo = repo
        self.admin_id = admin_id
        self._perfis: list[str] = []
        self._veiculos: list[str] = []
        self.requisicoes: list[str] = []

    def perfil(self, role: str) -> Perfil:
        sufixo = uuid.uuid4().hex[:10]
        conta = self.repo._c.auth.admin.create_user(
            {
                "email": f"contrato-{role}-{sufixo}@teste.example.com",
                "password": secrets.token_urlsafe(24),
                "email_confirm": True,
            }
        )
        uid = str(conta.user.id)
        self.repo._c.table("perfis").insert(
            {"id": uid, "nome": f"Contrato {role} {sufixo}", "unidade": "UEA", "role": role}
        ).execute()
        self._perfis.append(uid)
        perfil = self.repo.obter_perfil(uid)
        assert perfil is not None and perfil.role == role and perfil.ativo
        return perfil

    def veiculo(self) -> str:
        sufixo = uuid.uuid4().hex[:10]
        v = self.repo.inserir_veiculo(
            {"descricao": f"Veículo contrato {sufixo}"}, ator_id=self.admin_id
        )
        self._veiculos.append(str(v["id"]))
        return str(v["id"])

    def rastrear(self, requisicao_id: str) -> str:
        self.requisicoes.append(requisicao_id)
        return requisicao_id

    def limpar(self) -> None:
        """Melhor esforço: a falha da limpeza nunca mascara o resultado do teste."""
        for rid in self.requisicoes:
            with suppress(Exception):
                req = self.repo.obter_requisicao(rid)
                if req is not None and req["status"] in ESTADOS_ABERTOS:
                    self.repo.transicionar(
                        rid, req["status"], {"status": "cancelada", "cancelada_por": self.admin_id}
                    )
        for vid in self._veiculos:
            with suppress(Exception):
                self.repo.atualizar_veiculo(vid, {"ativo": False}, ator_id=self.admin_id)
        for uid in self._perfis:
            with suppress(Exception):
                self.repo.atualizar_perfil(uid, {"ativo": False}, ator_id=self.admin_id)


@pytest.fixture(scope="module")
def repo_real() -> SupabaseRepositorio:
    return SupabaseRepositorio(_LIBERACAO.url, _LIBERACAO.chave)


@pytest.fixture(scope="module")
def admin_id(repo_real: SupabaseRepositorio) -> str:
    admins = repo_real.listar_perfis(role="admin", ativo=True, limite=1, offset=0)
    if not admins:
        pytest.fail(
            "O banco local não tem admin ativo. Semeie o primeiro admin (scripts/seed-admin.sh "
            "com SEED_ADMIN_EMAIL de uma conta já criada no Auth local) antes de rodar o contrato."
        )
    return str(admins[0]["id"])


@pytest.fixture(scope="module", autouse=True)
def _recursos_opcionais_desligados(repo_real: SupabaseRepositorio) -> None:
    cfg = repo_real.ler_configuracoes()
    ligados = [n for n in ("quilometragem", "checklist", "motorista") if getattr(cfg, n)]
    if ligados:
        pytest.fail(
            "Recursos opcionais ligados no banco local ("
            + ", ".join(ligados)
            + "): o contrato cobre o fluxo base. Rode `supabase db reset`."
        )


@pytest.fixture
def fabrica(repo_real: SupabaseRepositorio, admin_id: str) -> Iterator[Fabrica]:
    f = Fabrica(repo_real, admin_id)
    try:
        yield f
    finally:
        f.limpar()


@pytest.fixture
def app_real(repo_real: SupabaseRepositorio) -> Flask:
    cfg = Config(
        jwt_issuer=ISSUER,
        jwt_audience=AUDIENCE,
        testing=True,
        limite_padrao="10000 per minute",
        limite_escrita="10000 per minute",
    )
    verificador = VerificadorJWT(
        issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
    )
    aplicacao = create_app(cfg, repo=repo_real, verificador=verificador)
    limiter.reset()
    return aplicacao


@pytest.fixture
def api(app_real: Flask) -> Callable[..., Any]:
    """Chama a rota real: api(perfil, "POST", "/requisicoes", json=...) -> resposta."""

    def chamar(
        perfil: Perfil | None, metodo: str, caminho: str, *, json: Any = None, client: Any = None
    ) -> Any:
        cli: FlaskClient = client or app_real.test_client()
        cab = {"Authorization": f"Bearer {_token(perfil.id)}"} if perfil else {}
        return cli.open(f"/api{caminho}", method=metodo, headers=cab, json=json)

    return chamar


@pytest.fixture
def erros_banco(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Espia `traduzir_erro_banco`: registra (SQLSTATE, texto) dos erros que vieram do Postgres."""
    original = supabase_repo.traduzir_erro_banco
    registro: list[tuple[str, str]] = []

    def espiao(e: APIError, *, admin: bool = False) -> Any:
        texto = f"{getattr(e, 'message', '')} {getattr(e, 'details', '')}"
        registro.append((str(getattr(e, "code", "") or ""), texto))
        return original(e, admin=admin)

    monkeypatch.setattr(supabase_repo, "traduzir_erro_banco", espiao)
    return registro


def _criar(api: Callable[..., Any], fabrica: Fabrica, perfil: Perfil, veiculo: str) -> Any:
    r = api(perfil, "POST", "/requisicoes", json=_corpo(veiculo))
    if r.status_code == 201:
        fabrica.rastrear(r.get_json()["id"])
    return r


def _erro(resposta: Any) -> dict[str, str]:
    corpo = resposta.get_json()
    assert set(corpo) == {"erro"}
    assert set(corpo["erro"]) == {"codigo", "mensagem"}
    return corpo["erro"]


# ---------------------------------------------------------------- fluxo feliz
def test_fluxo_completo_ate_concluida(api, fabrica) -> None:  # type: ignore[no-untyped-def]
    sol, aut = fabrica.perfil("solicitante"), fabrica.perfil("autorizador")
    veiculo = fabrica.veiculo()

    r = _criar(api, fabrica, sol, veiculo)
    assert r.status_code == 201
    req = r.get_json()
    rid = req["id"]
    assert req["status"] == "solicitada" and req["usuario_id"] == sol.id
    assert "cancelar" in req["acoes_permitidas"] and "autorizar" not in req["acoes_permitidas"]

    r = api(aut, "POST", f"/requisicoes/{rid}/autorizar")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["status"] == "autorizada" and r.get_json()["autorizado_por"] == aut.id
    assert r.get_json()["autorizado_em"]

    r = api(aut, "POST", f"/requisicoes/{rid}/saida", json={})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["status"] == "em_uso"
    assert r.get_json()["saida_real_em"] and r.get_json()["saida_registrada_por"] == aut.id
    assert r.get_json()["chegada_real_em"] is None

    r = api(aut, "POST", f"/requisicoes/{rid}/chegada", json={})
    assert r.status_code == 200, r.get_json()
    fim = r.get_json()
    assert fim["status"] == "concluida" and fim["chegada_registrada_por"] == aut.id
    assert fim["chegada_real_em"] > fim["saida_real_em"]
    assert fim["acoes_permitidas"] == []

    linha = api(sol, "GET", f"/requisicoes/{rid}").get_json()  # releitura pelo dono
    assert linha["status"] == "concluida"

    # concluída libera o veículo (índice parcial só cobre os status abertos)
    assert _criar(api, fabrica, sol, veiculo).status_code == 201


def test_cancelar_pelo_dono_libera_o_veiculo(api, fabrica) -> None:  # type: ignore[no-untyped-def]
    sol = fabrica.perfil("solicitante")
    veiculo = fabrica.veiculo()
    rid = _criar(api, fabrica, sol, veiculo).get_json()["id"]

    r = api(sol, "POST", f"/requisicoes/{rid}/cancelar")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["status"] == "cancelada" and r.get_json()["acoes_permitidas"] == []

    assert _criar(api, fabrica, sol, veiculo).status_code == 201


def test_negar_encerra_e_bloqueia_novas_transicoes(api, fabrica) -> None:  # type: ignore[no-untyped-def]
    sol, aut = fabrica.perfil("solicitante"), fabrica.perfil("autorizador")
    veiculo = fabrica.veiculo()
    rid = _criar(api, fabrica, sol, veiculo).get_json()["id"]

    r = api(aut, "POST", f"/requisicoes/{rid}/negar")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["status"] == "negada"

    r = api(aut, "POST", f"/requisicoes/{rid}/autorizar")
    assert r.status_code == 409 and _erro(r)["codigo"] == "transicao_invalida"
    assert _criar(api, fabrica, sol, veiculo).status_code == 201  # negada libera o veículo


# ---------------------------------------------------------------- permissões e IDOR
def test_solicitante_nao_autoriza(api, fabrica) -> None:  # type: ignore[no-untyped-def]
    sol = fabrica.perfil("solicitante")
    rid = _criar(api, fabrica, sol, fabrica.veiculo()).get_json()["id"]

    r = api(sol, "POST", f"/requisicoes/{rid}/autorizar")
    assert r.status_code == 403 and _erro(r)["codigo"] == "sem_permissao"


def test_sem_token_da_401_no_formato_de_erro(api) -> None:  # type: ignore[no-untyped-def]
    r = api(None, "GET", "/requisicoes")
    assert r.status_code == 401
    assert _erro(r)["codigo"] == "nao_autenticado"


def test_idor_solicitante_nao_ve_nem_cancela_requisicao_alheia(api, fabrica) -> None:  # type: ignore[no-untyped-def]
    a, b = fabrica.perfil("solicitante"), fabrica.perfil("solicitante")
    aut = fabrica.perfil("autorizador")
    rid = _criar(api, fabrica, a, fabrica.veiculo()).get_json()["id"]

    r = api(b, "GET", f"/requisicoes/{rid}")
    assert r.status_code == 404 and _erro(r)["codigo"] == "nao_encontrado"
    assert api(b, "POST", f"/requisicoes/{rid}/cancelar").status_code == 404
    ids_b = {x["id"] for x in api(b, "GET", "/requisicoes").get_json()["itens"]}
    assert rid not in ids_b

    ids_a = {x["id"] for x in api(a, "GET", "/requisicoes").get_json()["itens"]}
    assert rid in ids_a
    assert api(aut, "GET", f"/requisicoes/{rid}").status_code == 200  # a equipe vê tudo
    # a tentativa do outro não alterou nada no banco
    assert fabrica.repo.obter_requisicao(rid)["status"] == "solicitada"  # type: ignore[index]


# ---------------------------------------------------------------- veículo ocupado
def test_segunda_criacao_no_mesmo_veiculo_da_409(api, fabrica) -> None:  # type: ignore[no-untyped-def]
    a, b = fabrica.perfil("solicitante"), fabrica.perfil("solicitante")
    veiculo = fabrica.veiculo()
    assert _criar(api, fabrica, a, veiculo).status_code == 201

    r = _criar(api, fabrica, b, veiculo)
    assert r.status_code == 409
    erro = _erro(r)
    assert erro["codigo"] == "veiculo_ocupado" and erro["mensagem"] == MSG_OCUPADO


def test_indice_do_banco_recusa_insercao_direta_com_mensagem_traduzida(  # type: ignore[no-untyped-def]
    fabrica, erros_banco
) -> None:
    a, b = fabrica.perfil("solicitante"), fabrica.perfil("solicitante")
    veiculo = fabrica.veiculo()
    primeira = fabrica.repo.inserir_requisicao(_dados_repo(veiculo, a.id))
    fabrica.rastrear(primeira["id"])

    with pytest.raises(VeiculoOcupado) as e:  # sem o pré-check do serviço: quem decide é o índice
        fabrica.repo.inserir_requisicao(_dados_repo(veiculo, b.id))
    assert e.value.codigo == "veiculo_ocupado" and e.value.status == 409
    assert e.value.mensagem == supabase_repo.MSG_VEICULO_OCUPADO
    assert len(erros_banco) == 1
    sqlstate, texto = erros_banco[0]
    assert sqlstate == "23505" and INDICE_VEICULO_ABERTO in texto


# ---------------------------------------------------------------- concorrência real
def test_concorrencia_no_repositorio_o_indice_decide(fabrica, erros_banco) -> None:  # type: ignore[no-untyped-def]
    """Duas threads, Barrier, `inserir_requisicao` direto (sem pré-check): só uma persiste e o
    perdedor recebe VeiculoOcupado originado do erro 23505 do índice parcial."""
    a, b = fabrica.perfil("solicitante"), fabrica.perfil("solicitante")
    veiculo = fabrica.veiculo()
    barreira = threading.Barrier(2)
    resultados: list[Any] = []
    trava = threading.Lock()

    def tentar(perfil: Perfil) -> None:
        barreira.wait(timeout=10)
        try:
            r: Any = fabrica.repo.inserir_requisicao(_dados_repo(veiculo, perfil.id))
        except VeiculoOcupado as ex:
            r = ex
        with trava:
            resultados.append(r)

    ts = [threading.Thread(target=tentar, args=(p,)) for p in (a, b)]
    [t.start() for t in ts]
    [t.join(timeout=30) for t in ts]

    criadas = [r for r in resultados if isinstance(r, dict)]
    recusadas = [r for r in resultados if isinstance(r, VeiculoOcupado)]
    for r in criadas:
        fabrica.rastrear(r["id"])
    assert len(criadas) == 1 and len(recusadas) == 1, resultados
    assert [c for c, _ in erros_banco] == ["23505"]  # a recusa veio do Postgres, não do código
    assert INDICE_VEICULO_ABERTO in erros_banco[0][1]
    assert recusadas[0].mensagem == supabase_repo.MSG_VEICULO_OCUPADO


def test_concorrencia_pela_rota_so_uma_requisicao_aberta(api, app_real, fabrica) -> None:  # type: ignore[no-untyped-def]
    """Mesma disputa pela rota real: o pré-check pode antecipar a recusa, mas o resultado é
    sempre 1x 201 e 1x 409 `veiculo_ocupado`, e exatamente uma requisição aberta no banco."""
    a, b = fabrica.perfil("solicitante"), fabrica.perfil("solicitante")
    veiculo = fabrica.veiculo()
    barreira = threading.Barrier(2)
    respostas: list[tuple[int, dict[str, Any]]] = []
    trava = threading.Lock()

    def tentar(perfil: Perfil) -> None:
        barreira.wait(timeout=10)
        r = api(perfil, "POST", "/requisicoes", json=_corpo(veiculo), client=app_real.test_client())
        with trava:
            respostas.append((r.status_code, r.get_json()))

    ts = [threading.Thread(target=tentar, args=(p,)) for p in (a, b)]
    [t.start() for t in ts]
    [t.join(timeout=30) for t in ts]

    for status, corpo in respostas:
        if status == 201:
            fabrica.rastrear(corpo["id"])
    assert sorted(s for s, _ in respostas) == [201, 409], respostas
    erro409 = next(c for s, c in respostas if s == 409)["erro"]
    assert erro409["codigo"] == "veiculo_ocupado" and erro409["mensagem"]
    assert fabrica.repo.veiculo_ocupado(veiculo)
    abertas = [
        r
        for r in fabrica.repo.obter_requisicoes_por_ids(fabrica.requisicoes)
        if r["veiculo_id"] == veiculo and r["status"] in ("solicitada", "autorizada", "em_uso")
    ]
    assert len(abertas) == 1
