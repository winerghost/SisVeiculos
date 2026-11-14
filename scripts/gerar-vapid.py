"""Gera um par de chaves VAPID (P-256) para o Web Push.

Uso: python scripts/gerar-vapid.py

Imprime no terminal e NUNCA grava arquivo. Copie os valores direto para o .env da VPS
(fora do git). A chave privada é segredo: não cole em chat, issue, log ou commit.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode("ascii")


def main() -> None:
    privada = ec.generate_private_key(ec.SECP256R1())
    numero = privada.private_numbers().private_value.to_bytes(32, "big")
    publica = privada.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    print("# Cole no .env da VPS (fora do git). A privada é SEGREDO.")
    print(f"VAPID_PUBLIC_KEY={_b64url(publica)}")
    print(f"VAPID_PRIVATE_KEY={_b64url(numero)}")
    print("VAPID_SUBJECT=mailto:CONTATO@SEU-DOMINIO")


if __name__ == "__main__":
    main()
