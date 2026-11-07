"""Armazenamento das fotos do checklist (Supabase Storage, bucket privado `checklists`).

Todo acoplamento com o Storage e com a convenção de caminho do banco fica aqui. O caminho
espelha `checklist_fotos.caminho` (coluna gerada, migration 19):
`<requisicao_id>/<momento>/<id da foto>.<ext>`. Nada aqui é logado com caminho ou URL.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, TypedDict

import httpx
from supabase import Client, ClientOptions, create_client

from app.errors import ErroInterno

log = logging.getLogger(__name__)

BUCKET = "checklists"
EXTENSOES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
TIMEOUT_STORAGE = 20
MSG_INDISPONIVEL = "Armazenamento de fotos indisponível. Tente novamente."


PLACEHOLDER_PASTA = ".emptyFolderPlaceholder"


def _sem_upsert() -> Any:
    """Opções da URL de upload: `x-upsert: false` explícito.

    Com upsert falso, um SEGUNDO PUT no mesmo caminho (mesmo com o token válido) deve ser
    recusado pelo Storage com 409 (objeto já existe), então ninguém sobrescreve uma foto já
    enviada. Em storage3 2.34.1 (a versão fixada) `create_signed_upload_url` aceita
    `CreateSignedUploadUrlOptions(upsert="false")` e envia o cabeçalho; versões antigas (só
    ambiente de desenvolvimento) não têm o parâmetro e o padrão do Storage já é não sobrescrever.
    """
    try:
        from storage3.types import CreateSignedUploadUrlOptions
    except ImportError:  # pragma: no cover - só em ambiente com storage3 antigo
        return None
    return CreateSignedUploadUrlOptions(upsert="false")


def caminho_foto(requisicao_id: str, momento: str, foto_id: str, mime: str) -> str:
    """Mesmo caminho que o banco gera para a foto (ver migration 19)."""
    return f"{requisicao_id}/{momento}/{foto_id}.{EXTENSOES[mime]}"


class UploadAssinado(TypedDict):
    url: str
    token: str


class MetadadosObjeto(TypedDict):
    mime: str
    tamanho: int


class Armazenamento(Protocol):
    def url_upload(self, caminho: str) -> UploadAssinado:
        """URL assinada de UPLOAD (uso único, sem sobrescrever) para o caminho."""
        ...

    def metadados(self, caminho: str) -> MetadadosObjeto | None:
        """MIME e tamanho do objeto gravado, ou None se não existir."""
        ...

    def contar_objetos(self, pasta: str) -> int:
        """Quantidade de objetos na pasta `<requisicao_id>/<momento>`."""
        ...

    def url_leitura(self, caminho: str, expira_em: int) -> str:
        """URL assinada de LEITURA, válida por `expira_em` segundos."""
        ...

    def ler_inicio(self, caminho: str, n: int) -> bytes | None:
        """Primeiros `n` bytes do objeto (para conferir a assinatura do arquivo)."""
        ...

    def remover(self, caminho: str) -> None:
        """Remove o objeto (arquivo reprovado na conferência)."""
        ...


class SupabaseArmazenamento:
    """Usa a credencial service_role (só no backend). O front nunca a recebe."""

    def __init__(
        self,
        url: str,
        service_role_key: str,
        *,
        cliente: Client | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        # `cliente` e `http` existem para injeção nos testes (MockTransport / dublê do storage).
        self._c: Client = cliente or create_client(
            url,
            service_role_key,
            options=ClientOptions(storage_client_timeout=TIMEOUT_STORAGE),
        )
        self._http = http or httpx.Client(timeout=TIMEOUT_STORAGE)

    def _bucket(self) -> Any:
        return self._c.storage.from_(BUCKET)

    @staticmethod
    def _falha(e: Exception) -> ErroInterno:
        # Só o tipo vai ao log: mensagens do Storage podem trazer caminho/URL.
        log.error("erro_storage tipo=%s", type(e).__name__)
        return ErroInterno(MSG_INDISPONIVEL)

    def url_upload(self, caminho: str) -> UploadAssinado:
        try:
            opcoes = _sem_upsert()
            bucket = self._bucket()
            r = (
                bucket.create_signed_upload_url(caminho)
                if opcoes is None
                else bucket.create_signed_upload_url(caminho, opcoes)
            )
            return {"url": str(r["signed_url"]), "token": str(r["token"])}
        except Exception as e:
            raise self._falha(e) from None

    def _listar(self, pasta: str, arquivo: str | None) -> list[dict[str, Any]]:
        opcoes: dict[str, Any] = {"limit": 100}
        if arquivo is not None:
            opcoes["search"] = arquivo
        try:
            return list(self._bucket().list(pasta, opcoes))
        except Exception as e:
            raise self._falha(e) from None

    def metadados(self, caminho: str) -> MetadadosObjeto | None:
        pasta, _, arquivo = caminho.rpartition("/")
        for item in self._listar(pasta, arquivo):
            if item.get("name") != arquivo:
                continue
            meta = item.get("metadata") or {}
            try:
                return {"mime": str(meta["mimetype"]), "tamanho": int(meta["size"])}
            except (KeyError, TypeError, ValueError):
                return {"mime": "", "tamanho": -1}  # objeto sem metadados utilizáveis: diverge
        return None

    def contar_objetos(self, pasta: str) -> int:
        # Placeholder de pasta (`.emptyFolderPlaceholder`) e subpastas (sem `id`) não são fotos.
        return sum(
            1
            for item in self._listar(pasta, None)
            if item.get("name") != PLACEHOLDER_PASTA and item.get("id") is not None
        )

    def url_leitura(self, caminho: str, expira_em: int) -> str:
        try:
            r = self._bucket().create_signed_url(caminho, expira_em)
            return str(r.get("signedURL") or r["signedUrl"])
        except Exception as e:
            raise self._falha(e) from None

    def ler_inicio(self, caminho: str, n: int) -> bytes | None:
        try:
            url = self.url_leitura(caminho, 60)  # a URL nunca é logada
            # Em stream: se o Storage ignorar o Range (200 com o corpo inteiro), lê só `n`
            # bytes e fecha a conexão, sem baixar até 5 MB.
            with self._http.stream("GET", url, headers={"Range": f"bytes=0-{n - 1}"}) as r:
                if r.status_code not in {200, 206}:  # 404, 416 (objeto vazio) etc.
                    return None
                buf = bytearray()
                for parte in r.iter_bytes():
                    buf += parte
                    if len(buf) >= n:
                        break
                return bytes(buf[:n])
        except ErroInterno:
            raise
        except Exception as e:
            raise self._falha(e) from None

    def remover(self, caminho: str) -> None:
        try:
            self._bucket().remove([caminho])
        except Exception as e:
            raise self._falha(e) from None


def assinatura_confere(mime: str, inicio: bytes) -> bool:
    """Magic bytes de JPEG, PNG e WebP (RIFF....WEBP)."""
    if mime == "image/jpeg":
        return inicio.startswith(bytes([0xFF, 0xD8, 0xFF]))
    if mime == "image/png":
        return inicio.startswith(bytes([0x89]) + b"PNG" + bytes([0x0D, 0x0A, 0x1A, 0x0A]))
    if mime == "image/webp":
        return inicio[:4] == b"RIFF" and inicio[8:12] == b"WEBP"
    return False
