"""Despachante de e-mail das notificações críticas e manutenção (expurgo) das notificações.

E-mail (só com EMAIL_ENABLED=true): a cada ciclo reserva um lote pequeno no banco
(`reservar_emails_notificacao`, que já reavalia a flag `notificacao_email_criticos`, perfil ativo
e idade < 24 h), resolve o e-mail do destinatário no servidor (Auth, pelo `usuario_id`) e envia
uma mensagem fixa e genérica (nunca dados da requisição). O resultado volta ao banco (`enviado` /
`falhou` / `ignorado`); o banco repete tentativas `falhou` até 3 vezes.

Garantias:
  * Notificações `sistema_falha_envio` NÃO geram e-mail: são marcadas `ignorado` (código
    `sistema`) sem envio, para evitar o laço "falha de e-mail avisa por e-mail".
  * Tipos só-sino (motorista_designado, motorista_removido, viagem_cancelada_motorista) nascem
    sem e-mail no banco; se algum chegasse à fila, seria `ignorado` (código `so_sino`).
  * Entrega "pelo menos uma vez": se o envio der certo mas gravar `enviado` falhar (3 tentativas
    curtas), a linha fica `enviando` e o banco a reentrega depois (possível e-mail repetido).
  * Teto por hora (`EMAIL_NOTIF_MAX_POR_HORA`) em JANELA DESLIZANTE EM MEMÓRIA, por processo:
    coerente com o gunicorn de 1 worker. Com 2+ workers o teto efetivo seria multiplicado (e o
    expurgo rodaria em cada um; é idempotente). O lote nunca passa do que resta do teto.
  * Expurgo: `purgar_notificacoes(90)` no máx. 1x a cada 24 h (controle em memória; a primeira
    execução ocorre ~1 min após iniciar). Roda mesmo com e-mail desligado. Só o total vai ao log.
  * Nenhuma exceção derruba o ciclo nem a thread. LGPD: nunca se registra endereço, corpo nem
    senha; o log leva só códigos curtos ou o nome do tipo da exceção.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from urllib.parse import urlsplit

from app.config import PUSH_HOSTS_PADRAO, Config, vapid_valido
from app.notificacoes.email import ConfigSmtp, EnviadorEmail, EnviadorSmtp, FalhaEmail
from app.notificacoes.push import (
    TIPOS_PUSH,
    EnviadorPush,
    EnviadorWebPush,
    FalhaPush,
    endpoint_publico,
    montar_payload,
)
from app.repositorios.auth_admin import AuthAdmin
from app.repositorios.base import Repositorio
from app.services.notificacoes import (
    CODIGOS_SEM_ALERTA,
    TIPOS_SO_SINO,
    registrar_falha_sistema,
)

log = logging.getLogger(__name__)

ASSUNTO = "Nova notificação importante no SisVeiculos AGU"
CORPO = "Você tem uma notificação importante. Acesse o sistema para ver os detalhes."
TAMANHO_LOTE = 5
JANELA_SEGUNDOS = 3600.0
RETENCAO_DIAS = 90
INTERVALO_EXPURGO = 24 * 3600.0
PRIMEIRO_EXPURGO = 60.0
TENTATIVAS_ENVIADO = 3
TAMANHO_LOTE_PUSH = 5
ESPERA_ENVIADO = 0.5


def url_do_site(cfg: Config) -> str | None:
    """Origem https do site, derivada de URL_ACEITE_CONVITE (única URL pública configurada)."""
    partes = urlsplit(cfg.url_aceite_convite)
    if partes.scheme.lower() != "https" or not partes.netloc:
        return None
    return f"https://{partes.netloc}"


class Despachante:
    def __init__(
        self,
        repo: Repositorio,
        auth_admin: AuthAdmin | None,
        config: Config,
        *,
        enviador: EnviadorEmail | None = None,
        enviador_push: EnviadorPush | None = None,
        relogio: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        self._repo = repo
        self._auth = auth_admin
        self._cfg = config
        self._enviador = enviador
        self._enviador_push = enviador_push
        self._relogio = relogio
        self._dormir = dormir
        self._proximo_expurgo = relogio() + PRIMEIRO_EXPURGO
        self._enviados: deque[float] = deque()  # instantes (monotônicos) dos envios na janela
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_push: threading.Thread | None = None

    # -- teto por hora ----------------------------------------------------------------------
    def _restante(self) -> int:
        agora = self._relogio()
        while self._enviados and agora - self._enviados[0] >= JANELA_SEGUNDOS:
            self._enviados.popleft()
        return max(0, self._cfg.email_notif_max_por_hora - len(self._enviados))

    # -- ciclo ------------------------------------------------------------------------------
    def processar_uma_vez(self) -> int:
        """Um ciclo. Devolve quantos e-mails foram enviados. Nunca levanta."""
        try:
            restante = self._restante()
            if restante <= 0:
                return 0
            itens = self._repo.reservar_emails_notificacao(min(TAMANHO_LOTE, restante))
        except Exception as e:
            log.error("notif_email_reserva_falhou tipo=%s", type(e).__name__)
            return 0
        enviados = 0
        for item in itens:
            try:
                enviados += self._tratar(item)
            except Exception as e:  # um item ruim não derruba o lote
                log.error("notif_email_item_falhou tipo=%s", type(e).__name__)
        return enviados

    def _marcar(self, nid: str, status: str, codigo: str | None = None) -> None:
        try:
            self._repo.marcar_email_notificacao(nid, status, codigo)
        except Exception as e:
            log.error("notif_email_marcar_falhou tipo=%s", type(e).__name__)

    def _falhou(self, nid: str, codigo: str) -> None:
        self._marcar(nid, "falhou", codigo)
        log.error("notif_email_falhou codigo=%s", codigo)
        if codigo not in CODIGOS_SEM_ALERTA:
            registrar_falha_sistema(self._repo, codigo)

    def _tratar(self, item: dict[str, object]) -> int:
        nid = str(item["id"])
        if item.get("tipo") == "sistema_falha_envio":
            self._marcar(nid, "ignorado", "sistema")  # sem envio: evita laço de alertas
            return 0
        if item.get("tipo") in TIPOS_SO_SINO:  # defesa extra: motorista é só sino
            self._marcar(nid, "ignorado", "so_sino")
            return 0
        email = self._email_do(str(item.get("usuario_id") or ""))
        if email is None:
            self._marcar(nid, "ignorado", "sem_email")
            return 0
        if email == "":  # Auth indisponível
            self._falhou(nid, "auth_indisponivel")
            return 0
        corpo = CORPO
        site = url_do_site(self._cfg)
        if site:
            corpo += f"\n\n{site}"
        try:
            enviador = self._enviador or EnviadorSmtp(ConfigSmtp.de(self._cfg))
            enviador.enviar(destino=email, assunto=ASSUNTO, corpo=corpo)
        except FalhaEmail as e:
            self._falhou(nid, e.codigo)
            return 0
        except Exception as e:
            log.error("notif_email_inesperado tipo=%s", type(e).__name__)
            self._falhou(nid, "desconhecido")
            return 0
        self._enviados.append(self._relogio())
        self._marcar_enviado(nid)
        return 1

    def _marcar_enviado(self, nid: str) -> None:
        """O e-mail já saiu: insiste na gravação (3 tentativas curtas) antes de só logar."""
        for tentativa in range(TENTATIVAS_ENVIADO):
            try:
                self._repo.marcar_email_notificacao(nid, "enviado", None)
                return
            except Exception as e:
                log.error("notif_email_marcar_falhou tipo=%s", type(e).__name__)
                if tentativa < TENTATIVAS_ENVIADO - 1:
                    self._dormir(ESPERA_ENVIADO)

    # -- manutenção --------------------------------------------------------------------------
    def expurgar_se_devido(self) -> int | None:
        """Expurgo de notificações antigas, no máx. 1x/24 h. Nunca levanta; log só do total."""
        if self._relogio() < self._proximo_expurgo:
            return None
        self._proximo_expurgo = self._relogio() + INTERVALO_EXPURGO
        try:
            total = self._repo.purgar_notificacoes(RETENCAO_DIAS)
        except Exception as e:
            log.error("notif_expurgo_falhou tipo=%s", type(e).__name__)
            return None
        log.info("notif_expurgo removidas=%d", total)
        try:
            self._repo.purgar_inscricoes_push()
        except Exception as e:
            log.error("push_purga_falhou tipo=%s", type(e).__name__)
        try:
            removidos = self._repo.purgar_webauthn()
            log.info("webauthn_purga removidos=%d", removidos)
        except Exception as e:
            log.error("webauthn_purga_falhou tipo=%s", type(e).__name__)
        return total

    def _email_do(self, usuario_id: str) -> str | None:
        """E-mail da conta; None = sem e-mail/conta; "" = Auth indisponível."""
        if self._auth is None:
            return ""
        try:
            conta = self._auth.obter_conta(usuario_id)
        except Exception as e:
            log.error("notif_email_auth_falhou tipo=%s", type(e).__name__)
            return ""
        return conta.email if conta is not None and conta.email else None

    # -- push -------------------------------------------------------------------------------
    def push_configurado(self) -> bool:
        c = self._cfg
        return vapid_valido(
            getattr(c, "vapid_public_key", ""),
            getattr(c, "vapid_private_key", ""),
            getattr(c, "vapid_subject", ""),
        )

    def processar_push_uma_vez(self) -> int:
        """Um ciclo de push. Devolve quantas notificações foram entregues. Nunca levanta."""
        if not self.push_configurado():
            return 0
        try:
            itens = self._repo.reservar_push_notificacao(TAMANHO_LOTE_PUSH)
        except Exception as e:
            log.error("push_reserva_falhou tipo=%s", type(e).__name__)
            return 0
        entregues = 0
        for item in itens:
            try:
                entregues += self._tratar_push(item)
            except Exception as e:  # um item ruim não derruba o lote
                log.error("push_item_falhou tipo=%s", type(e).__name__)
        return entregues

    def _marcar_push(self, nid: str, status: str, codigo: str | None = None) -> None:
        try:
            self._repo.marcar_push_notificacao(nid, status, codigo)
        except Exception as e:
            log.error("push_marcar_falhou tipo=%s", type(e).__name__)

    def _resultado_inscricao(self, iid: str, ok: bool, expirada: bool) -> None:
        try:
            self._repo.marcar_resultado_inscricao_push(iid, ok, expirada)
        except Exception as e:
            log.error("push_inscricao_marcar_falhou tipo=%s", type(e).__name__)

    def _tratar_push(self, item: dict[str, object]) -> int:
        nid = str(item["id"])
        if item.get("tipo") not in TIPOS_PUSH:
            self._marcar_push(nid, "ignorado", "tipo")
            return 0
        try:
            inscricoes = self._repo.listar_inscricoes_push(str(item.get("usuario_id") or ""))
        except Exception as e:
            log.error("push_listar_falhou tipo=%s", type(e).__name__)
            self._marcar_push(nid, "falhou", "inscricoes_indisponivel")
            return 0
        if not inscricoes:
            self._marcar_push(nid, "ignorado", "sem_inscricao")
            return 0
        payload = montar_payload(item)
        enviador = self._enviador_push or EnviadorWebPush(self._cfg)
        entregas = 0
        codigo_falha: str | None = None
        for insc in inscricoes:
            try:
                ok, codigo = self._enviar_a(enviador, insc, payload)
            except Exception as e:  # inscrição ruim nunca derruba o lote
                log.error("push_inscricao_falhou tipo=%s", type(e).__name__)
                ok, codigo = False, "desconhecido"
            if ok:
                entregas += 1
            elif codigo is not None:
                codigo_falha = codigo_falha or codigo
        if entregas:
            self._marcar_push(nid, "enviado", None)
            return 1
        if codigo_falha is None:  # só inscrições expiradas: nada a repetir
            self._marcar_push(nid, "ignorado", "sem_inscricao")
        else:
            log.error("push_falhou codigo=%s", codigo_falha)
            self._marcar_push(nid, "falhou", codigo_falha)
        return 0

    def _enviar_a(
        self, enviador: EnviadorPush, insc: dict[str, object], payload: dict[str, str]
    ) -> tuple[bool, str | None]:
        """(entregue, código de falha temporária). Expirada => (False, None)."""
        iid = str(insc["id"])
        hosts = getattr(self._cfg, "push_endpoint_hosts", None) or PUSH_HOSTS_PADRAO
        if not endpoint_publico(str(insc.get("endpoint") or ""), hosts):
            # transitório/configuração: não conta falha da inscrição (a notificação repete)
            return False, "destino_invalido"
        try:
            enviador.enviar(insc, payload)
        except FalhaPush as e:
            if e.expirada:
                self._resultado_inscricao(iid, False, True)
                return False, None
            if e.definitiva:  # 5xx, 429, timeout e rede NÃO punem a inscrição
                self._resultado_inscricao(iid, False, False)
            return False, e.codigo
        self._resultado_inscricao(iid, True, False)
        return True, None

    # -- thread -----------------------------------------------------------------------------
    def iniciar(self) -> None:
        """Thread daemon; a factory chama uma vez (fora de testes); e-mail só com EMAIL_ENABLED."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._laco, name="notificacoes-email", daemon=True)
        self._thread.start()
        if self.push_configurado() and self._thread_push is None:
            self._thread_push = threading.Thread(
                target=self._laco_push, name="notificacoes-push", daemon=True
            )
            self._thread_push.start()

    def parar(self) -> None:
        self._parar.set()

    def _laco(self) -> None:
        intervalo = float(self._cfg.email_notif_intervalo_segundos)
        while not self._parar.wait(intervalo):
            try:
                self.expurgar_se_devido()
                if self._cfg.email_enabled and self._auth is not None:
                    self.processar_uma_vez()
            except Exception as e:  # defesa extra: a thread nunca morre
                log.error("notif_email_ciclo_falhou tipo=%s", type(e).__name__)

    def _laco_push(self) -> None:
        intervalo = float(getattr(self._cfg, "push_intervalo_segundos", 15) or 15)
        while not self._parar.wait(intervalo):
            try:
                self.processar_push_uma_vez()
            except Exception as e:  # defesa extra: a thread nunca morre
                log.error("push_ciclo_falhou tipo=%s", type(e).__name__)
