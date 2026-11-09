"""Testes da migration 7: ao menos um admin ativo, sem auto-rebaixamento/desativacao.

Somente banco local/dev descartavel (TEST_DATABASE_URL). Ver dbhelpers.py.
Como o responsavel precisa ser OUTRO admin ativo, o "ultimo admin" nunca fica
sem substituto: o cenario e coberto pela recusa do auto-ato (AG008) e pela recusa
de responsavel que ja deixou de ser admin ativo (AG002).

    pytest supabase/tests/test_guarda_admin.py -m db_local
"""

from __future__ import annotations

import threading

import pytest

psycopg = pytest.importorskip("psycopg")

import dbhelpers as h  # noqa: E402

pytestmark = [
    pytest.mark.db_local,
    pytest.mark.skipif(not h.DSN, reason="TEST_DATABASE_URL nao definida (banco local/dev)"),
]


def test_admin_nao_se_rebaixa() -> None:
    a = h.criar_usuario("admin", "admin")
    with pytest.raises(psycopg.Error) as exc:
        h.alterar_perfil(a, a, role="solicitante")
    assert h.sqlstate(exc) == "AG008"
    assert h.perfil_de(a) == ("admin", True)


def test_admin_nao_se_desativa() -> None:
    a = h.criar_usuario("admin", "admin")
    with pytest.raises(psycopg.Error) as exc:
        h.alterar_perfil(a, a, ativo=False)
    assert h.sqlstate(exc) == "AG008"
    assert h.perfil_de(a) == ("admin", True)


def test_alteracao_sem_responsavel_e_recusada() -> None:
    a = h.criar_usuario("admin", "admin")
    with pytest.raises(psycopg.Error) as exc:
        h.alterar_perfil(a, None, ativo=False)
    assert h.sqlstate(exc) == "AG002"


@pytest.mark.parametrize("papel", ["solicitante", "autorizador"])
def test_nao_admin_nao_altera_papel_de_ninguem(papel: str) -> None:
    ator = h.criar_usuario("ator", papel)
    alvo = h.criar_usuario("alvo")
    with pytest.raises(psycopg.Error) as exc:
        h.alterar_perfil(alvo, ator, role="admin")
    assert h.sqlstate(exc) == "AG002"
    assert h.perfil_de(alvo) == ("solicitante", True)


def test_admin_inativo_nao_altera_papel() -> None:
    inativo = h.criar_usuario("admin-inativo", "admin", ativo=False)
    alvo = h.criar_usuario("alvo")
    with pytest.raises(psycopg.Error) as exc:
        h.alterar_perfil(alvo, inativo, ativo=False)
    assert h.sqlstate(exc) == "AG002"


def test_admin_rebaixa_outro_admin_e_o_rebaixado_perde_o_poder() -> None:
    a = h.criar_usuario("admin-a", "admin")
    b = h.criar_usuario("admin-b", "admin")
    h.alterar_perfil(b, a, role="autorizador")  # permitido: a continua admin
    assert h.perfil_de(b) == ("autorizador", True)
    # b ja nao e admin: nao consegue mexer em a (nem "ultimo admin" sobra sem admin).
    with pytest.raises(psycopg.Error) as exc:
        h.alterar_perfil(a, b, ativo=False)
    assert h.sqlstate(exc) == "AG002"
    assert h.perfil_de(a) == ("admin", True)


def test_responsavel_nao_fica_gravado_e_nao_vale_para_proxima_alteracao() -> None:
    a = h.criar_usuario("admin-a", "admin")
    alvo = h.criar_usuario("alvo")
    h.alterar_perfil(alvo, a, ativo=False)
    with h.conectar() as c:
        (resto,) = c.execute("select alterado_por from public.perfis where id=%s", (alvo,)).fetchone() or (1,)
    assert resto is None
    # Nova alteracao sem informar responsavel: recusada (nao reaproveita o anterior).
    with pytest.raises(psycopg.Error) as exc:
        with h.conectar() as c:
            c.execute("update public.perfis set ativo = true where id=%s", (alvo,))
    assert h.sqlstate(exc) == "AG002"


def test_alteracao_de_papel_gera_auditoria() -> None:
    a = h.criar_usuario("admin-a", "admin")
    alvo = h.criar_usuario("alvo")
    h.alterar_perfil(alvo, a, role="autorizador")
    linhas = h.auditoria_de("perfis", alvo)
    # A criacao do perfil tambem gera auditoria (`perfil_criado`, sem ator); depois, a alteracao.
    assert linhas == [
        ("perfil_criado", None, None, "solicitante/ativo"),
        ("perfil_alterado", a, "solicitante/ativo", "autorizador/ativo"),
    ]


def test_alterar_nome_nao_exige_responsavel() -> None:
    a = h.criar_usuario("admin-a", "admin")
    with h.conectar() as c:
        c.execute("update public.perfis set nome='Outro Nome' where id=%s", (a,))
    assert h.perfil_de(a) == ("admin", True)


def test_dois_admins_se_desativando_ao_mesmo_tempo_nao_zeram_o_par() -> None:
    a = h.criar_usuario("admin-a", "admin")
    b = h.criar_usuario("admin-b", "admin")
    barreira = threading.Barrier(2)
    resultados: list[str] = []
    lock = threading.Lock()

    def desativar(ator, alvo) -> None:
        with psycopg.connect(h.DSN) as conn:
            barreira.wait(timeout=60)
            try:
                conn.execute(
                    "update public.perfis set ativo=false, alterado_por=%s where id=%s",
                    (ator, alvo),
                )
                conn.execute("select pg_sleep(0.5)")  # segura o lock para forcar a disputa
                conn.commit()
                r = "ok"
            except psycopg.Error as e:
                conn.rollback()
                r = str(e.sqlstate)
        with lock:
            resultados.append(r)

    ts = [
        threading.Thread(target=desativar, args=(a, b)),
        threading.Thread(target=desativar, args=(b, a)),
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=90)

    assert sorted(resultados) == ["AG002", "ok"], resultados
    ativos = [h.perfil_de(x)[1] for x in (a, b)]
    assert ativos.count(True) == 1


def test_anel_de_admins_se_desativando_ao_mesmo_tempo_nunca_zera_o_grupo() -> None:
    """A-07: invariante "ao menos um admin ativo" sob concorrencia, pelo trigger REAL.

    Usa a mesma rota SQL do backend (`UPDATE public.perfis SET ativo=false, alterado_por=<ator>
    WHERE id=<alvo>`, repositorios/supabase_repo.py, via PostgREST com service_role): sem RPC nem
    logica de aplicacao, quem serializa e o advisory lock do gatilho `perfis_guarda_admin`.
    Quatro admins em anel (A desativa B, B desativa C, C desativa D, D desativa A) disparam juntos,
    cada um segurando a transacao um instante para forcar a disputa. Sem a serializacao, todos
    passariam na checagem "o ator e admin ativo" e o grupo ficaria inteiro inativo.

    Garantias: (1) sobra ao menos um ativo; (2) toda recusa e AG002 (ator ja desativado por outra
    transacao) ou AG009 (ultimo admin), nunca erro inesperado; (3) cada sucesso foi feito por um
    ator que estava ativo; (4) a soma bate: ativos finais = 4 - sucessos.
    """
    admins = [h.criar_usuario(f"anel-{i}", "admin") for i in range(4)]
    barreira = threading.Barrier(4, timeout=60)
    resultados: dict[int, str] = {}
    lock = threading.Lock()

    def desativar(i: int) -> None:
        ator, alvo = admins[i], admins[(i + 1) % 4]
        with psycopg.connect(h.DSN) as conn:  # type: ignore[arg-type]
            barreira.wait()
            try:
                conn.execute(
                    "update public.perfis set ativo=false, alterado_por=%s where id=%s", (ator, alvo)
                )
                conn.execute("select pg_sleep(0.3)")  # segura o lock para forcar a disputa
                conn.commit()
                r = "ok"
            except psycopg.Error as e:
                conn.rollback()
                r = str(e.sqlstate)
        with lock:
            resultados[i] = r

    ts = [threading.Thread(target=desativar, args=(i,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=120)

    assert len(resultados) == 4, resultados
    assert set(resultados.values()) <= {"ok", "AG002", "AG009"}, resultados
    ativos = [h.perfil_de(x)[1] for x in admins]
    assert any(ativos), f"o grupo ficou sem admin ativo: {resultados}"
    sucessos = [i for i, r in resultados.items() if r == "ok"]
    assert ativos.count(True) == 4 - len(sucessos), (resultados, ativos)
    assert len(sucessos) <= 3  # nunca os 4
