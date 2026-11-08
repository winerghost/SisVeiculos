"""Parte administrativa dos fakes: emula o que os triggers/constraints das migrations 6 a 15
garantem (ator admin ativo, auto-alteração, último admin, convite de uso único, placa única,
dependência de recursos) e grava a auditoria como o banco grava. Os erros passam por
`traduzir_erro_banco`, então os testes também exercitam a tradução real.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import threading
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

from postgrest.exceptions import APIError

from app.errors import ContaExistente, ErroInterno, FalhaRegistroAcesso
from app.modelos import Configuracoes, Perfil
from app.repositorios.auth_admin import ContaAuth, IndiceEmails
from app.repositorios.supabase_repo import traduzir_erro_banco

_EMAIL = re.compile(r"^[a-z0-9._%+-]+@(gmail\.com|([a-z0-9-]+\.)*agu\.gov\.br)$")
_COLUNAS_CONVITE = ["id", "email", "role", "unidade", "criado_por", "criado_em"] + [
    "expira_em",
    "usado_em",
    "usado_por",
    "revogado_em",
    "revogado_por",
]
_UA_DB = re.compile(r"[A-Za-z][A-Za-z .+-]{0,23}/[A-Za-z][A-Za-z .+-]{0,23}")
ANTES = "2026-01-01T00:00:00+00:00"


def erro_db(codigo: str, texto: str = "") -> Exception:
    return traduzir_erro_banco(
        APIError({"message": texto, "code": codigo, "details": "", "hint": ""})
    )


def agora_iso() -> str:
    return datetime.now(UTC).isoformat()


class AdminMemoria:
    """Mixin do RepoMemoria."""

    perfis: dict[str, Perfil]
    veiculos: dict[str, dict[str, Any]]
    config: Configuracoes

    def _init_admin(self) -> None:
        self.convites: dict[str, dict[str, Any]] = {}  # guarda token_hash (não sai nas leituras)
        self.eventos: list[dict[str, Any]] = []  # auditoria administrativa, como o banco grava
        self.contas_auth: dict[str, dict[str, Any]] = {}
        self._adm = threading.RLock()  # o advisory lock exclusivo do banco
        self.acessos: dict[str, dict[str, Any]] = {}  # session_id -> linha (como a tabela)
        self.falha_registro: str | None = None  # injeta um código de erro (ex.: "AG019")
        self.chamadas_registro = 0
        self.falha_listagem: Exception | None = None
        self.falha_auditoria: Exception | None = None

    # -- helpers ------------------------------------------------------------------------
    def _audit(
        self, tabela: str, registro: str | None, acao: str, ator: str | None, **kw: Any
    ) -> None:
        self.eventos.append(
            {
                "tabela": tabela,
                "registro_id": registro,
                "acao": acao,
                "ator_id": ator,
                "de": kw.get("de"),
                "para": kw.get("para"),
                "detalhes": kw.get("detalhes", {}),
            }
        )

    def eventos_de(self, acao: str) -> list[dict[str, Any]]:
        return [e for e in self.eventos if e["acao"] == acao]

    def _exigir_admin_ativo(self, ator: str | None) -> None:
        p = self.perfis.get(ator or "")
        if ator is None or p is None or not p.ativo or p.role != "admin":
            raise erro_db("AG002")

    @staticmethod
    def _perfil_dict(p: Perfil) -> dict[str, Any]:
        return {
            "id": p.id,
            "nome": p.nome,
            "unidade": p.unidade,
            "role": p.role,
            "ativo": p.ativo,
            "criado_em": ANTES,
            "atualizado_em": ANTES,
        }

    # -- perfis -------------------------------------------------------------------------
    def _filtrados(
        self,
        role: str | None,
        ativo: bool | None,
        busca: str | None = None,
        ids_email: frozenset[str] = frozenset(),
    ) -> list[Perfil]:
        ps = [
            p
            for p in self.perfis.values()
            if (role is None or p.role == role)
            and (ativo is None or p.ativo == ativo)
            and (not busca or busca.lower() in p.nome.lower() or p.id in ids_email)
        ]
        return sorted(ps, key=lambda p: (p.nome, p.id))

    def listar_perfis(
        self,
        *,
        role: str | None,
        ativo: bool | None,
        busca: str | None = None,
        ids_email: frozenset[str] = frozenset(),
        limite: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        todos = self._filtrados(role, ativo, busca, ids_email)
        return [self._perfil_dict(p) for p in todos[offset : offset + limite]]

    def contar_perfis(
        self,
        *,
        role: str | None,
        ativo: bool | None,
        busca: str | None = None,
        ids_email: frozenset[str] = frozenset(),
    ) -> int:
        return len(self._filtrados(role, ativo, busca, ids_email))

    def obter_perfil_admin(self, usuario_id: str) -> dict[str, Any] | None:
        p = self.perfis.get(usuario_id)
        return self._perfil_dict(p) if p else None

    def contar_admins_ativos(self) -> int:
        return sum(1 for p in self.perfis.values() if p.role == "admin" and p.ativo)

    def atualizar_perfil(
        self, usuario_id: str, campos: dict[str, Any], *, ator_id: str
    ) -> dict[str, Any] | None:
        with self._adm:
            old = self.perfis.get(usuario_id)
            if old is None:
                return None
            new = replace(old, **campos)
            muda_papel = (new.role, new.ativo) != (old.role, old.ativo)
            muda_unidade = new.unidade != old.unidade
            if muda_papel or muda_unidade:
                self._exigir_admin_ativo(ator_id)
            if muda_papel:
                if ator_id == old.id:
                    raise erro_db("AG008")
                if old.role == "admin" and old.ativo and not (new.role == "admin" and new.ativo):
                    outros = [
                        p
                        for p in self.perfis.values()
                        if p.role == "admin" and p.ativo and p.id != old.id
                    ]
                    if not outros:
                        raise erro_db("AG009")
                fmt = lambda p: f"{p.role}/{'ativo' if p.ativo else 'inativo'}"  # noqa: E731
                self._audit(
                    "perfis", old.id, "perfil_alterado", ator_id, de=fmt(old), para=fmt(new)
                )
            if muda_unidade:
                self._audit(
                    "perfis",
                    old.id,
                    "perfil_unidade_alterada",
                    ator_id,
                    de=old.unidade,
                    para=new.unidade,
                )
            self.perfis[usuario_id] = new
            return self._perfil_dict(new)

    # -- convites -----------------------------------------------------------------------
    def _publico(self, c: dict[str, Any]) -> dict[str, Any]:
        return {k: c[k] for k in _COLUNAS_CONVITE}

    def _aberto(self, c: dict[str, Any]) -> bool:
        return c["usado_em"] is None and c["revogado_em"] is None

    def inserir_convite(self, dados: dict[str, Any]) -> dict[str, Any]:
        with self._adm:
            self._exigir_admin_ativo(dados.get("criado_por"))
            if not _EMAIL.fullmatch(dados["email"]):
                raise erro_db("23514", 'violates check constraint "ck_convites_email_dominio"')
            if any(
                self._aberto(c) and c["email"] == dados["email"] for c in self.convites.values()
            ):
                raise erro_db("23505", 'duplicate key value violates "ux_convites_email_aberto"')
            cid = str(uuid.uuid4())
            c = {
                "id": cid,
                "criado_em": agora_iso(),
                "usado_em": None,
                "usado_por": None,
                "revogado_em": None,
                "revogado_por": None,
                **dados,
            }
            self.convites[cid] = c
            self._audit(
                "convites",
                cid,
                "convite_criado",
                dados["criado_por"],
                detalhes={"role": c["role"], "unidade": c["unidade"], "expira_em": c["expira_em"]},
            )
            return self._publico(c)

    def obter_convite(self, convite_id: str) -> dict[str, Any] | None:
        c = self.convites.get(convite_id)
        return self._publico(c) if c else None

    def obter_convite_aberto_por_email(self, email: str) -> dict[str, Any] | None:
        return next(
            (
                self._publico(c)
                for c in self.convites.values()
                if c["email"] == email and self._aberto(c)
            ),
            None,
        )

    def _estado_ok(self, c: dict[str, Any], estado: str | None, agora: str) -> bool:
        if estado is None:
            return True
        if estado == "usado":
            return c["usado_em"] is not None
        if estado == "revogado":
            return c["revogado_em"] is not None
        expirado = datetime.fromisoformat(c["expira_em"]) <= datetime.fromisoformat(agora)
        return self._aberto(c) and (expirado if estado == "expirado" else not expirado)

    def listar_convites(
        self, *, estado: str | None, agora: str, limite: int, offset: int
    ) -> list[dict[str, Any]]:
        cs = [c for c in self.convites.values() if self._estado_ok(c, estado, agora)]
        cs.sort(key=lambda c: c["criado_em"], reverse=True)
        return [self._publico(c) for c in cs[offset : offset + limite]]

    def contar_convites(self, *, estado: str | None, agora: str) -> int:
        return sum(1 for c in self.convites.values() if self._estado_ok(c, estado, agora))

    def revogar_convite(self, convite_id: str, *, ator_id: str) -> dict[str, Any] | None:
        with self._adm:
            c = self.convites.get(convite_id)
            if c is None or not self._aberto(c):
                return None
            self._exigir_admin_ativo(ator_id)
            c["revogado_em"], c["revogado_por"] = agora_iso(), ator_id
            self._audit("convites", convite_id, "convite_revogado", ator_id)
            return self._publico(c)

    def consumir_convite(self, token_hash: str, usuario_id: str, nome: str) -> Perfil:
        with self._adm:
            c = next((c for c in self.convites.values() if c["token_hash"] == token_hash), None)
            if c is None:
                raise erro_db("AG007")
            if not self._aberto(c):
                raise erro_db("AG005")
            if datetime.fromisoformat(c["expira_em"]) <= datetime.now(UTC):
                raise erro_db("AG006")
            conta = self.contas_auth.get(usuario_id)
            if (
                conta is None
                or conta["email"] != c["email"]
                or not conta["confirmado"]
                or conta.get("banido")
            ):
                raise erro_db("AG007")
            if usuario_id in self.perfis:
                raise erro_db("23505", 'duplicate key value violates "perfis_pkey"')
            if c["role"] == "admin":
                self._exigir_admin_ativo(c["criado_por"])
            p = Perfil(usuario_id, nome, c["unidade"], c["role"], True)
            self.perfis[usuario_id] = p
            c["usado_em"], c["usado_por"] = agora_iso(), usuario_id
            ator = c["criado_por"] if c["role"] == "admin" else None
            self._audit("perfis", usuario_id, "perfil_criado", ator, para=f"{p.role}/ativo")
            self._audit("convites", c["id"], "convite_usado", usuario_id)
            return p

    # -- veículos -----------------------------------------------------------------------
    def listar_veiculos_admin(self, *, ativo: bool | None) -> list[dict[str, Any]]:
        vs = [dict(v) for v in self.veiculos.values() if ativo is None or v["ativo"] == ativo]
        return sorted(vs, key=lambda v: (v["descricao"], v["id"]))

    def _placa_livre(self, placa: str | None, ignorar: str | None = None) -> None:
        if placa and any(
            v["placa"] == placa and v["id"] != ignorar for v in self.veiculos.values()
        ):
            raise erro_db("23505", 'duplicate key value violates "ux_veiculos_placa"')

    def inserir_veiculo(self, dados: dict[str, Any], *, ator_id: str) -> dict[str, Any]:
        with self._adm:
            self._exigir_admin_ativo(ator_id)
            self._placa_livre(dados.get("placa"))
            vid = str(uuid.uuid4())
            v = {"id": vid, "ativo": True, "criado_em": ANTES, "atualizado_em": ANTES, **dados}
            self.veiculos[vid] = v
            self._audit("veiculos", vid, "veiculo_criado", ator_id)
            return dict(v)

    def atualizar_veiculo(
        self, veiculo_id: str, campos: dict[str, Any], *, ator_id: str
    ) -> dict[str, Any] | None:
        with self._adm:
            v = self.veiculos.get(veiculo_id)
            if v is None:
                return None
            mudou = [k for k, x in campos.items() if v.get(k) != x]
            if mudou:
                self._exigir_admin_ativo(ator_id)
                self._placa_livre(campos.get("placa"), ignorar=veiculo_id)
                v.update(campos)
                self._audit(
                    "veiculos",
                    veiculo_id,
                    "veiculo_alterado",
                    ator_id,
                    de=str(not v["ativo"]).lower() if "ativo" in mudou else None,
                    para=str(v["ativo"]).lower() if "ativo" in mudou else None,
                    detalhes={"campos": mudou},
                )
            return dict(v)

    # -- configurações ------------------------------------------------------------------
    def atualizar_configuracoes(self, campos: dict[str, bool], *, ator_id: str) -> Configuracoes:
        with self._adm:
            antigo = self.config.como_dict()
            novo = {**antigo, **campos}
            if any(novo[k] != antigo[k] for k in novo):
                self._exigir_admin_ativo(ator_id)
                if novo["abastecimento_manutencao"] and not novo["quilometragem"]:
                    raise erro_db("AG010")
                for k in novo:
                    if novo[k] != antigo[k]:
                        self._audit(
                            "configuracoes",
                            None,
                            "configuracao_alterada",
                            ator_id,
                            de=str(antigo[k]).lower(),
                            para=str(novo[k]).lower(),
                            detalhes={"recurso": k},
                        )
            self.config = Configuracoes(**novo)
            return self.config

    def registrar_auditoria(
        self, *, tabela: str, registro_id: str | None, ator_id: str, detalhes: dict[str, Any]
    ) -> None:
        if self.falha_auditoria is not None:
            raise self.falha_auditoria
        if ator_id not in self.perfis:
            raise erro_db("AG002")
        self._audit(tabela, registro_id, "acao_administrativa", ator_id, detalhes=detalhes)

    # -- histórico de acessos: emula as RPCs da migration 20261107122430 --------------------
    def registrar_acesso_login(
        self,
        *,
        usuario_id: str,
        session_id: str,
        ocorrido_em: str,
        metodo: str | None,
        aal: str | None,
        ip_mascarado: str | None,
        ua_familia: str | None,
    ) -> bool:
        self.chamadas_registro += 1
        if self.falha_registro is not None:
            raise FalhaRegistroAcesso(self.falha_registro)
        if metodo not in {None, "password", "totp", "outro"} or aal not in {None, "aal1", "aal2"}:
            raise FalhaRegistroAcesso("AG020")
        if ua_familia is not None and not _UA_DB.fullmatch(ua_familia):
            raise FalhaRegistroAcesso("AG020")
        if ip_mascarado is not None:
            rede = ipaddress.ip_network(ip_mascarado, strict=True)  # IP completo = erro
            if rede.prefixlen > (24 if rede.version == 4 else 48):
                raise FalhaRegistroAcesso("AG020")
        p = self.perfis.get(usuario_id)
        if p is None or not p.ativo:
            raise FalhaRegistroAcesso("AG019")
        with self._adm:
            if session_id in self.acessos:
                return False
            self.acessos[session_id] = {
                "id": str(uuid.uuid4()),
                "usuario_id": usuario_id,
                "session_id": session_id,
                "ocorrido_em": ocorrido_em,
                "metodo": metodo,
                "aal": aal,
                "ip_mascarado": ip_mascarado,
                "ua_familia": ua_familia,
            }
            return True

    def listar_acessos_login(
        self,
        *,
        usuario_id: str | None,
        de: str | None,
        ate: str | None,
        limite: int,
        offset: int,
    ) -> dict[str, Any]:
        self.ultima_listagem = {"usuario_id": usuario_id, "de": de, "ate": ate}
        if self.falha_listagem is not None:
            raise self.falha_listagem
        rows = [
            a
            for a in self.acessos.values()
            if (usuario_id is None or a["usuario_id"] == usuario_id)
            and (
                de is None or datetime.fromisoformat(a["ocorrido_em"]) >= datetime.fromisoformat(de)
            )
            and (
                ate is None
                or datetime.fromisoformat(a["ocorrido_em"]) < datetime.fromisoformat(ate)
            )
        ]
        rows.sort(key=lambda a: (a["ocorrido_em"], a["id"]), reverse=True)
        itens = [
            {
                "id": a["id"],
                "usuario_id": a["usuario_id"],
                "nome": self.perfis[a["usuario_id"]].nome,
                "ocorrido_em": a["ocorrido_em"],
                "metodo": a["metodo"],
                "aal": a["aal"],
                "ip_mascarado": a["ip_mascarado"],
                "ua_familia": a["ua_familia"],
                "sessao_ref": hashlib.sha256(a["session_id"].encode()).hexdigest()[:8],
            }
            for a in rows[offset : offset + limite]
        ]
        return {"total": len(rows), "limite": limite, "offset": offset, "itens": itens}

    def ultimo_acesso_por_usuario(self, usuario_ids: list[str]) -> dict[str, str]:
        if len(usuario_ids) > 100:
            raise erro_db("AG020")
        out: dict[str, str] = {}
        for a in self.acessos.values():
            if a["usuario_id"] in usuario_ids:
                out[a["usuario_id"]] = max(a["ocorrido_em"], out.get(a["usuario_id"], ""))
        return out


class AuthAdminMemoria:
    """Fake da Admin API do Supabase Auth, sobre `repo.contas_auth`."""

    def __init__(self, repo: AdminMemoria) -> None:
        self.contas = repo.contas_auth
        self.envios: list[tuple[str, str | None]] = []  # (email, redirect_to): e-mails "enviados"
        self.falhar_convite = False
        self.falhar_bloqueio = False
        self.falhar_reenvio_acesso = False
        self.consultas_email: list[set[str]] = []  # ids pedidos a emails_por_id
        self.varreduras = 0
        self.indice_truncado = False
        self.atraso_convidar: threading.Event | None = None  # segura o envio (concorrência)
        self.dentro_de_convidar = threading.Event()

    def nova_conta(self, email: str, nome: str | None = None, *, confirmado: bool = True) -> str:
        uid = str(uuid.uuid4())
        self.contas[uid] = {"email": email, "nome": nome, "confirmado": confirmado, "banido": False}
        return uid

    def id_de(self, email: str) -> str:
        return next(i for i, c in self.contas.items() if c["email"] == email)

    def ultimo_token(self) -> str:
        """Token em claro do último e-mail de convite (extraído do link, como o front faz)."""
        redirect = self.envios[-1][1] or ""
        return parse_qs(urlsplit(redirect).query)["convite"][0]

    def convidar(self, email: str, *, nome: str | None, redirect_to: str | None) -> str:
        if self.falhar_convite:
            raise ErroInterno("Serviço de autenticação indisponível. Tente novamente.")
        if self.atraso_convidar is not None:
            self.dentro_de_convidar.set()
            self.atraso_convidar.wait(5)
        existente = next((i for i, c in self.contas.items() if c["email"] == email), None)
        if existente and self.contas[existente]["confirmado"]:
            raise ContaExistente("Já existe uma conta com este e-mail.")
        if existente:
            if nome is not None:
                self.contas[existente]["nome"] = nome
            uid = existente
        else:
            uid = self.nova_conta(email, nome, confirmado=False)
        self.envios.append((email, redirect_to))
        return uid

    def obter_conta(self, usuario_id: str) -> ContaAuth | None:
        c = self.contas.get(usuario_id)
        return ContaAuth(usuario_id, c["email"], c["nome"]) if c else None

    def emails_por_id(self, ids: set[str]) -> dict[str, str]:
        self.consultas_email.append(set(ids))
        return {i: self.contas[i]["email"] for i in ids if i in self.contas}

    def id_por_email(self, email: str) -> str | None:
        self.varreduras += 1
        return next((i for i, c in self.contas.items() if c["email"] == email), None)

    def indice_emails(self) -> IndiceEmails:
        self.varreduras += 1
        mapa = {i: c["email"].lower() for i, c in self.contas.items()}
        return IndiceEmails(mapa, self.indice_truncado)

    def reenviar_acesso(self, email: str, *, redirect_to: str) -> None:
        if self.falhar_reenvio_acesso:
            raise ErroInterno("Serviço de autenticação indisponível. Tente novamente.")
        self.envios.append((email, redirect_to))

    def bloquear(self, usuario_id: str) -> None:
        if self.falhar_bloqueio:
            raise ErroInterno("Serviço de autenticação indisponível. Tente novamente.")
        if usuario_id in self.contas:
            self.contas[usuario_id]["banido"] = True

    def desbloquear(self, usuario_id: str) -> None:
        if usuario_id in self.contas:
            self.contas[usuario_id]["banido"] = False
