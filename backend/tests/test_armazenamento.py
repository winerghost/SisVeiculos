"""Adaptador real SupabaseArmazenamento, com dublê do cliente storage e httpx.MockTransport."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.errors import ErroInterno
from app.repositorios.armazenamento import BUCKET, SupabaseArmazenamento

PNG = bytes([0x89]) + b"PNG" + bytes([0x0D, 0x0A, 0x1A, 0x0A]) + bytes(8)


class BucketFalso:
    def __init__(self, itens: list[dict[str, Any]] | None = None) -> None:
        self.itens = itens or []
        self.listagens: list[tuple[str, dict[str, Any]]] = []
        self.upload_chamadas: list[tuple[Any, ...]] = []
        self.falha: Exception | None = None

    def list(self, pasta: str, opcoes: dict[str, Any]) -> list[dict[str, Any]]:
        if self.falha:
            raise self.falha
        self.listagens.append((pasta, opcoes))
        busca = opcoes.get("search")
        return [i for i in self.itens if not busca or i["name"].startswith(busca)]

    def create_signed_upload_url(self, *args: Any) -> dict[str, str]:
        if self.falha:
            raise self.falha
        self.upload_chamadas.append(args)
        return {"signed_url": "https://s.exemplo/up?token=t", "token": "t"}

    def create_signed_url(self, caminho: str, expira: int) -> dict[str, str]:
        return {"signedURL": f"https://s.exemplo/obj/{caminho}?exp={expira}"}


class ClienteFalso:
    def __init__(self, bucket: BucketFalso) -> None:
        self.bucket = bucket
        self.storage = self

    def from_(self, nome: str) -> BucketFalso:
        assert nome == BUCKET
        return self.bucket


def adaptador(
    bucket: BucketFalso, handler: Any = None
) -> tuple[SupabaseArmazenamento, list[httpx.Request]]:
    vistos: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        vistos.append(req)
        return handler(req)

    http = httpx.Client(transport=httpx.MockTransport(h))
    cliente: Any = ClienteFalso(bucket)
    arm = SupabaseArmazenamento("https://p.supabase.co", "x", cliente=cliente, http=http)
    return arm, vistos


def item(nome: str, tamanho: int = 10, mime: str = "image/png") -> dict[str, Any]:
    return {"name": nome, "id": f"id-{nome}", "metadata": {"size": tamanho, "mimetype": mime}}


# ------------------------------------------------------------------ list / metadados / contar
def test_metadados_ignora_itens_de_busca_por_prefixo():
    exato = item("abc.png", 7)
    b = BucketFalso([item("abc.png.bak", 99), item("abc.pngx", 98), exato])
    arm, _ = adaptador(b)
    assert arm.metadados("req/saida/abc.png") == {"mime": "image/png", "tamanho": 7}
    assert b.listagens[0][0] == "req/saida" and b.listagens[0][1]["search"] == "abc.png"


def test_metadados_sem_item_exato_devolve_none():
    arm, _ = adaptador(BucketFalso([item("abc.png.bak")]))
    assert arm.metadados("req/saida/abc.png") is None


def test_metadados_sem_metadata_utilizavel_diverge():
    arm, _ = adaptador(BucketFalso([{"name": "abc.png", "id": "1", "metadata": None}]))
    assert arm.metadados("r/s/abc.png") == {"mime": "", "tamanho": -1}


def test_contar_objetos_ignora_placeholder_e_subpastas():
    b = BucketFalso(
        [
            item("a.png"),
            item("b.jpg"),
            {"name": ".emptyFolderPlaceholder", "id": "p", "metadata": {"size": 0}},
            {"name": "subpasta", "id": None, "metadata": None},
        ]
    )
    arm, _ = adaptador(b)
    assert arm.contar_objetos("req/saida") == 2


# ------------------------------------------------------------------ upload sem upsert
def test_url_upload_pede_upsert_falso():
    b = BucketFalso()
    arm, _ = adaptador(b)
    r = arm.url_upload("req/saida/x.png")
    assert r == {"url": "https://s.exemplo/up?token=t", "token": "t"}
    args = b.upload_chamadas[0]
    if len(args) > 1:  # storage3 2.34.1 (fixado): opções explícitas
        assert args[1].upsert == "false"


# ------------------------------------------------------------------ ler_inicio
def test_ler_inicio_206_devolve_so_n_bytes_e_envia_range():
    arm, vistos = adaptador(BucketFalso(), lambda _r: httpx.Response(206, content=PNG[:16]))
    assert arm.ler_inicio("r/s/a.png", 16) == PNG[:16]
    assert vistos[0].headers["Range"] == "bytes=0-15"


def test_ler_inicio_range_ignorado_200_nao_baixa_o_corpo_inteiro():
    lidos = {"total": 0}

    def corpo():  # type: ignore[no-untyped-def]
        for _ in range(1000):  # 1000 x 8 KB = ~8 MB, se alguém consumir tudo
            lidos["total"] += 1
            yield PNG + bytes(8192)

    arm, _ = adaptador(BucketFalso(), lambda _r: httpx.Response(200, content=corpo()))
    assert arm.ler_inicio("r/s/a.png", 16) == PNG[:16]
    assert lidos["total"] <= 2  # parou no primeiro bloco


@pytest.mark.parametrize("status", [404, 416, 403, 500])
def test_ler_inicio_status_sem_conteudo_devolve_none(status):  # type: ignore[no-untyped-def]
    arm, _ = adaptador(BucketFalso(), lambda _r: httpx.Response(status, content=b"x"))
    assert arm.ler_inicio("r/s/a.png", 16) is None


def test_ler_inicio_erro_de_rede_vira_indisponivel_sem_vazar_url(caplog):  # type: ignore[no-untyped-def]
    def estoura(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("falha em https://s.exemplo/obj/segredo")

    arm, _ = adaptador(BucketFalso(), estoura)
    with pytest.raises(ErroInterno) as e:
        arm.ler_inicio("r/s/a.png", 16)
    assert e.value.status == 503 and e.value.codigo == "indisponivel"
    assert "segredo" not in caplog.text and "s.exemplo" not in caplog.text


# ------------------------------------------------------------------ Storage indisponível = 503
def test_storage_indisponivel_e_503_indisponivel_sem_vazar(caplog):  # type: ignore[no-untyped-def]
    b = BucketFalso()
    b.falha = RuntimeError("timeout em https://s.exemplo/obj/req/saida/foto.png?token=abc")
    arm, _ = adaptador(b)
    for chamada in (
        lambda: arm.url_upload("r/s/a.png"),
        lambda: arm.metadados("r/s/a.png"),
        lambda: arm.contar_objetos("r/s"),
    ):
        with pytest.raises(ErroInterno) as e:
            chamada()
        assert (e.value.status, e.value.codigo) == (503, "indisponivel")
    assert "token=abc" not in caplog.text and "foto.png" not in caplog.text


# ------------------------------------------------------------------ ler_tudo / sobrescrever
def test_ler_tudo_200_devolve_corpo_e_corta_acima_do_maximo():
    arm, _ = adaptador(BucketFalso(), lambda _r: httpx.Response(200, content=bytes(100)))
    assert arm.ler_tudo("r/s/a.png", 1000) == bytes(100)
    assert len(arm.ler_tudo("r/s/a.png", 10) or b"") == 11  # maximo + 1: o chamador recusa


@pytest.mark.parametrize("status", [404, 403, 500])
def test_ler_tudo_status_de_erro_devolve_none(status):  # type: ignore[no-untyped-def]
    arm, _ = adaptador(BucketFalso(), lambda _r: httpx.Response(status, content=b"x"))
    assert arm.ler_tudo("r/s/a.png", 10) is None


def test_sobrescrever_usa_upsert_true_e_mime():
    chamadas: list[tuple[Any, ...]] = []

    class BucketComUpload(BucketFalso):
        def upload(self, *args: Any) -> None:
            chamadas.append(args)

    arm, _ = adaptador(BucketComUpload())
    arm.sobrescrever("r/s/a.png", b"dados", "image/png")
    assert chamadas == [("r/s/a.png", b"dados", {"content-type": "image/png", "upsert": "true"})]


def test_sobrescrever_falha_vira_indisponivel_sem_vazar(caplog):  # type: ignore[no-untyped-def]
    class BucketQuebrado(BucketFalso):
        def upload(self, *args: Any) -> None:
            raise RuntimeError("falha em r/s/segredo.png")

    arm, _ = adaptador(BucketQuebrado())
    with pytest.raises(ErroInterno) as e:
        arm.sobrescrever("r/s/segredo.png", b"x", "image/png")
    assert e.value.status == 503 and "segredo" not in caplog.text
