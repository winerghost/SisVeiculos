"""Relatórios: permissões, escopo do solicitante (IDOR), filtros, limites, arquivos e auditoria."""

from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import openpyxl
import pytest

from app import create_app
from app.auth.jwt import VerificadorJWT
from app.config import Config
from app.extensions import limiter
from app.repositorios.supabase_repo import SupabaseRepositorio
from app.services.dashboards import FUSO
from tests.conftest import _CHAVE, AUDIENCE, ISSUER
from tests.fakes_admin import erro_db

BASE = "/api/relatorios"
ROTAS_AUTENTICADAS = [
    ("get", BASE),
    ("get", f"{BASE}/destinatarios"),
    ("get", f"{BASE}/envios/{uuid.uuid4()}"),
    ("get", f"{BASE}/movimentacao/previa"),
    ("get", f"{BASE}/movimentacao/arquivo"),
    ("post", f"{BASE}/movimentacao/email"),
]


@pytest.fixture
def mk(repo, auth_admin, storage, avatares):  # type: ignore[no-untyped-def]
    def _mk(**sobrescrever: Any):  # type: ignore[no-untyped-def]
        base: dict[str, Any] = {
            "jwt_issuer": ISSUER,
            "jwt_audience": AUDIENCE,
            "testing": True,
            "email_enabled": True,
            "email_host": "smtp.teste.invalido",
            "email_user": "usuario-smtp",
            "email_password": "SENHA-SECRETA-123",
            "email_from": "sistema@teste.gov.br",
            "limite_relatorios": "1000 per minute",
            "limite_relatorios_arquivo": "1000 per minute",
            "limite_relatorios_email": "1000 per minute",
        }
        base.update(sobrescrever)
        verificador = VerificadorJWT(
            issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
        )
        aplicacao = create_app(
            Config(**base),
            repo=repo,
            verificador=verificador,
            auth_admin=auth_admin,
            armazenamento=storage,
            armazenamento_avatares=avatares,
        )
        limiter.reset()
        return aplicacao

    return _mk


@pytest.fixture
def app(mk):  # type: ignore[no-untyped-def]
    return mk()


def nova_req(repo, sol, **extra):  # type: ignore[no-untyped-def]
    dados = {
        "usuario_id": sol.id,
        "nome_solicitante": sol.nome,
        "unidade": sol.unidade,
        "hora_saida": "2026-11-08T12:00:00+00:00",
        "trajeto": "Sede - Fórum",
        "observacao": None,
        "veiculo_id": repo.novo_veiculo(),
        **extra,
    }
    return repo.inserir_requisicao(dados)


def get(client, auth, perfil, url, **params):  # type: ignore[no-untyped-def]
    return client.get(url, headers=auth(perfil.id), query_string=params)


# ------------------------------------------------------------------------- autenticação
@pytest.mark.parametrize(("metodo", "url"), ROTAS_AUTENTICADAS)
def test_sem_token_401(client, metodo, url):  # type: ignore[no-untyped-def]
    r = getattr(client, metodo)(url)
    assert r.status_code == 401
    assert r.get_json()["erro"]["codigo"] == "nao_autenticado"


# ----------------------------------------------------------------------------- catálogo
def tipos(j: dict[str, Any]) -> set[str]:
    return {r["tipo"] for r in j["relatorios"]}


def test_catalogo_por_perfil(client, auth, c, repo):  # type: ignore[no-untyped-def]
    sol = get(client, auth, c.sol, BASE).get_json()
    aut = get(client, auth, c.aut, BASE).get_json()
    adm = get(client, auth, c.adm, BASE).get_json()
    assert tipos(sol) == {"movimentacao", "pendencias"}
    assert tipos(aut) == {"movimentacao", "pendencias", "resumo", "tempos"}
    assert tipos(adm) == tipos(aut) | {"auditoria"}
    repo.config = repo.config.__class__(quilometragem=True, checklist=True)
    assert {"opcionais_km", "opcionais_avarias"} <= tipos(get(client, auth, c.aut, BASE).get_json())


def test_catalogo_contrato_e_filtros_do_solicitante(client, auth, c):  # type: ignore[no-untyped-def]
    j = get(client, auth, c.sol, BASE).get_json()
    assert j["limite_linhas"] == 5000 and j["fuso"] == "America/Sao_Paulo"
    assert j["canais"] == {"download": True, "email": False, "whatsapp": False}
    mov = next(r for r in j["relatorios"] if r["tipo"] == "movimentacao")
    nomes = [f["nome"] for f in mov["filtros"]]
    assert nomes == ["inicio", "fim", "campo_data", "unidade", "veiculo"]  # sem incluir_*
    campo = next(f for f in mov["filtros"] if f["nome"] == "campo_data")
    assert (
        campo["tipo"] == "enum" and campo["padrao"] == "criacao" and campo["obrigatorio"] is False
    )
    assert {o["valor"] for o in campo["opcoes"]} == {"criacao", "saida_prevista", "saida_real"}
    inicio = next(f for f in mov["filtros"] if f["nome"] == "inicio")
    assert inicio["tipo"] == "data" and inicio["opcoes"] is None
    staff = get(client, auth, c.aut, BASE).get_json()
    mov_staff = next(r for r in staff["relatorios"] if r["tipo"] == "movimentacao")
    assert {"incluir_nomes", "incluir_trajeto"} <= {f["nome"] for f in mov_staff["filtros"]}
    nomes_flag = next(f for f in mov_staff["filtros"] if f["nome"] == "incluir_nomes")
    assert nomes_flag["tipo"] == "bool" and nomes_flag["padrao"] is False


def test_canais_refletem_config_e_email_enabled(mk, repo, auth, c):  # type: ignore[no-untyped-def]
    repo.config = repo.config.__class__(relatorio_email=True, relatorio_whatsapp=True)
    cli = mk().test_client()
    assert get(cli, auth, c.sol, BASE).get_json()["canais"] == {
        "download": True,
        "email": True,
        "whatsapp": True,
    }
    cli = mk(email_enabled=False).test_client()  # recurso ligado, mas SMTP desligado no ambiente
    assert get(cli, auth, c.sol, BASE).get_json()["canais"]["email"] is False
    repo.config = repo.config.__class__(relatorio_email=False)
    cli = mk().test_client()
    assert get(cli, auth, c.sol, BASE).get_json()["canais"]["email"] is False


def test_exportacao_paineis_filtrada_por_role(client, auth, c, repo):  # type: ignore[no-untyped-def]
    def paineis(p):  # type: ignore[no-untyped-def]
        return get(client, auth, p, BASE).get_json()["exportacao_paineis"]

    assert paineis(c.sol) == {"operacao": {"tipo": "pendencias", "parametros": {}}}
    aut = paineis(c.aut)
    assert set(aut) == {"operacao", "volume", "tempos", "unidades", "veiculos"}
    assert aut["volume"] == {"tipo": "resumo", "parametros": {"agrupar": "dia"}}
    assert aut["tempos"] == {"tipo": "tempos", "parametros": {"agrupar": "unidade"}}
    assert aut["unidades"]["parametros"] == {"agrupar": "unidade"}
    assert aut["veiculos"]["parametros"] == {"agrupar": "veiculo"}
    adm = paineis(c.adm)
    assert adm["governanca"] == {"tipo": "auditoria", "parametros": {}}
    assert "opcionais" not in adm
    repo.config = repo.config.__class__(quilometragem=True)
    assert paineis(c.aut)["opcionais"]["tipo"] == "opcionais_km"
    repo.config = repo.config.__class__(checklist=True)
    assert paineis(c.aut)["opcionais"]["tipo"] == "opcionais_avarias"


# ---------------------------------------------------------------------------- permissões
@pytest.mark.parametrize("tipo", ["resumo", "tempos", "auditoria", "opcionais_km"])
@pytest.mark.parametrize("rota", ["previa", "arquivo"])
def test_solicitante_so_movimentacao_e_pendencias(client, auth, c, repo, tipo, rota):  # type: ignore[no-untyped-def]
    repo.config = repo.config.__class__(quilometragem=True)
    r = get(client, auth, c.sol, f"{BASE}/{tipo}/{rota}")
    assert r.status_code == 403 and r.get_json()["erro"]["codigo"] == "sem_permissao"
    assert repo.chamadas_rel == [] and repo.eventos_de("relatorio_gerado") == []


def test_autorizador_nao_ve_auditoria_admin_ve(client, auth, c):  # type: ignore[no-untyped-def]
    assert get(client, auth, c.aut, f"{BASE}/auditoria/previa").status_code == 403
    assert get(client, auth, c.aut, f"{BASE}/auditoria/arquivo").status_code == 403
    r = get(client, auth, c.adm, f"{BASE}/auditoria/previa")
    assert r.status_code == 200
    j = r.get_json()
    assert j["linhas"][0]["acao"] == "Perfil alterado" and j["linhas"][0]["tabela"] == "Perfis"
    assert [x["chave"] for x in j["colunas"]][:3] == ["ocorrido_em", "tabela", "acao"]


def test_tipo_desconhecido_404_e_recurso_desligado(client, auth, c):  # type: ignore[no-untyped-def]
    assert get(client, auth, c.adm, f"{BASE}/inexistente/previa").status_code == 404
    for tipo in ("opcionais_km", "opcionais_avarias"):
        r = get(client, auth, c.aut, f"{BASE}/{tipo}/previa")
        assert r.status_code == 404 and r.get_json()["erro"]["codigo"] == "recurso_desligado"


def test_opcionais_km_ligado(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = repo.config.__class__(quilometragem=True)
    j = get(client, auth, c.aut, f"{BASE}/opcionais_km/previa").get_json()
    assert j["linhas"][0]["km_rodado"] == "10" and "Km rodado" in [
        x["rotulo"] for x in j["colunas"]
    ]


def test_avarias_nunca_pede_nem_exibe_descricao(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = repo.config.__class__(checklist=True)
    repo.rel_extra_colunas = {"descricao_avarias": "Maria bateu o carro"}  # banco "vazando"
    j = get(client, auth, c.aut, f"{BASE}/opcionais_avarias/previa").get_json()
    assert "descricao_avarias" not in [x["chave"] for x in j["colunas"]]
    assert "Maria" not in str(j)
    arq = get(client, auth, c.aut, f"{BASE}/opcionais_avarias/arquivo")
    assert b"Maria" not in arq.data


def test_repositorio_nunca_envia_incluir_descricao_true():  # type: ignore[no-untyped-def]
    r = SupabaseRepositorio.__new__(SupabaseRepositorio)
    r._c = MagicMock()
    r._c.rpc.return_value.execute.return_value.data = {"habilitado": True, "linhas": []}
    r.rel_opcionais_avarias(
        inicio="2026-11-01T03:00:00+00:00",
        fim="2026-11-02T03:00:00+00:00",
        unidade=None,
        veiculo=None,
        usuario=None,
        limite=5001,
    )
    nome, params = r._c.rpc.call_args.args
    assert nome == "rel_opcionais_avarias" and params["p_incluir_descricao"] is False


# ------------------------------------------------------------------- escopo / anti-IDOR
def test_solicitante_ve_so_o_proprio_e_ignora_usuario_do_query(client, auth, c, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol)
    nova_req(repo, c.outro, nome_solicitante="Pessoa Alheia")
    for url in (f"{BASE}/movimentacao/previa", f"{BASE}/pendencias/previa"):
        j = get(client, auth, c.sol, url, usuario=c.outro.id, usuario_id=c.outro.id).get_json()
        assert j["total_linhas"] == 1
        assert "usuario" not in j["filtros"] and "usuario_id" not in j["filtros"]
    for nome in ("movimentacao", "pendencias"):
        assert {p["usuario"] for p in repo.chamadas_de(nome)} == {c.sol.id}


def test_solicitante_nao_liga_pii_nem_pelo_arquivo(client, auth, c, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol)
    j = get(
        client,
        auth,
        c.sol,
        f"{BASE}/movimentacao/previa",
        incluir_nomes="true",
        incluir_trajeto="1",
    ).get_json()
    assert j["filtros"].get("incluir_nomes") is None and "trajeto" not in str(j["colunas"])
    p = repo.chamadas_de("movimentacao")[-1]
    assert p["incluir_nomes"] is False and p["incluir_trajeto"] is False
    arq = get(client, auth, c.sol, f"{BASE}/pendencias/arquivo", incluir_nomes="true")
    assert arq.status_code == 200 and c.sol.nome.encode() not in arq.data
    assert repo.chamadas_de("pendencias")[-1]["incluir_nomes"] is False


def test_staff_enxerga_tudo_e_liga_pii_por_flag(client, auth, c, repo):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol)
    nova_req(repo, c.outro)
    j = get(client, auth, c.aut, f"{BASE}/movimentacao/previa", usuario=c.sol.id).get_json()
    assert j["total_linhas"] == 2 and repo.chamadas_de("movimentacao")[-1]["usuario"] is None
    assert "solicitante" not in [x["chave"] for x in j["colunas"]]  # padrão: sem nomes
    j = get(
        client,
        auth,
        c.aut,
        f"{BASE}/movimentacao/previa",
        incluir_nomes="true",
        incluir_trajeto="true",
    ).get_json()
    chaves = [x["chave"] for x in j["colunas"]]
    assert "solicitante" in chaves and "trajeto" in chaves
    assert {ln["solicitante"] for ln in j["linhas"]} == {c.sol.nome, c.outro.nome}


# --------------------------------------------------------------------------- filtros
@pytest.mark.parametrize(
    "params",
    [
        {"inicio": "01/11/2026"},
        {"inicio": "2026-13-01"},
        {"inicio": "2026-11-05", "fim": "2026-11-01"},
        {"inicio": "2025-01-01", "fim": "2026-11-01"},  # > 366 dias
        {"inicio": "2018-12-31", "fim": "2019-01-05"},  # antes de ANO_MIN
        {"unidade": "XX"},
        {"veiculo": "nao-e-uuid"},
        {"campo_data": "inventado"},
        {"incluir_nomes": "talvez"},
    ],
)
def test_filtros_invalidos_400(client, auth, c, params):  # type: ignore[no-untyped-def]
    r = get(client, auth, c.adm, f"{BASE}/movimentacao/previa", **params)
    assert r.status_code == 400 and r.get_json()["erro"]["codigo"] == "requisicao_invalida"


def test_agrupar_invalido_por_relatorio(client, auth, c):  # type: ignore[no-untyped-def]
    assert get(client, auth, c.aut, f"{BASE}/tempos/previa", agrupar="dia").status_code == 400
    assert get(client, auth, c.aut, f"{BASE}/resumo/previa", agrupar="dia").status_code == 200
    assert (
        get(client, auth, c.adm, f"{BASE}/auditoria/previa", acao="drop table").status_code == 400
    )


def test_janela_padrao_30_dias_e_fim_inclusivo_vira_exclusivo(client, auth, c, repo):  # type: ignore[no-untyped-def]
    hoje = datetime.now(FUSO).date()
    j = get(client, auth, c.aut, f"{BASE}/movimentacao/previa").get_json()
    assert j["filtros"]["fim"] == hoje.isoformat()
    assert (hoje - datetime.fromisoformat(j["filtros"]["inicio"]).date()).days == 29
    get(client, auth, c.aut, f"{BASE}/movimentacao/previa", inicio="2026-11-01", fim="2026-11-02")
    p = repo.chamadas_de("movimentacao")[-1]
    assert p["inicio"] == "2026-11-01T03:00:00+00:00"  # 00:00 em -03:00
    assert p["fim"] == "2026-11-03T03:00:00+00:00"  # fim inclusivo => 00:00 do dia seguinte
    # 366 dias exatos passam; 367 não.
    ok = get(
        client, auth, c.aut, f"{BASE}/movimentacao/previa", inicio="2025-11-01", fim="2026-10-31"
    )
    assert ok.status_code == 200


# ---------------------------------------------------------------- prévia e limites
def test_previa_limitada_a_50_pede_51_ao_banco(client, auth, c, repo):  # type: ignore[no-untyped-def]
    for _ in range(60):
        nova_req(repo, c.sol)
    j = get(client, auth, c.aut, f"{BASE}/movimentacao/previa").get_json()
    # total_linhas = linhas devolvidas; truncado = há mais linhas que a prévia
    assert len(j["linhas"]) == 50 and j["total_linhas"] == 50 and j["truncado"] is True
    assert repo.chamadas_de("movimentacao")[-1]["limite"] == 51
    assert "limite_arquivo_excedido" not in j


def test_previa_curta_nao_trunca(client, auth, c, repo):  # type: ignore[no-untyped-def]
    for _ in range(3):
        nova_req(repo, c.sol)
    j = get(client, auth, c.aut, f"{BASE}/movimentacao/previa").get_json()
    assert j["total_linhas"] == 3 and j["truncado"] is False and j["tipo"] == "movimentacao"


def test_excesso_de_linhas_arquivo_422(mk, auth, c, repo):  # type: ignore[no-untyped-def]
    for _ in range(5):
        nova_req(repo, c.sol)
    cli = mk(relatorio_max_linhas=3).test_client()
    for formato in ("csv", "xlsx"):
        r = get(cli, auth, c.aut, f"{BASE}/movimentacao/arquivo", formato=formato)
        assert r.status_code == 422
        erro = r.get_json()["erro"]
        assert erro["codigo"] == "relatorio_muito_grande"
        assert "Estreite o filtro" in erro["mensagem"]
    assert repo.chamadas_de("movimentacao")[-1]["limite"] == 4  # limite + 1
    assert repo.eventos_de("relatorio_gerado") == []


# ---------------------------------------------------- auditoria da prévia com PII (M3)
@pytest.mark.parametrize("params", [{"incluir_nomes": "true"}, {"incluir_trajeto": "true"}])
def test_previa_com_pii_audita(client, auth, c, repo, params):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol)
    r = get(client, auth, c.aut, f"{BASE}/movimentacao/previa", **params)
    assert r.status_code == 200
    (ev,) = repo.eventos_de("relatorio_gerado")
    d = ev["detalhes"]
    assert ev["tabela"] == "relatorios" and ev["ator_id"] == c.aut.id
    assert d["via"] == "previa" and d["tipo"] == "movimentacao" and d["total_linhas"] == 1
    assert set(d) <= {"tipo", "filtros", "formato", "via", "total_linhas"}
    assert c.sol.nome not in str(d) and "@" not in str(d)


def test_previa_de_auditoria_audita_e_sem_pii_nao(client, auth, c, repo):  # type: ignore[no-untyped-def]
    get(client, auth, c.adm, f"{BASE}/auditoria/previa")
    assert [e["detalhes"]["tipo"] for e in repo.eventos_de("relatorio_gerado")] == ["auditoria"]
    get(client, auth, c.aut, f"{BASE}/movimentacao/previa")
    get(client, auth, c.sol, f"{BASE}/pendencias/previa")
    assert len(repo.eventos_de("relatorio_gerado")) == 1


def test_previa_sem_auditoria_nao_devolve_dados(client, auth, c, repo):  # type: ignore[no-untyped-def]
    from app.errors import ErroInterno

    nova_req(repo, c.sol)
    repo.falha_auditoria = ErroInterno("falha")
    r = get(client, auth, c.aut, f"{BASE}/movimentacao/previa", incluir_nomes="true")
    assert r.status_code == 503 and c.sol.nome not in r.get_data(as_text=True)
    assert get(client, auth, c.aut, f"{BASE}/movimentacao/previa").status_code == 200


# ----------------------------------------------- valor fora do formato é neutralizado (B3)
def _ler_csv(dados: bytes) -> list[list[str]]:
    texto = dados.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(texto, newline=""), delimiter=";"))


def test_valor_malicioso_em_coluna_nao_texto_e_neutralizado():  # type: ignore[no-untyped-def]
    from app.services.exportacao import Coluna, gerar_csv, gerar_xlsx

    cols = [
        Coluna("d", "Data", "datahora"),
        Coluna("n", "Número", "inteiro"),
        Coluna("x", "Duração", "duracao"),
        Coluna("g", "Dia", "dia"),
        Coluna("m", "Mês", "mes"),
        Coluna("o", "Dec", "decimal"),
    ]
    mal = "=cmd|' /C calc'!A0"
    linha = {k: mal for k in "dnxgmo"}
    assert _ler_csv(gerar_csv(cols, [linha]))[1] == ["'" + mal] * 6
    ws = openpyxl.load_workbook(io.BytesIO(gerar_xlsx(cols, [linha], "T"))).active
    for celula in ws[2]:
        assert celula.value == "'" + mal and celula.data_type == "s"
    # duração negativa gerada pelo sistema continua legítima
    assert _ler_csv(gerar_csv(cols[2:3], [{"x": -300}]))[1] == ["-00:05:00"]


# ------------------------------------------------------------------------------- CSV
def test_csv_bom_separador_crlf_nome_e_cabecalhos(client, auth, c, repo):  # type: ignore[no-untyped-def]
    sol = repo.novo_perfil("solicitante")
    repo.perfis[sol.id] = sol.__class__(sol.id, "João da Silva Ç", "PU", "solicitante", True)
    nova_req(repo, sol, nome_solicitante="João da Silva Ç")
    r = get(
        client, auth, c.aut, f"{BASE}/movimentacao/arquivo", formato="csv", incluir_nomes="true"
    )
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "no-store"
    assert r.mimetype == "text/csv" and "utf-8" in r.headers["Content-Type"]
    nome = re.fullmatch(
        r'attachment; filename="(relatorio-movimentacao-\d{8}\.csv)"',
        r.headers["Content-Disposition"],
    )
    assert nome and "Silva" not in nome[1]
    assert r.data.startswith(b"\xef\xbb\xbf")
    texto = r.data.decode("utf-8-sig")
    linhas = texto.split("\r\n")
    assert linhas[-1] == "" and len(linhas) == 3  # cabeçalho + 1 linha + final
    assert "\n" not in texto.replace("\r\n", "")
    cab = linhas[0].split(";")
    assert cab[:3] == ["Código", "Criada em", "Unidade"] and "Solicitante" in cab
    assert "Duração do uso (hh:mm:ss)" in cab
    dados = dict(zip(cab, linhas[1].split(";"), strict=True))
    assert dados["Solicitante"] == "João da Silva Ç" and dados["Status"] == "Solicitada"
    assert dados["Duração do uso (hh:mm:ss)"] == "01:02:05"
    assert re.fullmatch(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", dados["Saída prevista"])
    assert dados["Saída prevista"] == "08/11/2026 09:00"  # 12:00 UTC = 09:00 em -03:00


def test_csv_duracao_negativa_nao_e_tratada_como_formula(client, auth, c):  # type: ignore[no-untyped-def]
    texto = get(client, auth, c.aut, f"{BASE}/tempos/arquivo").data.decode("utf-8-sig")
    assert "-00:05:00" in texto
    assert "'-00:05:00" not in texto
    assert texto.split("\r\n")[0].split(";")[0] == "Unidade"


PERIGOSOS = ["=1+1", "+1+1", "-2+3", "@SOMA(1)", "\t=1+1", "\r=1+1", '=HYPERLINK("http://x")']


@pytest.mark.parametrize("perigoso", PERIGOSOS)
def test_injecao_de_formula_neutralizada_csv_e_xlsx(client, auth, c, repo, perigoso):  # type: ignore[no-untyped-def]
    nova_req(repo, c.sol, nome_solicitante=perigoso, trajeto=perigoso)
    url = f"{BASE}/movimentacao/arquivo"
    q = {"incluir_nomes": "true", "incluir_trajeto": "true"}
    csv_ = get(client, auth, c.aut, url, formato="csv", **q).data.decode("utf-8-sig")
    tabela = list(csv.reader(io.StringIO(csv_, newline=""), delimiter=";"))
    cab_csv, linha = tabela[0], tabela[1]
    assert linha[cab_csv.index("Solicitante")] == "'" + perigoso
    assert linha[cab_csv.index("Trajeto")] == "'" + perigoso
    xl = get(client, auth, c.aut, url, formato="xlsx", **q)
    ws = openpyxl.load_workbook(io.BytesIO(xl.data)).active
    cab = [cel.value for cel in ws[1]]
    celula = ws.cell(row=2, column=cab.index("Solicitante") + 1)
    assert celula.value.startswith("'") and celula.data_type == "s"
    assert celula.value[1:].strip() == perigoso.strip()  # CR pode virar LF no XML
    traj = ws.cell(row=2, column=cab.index("Trajeto") + 1)
    assert traj.value.startswith("'") and traj.data_type == "s"


# ------------------------------------------------------------------------------ XLSX
def test_xlsx_abre_estilo_fuso_e_auditoria(client, auth, c, repo):  # type: ignore[no-untyped-def]
    r = nova_req(repo, c.sol)
    repo.requisicoes[r["id"]]["criado_em"] = "2026-11-08T02:30:00+00:00"
    resp = get(client, auth, c.aut, f"{BASE}/movimentacao/arquivo", formato="xlsx", via="download")
    assert resp.status_code == 200
    assert resp.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert re.search(
        r'filename="relatorio-movimentacao-\d{8}\.xlsx"', resp.headers["Content-Disposition"]
    )
    assert resp.headers["Cache-Control"] == "no-store"
    ws = openpyxl.load_workbook(io.BytesIO(resp.data)).active
    cab = ws[1]
    assert cab[0].font.b is True and cab[0].font.color.rgb.endswith("FFFFFF")
    assert cab[0].fill.start_color.rgb.endswith("0F3D75")
    assert ws.freeze_panes == "A2"
    assert ws.column_dimensions["A"].width >= 10
    criada = ws.cell(row=2, column=2)
    assert criada.value == datetime(2026, 11, 7, 23, 30)  # 02:30 UTC = 23:30 do dia anterior
    assert criada.number_format == "dd/mm/yyyy hh:mm"
    duracao = [x.value for x in cab].index("Duração do uso (hh:mm:ss)") + 1
    assert ws.cell(row=2, column=duracao).value == "01:02:05"


def test_xlsx_sem_linhas_e_resumo(client, auth, c):  # type: ignore[no-untyped-def]
    resp = get(client, auth, c.aut, f"{BASE}/resumo/arquivo", formato="xlsx")
    ws = openpyxl.load_workbook(io.BytesIO(resp.data)).active
    assert [x.value for x in ws[1]][:2] == ["Dia", "Requisições"]
    assert ws.cell(row=2, column=1).value == "01/11/2026"
    assert ws.cell(row=2, column=7).value == 1.5


# ------------------------------------------------------------- arquivo: via e auditoria
def test_formato_e_via_invalidos(client, auth, c):  # type: ignore[no-untyped-def]
    url = f"{BASE}/movimentacao/arquivo"
    assert get(client, auth, c.aut, url, formato="pdf").status_code == 400
    assert get(client, auth, c.aut, url, via="correio").status_code == 400
    r = get(client, auth, c.aut, url, via="compartilhar")
    assert r.status_code == 404 and r.get_json()["erro"]["codigo"] == "recurso_desligado"


def test_auditoria_do_arquivo_sem_conteudo_nem_pii(client, auth, c, repo):  # type: ignore[no-untyped-def]
    repo.config = repo.config.__class__(relatorio_whatsapp=True)
    nova_req(repo, c.sol)
    r = get(
        client,
        auth,
        c.aut,
        f"{BASE}/movimentacao/arquivo",
        formato="xlsx",
        via="compartilhar",
        unidade="PU",
        incluir_nomes="true",
    )
    assert r.status_code == 200
    (ev,) = repo.eventos_de("relatorio_gerado")
    assert ev["tabela"] == "relatorios" and ev["ator_id"] == c.aut.id and ev["registro_id"] is None
    d = ev["detalhes"]
    assert set(d) == {"tipo", "filtros", "formato", "via", "total_linhas"}
    assert d["tipo"] == "movimentacao" and d["formato"] == "xlsx" and d["via"] == "compartilhar"
    assert d["total_linhas"] == 1 and d["filtros"]["unidade"] == "PU"
    assert "@" not in str(d) and c.sol.nome not in str(d)


def test_sem_auditoria_nao_ha_arquivo(client, auth, c, repo):  # type: ignore[no-untyped-def]
    from app.errors import ErroInterno

    repo.falha_auditoria = ErroInterno("falha")
    r = get(client, auth, c.aut, f"{BASE}/movimentacao/arquivo")
    assert r.status_code == 503 and b"Sede" not in r.data and not r.data.startswith(b"\xef")


def test_rate_limit_do_arquivo(mk, auth, c):  # type: ignore[no-untyped-def]
    cli = mk(limite_relatorios_arquivo="2 per minute").test_client()
    codigos = [get(cli, auth, c.aut, f"{BASE}/pendencias/arquivo").status_code for _ in range(3)]
    assert codigos == [200, 200, 429]
    # a prévia tem limite próprio e não é afetada
    assert get(cli, auth, c.aut, f"{BASE}/pendencias/previa").status_code == 200


# --------------------------------------------------------------- configurações / eu
def test_recursos_novos_em_eu_e_configuracoes(client, auth, c, repo):  # type: ignore[no-untyped-def]
    eu = get(client, auth, c.sol, "/api/eu").get_json()
    assert (
        eu["recursos"]["relatorio_email"] is False and eu["recursos"]["relatorio_whatsapp"] is False
    )
    r = client.put(
        "/api/admin/configuracoes",
        headers=auth(c.adm.id),
        json={"relatorio_email": True, "relatorio_whatsapp": True},
    )
    assert r.status_code == 200
    itens = {i["chave"]: i for i in r.get_json()["itens"]}
    assert itens["relatorio_email"]["rotulo"] == "Relatórios por e-mail"
    assert itens["relatorio_whatsapp"]["habilitado"] is True
    assert {e["detalhes"]["recurso"] for e in repo.eventos_de("configuracao_alterada")} == {
        "relatorio_email",
        "relatorio_whatsapp",
    }
    assert get(client, auth, c.sol, "/api/eu").get_json()["recursos"]["relatorio_email"] is True


def test_repo_le_e_grava_as_novas_chaves():  # type: ignore[no-untyped-def]
    r = SupabaseRepositorio.__new__(SupabaseRepositorio)
    r._c = MagicMock()
    linha = {
        "quilometragem": False,
        "abastecimento_manutencao": False,
        "checklist": False,
        "mfa": False,
        "relatorio_email": True,
        "relatorio_whatsapp": False,
        "notificacao_email_criticos": False,
        "motorista": False,
        "motorista_registra": False,
        "motorista_localizacao": False,
    }
    r._c.table.return_value.select.return_value.limit.return_value.execute.return_value.data = [
        linha
    ]
    cfg = r.ler_configuracoes()
    assert cfg.relatorio_email is True and cfg.relatorio_whatsapp is False
    assert "relatorio_email" in r._c.table.return_value.select.call_args.args[0]


@pytest.mark.parametrize(
    ("codigo", "status", "codigo_api"),
    [
        ("AG024", 422, "destinatario_invalido"),
        ("AG025", 429, "cota_envio_excedida"),
        ("AG026", 404, "recurso_desligado"),
        ("AG027", 400, "envio_invalido"),
        ("AG028", 404, "nao_encontrado"),
    ],
)
def test_traducao_dos_erros_de_envio(codigo, status, codigo_api):  # type: ignore[no-untyped-def]
    e = erro_db(codigo, "mensagem do banco com a@b.com")
    assert (e.status, e.codigo) == (status, codigo_api)  # type: ignore[attr-defined]
    assert "a@b.com" not in e.mensagem  # type: ignore[attr-defined]


def test_config_do_ambiente_e_validacao(monkeypatch):  # type: ignore[no-untyped-def]
    for k, v in {
        "EMAIL_ENABLED": "true",
        "EMAIL_HOST": "smtp.exemplo.gov.br",
        "EMAIL_PORT": "999999",
        "EMAIL_PASSWORD": "SEGREDO-XYZ",
        "EMAIL_SECURITY": "SSL",
        "EMAIL_FROM": "x@exemplo.gov.br",
        "EMAIL_MAX_PER_HOUR": "abc",
    }.items():
        monkeypatch.setenv(k, v)
    cfg = Config.from_env()
    assert cfg.email_enabled and cfg.email_port == 65535 and cfg.email_security == "ssl"
    assert cfg.email_max_per_hour == 30 and cfg.email_timeout_seconds == 15
    assert "SEGREDO-XYZ" not in repr(cfg)
    base = {
        "supabase_url": "https://x.supabase.co",
        "supabase_service_role_key": "k",
        "jwks_url": "https://x.supabase.co/auth/v1/.well-known/jwks.json",
        "jwt_issuer": "https://x.supabase.co/auth/v1",
    }
    Config(**base).validar_producao()  # e-mail desligado: nada a exigir
    with pytest.raises(RuntimeError, match="EMAIL_HOST"):
        Config(**base, email_enabled=True, email_from="a@b.gov.br").validar_producao()
    with pytest.raises(RuntimeError, match="EMAIL_SECURITY"):
        Config(
            **base, email_enabled=True, email_host="h", email_from="a@b.gov.br", email_security="x"
        ).validar_producao()


def test_rotas_do_blueprint_nao_registram_query_em_log(client, auth, c, caplog):  # type: ignore[no-untyped-def]
    caplog.set_level("DEBUG")
    get(client, auth, c.adm, f"{BASE}/movimentacao/previa", unidade="PU", inicio="2026-11-01")
    assert "inicio=" not in caplog.text and "2026-11-01" not in caplog.text
