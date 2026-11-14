"""Fake de exportar/importar dados: emula as RPCs `exportar_dados` e `importar_dados`.

Contrato do banco: prévia não grava; confirmar mescla só sem erro de linha (tudo ou nada); nunca
apaga; as duas RPCs auditam na própria transação (só contagens); falha de auditoria falha tudo.
"""

from __future__ import annotations

from typing import Any

TABELAS_DADOS = ("veiculos", "requisicoes", "localizacoes_viagem", "checklists", "configuracoes")


class DadosMemoria:
    def _init_dados(self) -> None:
        self.dados: dict[str, dict[str, dict[str, Any]]] = {t: {} for t in TABELAS_DADOS}
        self.chamadas_importar: list[dict[str, Any]] = []
        self.chamadas_exportar: list[str] = []
        self.payloads_importar: list[dict[str, Any]] = []
        self.erros_importar: list[dict[str, Any]] = []  # simula erro de linha do banco

    def exportar_dados(self, *, admin_id: str) -> dict[str, Any]:
        self.chamadas_exportar.append(admin_id)
        self._exigir_admin_ativo(admin_id)
        tabelas: dict[str, Any] = {
            t: [dict(r) for r in linhas.values()]
            for t, linhas in self.dados.items()
            if t != "configuracoes"
        }
        cfg = next(iter(self.dados["configuracoes"].values()), {})
        tabelas["configuracoes"] = dict(cfg)
        contagens = {t: len(v) for t, v in tabelas.items() if t != "configuracoes"}
        contagens["configuracoes"] = 1
        if self.falha_auditoria is not None:  # mesma transação: sem trilha, nada sai
            raise self.falha_auditoria
        self._audit(
            "configuracoes",
            None,
            "dados_exportados",
            admin_id,
            detalhes={"versao": 1, "contagens": contagens},
        )
        return {"tabelas": tabelas, "contagens": contagens}

    def importar_dados(
        self, payload: dict[str, Any], *, admin_id: str, confirmar: bool
    ) -> dict[str, Any]:
        self._exigir_admin_ativo(admin_id)
        self.chamadas_importar.append({"admin_id": admin_id, "confirmar": confirmar})
        self.payloads_importar.append(payload)
        tabelas = payload["tabelas"]
        resumo: dict[str, dict[str, int]] = {}
        novas: dict[str, list[dict[str, Any]]] = {}
        for t in TABELAS_DADOS:
            bruto = tabelas.get(t) or []
            linhas = [bruto] if isinstance(bruto, dict) else bruto
            novos = existentes = 0
            novas[t] = []
            for linha in linhas:
                if linha.get("id", "unica") in self.dados[t]:
                    existentes += 1
                else:
                    novos += 1
                    novas[t].append(linha)
            resumo[t] = {"novos": novos, "existentes": existentes, "conflitos": 0}
        # flags que mudariam (a RPC deixa notificacao_email_criticos fora da importação)
        atual = next(iter(self.dados["configuracoes"].values()), {})
        bruto_cfg = tabelas.get("configuracoes") or {}
        bruto_cfg = bruto_cfg[0] if isinstance(bruto_cfg, list) and bruto_cfg else bruto_cfg
        alteradas = [
            {"chave": k, "de": bool(atual.get(k, False)), "para": v}
            for k, v in (bruto_cfg if isinstance(bruto_cfg, dict) else {}).items()
            if k != "notificacao_email_criticos"
            and isinstance(v, bool)
            and bool(atual.get(k, False)) != v
        ]
        erros = list(self.erros_importar)
        aplicado = confirmar and not erros
        if aplicado:
            for t in TABELAS_DADOS:
                for linha in novas[t]:
                    self.dados[t][linha.get("id", "unica")] = dict(linha)
            if self.falha_auditoria is not None:
                raise self.falha_auditoria
            self._audit(
                "configuracoes",
                None,
                "dados_importados",
                admin_id,
                detalhes={"versao": 1, "resumo": resumo},
            )
        return {
            "resumo": resumo,
            "erros": erros,
            "erros_total": len(erros),
            "aplicado": aplicado,
            "configuracoes_alteradas": alteradas,
        }
