from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app import create_app
from app.auth.jwt import VerificadorJWT
from app.config import Config
from app.extensions import limiter
from app.services.admin_usuarios import INDICE_EMAILS
from app.services.checklist import EM_REGISTRO, EMISSOES, SANEADAS
from app.services.convites import CONTROLE
from app.services.foto_perfil import EMISSOES_FOTO
from tests.fakes import RepoMemoria
from tests.fakes_admin import AuthAdminMemoria
from tests.fakes_checklist import ArmazenamentoMemoria
from tests.fakes_foto import ArmazenamentoAvataresMemoria

ISSUER = "https://projeto.supabase.co/auth/v1"
AUDIENCE = "authenticated"

_CHAVE = ec.generate_private_key(ec.SECP256R1())
_OUTRA = ec.generate_private_key(ec.SECP256R1())


def emitir_token(
    sub: str,
    *,
    chave: Any = None,
    iss: str = ISSUER,
    aud: str = AUDIENCE,
    exp_em: int = 3600,
    alg: str = "ES256",
    extra: dict[str, Any] | None = None,
) -> str:
    agora = int(time.time())
    claims = {
        "sub": sub,
        "iss": iss,
        "aud": aud,
        "iat": agora,
        "exp": agora + exp_em,
        **(extra or {}),
    }
    return jwt.encode(claims, chave or _CHAVE, algorithm=alg, headers={"kid": "k1"})


@pytest.fixture
def chave_errada() -> Any:
    return _OUTRA


@pytest.fixture
def repo() -> RepoMemoria:
    return RepoMemoria()


@pytest.fixture
def storage() -> ArmazenamentoMemoria:
    return ArmazenamentoMemoria()


@pytest.fixture
def avatares() -> ArmazenamentoAvataresMemoria:
    return ArmazenamentoAvataresMemoria()


@pytest.fixture
def auth_admin(repo: RepoMemoria) -> AuthAdminMemoria:
    return AuthAdminMemoria(repo)


URL_ACEITE = "https://app.exemplo.gov.br/aceitar-convite"


@pytest.fixture
def app(  # type: ignore[no-untyped-def]
    repo: RepoMemoria,
    auth_admin: AuthAdminMemoria,
    storage: ArmazenamentoMemoria,
    avatares: ArmazenamentoAvataresMemoria,
):
    cfg = Config(
        jwt_issuer=ISSUER, jwt_audience=AUDIENCE, testing=True, url_aceite_convite=URL_ACEITE
    )
    verificador = VerificadorJWT(
        issuer=ISSUER, audience=AUDIENCE, resolvedor_chave=lambda _t: _CHAVE.public_key()
    )
    aplicacao = create_app(
        cfg,
        repo=repo,
        verificador=verificador,
        auth_admin=auth_admin,
        armazenamento=storage,
        armazenamento_avatares=avatares,
    )
    limiter.reset()
    CONTROLE.reiniciar()
    EMISSOES.reiniciar()
    SANEADAS.reiniciar()
    EM_REGISTRO.reiniciar()
    EMISSOES_FOTO.reiniciar()
    INDICE_EMAILS.reiniciar()
    return aplicacao


@pytest.fixture
def client(app):  # type: ignore[no-untyped-def]
    return app.test_client()


@pytest.fixture
def auth() -> Callable[[str], dict[str, str]]:
    return lambda sub: {"Authorization": f"Bearer {emitir_token(sub)}"}


@pytest.fixture
def auth_aal2() -> Callable[[str], dict[str, str]]:
    """Sessão com segundo fator (TOTP), como o Supabase emite após verificar o MFA."""
    extra = {"aal": "aal2", "amr": [{"method": "totp", "timestamp": 1}]}
    return lambda sub: {"Authorization": f"Bearer {emitir_token(sub, extra=extra)}"}


@pytest.fixture
def adm(repo: RepoMemoria):  # type: ignore[no-untyped-def]
    """Um administrador ativo (o primeiro admin nasce por seed no banco real)."""
    return repo.novo_perfil("admin")


@pytest.fixture
def c(repo: RepoMemoria):  # type: ignore[no-untyped-def]
    class C:
        sol = repo.novo_perfil("solicitante")
        outro = repo.novo_perfil("solicitante")
        aut = repo.novo_perfil("autorizador")
        adm = repo.novo_perfil("admin")
        veiculo = repo.novo_veiculo()

    return C
