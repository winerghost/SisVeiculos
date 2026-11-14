"""Exportar e importar dados (somente admin).

Escopo: veículos, requisições (com km), localizações, configurações e checklists SEM fotos.
Nunca perfis, contas, senhas, auditoria nem arquivos. Formato: um JSON versionado
`{versao, gerado_em, sha256, tabelas:{...}}`; o `sha256` cobre só `tabelas` (forma canônica) e
detecta arquivo corrompido ou editado à mão (integridade, não autenticidade).

Importação = MESCLAR, nunca apagar, numa transação única (RPC `importar_dados`). O service
valida versão, hash, tabelas, colunas e tipos; as regras de negócio (veículo ocupado, CHECKs,
FKs) são do banco e voltam como erros de linha. Com qualquer erro nada é gravado. As duas RPCs
auditam na própria transação (`dados_exportados`/`dados_importados`, só contagens).
Fora do arquivo, de propósito: mfa, passkeys, push e relatórios (segurança/infra externa).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from typing import Any

from app.errors import RequisicaoInvalida
from app.fuso import FUSO
from app.modelos import Perfil
from app.repositorios.base import Repositorio
from app.services.admin_configuracoes import ROTULOS_RECURSOS
from app.services.admin_usuarios import exigir_admin

VERSAO = 1
MAX_LINHAS_TOTAL = 100_000
# Mesmos limites da RPC importar_dados: o service recusa antes de chegar ao banco.
LIMITES_TABELA = {
    "veiculos": 500,
    "requisicoes": 20_000,
    "checklists": 40_000,
    "localizacoes_viagem": 40_000,
}
MAX_ERROS = 200  # a resposta lista no máximo isto; o total real vem em `total_erros`
MODOS = ("previa", "confirmar")
FORMATO_PLACA = re.compile(r"^[A-Z]{3}[0-9][A-Z0-9][0-9]{2}$")
CODIGO_PUBLICO = re.compile(r"^[a-f0-9]{32}$")
# NUL e demais controles (exceto tab, LF e CR) e substitutos soltos (não codificam em UTF-8).
CARACTERE_PROIBIDO = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")

UNIDADES = ("PU", "PF", "CJU", "UEA")
STATUS = ("solicitada", "autorizada", "em_uso", "concluida", "negada", "cancelada")
MOMENTOS = ("saida", "chegada")
COMBUSTIVEIS = ("vazio", "um_quarto", "meio", "tres_quartos", "cheio")

# Tipos de coluna: uuid, ts, bool, num, ("texto", max), ("int", min), ("enum", valores)
Tipo = Any
Coluna = tuple[Tipo, bool]  # (tipo, obrigatória)

ESQUEMA: dict[str, dict[str, Coluna]] = {
    "veiculos": {
        "id": ("uuid", True),
        "descricao": (("texto", 120), True),
        "placa": ("placa", False),
        "ativo": ("bool", True),
        "codigo_publico": ("codigo_publico", False),
        "criado_em": ("ts", False),
        "atualizado_em": ("ts", False),
    },
    "requisicoes": {
        "id": ("uuid", True),
        "usuario_id": ("uuid", True),
        "nome_solicitante": (("texto", 120), True),
        "unidade": (("enum", UNIDADES), True),
        "hora_saida": ("ts", True),
        "trajeto": (("texto", 500), True),
        "observacao": (("texto", 500), False),
        "veiculo_id": ("uuid", True),
        "status": (("enum", STATUS), True),
        "autorizado_por": ("uuid", False),
        "autorizado_em": ("ts", False),
        "saida_real_em": ("ts", False),
        "chegada_real_em": ("ts", False),
        "saida_registrada_por": ("uuid", False),
        "chegada_registrada_por": ("uuid", False),
        "cancelada_por": ("uuid", False),
        "cancelada_em": ("ts", False),
        "km_saida": (("int", 0), False),
        "km_chegada": (("int", 0), False),
        "motorista_id": ("uuid", False),
        "criado_em": ("ts", False),
        "atualizado_em": ("ts", False),
    },
    "localizacoes_viagem": {
        "id": ("uuid", True),
        "requisicao_id": ("uuid", True),
        "momento": (("enum", MOMENTOS), True),
        "latitude": (("num", -90, 90), True),
        "longitude": (("num", -180, 180), True),
        "precisao_m": (("num", 0, 1_000_000), False),
        "registrado_por": ("uuid", True),
        "registrado_em": ("ts", False),
    },
    "checklists": {
        "id": ("uuid", True),
        "requisicao_id": ("uuid", True),
        "momento": (("enum", MOMENTOS), True),
        "combustivel": (("enum", COMBUSTIVEIS), True),
        "avarias": ("bool", True),
        "descricao_avarias": (("texto", 500), False),
        "registrado_por": ("uuid", True),
        "registrado_em": ("ts", False),
    },
    "configuracoes": {  # objeto único (flags); mfa, passkeys, push e relatórios ficam de fora
        nome: ("bool", False)
        for nome in (
            "quilometragem",
            "abastecimento_manutencao",
            "checklist",
            "notificacao_email_criticos",
            "motorista",
            "motorista_registra",
            "motorista_localizacao",
            "vigilante_km",
            "autorizador_decide_propria",
            "autorizador_km",
            "indicador_conectividade",
            "feedback_tatil",
            "leitor_qr_veiculos",
            "voz_campos",
        )
    },
}
TABELAS = tuple(ESQUEMA)
ATUALIZAVEIS = ("veiculos", "configuracoes")  # conflito => o arquivo atualiza (política do banco)


def hash_tabelas(tabelas: dict[str, Any]) -> str:
    """SHA-256 da forma canônica (chaves ordenadas, sem espaços, UTF-8) de `tabelas`.

    Levanta UnicodeEncodeError se houver substituto solto no texto; quem recebe arquivo externo
    deve capturar."""
    bruto = json.dumps(tabelas, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()


def _valor_invalido(tipo: Tipo, valor: Any) -> bool:
    """True se `valor` (não nulo) não casa com o tipo."""
    if tipo == "bool":
        return not isinstance(valor, bool)
    if tipo == "uuid":
        if not isinstance(valor, str):
            return True
        try:
            return str(uuid.UUID(valor)) != valor.lower()
        except ValueError:
            return True
    if tipo == "ts":
        if not isinstance(valor, str) or len(valor) > 40:
            return True
        try:
            return datetime.fromisoformat(valor).tzinfo is None  # timestamptz: exige fuso
        except ValueError:
            return True
    if tipo == "placa":
        return not isinstance(valor, str) or FORMATO_PLACA.fullmatch(valor) is None
    if tipo == "codigo_publico":  # 32 hex minúsculos, como gera o banco
        return not isinstance(valor, str) or CODIGO_PUBLICO.fullmatch(valor) is None
    nome = tipo[0]
    if nome == "texto":
        return (
            not isinstance(valor, str)
            or not valor.strip()
            or len(valor) > tipo[1]
            or CARACTERE_PROIBIDO.search(valor) is not None
        )
    if nome == "int":
        return (
            isinstance(valor, bool)
            or not isinstance(valor, int)
            or not tipo[1] <= valor <= 2**31 - 1
        )
    if nome == "num":
        if isinstance(valor, bool) or not isinstance(valor, int | float):
            return True
        return not tipo[1] <= valor <= tipo[2]
    if nome == "enum":
        return not isinstance(valor, str) or valor not in tipo[1]
    return True


def _erros_da_linha(tabela: str, linha: Any) -> str | None:
    """Primeira falha estrutural da linha, em texto fixo (nunca ecoa o valor recebido)."""
    if not isinstance(linha, dict):
        return "A linha deve ser um objeto."
    esquema = ESQUEMA[tabela]
    desconhecidas = sorted(set(linha) - set(esquema))
    if desconhecidas:
        return "Coluna não reconhecida: " + ", ".join(str(c)[:40] for c in desconhecidas[:5]) + "."
    for coluna, (tipo, obrigatoria) in esquema.items():
        valor = linha.get(coluna)
        if valor is None:
            if obrigatoria:
                return f"Campo obrigatório ausente: {coluna}."
            continue
        if _valor_invalido(tipo, valor):
            return f"Valor inválido em '{coluna}'."
    return None


class ServicoDados:
    def __init__(self, repo: Repositorio, *, max_bytes: int) -> None:
        self._repo = repo
        self._max_bytes = max_bytes

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    # -- exportar --------------------------------------------------------------------------
    def exportar(self, admin: Perfil) -> tuple[dict[str, Any], str]:
        """Devolve (arquivo, nome sugerido). A RPC audita na mesma transação da leitura."""
        exigir_admin(admin)
        lidas = self._repo.exportar_dados(admin_id=admin.id).get("tabelas", {})
        tabelas: dict[str, Any] = {
            t: [self._so_colunas(t, r) for r in lidas.get(t) or []]
            for t in TABELAS
            if t != "configuracoes"
        }
        tabelas["configuracoes"] = self._so_colunas(
            "configuracoes", lidas.get("configuracoes") or {}
        )
        momento = datetime.now(FUSO)
        arquivo = {
            "versao": VERSAO,
            "gerado_em": momento.isoformat(),
            "sha256": hash_tabelas(tabelas),
            "tabelas": tabelas,
        }
        return arquivo, f"sisveiculos-dados-{momento:%Y%m%d-%H%M%S}.json"

    @staticmethod
    def _so_colunas(tabela: str, linha: dict[str, Any]) -> dict[str, Any]:
        """Só as colunas do esquema: coluna nova do banco nunca vaza sem decisão explícita."""
        return {c: linha.get(c) for c in ESQUEMA[tabela]}

    # -- importar --------------------------------------------------------------------------
    def importar(self, admin: Perfil, bruto: bytes, modo: str | None) -> dict[str, Any]:
        exigir_admin(admin)
        if modo not in MODOS:
            raise RequisicaoInvalida("O parâmetro 'modo' deve ser 'previa' ou 'confirmar'.")
        if len(bruto) > self._max_bytes:
            raise RequisicaoInvalida("Arquivo acima do tamanho permitido.")
        tabelas = self._validar_arquivo(bruto)
        erros = self._validar_linhas(tabelas)
        confirmar = modo == "confirmar"

        if erros:  # nada vai ao banco: sem contagem confiável, o resumo fica zerado
            resumo = {t: {"novos": 0, "existentes": 0, "conflitos": 0} for t in TABELAS}
            return self._resposta(resumo, erros, len(erros), [], aplicado=False)

        # A RPC recebe o envelope; o ator é sempre o admin do JWT. Prévia e confirmação rodam o
        # mesmo código no banco (a prévia só desfaz no fim); a auditoria também é do banco.
        envelope = {"versao": VERSAO, "tabelas": _uuids_minusculos(tabelas)}
        resultado = self._repo.importar_dados(envelope, admin_id=admin.id, confirmar=confirmar)
        resumo = {t: _contagem(resultado.get("resumo", {}).get(t)) for t in TABELAS}
        erros_banco = [
            {
                "tabela": str(e.get("tabela", ""))[:40],
                "indice": e.get("indice"),
                "mensagem": str(e.get("mensagem", "Linha recusada."))[:200],
            }
            for e in resultado.get("erros", [])
            if isinstance(e, dict)
        ]
        total = resultado.get("erros_total")
        total = total if isinstance(total, int) and total >= len(erros_banco) else len(erros_banco)
        return self._resposta(
            resumo,
            erros_banco,
            total,
            _alteracoes(resultado.get("configuracoes_alteradas")),
            aplicado=confirmar and total == 0 and resultado.get("aplicado") is True,
        )

    @staticmethod
    def _resposta(
        resumo: dict[str, Any],
        erros: list[dict[str, Any]],
        total: int,
        alteradas: list[dict[str, Any]],
        *,
        aplicado: bool,
    ) -> dict[str, Any]:
        # Há o que mesclar: linhas novas ou conflitos de tabelas mutáveis (o arquivo atualiza).
        mudaria = any(c["novos"] for c in resumo.values()) or any(
            resumo[t]["conflitos"] for t in ATUALIZAVEIS
        )
        return {
            "resumo": resumo,
            "erros": erros[:MAX_ERROS],
            "total_erros": total,
            "configuracoes_alteradas": alteradas,
            "aplicado": aplicado,
            "acoes_permitidas": ["confirmar"] if total == 0 and mudaria and not aplicado else [],
        }

    def _validar_arquivo(self, bruto: bytes) -> dict[str, Any]:
        try:
            arquivo = json.loads(bruto)
        except (ValueError, RecursionError):
            raise RequisicaoInvalida("O arquivo não é um JSON válido.") from None
        if not isinstance(arquivo, dict) or set(arquivo) != {
            "versao",
            "gerado_em",
            "sha256",
            "tabelas",
        }:
            raise RequisicaoInvalida("O arquivo não está no formato de exportação do sistema.")
        if arquivo["versao"] != VERSAO or isinstance(arquivo["versao"], bool):
            raise RequisicaoInvalida("Versão do arquivo não suportada.")
        tabelas = arquivo["tabelas"]
        if not isinstance(tabelas, dict):
            raise RequisicaoInvalida("O arquivo não está no formato de exportação do sistema.")
        desconhecidas = sorted(set(tabelas) - set(TABELAS))
        if desconhecidas:
            raise RequisicaoInvalida(
                "Tabela não reconhecida no arquivo: " + ", ".join(t[:40] for t in desconhecidas[:5])
            )
        if not all(
            isinstance(v, list) or (t == "configuracoes" and isinstance(v, dict))
            for t, v in tabelas.items()
        ):
            raise RequisicaoInvalida("Cada tabela do arquivo deve ser uma lista.")
        esperado = arquivo["sha256"]
        try:
            confere = isinstance(esperado, str) and hash_tabelas(tabelas) == esperado.lower()
        except UnicodeEncodeError:  # substituto solto no texto: não é UTF-8 válido
            raise RequisicaoInvalida(
                "O arquivo contém caracteres inválidos.", "arquivo_invalido"
            ) from None
        if not confere:
            raise RequisicaoInvalida(
                "O arquivo está corrompido ou foi alterado (sha256 não confere).",
                "arquivo_adulterado",
            )
        if sum(len(v) for t, v in tabelas.items() if t != "configuracoes") > MAX_LINHAS_TOTAL:
            raise RequisicaoInvalida("O arquivo tem linhas demais para uma única importação.")
        for tabela, limite in LIMITES_TABELA.items():
            if len(tabelas.get(tabela) or []) > limite:
                raise RequisicaoInvalida(
                    f"A tabela '{tabela}' tem linhas demais (máximo {limite} por importação)."
                )
        if isinstance(tabelas.get("configuracoes"), list) and len(tabelas["configuracoes"]) > 1:
            raise RequisicaoInvalida("A tabela 'configuracoes' aceita uma única linha.")
        return {t: tabelas[t] for t in TABELAS if t in tabelas}

    @staticmethod
    def _validar_linhas(tabelas: dict[str, Any]) -> list[dict[str, Any]]:
        erros: list[dict[str, Any]] = []
        for tabela, bruto in tabelas.items():
            linhas = [bruto] if isinstance(bruto, dict) else bruto  # configuracoes: objeto único
            vistos: set[str] = set()
            chaves: set[tuple[str, str]] = set()
            for i, linha in enumerate(linhas):
                msg = _erros_da_linha(tabela, linha)
                if msg is None and tabela != "configuracoes":
                    # UUID é case-insensitive: maiúscula e minúscula são o mesmo identificador.
                    ident = linha["id"].lower()
                    if ident in vistos:
                        msg = "Identificador repetido no arquivo."
                    vistos.add(ident)
                    if "momento" in linha:  # uma linha por requisição e momento
                        par = (linha["requisicao_id"].lower(), linha["momento"])
                        if par in chaves:
                            msg = "Momento repetido para a mesma requisição."
                        chaves.add(par)
                if msg is not None:
                    erros.append({"tabela": tabela, "indice": i, "mensagem": msg})
        return erros


def _uuids_minusculos(tabelas: dict[str, Any]) -> dict[str, Any]:
    """Cópia das tabelas (já validadas) com toda coluna uuid em minúsculas, como o banco grava."""
    saida: dict[str, Any] = {}
    for tabela, bruto in tabelas.items():
        if isinstance(bruto, dict):  # configuracoes: só booleanos
            saida[tabela] = bruto
            continue
        colunas_uuid = [c for c, (tipo, _) in ESQUEMA[tabela].items() if tipo == "uuid"]
        saida[tabela] = [
            {
                k: (v.lower() if k in colunas_uuid and isinstance(v, str) else v)
                for k, v in lin.items()
            }
            for lin in bruto
        ]
    return saida


def _alteracoes(bruto: Any) -> list[dict[str, Any]]:
    """Só flags conhecidas com de/para booleanos; o resto do que vier do banco é descartado.

    O rótulo pt-BR vem do catálogo de Configurações (o front só exibe)."""
    if not isinstance(bruto, list):
        return []
    return [
        {
            "chave": i["chave"],
            "rotulo": ROTULOS_RECURSOS.get(i["chave"], i["chave"]),
            "de": i["de"],
            "para": i["para"],
        }
        for i in bruto
        if isinstance(i, dict)
        and i.get("chave") in ESQUEMA["configuracoes"]
        and isinstance(i.get("de"), bool)
        and isinstance(i.get("para"), bool)
    ]


def _contagem(bruto: Any) -> dict[str, int]:
    base = bruto if isinstance(bruto, dict) else {}
    return {
        k: int(base.get(k, 0)) if isinstance(base.get(k, 0), int) else 0
        for k in ("novos", "existentes", "conflitos")
    }
