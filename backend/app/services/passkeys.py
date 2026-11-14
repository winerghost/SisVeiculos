"""Passkeys (WebAuthn), 1ª etapa: reautenticação e fator adicional. Regra só aqui, nunca nas rotas.

Princípios:
- O Supabase Auth é o único emissor de JWT. Este módulo NUNCA emite token nem sessão: a passkey só
  prova presença/identidade e grava uma reautenticação de 5 min ligada ao `session_id` do JWT.
- O usuário vem sempre do JWT. Nada que o front informe (resultado, usuário, RP ID) é confiado:
  toda verificação é criptográfica (py_webauthn) contra desafio, origem e RP ID do servidor.
- Nenhum dado biométrico existe no fluxo: o autenticador devolve só uma asserção assinada.
- LGPD: credential_id, chave pública, desafio, origem e apelido nunca vão a log nem a mensagens
  de erro (fixas). Falhas são auditadas só com códigos curtos.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, options_to_json_dict
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.config import Config
from app.errors import (
    Conflito,
    NaoAutenticado,
    NaoEncontrado,
    RecursoDesligado,
    RequisicaoInvalida,
    SemPermissao,
)
from app.modelos import Perfil
from app.repositorios.base import Repositorio

log = logging.getLogger(__name__)

MSG_DESLIGADO = "As passkeys estão desligadas nas Configurações."
MSG_INVALIDA = "Não foi possível validar a passkey."
MSG_DESAFIO = "Desafio inválido ou expirado. Inicie o processo novamente."
MSG_CORPO = "Dados da passkey inválidos."
MSG_NAO_ENCONTRADA = "Passkey não encontrada."
MSG_SEM_PASSKEY = "Nenhuma passkey cadastrada."
MSG_REAUTENTICAR = "Confirme sua identidade com uma passkey para continuar."
MSG_LOGIN_RECENTE = "Por segurança, entre novamente para cadastrar sua primeira passkey."

COD_REAUTENTICACAO = "reautenticacao_requerida"
MAX_CREDENCIAIS = 10  # espelha o limite do banco (AG052)
LOGIN_RECENTE_SEGUNDOS = 600
TOLERANCIA_RELOGIO = 60  # segundos de folga para relógio adiantado no `iat`/`timestamp`
TIMEOUT_MS = 60_000
MAX_APELIDO = 40

# Tamanhos máximos (caracteres base64url) de cada campo recebido.
MAX_ID = 1024  # também o teto do CHECK do banco
MAX_CLIENT_DATA = 2048
MAX_ATTESTATION = 6144
MAX_AUTH_DATA = 1024
MAX_ASSINATURA = 1024
MAX_USER_HANDLE = 128

TRANSPORTES = frozenset({"usb", "nfc", "ble", "internal", "hybrid"})
ALGORITMOS = [
    COSEAlgorithmIdentifier.ECDSA_SHA_256,
    COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
]
_RE_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_RE_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")

Relogio = Callable[[], float]


# -- validação de entrada (sem eco de valores) -------------------------------------------------
def _b64url_estrito(valor: Any, max_chars: int) -> bytes | None:
    """Base64url canônico, sem padding nem caracteres fora do alfabeto; senão None.

    O decodificador da biblioteca descarta caracteres inválidos em silêncio, então a checagem
    estrita é feita aqui, antes de qualquer decodificação."""
    if not isinstance(valor, str) or not valor or len(valor) > max_chars:
        return None
    if not _RE_B64URL.fullmatch(valor) or len(valor) % 4 == 1:
        return None
    bruto = base64url_to_bytes(valor)
    return bruto if bytes_to_base64url(bruto) == valor else None


def _objeto(corpo: Any, obrigatorios: set[str], opcionais: set[str]) -> dict[str, Any]:
    """Objeto JSON com os campos exatos; mensagem fixa (nunca cita nomes nem valores)."""
    if not isinstance(corpo, dict):
        raise RequisicaoInvalida(MSG_CORPO, "passkey_invalida")
    chaves = set(corpo)
    if not obrigatorios <= chaves or not chaves <= obrigatorios | opcionais:
        raise RequisicaoInvalida(MSG_CORPO, "passkey_invalida")
    return corpo


def validar_apelido(valor: Any) -> str | None:
    """Apelido opcional: texto de 1 a 40 caracteres, sem controle; None/vazio = sem apelido."""
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise RequisicaoInvalida("O apelido da passkey é inválido.", "passkey_apelido_invalido")
    texto = valor.strip()
    if not texto:
        return None
    if len(texto) > MAX_APELIDO or _RE_CONTROLE.search(texto):
        raise RequisicaoInvalida("O apelido da passkey é inválido.", "passkey_apelido_invalido")
    return texto


def _uuid(valor: Any) -> str | None:
    if not isinstance(valor, str) or len(valor) != 36:
        return None
    try:
        return str(uuid.UUID(valor))
    except ValueError:
        return None


def _transportes(valor: Any) -> list[str]:
    """Só transportes conhecidos (dica do cliente; o banco aceita até 5, sem repetição)."""
    if not isinstance(valor, list):
        return []
    vistos: list[str] = []
    for t in valor[:10]:
        if isinstance(t, str) and t in TRANSPORTES and t not in vistos:
            vistos.append(t)
    return vistos


def _credencial_base(corpo: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Valida o envelope `PublicKeyCredential` (JSON do navegador). Devolve (id, response)."""
    if not isinstance(corpo, dict) or not isinstance(corpo.get("response"), dict):
        raise RequisicaoInvalida(MSG_CORPO, "passkey_invalida")
    if corpo.get("type") != "public-key":
        raise RequisicaoInvalida(MSG_CORPO, "passkey_invalida")
    cid, raw = corpo.get("id"), corpo.get("rawId")
    if _b64url_estrito(cid, MAX_ID) is None or cid != raw:
        raise RequisicaoInvalida(MSG_CORPO, "passkey_invalida")
    return corpo, corpo["response"]


def _campo(resposta: dict[str, Any], nome: str, max_chars: int) -> str:
    valor = resposta.get(nome)
    if _b64url_estrito(valor, max_chars) is None:
        raise RequisicaoInvalida(MSG_CORPO, "passkey_invalida")
    return str(valor)


def sanear_credencial_registro(corpo: Any) -> tuple[dict[str, Any], list[str]]:
    """Credencial de cadastro só com campos conhecidos e validados: (credencial, transportes)."""
    cred, resp = _credencial_base(corpo)
    transportes = _transportes(resp.get("transports"))
    limpa = {
        "id": cred["id"],
        "rawId": cred["rawId"],
        "type": "public-key",
        "response": {
            "clientDataJSON": _campo(resp, "clientDataJSON", MAX_CLIENT_DATA),
            "attestationObject": _campo(resp, "attestationObject", MAX_ATTESTATION),
            "transports": transportes,
        },
    }
    return limpa, transportes


def sanear_credencial_autenticacao(corpo: Any) -> dict[str, Any]:
    """Credencial de asserção só com campos conhecidos e validados."""
    cred, resp = _credencial_base(corpo)
    resposta: dict[str, Any] = {
        "clientDataJSON": _campo(resp, "clientDataJSON", MAX_CLIENT_DATA),
        "authenticatorData": _campo(resp, "authenticatorData", MAX_AUTH_DATA),
        "signature": _campo(resp, "signature", MAX_ASSINATURA),
    }
    if resp.get("userHandle") not in (None, ""):
        resposta["userHandle"] = _campo(resp, "userHandle", MAX_USER_HANDLE)
    return {"id": cred["id"], "rawId": cred["rawId"], "type": "public-key", "response": resposta}


def _numero(valor: Any) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return float(valor)


def codigo_falha(exc: Exception) -> str:
    """Código curto e fixo (auditoria) a partir do tipo de falha da biblioteca. Nunca devolve
    nem registra a mensagem da exceção, que pode citar origem e dados da credencial."""
    msg = str(exc).lower()
    for trecho, codigo in (
        ("rp id", "rp_id_invalido"),
        ("challenge", "desafio_divergente"),
        ("origin", "origem_invalida"),
        ("signature", "assinatura_invalida"),
        ("user verification", "uv_ausente"),
        ("user presence", "presenca_ausente"),
        ("user was not present", "presenca_ausente"),
        ("user was not verified", "uv_ausente"),
        ("malformed", "resposta_malformada"),
    ):
        if trecho in msg:
            return codigo
    return "resposta_invalida"


class ServicoPasskeys:
    def __init__(self, repo: Repositorio, config: Config, relogio: Relogio = time.time) -> None:
        self._repo = repo
        self._config = config
        self._relogio = relogio

    # -- pré-condições ------------------------------------------------------------------------
    def _exigir_ligado(self) -> None:
        """Flag ligada E WEBAUTHN_* coerentes; senão o recurso é recusado como desligado."""
        if not self._repo.ler_configuracoes().passkeys or not self._config.webauthn_configurado():
            raise RecursoDesligado(MSG_DESLIGADO)

    def _evento(self, perfil: Perfil, evento: str, codigo: str) -> None:
        """Auditoria best-effort de falha (só códigos curtos); nunca derruba nem vaza."""
        try:
            self._repo.registrar_evento_webauthn(perfil.id, evento, codigo)
        except Exception as e:
            log.warning("passkey_evento_falhou tipo=%s", type(e).__name__)

    def _recusar(
        self, perfil: Perfil, codigo: str, evento: str = "passkey_falha"
    ) -> RequisicaoInvalida:
        self._evento(perfil, evento, codigo)
        log.info("passkey_recusada codigo=%s", codigo)
        return RequisicaoInvalida(MSG_INVALIDA, "passkey_invalida")

    def _desafio_invalido(self, perfil: Perfil) -> RequisicaoInvalida:
        self._evento(perfil, "passkey_desafio_invalido", "desafio_invalido")
        log.info("passkey_recusada codigo=desafio_invalido")
        return RequisicaoInvalida(MSG_DESAFIO, "desafio_invalido")

    @staticmethod
    def _sessao(claims: dict[str, Any]) -> str:
        sessao = claims.get("session_id")
        if not isinstance(sessao, str) or not 1 <= len(sessao) <= 128:
            raise NaoAutenticado("Sessão inválida ou expirada. Faça login novamente.")
        return sessao

    def _recente(self, instante: float | None) -> bool:
        if instante is None:
            return False
        idade = self._relogio() - instante
        return -TOLERANCIA_RELOGIO <= idade <= LOGIN_RECENTE_SEGUNDOS

    def login_recente(self, claims: dict[str, Any]) -> bool:
        """Login nos últimos 10 min: `iat` recente E o último `timestamp` de `amr` (o momento
        real da autenticação; o `iat` sozinho é renovado a cada refresh do token). Falha
        fechado: `amr` ausente, vazio ou malformado = não é login recente."""
        if not self._recente(_numero(claims.get("iat"))):
            return False
        amr = claims.get("amr")
        if not isinstance(amr, list):
            return False
        marcas = [
            m
            for item in amr
            if isinstance(item, dict) and (m := _numero(item.get("timestamp"))) is not None
        ]
        return bool(marcas) and self._recente(max(marcas))

    def _identidade_ok(
        self, perfil: Perfil, claims: dict[str, Any], ativas: list[dict[str, Any]]
    ) -> bool:
        if ativas:  # quem já tem passkey só se reautentica por passkey (sessão do JWT)
            sessao = claims.get("session_id")
            if not isinstance(sessao, str) or not 1 <= len(sessao) <= 128:
                return False
            return self._repo.reautenticacao_valida_webauthn(perfil.id, sessao)
        return self.login_recente(claims)

    def _exigir_identidade(
        self, perfil: Perfil, claims: dict[str, Any], ativas: list[dict[str, Any]]
    ) -> None:
        if not self._identidade_ok(perfil, claims, ativas):
            raise SemPermissao(
                MSG_REAUTENTICAR if ativas else MSG_LOGIN_RECENTE, COD_REAUTENTICACAO
            )

    # -- desafios e opções ---------------------------------------------------------------------
    def _novo_desafio(self, perfil: Perfil, tipo: str) -> tuple[str, bytes]:
        desafio_id, desafio = self._repo.criar_desafio_webauthn(perfil.id, tipo)
        return desafio_id, base64url_to_bytes(desafio)

    def opcoes_registro(self, perfil: Perfil, claims: dict[str, Any], corpo: Any) -> dict[str, Any]:
        self._exigir_ligado()
        dados = _objeto(corpo if corpo is not None else {}, set(), {"apelido"})
        apelido = validar_apelido(dados.get("apelido"))
        ativas = self._repo.credenciais_ativas_webauthn(perfil.id)
        self._exigir_identidade(perfil, claims, ativas)
        if len(ativas) >= MAX_CREDENCIAIS:
            raise Conflito(
                "Você atingiu o limite de 10 passkeys ativas. Revogue uma para cadastrar outra.",
                "passkey_limite",
            )
        desafio_id, desafio = self._novo_desafio(perfil, "registro")
        nome = apelido or "Passkey do SisVeiculos"  # sem dado pessoal: o aparelho guarda este nome
        opcoes = generate_registration_options(
            rp_id=self._config.webauthn_rp_id,
            rp_name=self._config.webauthn_rp_name,
            user_name=nome,
            user_display_name=nome,
            user_id=uuid.UUID(perfil.id).bytes,
            challenge=desafio,
            timeout=TIMEOUT_MS,
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["credential_id"]))
                for c in ativas
            ],
            supported_pub_key_algs=ALGORITMOS,
        )
        return {"id_desafio": desafio_id, "opcoes": options_to_json_dict(opcoes)}

    def opcoes_autenticacao(self, perfil: Perfil) -> dict[str, Any]:
        self._exigir_ligado()
        ativas = self._repo.credenciais_ativas_webauthn(perfil.id)
        if not ativas:
            raise NaoEncontrado(MSG_SEM_PASSKEY, "passkey_inexistente")
        desafio_id, desafio = self._novo_desafio(perfil, "autenticacao")
        opcoes = generate_authentication_options(
            rp_id=self._config.webauthn_rp_id,
            challenge=desafio,
            timeout=TIMEOUT_MS,
            allow_credentials=[
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["credential_id"]))
                for c in ativas
            ],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        return {"id_desafio": desafio_id, "opcoes": options_to_json_dict(opcoes)}

    # -- confirmações ---------------------------------------------------------------------------
    def _client_data_ok(self, credencial: dict[str, Any]) -> str | None:
        """Pré-checagem da origem (para auditar o motivo): `clientDataJSON` da allowlist, sem
        `crossOrigin`. A biblioteca confere de novo tipo, desafio e origem."""
        bruto = base64url_to_bytes(credencial["response"]["clientDataJSON"])
        try:
            dados = json.loads(bruto)
        except (ValueError, UnicodeDecodeError):
            return "resposta_malformada"
        if not isinstance(dados, dict):
            return "resposta_malformada"
        if dados.get("crossOrigin"):
            return "origem_cruzada"
        origem = dados.get("origin")
        if not isinstance(origem, str) or origem not in self._config.origens_webauthn():
            return "origem_invalida"
        return None

    def _consumir(self, perfil: Perfil, corpo: dict[str, Any], tipo: str) -> bytes:
        desafio_id = _uuid(corpo.get("id_desafio"))
        if desafio_id is None:
            raise RequisicaoInvalida(MSG_CORPO, "passkey_invalida")
        desafio = self._repo.consumir_desafio_webauthn(desafio_id, perfil.id, tipo)
        if desafio is None:  # inexistente, expirado, já usado, de outro usuário ou tipo
            raise self._desafio_invalido(perfil)
        return base64url_to_bytes(desafio)

    def confirmar_registro(
        self, perfil: Perfil, claims: dict[str, Any], corpo: Any
    ) -> dict[str, Any]:
        self._exigir_ligado()
        dados = _objeto(corpo, {"id_desafio", "credencial"}, {"apelido"})
        apelido = validar_apelido(dados.get("apelido"))
        credencial, transportes = sanear_credencial_registro(dados["credencial"])
        # Identidade conferida de novo na confirmação (além da emissão do desafio).
        self._exigir_identidade(perfil, claims, self._repo.credenciais_ativas_webauthn(perfil.id))
        desafio = self._consumir(perfil, dados, "registro")
        motivo = self._client_data_ok(credencial)
        if motivo:
            raise self._recusar(perfil, motivo)
        try:
            verificado = verify_registration_response(
                credential=credencial,
                expected_challenge=desafio,
                expected_rp_id=self._config.webauthn_rp_id,
                expected_origin=self._config.origens_webauthn(),
                require_user_presence=True,
                require_user_verification=True,
                supported_pub_key_algs=ALGORITMOS,
            )
        except Exception as e:  # qualquer falha de verificação = recusa
            raise self._recusar(perfil, codigo_falha(e)) from None
        credential_id = bytes_to_base64url(verificado.credential_id)
        if credential_id != credencial["id"] or not verificado.user_verified:
            raise self._recusar(perfil, "credencial_divergente")
        novo_id = self._repo.registrar_credencial_webauthn(
            perfil.id,
            credential_id,
            bytes_to_base64url(verificado.credential_public_key),
            int(verificado.sign_count),
            transportes or None,
            apelido,
        )
        return {"id": novo_id, "apelido": apelido, "transportes": transportes}

    def confirmar_autenticacao(
        self, perfil: Perfil, claims: dict[str, Any], corpo: Any
    ) -> dict[str, Any]:
        self._exigir_ligado()
        dados = _objeto(corpo, {"id_desafio", "credencial"}, set())
        credencial = sanear_credencial_autenticacao(dados["credencial"])
        sessao = self._sessao(claims)
        desafio = self._consumir(perfil, dados, "autenticacao")
        motivo = self._client_data_ok(credencial)
        if motivo:
            raise self._recusar(perfil, motivo)
        # Associação ao dono: a busca é por (usuário do JWT, credential_id) e só devolve ativas.
        cred = self._repo.buscar_credencial_webauthn(perfil.id, credencial["id"])
        if cred is None:  # inexistente, revogada ou de outro usuário: mesma resposta
            raise self._recusar(perfil, "credencial_desconhecida")
        handle = credencial["response"].get("userHandle")
        if handle is not None and base64url_to_bytes(handle) != uuid.UUID(perfil.id).bytes:
            raise self._recusar(perfil, "usuario_divergente")
        atual = int(cred["contador"])
        try:
            # Contador conferido abaixo (0 aqui): assim só uma asserção com ASSINATURA válida
            # chega à checagem de regressão, e o evento de clone não é forjável sem a chave.
            verificado = verify_authentication_response(
                credential=credencial,
                expected_challenge=desafio,
                expected_rp_id=self._config.webauthn_rp_id,
                expected_origin=self._config.origens_webauthn(),
                credential_public_key=base64url_to_bytes(cred["chave_publica"]),
                credential_current_sign_count=0,
                require_user_verification=True,
            )
        except Exception as e:  # qualquer falha de verificação = recusa
            raise self._recusar(perfil, codigo_falha(e)) from None
        novo = int(verificado.new_sign_count)
        regrediu = (novo > 0 or atual > 0) and novo <= atual
        if regrediu or not self._repo.atualizar_contador_webauthn(str(cred["id"]), novo):
            raise self._recusar(perfil, "contador_regressivo", "passkey_regressao_contador")
        ate = self._repo.registrar_reautenticacao_webauthn(perfil.id, sessao)
        return {"reautenticado_ate": ate}

    # -- listagem e revogação -------------------------------------------------------------------
    def listar(self, perfil: Perfil, claims: dict[str, Any]) -> dict[str, Any]:
        self._exigir_ligado()
        ativas = self._repo.credenciais_ativas_webauthn(perfil.id)
        itens = [
            {
                "id": str(c["id"]),
                "apelido": c.get("apelido"),
                "criada_em": c.get("criada_em"),
                "ultimo_uso_em": c.get("ultimo_uso_em"),
                "transportes": list(c.get("transportes") or []),
                "acoes_permitidas": ["revogar"],
            }
            for c in ativas
        ]
        acoes = ["cadastrar"] if len(ativas) < MAX_CREDENCIAIS else []
        if ativas:
            acoes.append("reautenticar")
        return {
            "itens": itens,
            "limite": MAX_CREDENCIAIS,
            "exige_passkey": bool(ativas),
            "identidade_confirmada": self._identidade_ok(perfil, claims, ativas),
            "acoes_permitidas": acoes,
        }

    def revogar(self, perfil: Perfil, claims: dict[str, Any], credencial_id: str) -> None:
        self._exigir_ligado()
        alvo = _uuid(credencial_id)
        ativas = self._repo.credenciais_ativas_webauthn(perfil.id)
        # Alheia, inexistente, revogada e id malformado: a mesma resposta, antes de qualquer outra
        # checagem (nada vaza sobre credenciais de terceiros).
        if alvo is None or alvo not in {str(c["id"]) for c in ativas}:
            raise NaoEncontrado(MSG_NAO_ENCONTRADA, "passkey_nao_encontrada")
        self._exigir_identidade(perfil, claims, ativas)
        if not self._repo.revogar_credencial_webauthn(perfil.id, alvo):
            raise NaoEncontrado(MSG_NAO_ENCONTRADA, "passkey_nao_encontrada")
