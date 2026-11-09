"""Tradução de AG003/AG002 da exclusão de saudação (sem vazar texto do banco)."""

from __future__ import annotations

from postgrest.exceptions import APIError

from app.errors import Conflito, ErroInterno, SemPermissao, TransicaoInvalida
from app.repositorios.supabase_repo import traduzir_erro_banco

SEGREDO = "fulano@agu.gov.br 123.456.789-00"


def tr(codigo: str, texto: str):  # type: ignore[no-untyped-def]
    return traduzir_erro_banco(APIError({"message": texto, "code": codigo, "details": SEGREDO}))


def test_ag003_saudacao_vira_transicao_invalida() -> None:
    e = tr("AG003", "Só é possível excluir saudação rejeitada (status atual: aprovada).")
    assert isinstance(e, TransicaoInvalida)
    assert "aprovada" not in e.mensagem and SEGREDO not in e.mensagem


def test_ag003_checklist_continua_conflito() -> None:
    e = tr("AG003", "checklists é imutável")
    assert isinstance(e, Conflito) and e.codigo == "checklist_imutavel"


def test_ag003_desconhecido_segue_fallback() -> None:
    e = tr("AG003", "TRUNCATE bloqueado em auditoria")
    assert isinstance(e, ErroInterno)


def test_ag002_exclusao_vira_sem_permissao() -> None:
    e = tr("AG002", "Exclusão de saudação exige o responsável (use public.excluir_saudacao).")
    assert isinstance(e, SemPermissao)
