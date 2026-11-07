"""Infra da administração: tradução de erros do banco, retry de lock (migration 15), repositório
sobre PostgREST (mock), adaptador do Auth Admin, histórico com `por` e separação de transações."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from postgrest.exceptions import APIError

from app.config import Config
from app.errors import ContaExistente, ErroApi, ErroInterno, SistemaOcupado
from app.repositorios import supabase_repo
from app.repositorios.auth_admin import SupabaseAuthAdmin
from app.repositorios.supabase_repo import SupabaseRepositorio, _exec, traduzir_erro_banco
from app.services.admin_usuarios import ServicoUsuarios
from app.services.requisicoes import ServicoRequisicoes
from tests.test_requisicoes import acao, criar


def api_erro(codigo: str, message: str = "") -> APIError:
    return APIError({"message": message, "code": codigo, "details": "", "hint": ""})


# ------------------------------------------------------------------------------ tradução
@pytest.mark.parametrize(
    ("codigo", "texto", "status", "codigo_api"),
    [
        ("23505", 'unique constraint "ux_veiculos_placa"', 409, "placa_duplicada"),
        ("23505", 'unique constraint "ux_convites_email_aberto"', 409, "convite_aberto_existente"),
        ("23505", 'unique constraint "perfis_pkey"', 409, "perfil_existente"),
        ("23505", 'unique constraint "outra"', 409, "conflito"),
        ("23505", 'unique constraint "ux_requisicoes_veiculo_aberta"', 409, "veiculo_ocupado"),
        ("23514", 'check constraint "ck_convites_email_dominio"', 400, "requisicao_invalida"),
        (
            "23514",
            'check constraint "ck_configuracoes_abastecimento_exige_km"',
            409,
            "dependencia_recurso",
        ),
        ("AG005", "", 409, "convite_encerrado"),
        ("AG006", "", 410, "convite_expirado"),
        ("AG007", "", 400, "convite_invalido"),
        ("AG008", "", 403, "auto_alteracao_proibida"),
        ("AG009", "", 409, "ultimo_admin"),
        ("AG010", "", 409, "dependencia_recurso"),
        ("55P03", "", 503, "ocupado"),
        ("40P01", "", 503, "ocupado"),
    ],
)
def test_traducao(codigo, texto, status, codigo_api):  # type: ignore[no-untyped-def]
    e = traduzir_erro_banco(api_erro(codigo, texto))
    assert (e.status, e.codigo) == (status, codigo_api) and e.mensagem


def test_traducao_nao_ecoa_dados_do_banco():  # type: ignore[no-untyped-def]
    e = traduzir_erro_banco(
        APIError(
            {
                "message": 'violates unique constraint "ux_convites_email_aberto"',
                "code": "23505",
                "details": "Key (email)=(fulano@gmail.com) already exists.",
                "hint": "",
            }
        )
    )
    assert "fulano" not in e.mensagem


# ------------------------------------------------------------------------------ retry de lock
@pytest.fixture
def sem_espera(monkeypatch):  # type: ignore[no-untyped-def]
    esperas: list[float] = []
    monkeypatch.setattr(supabase_repo, "_dormir", esperas.append)
    return esperas


@pytest.mark.parametrize("codigo", ["55P03", "40P01"])
def test_escrita_admin_repete_em_lock_ou_deadlock(sem_espera, codigo):  # type: ignore[no-untyped-def]
    chamadas = []

    def fn() -> str:
        chamadas.append(1)
        if len(chamadas) < 3:
            raise api_erro(codigo)
        return "ok"

    assert _exec(fn, repetir_lock=True) == "ok"
    assert len(chamadas) == 3 and len(sem_espera) == 2


def test_lock_persistente_vira_503_claro(sem_espera):  # type: ignore[no-untyped-def]
    n = []

    def fn() -> None:
        n.append(1)
        raise api_erro("55P03")

    with pytest.raises(SistemaOcupado) as ei:
        _exec(fn, repetir_lock=True)
    assert len(n) == 3 and ei.value.status == 503 and "Tente novamente" in ei.value.mensagem


def test_leitura_e_outros_erros_nao_repetem(sem_espera):  # type: ignore[no-untyped-def]
    n = []

    def trava() -> None:
        n.append(1)
        raise api_erro("55P03")

    with pytest.raises(SistemaOcupado):
        _exec(trava)  # sem repetir_lock
    assert len(n) == 1
    n.clear()

    def outro() -> None:
        n.append(1)
        raise api_erro("23505", "ux_veiculos_placa")

    with pytest.raises(ErroApi):
        _exec(outro, repetir_lock=True)
    assert len(n) == 1 and not sem_espera


# ------------------------------------------------------------------------------ repo PostgREST
def repo_mock() -> tuple[SupabaseRepositorio, MagicMock]:
    r = SupabaseRepositorio.__new__(SupabaseRepositorio)
    r._c = MagicMock()
    return r, r._c


def test_escritas_admin_enviam_o_ator_e_nao_devolvem_colunas_internas():  # type: ignore[no-untyped-def]
    r, c = repo_mock()
    linha = {
        "id": "i",
        "nome": "N",
        "unidade": "PU",
        "role": "admin",
        "ativo": True,
        "criado_em": "t",
        "atualizado_em": "t",
        "alterado_por": None,
    }
    c.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [linha]
    out = r.atualizar_perfil("i", {"role": "admin"}, ator_id="ator")
    c.table.return_value.update.assert_called_once_with({"role": "admin", "alterado_por": "ator"})
    assert "alterado_por" not in out

    c.table.return_value.insert.return_value.execute.return_value.data = [
        {
            "id": "c",
            "email": "e",
            "role": "solicitante",
            "unidade": "PU",
            "criado_por": "a",
            "criado_em": "t",
            "expira_em": "t",
            "usado_em": None,
            "usado_por": None,
            "revogado_em": None,
            "revogado_por": None,
            "token_hash": "SEGREDO",
        }
    ]
    assert "token_hash" not in r.inserir_convite({"token_hash": "h"})

    c.table.return_value.insert.return_value.execute.return_value.data = [
        {
            "id": "v",
            "descricao": "d",
            "placa": None,
            "ativo": True,
            "criado_em": "t",
            "atualizado_em": "t",
            "alterado_por": None,
        }
    ]
    r.inserir_veiculo({"descricao": "d", "placa": None}, ator_id="ator")
    c.table.return_value.insert.assert_called_with(
        {"descricao": "d", "placa": None, "alterado_por": "ator"}
    )

    c.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
        {"quilometragem": True, "abastecimento_manutencao": False, "checklist": False, "mfa": False}
    ]
    cfg = r.atualizar_configuracoes({"quilometragem": True}, ator_id="ator")
    c.table.return_value.update.assert_called_with({"quilometragem": True, "alterado_por": "ator"})
    assert cfg.quilometragem is True


def test_consumir_convite_usa_rpc_com_hash_e_aceita_objeto_ou_lista():  # type: ignore[no-untyped-def]
    r, c = repo_mock()
    perfil = {"id": "u", "nome": "N", "unidade": "PF", "role": "autorizador", "ativo": True}
    for data in (perfil, [perfil]):
        c.rpc.return_value.execute.return_value.data = data
        p = r.consumir_convite("hash", "u", "N")
        assert (p.id, p.role, p.unidade) == ("u", "autorizador", "PF")
    c.rpc.assert_called_with(
        "consumir_convite", {"p_token_hash": "hash", "p_usuario_id": "u", "p_nome": "N"}
    )


# ------------------------------------------------------------------------------ Auth Admin
def auth_com(admin: Any) -> SupabaseAuthAdmin:
    a = SupabaseAuthAdmin.__new__(SupabaseAuthAdmin)
    a._admin = admin
    return a


def test_auth_convidar_envia_nome_e_redirect_e_nao_sobrescreve_nome():  # type: ignore[no-untyped-def]
    adm = MagicMock()
    adm.invite_user_by_email.return_value = SimpleNamespace(user=SimpleNamespace(id="uid"))
    a = auth_com(adm)
    assert a.convidar("a@gmail.com", nome="Nome", redirect_to="https://x/y?convite=t") == "uid"
    adm.invite_user_by_email.assert_called_with(
        "a@gmail.com", {"data": {"nome": "Nome"}, "redirect_to": "https://x/y?convite=t"}
    )
    a.convidar("a@gmail.com", nome=None, redirect_to=None)
    adm.invite_user_by_email.assert_called_with("a@gmail.com", {})


def test_auth_traduz_conta_existente_e_falha_generica(caplog):  # type: ignore[no-untyped-def]
    adm = MagicMock()
    e = Exception("A user with this email address has already been registered")
    e.code = "email_exists"  # type: ignore[attr-defined]
    adm.invite_user_by_email.side_effect = e
    with pytest.raises(ContaExistente):
        auth_com(adm).convidar("a@gmail.com", nome="N", redirect_to=None)
    adm.invite_user_by_email.side_effect = RuntimeError("timeout contatando a@gmail.com")
    with pytest.raises(ErroInterno):
        auth_com(adm).convidar("a@gmail.com", nome="N", redirect_to=None)
    assert "a@gmail.com" not in caplog.text  # só o tipo da exceção é logado


def test_auth_bloquear_desbloquear_e_emails_paginados():  # type: ignore[no-untyped-def]
    adm = MagicMock()
    a = auth_com(adm)
    a.bloquear("u")
    adm.update_user_by_id.assert_called_with("u", {"ban_duration": "876000h"})
    a.desbloquear("u")
    adm.update_user_by_id.assert_called_with("u", {"ban_duration": "none"})

    adm.get_user_by_id.side_effect = lambda i: SimpleNamespace(
        user=SimpleNamespace(id=i, email=f"{i}@gmail.com")
    )
    assert a.emails_por_id({"alvo"}) == {"alvo": "alvo@gmail.com"}
    adm.list_users.assert_not_called()  # nunca varre as contas para montar a listagem
    adm.get_user_by_id.side_effect = RuntimeError("x")
    with pytest.raises(ErroInterno):
        a.emails_por_id({"alvo"})


def test_auth_emails_por_id_limita_lote_e_ignora_404(caplog):  # type: ignore[no-untyped-def]
    adm = MagicMock()
    a = auth_com(adm)
    nf = Exception("nf")
    nf.status = 404  # type: ignore[attr-defined]

    def obter(i):  # type: ignore[no-untyped-def]
        if i == "id000":
            raise nf
        return SimpleNamespace(user=SimpleNamespace(id=i, email=f"{i}@gmail.com"))

    adm.get_user_by_id.side_effect = obter
    ids = {f"id{n:03d}" for n in range(150)}
    r = a.emails_por_id(ids)
    assert adm.get_user_by_id.call_count == 100 and len(r) == 99 and "id000" not in r
    assert "emails_por_id_truncado" in caplog.text and "@gmail.com" not in caplog.text


def test_auth_indice_de_emails_trunca_e_nao_loga_pii(caplog):  # type: ignore[no-untyped-def]
    adm = MagicMock()
    a = auth_com(adm)
    pag = [SimpleNamespace(id=f"u{i}", email=f"U{i}@gmail.com") for i in range(200)]
    adm.list_users.return_value = pag  # sempre cheia: bate no teto de páginas
    assert a.id_por_email("U7@gmail.com") == "u7"
    assert a.id_por_email("nao@gmail.com") is None
    indice = a.indice_emails()
    assert indice.truncado and indice.emails["u7"] == "u7@gmail.com"  # e-mail em minúsculas
    assert "varredura_auth_truncada" in caplog.text and "@gmail.com" not in caplog.text
    adm.list_users.return_value = pag[:10]  # página curta: varredura completa
    assert not a.indice_emails().truncado
    adm.list_users.side_effect = RuntimeError("a@gmail.com")
    with pytest.raises(ErroInterno):
        a.indice_emails()


URL_RECOVER = "https://p.supabase.co/auth/v1/recover"


def auth_com_http(handler: Any) -> SupabaseAuthAdmin:
    a = SupabaseAuthAdmin.__new__(SupabaseAuthAdmin)
    a._url_recover = URL_RECOVER
    a._chave = "CHAVE-DE-TESTE"
    a._http = httpx.Client(transport=httpx.MockTransport(handler))
    return a


def test_reenviar_acesso_usa_recover_implicito_sem_pkce():  # type: ignore[no-untyped-def]
    vistos: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        vistos.append(req)
        return httpx.Response(200, json={})

    redirect = "https://app.exemplo.gov.br/convite?convite=tok&x=1"
    auth_com_http(handler).reenviar_acesso("a@gmail.com", redirect_to=redirect)
    (req,) = vistos
    url = urlsplit(str(req.url))
    assert req.method == "POST" and f"{url.scheme}://{url.netloc}{url.path}" == URL_RECOVER
    assert parse_qs(url.query) == {"redirect_to": [redirect]}  # só o redirect, codificado
    corpo = json.loads(req.content)
    assert corpo == {"email": "a@gmail.com"}  # sem code_challenge(_method): fluxo implícito
    assert "code_challenge" not in str(req.url) and "code_challenge" not in req.content.decode()
    assert req.headers["apikey"] == "CHAVE-DE-TESTE"
    assert req.headers["authorization"] == "Bearer CHAVE-DE-TESTE"


def test_reenviar_acesso_erro_http_ou_rede_vira_503_sem_pii(caplog):  # type: ignore[no-untyped-def]
    a = auth_com_http(lambda req: httpx.Response(429, json={"msg": "a@gmail.com"}))
    with pytest.raises(ErroInterno):
        a.reenviar_acesso("a@gmail.com", redirect_to="https://x/y?convite=SEGREDO")

    def cai(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("falha com a@gmail.com SEGREDO", request=req)

    with pytest.raises(ErroInterno):
        auth_com_http(cai).reenviar_acesso("a@gmail.com", redirect_to="https://x/y?convite=SEGREDO")
    assert "a@gmail.com" not in caplog.text and "SEGREDO" not in caplog.text


@pytest.mark.parametrize("atributo", ["code", "error_code"])
@pytest.mark.parametrize("codigo", ["email_exists", "user_already_exists"])
def test_ja_existe_confia_no_codigo_estruturado(atributo, codigo):  # type: ignore[no-untyped-def]
    adm = MagicMock()
    e = Exception("mensagem qualquer")
    setattr(e, atributo, codigo)
    adm.invite_user_by_email.side_effect = e
    with pytest.raises(ContaExistente):
        auth_com(adm).convidar("a@gmail.com", nome=None, redirect_to=None)


@pytest.mark.parametrize("extra", [{}, {"code": "validation_failed"}, {"error_code": "x"}])
def test_ja_existe_ignora_texto_com_already(extra):  # type: ignore[no-untyped-def]
    adm = MagicMock()
    e = Exception("User already registered / already exists")
    for k, v in extra.items():
        setattr(e, k, v)
    adm.invite_user_by_email.side_effect = e
    with pytest.raises(ErroInterno):
        auth_com(adm).convidar("a@gmail.com", nome=None, redirect_to=None)


def test_auth_obter_conta_le_nome_do_metadado():  # type: ignore[no-untyped-def]
    adm = MagicMock()
    adm.get_user_by_id.return_value = SimpleNamespace(
        user=SimpleNamespace(id="u", email="a@gmail.com", user_metadata={"nome": " Fulano "})
    )
    c = auth_com(adm).obter_conta("u")
    assert c is not None and c.nome == " Fulano " and c.email == "a@gmail.com"
    nf = Exception("nf")
    nf.status = 404  # type: ignore[attr-defined]
    adm.get_user_by_id.side_effect = nf
    assert auth_com(adm).obter_conta("u") is None


# ------------------------------------------------------------------------------ config
def test_url_de_aceite_exige_https_em_producao():  # type: ignore[no-untyped-def]
    base = {
        "supabase_url": "https://p.supabase.co",
        "supabase_service_role_key": "k",
        "jwks_url": "https://p.supabase.co/auth/v1/.well-known/jwks.json",
        "jwt_issuer": "https://p.supabase.co/auth/v1",
    }
    Config(**base, url_aceite_convite="https://app.exemplo.gov.br/aceitar").validar_producao()
    Config(**base).validar_producao()  # opcional: sem ela, só o convite fica desativado
    with pytest.raises(RuntimeError, match="URL_ACEITE_CONVITE"):
        Config(**base, url_aceite_convite="http://app.exemplo.gov.br/aceitar").validar_producao()


# ------------------------------------------------------------------------------ histórico com `por`
def test_historico_traz_autor_de_cada_evento(client, auth, repo):  # type: ignore[no-untyped-def]
    sol, aut = repo.novo_perfil("solicitante"), repo.novo_perfil("autorizador")
    outro = repo.novo_perfil("autorizador")
    v = repo.novo_veiculo()
    rid = criar(client, auth, sol, v).get_json()["id"]
    acao(client, auth, aut, rid, "autorizar")
    acao(client, auth, outro, rid, "saida")
    j = acao(client, auth, aut, rid, "chegada").get_json()
    assert [(h["rotulo"], h["por"]) for h in j["historico"]] == [
        ("Solicitada", sol.nome),
        ("Autorizada", aut.nome),
        ("Saída registrada", outro.nome),
        ("Chegada registrada", aut.nome),
    ]
    assert all(set(h) == {"rotulo", "quando", "por"} for h in j["historico"])
    # campos antigos mantidos por compatibilidade
    assert j["autorizado_por_nome"] == aut.nome and j["saida_registrada_por_nome"] == outro.nome


def test_historico_cancelada_mostra_quem_cancelou(client, auth, repo):  # type: ignore[no-untyped-def]
    sol = repo.novo_perfil("solicitante")
    rid = criar(client, auth, sol, repo.novo_veiculo()).get_json()["id"]
    j = acao(client, auth, sol, rid, "cancelar").get_json()
    assert [(h["rotulo"], h["por"]) for h in j["historico"]] == [
        ("Solicitada", sol.nome),
        ("Cancelada", sol.nome),
    ]


# ------------------------------------------------------------------- transações separadas
class Espiao:
    """Registra os métodos chamados no repositório (cada um = uma transação curta)."""

    def __init__(self, repo: Any) -> None:
        self._repo, self.chamadas = repo, []

    def __getattr__(self, nome: str) -> Any:
        alvo = getattr(self._repo, nome)

        def registra(*a: Any, **k: Any) -> Any:
            self.chamadas.append(nome)
            return alvo(*a, **k)

        return registra


ESCRITAS_REQ = {"inserir_requisicao", "transicionar"}
ESCRITAS_ADM = {
    "atualizar_perfil", "inserir_convite", "revogar_convite", "consumir_convite",
    "inserir_veiculo", "atualizar_veiculo", "atualizar_configuracoes", "registrar_auditoria",
}  # fmt: skip


def test_escrita_administrativa_nunca_mistura_com_escrita_de_requisicao(repo, auth_admin, adm):  # type: ignore[no-untyped-def]
    alvo = repo.novo_perfil("solicitante")
    esp = Espiao(repo)
    svc = ServicoUsuarios(esp, auth_admin)  # type: ignore[arg-type]
    svc.alterar(adm, alvo.id, {"nome": "Outro Nome", "role": "autorizador"})
    svc.desativar(adm, alvo.id)
    assert ESCRITAS_ADM & set(esp.chamadas) and not ESCRITAS_REQ & set(esp.chamadas)

    esp2 = Espiao(repo)
    sol = repo.novo_perfil("solicitante")
    ServicoRequisicoes(esp2).criar(  # type: ignore[arg-type]
        sol,
        {
            "nome_solicitante": "F",
            "unidade": "PU",
            "hora_saida": "2026-11-10T08:30:00-03:00",
            "trajeto": "A -> B",
            "veiculo_id": repo.novo_veiculo(),
        },
    )
    assert ESCRITAS_REQ & set(esp2.chamadas) and not ESCRITAS_ADM & set(esp2.chamadas)


# ------------------------------------------------------------------ lock em escritas de requisição
MSG_NEUTRA = "O sistema está ocupado no momento. Tente novamente em instantes."


def repo_real_com_execute(efeitos: list[Any]) -> SupabaseRepositorio:
    r, c = repo_mock()
    c.table.return_value.insert.return_value.execute.side_effect = efeitos
    c.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.side_effect = (
        efeitos
    )
    return r


def test_mensagem_neutra_fora_da_rota_admin_e_admin_mantida():  # type: ignore[no-untyped-def]
    assert traduzir_erro_banco(api_erro("55P03")).mensagem == MSG_NEUTRA
    assert "administrativa" in traduzir_erro_banco(api_erro("40P01"), admin=True).mensagem


@pytest.mark.parametrize("codigo", ["55P03", "40P01"])
def test_criar_requisicao_lock_persistente_vira_503_neutro(  # type: ignore[no-untyped-def]
    client, auth, repo, sem_espera, codigo
):
    cen = repo.novo_perfil("solicitante"), repo.novo_veiculo()
    real = repo_real_com_execute([api_erro(codigo, "Key (nome)=(Fulano)")] * 3)
    repo.inserir_requisicao = real.inserir_requisicao  # type: ignore[method-assign]
    r = criar(client, auth, cen[0], cen[1])
    assert r.status_code == 503
    erro = r.get_json()["erro"]
    assert erro == {"codigo": "ocupado", "mensagem": MSG_NEUTRA}
    assert sem_espera == [0.2, 0.5]
    assert real._c.table.return_value.insert.return_value.execute.call_count == 3


def test_criar_requisicao_recupera_na_segunda_tentativa(  # type: ignore[no-untyped-def]
    client, auth, repo, sem_espera
):
    sol, veic = repo.novo_perfil("solicitante"), repo.novo_veiculo()
    linha = {"id": "r1", "usuario_id": sol.id, "status": "solicitada", "veiculo_id": veic}
    real = repo_real_com_execute([api_erro("55P03"), SimpleNamespace(data=[linha])])
    repo.inserir_requisicao = real.inserir_requisicao  # type: ignore[method-assign]
    r = criar(client, auth, sol, veic)
    assert r.status_code == 201 and sem_espera == [0.2]


def test_transicionar_repete_em_lock(sem_espera):  # type: ignore[no-untyped-def]
    real = repo_real_com_execute([api_erro("55P03"), SimpleNamespace(data=[{"id": "r1"}])])
    assert real.transicionar("r1", "solicitada", {"status": "autorizada"}) == {"id": "r1"}
    assert sem_espera == [0.2]


def test_orcamento_de_tempo_cabe_no_gunicorn_e_no_timeout_http():  # type: ignore[no-untyped-def]
    total = 3 * 5 + sum(supabase_repo.ESPERAS_LOCK)
    assert total < 30 and supabase_repo.TIMEOUT_POSTGREST >= 16
    assert supabase_repo.TIMEOUT_POSTGREST < 30
