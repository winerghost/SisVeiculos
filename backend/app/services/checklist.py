"""Recurso opcional Checklist: URL assinada de upload, registro atômico e leitura.

Regras (CLAUDE.md, "Recursos opcionais"): o estado do recurso vem de `configuracoes.checklist`
(fonte da verdade). Desligado, os endpoints recusam com `recurso_desligado`. Ligado, o checklist
é obrigatório na saída e na chegada (a exigência na transição está em `ServicoRequisicoes`).

Quem vê o quê (anti-IDOR): registrar e ver checklist é só de autorizador/admin. O solicitante
dono NÃO vê o checklist (o CLAUDE.md lhe dá só criar/ver/cancelar a própria requisição; o
checklist traz fotos do veículo e texto livre que pode conter dado pessoal).
Contrato (não mudar sem alinhar com o frontend): nos POSTs (upload e registro) o decorador de
papel responde 403 a qualquer solicitante, dono ou alheio, antes de consultar a requisição; no
GET, o solicitante alheio recebe 404 (como nas demais rotas) e o dono recebe 403.

LGPD: `descricao_avarias` nunca vai para log, auditoria ou n8n; este módulo não loga nada.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from app.errors import (
    ChecklistForaDaJanela,
    Conflito,
    ErroInterno,
    RecursoDesligado,
    RequisicaoInvalida,
    SemPermissao,
)
from app.modelos import Perfil, Requisicao
from app.notificacoes import Notificador, NotificadorNulo
from app.repositorios.armazenamento import (
    MSG_INDISPONIVEL,
    Armazenamento,
    assinatura_confere,
    caminho_foto,
)
from app.repositorios.base import Repositorio
from app.services.requisicoes import STAFF, ServicoRequisicoes
from app.services.validacao_checklist import validar_momento, validar_registro, validar_upload

EXPIRA_LEITURA_S = 300  # URL de leitura: 5 minutos
# Janela de cada momento (espelha o trigger AG014 do banco).
ESTADO_POR_MOMENTO = {"saida": "autorizada", "chegada": "em_uso"}
# Teto de objetos por pasta no Storage: 6 fotos + sobra para uploads abandonados/reenvios.
MAX_OBJETOS_PASTA = 12

# Validade do token de upload do Storage (~2 h): a contagem de emissões usa o mesmo prazo.
TTL_EMISSAO_S = 2 * 60 * 60
MAX_URLS_UPLOAD = 12

MSG_DESLIGADO = "O recurso Checklist está desligado. Peça ao administrador para habilitá-lo."
MSG_JA_REGISTRADO = "Já existe checklist registrado para este momento."


class LimiteEmissao:
    """Conta URLs de upload emitidas por (requisição, momento), em memória do processo.

    O teto de objetos na pasta só vê o que já foi enviado; um staff poderia pedir muitos tokens
    antes de enviar. Cada emissão vale `ttl` segundos (o prazo do token). Sem PII: a chave é
    só (id da requisição, momento). Com vários workers do gunicorn o teto vale por processo
    (limite efetivo = teto x workers); o teto de objetos na pasta segue valendo para todos.
    """

    def __init__(self, ttl: int = TTL_EMISSAO_S) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._emissoes: dict[tuple[str, str], list[float]] = {}

    def reservar(self, chave: tuple[str, str], maximo: int) -> bool:
        """Registra uma emissão se ainda houver cota; False se o teto foi atingido."""
        agora = time.monotonic()
        with self._lock:
            for k in [k for k, v in self._emissoes.items() if v[-1] <= agora - self._ttl]:
                del self._emissoes[k]  # expurga chaves cujas emissões todas expiraram
            vigentes = [t for t in self._emissoes.get(chave, []) if t > agora - self._ttl]
            if len(vigentes) >= maximo:
                self._emissoes[chave] = vigentes
                return False
            vigentes.append(agora)
            self._emissoes[chave] = vigentes
            return True

    def devolver(self, chave: tuple[str, str]) -> None:
        """Desfaz a última reserva (a emissão da URL falhou)."""
        with self._lock:
            lista = self._emissoes.get(chave)
            if lista:
                lista.pop()
                if not lista:
                    del self._emissoes[chave]

    def reiniciar(self) -> None:
        with self._lock:
            self._emissoes.clear()


EMISSOES = LimiteEmissao()


class ServicoChecklist:
    def __init__(
        self,
        repo: Repositorio,
        armazenamento: Armazenamento | None,
        requisicoes: ServicoRequisicoes,
        max_urls_upload: int = MAX_URLS_UPLOAD,
        notificador: Notificador | None = None,
    ) -> None:
        self._notificador: Notificador = notificador or NotificadorNulo()
        self._repo = repo
        self._arm = armazenamento
        self._requisicoes = requisicoes
        self._max_urls = max_urls_upload

    # -- guardas ---------------------------------------------------------------------
    def _storage(self) -> Armazenamento:
        if self._arm is None:
            raise ErroInterno(MSG_INDISPONIVEL)
        return self._arm

    def _exigir_ligado(self) -> None:
        if not self._repo.ler_configuracoes().checklist:
            raise RecursoDesligado(MSG_DESLIGADO)

    def _requisicao_operador(
        self, perfil: Perfil, requisicao_id: str, *, escrita: bool
    ) -> Requisicao:
        """Staff, ou o motorista da própria viagem (escrita só com `motorista_registra`)."""
        req = self._requisicoes.obter(perfil, requisicao_id)  # 404 para requisição alheia
        if perfil.role in STAFF:
            return req
        if perfil.role == "motorista" and not (
            escrita and not self._repo.ler_configuracoes().motorista_registra
        ):
            return req  # `obter` já garantiu: viagem dele e recurso Motorista ligado
        raise SemPermissao("Você não tem permissão para esta ação.")

    @staticmethod
    def _exigir_janela(req: Requisicao, momento: str) -> None:
        if req["status"] != ESTADO_POR_MOMENTO[momento]:
            rotulo, quando = (
                ("saída", "autorizada") if momento == "saida" else ("chegada", "em uso")
            )
            raise ChecklistForaDaJanela(
                f"O checklist de {rotulo} só pode ser registrado com a requisição {quando}."
            )

    def _preparar_escrita(
        self, perfil: Perfil, requisicao_id: str, momento: str
    ) -> tuple[Requisicao, str]:
        self._exigir_ligado()
        momento = validar_momento(momento)
        req = self._requisicao_operador(perfil, requisicao_id, escrita=True)
        self._exigir_janela(req, momento)
        if momento in self._repo.momentos_checklist({req["id"]}).get(req["id"], set()):
            raise Conflito(MSG_JA_REGISTRADO, "checklist_ja_registrado")
        return req, momento

    # -- (a) URL assinada de upload --------------------------------------------------
    def solicitar_upload(
        self, perfil: Perfil, requisicao_id: str, momento: str, corpo: Any
    ) -> dict[str, Any]:
        req, momento = self._preparar_escrita(perfil, requisicao_id, momento)
        dados = validar_upload(corpo)
        arm = self._storage()
        if arm.contar_objetos(f"{req['id']}/{momento}") >= MAX_OBJETOS_PASTA:
            raise RequisicaoInvalida(
                "Limite de envios de fotos deste checklist atingido.", "limite_fotos"
            )
        chave = (req["id"], momento)
        if not EMISSOES.reservar(chave, self._max_urls):
            raise RequisicaoInvalida(
                "Limite de envios de fotos deste checklist atingido.", "limite_fotos"
            )
        foto_id = str(uuid.uuid4())
        caminho = caminho_foto(req["id"], momento, foto_id, dados["mime"])
        try:
            assinado = arm.url_upload(caminho)
        except Exception:
            EMISSOES.devolver(chave)
            raise
        return {
            "foto_id": foto_id,
            "mime": dados["mime"],
            "caminho": caminho,
            "url_upload": assinado["url"],
            "token": assinado["token"],
        }

    # -- (c) registro ----------------------------------------------------------------
    def registrar(
        self, perfil: Perfil, requisicao_id: str, momento: str, corpo: Any
    ) -> dict[str, Any]:
        req, momento = self._preparar_escrita(perfil, requisicao_id, momento)
        dados = validar_registro(corpo)
        self._conferir_fotos(req["id"], momento, dados["fotos"])
        checklist_id = self._repo.registrar_checklist(
            requisicao_id=req["id"],
            momento=momento,
            combustivel=dados["combustivel"],
            avarias=dados["avarias"],
            descricao_avarias=dados["descricao_avarias"],
            registrado_por=perfil.id,
            fotos=[
                {"id": f["id"], "mime_type": f["mime"], "tamanho_bytes": f["tamanho"]}
                for f in dados["fotos"]
            ],
        )
        atual = self._requisicoes.obter(perfil, req["id"])
        self._notificador.notificar("checklist.registrado", atual)  # só após o banco confirmar
        return {
            "checklist": {
                "id": checklist_id,
                "momento": momento,
                "combustivel": dados["combustivel"],
                "avarias": dados["avarias"],
                "fotos": len(dados["fotos"]),
            },
            "requisicao": self._requisicoes.apresentar(perfil, atual),
        }

    def _conferir_fotos(
        self, requisicao_id: str, momento: str, fotos: list[dict[str, Any]]
    ) -> None:
        """Cada foto declarada precisa existir no Storage com o MIME e o tamanho declarados."""
        if not fotos:
            return
        arm = self._storage()
        for pos, f in enumerate(fotos, start=1):
            caminho = caminho_foto(requisicao_id, momento, f["id"], f["mime"])
            meta = arm.metadados(caminho)
            if meta is None:
                raise RequisicaoInvalida(
                    f"A foto {pos} não foi enviada. Envie o arquivo antes de registrar.",
                    "foto_nao_enviada",
                )
            inicio = None
            if meta["mime"] == f["mime"] and meta["tamanho"] == f["tamanho"]:
                inicio = arm.ler_inicio(caminho, 16)
                if inicio is not None and assinatura_confere(f["mime"], inicio):
                    continue
            # Reprovada (tipo, tamanho ou assinatura): remove o objeto para não deixar lixo,
            # exceto se outro registro concorrente já o tornou parte de um checklist.
            if not self._foto_em_uso(requisicao_id, momento, f["id"]):
                arm.remover(caminho)
            if inicio is not None:
                raise RequisicaoInvalida(
                    f"A foto {pos} não é uma imagem válida do tipo declarado.",
                    "foto_invalida",
                )
            if meta["mime"] != f["mime"] or meta["tamanho"] != f["tamanho"]:
                raise RequisicaoInvalida(
                    f"A foto {pos} não confere com o arquivo enviado (tipo ou tamanho).",
                    "foto_divergente",
                )

    def _foto_em_uso(self, requisicao_id: str, momento: str, foto_id: str) -> bool:
        """Relê o banco: o momento já tem checklist ou a foto já consta em checklist_fotos.

        Numa corrida, dois registros podem declarar os mesmos ids; o que perdeu a corrida não
        pode apagar o objeto que o vencedor acabou de registrar. Resta uma janela mínima entre
        esta leitura e a remoção; objetos órfãos são tratados pelo expurgo (pendência M4).
        """
        if momento in self._repo.momentos_checklist({requisicao_id}).get(requisicao_id, set()):
            return True
        return any(
            f["id"] == foto_id
            for c in self._repo.obter_checklists(requisicao_id)
            for f in c.get("fotos", [])
        )

    # -- (d) leitura -----------------------------------------------------------------
    def listar(self, perfil: Perfil, requisicao_id: str) -> dict[str, Any]:
        self._exigir_ligado()
        req = self._requisicao_operador(perfil, requisicao_id, escrita=False)
        checklists = self._repo.obter_checklists(req["id"])
        nomes = self._repo.nomes_perfis({c["registrado_por"] for c in checklists})
        por_momento: dict[str, Any] = {"saida": None, "chegada": None}
        for c in checklists:
            por_momento[c["momento"]] = self._apresentar(req["id"], c, nomes)
        return {
            "requisicao_id": req["id"],
            "checklists": por_momento,
            "url_expira_em_segundos": EXPIRA_LEITURA_S,
        }

    def _apresentar(self, requisicao_id: str, c: dict[str, Any], nomes: dict[str, str]) -> Any:
        prefixo = f"{requisicao_id}/{c['momento']}/"
        fotos = []
        for f in c.get("fotos", []):
            caminho = f.get("caminho") or ""
            # Defesa em profundidade: só assina caminho da própria requisição e momento.
            url = (
                self._storage().url_leitura(caminho, EXPIRA_LEITURA_S)
                if caminho.startswith(prefixo)
                else None
            )
            fotos.append(
                {
                    "id": f["id"],
                    "posicao": f["posicao"],
                    "mime": f["mime_type"],
                    "tamanho": f["tamanho_bytes"],
                    "url": url,
                }
            )
        return {
            "id": c["id"],
            "momento": c["momento"],
            "combustivel": c["combustivel"],
            "avarias": c["avarias"],
            "descricao_avarias": c.get("descricao_avarias"),
            "registrado_por_nome": nomes.get(c["registrado_por"]),
            "registrado_em": c["registrado_em"],
            "fotos": fotos,
        }
