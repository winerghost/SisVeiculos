"""Testes da migration 20261202120000: public.atualizacoes_versao (marcadores do polling).

Somente banco local/dev DESCARTAVEL (ver dbhelpers.py). Cada teste cria usuarios/requisicoes proprios.

    pytest supabase/tests/test_atualizacoes_versao.py -m db_local

Propriedade central: o marcador de um recurso muda se e so se algo relevante PARA AQUELE PERFIL mudou
(anti canal lateral: o solicitante A nao percebe acoes na requisicao de B; leitura nao muda versao).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]

NEGADO = "42501"
FUNCAO = "public.atualizacoes_versao(uuid)"


def versao(uid: uuid.UUID) -> dict:
    with h.conectar() as c:
        row = c.execute("select public.atualizacoes_versao(%s)", (uid,)).fetchone()
    assert row is not None
    v = row[0]
    return v if isinstance(v, dict) else json.loads(v)


def _cfg(valores: dict[str, bool], por: uuid.UUID) -> None:
    sets = ", ".join(f"{k} = %s" for k in valores)
    with h.conectar() as c:
        c.execute(
            f"update public.configuracoes set {sets}, alterado_por = %s where id = true",
            (*valores.values(), por),
        )


def _motorista_ligado() -> bool:
    with h.conectar() as c:
        row = c.execute("select motorista, motorista_registra from public.configuracoes").fetchone()
    assert row is not None
    return bool(row[0])


class Cena:
    def __init__(self) -> None:
        self.admin = h.criar_usuario("atu-admin", "admin")
        self.aut = h.criar_usuario("atu-aut", "autorizador")
        self.sol_a = h.criar_usuario("atu-sol-a")
        self.sol_b = h.criar_usuario("atu-sol-b")
        self.mot = h.criar_usuario("atu-mot", "motorista")
        self.mot2 = h.criar_usuario("atu-mot2", "motorista")
        self.vig = h.criar_usuario("atu-vig", "vigilante")

    def solicitada(self, dono: uuid.UUID) -> uuid.UUID:
        with h.conectar() as c:
            return h.inserir_requisicao(c, dono, h.novo_veiculo(self.admin))


@pytest.fixture
def cena() -> Cena:
    return Cena()


@pytest.fixture
def motorista_ligado(cena: Cena) -> Iterator[None]:
    orig = _motorista_ligado()
    if not orig:
        _cfg({"motorista": True}, cena.admin)
    try:
        yield
    finally:
        if not orig:
            with h.conectar() as c:
                reg = c.execute("select motorista_registra from public.configuracoes").fetchone()
            if reg and reg[0]:
                _cfg({"motorista_registra": False}, cena.admin)
            _cfg({"motorista": False}, cena.admin)


# --- grants e forma ----------------------------------------------------------------------------
@pytest.mark.parametrize("papel", ["anon", "authenticated"])
def test_execute_so_service_role(cena: Cena, papel: str) -> None:
    with pytest.raises(psycopg.Error) as e:
        with h.como(papel, cena.sol_a) as c:
            c.execute("select public.atualizacoes_versao(%s)", (cena.sol_a,))
    assert h.sqlstate(e) == NEGADO


def test_service_role_executa(cena: Cena) -> None:
    with h.como("service_role") as c:
        row = c.execute("select public.atualizacoes_versao(%s)", (cena.sol_a,)).fetchone()
    assert row is not None


def test_funcao_security_definer_search_path_vazio() -> None:
    with h.conectar() as c:
        row = c.execute(
            "select prosecdef, proconfig, provolatile from pg_proc where oid = %s::regprocedure", (FUNCAO,)
        ).fetchone()
    assert row is not None
    assert row[0] is True and row[2] == "s"
    assert any(cfg.startswith("search_path=") and cfg in ('search_path=""', "search_path=") for cfg in row[1])


def test_forma_por_papel(cena: Cena) -> None:
    for uid in (cena.sol_a, cena.aut, cena.mot, cena.vig):
        v = versao(uid)
        assert set(v) == {"requisicoes", "notificacoes", "veiculos", "configuracoes", "usuarios", "nao_lidas"}
        assert v["usuarios"] is None and isinstance(v["nao_lidas"], int)
    assert isinstance(versao(cena.admin)["usuarios"], str)


def test_perfil_inexistente_ou_inativo_zera(cena: Cena) -> None:
    inativo = h.criar_usuario("atu-inativo", ativo=False)
    for uid in (uuid.uuid4(), inativo):
        v = versao(uid)
        assert v["requisicoes"] == "0" and v["usuarios"] is None and v["nao_lidas"] == 0


def test_sem_dado_pessoal_no_retorno(cena: Cena) -> None:
    rid = cena.solicitada(cena.sol_a)
    texto = json.dumps(versao(cena.admin))
    assert str(rid) not in texto and str(cena.sol_a) not in texto and "@" not in texto


# --- requisicoes: canal lateral ----------------------------------------------------------------
def test_solicitante_nao_ve_requisicao_alheia(cena: Cena) -> None:
    a0, aut0 = versao(cena.sol_a)["requisicoes"], versao(cena.aut)["requisicoes"]
    rid_b = cena.solicitada(cena.sol_b)
    assert versao(cena.sol_a)["requisicoes"] == a0
    assert versao(cena.aut)["requisicoes"] != aut0
    h.autorizar(rid_b, cena.aut)  # saida/chegada dependem de recursos (checklist) do banco local
    assert versao(cena.sol_a)["requisicoes"] == a0
    h.cancelar(rid_b, cena.aut)
    assert versao(cena.sol_a)["requisicoes"] == a0


def test_solicitante_ve_a_propria(cena: Cena) -> None:
    rid = cena.solicitada(cena.sol_a)
    v0 = versao(cena.sol_a)["requisicoes"]
    h.autorizar(rid, cena.aut)
    assert versao(cena.sol_a)["requisicoes"] != v0


def test_leitura_de_localizacao_nao_muda(cena: Cena) -> None:
    rid = cena.solicitada(cena.sol_a)
    antes = {u: versao(u)["requisicoes"] for u in (cena.sol_a, cena.aut, cena.admin)}
    with h.conectar() as c:  # mesma linha que a RPC registrar_auditoria grava na leitura do admin
        c.execute(
            "insert into public.auditoria (tabela, registro_id, acao, ator_id, detalhes) "
            "values ('requisicoes', %s, 'localizacao_lida', %s, jsonb_build_object('itens', 0))",
            (rid, cena.admin),
        )
    assert {u: versao(u)["requisicoes"] for u in antes} == antes


def test_arquivamento_e_pessoal(cena: Cena) -> None:
    rid = cena.solicitada(cena.sol_a)
    h.cancelar(rid, cena.sol_a)
    a0, aut0 = versao(cena.sol_a)["requisicoes"], versao(cena.aut)["requisicoes"]
    with h.conectar() as c:
        c.execute(
            "insert into public.requisicoes_arquivadas (usuario_id, requisicao_id) values (%s, %s)",
            (cena.sol_a, rid),
        )
    a1 = versao(cena.sol_a)["requisicoes"]
    assert a1 != a0 and versao(cena.aut)["requisicoes"] == aut0
    with h.conectar() as c:  # desarquivar apaga a linha: a versao muda de novo
        c.execute(
            "delete from public.requisicoes_arquivadas where usuario_id = %s and requisicao_id = %s",
            (cena.sol_a, rid),
        )
    assert versao(cena.sol_a)["requisicoes"] not in (a1,)


def test_motorista_so_atribuidas_e_remocao(cena: Cena, motorista_ligado: None) -> None:
    rid = cena.solicitada(cena.sol_a)
    h.autorizar(rid, cena.aut)
    m0, m2_0 = versao(cena.mot)["requisicoes"], versao(cena.mot2)["requisicoes"]
    with h.conectar() as c:
        c.execute(
            "update public.requisicoes set motorista_id = %s, motorista_alterado_por = %s where id = %s",
            (cena.mot, cena.aut, rid),
        )
    m1 = versao(cena.mot)["requisicoes"]
    assert m1 != m0 and versao(cena.mot2)["requisicoes"] == m2_0
    # Outra requisicao sem motorista: nenhum dos dois percebe.
    outra = cena.solicitada(cena.sol_b)
    h.autorizar(outra, cena.aut)
    assert versao(cena.mot)["requisicoes"] == m1
    assert versao(cena.mot2)["requisicoes"] == m2_0
    # Removido da viagem: a lista dele muda.
    with h.conectar() as c:
        c.execute(
            "update public.requisicoes set motorista_id = %s, motorista_alterado_por = %s where id = %s",
            (cena.mot2, cena.aut, rid),
        )
    assert versao(cena.mot)["requisicoes"] != m1
    assert versao(cena.mot2)["requisicoes"] != m2_0


def test_vigilante_ve_entrada_e_saida_de_autorizada(cena: Cena) -> None:
    v0 = versao(cena.vig)["requisicoes"]
    rid = cena.solicitada(cena.sol_a)
    assert versao(cena.vig)["requisicoes"] == v0  # solicitada alheia: invisivel
    h.autorizar(rid, cena.aut)
    v1 = versao(cena.vig)["requisicoes"]
    assert v1 != v0
    h.cancelar(rid, cena.aut)  # sai de autorizada: some da lista dele
    assert versao(cena.vig)["requisicoes"] != v1


# --- notificacoes, veiculos, configuracoes, usuarios -------------------------------------------
def test_marcar_lida_muda_e_nao_lidas(cena: Cena) -> None:
    rid = cena.solicitada(cena.sol_a)
    h.autorizar(rid, cena.aut)  # gera requisicao_autorizada para o solicitante
    v0 = versao(cena.sol_a)
    assert v0["nao_lidas"] >= 1
    outro0 = versao(cena.sol_b)["notificacoes"]
    with h.conectar() as c:
        nid = c.execute(
            "select id from public.notificacoes where usuario_id = %s and lida_em is null limit 1",
            (cena.sol_a,),
        ).fetchone()[0]
        c.execute("select public.marcar_notificacao_lida(%s, %s)", (cena.sol_a, nid))
    v1 = versao(cena.sol_a)
    assert v1["notificacoes"] != v0["notificacoes"] and v1["nao_lidas"] == v0["nao_lidas"] - 1
    assert versao(cena.sol_b)["notificacoes"] == outro0


def test_veiculo_muda_para_todos(cena: Cena) -> None:
    v0 = versao(cena.sol_a)["veiculos"]
    vid = h.novo_veiculo(cena.admin)
    v1 = versao(cena.sol_a)["veiculos"]
    assert v1 != v0
    h.alterar_veiculo(vid, cena.admin, "descricao = 'outro nome'")
    assert versao(cena.sol_a)["veiculos"] != v1


def test_veiculos_muda_com_ocupacao_alheia(cena: Cena) -> None:
    """Migration 20261203120000 (N1): abrir/fechar requisicao de B muda `veiculos` para A (a
    disponibilidade do GET /api/veiculos mudou); transicao entre status abertos e edicao nao."""
    vid = h.novo_veiculo(cena.admin)  # cadastro antes: so a ocupacao muda daqui em diante
    v0 = versao(cena.sol_a)["veiculos"]
    with h.conectar() as c:
        rid_b = h.inserir_requisicao(c, cena.sol_b, vid)
    v1 = versao(cena.sol_a)["veiculos"]
    assert v1 != v0
    # observacao so muda enquanto 'solicitada' (requisicoes_validar, AG004): edita antes de autorizar.
    with h.conectar() as c:
        c.execute("update public.requisicoes set observacao = 'outra' where id = %s", (rid_b,))
    assert versao(cena.sol_a)["veiculos"] == v1
    h.autorizar(rid_b, cena.aut)  # continua ocupado
    assert versao(cena.sol_a)["veiculos"] == v1
    h.cancelar(rid_b, cena.aut)  # libera o veiculo
    assert versao(cena.sol_a)["veiculos"] != v1


def test_usuarios_muda_com_perfil_e_nao_com_passkey_autenticada(cena: Cena) -> None:
    u0 = versao(cena.admin)["usuarios"]
    h.alterar_unidade(cena.sol_a, cena.admin, "PU")
    u1 = versao(cena.admin)["usuarios"]
    assert u1 != u0
    with h.conectar() as c:
        c.execute(
            "insert into public.auditoria (tabela, registro_id, acao, ator_id, detalhes) "
            "values ('perfis', %s, 'passkey_autenticada', %s, '{}'::jsonb)",
            (cena.sol_a, cena.sol_a),
        )
        c.execute(
            "insert into public.auditoria (tabela, registro_id, acao, ator_id, detalhes) "
            "values ('perfis', null, 'acao_administrativa', %s, jsonb_build_object('recurso', 'acessos'))",
            (cena.admin,),
        )
    assert versao(cena.admin)["usuarios"] == u1


def test_funcao_nao_grava_nada(cena: Cena) -> None:
    with h.conectar() as c:
        antes = c.execute("select max(id) from public.auditoria").fetchone()[0]
    versao(cena.admin)
    versao(cena.sol_a)
    with h.conectar() as c:
        assert c.execute("select max(id) from public.auditoria").fetchone()[0] == antes


def test_indices_criados() -> None:
    with h.conectar() as c:
        nomes = {
            r[0]
            for r in c.execute(
                "select indexname from pg_indexes where schemaname = 'public' and indexname = any(%s)",
                (["ix_auditoria_tabela_id", "ix_auditoria_tabela_registro_id", "ix_requisicoes_motorista_id",
                  "ix_requisicoes_atualizado_em"],),
            ).fetchall()
        }
    assert len(nomes) == 4
