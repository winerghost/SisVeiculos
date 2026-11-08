"""Armazenamento das fotos de perfil (Supabase Storage, bucket privado `avatares`).

Todo acoplamento com o Storage e com a convenção de caminhos do banco fica aqui:
- `tmp/<usuario_id>/<id>`: STAGING, arquivo original enviado pelo navegador (não confiável);
- `<usuario_id>/<id>.<ext>`: foto FINAL, só a imagem recodificada pelo servidor. Espelha
  `perfil_fotos.caminho` (coluna gerada, migration 25a).

Nada aqui é logado com caminho, URL ou bytes; falhas do Storage viram `ErroInterno` fixo.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from app.errors import ErroInterno
from app.repositorios.armazenamento import (
    EXTENSOES,
    MSG_INDISPONIVEL,
    MetadadosObjeto,
    SupabaseArmazenamento,
    UploadAssinado,
)

BUCKET_AVATARES = "avatares"
TAMANHO_MAX_BYTES = 1_048_576  # espelha file_size_limit do bucket e o CHECK do banco
_FINAL = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(jpg|png|webp)$"
)


def caminho_staging(usuario_id: str, foto_id: str) -> str:
    """Objeto temporário do upload. O dono sai do JWT, nunca do corpo."""
    return f"tmp/{usuario_id}/{foto_id}"


def caminho_final(usuario_id: str, foto_id: str, mime: str) -> str:
    """Mesmo caminho que o banco gera para a foto (ver migration 25a)."""
    return f"{usuario_id}/{foto_id}.{EXTENSOES[mime]}"


def caminho_final_do_usuario(usuario_id: str, caminho: str | None) -> bool:
    """Defesa em profundidade: só apaga/assina o que tem o formato final E é do próprio usuário."""
    if not caminho or not _FINAL.match(caminho):
        return False
    return caminho.startswith(usuario_id + "/")


class ObjetoJaExiste(Exception):  # noqa: N818
    """Gravação recusada porque o caminho já existe (upsert=false)."""


class ArmazenamentoAvatares(Protocol):
    def url_upload(self, caminho: str) -> UploadAssinado:
        """URL assinada de UPLOAD (uso único, sem sobrescrever) para o staging."""
        ...

    def metadados(self, caminho: str) -> MetadadosObjeto | None:
        """MIME e tamanho do objeto gravado, ou None se não existir."""
        ...

    def ler_tudo(self, caminho: str, maximo: int) -> bytes | None:
        """Lê até `maximo`+1 bytes (quem chama compara com `maximo`). None se não existe."""
        ...

    def gravar(self, caminho: str, dados: bytes, mime: str) -> None:
        """Grava com upsert=false. Levanta `ObjetoJaExiste` se o caminho já existir."""
        ...

    def url_leitura(self, caminho: str, expira_em: int) -> str: ...

    def urls_leitura(self, caminhos: list[str], expira_em: int) -> dict[str, str]:
        """URLs assinadas de leitura em lote (caminho -> URL). Caminho sem URL fica de fora."""
        ...

    def remover(self, caminho: str) -> None: ...


class SupabaseArmazenamentoAvatares(SupabaseArmazenamento):
    """Mesma credencial service_role; muda só o bucket. Herda upload assinado, metadados,
    leitura assinada e remoção do adaptador do checklist."""

    def _bucket(self) -> Any:
        return self._c.storage.from_(BUCKET_AVATARES)

    def ler_tudo(self, caminho: str, maximo: int) -> bytes | None:
        try:
            url = self.url_leitura(caminho, 60)  # a URL nunca é logada
            with self._http.stream("GET", url) as r:
                if r.status_code != 200:
                    return None
                buf = bytearray()
                for parte in r.iter_bytes():
                    buf += parte
                    if len(buf) > maximo:  # para de baixar: o chamador recusa por tamanho
                        break
                return bytes(buf[: maximo + 1])
        except ErroInterno:
            raise
        except Exception as e:
            raise self._falha(e) from None

    def gravar(self, caminho: str, dados: bytes, mime: str) -> None:
        try:
            self._bucket().upload(caminho, dados, {"content-type": mime, "upsert": "false"})
        except Exception as e:
            if str(getattr(e, "status", "")) == "409" or (
                str(getattr(e, "code", "")).lower() == "duplicate"
            ):
                raise ObjetoJaExiste from None
            raise self._falha(e) from None

    def urls_leitura(self, caminhos: list[str], expira_em: int) -> dict[str, str]:
        """Uma chamada em lote (`create_signed_urls`, presente no storage3 2.34.1 fixado). Se a
        lib não tiver o método, vira ErroInterno (quem exibe degrada para foto_url nulo): nunca
        N chamadas uma a uma."""
        if not caminhos:
            return {}
        try:
            itens = self._bucket().create_signed_urls(caminhos, expira_em)
        except Exception as e:
            raise self._falha(e) from None
        saida: dict[str, str] = {}
        for i in itens:
            url = i.get("signedURL") or i.get("signedUrl")
            if url and not i.get("error") and i.get("path"):
                saida[str(i["path"])] = str(url)
        return saida


__all__ = [
    "BUCKET_AVATARES",
    "MSG_INDISPONIVEL",
    "TAMANHO_MAX_BYTES",
    "ArmazenamentoAvatares",
    "ObjetoJaExiste",
    "SupabaseArmazenamentoAvatares",
    "caminho_final",
    "caminho_final_do_usuario",
    "caminho_staging",
]
