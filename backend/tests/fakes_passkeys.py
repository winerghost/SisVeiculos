"""Repositório em memória com as RPCs de passkeys e um autenticador de teste (criptografia REAL).

`RepoPasskeys` espelha a migration 20261122120000: desafio de uso único com validade de 5 min,
contador monotônico, limite de 10 ativas, credencial única, janela de reautenticação por sessão.
`Autenticador` gera um par ES256 de verdade e monta clientDataJSON, authenticatorData e
assinatura, para exercitar a verificação criptográfica do servidor.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from app.errors import Conflito
from tests.fakes import RepoMemoria

RP_ID = "agu.gov.br"
ORIGEM = "https://sisveiculos.agu.gov.br"
VALIDADE_DESAFIO = 300.0
VALIDADE_REAUTENTICACAO = 300.0


def b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


def de_b64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


class RepoPasskeys(RepoMemoria):
    def __init__(self) -> None:
        super().__init__()
        self._lock_pk = threading.Lock()
        self.deslocamento = 0.0  # segundos somados ao relógio do "banco"
        self.desafios: dict[str, dict[str, Any]] = {}
        self.credenciais: list[dict[str, Any]] = []
        self.reautenticacoes: list[dict[str, Any]] = []
        self.eventos: list[tuple[str, str, str]] = []

    def avancar(self, segundos: float) -> None:
        self.deslocamento += segundos

    def _agora(self) -> float:
        return datetime.now(UTC).timestamp() + self.deslocamento

    # -- desafios -----------------------------------------------------------------------------
    def criar_desafio_webauthn(self, usuario_id: str, tipo: str) -> tuple[str, str]:
        with self._lock_pk:
            for d in self.desafios.values():  # invalida os pendentes do mesmo usuário/tipo
                if d["usuario"] == usuario_id and d["tipo"] == tipo:
                    d["usado"] = True
            did, valor = str(uuid.uuid4()), b64(secrets.token_bytes(32))
            self.desafios[did] = {
                "usuario": usuario_id,
                "tipo": tipo,
                "desafio": valor,
                "expira": self._agora() + VALIDADE_DESAFIO,
                "usado": False,
            }
            return did, valor

    def consumir_desafio_webauthn(self, desafio_id: str, usuario_id: str, tipo: str) -> str | None:
        with self._lock_pk:  # atômico, como o UPDATE ... RETURNING do banco
            d = self.desafios.get(desafio_id)
            if (
                d is None
                or d["usuario"] != usuario_id
                or d["tipo"] != tipo
                or d["usado"]
                or d["expira"] <= self._agora()
            ):
                return None
            d["usado"] = True
            return str(d["desafio"])

    # -- credenciais --------------------------------------------------------------------------
    def registrar_credencial_webauthn(
        self,
        usuario_id: str,
        credential_id: str,
        chave_publica: str,
        contador: int,
        transportes: list[str] | None,
        apelido: str | None,
    ) -> str:
        with self._lock_pk:
            if any(c["credential_id"] == credential_id for c in self.credenciais):
                raise Conflito("Esta passkey já está cadastrada.", "passkey_ja_cadastrada")
            ativas = [c for c in self.credenciais if c["usuario_id"] == usuario_id and not c["rev"]]
            if len(ativas) >= 10:
                raise Conflito("Limite de passkeys.", "passkey_limite")
            linha = {
                "id": str(uuid.uuid4()),
                "usuario_id": usuario_id,
                "credential_id": credential_id,
                "chave_publica": chave_publica,
                "contador": contador,
                "transportes": transportes,
                "apelido": apelido,
                "criada_em": datetime.now(UTC).isoformat(),
                "ultimo_uso_em": None,
                "rev": False,
            }
            self.credenciais.append(linha)
            return str(linha["id"])

    def credenciais_ativas_webauthn(self, usuario_id: str) -> list[dict[str, Any]]:
        return [
            {k: c[k] for k in ("id", "credential_id", "transportes", "apelido", "criada_em")}
            | {"ultimo_uso_em": c["ultimo_uso_em"]}
            for c in self.credenciais
            if c["usuario_id"] == usuario_id and not c["rev"]
        ]

    def buscar_credencial_webauthn(
        self, usuario_id: str, credential_id: str
    ) -> dict[str, Any] | None:
        for c in self.credenciais:
            if (
                c["usuario_id"] == usuario_id
                and c["credential_id"] == credential_id
                and not c["rev"]
            ):
                return {k: c[k] for k in ("id", "chave_publica", "contador")}
        return None

    def atualizar_contador_webauthn(self, credencial_id: str, novo: int) -> bool:
        with self._lock_pk:
            for c in self.credenciais:
                if c["id"] == credencial_id and not c["rev"]:
                    if novo > c["contador"] or (novo == 0 and c["contador"] == 0):
                        c["contador"] = novo
                        c["ultimo_uso_em"] = datetime.now(UTC).isoformat()
                        return True
                    return False
            return False

    def revogar_credencial_webauthn(self, usuario_id: str, credencial_id: str) -> bool:
        with self._lock_pk:
            for c in self.credenciais:
                if c["id"] == credencial_id and c["usuario_id"] == usuario_id and not c["rev"]:
                    c["rev"] = True
                    return True
            return False

    # -- reautenticação e eventos -------------------------------------------------------------
    def registrar_reautenticacao_webauthn(self, usuario_id: str, sessao: str) -> str:
        ate = self._agora() + VALIDADE_REAUTENTICACAO
        self.reautenticacoes.append({"usuario": usuario_id, "sessao": sessao, "ate": ate})
        return datetime.fromtimestamp(ate, UTC).isoformat()

    def reautenticacao_valida_webauthn(self, usuario_id: str, sessao: str) -> bool:
        return any(
            r["usuario"] == usuario_id and r["sessao"] == sessao and r["ate"] > self._agora()
            for r in self.reautenticacoes
        )

    def registrar_evento_webauthn(self, usuario_id: str, evento: str, codigo: str) -> None:
        self.eventos.append((usuario_id, evento, codigo))

    def purgar_webauthn(self) -> int:
        return 0


class Autenticador:
    """Autenticador de software: par ES256 real; devolve o JSON de `PublicKeyCredential`."""

    def __init__(self) -> None:
        self.chave = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.contador = 0

    @property
    def id(self) -> str:
        return b64(self.credential_id)

    def _cose(self) -> bytes:
        n = self.chave.public_key().public_numbers()
        return cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")}
        )

    @staticmethod
    def _dados_cliente(tipo: str, desafio: str, origem: str, cross_origin: bool = False) -> bytes:
        return json.dumps(
            {"type": tipo, "challenge": desafio, "origin": origem, "crossOrigin": cross_origin}
        ).encode()

    def criar(
        self,
        opcoes: dict[str, Any],
        *,
        origem: str = ORIGEM,
        rp_id: str = RP_ID,
        uv: bool = True,
        cross_origin: bool = False,
        desafio: str | None = None,
    ) -> dict[str, Any]:
        flags = 0x01 | (0x04 if uv else 0) | 0x40
        dados_auth = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([flags])
            + self.contador.to_bytes(4, "big")
            + bytes(16)
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + self._cose()
        )
        atestado = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": dados_auth})
        cliente = self._dados_cliente(
            "webauthn.create", desafio or opcoes["challenge"], origem, cross_origin
        )
        return {
            "id": self.id,
            "rawId": self.id,
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64(cliente),
                "attestationObject": b64(atestado),
                "transports": ["internal", "hybrid"],
            },
        }

    def afirmar(
        self,
        opcoes: dict[str, Any],
        *,
        contador: int | None = None,
        origem: str = ORIGEM,
        rp_id: str = RP_ID,
        uv: bool = True,
        chave: Any = None,
        user_handle: bytes | None = None,
        desafio: str | None = None,
        credential_id: str | None = None,
    ) -> dict[str, Any]:
        self.contador = self.contador + 1 if contador is None else contador
        flags = 0x01 | (0x04 if uv else 0)
        dados_auth = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([flags])
            + self.contador.to_bytes(4, "big")
        )
        cliente = self._dados_cliente("webauthn.get", desafio or opcoes["challenge"], origem)
        assinatura = (chave or self.chave).sign(
            dados_auth + hashlib.sha256(cliente).digest(), ec.ECDSA(hashes.SHA256())
        )
        resposta: dict[str, Any] = {
            "clientDataJSON": b64(cliente),
            "authenticatorData": b64(dados_auth),
            "signature": b64(assinatura),
        }
        if user_handle is not None:
            resposta["userHandle"] = b64(user_handle)
        cid = credential_id or self.id
        return {
            "id": cid,
            "rawId": cid,
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": resposta,
        }
