"""Helpers compartilhados pelos testes de banco (somente banco local/dev DESCARTAVEL).

A conexao vem de TEST_DATABASE_URL (ex.: a URL do `supabase start`). Como as migrations proibem
DELETE fisico e auditoria e append-only, os testes NAO limpam o que criam: use um banco descartavel
(`supabase db reset`). Todos os dados usam IDs/e-mails unicos, entao repetir a suite nao gera colisao.

GUARDA DE BANCO DESCARTAVEL (os testes gravam e nunca apagam; em banco real poluiriam dados e a
auditoria append-only). O DSN so e aceito (h.DSN) quando TODAS as condicoes valem; senao h.DSN fica
None, os testes sao PULADOS e um aviso explica o motivo (sem repassar detalhes de erro do banco):
  1. TEST_DATABASE_URL definida;
  2. confirmacao positiva TEST_DATABASE_CONFIRM=descartavel (segunda chave, sempre exigida);
  3. host local EXPLICITO (localhost, 127.0.0.1, ::1 ou socket '/...'); DSN com hostaddr, service,
     passfile ou lista de hosts e recusado; host vazio so vale sem PGHOST/PGHOSTADDR/PGSERVICE no
     ambiente; PGHOSTADDR/PGSERVICE no ambiente sempre recusam. TEST_DATABASE_ALLOW_REMOTE=1 e
     excecao explicita para o host e NAO dispensa as demais condicoes;
  4. current_database() com nome de teste (token test/teste/tmp/dev/local/descartavel separado por
     _ ou -; o banco "postgres" so vale em host local);
  5. MARCADOR POSITIVO no proprio banco: comment on database <nome> is 'sisveiculos:descartavel'.
     O `supabase db reset` recria o banco e apaga o marcador: crie-o DEPOIS do reset e antes do pytest.
Risco residual: um tunel/porta-forward local para banco real passa nas condicoes 3 e 4; so o marcador
(que ninguem cria em banco real) e a confirmacao protegem. Nunca crie o marcador em banco real.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
import uuid
import warnings
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.conninfo import conninfo_to_dict

CONFIRMACAO_ESPERADA = "descartavel"
MARCADOR_BANCO = "sisveiculos:descartavel"
_NOME_DE_TESTE = re.compile(r"(^|[_-])(test|teste|tmp|dev|local|descartavel)([_-]|$)", re.IGNORECASE)
_HOSTS_LOCAIS = ("localhost", "127.0.0.1", "::1")


def _host_local(dsn: str) -> bool:
    if os.environ.get("PGHOSTADDR") or os.environ.get("PGSERVICE"):
        return False
    try:
        d = conninfo_to_dict(dsn)
    except psycopg.Error:
        return False
    if any(d.get(k) for k in ("hostaddr", "service", "passfile")):
        return False
    host = d.get("host") or ""
    if not host:
        return not os.environ.get("PGHOST")
    return "," not in host and (host.startswith("/") or host in _HOSTS_LOCAIS)


def _avaliar_dsn() -> tuple[str | None, str | None]:
    """(dsn aceito, motivo do pulo). Nunca inclui o DSN nem a mensagem de erro do banco."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        return None, "TEST_DATABASE_URL nao definida"
    if os.environ.get("TEST_DATABASE_CONFIRM") != CONFIRMACAO_ESPERADA:
        return None, (
            "TEST_DATABASE_CONFIRM=descartavel nao definida: confirme que o banco e DESCARTAVEL "
            "(os testes gravam e nao apagam)"
        )
    local = _host_local(url)
    if not local and os.environ.get("TEST_DATABASE_ALLOW_REMOTE") != "1":
        return None, "host do DSN nao e local explicito (TEST_DATABASE_ALLOW_REMOTE=1 + confirmacao forca)"
    try:
        with psycopg.connect(url, connect_timeout=10) as c:
            row = c.execute("select current_database()").fetchone()
            nome = str(row[0]) if row else ""
            if not (_NOME_DE_TESTE.search(nome) or (local and nome == "postgres")):
                return None, "current_database() nao parece banco de teste"
            marca = c.execute(
                "select shobj_description(oid, 'pg_database') = %s from pg_database "
                "where datname = current_database()",
                (MARCADOR_BANCO,),
            ).fetchone()
    except psycopg.Error:
        return None, "banco inacessivel"
    if not (marca and marca[0]):
        return None, (
            f"marcador ausente no banco: rode (so em banco descartavel, depois do db reset) "
            f"comment on database <nome> is '{MARCADOR_BANCO}'"
        )
    return url, None


DSN, MOTIVO_PULO = _avaliar_dsn()
if DSN is None:
    warnings.warn(
        f"Testes de banco PULADOS: {MOTIVO_PULO}. Ver docs/deploy-banco.md, 'Testes de banco'.",
        stacklevel=1,
    )


def conectar() -> "psycopg.Connection":
    assert DSN, f"banco de teste nao liberado: {MOTIVO_PULO}"
    return psycopg.connect(DSN, autocommit=True)


def admin_ativo_qualquer() -> uuid.UUID | None:
    """Um admin ativo existente (responsavel exigido para criar outro admin, migration 11)."""
    with conectar() as c:
        row = c.execute(
            "select id from public.perfis where role = 'admin' and ativo order by id limit 1"
        ).fetchone()
    return row[0] if row else None


def criar_usuario(
    nome: str, role: str = "solicitante", ativo: bool = True, por: uuid.UUID | None = None
) -> uuid.UUID:
    """Cria conta confirmada no Auth + perfil.

    Admin: segue a regra da migration 11. O responsavel (alterado_por) e `por`, se dado;
    senao um admin ativo existente (order by id); sem nenhum, o primeiro nasce sem ator (bootstrap).
    """
    uid = uuid.uuid4()
    ator = (por or admin_ativo_qualquer()) if role == "admin" else None
    with conectar() as c:
        c.execute(
            "insert into auth.users (id, email, email_confirmed_at) values (%s, %s, now())",
            (uid, f"{nome}-{uid.hex[:8]}@teste.invalid"),
        )
        # Migration 12 (M1): o bootstrap sem ator so vale com a marca de seed em curso na
        # mesma transacao; o helper a informa (mesmo que privado.semear_primeiro_admin faria).
        with c.transaction():
            if role == "admin" and ator is None:
                c.execute("select set_config('sisveiculos.seed_em_curso', '1', true)")
            c.execute(
                "insert into public.perfis (id, nome, unidade, role, ativo, alterado_por) "
                "values (%s, %s, 'UEA', %s, %s, %s)",
                (uid, nome, role, ativo, ator),
            )
    return uid


def novo_veiculo(por: uuid.UUID | None = None) -> uuid.UUID:
    """Cria veiculo. Migration 13 (M5): exige admin ativo como responsavel (alterado_por).

    Sem `por`, usa um admin ativo existente (ou cria um).
    """
    vid = uuid.uuid4()
    ator = por or admin_ativo_qualquer() or criar_usuario("admin-veic", "admin")
    with conectar() as c:
        c.execute(
            "insert into public.veiculos (id, descricao, alterado_por) values (%s, %s, %s)",
            (vid, f"teste-{vid.hex[:8]}", ator),
        )
    return vid


def alterar_veiculo(vid: uuid.UUID, por: uuid.UUID | None, set_sql: str) -> None:
    """Altera veiculo informando o responsavel (alterado_por); set_sql ex.: "ativo = false"."""
    _atualizar(f"update public.veiculos set {set_sql}, alterado_por = %s where id = %s", (por, vid))


def alterar_unidade(pid: uuid.UUID, por: uuid.UUID | None, unidade: str) -> None:
    """Troca a unidade do perfil informando o responsavel (alterado_por)."""
    _atualizar(
        "update public.perfis set unidade = %s::public.unidade, alterado_por = %s where id = %s",
        (unidade, por, pid),
    )


def inserir_requisicao(
    conn: "psycopg.Connection", usuario_id: uuid.UUID, veiculo_id: uuid.UUID
) -> uuid.UUID:
    """Insere requisicao 'solicitada' na conexao dada (sem commit se nao for autocommit)."""
    rid = uuid.uuid4()
    conn.execute(
        """insert into public.requisicoes
           (id, usuario_id, nome_solicitante, unidade, hora_saida, trajeto, veiculo_id)
           values (%s, %s, 'Teste', 'UEA', now() + interval '1 day', 'A-B', %s)""",
        (rid, usuario_id, veiculo_id),
    )
    return rid


def _atualizar(sql: str, params: tuple) -> None:
    with conectar() as c:
        c.execute(sql, params)


def autorizar(rid: uuid.UUID, por: uuid.UUID) -> None:
    _atualizar("update public.requisicoes set status='autorizada', autorizado_por=%s where id=%s", (por, rid))


def negar(rid: uuid.UUID, por: uuid.UUID) -> None:
    _atualizar("update public.requisicoes set status='negada', autorizado_por=%s where id=%s", (por, rid))


def iniciar(rid: uuid.UUID, por: uuid.UUID) -> None:
    _atualizar("update public.requisicoes set status='em_uso', saida_registrada_por=%s where id=%s", (por, rid))


def concluir(rid: uuid.UUID, por: uuid.UUID) -> None:
    _atualizar("update public.requisicoes set status='concluida', chegada_registrada_por=%s where id=%s", (por, rid))


def cancelar(rid: uuid.UUID, por: uuid.UUID) -> None:
    _atualizar("update public.requisicoes set status='cancelada', cancelada_por=%s where id=%s", (por, rid))


@contextmanager
def reaplicando_migration() -> Iterator["psycopg.Connection"]:
    """Conexao em transacao que SEMPRE termina em ROLLBACK, para testes de "migration idempotente".

    Reaplicar uma migration antiga sobre o banco ja migrado sobrescreve definicoes mais novas (gatilhos,
    funcoes, CHECKs) e deixaria o banco inconsistente para os testes seguintes (ex.: o gatilho
    trg_auditoria_notificar voltaria a versao sem os avisos de motorista). O DDL do Postgres e
    transacional: a reaplicacao e verificada dentro da transacao e depois desfeita.
    """
    assert DSN, f"banco de teste nao liberado: {MOTIVO_PULO}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        with conn.transaction(force_rollback=True):
            yield conn


PAPEIS_API = ("anon", "authenticated", "service_role")


@contextmanager
def como(papel: str, uid: uuid.UUID | None = None) -> Iterator["psycopg.Connection"]:
    """Conexao em transacao que assume um papel da API (RLS/grants valem).

    Para 'authenticated', informa o JWT simulado (claim sub = uid) como o PostgREST
    faria. Sempre termina em rollback: nada e gravado. Use uma chamada por comando
    que se espera falhar (apos erro a transacao fica abortada).
    """
    assert papel in PAPEIS_API, papel
    assert DSN, f"banco de teste nao liberado: {MOTIVO_PULO}"
    with psycopg.connect(DSN) as conn:
        conn.execute(f"set local role {papel}")
        if uid is not None:
            claims = json.dumps({"sub": str(uid), "role": papel})
            conn.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
        try:
            yield conn
        finally:
            conn.rollback()


def sqlstate(excinfo: "object") -> str | None:
    """SQLSTATE do erro capturado por pytest.raises (ex.: 'AG002')."""
    return getattr(getattr(excinfo, "value", None), "sqlstate", None)


# --- perfis (migration 7) -----------------------------------------------------
def alterar_perfil(
    alvo: uuid.UUID,
    por: uuid.UUID | None,
    role: str | None = None,
    ativo: bool | None = None,
) -> None:
    """Altera role/ativo informando o responsavel (alterado_por), como o backend faz."""
    _atualizar(
        "update public.perfis set role = coalesce(%s::public.role_usuario, role), "
        "ativo = coalesce(%s::boolean, ativo), alterado_por = %s where id = %s",
        (role, ativo, por, alvo),
    )


def perfil_de(pid: uuid.UUID) -> tuple[str, bool]:
    with conectar() as c:
        row = c.execute("select role::text, ativo from public.perfis where id=%s", (pid,)).fetchone()
    assert row is not None
    return str(row[0]), bool(row[1])


def auditoria_de(tabela: str, registro_id: uuid.UUID | None) -> list[tuple]:
    with conectar() as c:
        return c.execute(
            "select acao, ator_id, de, para from public.auditoria "
            "where tabela=%s and registro_id is not distinct from %s order by id",
            (tabela, registro_id),
        ).fetchall()


# --- convites (migration 6) ---------------------------------------------------
def novo_token() -> tuple[str, str]:
    """(token em claro, sha256 hex). Em producao so o hash vai para o banco."""
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def email_unico() -> str:
    return f"t{uuid.uuid4().hex[:12]}@gmail.com"


def criar_auth(
    email: str,
    *,
    confirmado: bool = True,
    banido_ate: str | None = None,
    apagado: bool = False,
) -> uuid.UUID:
    """Conta no Auth SEM perfil (estado anterior a consumir o convite).

    Por padrao confirmada (a migration 11 exige email_confirmed_at). banido_ate e uma
    expressao SQL (ex.: "now() + interval '1 day'").
    """
    uid = uuid.uuid4()
    confirmacao = "now()" if confirmado else "null"
    banimento = banido_ate or "null"
    exclusao = "now()" if apagado else "null"
    with conectar() as c:
        c.execute(
            "insert into auth.users (id, email, email_confirmed_at, banned_until, deleted_at) "
            f"values (%s, %s, {confirmacao}, {banimento}, {exclusao})",
            (uid, email),
        )
    return uid


def novo_convite(
    admin: uuid.UUID,
    email: str,
    *,
    role: str = "solicitante",
    expirado: bool = False,
) -> tuple[uuid.UUID, str]:
    """Insere convite e devolve (id, token_hash).

    expirado=True: como criado_em e do servidor (migration 11), nao da para inserir um
    convite ja vencido; cria com validade de 1 s e espera vencer.
    """
    _, token_hash = novo_token()
    cid = uuid.uuid4()
    expira = "now() + interval '1 second'" if expirado else "now() + interval '7 days'"
    with conectar() as c:
        c.execute(
            f"insert into public.convites (id, email, role, unidade, token_hash, criado_por, expira_em) "
            f"values (%s, %s, %s, 'UEA', %s, %s, {expira})",
            (cid, email, role, token_hash, admin),
        )
    if expirado:
        time.sleep(1.5)
    return cid, token_hash


def status_de(rid: uuid.UUID) -> str:
    with conectar() as c:
        row = c.execute("select status::text from public.requisicoes where id=%s", (rid,)).fetchone()
    assert row is not None
    return str(row[0])
