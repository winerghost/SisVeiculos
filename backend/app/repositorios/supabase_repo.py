"""Implementação do repositório sobre Supabase (PostgREST, service_role).

Todas as consultas usam o cliente PostgREST (parametrizado). A tradução dos erros do
banco (índice parcial de veículo ocupado e SQLSTATE AGxxx) fica aqui.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from postgrest.exceptions import APIError
from supabase import Client, ClientOptions, create_client

from app.errors import (
    AutoAlteracaoProibida,
    ChecklistForaDaJanela,
    ChecklistObrigatorio,
    Conflito,
    ConviteAbertoExistente,
    ConviteEncerrado,
    ConviteExpirado,
    ConviteInvalido,
    DependenciaRecurso,
    ErroApi,
    ErroInterno,
    FalhaRegistroAcesso,
    PlacaDuplicada,
    RequisicaoInvalida,
    SemPermissao,
    SistemaOcupado,
    TransicaoInvalida,
    UltimoAdmin,
    VeiculoIndisponivel,
    VeiculoOcupado,
)
from app.modelos import Configuracoes, Perfil, Requisicao

log = logging.getLogger(__name__)
T = TypeVar("T")

INDICE_VEICULO_ABERTO = "ux_requisicoes_veiculo_aberta"

COLUNAS_REQ = (
    "id,usuario_id,nome_solicitante,unidade,hora_saida,trajeto,observacao,veiculo_id,status,"
    "autorizado_por,autorizado_em,saida_real_em,chegada_real_em,saida_registrada_por,"
    "chegada_registrada_por,cancelada_por,cancelada_em,km_saida,km_chegada,criado_em,atualizado_em"
)

COLUNAS_REQ_SET = frozenset(COLUNAS_REQ.split(","))

MSG_VEICULO_OCUPADO = (
    "Este veículo já possui uma requisição em aberto. "
    "Ele só aceita novo pedido após a conclusão, negativa ou cancelamento da anterior."
)


COLUNAS_CHECKLIST = (
    "id,requisicao_id,momento,combustivel,avarias,descricao_avarias,registrado_por,registrado_em"
)
COLUNAS_CHECKLIST_FOTO = "id,requisicao_id,momento,posicao,mime_type,tamanho_bytes,caminho"
COLUNAS_PERFIL_ADMIN = "id,nome,unidade,role,ativo,criado_em,atualizado_em"
COLUNAS_CONVITE = (
    "id,email,role,unidade,criado_por,criado_em,expira_em,usado_em,usado_por,"
    "revogado_em,revogado_por"
)
COLUNAS_VEICULO_ADMIN = "id,descricao,placa,ativo,criado_em,atualizado_em"
_SET_PERFIL = frozenset(COLUNAS_PERFIL_ADMIN.split(","))
_SET_CONVITE = frozenset(COLUNAS_CONVITE.split(","))
_SET_VEICULO = frozenset(COLUNAS_VEICULO_ADMIN.split(","))
_SET_ACESSO = frozenset(
    [
        "id",
        "usuario_id",
        "nome",
        "ocorrido_em",
        "metodo",
        "aal",
        "ip_mascarado",
        "ua_familia",
        "sessao_ref",
    ]
)

# Contrato da migration 15: lock_timeout curto no banco + nova tentativa em lock/deadlock.
CODIGOS_LOCK = frozenset({"55P03", "40P01", "40001"})  # lock, deadlock, serialização
ESPERAS_LOCK = (0.2, 0.5)  # segundos entre as 3 tentativas
_dormir = time.sleep  # substituível nos testes

MSG_OCUPADO = "O sistema está ocupado no momento. Tente novamente em instantes."
MSG_OCUPADO_ADMIN = "O sistema está processando outra alteração administrativa. Tente novamente."
# Orçamento de tempo de uma escrita com retry: 3 x lock_timeout(5 s) + 0,7 s de espera ~ 15,7 s,
# abaixo do gunicorn --timeout 30. O timeout HTTP do PostgREST (20 s) fica acima desse orçamento
# por tentativa e abaixo do gunicorn.
TIMEOUT_POSTGREST = 20
LOTE_IN = 50  # ids por consulta `in (...)` (UUIDs longos: evita URL gigante)

MSG_KM_OBRIGATORIO = (
    "Informe a quilometragem: km_saida ao registrar a saída e km_chegada ao registrar a "
    "chegada (se a saída foi registrada sem km, envie km_saida e km_chegada juntos)."
)
MSG_KM_JA_GRAVADO = "A quilometragem já registrada não pode ser alterada."
MSG_KM_CHEGADA_MENOR = "O km de chegada não pode ser menor que o km de saída."
MSG_KM_INVALIDO = "Quilometragem inválida: use números inteiros, sem negativos."
MSG_KM_ESTOURO = "Quilometragem acima do valor máximo aceito (2147483647)."

MSG_CHECKLIST_SAIDA = "O checklist de saída é obrigatório antes de registrar a saída."
MSG_CHECKLIST_CHEGADA = "O checklist de chegada é obrigatório antes de registrar a chegada."
MSG_CHECKLIST_JANELA = (
    "O checklist não pode ser registrado agora: a saída só aceita checklist com a requisição "
    "autorizada e a chegada, com a requisição em uso."
)
MSG_CHECKLIST_DUPLICADO = "Já existe checklist registrado para este momento."
MSG_CHECKLIST_INVALIDO = "Os dados do checklist não são válidos."
MSG_CHECKLIST_MAX_FOTOS = "O checklist aceita no máximo 6 fotos."

MSG_FOTO_INVALIDA = "A foto enviada não é válida."
MSG_FOTO_REPETIDA = "Esta foto já foi registrada. Envie a foto novamente."

MSG_EMAIL = "Informe um e-mail institucional (@agu.gov.br) ou Gmail."


def filtrar_colunas(linha: dict[str, Any]) -> dict[str, Any]:
    """Só as colunas públicas de COLUNAS_REQ (evita vazar colunas futuras da tabela)."""
    return {k: v for k, v in linha.items() if k in COLUNAS_REQ_SET}


def traduzir_erro_banco(e: APIError, *, admin: bool = False) -> ErroApi:
    """Converte erro do PostgREST/Postgres em erro de API com mensagem clara."""
    codigo = str(getattr(e, "code", "") or "")
    texto = f"{getattr(e, 'message', '')} {getattr(e, 'details', '')}"
    if codigo == "23505" and INDICE_VEICULO_ABERTO in texto:
        return VeiculoOcupado(MSG_VEICULO_OCUPADO)
    if codigo == "23505":
        if "perfil_fotos" in texto or "uq_perfil_fotos_ativa" in texto:  # PK repetida (p_id)
            return Conflito(MSG_FOTO_REPETIDA, "foto_ja_registrada")
        if "uq_checklists_requisicao_momento" in texto:
            return Conflito(MSG_CHECKLIST_DUPLICADO, "checklist_ja_registrado")
        if "checklist_fotos" in texto:  # uq_checklist_fotos_posicao ou PK (foto repetida)
            return RequisicaoInvalida("Há fotos repetidas no checklist.", "checklist_invalido")
        if "ux_veiculos_placa" in texto:
            return PlacaDuplicada("Já existe um veículo cadastrado com esta placa.")
        if "ux_convites_email_aberto" in texto:
            return ConviteAbertoExistente(
                "Já existe um convite em aberto para este e-mail. "
                "Reenvie ou revogue o convite atual antes de criar outro."
            )
        if "perfis_pkey" in texto:
            return Conflito("Esta conta já possui perfil de acesso.", "perfil_existente")
        return Conflito("Já existe um registro com estes dados.")
    if codigo == "AG017":  # foto: usuário inexistente ou inativo (mensagem fixa)
        return SemPermissao("Conta sem acesso ao sistema.", codigo="conta_inativa")
    if codigo == "AG022":  # remoção de foto alheia por quem não é admin ativo
        return SemPermissao("Você não tem permissão para esta ação.")
    if codigo == "23514" and "ck_perfil_fotos_" in texto:
        return RequisicaoInvalida(MSG_FOTO_INVALIDA, "foto_invalida")
    if codigo == "AG012":
        return RequisicaoInvalida(MSG_KM_OBRIGATORIO, "km_obrigatorio")
    if codigo == "AG004" and "uilometragem" in texto:
        return Conflito(MSG_KM_JA_GRAVADO, "km_ja_registrado")
    if codigo == "23514" and "ck_requisicoes_km_chegada_maior_igual_saida" in texto:
        return RequisicaoInvalida(MSG_KM_CHEGADA_MENOR, "km_invalido")
    if codigo == "23514" and "ck_requisicoes_km_" in texto:
        return RequisicaoInvalida(MSG_KM_INVALIDO, "km_invalido")
    if codigo == "22003":
        return RequisicaoInvalida(MSG_KM_ESTOURO, "km_invalido")
    if codigo == "AG013":
        msg = MSG_CHECKLIST_CHEGADA if "chegada" in texto.lower() else MSG_CHECKLIST_SAIDA
        return ChecklistObrigatorio(msg)
    if codigo == "AG014":
        return ChecklistForaDaJanela(MSG_CHECKLIST_JANELA)
    if codigo == "AG003" and "checklist" in texto.lower():
        return Conflito("Um checklist registrado não pode ser alterado.", "checklist_imutavel")
    if codigo == "23514" and "ck_checklist_fotos_posicao" in texto:
        return RequisicaoInvalida(MSG_CHECKLIST_MAX_FOTOS, "checklist_invalido")
    if codigo == "23514" and "ck_checklist" in texto:
        return RequisicaoInvalida(MSG_CHECKLIST_INVALIDO, "checklist_invalido")
    if codigo == "22P02" and "nivel_combustivel" in texto:
        return RequisicaoInvalida("Nível de combustível inválido.", "checklist_invalido")
    if codigo == "AG001":
        return TransicaoInvalida("A requisição não está em um estado que permita esta ação.")
    if codigo == "AG002":
        return SemPermissao("Você não tem permissão para esta ação.")
    if codigo == "AG011":
        return VeiculoIndisponivel("Veículo inexistente ou inativo.")
    if codigo == "AG005":
        return ConviteEncerrado("Este convite já foi utilizado ou revogado.")
    if codigo == "AG006":
        return ConviteExpirado("Este convite expirou. Peça ao administrador um novo convite.")
    if codigo == "AG007":
        return ConviteInvalido("Convite inválido.")
    if codigo == "AG008":
        return AutoAlteracaoProibida(
            "O administrador não pode alterar o próprio papel nem desativar a si mesmo."
        )
    if codigo == "AG009":
        return UltimoAdmin("Deve existir ao menos um administrador ativo.")
    if codigo == "AG010" or (codigo == "23514" and "ck_configuracoes_abastecimento" in texto):
        return DependenciaRecurso("Abastecimento e manutenção exigem a Quilometragem ligada.")
    if codigo == "23514" and "ck_convites_email_dominio" in texto:
        return RequisicaoInvalida(MSG_EMAIL)
    if codigo in CODIGOS_LOCK:
        return SistemaOcupado(MSG_OCUPADO_ADMIN if admin else MSG_OCUPADO)
    if codigo == "23502":  # not_null_violation: resposta fixa; a mensagem pode citar coluna/valor
        log.warning("erro_banco codigo=%s", codigo)
        return RequisicaoInvalida("Há campos obrigatórios não preenchidos.")
    if codigo in {"AG004", "23514", "22P02", "23503", "22023"}:
        return RequisicaoInvalida("Os dados enviados não são válidos para esta operação.")
    # Só o código vai ao log: a mensagem pode trazer dados pessoais.
    log.error("erro_banco codigo=%s", codigo)
    return ErroInterno("Serviço de dados indisponível. Tente novamente.")


def _exec(  # noqa: UP047
    fn: Callable[[], T], *, repetir_lock: bool = False, admin: bool = False
) -> T:
    """Executa e traduz erros. `repetir_lock`: nova tentativa em 55P03/40P01 (escritas).
    `admin`: usa a mensagem de ocupado das rotas administrativas.

    Cada tentativa é uma transação nova no PostgREST; a anterior foi desfeita, então repetir
    o comando único é seguro. Só o código SQLSTATE vai ao log.
    """
    tentativas = len(ESPERAS_LOCK) + 1 if repetir_lock else 1
    for n in range(tentativas):
        try:
            return fn()
        except APIError as e:
            if str(getattr(e, "code", "") or "") in CODIGOS_LOCK and n < tentativas - 1:
                log.warning("lock_repetindo tentativa=%d", n + 1)
                _dormir(ESPERAS_LOCK[n])
                continue
            raise traduzir_erro_banco(e, admin=admin) from None
        except ErroApi:
            raise
        except Exception as e:  # rede, timeout etc.
            log.error("erro_acesso_dados tipo=%s", type(e).__name__)
            raise ErroInterno("Serviço de dados indisponível. Tente novamente.") from None
    raise AssertionError("inalcançável")  # pragma: no cover


def _so(linha: dict[str, Any], colunas: frozenset[str]) -> dict[str, Any]:
    return {k: v for k, v in linha.items() if k in colunas}


class SupabaseRepositorio:
    def __init__(self, url: str, service_role_key: str) -> None:
        self._c: Client = create_client(
            url,
            service_role_key,
            options=ClientOptions(postgrest_client_timeout=TIMEOUT_POSTGREST),
        )

    def obter_perfil(self, usuario_id: str) -> Perfil | None:
        res = _exec(
            lambda: self._c.table("perfis")
            .select("id,nome,unidade,role,ativo")
            .eq("id", usuario_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        r = res.data[0]
        return Perfil(r["id"], r["nome"], r["unidade"], r["role"], bool(r["ativo"]))

    def ler_configuracoes(self) -> Configuracoes:
        res = _exec(
            lambda: self._c.table("configuracoes")
            .select("quilometragem,abastecimento_manutencao,checklist,mfa")
            .limit(1)
            .execute()
        )
        if not res.data:
            return Configuracoes()  # sem linha: tudo desligado
        r = res.data[0]
        return Configuracoes(
            bool(r["quilometragem"]),
            bool(r["abastecimento_manutencao"]),
            bool(r["checklist"]),
            bool(r["mfa"]),
        )

    def listar_veiculos(self) -> list[dict[str, Any]]:
        res = _exec(
            lambda: self._c.table("veiculos")
            .select("id,descricao,placa")
            .eq("ativo", True)
            .order("descricao")
            .execute()
        )
        return list(res.data)

    def obter_veiculo(self, veiculo_id: str) -> dict[str, Any] | None:
        res = _exec(
            lambda: self._c.table("veiculos")
            .select(COLUNAS_VEICULO_ADMIN)
            .eq("id", veiculo_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def veiculos_ocupados(self) -> set[str]:
        res = _exec(
            lambda: self._c.table("requisicoes")
            .select("veiculo_id")
            .in_("status", ["solicitada", "autorizada", "em_uso"])
            .execute()
        )
        return {r["veiculo_id"] for r in res.data}

    def veiculo_ocupado(self, veiculo_id: str) -> bool:
        res = _exec(
            lambda: self._c.table("requisicoes")
            .select("id")
            .eq("veiculo_id", veiculo_id)
            .in_("status", ["solicitada", "autorizada", "em_uso"])
            .limit(1)
            .execute()
        )
        return bool(res.data)

    def inserir_requisicao(self, dados: dict[str, Any]) -> Requisicao:
        res = _exec(lambda: self._c.table("requisicoes").insert(dados).execute(), repetir_lock=True)
        return filtrar_colunas(res.data[0])

    def obter_requisicao(self, requisicao_id: str) -> Requisicao | None:
        res = _exec(
            lambda: self._c.table("requisicoes")
            .select(COLUNAS_REQ)
            .eq("id", requisicao_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def listar_requisicoes(
        self, *, usuario_id: str | None, status: str | None, limite: int, offset: int
    ) -> list[Requisicao]:
        def consulta() -> Any:
            q = self._c.table("requisicoes").select(COLUNAS_REQ)
            if usuario_id is not None:
                q = q.eq("usuario_id", usuario_id)
            if status is not None:
                q = q.eq("status", status)
            return q.order("criado_em", desc=True).range(offset, offset + limite - 1).execute()

        return list(_exec(consulta).data)

    def contar_requisicoes(self, *, usuario_id: str | None, status: str | None) -> int:
        def consulta() -> Any:
            q = self._c.table("requisicoes").select("id", count="exact").limit(1)  # type: ignore[arg-type]
            if usuario_id is not None:
                q = q.eq("usuario_id", usuario_id)
            if status is not None:
                q = q.eq("status", status)
            return q.execute()

        return int(_exec(consulta).count or 0)

    def nomes_perfis(self, ids: set[str]) -> dict[str, str]:
        if not ids:
            return {}
        res = _exec(
            lambda: self._c.table("perfis").select("id,nome").in_("id", sorted(ids)).execute()
        )
        return {r["id"]: r["nome"] for r in res.data}

    def descricoes_veiculos(self, ids: set[str]) -> dict[str, str]:
        if not ids:
            return {}
        res = _exec(
            lambda: self._c.table("veiculos")
            .select("id,descricao")
            .in_("id", sorted(ids))
            .execute()
        )
        return {r["id"]: r["descricao"] for r in res.data}

    # -- checklist (migrations 19 a 22) ---------------------------------------------------
    def momentos_checklist(self, requisicao_ids: set[str]) -> dict[str, set[str]]:
        if not requisicao_ids:
            return {}
        res = _exec(
            lambda: self._c.table("checklists")
            .select("requisicao_id,momento")
            .in_("requisicao_id", sorted(requisicao_ids))
            .execute()
        )
        saida: dict[str, set[str]] = {}
        for r in res.data:
            saida.setdefault(r["requisicao_id"], set()).add(r["momento"])
        return saida

    def obter_checklists(self, requisicao_id: str) -> list[dict[str, Any]]:
        res = _exec(
            lambda: self._c.table("checklists")
            .select(COLUNAS_CHECKLIST)
            .eq("requisicao_id", requisicao_id)
            .order("registrado_em")
            .execute()
        )
        fotos = _exec(
            lambda: self._c.table("checklist_fotos")
            .select(COLUNAS_CHECKLIST_FOTO)
            .eq("requisicao_id", requisicao_id)
            .order("posicao")
            .execute()
        )
        return [
            {**c, "fotos": [f for f in fotos.data if f["momento"] == c["momento"]]}
            for c in res.data
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
        params = {
            "p_requisicao_id": requisicao_id,
            "p_momento": momento,
            "p_combustivel": combustivel,
            "p_avarias": avarias,
            "p_descricao_avarias": descricao_avarias,
            "p_registrado_por": registrado_por,
            "p_fotos": fotos,
        }
        res = _exec(lambda: self._c.rpc("registrar_checklist", params).execute(), repetir_lock=True)
        return str(res.data)

    def transicionar(
        self, requisicao_id: str, status_atual: str, campos: dict[str, Any]
    ) -> Requisicao | None:
        res = _exec(
            lambda: self._c.table("requisicoes")
            .update(campos)
            .eq("id", requisicao_id)
            .eq("status", status_atual)
            .execute(),
            repetir_lock=True,
        )
        return filtrar_colunas(res.data[0]) if res.data else None

    # -- administração (contrato em base.py) ---------------------------------------------
    @staticmethod
    def _filtrar_perfis(
        q: Any, role: str | None, ativo: bool | None, busca: str | None, ids_email: frozenset[str]
    ) -> Any:
        if role is not None:
            q = q.eq("role", role)
        if ativo is not None:
            q = q.eq("ativo", ativo)
        if busca:
            # `busca` já foi validada (sem , ( ) " \ * %), então não altera a sintaxe do filtro;
            # os ids vêm do Auth (UUIDs). O resto do valor é dado, nunca SQL.
            partes = [f"nome.ilike.*{busca}*"]
            if ids_email:
                partes.append("id.in.(" + ",".join(sorted(ids_email)) + ")")
            q = q.or_(",".join(partes))
        return q

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
        def consulta() -> Any:
            q = self._filtrar_perfis(
                self._c.table("perfis").select(COLUNAS_PERFIL_ADMIN),
                role,
                ativo,
                busca,
                ids_email,
            )
            return q.order("nome").order("id").range(offset, offset + limite - 1).execute()

        return [_so(r, _SET_PERFIL) for r in _exec(consulta).data]

    def contar_perfis(
        self,
        *,
        role: str | None,
        ativo: bool | None,
        busca: str | None = None,
        ids_email: frozenset[str] = frozenset(),
    ) -> int:
        def consulta() -> Any:
            q = self._c.table("perfis").select("id", count="exact").limit(1)  # type: ignore[arg-type]
            return self._filtrar_perfis(q, role, ativo, busca, ids_email).execute()

        return int(_exec(consulta).count or 0)

    def obter_perfil_admin(self, usuario_id: str) -> dict[str, Any] | None:
        res = _exec(
            lambda: self._c.table("perfis")
            .select(COLUNAS_PERFIL_ADMIN)
            .eq("id", usuario_id)
            .limit(1)
            .execute()
        )
        return _so(res.data[0], _SET_PERFIL) if res.data else None

    def contar_admins_ativos(self) -> int:
        res = _exec(
            lambda: self._c.table("perfis")
            .select("id", count="exact")  # type: ignore[arg-type]
            .eq("role", "admin")
            .eq("ativo", True)
            .limit(1)
            .execute()
        )
        return int(res.count or 0)

    def atualizar_perfil(
        self, usuario_id: str, campos: dict[str, Any], *, ator_id: str
    ) -> dict[str, Any] | None:
        res = _exec(
            lambda: self._c.table("perfis")
            .update({**campos, "alterado_por": ator_id})
            .eq("id", usuario_id)
            .execute(),
            repetir_lock=True,
            admin=True,
        )
        return _so(res.data[0], _SET_PERFIL) if res.data else None

    def inserir_convite(self, dados: dict[str, Any]) -> dict[str, Any]:
        res = _exec(
            lambda: self._c.table("convites").insert(dados).execute(), repetir_lock=True, admin=True
        )
        return _so(res.data[0], _SET_CONVITE)  # nunca devolve token_hash

    def obter_convite(self, convite_id: str) -> dict[str, Any] | None:
        res = _exec(
            lambda: self._c.table("convites")
            .select(COLUNAS_CONVITE)
            .eq("id", convite_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def obter_convite_aberto_por_email(self, email: str) -> dict[str, Any] | None:
        res = _exec(
            lambda: self._c.table("convites")
            .select(COLUNAS_CONVITE)
            .eq("email", email)
            .is_("usado_em", "null")
            .is_("revogado_em", "null")
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    @staticmethod
    def _filtrar_estado(q: Any, estado: str | None, agora: str) -> Any:
        if estado in {"aberto", "expirado"}:
            q = q.is_("usado_em", "null").is_("revogado_em", "null")
            q = q.gt("expira_em", agora) if estado == "aberto" else q.lte("expira_em", agora)
        elif estado == "usado":
            q = q.not_.is_("usado_em", "null")
        elif estado == "revogado":
            q = q.not_.is_("revogado_em", "null")
        return q

    def listar_convites(
        self, *, estado: str | None, agora: str, limite: int, offset: int
    ) -> list[dict[str, Any]]:
        def consulta() -> Any:
            q = self._filtrar_estado(
                self._c.table("convites").select(COLUNAS_CONVITE), estado, agora
            )
            return q.order("criado_em", desc=True).range(offset, offset + limite - 1).execute()

        return list(_exec(consulta).data)

    def contar_convites(self, *, estado: str | None, agora: str) -> int:
        def consulta() -> Any:
            q = self._c.table("convites").select("id", count="exact").limit(1)  # type: ignore[arg-type]
            return self._filtrar_estado(q, estado, agora).execute()

        return int(_exec(consulta).count or 0)

    def revogar_convite(self, convite_id: str, *, ator_id: str) -> dict[str, Any] | None:
        # revogado_em é sobrescrito pelo trigger com now(); o CHECK de par exige ambos não nulos.
        agora = datetime.now(UTC).isoformat()
        res = _exec(
            lambda: self._c.table("convites")
            .update({"revogado_por": ator_id, "revogado_em": agora})
            .eq("id", convite_id)
            .is_("usado_em", "null")
            .is_("revogado_em", "null")
            .execute(),
            repetir_lock=True,
            admin=True,
        )
        return _so(res.data[0], _SET_CONVITE) if res.data else None

    def consumir_convite(self, token_hash: str, usuario_id: str, nome: str) -> Perfil:
        res = _exec(
            lambda: self._c.rpc(
                "consumir_convite",
                {"p_token_hash": token_hash, "p_usuario_id": usuario_id, "p_nome": nome},
            ).execute(),
            repetir_lock=True,
        )
        r = res.data[0] if isinstance(res.data, list) else res.data
        return Perfil(r["id"], r["nome"], r["unidade"], r["role"], bool(r["ativo"]))

    def listar_veiculos_admin(self, *, ativo: bool | None) -> list[dict[str, Any]]:
        def consulta() -> Any:
            q = self._c.table("veiculos").select(COLUNAS_VEICULO_ADMIN)
            if ativo is not None:
                q = q.eq("ativo", ativo)
            return q.order("descricao").order("id").execute()

        return [_so(r, _SET_VEICULO) for r in _exec(consulta).data]

    def inserir_veiculo(self, dados: dict[str, Any], *, ator_id: str) -> dict[str, Any]:
        res = _exec(
            lambda: self._c.table("veiculos").insert({**dados, "alterado_por": ator_id}).execute(),
            repetir_lock=True,
            admin=True,
        )
        return _so(res.data[0], _SET_VEICULO)

    def atualizar_veiculo(
        self, veiculo_id: str, campos: dict[str, Any], *, ator_id: str
    ) -> dict[str, Any] | None:
        res = _exec(
            lambda: self._c.table("veiculos")
            .update({**campos, "alterado_por": ator_id})
            .eq("id", veiculo_id)
            .execute(),
            repetir_lock=True,
            admin=True,
        )
        return _so(res.data[0], _SET_VEICULO) if res.data else None

    def atualizar_configuracoes(self, campos: dict[str, bool], *, ator_id: str) -> Configuracoes:
        res = _exec(
            lambda: self._c.table("configuracoes")
            .update({**campos, "alterado_por": ator_id})
            .eq("id", True)
            .execute(),
            repetir_lock=True,
            admin=True,
        )
        if not res.data:
            raise ErroInterno("Configurações indisponíveis. Tente novamente.")
        r = res.data[0]
        return Configuracoes(
            bool(r["quilometragem"]),
            bool(r["abastecimento_manutencao"]),
            bool(r["checklist"]),
            bool(r["mfa"]),
        )

    def registrar_auditoria(
        self, *, tabela: str, registro_id: str | None, ator_id: str, detalhes: dict[str, Any]
    ) -> None:
        _exec(
            lambda: self._c.rpc(
                "registrar_auditoria",
                {
                    "p_tabela": tabela,
                    "p_registro_id": registro_id,
                    "p_acao": "acao_administrativa",
                    "p_ator_id": ator_id,
                    "p_detalhes": detalhes,
                },
            ).execute(),
            repetir_lock=True,
            admin=True,
        )

    # -- histórico de acessos --------------------------------------------------------------
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
        try:
            res = self._c.rpc(
                "registrar_acesso_login",
                {
                    "p_usuario": usuario_id,
                    "p_session": session_id,
                    "p_ocorrido_em": ocorrido_em,
                    "p_metodo": metodo,
                    "p_aal": aal,
                    "p_ip": ip_mascarado,
                    "p_ua": ua_familia,
                },
            ).execute()
        except APIError as e:
            # Só o código: a mensagem/detalhe do banco pode repetir dado pessoal.
            raise FalhaRegistroAcesso(str(getattr(e, "code", "") or "desconhecido")[:16]) from None
        except Exception as e:  # rede, timeout etc.
            raise FalhaRegistroAcesso(type(e).__name__) from None
        return res.data is True

    def listar_acessos_login(
        self,
        *,
        usuario_id: str | None,
        de: str | None,
        ate: str | None,
        limite: int,
        offset: int,
    ) -> dict[str, Any]:
        res = _exec(
            lambda: self._c.rpc(
                "listar_acessos_login",
                {
                    "p_usuario": usuario_id,
                    "p_de": de,
                    "p_ate": ate,
                    "p_limite": limite,
                    "p_offset": offset,
                },
            ).execute(),
            admin=True,
        )
        dados = res.data
        if not isinstance(dados, dict) or not isinstance(dados.get("itens"), list):
            log.error("erro_banco resposta_acessos_invalida")
            raise ErroInterno("Serviço de dados indisponível. Tente novamente.")
        return {
            "total": int(dados.get("total") or 0),
            "limite": int(dados.get("limite") or limite),
            "offset": int(dados.get("offset") or 0),
            # Lista fechada: nada além disso (nem session_id) sai do repositório.
            "itens": [_so(i, _SET_ACESSO) for i in dados["itens"] if isinstance(i, dict)],
        }

    def ultimo_acesso_por_usuario(self, usuario_ids: list[str]) -> dict[str, str]:
        if not usuario_ids:
            return {}
        res = _exec(
            lambda: self._c.rpc("ultimo_acesso_por_usuario", {"p_usuarios": usuario_ids}).execute(),
            admin=True,
        )
        linhas = res.data if isinstance(res.data, list) else []
        return {
            str(r["usuario_id"]): str(r["ultimo_acesso_em"])
            for r in linhas
            if isinstance(r, dict) and r.get("usuario_id") and r.get("ultimo_acesso_em")
        }

    # -- foto de perfil (migrations 25a e 25c) ------------------------------------------------
    @staticmethod
    def _caminho_anterior(data: Any) -> str | None:
        """RPCs `returns table (id_anterior, caminho_anterior)`: lista de 1 linha (NULLs = nada)."""
        linha = data[0] if isinstance(data, list) and data else data
        if not isinstance(linha, dict):
            return None
        caminho = linha.get("caminho_anterior")
        return str(caminho) if caminho else None

    def trocar_foto_perfil(
        self, *, usuario_id: str, foto_id: str, mime: str, tamanho: int
    ) -> str | None:
        params = {"p_usuario": usuario_id, "p_id": foto_id, "p_mime": mime, "p_tamanho": tamanho}
        res = _exec(lambda: self._c.rpc("trocar_foto_perfil", params).execute(), repetir_lock=True)
        return self._caminho_anterior(res.data)

    def remover_foto_perfil_por(self, *, usuario_id: str, ator_id: str) -> str | None:
        params = {"p_usuario": usuario_id, "p_ator": ator_id}
        res = _exec(
            lambda: self._c.rpc("remover_foto_perfil_por", params).execute(),
            repetir_lock=True,
            admin=ator_id != usuario_id,
        )
        return self._caminho_anterior(res.data)

    def fotos_ativas(self, usuario_ids: list[str]) -> dict[str, str]:
        saida: dict[str, str] = {}
        for i in range(0, len(usuario_ids), LOTE_IN):  # URL curta: ids em blocos
            bloco = usuario_ids[i : i + LOTE_IN]
            res = _exec(
                lambda b=bloco: self._c.table("perfil_fotos")  # type: ignore[misc]
                .select("usuario_id,caminho")
                .in_("usuario_id", b)
                .is_("removida_em", "null")
                .execute()
            )
            saida.update({str(r["usuario_id"]): str(r["caminho"]) for r in res.data})
        return saida

    def ids_ativos(self, usuario_ids: list[str]) -> set[str]:
        saida: set[str] = set()
        for i in range(0, len(usuario_ids), LOTE_IN):
            bloco = usuario_ids[i : i + LOTE_IN]
            res = _exec(
                lambda b=bloco: self._c.table("perfis")  # type: ignore[misc]
                .select("id")
                .in_("id", b)
                .eq("ativo", True)
                .execute()
            )
            saida.update(str(r["id"]) for r in res.data)
        return saida
