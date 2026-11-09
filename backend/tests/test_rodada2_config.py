"""Rodada 2: IP do cliente validado, CIDRs de proxy estreitos, domínios de relatório no Config,
regex de e-mail, limite de registro do checklist e lacunas do QA (HMAC, DNS, Dockerfile)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.config import Config
from app.extensions import ip_cliente, limiter
from app.notificacoes.n8n import exigir_destino_publico
from app.services.relatorios import _dominio_do_email, dominios_institucionais
from tests.test_auditoria_autenticacao import BASE_PROD, TOKEN
from tests.test_checklist import BASE, nova, post
from tests.test_checklist_seguranca import _cliente, hdr_fn


# ----------------------------------------------------------------- N-02: ip_cliente
def _ip(app: Any, remoto: str, cabecalho: str | None) -> str:
    headers = {"CF-Connecting-IP": cabecalho} if cabecalho is not None else {}
    with app.test_request_context("/", headers=headers, environ_base={"REMOTE_ADDR": remoto}):
        return ip_cliente()


@pytest.mark.parametrize(
    "lixo",
    ["lixo", "1.2.3", "999.1.1.1", "1.2.3.4, 5.6.7.8", "1.2.3.4:80", "<script>", "a" * 500, " "],
)
def test_cabecalho_invalido_cai_no_remote_addr(app, lixo) -> None:  # type: ignore[no-untyped-def]
    assert _ip(app, "10.213.88.2", lixo) == "10.213.88.2"


def test_cabecalho_valido_ipv4_e_ipv6_canonico(app) -> None:  # type: ignore[no-untyped-def]
    assert _ip(app, "10.213.88.2", " 203.0.113.7 ") == "203.0.113.7"
    assert _ip(app, "10.213.88.2", "2001:DB8:0:0:0:0:0:1") == "2001:db8::1"


# ----------------------------------------------------------------- N-03: CIDRs estreitos
@pytest.mark.parametrize(
    "cidr",
    ["0.0.0.0/0", "::/0", "10.0.0.0/8", "172.16.0.0/12", "8.8.8.0/24", "1.1.1.0/24", "fc00::/7"],
)
def test_producao_recusa_proxy_amplo_ou_publico(cidr: str) -> None:
    with pytest.raises(RuntimeError, match="TRUSTED_PROXY_CIDRS"):
        Config(**BASE_PROD, trusted_proxy_cidrs=[cidr]).validar_producao()


@pytest.mark.parametrize(
    "cidr", ["10.213.88.0/24", "172.30.0.0/16", "127.0.0.1/32", "::1/128", "fd00:1:2::/48"]
)
def test_producao_aceita_proxy_privado_e_estreito(cidr: str) -> None:
    Config(**BASE_PROD, trusted_proxy_cidrs=[cidr]).validar_producao()


def test_padrao_de_proxies_passa_na_validacao() -> None:
    Config(**BASE_PROD).validar_producao()


# ----------------------------------------------------------------- N-06: domínios de relatório
def test_config_le_dominios_do_ambiente_em_minusculas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_DOMINIOS_RELATORIO", " @AGU.gov.br , Outro.GOV.br ,")
    assert Config.from_env().email_dominios_relatorio == ["agu.gov.br", "outro.gov.br"]
    monkeypatch.delenv("EMAIL_DOMINIOS_RELATORIO")
    assert Config.from_env().email_dominios_relatorio == []


@pytest.mark.parametrize(
    "ruim",
    ["*", "*.agu.gov.br", "agu.*", "gmail.com", "hotmail.com", "outlook.com", "yahoo.com",
     "live.com", "icloud.com", "semponto", "a b.gov.br", "https://agu.gov.br", "agu.gov.br/x",
     "-agu.gov.br", "agu..gov.br"],
)  # fmt: skip
def test_producao_recusa_dominio_de_relatorio_perigoso(ruim: str) -> None:
    with pytest.raises(RuntimeError, match="EMAIL_DOMINIOS_RELATORIO"):
        Config(**BASE_PROD, email_dominios_relatorio=[ruim]).validar_producao()


def test_producao_aceita_dominio_institucional() -> None:
    Config(
        **BASE_PROD, email_dominios_relatorio=["agu.gov.br", "sub.agu.gov.br"]
    ).validar_producao()


def test_dominios_institucionais_vem_do_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_DOMINIOS_RELATORIO", "ambiente.gov.br")
    cfg = Config(email_dominios_relatorio=["agu.gov.br"])
    assert dominios_institucionais(cfg) == {"agu.gov.br"}
    assert dominios_institucionais(Config()) == frozenset()


@pytest.mark.parametrize(
    ("email", "dominio"),
    [
        ("fulano@agu.gov.br", "agu.gov.br"),
        ("Fulano.Silva+x@AGU.gov.br", "agu.gov.br"),
        ("a@b@agu.gov.br", ""),
        ('"a b"@agu.gov.br', ""),
        ("fulano@agu.gov.br\n", "agu.gov.br"),
        ("fulano@agu.gov.br, outro@gmail.com", ""),
        ("fulano@[10.0.0.1]", ""),
        ("fulano@agu", ""),
        ("fulano @agu.gov.br", ""),
        ("fulano@agu.gov.br>", ""),
        ("", ""),
    ],
)
def test_regex_estrita_do_destinatario(email: str, dominio: str) -> None:
    assert _dominio_do_email(email) == dominio


# ----------------------------------------------------------------- N-08: limite por usuário
def test_registro_de_checklist_tem_limite_por_usuario(repo, storage, c) -> None:  # type: ignore[no-untyped-def]
    from tests.test_checklist import LIGADO

    cli, hdr = _cliente(repo, storage, limite_registro_checklist="2 per minute")
    repo.config = LIGADO
    rid = nova(cli, hdr_fn(hdr), c)
    outro = repo.novo_perfil("autorizador")
    url = f"/api/requisicoes/{rid}/checklist/saida"
    # 1o registra (201), 2o é duplicado (409) mas conta no limite, 3o é cortado (429)
    codigos = [cli.post(url, headers=hdr(c.aut), json=BASE).status_code for _ in range(3)]
    assert codigos == [201, 409, 429]
    assert cli.post(url, headers=hdr(outro), json=BASE).status_code == 409  # outro usuário: livre
    limiter.reset()


def test_limite_de_registro_padrao_nao_atrapalha_o_uso_normal(client, auth, c, repo) -> None:  # type: ignore[no-untyped-def]
    from tests.test_checklist import LIGADO

    repo.config = LIGADO
    rid = nova(client, auth, c)
    assert post(client, auth, c.aut, rid, "checklist/saida", BASE).status_code == 201


# ----------------------------------------------------------------- lacunas do QA
def test_producao_recusa_hmac_igual_ao_token() -> None:
    segredo = "x" * 40  # longo o bastante: a recusa é só pela igualdade com o token
    base = {**BASE_PROD, "n8n_webhook_token": segredo, "n8n_hmac_secret": segredo}
    with pytest.raises(RuntimeError, match="distinto"):
        Config(**base).validar_producao()
    Config(**{**base, "n8n_webhook_token": TOKEN}).validar_producao()


def test_resolver_dns_real_recusa_localhost() -> None:
    # Resolve pelo resolvedor REAL (hosts/loopback, sem rede externa) e recusa 127.0.0.1/::1.
    with pytest.raises(RuntimeError, match="não público"):
        exigir_destino_publico("localhost", 443)


def test_cmd_do_dockerfile_nao_loga_ip_do_cliente() -> None:
    texto = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    sem_comentarios = "\n".join(
        linha for linha in texto.splitlines() if not linha.lstrip().startswith("#")
    )
    cmd = sem_comentarios[sem_comentarios.index("CMD") :]
    assert "gunicorn" in cmd and "--access-logformat" in cmd
    assert "%(h)s" not in cmd and not re.search(r"%\((h|a|\{x-forwarded-for\}i)\)s", cmd)
