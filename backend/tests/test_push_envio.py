"""Envio de Web Push pelo despachante (serviço push simulado, repositório falso, sem rede)."""

from __future__ import annotations

import json
import logging
import re
from types import SimpleNamespace
from typing import Any

import pytest

from app.notificacoes import despachante as modulo
from app.notificacoes.despachante import Despachante
from app.notificacoes.push import FalhaPush, montar_payload
from tests.test_push_vapid import PRIVADA, PUBLICA

ENDPOINT = "https://push.exemplo-servico.com/send/SEGREDO-ENDPOINT-123"
CHAVE = PRIVADA
RID = "11111111-1111-4111-8111-111111111111"


class RepoPush:
    def __init__(self) -> None:
        self.notifs: list[dict[str, Any]] = []
        self.inscricoes: dict[str, list[dict[str, Any]]] = {}
        self.status: dict[str, tuple[str, str | None]] = {}
        self.resultados: list[tuple[str, bool, bool]] = []
        self.purgas = 0
        self.falha_reserva: Exception | None = None

    def nova(self, tipo: str = "requisicao_nova", usuario: str = "u1") -> str:
        nid = f"id-{len(self.notifs) + 1}"
        self.notifs.append(
            {"id": nid, "usuario_id": usuario, "tipo": tipo, "requisicao_id": RID, "codigo": None}
        )
        self.status[nid] = ("pendente", None)
        return nid

    def reservar_push_notificacao(self, limite: int) -> list[dict[str, Any]]:
        if self.falha_reserva:
            raise self.falha_reserva
        lote = [n for n in self.notifs if self.status[n["id"]][0] == "pendente"][:limite]
        for n in lote:
            self.status[n["id"]] = ("enviando", None)
        return lote

    def marcar_push_notificacao(self, nid: str, status: str, codigo: str | None = None) -> None:
        self.status[nid] = (status, codigo)

    def listar_inscricoes_push(self, usuario: str) -> list[dict[str, Any]]:
        return self.inscricoes.get(usuario, [])

    def marcar_resultado_inscricao_push(self, iid: str, ok: bool, expirada: bool) -> None:
        self.resultados.append((iid, ok, expirada))

    def purgar_inscricoes_push(self) -> int:
        self.purgas += 1
        return 0

    def purgar_notificacoes(self, dias: int) -> int:
        return 0


class EnviadorFalso:
    def __init__(self, falhas: dict[str, Exception] | None = None) -> None:
        self.falhas = falhas or {}
        self.enviados: list[tuple[dict[str, Any], dict[str, str]]] = []

    def enviar(self, inscricao: Any, payload: Any) -> None:
        if inscricao["id"] in self.falhas:
            raise self.falhas[inscricao["id"]]
        self.enviados.append((dict(inscricao), dict(payload)))


def _publico(host: str, porta: int) -> list[str]:
    return ["8.8.8.8"]


def insc(iid: str = "i1") -> dict[str, Any]:
    return {"id": iid, "endpoint": ENDPOINT, "p256dh": "P", "auth": "A"}


def cfg(vapid: bool = True) -> Any:
    return SimpleNamespace(
        vapid_public_key=PUBLICA if vapid else "",
        vapid_private_key=CHAVE if vapid else "",
        vapid_subject="mailto:ti@exemplo.gov.br" if vapid else "",
        push_endpoint_hosts=[".exemplo-servico.com"],
        push_intervalo_segundos=1,
        email_notif_intervalo_segundos=1,
        email_enabled=False,
        email_notif_max_por_hora=20,
    )


@pytest.fixture(autouse=True)
def _destino_publico(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modulo, "endpoint_publico", lambda e, h=None: True)


def montar(repo: RepoPush, env: EnviadorFalso, vapid: bool = True) -> Despachante:
    return Despachante(repo, None, cfg(vapid), enviador_push=env)  # type: ignore[arg-type]


def test_sucesso_envia_payload_generico() -> None:
    repo, env = RepoPush(), EnviadorFalso()
    nid = repo.nova()
    repo.inscricoes["u1"] = [insc("i1"), insc("i2")]
    assert montar(repo, env).processar_push_uma_vez() == 1
    assert repo.status[nid] == ("enviado", None)
    assert len(env.enviados) == 2
    payload = env.enviados[0][1]
    assert set(payload) == {"titulo", "texto", "destino", "tag"}
    assert payload["tag"] == f"n-{nid}"
    assert payload["destino"].endswith(RID)
    assert ("i1", True, False) in repo.resultados


def test_sem_vapid_nao_reserva() -> None:
    repo, env = RepoPush(), EnviadorFalso()
    repo.nova()
    assert montar(repo, env, vapid=False).processar_push_uma_vez() == 0
    assert repo.status["id-1"][0] == "pendente"


def test_inscricao_expirada_e_removida() -> None:
    repo = RepoPush()
    nid = repo.nova()
    repo.inscricoes["u1"] = [insc("i1")]
    env = EnviadorFalso({"i1": FalhaPush("inscricao_expirada", expirada=True)})
    montar(repo, env).processar_push_uma_vez()
    assert ("i1", False, True) in repo.resultados
    assert repo.status[nid][0] == "ignorado"


def test_falha_temporaria_marca_falhou_sem_levantar() -> None:
    repo = RepoPush()
    nid = repo.nova()
    repo.inscricoes["u1"] = [insc("i1")]
    env = EnviadorFalso({"i1": FalhaPush("http_503")})
    assert montar(repo, env).processar_push_uma_vez() == 0
    assert repo.status[nid] == ("falhou", "http_503")
    assert repo.resultados == []  # 5xx não pune a inscrição


def test_excecao_inesperada_nao_derruba_lote() -> None:
    repo = RepoPush()
    n1, n2 = repo.nova(usuario="u1"), repo.nova(usuario="u2")
    repo.inscricoes = {"u1": [insc("i1")], "u2": [insc("i2")]}
    env = EnviadorFalso({"i1": RuntimeError(ENDPOINT)})
    assert montar(repo, env).processar_push_uma_vez() == 1
    assert repo.status[n1][0] == "falhou"
    assert repo.status[n2][0] == "enviado"


def test_uma_entrega_basta_para_enviado() -> None:
    repo = RepoPush()
    nid = repo.nova()
    repo.inscricoes["u1"] = [insc("i1"), insc("i2")]
    env = EnviadorFalso({"i1": FalhaPush("http_429")})
    assert montar(repo, env).processar_push_uma_vez() == 1
    assert repo.status[nid][0] == "enviado"


def test_sem_inscricao_e_tipo_nao_permitido_sao_ignorados() -> None:
    repo, env = RepoPush(), EnviadorFalso()
    a = repo.nova()
    b = repo.nova(tipo="sistema_falha_envio")
    montar(repo, env).processar_push_uma_vez()
    assert repo.status[a] == ("ignorado", "sem_inscricao")
    assert repo.status[b] == ("ignorado", "tipo")
    assert env.enviados == []


def test_sem_duplicidade_entre_ciclos() -> None:
    repo, env = RepoPush(), EnviadorFalso()
    repo.nova()
    repo.inscricoes["u1"] = [insc()]
    d = montar(repo, env)
    d.processar_push_uma_vez()
    d.processar_push_uma_vez()
    assert len(env.enviados) == 1


def test_endpoint_nao_publico_nao_e_enviado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modulo, "endpoint_publico", lambda e, h=None: False)
    repo, env = RepoPush(), EnviadorFalso()
    nid = repo.nova()
    repo.inscricoes["u1"] = [insc()]
    montar(repo, env).processar_push_uma_vez()
    assert env.enviados == []
    assert repo.status[nid] == ("falhou", "destino_invalido")
    assert repo.resultados == []


def test_falha_na_reserva_nao_levanta() -> None:
    repo, env = RepoPush(), EnviadorFalso()
    repo.falha_reserva = RuntimeError("banco fora")
    assert montar(repo, env).processar_push_uma_vez() == 0


def test_payload_sem_pii_varre_json() -> None:
    item = {
        "id": "id-9",
        "tipo": "checklist_avaria",
        "requisicao_id": RID,
        "codigo": "saida",
        "nome": "Maria Souza",
        "placa": "ABC1D23",
        "trajeto": "Sede -> Aeroporto",
    }
    bruto = json.dumps(montar_payload(item), ensure_ascii=False)
    for proibido in ("Maria", "ABC1D23", "Aeroporto", "trajeto", "placa"):
        assert proibido not in bruto
    assert not re.search(r"@|\bkm\b", bruto)


def test_logs_sem_endpoint_nem_chave(caplog: pytest.LogCaptureFixture) -> None:
    repo = RepoPush()
    repo.nova()
    repo.nova(usuario="u2")
    repo.inscricoes = {"u1": [insc("i1")], "u2": [insc("i2")]}
    env = EnviadorFalso({"i1": RuntimeError(ENDPOINT + CHAVE), "i2": FalhaPush("http_500")})
    with caplog.at_level(logging.DEBUG):
        montar(repo, env).processar_push_uma_vez()
    assert "SEGREDO-ENDPOINT" not in caplog.text and "push.exemplo" not in caplog.text
    assert CHAVE not in caplog.text


def test_enviador_real_descarta_mensagem_da_excecao(monkeypatch: pytest.MonkeyPatch) -> None:
    pywebpush = pytest.importorskip("pywebpush")
    from app.notificacoes.push import EnviadorWebPush

    def falso(**kw: Any) -> None:
        assert kw["requests_session"].max_redirects == 0
        assert kw["ttl"] == 24 * 3600 and kw["vapid_claims"]["sub"].startswith("mailto:")
        raise pywebpush.WebPushException(ENDPOINT, response=SimpleNamespace(status_code=410))

    monkeypatch.setattr(pywebpush, "webpush", falso)
    with pytest.raises(FalhaPush) as e:
        EnviadorWebPush(cfg(), _publico).enviar(insc(), {"titulo": "t"})
    assert e.value.expirada and ENDPOINT not in str(e.value)


def test_expurgo_chama_purga_de_inscricoes() -> None:
    repo, env = RepoPush(), EnviadorFalso()
    t = [0.0]
    d = Despachante(repo, None, cfg(), enviador_push=env, relogio=lambda: t[0])  # type: ignore[arg-type]
    t[0] = 1000.0
    d.expurgar_se_devido()
    assert repo.purgas == 1
