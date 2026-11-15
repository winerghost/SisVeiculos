"""Limpeza dos arquivos do Storage no pré-lançamento (ferramenta de operação, sem rota HTTP).

Uso (na VPS, DEPOIS do `supabase/ops/limpar_dados_de_teste.sql`, para os órfãos ficarem coerentes):

    docker compose exec backend python -m app.ops.limpar_storage                 # ensaio
    docker compose exec backend python -m app.ops.limpar_storage --executar      # pede a frase
    docker compose exec backend python -m app.ops.limpar_storage --executar --apagar-usuarios

Regras:
- Padrão é ENSAIO: só lista contagens por bucket, nada é apagado.
- `--executar` exige a frase exata `APAGAR ARQUIVOS <ref>` (via `--confirmacao` ou stdin). O ref
  vem de SUPABASE_URL (`https://<ref>.supabase.co`; fora do supabase.co, o host inteiro).
- `checklists`: sempre esvaziado. `avatares`: só a foto VIGENTE de cada perfil fica
  (`perfil_fotos.removida_em IS NULL`); apaga órfãos, uploads temporários `tmp/` e fotos já
  trocadas/removidas. Com `--apagar-usuarios`, apaga também as vigentes de quem
  não é admin ativo. Qualquer outro bucket só é contado, nunca apagado.
- Recusa se o banco estiver LACRADO (mesma trava do SQL). O lacre é lido pela RPC
  `public.ops_banco_lacrado()` (só service_role). Sem como confirmar o estado do lacre, o ensaio
  avisa e a execução é recusada (falha fechada).
- A saída mostra só contagens: nunca segredos, URLs nem caminhos (que contêm id de pessoa).

Códigos de saída: 0 = ok; 2 = recusado (nada apagado); 1 = falha (pode ter apagado parte;
rode o ensaio de novo para conferir o que restou).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, TextIO
from urllib.parse import urlsplit

BUCKET_CHECKLISTS = "checklists"
BUCKET_AVATARES = "avatares"
PLACEHOLDER_PASTA = ".emptyFolderPlaceholder"
TAMANHO_PAGINA = 100  # itens por chamada de listagem do Storage
TAMANHO_LOTE_REMOCAO = 100  # caminhos por chamada de remoção
TAMANHO_PAGINA_BANCO = 1000
PROFUNDIDADE_MAX = 8  # os caminhos do sistema têm no máximo 3 níveis
MAX_PAGINAS = 100_000  # trava contra laço infinito se o Storage repetir páginas
RPC_LACRE = "ops_banco_lacrado"

SAIDA_OK = 0
SAIDA_FALHA = 1
SAIDA_RECUSADO = 2


class Recusado(Exception):  # noqa: N818
    """Execução recusada antes de apagar qualquer arquivo. A mensagem é segura para exibir."""


class LacreDesconhecido(Exception):  # noqa: N818
    """Não foi possível confirmar se o banco está lacrado."""


class ClienteStorage(Protocol):
    def listar_buckets(self) -> list[str]: ...

    def listar(self, bucket: str, prefixo: str, limite: int, offset: int) -> list[dict[str, Any]]:
        """Uma página da pasta `prefixo` (itens com `id` nulo são subpastas)."""
        ...

    def remover(self, bucket: str, caminhos: list[str]) -> None: ...


class FonteBanco(Protocol):
    def banco_lacrado(self) -> bool:
        """True se o banco tem o comentário `sisveiculos:lacrado`. Levanta se não souber."""
        ...

    def caminhos_fotos_vigentes(self) -> set[str]:
        """`perfil_fotos.caminho` das fotos vigentes (`removida_em IS NULL`)."""
        ...

    def admins_ativos(self) -> set[str]:
        """Ids dos perfis com role admin e ativo = true."""
        ...


# ----------------------------------------------------------------------------- regras puras


def ref_do_projeto(supabase_url: str) -> str:
    """`https://<ref>.supabase.co` -> `<ref>`; outro host (ex.: Supabase local) -> o host."""
    host = (urlsplit(supabase_url.strip()).hostname or "").lower()
    if not host:
        raise Recusado("SUPABASE_URL ausente ou inválida.")
    if host.endswith(".supabase.co"):
        return host.split(".", 1)[0]
    return host


def frase_esperada(ref: str) -> str:
    return f"APAGAR ARQUIVOS {ref}"


def dono_do_caminho(caminho: str) -> str | None:
    """Usuário dono de um arquivo de `avatares`: `<uid>/<id>.<ext>` ou `tmp/<uid>/<id>`."""
    partes = caminho.split("/")
    if partes[0] == "tmp":
        return partes[1] if len(partes) >= 3 and partes[1] else None
    return partes[0] if len(partes) >= 2 and partes[0] else None


def listar_arquivos(cliente: ClienteStorage, bucket: str) -> list[str]:
    """Todos os caminhos de arquivo do bucket (recursivo, paginado). Ignora o placeholder de
    pasta. Coleta tudo ANTES de qualquer remoção (remover durante a paginação pularia itens)."""
    arquivos: list[str] = []
    pendentes: list[tuple[str, int]] = [("", 0)]
    paginas = 0
    while pendentes:
        pasta, nivel = pendentes.pop()
        offset = 0
        while True:
            paginas += 1
            if paginas > MAX_PAGINAS:
                raise RuntimeError("listagem do Storage excedeu o limite de páginas")
            itens = cliente.listar(bucket, pasta, TAMANHO_PAGINA, offset)
            for item in itens:
                nome = str(item.get("name") or "")
                if not nome or nome == PLACEHOLDER_PASTA:
                    continue
                caminho = f"{pasta}/{nome}" if pasta else nome
                if item.get("id") is None:  # subpasta
                    if nivel + 1 > PROFUNDIDADE_MAX:
                        raise RuntimeError("estrutura de pastas mais profunda que o esperado")
                    pendentes.append((caminho, nivel + 1))
                else:
                    arquivos.append(caminho)
            if len(itens) < TAMANHO_PAGINA:
                break
            offset += len(itens)
    return arquivos


@dataclass
class PlanoBucket:
    bucket: str
    total: int
    a_apagar: list[str] = field(default_factory=list, repr=False)
    gerenciado: bool = True  # False = bucket desconhecido: só contado

    @property
    def mantidos(self) -> int:
        return self.total - len(self.a_apagar)


def planejar(
    cliente: ClienteStorage, banco: FonteBanco | None, *, apagar_usuarios: bool
) -> list[PlanoBucket]:
    """Decide, por bucket, o que seria apagado. Não apaga nada."""
    planos: list[PlanoBucket] = []
    for bucket in sorted(cliente.listar_buckets()):
        arquivos = listar_arquivos(cliente, bucket)
        if bucket == BUCKET_CHECKLISTS:
            planos.append(PlanoBucket(bucket, len(arquivos), list(arquivos)))
        elif bucket == BUCKET_AVATARES:
            if banco is None:
                raise RuntimeError("fonte do banco ausente")
            vigentes = banco.caminhos_fotos_vigentes()
            admins = banco.admins_ativos() if apagar_usuarios else set()
            apagar = [
                c
                for c in arquivos
                if c not in vigentes or (apagar_usuarios and dono_do_caminho(c) not in admins)
            ]
            planos.append(PlanoBucket(bucket, len(arquivos), apagar))
        else:
            planos.append(PlanoBucket(bucket, len(arquivos), [], gerenciado=False))
    return planos


def _lotes(itens: list[str], tamanho: int) -> Iterable[list[str]]:
    for i in range(0, len(itens), tamanho):
        yield itens[i : i + tamanho]


def executar_plano(cliente: ClienteStorage, planos: list[PlanoBucket]) -> None:
    for plano in planos:
        if not plano.gerenciado:  # defesa: bucket desconhecido nunca é apagado
            continue
        for lote in _lotes(plano.a_apagar, TAMANHO_LOTE_REMOCAO):
            cliente.remover(plano.bucket, lote)


def _relatorio(planos: list[PlanoBucket], saida: TextIO, *, titulo: str) -> None:
    print(titulo, file=saida)
    if not planos:
        print("  (nenhum bucket encontrado)", file=saida)
    for p in planos:
        if p.gerenciado:
            apagar = len(p.a_apagar)
            print(
                f"  {p.bucket}: {p.total} arquivo(s); apagar {apagar}; manter {p.mantidos}",
                file=saida,
            )
        else:
            print(
                f"  {p.bucket}: {p.total} arquivo(s); bucket fora da limpeza (intocado)", file=saida
            )


# ----------------------------------------------------------------------------- execução


def rodar(
    argv: list[str],
    *,
    cliente: ClienteStorage,
    banco: FonteBanco,
    supabase_url: str,
    entrada: Callable[[], str],
    saida: TextIO,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.limpar_storage",
        description="Limpeza dos arquivos do Storage (pré-lançamento). Padrão: ensaio.",
    )
    parser.add_argument("--executar", action="store_true", help="apaga de fato (pede a frase)")
    parser.add_argument(
        "--apagar-usuarios",
        action="store_true",
        help="apaga também as fotos de perfil de quem não é admin ativo",
    )
    parser.add_argument("--confirmacao", default=None, help="frase de confirmação (sem stdin)")
    args = parser.parse_args(argv)

    try:
        ref = ref_do_projeto(supabase_url)
        try:
            lacrado: bool | None = banco.banco_lacrado()
        except Exception as e:  # noqa: BLE001 - só o tipo é exibido
            print(f"AVISO: não foi possível ler o lacre do banco ({type(e).__name__}).", file=saida)
            lacrado = None
        if lacrado:
            raise Recusado(
                "este banco está LACRADO (sisveiculos:lacrado). A limpeza não roda mais aqui."
            )
        modo = (
            "apagar usuários (só admins ativos mantêm foto)"
            if args.apagar_usuarios
            else ("manter usuários (só a foto vigente de cada perfil fica)")
        )
        planos = planejar(cliente, banco, apagar_usuarios=args.apagar_usuarios)
        if not args.executar:
            _relatorio(planos, saida, titulo=f"ENSAIO (nada foi apagado). Modo: {modo}.")
            print(f'Para executar: --executar e a frase "{frase_esperada(ref)}".', file=saida)
            return SAIDA_OK
        if lacrado is None:
            raise Recusado(
                f"sem confirmar o lacre do banco a execução é recusada (RPC {RPC_LACRE})."
            )
        _relatorio(planos, saida, titulo=f"PLANO. Modo: {modo}.")
        frase = args.confirmacao
        if frase is None:
            print(f"Digite exatamente: {frase_esperada(ref)}", file=saida)
            frase = entrada()
        if frase.strip() != frase_esperada(ref):
            raise Recusado("frase de confirmação incorreta.")
    except Recusado as e:
        print(f"RECUSADO: {e} Nada foi apagado.", file=saida)
        return SAIDA_RECUSADO
    except Exception as e:  # noqa: BLE001 - mensagens do Storage podem trazer caminho/URL
        print(f"FALHA ({type(e).__name__}). Nada foi apagado.", file=saida)
        return SAIDA_FALHA

    try:
        executar_plano(cliente, planos)
        conferencia = planejar(cliente, banco, apagar_usuarios=args.apagar_usuarios)
    except Exception as e:  # noqa: BLE001
        print(
            f"FALHA durante a remoção ({type(e).__name__}). Parte pode ter sido apagada: "
            "rode o ensaio para conferir.",
            file=saida,
        )
        return SAIDA_FALHA
    _relatorio(conferencia, saida, titulo="CONCLUÍDO. Situação atual:")
    restantes = sum(len(p.a_apagar) for p in conferencia if p.gerenciado)
    if restantes:
        print(f"ATENÇÃO: {restantes} arquivo(s) ainda deveriam ter sido apagados.", file=saida)
        return SAIDA_FALHA
    return SAIDA_OK


# ----------------------------------------------------------------------------- adaptadores reais


class SupabaseClienteStorage:
    def __init__(self, cliente: Any) -> None:
        self._c = cliente

    def listar_buckets(self) -> list[str]:
        saida = []
        for b in self._c.storage.list_buckets():
            nome = getattr(b, "id", None) or getattr(b, "name", None)
            if nome is None and isinstance(b, dict):
                nome = b.get("id") or b.get("name")
            if nome:
                saida.append(str(nome))
        return saida

    def listar(self, bucket: str, prefixo: str, limite: int, offset: int) -> list[dict[str, Any]]:
        opcoes = {
            "limit": limite,
            "offset": offset,
            "sortBy": {"column": "name", "order": "asc"},
        }
        return list(self._c.storage.from_(bucket).list(prefixo, opcoes))

    def remover(self, bucket: str, caminhos: list[str]) -> None:
        self._c.storage.from_(bucket).remove(caminhos)


class SupabaseFonteBanco:
    def __init__(self, cliente: Any) -> None:
        self._c = cliente

    def banco_lacrado(self) -> bool:
        dados = self._c.rpc(RPC_LACRE).execute().data
        if isinstance(dados, list):  # alguns clientes embrulham o escalar
            dados = dados[0] if dados else None
        if not isinstance(dados, bool):
            raise LacreDesconhecido("resposta inesperada")
        return dados

    def _paginar(self, consulta: Callable[[int, int], Any], coluna: str) -> set[str]:
        valores: set[str] = set()
        inicio = 0
        while True:
            linhas = consulta(inicio, inicio + TAMANHO_PAGINA_BANCO - 1).execute().data or []
            valores.update(str(r[coluna]) for r in linhas if r.get(coluna))
            if len(linhas) < TAMANHO_PAGINA_BANCO:
                return valores
            inicio += TAMANHO_PAGINA_BANCO

    def caminhos_fotos_vigentes(self) -> set[str]:
        return self._paginar(
            lambda a, b: self._c.table("perfil_fotos")
            .select("caminho")
            .is_("removida_em", "null")
            .order("id")
            .range(a, b),
            "caminho",
        )

    def admins_ativos(self) -> set[str]:
        return self._paginar(
            lambda a, b: self._c.table("perfis")
            .select("id")
            .eq("role", "admin")
            .eq("ativo", True)
            .order("id")
            .range(a, b),
            "id",
        )


def main() -> int:  # pragma: no cover - integra com o ambiente do container
    # httpx/supabase registram URLs das chamadas em INFO: silencia para não vazar nada.
    logging.basicConfig(level=logging.WARNING)
    for nome in ("httpx", "httpcore", "supabase", "storage3", "postgrest"):
        logging.getLogger(nome).setLevel(logging.WARNING)
    url = os.environ.get("SUPABASE_URL", "")
    chave = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not chave:
        print(
            "RECUSADO: SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY são obrigatórias. Nada foi apagado."
        )
        return SAIDA_RECUSADO
    from supabase import create_client

    try:
        cliente = create_client(url, chave)
    except Exception as e:  # noqa: BLE001
        print(f"FALHA ao conectar ({type(e).__name__}). Nada foi apagado.")
        return SAIDA_FALHA

    def entrada() -> str:
        try:
            return input()
        except EOFError:
            return ""

    return rodar(
        sys.argv[1:],
        cliente=SupabaseClienteStorage(cliente),
        banco=SupabaseFonteBanco(cliente),
        supabase_url=url,
        entrada=entrada,
        saida=sys.stdout,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
