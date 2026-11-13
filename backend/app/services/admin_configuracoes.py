"""Configurações: flags dos recursos opcionais (somente admin).

Fonte da verdade é a tabela `configuracoes`; a auditoria (`configuracao_alterada`, uma linha por
recurso) sai do trigger do banco quando `alterado_por` é o admin. Desligar nunca apaga dados:
aqui só se gravam flags. A lógica de km/checklist nas requisições NÃO existe ainda.
"""

from __future__ import annotations

from typing import Any

from app.errors import DependenciaRecurso, SemPermissao
from app.modelos import Configuracoes, Perfil
from app.repositorios.base import Repositorio
from app.services.admin_usuarios import exigir_admin
from app.services.validacao_admin import validar_recursos

# chave -> (rótulo, descrição, recursos de que depende)
CATALOGO: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "quilometragem": (
        "Quilometragem",
        "Exige km de saída e de chegada; o km de chegada não pode ser menor que o de saída.",
        (),
    ),
    "abastecimento_manutencao": (
        "Abastecimento e manutenção",
        "Registra abastecimentos e manutenções por veículo, com alerta de revisão por km.",
        ("quilometragem",),
    ),
    "checklist": (
        "Checklist",
        "Torna obrigatório o checklist na saída e na chegada (avarias, combustível, fotos).",
        (),
    ),
    "mfa": (
        "Verificação em duas etapas (MFA)",
        "Exige segundo fator (TOTP) no login de administradores e autorizadores.",
        (),
    ),
    "relatorio_email": (
        "Relatórios por e-mail",
        "Permite enviar relatórios (CSV ou XLSX) por e-mail. Exige o servidor de e-mail "
        "configurado no ambiente do sistema.",
        (),
    ),
    "relatorio_whatsapp": (
        "Relatórios por WhatsApp",
        "Permite compartilhar o arquivo do relatório pelo WhatsApp (pelo próprio aparelho).",
        (),
    ),
    "notificacao_email_criticos": (
        "E-mail para notificações críticas",
        "Envia um aviso genérico por e-mail quando houver uma notificação crítica (sem dados da "
        "requisição). Exige o servidor de e-mail configurado no ambiente do sistema.",
        (),
    ),
    "motorista": (
        "Perfil Motorista",
        "Permite atribuir um motorista à viagem autorizada. O motorista vê apenas as viagens "
        "atribuídas a ele.",
        (),
    ),
    "motorista_registra": (
        "Motorista registra saída e chegada",
        "O motorista passa a registrar a saída e a chegada das próprias viagens (km e "
        "checklist continuam obrigatórios quando ligados).",
        ("motorista",),
    ),
    "motorista_localizacao": (
        "Localização do motorista",
        "O motorista só registra saída e chegada informando a localização (latitude e "
        "longitude); sem ela o registro é bloqueado. Apenas administradores veem as coordenadas.",
        ("motorista_registra",),
    ),
    "vigilante_km": (
        "KM pelo vigilante",
        "O vigilante informa o km de saída ao registrar a saída e o km de chegada ao registrar "
        "a chegada. Desligado, o vigilante registra sem informar km.",
        ("quilometragem",),
    ),
    "autorizador_decide_propria": (
        "Autorizador decide a própria requisição",
        "Permite que autorizadores e administradores autorizem ou neguem as requisições que "
        "eles mesmos criaram. Desligado, outra pessoa precisa decidir.",
        (),
    ),
    "autorizador_km": (
        "KM pelo autorizador",
        "Autorizadores e administradores informam o km de saída e o de chegada ao registrar a "
        "saída e a chegada. Desligado, registram sem informar km.",
        ("quilometragem",),
    ),
}
ROTULOS_RECURSOS = {k: v[0] for k, v in CATALOGO.items()}

MSG_MFA_SEM_SEGUNDO_FATOR = (
    "Para ligar a verificação em duas etapas, entre antes com o segundo fator (TOTP): "
    "caso contrário você perderia o próprio acesso."
)


def _dependencias_violadas(estado: dict[str, bool]) -> list[str]:
    msgs = []
    for chave, (_, _, exige) in CATALOGO.items():
        if estado[chave]:
            for dep in exige:
                if not estado[dep]:
                    msgs.append(
                        f"'{ROTULOS_RECURSOS[chave]}' exige '{ROTULOS_RECURSOS[dep]}' ligado."
                    )
    return msgs


class ServicoConfiguracoes:
    def __init__(self, repo: Repositorio) -> None:
        self._repo = repo

    def apresentar(self, cfg: Configuracoes, *, segundo_fator: bool) -> dict[str, Any]:
        estado = cfg.como_dict()
        itens = []
        for chave, (rotulo, descricao, exige) in CATALOGO.items():
            ligado = estado[chave]
            motivo: str | None = None
            if ligado:
                dependentes = [
                    ROTULOS_RECURSOS[o]
                    for o, (_, _, ex) in CATALOGO.items()
                    if chave in ex and estado[o]
                ]
                if dependentes:
                    motivo = "Desligue antes: " + ", ".join(dependentes) + "."
                acoes = [] if motivo else ["desligar"]
            else:
                faltando = [ROTULOS_RECURSOS[d] for d in exige if not estado[d]]
                if faltando:
                    motivo = "Ligue antes: " + ", ".join(faltando) + "."
                elif chave == "mfa" and not segundo_fator:
                    motivo = MSG_MFA_SEM_SEGUNDO_FATOR
                acoes = [] if motivo else ["ligar"]
            itens.append(
                {
                    "chave": chave,
                    "rotulo": rotulo,
                    "descricao": descricao,
                    "habilitado": ligado,
                    "exige": list(exige),
                    "motivo_indisponibilidade": motivo,
                    "acoes_permitidas": acoes,
                }
            )
        return {"recursos": estado, "itens": itens, "acoes_permitidas": ["editar"]}

    def obter(self, admin: Perfil, *, segundo_fator: bool) -> dict[str, Any]:
        exigir_admin(admin)
        return self.apresentar(self._repo.ler_configuracoes(), segundo_fator=segundo_fator)

    def atualizar(self, admin: Perfil, corpo: Any, *, segundo_fator: bool) -> dict[str, Any]:
        """Aceita um subconjunto dos flags (os não informados ficam como estão)."""
        exigir_admin(admin)
        pedido = validar_recursos(corpo)
        atual = self._repo.ler_configuracoes()
        novo = {**atual.como_dict(), **pedido}
        violadas = _dependencias_violadas(novo)
        if violadas:
            raise DependenciaRecurso(" ".join(violadas))
        mudou = {k: v for k, v in pedido.items() if atual.como_dict()[k] != v}
        if mudou.get("mfa") and not segundo_fator:
            raise SemPermissao(MSG_MFA_SEM_SEGUNDO_FATOR, codigo="mfa_requerido")
        if not mudou:
            return self.apresentar(atual, segundo_fator=segundo_fator)
        gravado = self._repo.atualizar_configuracoes(mudou, ator_id=admin.id)
        return self.apresentar(gravado, segundo_fator=segundo_fator)
