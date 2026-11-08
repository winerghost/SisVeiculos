"""Fakes da foto de perfil: parte do repositório (emula as migrations 25a e 25c) e Storage.

Os erros passam por `traduzir_erro_banco` (via `erro_db`), então os testes exercitam a tradução
real, e as mensagens/códigos imitam os do Postgres.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from app.errors import ErroInterno
from app.repositorios.armazenamento import MetadadosObjeto, UploadAssinado
from app.repositorios.armazenamento_avatares import ObjetoJaExiste
from tests.fakes_admin import agora_iso, erro_db

EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class FotoPerfilMemoria:
    """Mixin do RepoMemoria."""

    def _init_foto(self) -> None:
        self.fotos: list[dict[str, Any]] = []  # linhas de perfil_fotos
        self.chamadas_trocar: list[dict[str, Any]] = []
        self.chamadas_trocar_por: list[dict[str, Any]] = []
        self.chamadas_remover: list[dict[str, str]] = []
        self.falha_trocar: Exception | None = None  # levantada ANTES de gravar
        self.perder_resposta_trocar = False  # grava e levanta ErroInterno (resposta perdida)
        self.falha_remover_foto: Exception | None = None
        self.falha_leitura_fotos: Exception | None = None

    # -- RPCs ----------------------------------------------------------------------------
    def trocar_foto_perfil(
        self, *, usuario_id: str, foto_id: str, mime: str, tamanho: int
    ) -> str | None:
        self.chamadas_trocar.append(
            {"usuario_id": usuario_id, "foto_id": foto_id, "mime": mime, "tamanho": tamanho}
        )
        if self.falha_trocar is not None:
            raise self.falha_trocar
        with self._lock:
            if mime not in EXT:
                raise erro_db("23514", "Violação da regra ck_perfil_fotos_mime: tipo.")
            if not 1 <= tamanho <= 1_048_576:
                raise erro_db("23514", "Violação da regra ck_perfil_fotos_tamanho: tamanho.")
            p = self.perfis.get(usuario_id)
            if p is None or not p.ativo:
                raise erro_db("AG017", "Usuário inexistente ou inativo.")
            if any(f["id"] == foto_id for f in self.fotos):
                raise erro_db("23505", 'duplicate key value violates "perfil_fotos_pkey"')
            anterior = self._baixar_ativa(usuario_id, por=None)
            self.fotos.append(
                {
                    "id": foto_id,
                    "usuario_id": usuario_id,
                    "mime": mime,
                    "tamanho_bytes": tamanho,
                    "caminho": f"{usuario_id}/{foto_id}.{EXT[mime]}",
                    "removida_em": None,
                    "removida_por": None,
                }
            )
            self._audit(
                "perfil_fotos",
                foto_id,
                "foto_perfil_alterada",
                usuario_id,
                detalhes={"mime": mime, "tamanho_bytes": tamanho},
            )
        if self.perder_resposta_trocar:
            raise ErroInterno("Serviço de dados indisponível. Tente novamente.")
        return anterior

    def trocar_foto_perfil_por(
        self, *, usuario_id: str, ator_id: str, foto_id: str, mime: str, tamanho: int
    ) -> str | None:
        self.chamadas_trocar_por.append(
            {
                "usuario_id": usuario_id,
                "ator_id": ator_id,
                "foto_id": foto_id,
                "mime": mime,
                "tamanho": tamanho,
            }
        )
        with self._lock:
            a = self.perfis.get(ator_id)
            if a is None or a.role != "admin" or not a.ativo:
                raise erro_db("AG022", "Troca da foto de perfil não autorizada.")
        return self.trocar_foto_perfil(
            usuario_id=usuario_id, foto_id=foto_id, mime=mime, tamanho=tamanho
        )

    def _baixar_ativa(self, usuario_id: str, *, por: str | None) -> str | None:
        for f in self.fotos:
            if f["usuario_id"] == usuario_id and f["removida_em"] is None:
                f["removida_em"] = agora_iso()
                f["removida_por"] = por
                ator = por or usuario_id
                self._audit(
                    "perfil_fotos",
                    f["id"],
                    "foto_perfil_removida",
                    ator,
                    detalhes={
                        "mime": f["mime"],
                        "tamanho_bytes": f["tamanho_bytes"],
                        "por_admin": ator != usuario_id,
                    },
                )
                return str(f["caminho"])
        return None

    def remover_foto_perfil_por(self, *, usuario_id: str, ator_id: str) -> str | None:
        self.chamadas_remover.append({"usuario_id": usuario_id, "ator_id": ator_id})
        if self.falha_remover_foto is not None:
            raise self.falha_remover_foto
        with self._lock:
            if ator_id != usuario_id:
                a = self.perfis.get(ator_id)
                if a is None or a.role != "admin" or not a.ativo:
                    raise erro_db("AG022", "Remoção da foto de perfil não autorizada.")
            if usuario_id not in self.perfis:
                raise erro_db("AG017", "Usuário inexistente ou inativo.")
            return self._baixar_ativa(usuario_id, por=ator_id if ator_id != usuario_id else None)

    # -- leitura (service_role tem SELECT em perfil_fotos) ------------------------------------
    def fotos_ativas(self, usuario_ids: list[str]) -> dict[str, str]:
        if self.falha_leitura_fotos is not None:
            raise self.falha_leitura_fotos
        return {
            f["usuario_id"]: f["caminho"]
            for f in self.fotos
            if f["removida_em"] is None and f["usuario_id"] in usuario_ids
        }

    def ids_ativos(self, usuario_ids: list[str]) -> set[str]:
        return {i for i in usuario_ids if i in self.perfis and self.perfis[i].ativo}

    def foto_ativa_de(self, usuario_id: str) -> dict[str, Any] | None:
        return next(
            (f for f in self.fotos if f["usuario_id"] == usuario_id and f["removida_em"] is None),
            None,
        )


class ArmazenamentoAvataresMemoria:
    """Bucket `avatares` fake. `metadados` reflete os bytes reais, a menos que se force o
    contrário com `meta_forcada` (simula MIME/tamanho declarados diferentes do conteúdo)."""

    def __init__(self) -> None:
        self.objetos: dict[str, tuple[str, bytes]] = {}
        self.meta_forcada: dict[str, MetadadosObjeto] = {}
        self.removidos: list[str] = []
        self.gravados: list[str] = []
        self.urls_upload: list[str] = []
        self.lotes_leitura: list[list[str]] = []
        self.indisponivel = False
        self.falha_remover = False
        self.falha_assinar = False
        self._trava = threading.Lock()  # o upsert=false do Storage é atômico

    def enviar(self, caminho: str, mime: str, dados: bytes) -> None:
        """Simula o navegador enviando o arquivo pela URL assinada."""
        self.objetos[caminho] = (mime, dados)

    def _checar(self) -> None:
        if self.indisponivel:
            raise ErroInterno("Armazenamento de fotos indisponível. Tente novamente.")

    def url_upload(self, caminho: str) -> UploadAssinado:
        self._checar()
        self.urls_upload.append(caminho)
        n = len(self.urls_upload)
        return {"url": f"https://storage.exemplo/upload?token=tok-{n}", "token": f"tok-{n}"}

    def metadados(self, caminho: str) -> MetadadosObjeto | None:
        self._checar()
        if caminho in self.meta_forcada:
            return self.meta_forcada[caminho]
        if caminho not in self.objetos:
            return None
        mime, dados = self.objetos[caminho]
        return {"mime": mime, "tamanho": len(dados)}

    def ler_tudo(self, caminho: str, maximo: int) -> bytes | None:
        self._checar()
        if caminho not in self.objetos:
            return None
        return self.objetos[caminho][1][: maximo + 1]

    def gravar(self, caminho: str, dados: bytes, mime: str) -> None:
        self._checar()
        with self._trava:
            if caminho in self.objetos:
                raise ObjetoJaExiste
            self.gravados.append(caminho)
            self.objetos[caminho] = (mime, dados)

    def url_leitura(self, caminho: str, expira_em: int) -> str:
        self._checar()
        if self.falha_assinar:
            raise ErroInterno("Armazenamento de fotos indisponível. Tente novamente.")
        return f"https://storage.exemplo/leitura/{caminho}?exp={expira_em}"

    def urls_leitura(self, caminhos: list[str], expira_em: int) -> dict[str, str]:
        self._checar()
        if self.falha_assinar:
            raise ErroInterno("Armazenamento de fotos indisponível. Tente novamente.")
        self.lotes_leitura.append(list(caminhos))
        return {c: f"https://storage.exemplo/leitura/{c}?exp={expira_em}" for c in caminhos}

    def remover(self, caminho: str) -> None:
        if self.falha_remover:
            raise RuntimeError("falha segredo-do-storage")
        self._checar()
        self.removidos.append(caminho)
        self.objetos.pop(caminho, None)


def novo_id() -> str:
    return str(uuid.uuid4())
