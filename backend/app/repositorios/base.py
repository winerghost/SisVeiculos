"""Contrato de acesso a dados. Os services dependem só disto (fakes nos testes)."""

from __future__ import annotations

from typing import Any, Protocol

from app.modelos import Configuracoes, Perfil, Requisicao


class Repositorio(Protocol):
    def obter_perfil(self, usuario_id: str) -> Perfil | None: ...

    def ler_configuracoes(self) -> Configuracoes: ...

    def listar_veiculos(self) -> list[dict[str, Any]]:
        """Veículos ativos (id, descricao, placa)."""
        ...

    def obter_veiculo(self, veiculo_id: str) -> dict[str, Any] | None: ...

    def veiculos_ocupados(self) -> set[str]:
        """Ids de veículos com requisição aberta (solicitada, autorizada, em_uso)."""
        ...

    def veiculo_ocupado(self, veiculo_id: str) -> bool:
        """True se o veículo tem requisição aberta (consulta pontual, limit 1)."""
        ...

    def inserir_requisicao(self, dados: dict[str, Any]) -> Requisicao:
        """Insere como `solicitada`. Levanta VeiculoOcupado se o índice parcial recusar."""
        ...

    def obter_requisicao(self, requisicao_id: str) -> Requisicao | None: ...

    def listar_requisicoes(
        self,
        *,
        usuario_id: str | None,
        status: str | None,
        limite: int,
        offset: int,
        motorista_id: str | None = None,
        ou_status: tuple[str, ...] | None = None,
    ) -> list[Requisicao]:
        """`usuario_id` não nulo restringe ao dono e `motorista_id` ao motorista atribuído
        (anti-IDOR aplicado na consulta). `ou_status` (vigilante) amplia o filtro de dono para
        "dono OU status na lista"."""
        ...

    def contar_requisicoes(
        self,
        *,
        usuario_id: str | None,
        status: str | None,
        motorista_id: str | None = None,
        ou_status: tuple[str, ...] | None = None,
    ) -> int:
        """Total com os mesmos filtros de `listar_requisicoes` (sem paginação)."""
        ...

    def nomes_perfis(self, ids: set[str]) -> dict[str, str]:
        """Só `perfis.nome` por id (nunca e-mail nem outros dados pessoais)."""
        ...

    def descricoes_veiculos(self, ids: set[str]) -> dict[str, str]:
        """`veiculos.descricao` por id (inclui veículos inativos, para o histórico)."""
        ...

    def transicionar(
        self, requisicao_id: str, status_atual: str, campos: dict[str, Any]
    ) -> Requisicao | None:
        """UPDATE condicional (`where status = status_atual`). None se ninguém casou."""
        ...

    def atribuir_motorista(
        self,
        requisicao_id: str,
        motorista_id: str | None,
        status_permitidos: list[str],
        *,
        ator_id: str,
    ) -> Requisicao | None:
        """UPDATE condicional de `motorista_id` (`where status in status_permitidos`).
        None se ninguém casou. `motorista_id` None desatribui. O trigger do banco exige
        `motorista_alterado_por` = `ator_id` (autorizador/admin do JWT) no MESMO UPDATE; sem ele,
        AG002. Só chamar quando `motorista_id` realmente muda (senão AG004)."""
        ...

    def registrar_transicao_motorista(
        self,
        *,
        requisicao_id: str,
        momento: str,
        ator_id: str,
        latitude: float | None,
        longitude: float | None,
        precisao_m: float | None,
        km_saida: int | None,
        km_chegada: int | None,
    ) -> dict[str, Any]:
        """RPC `registrar_transicao_motorista` (uma transação: localização + transição).
        `momento`: 'saida' ou 'chegada'. Devolve {status, localizacao_gravada}. Coordenadas são
        dado sensível: nunca logar nem repassar a outro destino."""
        ...

    def listar_localizacoes(self, requisicao_id: str) -> list[dict[str, Any]]:
        """Coordenadas da viagem (`localizacoes_viagem`), por `registrado_em`: momento,
        latitude, longitude, precisao_m, registrado_em, registrado_por (id). SÓ para o admin."""
        ...

    # -- administração ------------------------------------------------------------------
    # Contrato da migration 15: cada método abaixo é UMA transação curta (um comando
    # PostgREST/RPC), nunca mistura escrita de requisição com escrita administrativa e
    # repete a operação em 55P03/40P01. Toda escrita leva o `ator_id` (admin ativo).
    def listar_perfis(
        self,
        *,
        role: str | None,
        ativo: bool | None,
        busca: str | None = None,
        ids_email: frozenset[str] = frozenset(),
        limite: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        """`busca` casa com `nome` (ilike, parametrizado); `ids_email` são ids já resolvidos
        pelo Auth cujo e-mail casa com a busca (OU lógico). Filtros combinam em E."""
        ...

    def contar_perfis(
        self,
        *,
        role: str | None,
        ativo: bool | None,
        busca: str | None = None,
        ids_email: frozenset[str] = frozenset(),
    ) -> int: ...

    def obter_perfil_admin(self, usuario_id: str) -> dict[str, Any] | None: ...

    def contar_admins_ativos(self) -> int: ...

    def atualizar_perfil(
        self, usuario_id: str, campos: dict[str, Any], *, ator_id: str
    ) -> dict[str, Any] | None:
        """UPDATE de nome/unidade/role/ativo com `alterado_por` = ator. None se não existe."""
        ...

    def inserir_convite(self, dados: dict[str, Any]) -> dict[str, Any]:
        """Sem `token_hash` no retorno. Índice de e-mail aberto => ConviteAbertoExistente."""
        ...

    def obter_convite(self, convite_id: str) -> dict[str, Any] | None: ...

    def obter_convite_aberto_por_email(self, email: str) -> dict[str, Any] | None:
        """Convite sem uso nem revogação (inclui expirado, que ainda bloqueia novo convite)."""
        ...

    def listar_convites(
        self, *, estado: str | None, agora: str, limite: int, offset: int
    ) -> list[dict[str, Any]]: ...

    def contar_convites(self, *, estado: str | None, agora: str) -> int: ...

    def revogar_convite(self, convite_id: str, *, ator_id: str) -> dict[str, Any] | None:
        """Revoga se ainda aberto (condicional). None se ninguém casou."""
        ...

    def reenviar_convite(
        self, convite_id: str, *, ator_id: str, token_hash: str, expira_em: str
    ) -> dict[str, Any]:
        """RPC atômica: revoga o convite (FOR UPDATE) e insere o novo com o mesmo e-mail, papel
        e unidade. Devolve o novo convite sem `token_hash`. AG002 -> SemPermissao, AG007 ->
        ConviteInvalido, AG038 -> ConviteEncerrado, AG039 -> ErroInterno."""
        ...

    def consumir_convite(self, token_hash: str, usuario_id: str, nome: str) -> Perfil:
        """RPC atômica: valida, cria o perfil e marca o convite como usado."""
        ...

    def listar_veiculos_admin(self, *, ativo: bool | None) -> list[dict[str, Any]]: ...

    def inserir_veiculo(self, dados: dict[str, Any], *, ator_id: str) -> dict[str, Any]: ...

    def atualizar_veiculo(
        self, veiculo_id: str, campos: dict[str, Any], *, ator_id: str
    ) -> dict[str, Any] | None: ...

    def atualizar_configuracoes(self, campos: dict[str, bool], *, ator_id: str) -> Configuracoes:
        """Grava só os flags informados. A auditoria sai do trigger do banco."""
        ...

    def registrar_auditoria(
        self,
        *,
        tabela: str,
        registro_id: str | None,
        ator_id: str,
        detalhes: dict[str, Any],
        acao: str = "acao_administrativa",
    ) -> None:
        """Só para o que os triggers não cobrem (ex.: troca de nome). Sem dados pessoais.

        `acao` deve constar no CHECK ck_auditoria_acao (ex.: 'localizacao_lida')."""
        ...

    # -- checklist (recurso opcional; migrations 19 a 22) ---------------------------------
    def momentos_checklist(self, requisicao_ids: set[str]) -> dict[str, set[str]]:
        """Momentos ('saida'/'chegada') com checklist já registrado, por requisição."""
        ...

    def obter_checklists(self, requisicao_id: str) -> list[dict[str, Any]]:
        """Checklists da requisição, cada um com a lista `fotos` (id, posicao, mime_type,
        tamanho_bytes, caminho). Inclui `descricao_avarias`: só o service decide quem vê."""
        ...

    def registrar_checklist(
        self,
        *,
        requisicao_id: str,
        momento: str,
        combustivel: str,
        avarias: bool,
        descricao_avarias: str | None,
        registrado_por: str,
        fotos: list[dict[str, Any]],
    ) -> str:
        """Grava checklist e fotos numa transação (RPC). `fotos`: id, mime_type, tamanho_bytes
        (a posição é a ordem). Devolve o id do checklist."""
        ...

    # -- histórico de acessos (migration 20261107122430; só via RPC, nunca SELECT direto) -----
    def registrar_acesso_login(
        self,
        *,
        usuario_id: str,
        session_id: str,
        ocorrido_em: str,
        metodo: str | None,
        aal: str | None,
        ip_mascarado: str | None,
        ua_familia: str | None,
    ) -> bool:
        """True se gravou, False se a sessão já estava registrada (idempotente). Qualquer falha
        vira `FalhaRegistroAcesso(codigo)`, sem a mensagem do banco."""
        ...

    def listar_acessos_login(
        self,
        *,
        usuario_id: str | None,
        de: str | None,
        ate: str | None,
        limite: int,
        offset: int,
    ) -> dict[str, Any]:
        """{total, limite, offset, itens[]} (itens sem session_id; só `sessao_ref`)."""
        ...

    def ultimo_acesso_por_usuario(self, usuario_ids: list[str]) -> dict[str, str]:
        """id -> instante do último acesso (ISO); quem nunca acessou não aparece. Máx. 100 ids."""
        ...

    # -- foto de perfil (migrations 25a e 25c; escrita só via RPC, leitura só de perfil_fotos) ---
    def trocar_foto_perfil(
        self, *, usuario_id: str, foto_id: str, mime: str, tamanho: int
    ) -> str | None:
        """RPC `trocar_foto_perfil`: registra a nova foto e marca a anterior como removida.
        Devolve o caminho da foto ANTERIOR (None se não havia). `usuario_id` vem do JWT."""
        ...

    def trocar_foto_perfil_por(
        self, *, usuario_id: str, ator_id: str, foto_id: str, mime: str, tamanho: int
    ) -> str | None:
        """RPC `trocar_foto_perfil_por`: admin ativo troca a foto de outra conta ATIVA (senão
        AG022 -> SemPermissao, AG017 -> conta_inativa). Devolve o caminho anterior (ou None)."""
        ...

    def remover_foto_perfil_por(self, *, usuario_id: str, ator_id: str) -> str | None:
        """RPC `remover_foto_perfil_por`: remoção lógica; idempotente. `ator_id` = dono ou admin
        ativo (senão AG022 vira SemPermissao). Devolve o caminho removido (None se não havia)."""
        ...

    def fotos_ativas(self, usuario_ids: list[str]) -> dict[str, str]:
        """usuario_id -> caminho da foto ATIVA (removida_em nulo). Em lote, sem filtrar inativos."""
        ...

    def ids_ativos(self, usuario_ids: list[str]) -> set[str]:
        """Subconjunto dos ids cuja conta (perfis.ativo) está ativa."""
        ...

    # -- dashboards (migration 20261108120000; RPCs jsonb, só service_role) -------------------
    # `inicio`/`fim` são ISO 8601 com fuso, janela [inicio, fim) já validada pelo service. O
    # repositório só repassa; parâmetro inválido no banco (AG023) vira RequisicaoInvalida.
    def obter_requisicoes_por_ids(self, ids: list[str]) -> list[Requisicao]:
        """Requisições por id, em lote (sem N+1). Ordem não garantida; ids inexistentes somem."""
        ...

    def dash_operacao_agora(
        self, *, unidade: str | None, veiculo: str | None
    ) -> dict[str, Any]: ...

    def dash_volume_desfecho(
        self,
        *,
        inicio: str,
        fim: str,
        unidade: str | None,
        veiculo: str | None,
        granularidade: str,
    ) -> dict[str, Any]: ...

    def dash_tempos_fluxo(
        self, *, inicio: str, fim: str, unidade: str | None, veiculo: str | None
    ) -> dict[str, Any]: ...

    def dash_uso_unidade(
        self, *, inicio: str, fim: str, unidade: str | None, veiculo: str | None
    ) -> dict[str, Any]: ...

    def dash_uso_veiculo(
        self, *, inicio: str, fim: str, unidade: str | None, veiculo: str | None
    ) -> dict[str, Any]: ...

    def dash_opcionais(
        self, *, inicio: str, fim: str, unidade: str | None, veiculo: str | None
    ) -> dict[str, Any]: ...

    def dash_governanca(self) -> dict[str, Any]: ...

    # -- relatórios (migration 20261109120000; RPCs jsonb, só service_role) ------------------
    # Janela [inicio, fim) em ISO 8601 já validada pelo service. `usuario` não nulo restringe às
    # requisições daquele usuário (o service força o id do JWT para o solicitante). Listas pedem
    # `limite` (o service usa o limite máximo + 1 para detectar excesso).
    def rel_movimentacao(
        self,
        *,
        inicio: str,
        fim: str,
        campo_data: str,
        unidade: str | None,
        veiculo: str | None,
        usuario: str | None,
        incluir_nomes: bool,
        incluir_trajeto: bool,
        limite: int,
        incluir_observacao: bool = False,
    ) -> dict[str, Any]: ...

    def rel_resumo(
        self,
        *,
        inicio: str,
        fim: str,
        agrupar: str,
        unidade: str | None,
        veiculo: str | None,
        usuario: str | None,
    ) -> dict[str, Any]: ...

    def rel_tempos(
        self,
        *,
        inicio: str,
        fim: str,
        agrupar: str,
        unidade: str | None,
        veiculo: str | None,
        usuario: str | None,
    ) -> dict[str, Any]: ...

    def rel_pendencias(
        self,
        *,
        unidade: str | None,
        veiculo: str | None,
        usuario: str | None,
        incluir_nomes: bool,
        limite: int,
    ) -> dict[str, Any]: ...

    def rel_auditoria(
        self, *, inicio: str, fim: str, acao: str | None, limite: int
    ) -> dict[str, Any]: ...

    def lancamentos_vigilante_km(
        self,
        *,
        inicio: str,
        fim: str,
        unidade: str | None,
        veiculo: str | None,
        usuario: str | None,
        incluir_nomes: bool,
    ) -> dict[str, dict[str, Any]]:
        """Viagens concluídas na janela (mesmos filtros de `rel_opcionais_km`) cuja saída ou
        chegada foi registrada por um VIGILANTE: `{id completo: {"veiculo_id", "saida_real_em",
        "lancado_por": nome | None}}`; conta só o campo (km_saida/km_chegada) não nulo cujo
        registrador é vigilante. O nome só é lido com `incluir_nomes`.
        Origem: `saida_registrada_por`/
        `chegada_registrada_por` + `perfis.role` (sem coluna nova)."""
        ...

    def rel_opcionais_km(
        self,
        *,
        inicio: str,
        fim: str,
        unidade: str | None,
        veiculo: str | None,
        usuario: str | None,
        limite: int,
    ) -> dict[str, Any]: ...

    def rel_opcionais_avarias(
        self,
        *,
        inicio: str,
        fim: str,
        unidade: str | None,
        veiculo: str | None,
        usuario: str | None,
        limite: int,
    ) -> dict[str, Any]:
        """Nunca inclui a descrição das avarias (texto livre, pode ter dado pessoal)."""
        ...

    def reservar_envio_email(
        self,
        *,
        solicitante: str,
        destinatario: str,
        tipo: str,
        formato: str,
        filtros: dict[str, Any],
        max_por_hora: int,
        max_por_usuario_hora: int | None = None,
    ) -> dict[str, Any]:
        """RPC atômica: valida recurso, solicitante, destinatário ativo e cotas por hora (global
        e por usuário; AG025, e a mensagem do banco da cota por usuário cita "usuário").
        Devolve `{"id": str, "duplicado": bool}`. `duplicado=True`: já havia envio idêntico
        pendente/enviando nos últimos 30 s; nada foi inserido nem a cota consumida e o chamador
        NÃO deve enviar outro e-mail. AG024/25/26/27 viram erros de API."""
        ...

    def reaper_envios_email(self, minutos: int = 15) -> int:
        """RPC: move envios pendente/enviando parados há mais de `minutos` para falhou/timeout.
        Devolve quantos moveu. AG027 (fora de 5..1440) vira RequisicaoInvalida."""
        ...

    def marcar_envio_email(
        self, envio_id: str, status: str, erro_codigo: str | None = None
    ) -> None:
        """`enviando`, `enviado` ou `falhou` (com código curto). Transição validada no banco."""
        ...

    def obter_envio_email(self, envio_id: str) -> dict[str, Any] | None:
        """id, solicitado_por, status, erro_codigo (nunca e-mail nem arquivo)."""
        ...

    def registrar_auditoria_relatorio(
        self, *, acao: str, registro_id: str | None, ator_id: str, detalhes: dict[str, Any]
    ) -> None:
        """`relatorio_gerado` ou `relatorio_enviado`. `detalhes`: só tipo, filtros, formato, via,
        destinatario_id, total_linhas. Nunca conteúdo do relatório nem e-mail."""
        ...

    # -- notificações (migration 20261110120000). O dono vem SEMPRE do JWT, nunca do cliente. -----
    def listar_notificacoes(
        self, usuario: str, *, limite: int, offset: int, somente_nao_lidas: bool
    ) -> dict[str, Any]:
        """{total, nao_lidas, limite, offset, itens:[{id, tipo, requisicao_id, referencia_id,
        codigo, critica, criada_em, lida_em}]}. Só as notificações de `usuario`."""
        ...

    def contar_notificacoes_nao_lidas(self, usuario: str) -> int: ...

    def marcar_notificacao_lida(self, usuario: str, notificacao_id: str) -> dict[str, Any]:
        """{id, lida_em}. Inexistente ou de outro usuário: AG029 (NaoEncontrado). Idempotente."""
        ...

    def marcar_todas_lidas(self, usuario: str) -> int:
        """Quantidade marcada agora."""
        ...

    def registrar_notificacao_sistema(self, tipo: str, detalhe_codigo: str) -> int:
        """`sistema_falha_envio` para os admins ativos (dedupe de 1 h no banco). Devolve `criadas`.
        `detalhe_codigo` casa com ^[a-z0-9_.-]{1,40}$ (AG030 caso contrário)."""
        ...

    def reservar_emails_notificacao(self, limite: int) -> list[dict[str, Any]]:
        """Lote do despachante: [{id, usuario_id, tipo, requisicao_id, referencia_id, codigo}]."""
        ...

    def marcar_email_notificacao(
        self, notificacao_id: str, status: str, erro_codigo: str | None = None
    ) -> None:
        """`enviado`, `falhou` ou `ignorado`; `erro_codigo` curto (^[A-Za-z0-9_.-]{1,40}$)."""
        ...

    def purgar_notificacoes(self, dias: int = 90) -> int: ...
