"""Testes da migration 20261123120000 (recurso Dados: exportar_dados e importar_dados).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py): os testes gravam e nao apagam (sem DELETE fisico,
auditoria append-only). Cada teste usa ids/placas unicos.

    pytest supabase/tests/test_dados_exportar_importar.py -m db_local -p no:xdist
"""

from __future__ import annotations

import functools
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(
        not h.DSN, reason="banco de teste nao liberado (ver aviso de dbhelpers)"
    ),
]

TABELAS = (
    "veiculos",
    "requisicoes",
    "checklists",
    "localizacoes_viagem",
    "configuracoes",
)
SIG_IMPORTAR = "public.importar_dados(jsonb, uuid, boolean)"
SIG_EXPORTAR = "public.exportar_dados(uuid)"
AGORA = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def importar(payload: dict, admin: uuid.UUID, confirmar: bool) -> dict:
    with h.conectar() as c:
        row = c.execute(
            "select public.importar_dados(%s::jsonb, %s, %s)",
            (json.dumps(payload), admin, confirmar),
        ).fetchone()
    assert row is not None
    return row[0]


def exportar(admin: uuid.UUID) -> dict:
    with h.conectar() as c:
        row = c.execute("select public.exportar_dados(%s)", (admin,)).fetchone()
    assert row is not None
    return row[0]


def arquivo(**tabelas) -> dict:
    return {"versao": 1, "tabelas": tabelas}


def contar(sql: str, *params) -> int:
    with h.conectar() as c:
        row = c.execute(sql, params).fetchone()
    assert row is not None
    return int(row[0])


def veiculo_arq(placa: str | None = None) -> dict:
    vid = uuid.uuid4()
    return {
        "id": str(vid),
        "descricao": f"import-{vid.hex[:8]}",
        "placa": placa,
        "ativo": True,
        "codigo_publico": vid.hex,
        "criado_em": AGORA.isoformat(),
        "atualizado_em": AGORA.isoformat(),
    }


def placa_unica() -> str:
    n = uuid.uuid4().int
    letras = "".join(chr(65 + (n >> (i * 5)) % 26) for i in range(3))
    return f"{letras}{n % 10}{chr(65 + (n >> 20) % 26)}{(n >> 24) % 10}{(n >> 28) % 10}"


@functools.cache
def autorizador() -> uuid.UUID:
    """Autorizador ativo compartilhado: autorizado_por e saida/chegada_registrada_por exigem esse papel."""
    return h.criar_usuario("aut-dados", "autorizador")


def requisicao_arq(
    usuario: uuid.UUID, veiculo_id: str, _admin: uuid.UUID, status: str = "concluida"
) -> dict:
    admin = autorizador()
    rid = uuid.uuid4()
    r = {
        "id": str(rid),
        "usuario_id": str(usuario),
        "nome_solicitante": "Fulano Importado",
        "unidade": "UEA",
        "hora_saida": (AGORA + timedelta(hours=1)).isoformat(),
        "trajeto": "A-B",
        "observacao": None,
        "veiculo_id": veiculo_id,
        "status": status,
        "autorizado_por": str(admin),
        "autorizado_em": (AGORA + timedelta(minutes=1)).isoformat(),
        "saida_real_em": None,
        "chegada_real_em": None,
        "saida_registrada_por": None,
        "chegada_registrada_por": None,
        "cancelada_por": None,
        "cancelada_em": None,
        "km_saida": None,
        "km_chegada": None,
        "motorista_id": None,
        "criado_em": AGORA.isoformat(),
        "atualizado_em": AGORA.isoformat(),
    }
    if status == "concluida":
        r.update(
            saida_real_em=(AGORA + timedelta(hours=1)).isoformat(),
            chegada_real_em=(AGORA + timedelta(hours=3)).isoformat(),
            saida_registrada_por=str(admin),
            chegada_registrada_por=str(admin),
            km_saida=100,
            km_chegada=150,
        )
    return r


def checklist_arq(requisicao_id: str, admin: uuid.UUID, momento: str = "saida") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "requisicao_id": requisicao_id,
        "momento": momento,
        "combustivel": "cheio",
        "avarias": False,
        "descricao_avarias": None,
        "registrado_por": str(admin),
        "registrado_em": AGORA.isoformat(),
    }


def estado_gatilhos() -> list[tuple[str, str, str]]:
    with h.conectar() as c:
        return [
            (r[0], r[1], r[2])
            for r in c.execute("""select c.relname, t.tgname, t.tgenabled::text
                     from pg_trigger t join pg_class c on c.oid = t.tgrelid
                     join pg_namespace n on n.oid = c.relnamespace
                    where n.nspname = 'public' and not t.tgisinternal
                      and c.relname in ('requisicoes', 'checklists', 'localizacoes_viagem')
                    order by 1, 2""").fetchall()
        ]


@pytest.fixture()
def admin() -> uuid.UUID:
    return h.criar_usuario("adm-dados", "admin")


@pytest.fixture()
def solicitante() -> uuid.UUID:
    return h.criar_usuario("sol-dados", "solicitante")


# ---------------------------------------------------------------------------
# Grants e acesso
# ---------------------------------------------------------------------------
def test_execute_so_service_role() -> None:
    with h.conectar() as c:
        for sig in (SIG_IMPORTAR, SIG_EXPORTAR):
            for papel in ("anon", "authenticated", "public"):
                assert not c.execute(
                    "select has_function_privilege(%s, %s::regprocedure, 'execute')",
                    (papel, sig),
                ).fetchone()[0], f"{papel} executa {sig}"
            assert c.execute(
                "select has_function_privilege('service_role', %s::regprocedure, 'execute')",
                (sig,),
            ).fetchone()[0]


def test_nao_admin_recusado(solicitante: uuid.UUID) -> None:
    for fn in (
        lambda: importar(arquivo(), solicitante, False),
        lambda: exportar(solicitante),
    ):
        with pytest.raises(psycopg.errors.Error) as e:
            fn()
        assert e.value.sqlstate == "AG002"


def test_admin_inativo_recusado() -> None:
    inativo = h.criar_usuario("adm-inativo", "solicitante", ativo=False)
    with pytest.raises(psycopg.errors.Error) as e:
        importar(arquivo(), inativo, False)
    assert e.value.sqlstate == "AG002"


@pytest.mark.parametrize(
    "payload",
    [
        {"versao": 1},
        {"versao": 2, "tabelas": {}},
        {"versao": 1, "tabelas": {"perfis": []}},
        {"versao": 1, "tabelas": {"veiculos": {}}},
        {"versao": 1, "tabelas": {"configuracoes": [{}, {}]}},
    ],
)
def test_estrutura_invalida_ag060(admin: uuid.UUID, payload: dict) -> None:
    with pytest.raises(psycopg.errors.Error) as e:
        importar(payload, admin, False)
    assert e.value.sqlstate == "AG060"


# ---------------------------------------------------------------------------
# Previa, confirmacao e idempotencia
# ---------------------------------------------------------------------------
def test_previa_nao_grava_e_confirmar_grava_idempotente(
    admin: uuid.UUID, solicitante: uuid.UUID
) -> None:
    v = veiculo_arq(placa_unica())
    r = requisicao_arq(solicitante, v["id"], admin)
    c1 = checklist_arq(r["id"], admin, "saida")
    c2 = checklist_arq(r["id"], admin, "chegada")
    loc = {
        "id": str(uuid.uuid4()),
        "requisicao_id": r["id"],
        "momento": "saida",
        "latitude": "-15.793889",
        "longitude": "-47.882778",
        "precisao_m": "12.50",
        "registrado_por": str(admin),
        "registrado_em": AGORA.isoformat(),
    }
    dados = arquivo(
        veiculos=[v], requisicoes=[r], checklists=[c1, c2], localizacoes_viagem=[loc]
    )
    gatilhos_antes = estado_gatilhos()
    auditorias_antes = contar(
        "select count(*) from public.auditoria where acao = 'dados_importados'"
    )

    previa = importar(dados, admin, False)
    assert previa["erros"] == [] and previa["aplicado"] is False
    assert previa["resumo"]["veiculos"] == {"novos": 1, "existentes": 0, "conflitos": 0}
    assert previa["resumo"]["requisicoes"]["novos"] == 1
    assert previa["resumo"]["checklists"]["novos"] == 2
    assert previa["resumo"]["localizacoes_viagem"]["novos"] == 1
    assert contar("select count(*) from public.veiculos where id = %s", v["id"]) == 0
    assert contar("select count(*) from public.requisicoes where id = %s", r["id"]) == 0
    assert estado_gatilhos() == gatilhos_antes
    assert (
        contar("select count(*) from public.auditoria where acao = 'dados_importados'")
        == auditorias_antes
    )

    ok = importar(dados, admin, True)
    assert ok["erros"] == [] and ok["aplicado"] is True
    assert ok["resumo"]["requisicoes"]["novos"] == 1
    assert (
        contar(
            "select count(*) from public.requisicoes where id = %s and status = 'concluida'",
            r["id"],
        )
        == 1
    )
    assert (
        contar("select km_chegada from public.requisicoes where id = %s", r["id"])
        == 150
    )
    assert (
        contar(
            "select count(*) from public.checklists where requisicao_id = %s", r["id"]
        )
        == 2
    )
    assert (
        contar(
            "select count(*) from public.localizacoes_viagem where requisicao_id = %s",
            r["id"],
        )
        == 1
    )
    assert estado_gatilhos() == gatilhos_antes  # gatilhos de volta ao estado original
    assert (
        contar("select count(*) from public.auditoria where acao = 'dados_importados'")
        == auditorias_antes + 1
    )

    de_novo = importar(dados, admin, True)  # idempotente: tudo "existente"
    assert de_novo["aplicado"] is True
    for t in ("veiculos", "requisicoes", "checklists", "localizacoes_viagem"):
        assert de_novo["resumo"][t]["novos"] == 0, t
    assert de_novo["resumo"]["requisicoes"]["existentes"] == 1
    assert contar("select count(*) from public.requisicoes where id = %s", r["id"]) == 1


def test_auditoria_so_contagens_sem_dado_pessoal(
    admin: uuid.UUID, solicitante: uuid.UUID
) -> None:
    v = veiculo_arq()
    r = requisicao_arq(solicitante, v["id"], admin)
    importar(arquivo(veiculos=[v], requisicoes=[r]), admin, True)
    with h.conectar() as c:
        row = c.execute(
            "select tabela, registro_id, ator_id, detalhes from public.auditoria "
            "where acao = 'dados_importados' order by id desc limit 1"
        ).fetchone()
    assert row is not None
    tabela, registro_id, ator, detalhes = row
    assert (tabela, registro_id, ator) == ("configuracoes", None, admin)
    assert set(detalhes) == {"versao", "resumo"}
    texto = json.dumps(detalhes)
    assert (
        "Fulano" not in texto and "A-B" not in texto and str(solicitante) not in texto
    )


def test_importacao_nao_gera_linhas_de_historico_falsas(
    admin: uuid.UUID, solicitante: uuid.UUID
) -> None:
    v = veiculo_arq()
    r = requisicao_arq(solicitante, v["id"], admin)
    importar(
        arquivo(
            veiculos=[v], requisicoes=[r], checklists=[checklist_arq(r["id"], admin)]
        ),
        admin,
        True,
    )
    # Só a linha única `criada` com origem importação (migration 20261124120000); nenhuma transição
    # de status falsa a partir do estado importado.
    assert (
        contar(
            "select count(*) from public.auditoria where tabela = 'requisicoes' and registro_id = %s",
            r["id"],
        )
        == 1
    )
    assert (
        contar(
            "select count(*) from public.notificacoes where requisicao_id = %s", r["id"]
        )
        == 0
    )


# ---------------------------------------------------------------------------
# Erros de linha: nada grava
# ---------------------------------------------------------------------------
def test_veiculo_ocupado_no_arquivo_nao_grava_nada(
    admin: uuid.UUID, solicitante: uuid.UUID
) -> None:
    v = veiculo_arq()
    a = requisicao_arq(solicitante, v["id"], admin, status="autorizada")
    b = requisicao_arq(solicitante, v["id"], admin, status="solicitada")
    b["autorizado_por"] = None
    b["autorizado_em"] = None
    dados = arquivo(veiculos=[v], requisicoes=[a, b])
    for confirmar in (False, True):
        res = importar(dados, admin, confirmar)
        assert res["aplicado"] is False
        assert [e["tabela"] for e in res["erros"]] == ["requisicoes"]
        assert res["erros"][0]["indice"] == 1
        assert "outra requisição aberta" in res["erros"][0]["mensagem"]
    assert contar("select count(*) from public.veiculos where id = %s", v["id"]) == 0
    assert (
        contar("select count(*) from public.requisicoes where veiculo_id = %s", v["id"])
        == 0
    )


def test_veiculo_ocupado_no_banco(admin: uuid.UUID, solicitante: uuid.UUID) -> None:
    v = veiculo_arq()
    base = requisicao_arq(solicitante, v["id"], admin, status="autorizada")
    assert (
        importar(arquivo(veiculos=[v], requisicoes=[base]), admin, True)["aplicado"]
        is True
    )
    outra = requisicao_arq(solicitante, v["id"], admin, status="autorizada")
    res = importar(arquivo(requisicoes=[outra]), admin, True)
    assert res["aplicado"] is False and res["erros_total"] == 1
    assert (
        contar("select count(*) from public.requisicoes where id = %s", outra["id"])
        == 0
    )


def test_fk_inexistente_e_check_viram_erro_de_linha(
    admin: uuid.UUID, solicitante: uuid.UUID
) -> None:
    v = veiculo_arq()
    sem_usuario = requisicao_arq(uuid.uuid4(), v["id"], admin)
    chegada_antes_saida = requisicao_arq(solicitante, v["id"], admin)
    chegada_antes_saida["chegada_real_em"] = AGORA.isoformat()
    status_ruim = requisicao_arq(solicitante, v["id"], admin)
    status_ruim["status"] = "zzstatus"
    res = importar(
        arquivo(
            veiculos=[v], requisicoes=[sem_usuario, chegada_antes_saida, status_ruim]
        ),
        admin,
        True,
    )
    assert res["aplicado"] is False and res["erros_total"] == 3
    mensagens = " | ".join(e["mensagem"] for e in res["erros"])
    assert "usuario_id deve ser um usuário existente" in mensagens
    assert "chegada_real_em deve ser posterior a saida_real_em" in mensagens
    assert "Valor com formato inválido" in mensagens
    # mensagens nunca carregam valores
    for valor in (str(solicitante), "Fulano", "zzstatus"):
        assert valor not in mensagens
    assert contar("select count(*) from public.veiculos where id = %s", v["id"]) == 0


def test_checklist_sem_requisicao_e_erro(admin: uuid.UUID) -> None:
    res = importar(
        arquivo(checklists=[checklist_arq(str(uuid.uuid4()), admin)]), admin, True
    )
    assert res["aplicado"] is False and res["erros"][0]["tabela"] == "checklists"


# ---------------------------------------------------------------------------
# Conflitos e mesclagem
# ---------------------------------------------------------------------------
def test_requisicao_existente_diferente_e_conflito_ignorado(
    admin: uuid.UUID, solicitante: uuid.UUID
) -> None:
    v = veiculo_arq()
    r = requisicao_arq(solicitante, v["id"], admin)
    importar(arquivo(veiculos=[v], requisicoes=[r]), admin, True)
    mudada = dict(r, trajeto="OUTRO TRAJETO")
    res = importar(arquivo(requisicoes=[mudada]), admin, True)
    assert res["aplicado"] is True
    assert res["resumo"]["requisicoes"] == {"novos": 0, "existentes": 0, "conflitos": 1}
    assert res["politica_conflito"]["requisicoes"] == "ignora"
    with h.conectar() as c:
        assert (
            c.execute(
                "select trajeto from public.requisicoes where id = %s", (r["id"],)
            ).fetchone()[0]
            == "A-B"
        )


def test_veiculo_casado_por_placa_remapeia_requisicoes(
    admin: uuid.UUID, solicitante: uuid.UUID
) -> None:
    placa = placa_unica()
    local = veiculo_arq(placa)
    importar(arquivo(veiculos=[local]), admin, True)
    do_arquivo = dict(
        veiculo_arq(placa), descricao=local["descricao"]
    )  # outro id, mesma placa
    r = requisicao_arq(solicitante, do_arquivo["id"], admin)
    res = importar(arquivo(veiculos=[do_arquivo], requisicoes=[r]), admin, True)
    assert res["aplicado"] is True
    assert res["resumo"]["veiculos"] == {"novos": 0, "existentes": 1, "conflitos": 0}
    assert contar("select count(*) from public.veiculos where placa = %s", placa) == 1
    assert (
        contar(
            "select count(*) from public.requisicoes where id = %s and veiculo_id = %s",
            r["id"],
            local["id"],
        )
        == 1
    )
    # reimportar o mesmo arquivo continua idempotente (requisicao ja existe apontando para o veiculo local)
    de_novo = importar(arquivo(veiculos=[do_arquivo], requisicoes=[r]), admin, True)
    assert de_novo["resumo"]["requisicoes"]["existentes"] == 1


def test_veiculo_existente_diferente_atualiza_e_audita(admin: uuid.UUID) -> None:
    v = veiculo_arq(placa_unica())
    importar(arquivo(veiculos=[v]), admin, True)
    novo = dict(v, descricao="Descricao nova " + v["id"][:6], ativo=False)
    res = importar(arquivo(veiculos=[novo]), admin, True)
    assert res["resumo"]["veiculos"]["conflitos"] == 1
    with h.conectar() as c:
        row = c.execute(
            "select descricao, ativo from public.veiculos where id = %s", (v["id"],)
        ).fetchone()
        assert row == (novo["descricao"], False)
        assert (
            c.execute(
                "select count(*) from public.auditoria where tabela = 'veiculos' and registro_id = %s "
                "and acao = 'veiculo_alterado' and ator_id = %s",
                (v["id"], admin),
            ).fetchone()[0]
            == 1
        )


def test_placa_de_outro_veiculo_e_erro_de_unicidade(admin: uuid.UUID) -> None:
    a = veiculo_arq(placa_unica())
    importar(arquivo(veiculos=[a]), admin, True)
    # mesmo id com placa de outro veiculo existente -> unicidade (nao ha como casar por placa e por id ao mesmo tempo)
    b = veiculo_arq(placa_unica())
    importar(arquivo(veiculos=[b]), admin, True)
    res = importar(arquivo(veiculos=[dict(b, placa=a["placa"])]), admin, True)
    assert (
        res["aplicado"] is False and "Placa já pertence" in res["erros"][0]["mensagem"]
    )


# ---------------------------------------------------------------------------
# Configuracoes
# ---------------------------------------------------------------------------
def test_configuracoes_aplica_flags_e_ignora_seguranca(admin: uuid.UUID) -> None:
    with h.conectar() as c:
        antes = c.execute(
            "select feedback_tatil, mfa, passkeys, notificacao_push from public.configuracoes"
        ).fetchone()
    assert antes is not None
    alvo = not antes[0]
    try:
        res = importar(
            arquivo(
                configuracoes={
                    "feedback_tatil": alvo,
                    "mfa": True,
                    "passkeys": True,
                    "notificacao_push": True,
                    "campo_desconhecido": 1,
                }
            ),
            admin,
            True,
        )
        assert (
            res["aplicado"] is True and res["resumo"]["configuracoes"]["conflitos"] == 1
        )
        assert res["configuracoes_alteradas"] == [
            {"chave": "feedback_tatil", "de": antes[0], "para": alvo}
        ]
        with h.conectar() as c:
            depois = c.execute(
                "select feedback_tatil, mfa, passkeys, notificacao_push from public.configuracoes"
            ).fetchone()
        assert depois == (
            alvo,
            antes[1],
            antes[2],
            antes[3],
        )  # mfa/passkeys/push nunca mudam
        assert (
            contar(
                "select count(*) from public.auditoria where acao = 'configuracao_alterada' and ator_id = %s "
                "and detalhes ->> 'recurso' = 'feedback_tatil'",
                admin,
            )
            >= 1
        )
        igual = importar(
            arquivo(configuracoes=[{"feedback_tatil": alvo}]), admin, False
        )
        assert igual["resumo"]["configuracoes"] == {
            "novos": 0,
            "existentes": 1,
            "conflitos": 0,
        }
        assert igual["configuracoes_alteradas"] == []
    finally:
        importar(arquivo(configuracoes={"feedback_tatil": antes[0]}), admin, True)


def test_configuracoes_valor_nao_booleano_e_dependencia_sao_erro(
    admin: uuid.UUID,
) -> None:
    res = importar(arquivo(configuracoes={"feedback_tatil": "sim"}), admin, True)
    assert res["aplicado"] is False and res["erros"][0]["tabela"] == "configuracoes"
    # abastecimento exige quilometragem: o estado final viola a dependencia -> erro, nada muda
    with h.conectar() as c:
        km = c.execute("select quilometragem from public.configuracoes").fetchone()[0]
    if not km:
        res = importar(
            arquivo(configuracoes={"abastecimento_manutencao": True}), admin, True
        )
        assert res["aplicado"] is False and res["erros_total"] == 1


def test_previa_de_configuracao_nao_grava(admin: uuid.UUID) -> None:
    with h.conectar() as c:
        antes = c.execute(
            "select leitor_qr_veiculos from public.configuracoes"
        ).fetchone()[0]
    res = importar(
        arquivo(configuracoes={"leitor_qr_veiculos": not antes}), admin, False
    )
    assert res["resumo"]["configuracoes"]["conflitos"] == 1 and res["aplicado"] is False
    assert res["configuracoes_alteradas"] == [
        {"chave": "leitor_qr_veiculos", "de": antes, "para": not antes}
    ]
    with h.conectar() as c:
        assert (
            c.execute("select leitor_qr_veiculos from public.configuracoes").fetchone()[
                0
            ]
            == antes
        )


def test_notificacao_email_criticos_fica_fora_da_importacao(admin: uuid.UUID) -> None:
    with h.conectar() as c:
        antes = c.execute(
            "select notificacao_email_criticos from public.configuracoes"
        ).fetchone()[0]
    res = importar(
        arquivo(configuracoes={"notificacao_email_criticos": not antes}), admin, True
    )
    assert res["aplicado"] is True
    assert res["configuracoes_alteradas"] == []
    assert res["resumo"]["configuracoes"] == {
        "novos": 0,
        "existentes": 1,
        "conflitos": 0,
    }
    with h.conectar() as c:
        assert (
            c.execute(
                "select notificacao_email_criticos from public.configuracoes"
            ).fetchone()[0]
            == antes
        )


# ---------------------------------------------------------------------------
# Auditoria por requisicao e validacao de responsaveis/estado/km
# ---------------------------------------------------------------------------
AUD_REQ = "select count(*) from public.auditoria where tabela = 'requisicoes' and registro_id = %s"


def test_requisicao_nova_gera_auditoria_criada_com_origem(
    admin: uuid.UUID, solicitante: uuid.UUID
) -> None:
    v = veiculo_arq()
    r = requisicao_arq(solicitante, v["id"], admin)
    previa = importar(arquivo(veiculos=[v], requisicoes=[r]), admin, False)
    assert previa["erros"] == []
    assert contar(AUD_REQ, r["id"]) == 0
    importar(arquivo(veiculos=[v], requisicoes=[r]), admin, True)
    with h.conectar() as c:
        rows = c.execute(
            "select acao, ator_id, de, para, detalhes from public.auditoria "
            "where tabela = 'requisicoes' and registro_id = %s",
            (r["id"],),
        ).fetchall()
    assert rows == [("criada", admin, None, "concluida", {"origem": "importacao"})]
    importar(
        arquivo(veiculos=[v], requisicoes=[r]), admin, True
    )  # reimportar nao duplica
    assert contar(AUD_REQ, r["id"]) == 1


def _erro_unico(admin: uuid.UUID, mutar, status: str = "concluida") -> str:
    v = veiculo_arq()
    r = requisicao_arq(
        h.criar_usuario("sol-val", "solicitante"), v["id"], admin, status=status
    )
    mutar(r)
    res = importar(arquivo(veiculos=[v], requisicoes=[r]), admin, True)
    assert res["aplicado"] is False and res["erros_total"] == 1, res
    assert contar("select count(*) from public.requisicoes where id = %s", r["id"]) == 0
    return res["erros"][0]["mensagem"]


def test_autorizado_por_exige_autorizador_ou_admin_ativo(admin: uuid.UUID) -> None:
    sol = h.criar_usuario("sol-aut", "solicitante")
    inativo = h.criar_usuario("aut-inativo", "autorizador", ativo=False)
    for quem in (sol, inativo):
        msg = _erro_unico(admin, lambda r, q=quem: r.update(autorizado_por=str(q)))
        assert "autorizado_por deve ser" in msg and str(quem) not in msg


def test_autorizado_por_admin_ativo_vale(
    admin: uuid.UUID, solicitante: uuid.UUID
) -> None:
    v = veiculo_arq()
    r = requisicao_arq(solicitante, v["id"], admin)
    r["autorizado_por"] = str(admin)
    assert (
        importar(arquivo(veiculos=[v], requisicoes=[r]), admin, True)["aplicado"]
        is True
    )


def test_motorista_id_exige_motorista_ativo(
    admin: uuid.UUID, solicitante: uuid.UUID
) -> None:
    sol = h.criar_usuario("sol-mot", "solicitante")
    inativo = h.criar_usuario("mot-inativo", "motorista", ativo=False)
    for quem in (sol, inativo):
        msg = _erro_unico(admin, lambda r, q=quem: r.update(motorista_id=str(q)))
        assert "motorista_id deve ser um motorista ativo" in msg
    ok = h.criar_usuario("mot-ok", "motorista")
    v = veiculo_arq()
    r = requisicao_arq(solicitante, v["id"], admin)
    r["motorista_id"] = str(ok)
    assert (
        importar(arquivo(veiculos=[v], requisicoes=[r]), admin, True)["aplicado"]
        is True
    )


def test_registrada_por_exige_autorizador_ou_vigilante_ativo(
    admin: uuid.UUID, solicitante: uuid.UUID
) -> None:
    sol = h.criar_usuario("sol-reg", "solicitante")
    assert "saida_registrada_por deve ser" in _erro_unico(
        admin, lambda r: r.update(saida_registrada_por=str(sol))
    )
    assert "chegada_registrada_por deve ser" in _erro_unico(
        admin, lambda r: r.update(chegada_registrada_por=str(sol))
    )
    vig = h.criar_usuario("vig-ok", "vigilante")
    v = veiculo_arq()
    r = requisicao_arq(solicitante, v["id"], admin)
    r["saida_registrada_por"] = str(vig)
    r["chegada_registrada_por"] = str(vig)
    assert (
        importar(arquivo(veiculos=[v], requisicoes=[r]), admin, True)["aplicado"]
        is True
    )


def test_regras_de_km_e_estado_sao_da_rpc(admin: uuid.UUID) -> None:
    assert "km_chegada não pode ser menor" in _erro_unico(
        admin, lambda r: r.update(km_saida=200, km_chegada=100)
    )
    assert "km_saida não pode ser negativo" in _erro_unico(
        admin, lambda r: r.update(km_saida=-1)
    )
    assert "km_chegada exige km_saida" in _erro_unico(
        admin, lambda r: r.update(km_saida=None, km_chegada=10)
    )
    assert "km_saida exige saída real" in _erro_unico(
        admin, lambda r: r.update(km_saida=10), status="autorizada"
    )
    assert "Estado da requisição incoerente" in _erro_unico(
        admin,
        lambda r: r.update(
            chegada_real_em=None, chegada_registrada_por=None, km_chegada=None
        ),
    )


def test_cancelada_por_inexistente_e_erro_e_inativo_vale(admin: uuid.UUID) -> None:
    fantasma = uuid.uuid4()

    def cancelar(r: dict, quem: uuid.UUID) -> None:
        r.update(
            status="cancelada",
            cancelada_por=str(quem),
            cancelada_em=(AGORA + timedelta(minutes=5)).isoformat(),
            autorizado_por=None,
            autorizado_em=None,
            saida_real_em=None,
            chegada_real_em=None,
            saida_registrada_por=None,
            chegada_registrada_por=None,
            km_saida=None,
            km_chegada=None,
        )

    msg = _erro_unico(admin, lambda r: cancelar(r, fantasma))
    assert (
        "cancelada_por deve ser um usuário existente" in msg
        and str(fantasma) not in msg
    )
    # existente e INATIVO vale: so existencia (historico sobrevive a desativacao)
    inativo = h.criar_usuario("canc-inativo", "solicitante", ativo=False)
    v = veiculo_arq()
    r = requisicao_arq(h.criar_usuario("sol-canc", "solicitante"), v["id"], admin)
    cancelar(r, inativo)
    assert (
        importar(arquivo(veiculos=[v], requisicoes=[r]), admin, True)["aplicado"]
        is True
    )


def test_usuario_id_inativo_vale_so_existencia(admin: uuid.UUID) -> None:
    inativo = h.criar_usuario("sol-inativo", "solicitante", ativo=False)
    v = veiculo_arq()
    r = requisicao_arq(inativo, v["id"], admin)
    assert (
        importar(arquivo(veiculos=[v], requisicoes=[r]), admin, True)["aplicado"]
        is True
    )


def test_responsavel_inativo_segue_recusado_decisao_documentada(
    admin: uuid.UUID,
) -> None:
    """(c) autorizado_por/motorista/registradas_por exigem perfil ATIVO: nao relaxado."""
    inativo = h.criar_usuario("aut-desativado", "autorizador", ativo=False)
    for campo in ("autorizado_por", "saida_registrada_por", "chegada_registrada_por"):
        msg = _erro_unico(admin, lambda r, c=campo, q=inativo: r.update({c: str(q)}))
        assert campo in msg and str(inativo) not in msg


def _erro_filho(admin: uuid.UUID, tabela: str, filho: dict, v: dict, r: dict) -> str:
    res = importar(
        arquivo(veiculos=[v], requisicoes=[r], **{tabela: [filho]}),
        admin,
        True,
    )
    assert res["aplicado"] is False, res
    erros = [e for e in res["erros"] if e["tabela"] == tabela]
    assert len(erros) == 1, res
    return erros[0]["mensagem"]


def _req_com_veiculo(admin: uuid.UUID, status: str = "concluida") -> tuple[dict, dict]:
    v = veiculo_arq()
    return v, requisicao_arq(
        h.criar_usuario("sol-filho", "solicitante"), v["id"], admin, status
    )


def loc_arq(requisicao_id: str, autor: uuid.UUID, momento: str = "saida") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "requisicao_id": requisicao_id,
        "momento": momento,
        "latitude": "-15.793889",
        "longitude": "-47.882778",
        "precisao_m": "12.50",
        "registrado_por": str(autor),
        "registrado_em": AGORA.isoformat(),
    }


def test_checklist_valida_requisicao_autor_e_valores(admin: uuid.UUID) -> None:
    v, r = _req_com_veiculo(admin)

    def com(**mud) -> dict:
        return dict(checklist_arq(r["id"], admin), **mud)

    def erro(chk: dict) -> str:
        res = importar(
            arquivo(veiculos=[v], requisicoes=[r], checklists=[chk]), admin, True
        )
        assert res["aplicado"] is False and res["erros_total"] == 1, res
        assert res["erros"][0]["tabela"] == "checklists"
        return res["erros"][0]["mensagem"]

    fantasma = uuid.uuid4()
    assert "requisição inexistente" in erro(com(requisicao_id=str(uuid.uuid4())))
    msg = erro(com(registrado_por=str(fantasma)))
    assert "Autor do checklist" in msg and str(fantasma) not in msg
    assert "Momento do checklist inválido" in erro(com(momento="meio"))
    assert "Informe a descrição das avarias" in erro(
        com(avarias=True, descricao_avarias="  \n")
    )
    assert "sem avarias não pode ter descrição" in erro(com(descricao_avarias="x"))
    assert "excede 500" in erro(com(avarias=True, descricao_avarias="x" * 501))
    assert "Campo obrigatório do checklist ausente: registrado_por" in erro(
        com(registrado_por=None)
    )
    # autor existente porem INATIVO vale (historico); requisicao importada na mesma chamada vale
    inativo = h.criar_usuario("chk-inativo", "autorizador", ativo=False)
    ok = importar(
        arquivo(
            veiculos=[v], requisicoes=[r], checklists=[com(registrado_por=str(inativo))]
        ),
        admin,
        True,
    )
    assert ok["aplicado"] is True and ok["resumo"]["checklists"]["novos"] == 1


def test_checklist_momento_incoerente_com_a_requisicao(admin: uuid.UUID) -> None:
    v, r = _req_com_veiculo(admin, status="solicitada")
    r.update(autorizado_por=None, autorizado_em=None)
    msg = _erro_filho(admin, "checklists", checklist_arq(r["id"], admin, "saida"), v, r)
    assert "incoerente com o estado" in msg
    # chegada exige chegada real ou em_uso; autorizada nao tem
    v2, r2 = _req_com_veiculo(admin, status="autorizada")
    msg = _erro_filho(
        admin, "checklists", checklist_arq(r2["id"], admin, "chegada"), v2, r2
    )
    assert "incoerente com o estado" in msg
    # saida em requisicao autorizada vale (checklist registrado antes da saida)
    res = importar(
        arquivo(
            veiculos=[v2], requisicoes=[r2], checklists=[checklist_arq(r2["id"], admin)]
        ),
        admin,
        False,
    )
    assert res["erros"] == []


def test_localizacao_valida_requisicao_autor_e_faixas(admin: uuid.UUID) -> None:
    v, r = _req_com_veiculo(admin)

    def erro(loc: dict) -> str:
        res = importar(
            arquivo(veiculos=[v], requisicoes=[r], localizacoes_viagem=[loc]),
            admin,
            True,
        )
        assert res["aplicado"] is False and res["erros_total"] == 1, res
        assert res["erros"][0]["tabela"] == "localizacoes_viagem"
        return res["erros"][0]["mensagem"]

    base = loc_arq(r["id"], admin)
    fantasma = uuid.uuid4()
    assert "requisição inexistente" in erro(dict(base, requisicao_id=str(uuid.uuid4())))
    msg = erro(dict(base, registrado_por=str(fantasma)))
    assert "Autor da localização" in msg and str(fantasma) not in msg
    assert "Latitude fora do intervalo" in erro(dict(base, latitude="91"))
    assert "Longitude fora do intervalo" in erro(dict(base, longitude="-180.5"))
    assert "Precisão da localização inválida" in erro(dict(base, precisao_m="-1"))
    assert "Campo obrigatório da localização ausente: latitude/longitude" in erro(
        dict(base, latitude=None)
    )
    assert "Momento da localização inválido" in erro(dict(base, momento="meio"))
    ok = importar(
        arquivo(veiculos=[v], requisicoes=[r], localizacoes_viagem=[base]), admin, True
    )
    assert ok["aplicado"] is True and ok["resumo"]["localizacoes_viagem"]["novos"] == 1


def test_erro_em_filho_nao_grava_nada(admin: uuid.UUID) -> None:
    v, r = _req_com_veiculo(admin)
    ruim = checklist_arq(r["id"], admin)
    ruim["registrado_por"] = str(uuid.uuid4())
    res = importar(
        arquivo(veiculos=[v], requisicoes=[r], checklists=[ruim]), admin, True
    )
    assert res["aplicado"] is False
    assert contar("select count(*) from public.requisicoes where id = %s", r["id"]) == 0
    assert contar("select count(*) from public.veiculos where id = %s", v["id"]) == 0


# ---------------------------------------------------------------------------
# Limites por tabela (AG060)
# ---------------------------------------------------------------------------
def test_limite_por_tabela_ag060_com_mensagem_clara(admin: uuid.UUID) -> None:
    with pytest.raises(psycopg.errors.Error) as e:
        importar(arquivo(veiculos=[{}] * 501), admin, False)
    assert e.value.sqlstate == "AG060"
    texto = str(e.value)
    assert "veiculos" in texto and "500" in texto and "501" in texto
    with pytest.raises(psycopg.errors.Error) as e:
        importar(arquivo(checklists={}), admin, False)
    assert e.value.sqlstate == "AG060" and "checklists" in str(e.value)


# ---------------------------------------------------------------------------
# Exportacao
# ---------------------------------------------------------------------------
def test_exportar_cobre_todas_as_colunas_e_audita(
    admin: uuid.UUID, solicitante: uuid.UUID
) -> None:
    v = veiculo_arq(placa_unica())
    r = requisicao_arq(solicitante, v["id"], admin)
    importar(
        arquivo(
            veiculos=[v], requisicoes=[r], checklists=[checklist_arq(r["id"], admin)]
        ),
        admin,
        True,
    )
    antes = contar(
        "select count(*) from public.auditoria where acao = 'dados_exportados' and ator_id = %s",
        admin,
    )

    ex = exportar(admin)
    assert set(ex["tabelas"]) == set(TABELAS)
    assert ex["contagens"]["requisicoes"] == len(ex["tabelas"]["requisicoes"])
    assert "foto" not in json.dumps(ex["tabelas"]["checklists"][0]).lower()
    # nenhuma coluna de transporte/segredo e nenhuma coluna ficou de fora
    excecoes = {
        "alterado_por",
        "motorista_alterado_por",
        "id",
        "atualizado_em",
        "mfa",
        "passkeys",
        "notificacao_push",
        "relatorio_email",
        "relatorio_whatsapp",
        "compartilhar_imagem",  # 20261205120000: fora da exportacao (decisao LGPD)
    }
    with h.conectar() as c:
        for tabela in TABELAS:
            colunas = {
                x[0]
                for x in c.execute(
                    "select column_name from information_schema.columns where table_schema = 'public' "
                    "and table_name = %s",
                    (tabela,),
                ).fetchall()
            }
            linha = ex["tabelas"][tabela]
            linha = linha[0] if isinstance(linha, list) else linha
            esperado = colunas - (
                {"alterado_por", "motorista_alterado_por"}
                if tabela != "configuracoes"
                else excecoes
            )
            if tabela == "requisicoes":  # derivada: recalculada pelo gatilho na importacao
                esperado -= {"km_percorrido"}
            assert set(linha) == esperado, (tabela, set(linha) ^ esperado)

    with h.conectar() as c:
        row = c.execute(
            "select tabela, registro_id, detalhes from public.auditoria where acao = 'dados_exportados' "
            "and ator_id = %s order by id desc limit 1",
            (admin,),
        ).fetchone()
    assert (
        contar(
            "select count(*) from public.auditoria where acao = 'dados_exportados' and ator_id = %s",
            admin,
        )
        == antes + 1
    )
    assert row is not None and row[0] == "configuracoes" and row[1] is None
    assert set(row[2]) == {"versao", "contagens"}
    assert all(isinstance(n, int) for n in row[2]["contagens"].values())


def test_exportar_e_reimportar_tudo_existente(
    admin: uuid.UUID, solicitante: uuid.UUID
) -> None:
    v = veiculo_arq(placa_unica())
    r = requisicao_arq(solicitante, v["id"], admin)
    importar(
        arquivo(
            veiculos=[v], requisicoes=[r], checklists=[checklist_arq(r["id"], admin)]
        ),
        admin,
        True,
    )
    ex = exportar(admin)
    res = importar({"versao": 1, "tabelas": ex["tabelas"]}, admin, False)
    assert res["erros"] == []
    for t in (
        "veiculos",
        "requisicoes",
        "checklists",
        "localizacoes_viagem",
        "configuracoes",
    ):
        assert res["resumo"][t]["novos"] == 0, t
        assert res["resumo"][t]["conflitos"] == 0, t


# ---------------------------------------------------------------------------
# Concorrencia: duas importacoes simultaneas do mesmo arquivo -> uma so grava
# ---------------------------------------------------------------------------
def test_importacoes_simultaneas_nao_duplicam(
    admin: uuid.UUID, solicitante: uuid.UUID
) -> None:
    v = veiculo_arq()
    r = requisicao_arq(solicitante, v["id"], admin)
    dados = arquivo(veiculos=[v], requisicoes=[r])
    resultados: list[dict] = []
    erros: list[BaseException] = []

    def rodar() -> None:
        try:
            resultados.append(importar(dados, admin, True))
        except BaseException as exc:  # noqa: BLE001
            erros.append(exc)

    ts = [threading.Thread(target=rodar) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not erros, erros
    assert sum(x["resumo"]["requisicoes"]["novos"] for x in resultados) == 1
    assert contar("select count(*) from public.requisicoes where id = %s", r["id"]) == 1
