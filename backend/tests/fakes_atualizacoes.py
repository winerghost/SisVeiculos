"""Fake de `atualizacoes_versao` (migration 20261202120000).

O banco usa ids de auditoria e carimbos; aqui o marcador é o ESTADO visível ao perfil (mesma
visibilidade da função SQL). Para os testes da API basta a propriedade: muda se e só se algo
relevante para aquele perfil mudou. A relevância por auditoria (ex.: `localizacao_lida` não
conta) é coberta pelos testes SQL em supabase/tests/test_atualizacoes_versao.py.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.modelos import Configuracoes, Perfil, Requisicao

STAFF = frozenset({"autorizador", "admin"})


def _h(valor: Any) -> str:
    return hashlib.sha256(repr(valor).encode()).hexdigest()


class AtualizacoesMemoria:
    """Mixin do RepoMemoria."""

    perfis: dict[str, Perfil]
    veiculos: dict[str, dict[str, Any]]
    requisicoes: dict[str, Requisicao]
    config: Configuracoes
    arquivadas: dict[tuple[str, str], str]
    notificacoes: list[dict[str, Any]]
    checklists: dict[tuple[str, str], dict[str, Any]]
    localizacoes: list[dict[str, Any]]
    convites: dict[str, dict[str, Any]]
    telefones: dict[str, str | None]
    fotos: list[dict[str, Any]]

    def _init_atualizacoes(self) -> None:
        self.chamadas_versoes: list[str] = []

    @staticmethod
    def _visivel_versao(p: Perfil, r: Requisicao) -> bool:
        if p.role in STAFF or r["usuario_id"] == p.id:
            return True
        if p.role == "motorista":
            return r.get("motorista_id") == p.id
        if p.role == "vigilante":
            return r["status"] in ("autorizada", "em_uso") or p.id in (
                r.get("saida_registrada_por"),
                r.get("chegada_registrada_por"),
            )
        return False

    def versoes_atualizacao(self, usuario_id: str) -> dict[str, Any]:
        self.chamadas_versoes.append(usuario_id)
        p = self.perfis.get(usuario_id)
        if p is None or not p.ativo:
            return {
                "requisicoes": "0",
                "notificacoes": "0",
                "veiculos": "0",
                "configuracoes": "0",
                "usuarios": None,
                "nao_lidas": 0,
            }
        visiveis = sorted(
            (rid, sorted(r.items()))
            for rid, r in self.requisicoes.items()
            if self._visivel_versao(p, r)
        )
        ids = {rid for rid, _ in visiveis}
        req = (
            visiveis,
            sorted(str(k) for k in self.checklists if k[0] in ids),
            len([x for x in self.localizacoes if x.get("requisicao_id") in ids]),
            sorted(rid for (u, rid) in self.arquivadas if u == usuario_id),
        )
        minhas = [n for n in self.notificacoes if n["usuario_id"] == usuario_id]
        nao_lidas = sum(1 for n in minhas if n.get("lida_em") is None)
        notif = sorted((n["id"], n.get("lida_em")) for n in minhas)
        usuarios = None
        if p.role == "admin":
            usuarios = _h(
                (
                    sorted((k, repr(v)) for k, v in self.perfis.items()),
                    sorted(self.telefones.items()),
                    sorted(
                        (k, sorted((c, repr(x)) for c, x in v.items()))
                        for k, v in self.convites.items()
                    ),
                    [sorted((c, repr(x)) for c, x in f.items()) for f in self.fotos],
                )
            )
        return {
            "requisicoes": _h(req),
            "notificacoes": _h(notif),
            # Cadastro + ocupação (migration 20261203120000): abrir/fechar requisição alheia muda.
            "veiculos": _h(
                (
                    sorted(
                        (k, sorted((c, repr(x)) for c, x in v.items()))
                        for k, v in self.veiculos.items()
                    ),
                    sorted(
                        {
                            r["veiculo_id"]
                            for r in self.requisicoes.values()
                            if r["status"] in ("solicitada", "autorizada", "em_uso")
                        }
                    ),
                )
            ),
            "configuracoes": _h(repr(self.config)),
            "usuarios": usuarios,
            "nao_lidas": nao_lidas,
        }
