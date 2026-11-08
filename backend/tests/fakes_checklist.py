"""Fakes do recurso Checklist: parte do repositório (emula as migrations 19 a 22) e Storage.

Os erros passam por `traduzir_erro_banco`, então os testes exercitam a tradução real.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.errors import ErroInterno
from app.repositorios.armazenamento import MetadadosObjeto, UploadAssinado
from tests.fakes_admin import erro_db

MAGIC = {
    "image/jpeg": bytes([0xFF, 0xD8, 0xFF, 0xE0]) + bytes(12),
    "image/png": bytes([0x89]) + b"PNG" + bytes([0x0D, 0x0A, 0x1A, 0x0A]) + bytes(8),
    "image/webp": b"RIFF" + bytes(4) + b"WEBPVP8 ",
}
EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class ChecklistMemoria:
    """Mixin do RepoMemoria."""

    def _init_checklist(self) -> None:
        self.checklists: dict[tuple[str, str], dict[str, Any]] = {}
        self.chamadas_rpc: list[dict[str, Any]] = []
        self.auditoria_checklist: list[dict[str, Any]] = []

    def momentos_checklist(self, requisicao_ids: set[str]) -> dict[str, set[str]]:
        saida: dict[str, set[str]] = {}
        for rid, momento in self.checklists:
            if rid in requisicao_ids:
                saida.setdefault(rid, set()).add(momento)
        return saida

    def obter_checklists(self, requisicao_id: str) -> list[dict[str, Any]]:
        return [
            {**dict(c), "fotos": [dict(f) for f in c["fotos"]]}
            for (rid, _), c in self.checklists.items()
            if rid == requisicao_id
        ]

    def registrar_checklist(
        self,
        *,
        requisicao_id: str,
        momento: str,
        combustivel: str,
        avarias: bool,
        descricao_avarias: str | None,
        registrado_por: str,
        fotos: list[dict[str, Any]],
    ) -> str:
        """Emula a RPC registrar_checklist + triggers/constraints: tudo ou nada, sob lock."""
        self.chamadas_rpc.append({"requisicao_id": requisicao_id, "momento": momento})
        with self._lock:
            r = self.requisicoes.get(requisicao_id)
            if r is None:
                raise erro_db("23503", "violates foreign key constraint")
            if (requisicao_id, momento) in self.checklists:
                raise erro_db("23505", 'unique constraint "uq_checklists_requisicao_momento"')
            esperado = {"saida": "autorizada", "chegada": "em_uso"}[momento]
            if r["status"] != esperado:
                raise erro_db("AG014", "Checklist fora da janela.")
            p = self.perfis.get(registrado_por)
            # Contrato esperado da migration do Motorista: além da equipe, o motorista ATIVO
            # atribuído à viagem, e só com `motorista_registra` ligado.
            motorista_da_viagem = (
                p is not None
                and p.role == "motorista"
                and self.config.motorista_registra
                and r.get("motorista_id") == p.id
            )
            if (
                p is None
                or not p.ativo
                or not (p.role in {"autorizador", "admin"} or motorista_da_viagem)
            ):
                raise erro_db("AG002", "Apenas autorizador ou admin ativo registra checklist.")
            if avarias != (descricao_avarias is not None):
                raise erro_db("23514", 'check constraint "ck_checklists_avarias_descricao"')
            if len(fotos) > 6:
                raise erro_db("23514", 'check constraint "ck_checklist_fotos_posicao"')
            if len({f["id"] for f in fotos}) != len(fotos):
                raise erro_db("23505", 'unique constraint "checklist_fotos_pkey"')
            linhas = []
            for pos, f in enumerate(fotos, start=1):
                if f["mime_type"] not in EXT:
                    raise erro_db("23514", 'check constraint "ck_checklist_fotos_mime"')
                if not 1 <= f["tamanho_bytes"] <= 5_242_880:
                    raise erro_db("23514", 'check constraint "ck_checklist_fotos_tamanho"')
                linhas.append(
                    {
                        "id": f["id"],
                        "posicao": pos,
                        "mime_type": f["mime_type"],
                        "tamanho_bytes": f["tamanho_bytes"],
                        "caminho": f"{requisicao_id}/{momento}/{f['id']}.{EXT[f['mime_type']]}",
                    }
                )
            cid = str(uuid.uuid4())
            self.checklists[(requisicao_id, momento)] = {
                "id": cid,
                "requisicao_id": requisicao_id,
                "momento": momento,
                "combustivel": combustivel,
                "avarias": avarias,
                "descricao_avarias": descricao_avarias,
                "registrado_por": registrado_por,
                "registrado_em": datetime.now(UTC).isoformat(),
                "fotos": linhas,
            }
            # Auditoria da migration 20: sem descricao, combustivel nem caminhos.
            self.auditoria_checklist.append(
                {
                    "tabela": "checklists",
                    "acao": "checklist_registrado",
                    "detalhes": {
                        "requisicao_id": requisicao_id,
                        "momento": momento,
                        "avarias": avarias,
                        "fotos": len(linhas),
                    },
                }
            )
            return cid

    def _triggers_checklist(self, r: dict[str, Any], campos: dict[str, Any]) -> None:
        """Migration 22: ligado, em_uso exige checklist de saída e concluida, o de chegada."""
        if not self.config.checklist or campos["status"] not in {"em_uso", "concluida"}:
            return
        momento = "saida" if campos["status"] == "em_uso" else "chegada"
        if (r["id"], momento) not in self.checklists:
            rotulo = "saída" if momento == "saida" else "chegada"
            raise erro_db("AG013", f"Checklist de {rotulo} é obrigatório.")


class ArmazenamentoMemoria:
    """Storage fake: objetos por caminho com MIME e tamanho; registra URLs emitidas."""

    def __init__(self) -> None:
        self.objetos: dict[str, MetadadosObjeto] = {}
        self.inicios: dict[str, bytes] = {}  # primeiros bytes; padrão = assinatura válida
        self.removidos: list[str] = []
        self.urls_upload: list[str] = []  # caminhos para os quais houve URL de upload
        self.urls_leitura: list[tuple[str, int]] = []
        self.indisponivel = False

    def enviar(self, caminho: str, mime: str, tamanho: int, inicio: bytes | None = None) -> None:
        """Simula o navegador enviando o arquivo pela URL assinada."""
        self.objetos[caminho] = {"mime": mime, "tamanho": tamanho}
        self.inicios[caminho] = MAGIC[mime] if inicio is None else inicio

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
        return self.objetos.get(caminho)

    def contar_objetos(self, pasta: str) -> int:
        self._checar()
        return sum(1 for c in self.objetos if c.startswith(pasta + "/"))

    def url_leitura(self, caminho: str, expira_em: int) -> str:
        self._checar()
        self.urls_leitura.append((caminho, expira_em))
        return f"https://storage.exemplo/leitura/{caminho}?exp={expira_em}"

    def ler_inicio(self, caminho: str, n: int) -> bytes | None:
        self._checar()
        return self.inicios.get(caminho, b"")[:n] if caminho in self.objetos else None

    def remover(self, caminho: str) -> None:
        self._checar()
        self.removidos.append(caminho)
        self.objetos.pop(caminho, None)
