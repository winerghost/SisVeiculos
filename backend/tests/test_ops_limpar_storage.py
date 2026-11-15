"""Limpeza do Storage (app/ops/limpar_storage.py) com Storage e banco falsos."""

from __future__ import annotations

import io
from typing import Any

import pytest

from app.ops import limpar_storage as ls

URL = "https://abcdefghijklmnopqrst.supabase.co"
REF = "abcdefghijklmnopqrst"
FRASE = f"APAGAR ARQUIVOS {REF}"
SEGREDO = "chave-service-role-super-secreta"

ADMIN = "11111111-1111-1111-1111-111111111111"
USUARIO = "22222222-2222-2222-2222-222222222222"
FOTO_ADMIN = f"{ADMIN}/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.jpg"
FOTO_USUARIO = f"{USUARIO}/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb.png"
ORFA = f"{USUARIO}/cccccccc-cccc-cccc-cccc-cccccccccccc.webp"
STAGING = f"tmp/{ADMIN}/dddddddd-dddd-dddd-dddd-dddddddddddd"
# Foto já trocada: tem linha em perfil_fotos (removida_em preenchido), mas não é a vigente.
TROCADA = f"{ADMIN}/eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee.jpg"


class StorageFalso:
    """Simula a listagem do Storage: por pasta, itens com `id` nulo para subpastas."""

    def __init__(self, buckets: dict[str, list[str]]) -> None:
        self.arquivos = {b: set(c) for b, c in buckets.items()}
        self.chamadas_listar: list[tuple[str, str, int, int]] = []
        self.remocoes: list[tuple[str, list[str]]] = []
        self.falhar_remocao = False

    def listar_buckets(self) -> list[str]:
        return list(self.arquivos)

    def listar(self, bucket: str, prefixo: str, limite: int, offset: int) -> list[dict[str, Any]]:
        self.chamadas_listar.append((bucket, prefixo, limite, offset))
        base = f"{prefixo}/" if prefixo else ""
        itens: dict[str, dict[str, Any]] = {}
        for c in self.arquivos[bucket]:
            if not c.startswith(base):
                continue
            resto = c[len(base) :]
            nome, _, sub = resto.partition("/")
            itens[nome] = {"name": nome, "id": None} if sub else {"name": nome, "id": "x"}
        ordenados = [itens[k] for k in sorted(itens)]
        return ordenados[offset : offset + limite]

    def remover(self, bucket: str, caminhos: list[str]) -> None:
        if self.falhar_remocao:
            raise RuntimeError(f"erro com caminho {caminhos[0]} e https://x/sign?token=abc")
        assert len(caminhos) <= ls.TAMANHO_LOTE_REMOCAO
        self.remocoes.append((bucket, list(caminhos)))
        self.arquivos[bucket] -= set(caminhos)


class BancoFalso:
    def __init__(
        self,
        *,
        lacrado: bool | Exception = False,
        fotos: dict[str, bool] | None = None,
        admins: set[str] | None = None,
    ) -> None:
        self.lacrado = lacrado
        # caminho -> vigente (removida_em IS NULL); False = linha de foto trocada/removida
        self.fotos = (
            fotos if fotos is not None else {FOTO_ADMIN: True, FOTO_USUARIO: True, TROCADA: False}
        )
        self.admins = admins if admins is not None else {ADMIN}

    def banco_lacrado(self) -> bool:
        if isinstance(self.lacrado, Exception):
            raise self.lacrado
        return self.lacrado

    def caminhos_fotos_vigentes(self) -> set[str]:
        return {c for c, vigente in self.fotos.items() if vigente}

    def admins_ativos(self) -> set[str]:
        return set(self.admins)


def _storage() -> StorageFalso:
    return StorageFalso(
        {
            "checklists": [
                "r1/saida/f1.jpg",
                "r1/chegada/f2.png",
                "r2/saida/f3.webp",
                f"r2/saida/{ls.PLACEHOLDER_PASTA}",
            ],
            "avatares": [FOTO_ADMIN, FOTO_USUARIO, ORFA, STAGING, TROCADA],
            "outro": ["qualquer/coisa.txt"],
        }
    )


def _rodar(
    argv: list[str],
    storage: StorageFalso,
    banco: BancoFalso | None = None,
    entrada: str = "",
) -> tuple[int, str]:
    saida = io.StringIO()
    codigo = ls.rodar(
        argv,
        cliente=storage,
        banco=banco or BancoFalso(),
        supabase_url=URL,
        entrada=lambda: entrada,
        saida=saida,
    )
    return codigo, saida.getvalue()


def _apagados(storage: StorageFalso, bucket: str) -> set[str]:
    return {c for b, lote in storage.remocoes if b == bucket for c in lote}


# ----------------------------------------------------------------------------- regras puras


def test_ref_do_projeto() -> None:
    assert ls.ref_do_projeto(URL) == REF
    assert ls.ref_do_projeto("http://127.0.0.1:54321") == "127.0.0.1"
    with pytest.raises(ls.Recusado):
        ls.ref_do_projeto("")


def test_dono_do_caminho() -> None:
    assert ls.dono_do_caminho(FOTO_USUARIO) == USUARIO
    assert ls.dono_do_caminho(STAGING) == ADMIN
    assert ls.dono_do_caminho("solto.jpg") is None
    assert ls.dono_do_caminho("tmp/x") is None


def test_paginacao_lista_todos_os_arquivos() -> None:
    nomes = [f"r/saida/{i:04d}.jpg" for i in range(ls.TAMANHO_PAGINA * 2 + 7)]
    storage = StorageFalso({"checklists": nomes})
    assert sorted(ls.listar_arquivos(storage, "checklists")) == sorted(nomes)
    offsets = [o for _, p, _, o in storage.chamadas_listar if p == "r/saida"]
    assert offsets == [0, ls.TAMANHO_PAGINA, ls.TAMANHO_PAGINA * 2]


def test_remocao_em_lotes() -> None:
    nomes = [f"r/saida/{i:04d}.jpg" for i in range(ls.TAMANHO_LOTE_REMOCAO * 2 + 1)]
    storage = StorageFalso({"checklists": nomes})
    codigo, _ = _rodar(["--executar", "--confirmacao", FRASE], storage)
    assert codigo == ls.SAIDA_OK
    assert len(storage.remocoes) == 3
    assert storage.arquivos["checklists"] == set()


# ----------------------------------------------------------------------------- ensaio e frase


def test_ensaio_nao_apaga_e_mostra_contagens() -> None:
    storage = _storage()
    codigo, out = _rodar([], storage)
    assert codigo == ls.SAIDA_OK
    assert storage.remocoes == []
    assert "ENSAIO" in out
    assert "checklists: 3 arquivo(s); apagar 3; manter 0" in out
    assert "avatares: 5 arquivo(s); apagar 3; manter 2" in out
    assert "outro: 1 arquivo(s); bucket fora da limpeza" in out
    assert FRASE in out


def test_frase_errada_recusa() -> None:
    storage = _storage()
    codigo, out = _rodar(["--executar", "--confirmacao", "APAGAR ARQUIVOS outro"], storage)
    assert codigo == ls.SAIDA_RECUSADO
    assert storage.remocoes == []
    assert "RECUSADO" in out


def test_frase_vazia_no_stdin_recusa() -> None:
    storage = _storage()
    codigo, _ = _rodar(["--executar"], storage, entrada="")
    assert codigo == ls.SAIDA_RECUSADO
    assert storage.remocoes == []


def test_frase_pelo_stdin_executa() -> None:
    storage = _storage()
    codigo, _ = _rodar(["--executar"], storage, entrada=FRASE + "\n")
    assert codigo == ls.SAIDA_OK
    assert storage.remocoes


# ----------------------------------------------------------------------------- modos


def test_manter_usuarios_fica_so_a_foto_vigente_de_cada_perfil() -> None:
    storage = _storage()
    codigo, out = _rodar(["--executar", "--confirmacao", FRASE], storage)
    assert codigo == ls.SAIDA_OK, out
    assert storage.arquivos["checklists"] == {f"r2/saida/{ls.PLACEHOLDER_PASTA}"}
    assert _apagados(storage, "avatares") == {ORFA, STAGING, TROCADA}
    assert storage.arquivos["avatares"] == {FOTO_ADMIN, FOTO_USUARIO}
    assert "CONCLUÍDO" in out


def test_apagar_usuarios_mantem_so_fotos_de_admin_ativo() -> None:
    storage = _storage()
    codigo, _ = _rodar(["--executar", "--apagar-usuarios", "--confirmacao", FRASE], storage)
    assert codigo == ls.SAIDA_OK
    assert storage.arquivos["avatares"] == {FOTO_ADMIN}
    assert _apagados(storage, "avatares") == {FOTO_USUARIO, ORFA, STAGING, TROCADA}


def test_apagar_usuarios_foto_do_admin_nao_vigente_e_apagada() -> None:
    storage = _storage()
    banco = BancoFalso(fotos={FOTO_USUARIO: True, FOTO_ADMIN: False})
    codigo, _ = _rodar(["--executar", "--apagar-usuarios", "--confirmacao", FRASE], storage, banco)
    assert codigo == ls.SAIDA_OK
    assert storage.arquivos["avatares"] == set()


def test_outro_bucket_intocado() -> None:
    storage = _storage()
    _rodar(["--executar", "--apagar-usuarios", "--confirmacao", FRASE], storage)
    assert storage.arquivos["outro"] == {"qualquer/coisa.txt"}
    assert all(b != "outro" for b, _ in storage.remocoes)


def test_executar_plano_ignora_bucket_nao_gerenciado() -> None:
    storage = _storage()
    plano = ls.PlanoBucket("outro", 1, ["qualquer/coisa.txt"], gerenciado=False)
    ls.executar_plano(storage, [plano])
    assert storage.remocoes == []


# ----------------------------------------------------------------------------- lacre


def test_lacrado_recusa_ensaio_e_execucao() -> None:
    for argv in ([], ["--executar", "--confirmacao", FRASE]):
        storage = _storage()
        codigo, out = _rodar(argv, storage, BancoFalso(lacrado=True))
        assert codigo == ls.SAIDA_RECUSADO
        assert "LACRADO" in out
        assert storage.remocoes == []


def test_lacre_desconhecido_avisa_no_ensaio_e_recusa_execucao() -> None:
    storage = _storage()
    codigo, out = _rodar([], storage, BancoFalso(lacrado=RuntimeError(SEGREDO)))
    assert codigo == ls.SAIDA_OK
    assert "AVISO" in out
    assert SEGREDO not in out

    codigo, out = _rodar(
        ["--executar", "--confirmacao", FRASE], storage, BancoFalso(lacrado=RuntimeError("x"))
    )
    assert codigo == ls.SAIDA_RECUSADO
    assert storage.remocoes == []


# ----------------------------------------------------------------------------- saída segura


def test_saida_sem_segredos_urls_ou_caminhos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", SEGREDO)
    storage = _storage()
    textos = [
        _rodar([], storage)[1],
        _rodar(["--executar", "--apagar-usuarios", "--confirmacao", FRASE], storage)[1],
    ]
    falha = _storage()
    falha.falhar_remocao = True
    codigo, out_falha = _rodar(["--executar", "--confirmacao", FRASE], falha)
    assert codigo == ls.SAIDA_FALHA
    textos.append(out_falha)
    for out in textos:
        assert SEGREDO not in out
        assert "http" not in out
        assert "token" not in out
        for proibido in (ADMIN, USUARIO, "r1/", "tmp/", "qualquer/coisa"):
            assert proibido not in out


def test_adaptador_real_le_lacre_e_pagina_banco() -> None:
    class Resp:
        def __init__(self, data: Any) -> None:
            self.data = data

        def execute(self) -> Resp:
            return self

    class Consulta:
        def __init__(self, linhas: list[dict[str, Any]], filtros: list[Any]) -> None:
            self.linhas, self.filtros = linhas, filtros

        def select(self, *_: Any) -> Consulta:
            return self

        def eq(self, coluna: str, valor: Any) -> Consulta:
            self.filtros.append((coluna, valor))
            return self

        def is_(self, coluna: str, valor: Any) -> Consulta:
            self.filtros.append(("is", coluna, valor))
            return self

        def order(self, *_: Any) -> Consulta:
            return self

        def range(self, a: int, b: int) -> Resp:
            return Resp(self.linhas[a : b + 1])

    filtros: list[Any] = []
    linhas = [{"caminho": f"u/{i}.jpg"} for i in range(ls.TAMANHO_PAGINA_BANCO + 5)]

    class Cliente:
        def rpc(self, nome: str) -> Resp:
            assert nome == ls.RPC_LACRE
            return Resp(True)

        def table(self, nome: str) -> Consulta:
            if nome == "perfil_fotos":
                return Consulta(linhas, filtros)
            return Consulta([{"id": ADMIN}], filtros)

    fonte = ls.SupabaseFonteBanco(Cliente())
    assert fonte.banco_lacrado() is True
    assert len(fonte.caminhos_fotos_vigentes()) == ls.TAMANHO_PAGINA_BANCO + 5
    assert ("is", "removida_em", "null") in filtros
    assert fonte.admins_ativos() == {ADMIN}
    assert ("role", "admin") in filtros and ("ativo", True) in filtros

    class ClienteRuim(Cliente):
        def rpc(self, nome: str) -> Resp:
            return Resp(None)

    with pytest.raises(ls.LacreDesconhecido):
        ls.SupabaseFonteBanco(ClienteRuim()).banco_lacrado()
