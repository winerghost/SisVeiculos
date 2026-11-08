"""Foto de perfil (recurso sempre ligado e opcional): upload em 3 passos, troca e remoção.

Fluxo (molde do checklist; a imagem enviada NUNCA é servida, só a recodificada pelo servidor):
 1. `solicitar_upload`: valida {mime, tamanho}, aplica a cota de emissão e devolve a URL assinada
    de um objeto de STAGING `tmp/<usuario_id>/<id>` (id gerado aqui; o usuário vem do JWT);
 2. o navegador envia o arquivo ao staging (upsert=false);
 3. `registrar`: lê o staging do PRÓPRIO usuário, valida, recodifica (256x256, JPEG, sem
    metadados), grava em `<usuario_id>/<id>.jpg`, chama a RPC `trocar_foto_perfil` e limpa
    staging e foto anterior.

Privacidade: nada daqui loga URL, caminho, nome de arquivo nem bytes (só o tipo da falha); as
mensagens de erro são fixas e não repetem detalhe do Pillow nem do Postgres. O payload do n8n
nunca leva foto. Anti-IDOR: o dono é sempre `perfil.id` (JWT); o `id` do corpo só vale se o
staging existe sob o usuário do JWT, então ninguém registra upload alheio.

Órfãos (staging esquecido, objeto final de RPC que falhou, anterior não apagado): a limpeza é
best-effort e o expurgo periódico foi ADIADO (pendência registrada em docs/deploy-banco.md).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.errors import (
    Conflito,
    ErroApi,
    ErroInterno,
    RequisicaoInvalida,
    SemPermissao,
    SistemaOcupado,
)
from app.modelos import Perfil
from app.repositorios.armazenamento import MSG_INDISPONIVEL, assinatura_confere
from app.repositorios.armazenamento_avatares import (
    TAMANHO_MAX_BYTES,
    ArmazenamentoAvatares,
    ObjetoJaExiste,
    caminho_final,
    caminho_final_do_usuario,
    caminho_staging,
)
from app.repositorios.base import Repositorio
from app.services.checklist import LimiteEmissao
from app.services.fotos_exibicao import EXPIRA_LEITURA_S
from app.services.imagem_perfil import ImagemInvalida, recodificar
from app.services.validacao import validar_uuid

log = logging.getLogger(__name__)

MIMES = ("image/jpeg", "image/png", "image/webp")
MIME_SAIDA = "image/jpeg"
TTL_EMISSAO_S = 60 * 60  # a cota de emissões vale por hora
MAX_EMISSOES = 10

MSG_NAO_ENVIADA = "A foto não foi enviada. Envie o arquivo antes de confirmar."
MSG_INVALIDA = "O arquivo enviado não é uma imagem válida (use JPEG, PNG ou WebP)."
MSG_ANIMADA = "A foto não pode ser animada. Envie uma imagem estática (JPEG, PNG ou WebP)."
MSG_GRANDE = "A foto deve ter no máximo 1 MB."
MSG_DIVERGENTE = "A foto enviada não confere com o tipo ou o tamanho informados."
MSG_LIMITE = "Limite de envios de foto atingido. Tente novamente mais tarde."
MSG_EM_PROCESSAMENTO = "Esta foto já está sendo processada. Atualize a tela em instantes."
MSG_INATIVO = "Conta sem acesso ao sistema."

# Emissões de URL de upload por usuário (em memória do processo; ver LimiteEmissao).
EMISSOES_FOTO = LimiteEmissao(ttl=TTL_EMISSAO_S)


def _objeto(corpo: Any, permitidos: set[str]) -> dict[str, Any]:
    if not isinstance(corpo, dict):
        raise RequisicaoInvalida("Envie um objeto JSON.")
    extras = set(corpo) - permitidos
    if extras:  # não ecoa as chaves enviadas, só a contagem
        raise RequisicaoInvalida(
            f"O corpo contém {len(extras)} campo(s) não permitido(s). "
            "Envie apenas: " + ", ".join(sorted(permitidos)) + "."
        )
    return corpo


def validar_upload(corpo: Any) -> tuple[str, int]:
    c = _objeto(corpo, {"mime", "tamanho"})
    mime = c.get("mime")
    if not isinstance(mime, str) or mime not in MIMES:
        raise RequisicaoInvalida("O campo 'mime' deve ser image/jpeg, image/png ou image/webp.")
    tamanho = c.get("tamanho")
    if isinstance(tamanho, bool) or not isinstance(tamanho, int):
        raise RequisicaoInvalida("O campo 'tamanho' deve ser um número inteiro de bytes.")
    if tamanho < 1:
        raise RequisicaoInvalida("O campo 'tamanho' deve ser maior que zero.")
    if tamanho > TAMANHO_MAX_BYTES:
        raise RequisicaoInvalida(MSG_GRANDE, "foto_grande")
    return mime, tamanho


def validar_registro(corpo: Any) -> str:
    c = _objeto(corpo, {"id"})
    return validar_uuid(c.get("id"), "id")


class ServicoFotoPerfil:
    def __init__(
        self,
        repo: Repositorio,
        armazenamento: ArmazenamentoAvatares | None,
        *,
        max_emissoes: int = MAX_EMISSOES,
    ) -> None:
        self._repo = repo
        self._arm = armazenamento
        self._max_emissoes = max_emissoes

    # -- guardas / utilitários -------------------------------------------------------
    def _storage(self) -> ArmazenamentoAvatares:
        if self._arm is None:
            raise ErroInterno(MSG_INDISPONIVEL)
        return self._arm

    @staticmethod
    def _exigir_ativo(perfil: Perfil) -> None:
        # O decorador já barra conta inativa; o service não presume.
        if not perfil.ativo:
            raise SemPermissao(MSG_INATIVO, codigo="conta_inativa")

    def _apagar(self, caminho: str | None) -> None:
        """Remove um objeto do Storage sem nunca falhar (só o tipo da falha vai ao log)."""
        if not caminho or self._arm is None:
            return
        try:
            self._arm.remover(caminho)
        except Exception as e:
            log.warning("foto_remocao_falhou tipo=%s", type(e).__name__)

    def _apagar_anterior(self, usuario_id: str, caminho: str | None, manter: str | None) -> None:
        # Só apaga o que tem cara de foto final DESTE usuário (defesa em profundidade).
        if caminho and caminho != manter and caminho_final_do_usuario(usuario_id, caminho):
            self._apagar(caminho)

    # -- (1) URL assinada de upload --------------------------------------------------
    def solicitar_upload(
        self, perfil: Perfil, corpo: Any, *, escopo: str = "avatar"
    ) -> dict[str, Any]:
        """O staging fica SEMPRE sob `perfil.id` (o do JWT). `escopo` separa a cota do admin
        que envia a foto de outra conta da cota da própria foto."""
        self._exigir_ativo(perfil)
        validar_upload(corpo)  # o mime/tamanho declarados só servem para recusar cedo
        arm = self._storage()
        chave = (perfil.id, escopo)
        if not EMISSOES_FOTO.reservar(chave, self._max_emissoes):
            raise ErroApi(MSG_LIMITE, "limite_fotos", 429)
        foto_id = str(uuid.uuid4())
        try:
            assinado = arm.url_upload(caminho_staging(perfil.id, foto_id))
        except Exception:
            EMISSOES_FOTO.devolver(chave)
            raise
        return {
            "id": foto_id,
            "url_upload": assinado["url"],
            "token": assinado["token"],
        }

    # -- (3) confirmação: valida, recodifica, grava e troca ------------------------
    def registrar(self, perfil: Perfil, corpo: Any) -> dict[str, Any]:
        return self.registrar_para(perfil, perfil.id, corpo)

    def registrar_para(
        self, ator: Perfil, alvo_id: str, corpo: Any, *, via_admin: bool = False
    ) -> dict[str, Any]:
        """Registra a foto de `alvo_id`. O staging é lido do prefixo do ATOR (JWT). Com
        `via_admin`, a RPC `trocar_foto_perfil_por` confere o papel no banco (AG022).

        Defesa em profundidade: este método não depende de o chamador ter validado o papel.
        Trocar a foto de OUTRA conta exige `via_admin` e ator com role admin (revisão M1).
        """
        self._exigir_ativo(ator)
        if alvo_id != ator.id and not via_admin:
            raise SemPermissao("Você não tem permissão para esta ação.")
        if via_admin and ator.role != "admin":
            raise SemPermissao("Você não tem permissão para esta ação.")
        foto_id = validar_registro(corpo)
        arm = self._storage()
        staging = caminho_staging(ator.id, foto_id)  # sempre sob o usuário do JWT
        dados, mime = self._ler_staging(arm, staging)
        try:
            jpeg = recodificar(dados, mime)
        except ImagemInvalida as e:
            self._apagar(staging)
            if e.animada:
                raise RequisicaoInvalida(MSG_ANIMADA, "foto_invalida") from None
            raise RequisicaoInvalida(MSG_INVALIDA, "foto_invalida") from None
        final = caminho_final(alvo_id, foto_id, MIME_SAIDA)
        try:
            arm.gravar(final, jpeg, MIME_SAIDA)
        except ObjetoJaExiste:
            # Registro concorrente do mesmo id: não apaga nada, o outro pode ter vencido.
            raise Conflito(MSG_EM_PROCESSAMENTO, "foto_em_processamento") from None
        por = ator.id if via_admin else None
        anterior = self._trocar(alvo_id, foto_id, len(jpeg), final, staging, por=por)
        self._apagar(staging)
        self._apagar_anterior(alvo_id, anterior, manter=final)
        return {"foto_url": self._assinar(final)}

    def _trocar(
        self,
        usuario_id: str,
        foto_id: str,
        tamanho: int,
        final: str,
        staging: str,
        *,
        por: str | None = None,
    ) -> str | None:
        """Chama a RPC. Devolve o caminho anterior. Falha definitiva: remove final e staging;
        transitória: só o final (o staging permite repetir)."""
        try:
            if por is not None:
                return self._repo.trocar_foto_perfil_por(
                    usuario_id=usuario_id,
                    ator_id=por,
                    foto_id=foto_id,
                    mime=MIME_SAIDA,
                    tamanho=tamanho,
                )
            return self._repo.trocar_foto_perfil(
                usuario_id=usuario_id, foto_id=foto_id, mime=MIME_SAIDA, tamanho=tamanho
            )
        except ErroApi as e:
            # ErroInterno = resposta perdida: o commit pode ter ocorrido. Confere antes de
            # apagar o objeto final (apagá-lo deixaria a foto ativa apontando para o nada).
            situacao = self._situacao(usuario_id, final) if isinstance(e, ErroInterno) else "falhou"
            if situacao == "gravada":
                return None  # a troca valeu; o anterior (desconhecido) fica para o expurgo
            if situacao == "falhou":
                self._apagar(final)  # libera o caminho: o retry do mesmo id grava de novo
                if not isinstance(e, ErroInterno | SistemaOcupado):
                    self._apagar(staging)  # erro DEFINITIVO: o upload não serve mais
                # Transitório (lock, deadlock, serialização, banco fora): o staging fica para o
                # usuário repetir POST /api/eu/foto {id} sem gastar nova cota de emissão.
            raise

    def _situacao(self, usuario_id: str, final: str) -> str:
        try:
            ativa = self._repo.fotos_ativas([usuario_id]).get(usuario_id)
        except Exception:
            return "desconhecida"  # sem como saber: não apaga nada
        return "gravada" if ativa == final else "falhou"

    def _ler_staging(self, arm: ArmazenamentoAvatares, staging: str) -> tuple[bytes, str]:
        """Objeto inteiro (<= 1 MB) com MIME, tamanho e assinatura coerentes. Reprovado: apaga o
        staging e responde 400 com mensagem fixa."""
        meta = arm.metadados(staging)
        if meta is None:
            raise RequisicaoInvalida(MSG_NAO_ENVIADA, "foto_nao_enviada")
        if meta["tamanho"] > TAMANHO_MAX_BYTES:
            self._apagar(staging)
            raise RequisicaoInvalida(MSG_GRANDE, "foto_grande")
        if meta["mime"] not in MIMES or meta["tamanho"] < 1:
            self._apagar(staging)
            raise RequisicaoInvalida(MSG_DIVERGENTE, "foto_divergente")
        dados = arm.ler_tudo(staging, TAMANHO_MAX_BYTES)
        if dados is None:
            raise RequisicaoInvalida(MSG_NAO_ENVIADA, "foto_nao_enviada")
        if len(dados) > TAMANHO_MAX_BYTES:
            self._apagar(staging)
            raise RequisicaoInvalida(MSG_GRANDE, "foto_grande")
        if len(dados) != meta["tamanho"]:
            self._apagar(staging)
            raise RequisicaoInvalida(MSG_DIVERGENTE, "foto_divergente")
        if not assinatura_confere(meta["mime"], dados[:16]):
            self._apagar(staging)
            # É outra imagem aceita (tipo declarado errado) ou nem é imagem (SVG, GIF, HTML...)?
            outra = any(assinatura_confere(m, dados[:16]) for m in MIMES)
            if outra:
                raise RequisicaoInvalida(MSG_DIVERGENTE, "foto_divergente")
            raise RequisicaoInvalida(MSG_INVALIDA, "foto_invalida")
        return dados, meta["mime"]

    def _assinar(self, caminho: str) -> str | None:
        """URL de leitura da foto recém-gravada. A troca já valeu: falha aqui não a desfaz."""
        try:
            return self._storage().url_leitura(caminho, EXPIRA_LEITURA_S)
        except Exception as e:
            log.warning("foto_assinatura_falhou tipo=%s", type(e).__name__)
            return None

    # -- remoção ---------------------------------------------------------------------
    def remover(self, perfil: Perfil) -> dict[str, Any]:
        """O próprio usuário remove a sua foto. Idempotente."""
        self._exigir_ativo(perfil)
        self.remover_de(usuario_id=perfil.id, ator_id=perfil.id)
        return {"foto_url": None}

    def remover_de(self, *, usuario_id: str, ator_id: str) -> None:
        """RPC (o banco confere o ator e audita) e depois apaga o objeto, best-effort.
        Erros da RPC sobem traduzidos; falha do Storage nunca."""
        anterior = self._repo.remover_foto_perfil_por(usuario_id=usuario_id, ator_id=ator_id)
        self._apagar_anterior(usuario_id, anterior, manter=None)

    def remover_melhor_esforco(self, *, usuario_id: str, ator_id: str) -> None:
        """Usado ao desativar a conta: em chamada separada e sem nunca falhar o chamador."""
        try:
            self.remover_de(usuario_id=usuario_id, ator_id=ator_id)
        except Exception as e:
            log.warning("foto_remocao_desativacao_falhou tipo=%s", type(e).__name__)
