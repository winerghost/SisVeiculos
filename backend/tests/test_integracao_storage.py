"""Upload único no Storage REAL (URL assinada sem upsert). Ignorado por padrão.

Guarda técnica (tests/guarda_banco.py): só roda contra Supabase LOCAL e descartável (host
127.0.0.1/localhost) e com CONTRATO_CONFIRM=descartavel; URL remota é
recusada (teste pulado):

    RUN_INTEGRATION=1 CONTRATO_CONFIRM=descartavel \
    SUPABASE_URL=http://127.0.0.1:54321 SUPABASE_SERVICE_ROLE_KEY=<chave local> \
    python -m pytest -m integracao tests/test_integracao_storage.py

Cenário (M2): a URL de upload é criada com `x-upsert: false` (storage3 2.34.1). Um primeiro
PUT com o token cria o objeto (200). Um SEGUNDO PUT no mesmo caminho, mesmo com o token ainda
dentro do prazo, deve ser recusado pelo Storage com 409 (objeto já existe), então ninguém
sobrescreve uma foto já enviada. O teste usa um UUID aleatório sob uma pasta de teste e
remove o objeto ao final.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

from app.repositorios.armazenamento import SupabaseArmazenamento
from tests.guarda_banco import avaliar, marca_pular

_LIBERACAO = avaliar("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")

pytestmark = [
    pytest.mark.integracao,
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="integração desligada"),
    marca_pular(_LIBERACAO),
]

PNG = bytes([0x89]) + b"PNG" + bytes([0x0D, 0x0A, 0x1A, 0x0A]) + bytes(8)


def test_segundo_put_no_mesmo_caminho_da_409():
    arm = SupabaseArmazenamento(_LIBERACAO.url, _LIBERACAO.chave)
    caminho = f"teste-integracao/{uuid.uuid4()}/{uuid.uuid4()}.png"
    try:
        up = arm.url_upload(caminho)
        cab = {"Content-Type": "image/png"}
        # O contrato exato do PUT assinado (corpo/cabeçalhos) é o do SDK `upload_to_signed_url`;
        # aqui o importante é o status de cada tentativa.
        primeiro = httpx.put(up["url"], content=PNG, headers=cab, timeout=20)
        assert primeiro.status_code in {200, 201}
        segundo = httpx.put(up["url"], content=PNG + b"x", headers=cab, timeout=20)
        assert segundo.status_code == 409
    finally:
        arm.remover(caminho)
