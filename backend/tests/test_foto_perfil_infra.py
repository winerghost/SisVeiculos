"""Infra da foto de perfil: repositório real sobre PostgREST (mock), tradução de erros do banco,
retry de lock e adaptador real do Storage (bucket `avatares`)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from postgrest.exceptions import APIError

from app.errors import (
    Conflito,
    ErroInterno,
    RequisicaoInvalida,
    SemPermissao,
    SistemaOcupado,
)
from app.repositorios import supabase_repo
from app.repositorios.armazenamento_avatares import (
    BUCKET_AVATARES,
    ObjetoJaExiste,
    SupabaseArmazenamentoAvatares,
)
from app.repositorios.supabase_repo import SupabaseRepositorio, traduzir_erro_banco
from app.services.fotos_exibicao import FotosExibicao
from tests.fakes_foto import novo_id


def api_erro(codigo: str, message: str = "") -> APIError:
    return APIError({"message": message, "code": codigo, "details": "", "hint": ""})


@pytest.fixture
def sem_espera(monkeypatch):  # type: ignore[no-untyped-def]
    esperas: list[float] = []
    monkeypatch.setattr(supabase_repo, "_dormir", esperas.append)
    return esperas


def repo_mock() -> tuple[SupabaseRepositorio, MagicMock]:
    r = SupabaseRepositorio.__new__(SupabaseRepositorio)
    r._c = MagicMock()
    return r, r._c


# ------------------------------------------------------------------ tradução de erros
@pytest.mark.parametrize(
    ("codigo", "texto", "classe", "status", "api"),
    [
        ("AG017", "Usuário inexistente ou inativo.", SemPermissao, 403, "conta_inativa"),
        ("AG022", "Remoção da foto de perfil não autorizada.", SemPermissao, 403, "sem_permissao"),
        (
            "23514",
            "Violação da regra ck_perfil_fotos_mime: x",
            RequisicaoInvalida,
            400,
            "foto_invalida",
        ),
        (
            "23514",
            "Violação da regra ck_perfil_fotos_tamanho: x",
            RequisicaoInvalida,
            400,
            "foto_invalida",
        ),
        ("23505", 'duplicate key "perfil_fotos_pkey"', Conflito, 409, "foto_ja_registrada"),
        ("23505", 'duplicate key "uq_perfil_fotos_ativa"', Conflito, 409, "foto_ja_registrada"),
        ("55P03", "lock timeout", SistemaOcupado, 503, "ocupado"),
        ("40P01", "deadlock", SistemaOcupado, 503, "ocupado"),
        ("40001", "serialization", SistemaOcupado, 503, "ocupado"),
        ("XX000", "falha desconhecida", ErroInterno, 503, "indisponivel"),
    ],
)
def test_traducao_dos_erros_da_foto(codigo, texto, classe, status, api):  # type: ignore[no-untyped-def]
    e = traduzir_erro_banco(api_erro(codigo, texto))
    assert isinstance(e, classe) and e.status == status and e.codigo == api
    assert texto not in e.mensagem  # mensagem fixa, nunca a do Postgres


def test_traducao_nao_ecoa_detalhe_do_banco():
    e = traduzir_erro_banco(
        APIError(
            {
                "message": "Violação da regra ck_perfil_fotos_mime",
                "code": "23514",
                "details": "Key (usuario_id)=(1111) caminho=1111/x.jpg",
                "hint": "",
            }
        )
    )
    assert "1111" not in e.mensagem and "caminho" not in e.mensagem


# ------------------------------------------------------------------ repositório real
def test_trocar_envia_os_parametros_da_rpc_e_devolve_o_caminho_anterior():
    r, c = repo_mock()
    uid, fid = novo_id(), novo_id()
    c.rpc.return_value.execute.return_value = SimpleNamespace(
        data=[{"id_anterior": novo_id(), "caminho_anterior": f"{uid}/antiga.jpg"}]
    )
    assert (
        r.trocar_foto_perfil(usuario_id=uid, foto_id=fid, mime="image/jpeg", tamanho=900)
        == f"{uid}/antiga.jpg"
    )
    c.rpc.assert_called_once_with(
        "trocar_foto_perfil",
        {"p_usuario": uid, "p_id": fid, "p_mime": "image/jpeg", "p_tamanho": 900},
    )


@pytest.mark.parametrize(
    "data",
    [[{"id_anterior": None, "caminho_anterior": None}], [], None, {"caminho_anterior": None}],
)
def test_sem_foto_anterior_devolve_none(data):  # type: ignore[no-untyped-def]
    r, c = repo_mock()
    c.rpc.return_value.execute.return_value = SimpleNamespace(data=data)
    assert r.trocar_foto_perfil(usuario_id="u", foto_id="f", mime="image/jpeg", tamanho=1) is None
    assert r.remover_foto_perfil_por(usuario_id="u", ator_id="u") is None


def test_remover_por_envia_dono_e_ator():
    r, c = repo_mock()
    c.rpc.return_value.execute.return_value = SimpleNamespace(
        data=[{"id_anterior": "i", "caminho_anterior": "u/f.jpg"}]
    )
    assert r.remover_foto_perfil_por(usuario_id="u", ator_id="admin") == "u/f.jpg"
    c.rpc.assert_called_once_with("remover_foto_perfil_por", {"p_usuario": "u", "p_ator": "admin"})


@pytest.mark.parametrize("codigo", ["55P03", "40P01", "40001"])
def test_trocar_repete_em_lock_deadlock_e_serializacao(sem_espera, codigo):  # type: ignore[no-untyped-def]
    r, c = repo_mock()
    ok = SimpleNamespace(data=[{"id_anterior": None, "caminho_anterior": "u/a.jpg"}])
    c.rpc.return_value.execute.side_effect = [api_erro(codigo), api_erro(codigo), ok]
    assert (
        r.trocar_foto_perfil(usuario_id="u", foto_id="f", mime="image/jpeg", tamanho=1) == "u/a.jpg"
    )
    assert sem_espera == [0.2, 0.5] and c.rpc.return_value.execute.call_count == 3


def test_trocar_lock_persistente_vira_503_ocupado(sem_espera):  # type: ignore[no-untyped-def]
    r, c = repo_mock()
    c.rpc.return_value.execute.side_effect = [api_erro("55P03")] * 3
    with pytest.raises(SistemaOcupado) as ei:
        r.trocar_foto_perfil(usuario_id="u", foto_id="f", mime="image/jpeg", tamanho=1)
    assert ei.value.status == 503 and c.rpc.return_value.execute.call_count == 3


def test_remover_por_repete_em_lock_e_usa_mensagem_admin_so_para_admin(sem_espera):  # type: ignore[no-untyped-def]
    r, c = repo_mock()
    c.rpc.return_value.execute.side_effect = [api_erro("55P03")] * 3
    with pytest.raises(SistemaOcupado) as ei:
        r.remover_foto_perfil_por(usuario_id="u", ator_id="admin")
    assert "administrativa" in ei.value.mensagem
    c.rpc.return_value.execute.side_effect = [api_erro("55P03")] * 3
    with pytest.raises(SistemaOcupado) as ei2:
        r.remover_foto_perfil_por(usuario_id="u", ator_id="u")
    assert "administrativa" not in ei2.value.mensagem


@pytest.mark.parametrize("codigo", ["AG017", "AG022", "23514"])
def test_erros_definitivos_nao_repetem(sem_espera, codigo):  # type: ignore[no-untyped-def]
    r, c = repo_mock()
    c.rpc.return_value.execute.side_effect = [api_erro(codigo, "ck_perfil_fotos_x")] * 3
    with pytest.raises((SemPermissao, RequisicaoInvalida)):
        r.trocar_foto_perfil(usuario_id="u", foto_id="f", mime="image/jpeg", tamanho=1)
    assert c.rpc.return_value.execute.call_count == 1 and sem_espera == []


def test_leitura_em_lote_so_ativas_e_em_blocos():
    r, c = repo_mock()
    ids = [novo_id() for _ in range(120)]
    consulta = c.table.return_value.select.return_value.in_.return_value.is_.return_value
    consulta.execute.side_effect = [
        SimpleNamespace(data=[{"usuario_id": ids[0], "caminho": f"{ids[0]}/a.jpg"}]),
        SimpleNamespace(data=[]),
        SimpleNamespace(data=[{"usuario_id": ids[119], "caminho": f"{ids[119]}/b.jpg"}]),
    ]
    out = r.fotos_ativas(ids)
    assert out == {ids[0]: f"{ids[0]}/a.jpg", ids[119]: f"{ids[119]}/b.jpg"}
    c.table.assert_called_with("perfil_fotos")
    c.table.return_value.select.assert_called_with("usuario_id,caminho")
    # filtro de removida_em nulo (só fotos ativas) e blocos de 50 ids
    c.table.return_value.select.return_value.in_.return_value.is_.assert_called_with(
        "removida_em", "null"
    )
    tamanhos = [len(a.args[1]) for a in c.table.return_value.select.return_value.in_.call_args_list]
    assert tamanhos == [50, 50, 20]


def test_ids_ativos_filtra_por_ativo():
    r, c = repo_mock()
    consulta = c.table.return_value.select.return_value.in_.return_value.eq.return_value
    consulta.execute.return_value = SimpleNamespace(data=[{"id": "a"}])
    assert r.ids_ativos(["a", "b"]) == {"a"}
    c.table.return_value.select.return_value.in_.return_value.eq.assert_called_with("ativo", True)


def test_sem_ids_nao_consulta():
    r, c = repo_mock()
    assert r.fotos_ativas([]) == {} and r.ids_ativos([]) == set()
    c.table.assert_not_called()


# ------------------------------------------------------------------ FotosExibicao
class RepoFotos:
    def __init__(self, ativas: dict[str, str]) -> None:
        self.ativas = ativas
        self.consultas: list[list[str]] = []

    def fotos_ativas(self, ids: list[str]) -> dict[str, str]:
        self.consultas.append(list(ids))
        return {i: self.ativas[i] for i in ids if i in self.ativas}

    def ids_ativos(self, ids: list[str]) -> set[str]:
        return set(ids)


class ArmFotos:
    def __init__(self) -> None:
        self.lotes: list[list[str]] = []

    def urls_leitura(self, caminhos: list[str], expira_em: int) -> dict[str, str]:
        self.lotes.append(caminhos)
        return {c: f"u:{c}:{expira_em}" for c in caminhos}


def test_exibicao_descarta_caminho_fora_do_formato_final_ou_de_outro_dono():
    u, outro = novo_id(), novo_id()
    repo = RepoFotos({u: f"{outro}/{novo_id()}.jpg"})  # banco devolve caminho alheio: nunca assina
    arm = ArmFotos()
    assert FotosExibicao(repo, arm).urls([u]) == {}  # type: ignore[arg-type]
    assert arm.lotes == []


def test_exibicao_limita_o_tamanho_do_lote_e_deduplica():
    from app.services.fotos_exibicao import MAX_IDS

    assert MAX_IDS == 150
    ids = [novo_id() for _ in range(450)]
    repo = RepoFotos({})
    FotosExibicao(repo, ArmFotos()).urls(ids + ids)  # type: ignore[arg-type]
    assert len(repo.consultas) == 1 and len(repo.consultas[0]) == MAX_IDS


def test_exibicao_sem_storage_configurado_devolve_vazio():
    u = novo_id()
    repo = RepoFotos({u: f"{u}/{novo_id()}.jpg"})
    assert FotosExibicao(repo, None).urls([u]) == {}  # type: ignore[arg-type]


def test_exibicao_expira_em_faixa_curta():
    from app.services.fotos_exibicao import EXPIRA_LEITURA_S

    assert 60 <= EXPIRA_LEITURA_S <= 300


# ------------------------------------------------------------------ adaptador do Storage
class BucketFalso:
    def __init__(self) -> None:
        self.uploads: list[tuple[Any, ...]] = []
        self.falha: Exception | None = None
        self.assinados: list[tuple[list[str], int]] = []
        self.itens_assinados: list[dict[str, Any]] | None = None

    def upload(self, *args: Any) -> None:
        if self.falha:
            raise self.falha
        self.uploads.append(args)

    def create_signed_url(self, caminho: str, expira: int) -> dict[str, str]:
        return {"signedURL": f"https://s.exemplo/obj/{caminho}?exp={expira}"}

    def create_signed_urls(self, caminhos: list[str], expira: int) -> list[dict[str, Any]]:
        if self.falha:
            raise self.falha
        self.assinados.append((caminhos, expira))
        if self.itens_assinados is not None:
            return self.itens_assinados
        return [
            {"path": c, "signedURL": f"https://s.exemplo/obj/{c}?exp={expira}", "error": None}
            for c in caminhos
        ]


class ClienteFalso:
    def __init__(self, bucket: BucketFalso) -> None:
        self.bucket = bucket
        self.storage = self
        self.buckets: list[str] = []

    def from_(self, nome: str) -> BucketFalso:
        self.buckets.append(nome)
        return self.bucket


def adaptador(
    bucket: BucketFalso, handler: Any = None
) -> tuple[SupabaseArmazenamentoAvatares, ClienteFalso, list[httpx.Request]]:
    vistos: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        vistos.append(req)
        return handler(req)

    cliente = ClienteFalso(bucket)
    arm = SupabaseArmazenamentoAvatares(
        "https://p.supabase.co",
        "x",
        cliente=cliente,  # type: ignore[arg-type]
        http=httpx.Client(transport=httpx.MockTransport(h)),
    )
    return arm, cliente, vistos


def test_usa_o_bucket_avatares():
    arm, cliente, _ = adaptador(BucketFalso())
    arm.url_leitura("u/f.jpg", 60)
    assert BUCKET_AVATARES == "avatares" and cliente.buckets == ["avatares"]


def test_gravar_envia_content_type_e_upsert_falso():
    b = BucketFalso()
    arm, _, _ = adaptador(b)
    arm.gravar("u/f.jpg", b"dados", "image/jpeg")
    assert b.uploads == [("u/f.jpg", b"dados", {"content-type": "image/jpeg", "upsert": "false"})]


def test_gravar_objeto_existente_levanta_objeto_ja_existe():
    b = BucketFalso()
    erro = Exception("Duplicate")
    erro.status = 409  # type: ignore[attr-defined]
    b.falha = erro
    arm, _, _ = adaptador(b)
    with pytest.raises(ObjetoJaExiste):
        arm.gravar("u/f.jpg", b"x", "image/jpeg")
    outro = Exception("The resource already exists")
    outro.code = "Duplicate"  # type: ignore[attr-defined]
    b.falha = outro
    with pytest.raises(ObjetoJaExiste):
        arm.gravar("u/f.jpg", b"x", "image/jpeg")


def test_gravar_outras_falhas_viram_503_sem_vazar_caminho(caplog):  # type: ignore[no-untyped-def]
    b = BucketFalso()
    b.falha = RuntimeError("falha em u/segredo.jpg token=abc")
    arm, _, _ = adaptador(b)
    with pytest.raises(ErroInterno) as ei:
        arm.gravar("u/segredo.jpg", b"x", "image/jpeg")
    assert "segredo" not in ei.value.mensagem and "segredo" not in caplog.text


def test_ler_tudo_para_de_baixar_acima_do_limite():
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"a" * 5000)

    arm, _, vistos = adaptador(BucketFalso(), h)
    dados = arm.ler_tudo("u/f.jpg", 1000)
    assert dados is not None and len(dados) == 1001  # 1 byte além do teto: o service recusa
    assert vistos[0].url.host == "s.exemplo"


def test_ler_tudo_objeto_inexistente_e_none():
    arm, _, _ = adaptador(BucketFalso(), lambda r: httpx.Response(404))
    assert arm.ler_tudo("u/f.jpg", 1000) is None


def test_ler_tudo_erro_de_rede_vira_indisponivel_sem_vazar_url(caplog):  # type: ignore[no-untyped-def]
    def h(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("falha ao conectar em https://s.exemplo/obj/segredo")

    arm, _, _ = adaptador(BucketFalso(), h)
    with pytest.raises(ErroInterno) as ei:
        arm.ler_tudo("u/f.jpg", 1000)
    assert "s.exemplo" not in ei.value.mensagem and "segredo" not in caplog.text


def test_urls_em_lote():
    b = BucketFalso()
    arm, _, _ = adaptador(b)
    out = arm.urls_leitura(["a/1.jpg", "b/2.jpg"], 120)
    assert b.assinados == [(["a/1.jpg", "b/2.jpg"], 120)]
    assert out["a/1.jpg"].endswith("exp=120") and set(out) == {"a/1.jpg", "b/2.jpg"}
    assert arm.urls_leitura([], 120) == {}


def test_urls_em_lote_ignora_itens_com_erro():
    b = BucketFalso()
    b.itens_assinados = [
        {"path": "a/1.jpg", "signedURL": "https://s/1", "error": None},
        {"path": "b/2.jpg", "signedURL": None, "error": "Object not found"},
    ]
    arm, _, _ = adaptador(b)
    assert arm.urls_leitura(["a/1.jpg", "b/2.jpg"], 60) == {"a/1.jpg": "https://s/1"}


def test_urls_em_lote_falha_vira_indisponivel():
    b = BucketFalso()
    b.falha = RuntimeError("boom")
    arm, _, _ = adaptador(b)
    with pytest.raises(ErroInterno):
        arm.urls_leitura(["a/1.jpg"], 60)


def test_sem_create_signed_urls_degrada_sem_n_chamadas():
    """Lib sem o método em lote: erro (a exibição vira null); nunca uma chamada por caminho."""

    class SemLote:
        chamadas = 0

        def create_signed_url(self, *_a: Any) -> dict[str, str]:  # pragma: no cover
            SemLote.chamadas += 1
            return {"signedURL": "x"}

    arm, _, _ = adaptador(SemLote())  # type: ignore[arg-type]
    with pytest.raises(ErroInterno):
        arm.urls_leitura([f"a/{i}.jpg" for i in range(30)], 60)
    assert SemLote.chamadas == 0
    repo = RepoFotos({"u": "u/x.jpg"})
    assert FotosExibicao(repo, arm).assinar({"u": "u/x.jpg"}) == {}  # type: ignore[arg-type]


def test_storage3_fixado_tem_create_signed_urls():
    from storage3._sync.file_api import SyncBucketActionsMixin

    assert hasattr(SyncBucketActionsMixin, "create_signed_urls")
