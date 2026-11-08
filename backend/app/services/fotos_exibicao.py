"""Exibição das fotos de perfil: URLs assinadas curtas, geradas em lote por página.

Regras (LGPD):
- Quem chama só passa ids de pessoas que JÁ aparecem numa tela que o espectador pode ver
  (o filtro por dono/role das listagens limita os ids). Isto NÃO é um diretório de usuários.
- Conta INATIVA nunca recebe URL (a foto deixa de ser servida; só pode ser removida).
- A URL é efêmera: nunca é persistida nem logada. Falha do Storage ou do banco nunca derruba a
  tela que a chamou: o resultado simplesmente não traz a URL (só o tipo do erro vai ao log).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from app.repositorios.armazenamento_avatares import ArmazenamentoAvatares, caminho_final_do_usuario
from app.repositorios.base import Repositorio

log = logging.getLogger(__name__)

EXPIRA_LEITURA_S = 120  # URL de leitura: 2 minutos (faixa aprovada: 60 a 300 s)
MAX_IDS = 150  # teto de pessoas por página; o excedente fica sem foto (foto_url null)


class FotosExibicao:
    def __init__(self, repo: Repositorio, armazenamento: ArmazenamentoAvatares | None) -> None:
        self._repo = repo
        self._arm = armazenamento

    def caminhos(self, ids: Iterable[str]) -> dict[str, str]:
        """usuario_id -> caminho da foto ATIVA (qualquer conta). Vazio se o banco falhar."""
        unicos = sorted({i for i in ids if i})[:MAX_IDS]
        if not unicos:
            return {}
        try:
            ativas = self._repo.fotos_ativas(unicos)
        except Exception as e:
            log.warning("fotos_indisponivel etapa=consulta tipo=%s", type(e).__name__)
            return {}
        # Defesa em profundidade: só o formato final, do próprio usuário.
        return {u: c for u, c in ativas.items() if caminho_final_do_usuario(u, c)}

    def assinar(self, caminhos: dict[str, str]) -> dict[str, str]:
        """usuario_id -> URL assinada. Sem Storage configurado ou com falha: vazio."""
        if self._arm is None or not caminhos:
            return {}
        try:
            urls = self._arm.urls_leitura(sorted(set(caminhos.values())), EXPIRA_LEITURA_S)
        except Exception as e:
            log.warning("fotos_indisponivel etapa=assinatura tipo=%s", type(e).__name__)
            return {}
        return {u: urls[c] for u, c in caminhos.items() if c in urls}

    def urls(self, ids: Iterable[str], *, ativos: set[str] | None = None) -> dict[str, str]:
        """usuario_id -> URL assinada, só de contas ativas que têm foto.

        `ativos`: ids que o chamador já sabe ativos (evita a consulta de `perfis`); os demais
        são conferidos no banco.
        """
        com_foto = self.caminhos(ids)
        if not com_foto:
            return {}
        conhecidos = ativos or set()
        faltam = [u for u in com_foto if u not in conhecidos]
        try:
            ok = conhecidos | (self._repo.ids_ativos(faltam) if faltam else set())
        except Exception as e:
            log.warning("fotos_indisponivel etapa=ativos tipo=%s", type(e).__name__)
            return {}
        return self.assinar({u: c for u, c in com_foto.items() if u in ok})
