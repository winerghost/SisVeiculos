"""Cliente HTTP (httpx) compartilhado pelos clientes Supabase do backend.

O supabase-py cria, por padrão, um httpx com `http2=True`. Com o gunicorn de vida longa, a
conexão HTTP/2 ociosa é fechada pelo outro lado (Cloudflare/Supabase) e a primeira requisição
após o ócio falha antes de chegar ao Supabase ("Serviço de dados indisponível"; o F5 resolve
porque abre conexão nova). Aqui: HTTP/1.1 e keep-alive curto, abaixo do ócio do servidor,
para a conexão ser descartada pelo nosso lado antes de ficar "meio fechada".

Com `httpx_client` em `ClientOptions`, o supabase-py ignora `postgrest_client_timeout` e
`storage_client_timeout`: o timeout passa a ser o do próprio httpx.Client.
"""

from __future__ import annotations

import httpx
from supabase import Client, ClientOptions, create_client

KEEPALIVE_EXPIRY = 20.0  # segundos que uma conexão ociosa fica no pool


def cliente_http(timeout: float) -> httpx.Client:
    """httpx.Client síncrono, só HTTP/1.1, com keep-alive curto e sem seguir redirect.

    Sem redirect: o cliente leva a `service_role` no header `apikey`, que o httpx NÃO remove num
    redirect para outra origem (só o `Authorization`), e baixa URLs assinadas com token na
    query. A API do Supabase não redireciona; um 3xx vira erro em vez de levar a chave adiante.
    """
    return httpx.Client(
        http2=False,
        timeout=timeout,
        follow_redirects=False,
        # Limites padrão do httpx (100/20): `Limits(...)` sem eles deixaria o pool ilimitado.
        limits=httpx.Limits(
            max_connections=100, max_keepalive_connections=20, keepalive_expiry=KEEPALIVE_EXPIRY
        ),
    )


def criar_cliente_supabase(url: str, chave: str, *, timeout: float) -> Client:
    """`create_client` com o httpx acima (PostgREST, Storage, Auth e Functions)."""
    return create_client(url, chave, options=ClientOptions(httpx_client=cliente_http(timeout)))
