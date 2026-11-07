"""Concorrência contra o Supabase REAL (índice parcial ux_requisicoes_veiculo_aberta).

Ignorado por padrão. Rode SOMENTE contra um projeto de teste/homologação, com o .env
carregado no ambiente (nunca em produção):

    RUN_INTEGRATION=1 \\
    INT_SOLICITANTE_IDS=<uuid1>,<uuid2> INT_AUTORIZADOR_ID=<uuid> INT_VEICULO_ID=<uuid> \\
    python -m pytest -m integracao tests/test_integracao_concorrencia.py

Pré-requisitos: perfis ativos informados; veículo ativo SEM requisição aberta. O teste cria
requisições e cancela a que persistir ao final (o histórico permanece: não há DELETE).
"""

from __future__ import annotations

import os
import threading

import pytest

from app.errors import VeiculoOcupado
from app.modelos import Perfil
from app.services.requisicoes import ServicoRequisicoes

pytestmark = [
    pytest.mark.integracao,
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="integração desligada"),
]


def test_duas_requisicoes_simultaneas_so_uma_persiste() -> None:
    from app.repositorios.supabase_repo import SupabaseRepositorio

    repo = SupabaseRepositorio(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    ids = os.environ["INT_SOLICITANTE_IDS"].split(",")[:2]
    veiculo = os.environ["INT_VEICULO_ID"]
    autorizador = repo.obter_perfil(os.environ["INT_AUTORIZADOR_ID"])
    solicitantes: list[Perfil] = [p for i in ids if (p := repo.obter_perfil(i.strip())) is not None]
    assert len(solicitantes) == 2 and autorizador is not None

    svc = ServicoRequisicoes(repo)
    barreira = threading.Barrier(2)
    resultados: list[object] = []

    def tentar(perfil: Perfil) -> None:
        barreira.wait()
        try:
            resultados.append(
                svc.criar(
                    perfil,
                    {
                        "nome_solicitante": "Teste de concorrência",
                        "unidade": "PU",
                        "hora_saida": "2026-12-01T08:00:00-03:00",
                        "trajeto": "Teste automatizado",
                        "veiculo_id": veiculo,
                    },
                )
            )
        except VeiculoOcupado as e:
            resultados.append(e)

    ts = [threading.Thread(target=tentar, args=(p,)) for p in solicitantes]
    [t.start() for t in ts]
    [t.join() for t in ts]

    criadas = [r for r in resultados if isinstance(r, dict)]
    try:
        assert len(criadas) == 1
        assert sum(isinstance(r, VeiculoOcupado) for r in resultados) == 1
    finally:
        for r in criadas:  # libera o veículo
            svc.executar("cancelar", autorizador, r["id"])
