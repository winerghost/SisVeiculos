// Contrato da API Flask (backend/app/rotas/api.py). O cliente só exibe estes dados.

export type Status = 'solicitada' | 'autorizada' | 'em_uso' | 'concluida' | 'negada' | 'cancelada';

export type Acao = 'autorizar' | 'negar' | 'registrar_saida' | 'registrar_chegada' | 'cancelar';

export interface Eu {
  id: string;
  nome: string;
  unidade: string;
  rotulo_perfil: string;
  unidades: string[];
  role: string;
  permissoes: string[];
  recursos: Record<string, boolean>;
  /** URL assinada curta (~120 s) da própria foto, ou null. Nunca guardar nem registrar. */
  foto_url?: string | null;
}

export interface Veiculo {
  id: string;
  descricao: string;
  placa: string | null;
  disponivel: boolean;
  motivo_indisponibilidade: string | null;
  acoes_permitidas: string[];
}

/** Motorista designado (recurso opcional Perfil Motorista). */
export interface Motorista {
  id: string;
  nome: string;
}

/** GET /api/motoristas (staff): candidatos para o seletor da autorização. */
export interface ListaMotoristas {
  itens: Motorista[];
}

export interface Requisicao {
  id: string;
  usuario_id: string;
  nome_solicitante: string;
  unidade: string;
  hora_saida: string;
  trajeto: string;
  observacao: string | null;
  veiculo_id: string;
  status: Status;
  autorizado_por: string | null;
  autorizado_em: string | null;
  saida_real_em: string | null;
  chegada_real_em: string | null;
  saida_registrada_por: string | null;
  chegada_registrada_por: string | null;
  cancelada_por: string | null;
  cancelada_em: string | null;
  criado_em: string;
  atualizado_em: string;
  acoes_permitidas: string[];
  /** Mantido por compatibilidade; a tela usa `linha_do_tempo`. */
  historico: EventoHistorico[];
  /** Linha do tempo já pronta da API (estado, etapas e durações). O cliente só a exibe. */
  linha_do_tempo?: LinhaDoTempo | null;
  autorizado_por_nome: string | null;
  saida_registrada_por_nome: string | null;
  chegada_registrada_por_nome: string | null;
  /** URLs assinadas curtas das fotos de perfil (null sem foto ou conta inativa). */
  solicitante_foto_url?: string | null;
  autorizado_por_foto_url?: string | null;
  saida_registrada_por_foto_url?: string | null;
  chegada_registrada_por_foto_url?: string | null;
  veiculo_descricao: string | null;
  /** Motorista designado na autorização; ausente/null com o recurso desligado ou sem designação. */
  motorista?: Motorista | null;
  /** Odômetro gravado; null se nunca gravado (permanece visível com o recurso desligado). */
  km_saida: number | null;
  km_chegada: number | null;
  /** Campos extras que a API manda pedir por ação executável ({} com o recurso desligado). */
  campos_acao?: Partial<
    Record<Acao, { campos_obrigatorios: CampoAcao[]; checklist_pendente?: MomentoChecklist }>
  >;
}

// ---- Checklist (recurso opcional; backend/app/services/checklist.py) ----

export type MomentoChecklist = 'saida' | 'chegada';

export interface UploadFoto {
  foto_id: string;
  mime: string;
  caminho: string;
  url_upload: string;
  token: string;
}

/** POST /api/eu/foto/upload-url. O objeto de staging é `tmp/<usuario_id>/<id>` (bucket avatares). */
export interface UploadFotoPerfil {
  id: string;
  url_upload: string;
  token: string;
}

/** Upload de foto de OUTRO usuário (admin): `caminho` é o objeto de staging ditado pela API. */
export interface UploadFotoUsuario extends UploadFotoPerfil {
  caminho: string;
}

export interface RespostaFotoPerfil {
  foto_url: string | null;
}

export interface FotoEnviada {
  id: string;
  mime: string;
  tamanho: number;
}

export interface CorpoChecklist {
  combustivel: string;
  avarias: boolean;
  descricao_avarias?: string;
  fotos: FotoEnviada[];
}

export interface FotoChecklist {
  id: string;
  posicao: number;
  mime: string;
  tamanho: number;
  /** URL assinada de leitura (expira em ~300 s): nunca guardar nem cachear. */
  url: string | null;
}

export interface ChecklistRegistrado {
  id: string;
  momento: MomentoChecklist;
  combustivel: string;
  avarias: boolean;
  descricao_avarias: string | null;
  registrado_por_nome: string | null;
  registrado_em: string;
  fotos: FotoChecklist[];
}

export interface RespostaChecklists {
  requisicao_id: string;
  checklists: Record<MomentoChecklist, ChecklistRegistrado | null>;
  url_expira_em_segundos: number;
}

export type CampoKm = 'km_saida' | 'km_chegada';
/** Coordenadas: a API as lista quando exige a localização do aparelho (recurso opcional). */
export type CampoLocal = 'latitude' | 'longitude';
export type CampoAcao = CampoKm | CampoLocal;

/**
 * Corpo opcional do POST de ação: km (só os campos que a API pediu), coordenadas (idem; graus
 * decimais e precisão em metros) e, ao autorizar, o `motorista_id` escolhido (a API valida).
 */
export type CorpoAcao = Partial<Record<CampoAcao, number>> & {
  precisao_m?: number;
  motorista_id?: string;
};

/** GET /api/admin/requisicoes/<id>/localizacoes (só admin; 403 para os demais). */
export interface LocalizacaoRegistro {
  momento: MomentoChecklist;
  latitude: number;
  longitude: number;
  precisao_m: number | null;
  registrado_em: string;
  registrado_por_nome: string | null;
}

export interface ListaLocalizacoes {
  itens: LocalizacaoRegistro[];
}

export interface EventoHistorico {
  rotulo: string;
  quando: string;
  /** Nome de quem fez a ação, já resolvido pela API (pode faltar em versões antigas). */
  por?: string | null;
  /** Odômetro do evento, quando gravado. */
  km?: number;
}

// ---- Administração (backend/app/rotas/admin.py e convites.py) ----

export interface UsuarioAdmin {
  id: string;
  nome: string;
  unidade: string;
  role: string;
  ativo: boolean;
  email: string | null;
  rotulo_perfil: string;
  /** ISO 8601 do último login registrado; null = nunca acessou. */
  ultimo_acesso_em?: string | null;
  /** URL assinada curta da foto de perfil (null sem foto ou conta inativa). */
  foto_url?: string | null;
  acoes_permitidas: string[];
}

/** Registro de acesso (GET /api/admin/acessos). IP já vem mascarado (/24 ou /48). */
export interface Acesso {
  id: string;
  usuario_id: string;
  nome: string | null;
  ocorrido_em: string;
  metodo: string | null;
  aal: string | null;
  ip_mascarado: string | null;
  ua_familia: string | null;
  sessao_ref: string | null;
}

export interface ListaAcessos {
  itens: Acesso[];
  total: number;
  limite: number;
  offset: number;
}

export interface ListaUsuarios {
  itens: UsuarioAdmin[];
  total: number;
  /** Ações da coleção (ex.: `criar`); define se o botão de convidar aparece. */
  acoes_permitidas: string[];
  /** A API sinaliza que a busca por e-mail pode estar incompleta. */
  busca_parcial?: boolean;
}

export interface Convite {
  id: string;
  email: string;
  role: string;
  unidade: string;
  estado: string;
  criado_em: string;
  expira_em: string;
  usado_em: string | null;
  revogado_em: string | null;
  acoes_permitidas: string[];
}

export interface ListaConvites {
  itens: Convite[];
  total: number;
}

export interface NovoConvite {
  email: string;
  nome: string;
  unidade: string;
  role: string;
  validade_dias?: number;
}

export interface VeiculoAdmin {
  id: string;
  descricao: string;
  placa: string | null;
  ativo: boolean;
  requisicao_aberta: boolean;
  acoes_permitidas: string[];
}

export interface ListaVeiculosAdmin {
  itens: VeiculoAdmin[];
  acoes_permitidas: string[];
}

export interface DadosVeiculo {
  descricao: string;
  placa: string;
}

export interface ItemConfiguracao {
  chave: string;
  rotulo: string;
  descricao: string;
  habilitado: boolean;
  exige: string[];
  motivo_indisponibilidade: string | null;
  acoes_permitidas: string[];
}

export interface ConfiguracoesAdmin {
  recursos: Record<string, boolean>;
  itens: ItemConfiguracao[];
  acoes_permitidas: string[];
}

export interface ItemIA {
  chave: string;
  rotulo: string;
  descricao: string;
  habilitado: boolean;
  motivo_indisponibilidade: string | null;
  acoes_permitidas: string[];
}

export interface ProvedorIA {
  chave: string;
  rotulo: string;
  /** Só o booleano: a chave de API nunca chega ao frontend. */
  chave_configurada: boolean;
  modelos: string[];
  circuito_aberto: boolean;
}

export interface ConfiguracaoIA {
  servidor_habilitado: boolean;
  motivo_indisponibilidade: string | null;
  itens: ItemIA[];
  provedores: ProvedorIA[];
  provedor: string | null;
  modelo: string | null;
  provedor_reserva: string | null;
  teto_tokens_mes: number | null;
  teto_efetivo_tokens_mes: number;
  acoes_permitidas: string[];
  /** Chaves de provedor que a API permite testar; vazio = nenhum botão. */
  testes_disponiveis: string[];
}

/** Resposta de POST /admin/ia/testar. Só exibida, nunca interpretada no cliente. */
export interface ResultadoTesteIA {
  ok: boolean;
  provedor: string;
  modelo: string;
  latencia_ms: number | null;
  tokens_entrada: number;
  tokens_saida: number;
  tipo: string | null;
  mensagem: string;
}

export type CorpoIA = Partial<{
  mestre: boolean;
  saudacao: boolean;
  provedor: string | null;
  modelo: string | null;
  provedor_reserva: string | null;
  teto_tokens_mes: number | null;
}>;

export interface ListaRequisicoes {
  itens: Requisicao[];
  total: number;
}

export interface NovaRequisicao {
  nome_solicitante: string;
  unidade: string;
  hora_saida: string;
  trajeto: string;
  observacao?: string;
  veiculo_id: string;
}

// ---- Linha do tempo (presente em toda requisição apresentada pela API) ----

export type SituacaoEtapa = 'concluida' | 'atual' | 'pendente' | 'negada' | 'cancelada';

export interface EtapaLinhaDoTempo {
  chave: string;
  rotulo: string;
  situacao: SituacaoEtapa;
  em: string | null;
  /** Quem fez: `{nome}` ou `{papel}`, como a API enviar. */
  por: { nome?: string | null; papel?: string | null } | null;
  duracao_desde_anterior_s: number | null;
  decorrido_s: number | null;
}

export interface LinhaDoTempo {
  desfecho: 'em_andamento' | 'concluida' | 'negada' | 'cancelada';
  etapa_atual: number;
  total_etapas: number;
  texto_equivalente: string;
  gerado_em: string;
  etapas: EtapaLinhaDoTempo[];
}

// ---- Dashboards (GET /api/dashboards e /api/dashboards/<painel>) ----

export type PainelId =
  'operacao' | 'volume' | 'tempos' | 'unidades' | 'veiculos' | 'governanca' | 'opcionais';

/** Item do menu de painéis: `chave` vai na rota; `rotulo` (texto) vem do servidor. */
export interface PainelMenu {
  chave: string;
  rotulo: string;
}

export interface RespostaPaineis {
  paineis: PainelMenu[];
  fuso: string;
  periodo_padrao_dias: number;
  periodo_maximo_dias: number;
}

/** Filtros enviados como o usuário escolheu; o servidor valida e decide os padrões. */
export interface FiltrosDashboard {
  inicio?: string;
  fim?: string;
  unidade?: string;
  veiculo?: string;
  granularidade?: string;
}

export interface RespostaPainel<T> {
  painel: string;
  /** Filtros efetivos (com os padrões que o servidor aplicou). */
  filtros: Record<string, string | null | undefined>;
  dados: T;
  /** Só no painel `operacao`: lista na raiz da resposta. */
  itens?: Requisicao[];
  itens_truncados?: boolean;
}

export interface MaisAntiga {
  id: string;
  criado_em: string;
  decorrido_s: number;
}

export interface DadosOperacao {
  gerado_em: string;
  veiculos: { ativos: number; livres: number; reservados: number; em_uso: number };
  solicitadas: { total: number; mais_antiga: MaisAntiga | null };
  autorizadas_sem_saida: { total: number; mais_antiga: MaisAntiga | null };
  em_uso: { total: number; decorrido_medio_s: number | null; decorrido_max_s: number | null };
}

export interface DadosVolume {
  totais: {
    total: number;
    autorizadas: number;
    negadas: number;
    canceladas: number;
    solicitadas_pendentes: number;
    pct_autorizadas: number;
    pct_negadas: number;
    pct_canceladas: number;
  };
  serie: {
    periodo: string;
    total: number;
    autorizadas: number;
    negadas: number;
    canceladas: number;
  }[];
  granularidade: string;
  fuso: string;
}

export interface EstatisticaTempo {
  n: number;
  media_s: number | null;
  mediana_s: number | null;
  min_s?: number | null;
  max_s: number | null;
}

export interface DadosTempos {
  espera_autorizacao: EstatisticaTempo;
  duracao_uso: EstatisticaTempo;
  /** Assinado: negativo = antes do previsto. */
  saida_vs_prevista: EstatisticaTempo;
}

export interface DadosUnidades {
  itens: { unidade: string; requisicoes: number; usos: number; horas_uso: number }[];
}

export interface DadosVeiculos {
  horas_periodo: number;
  itens: {
    veiculo_id: string;
    ativo: boolean;
    usos: number;
    horas_uso: number;
    horas_ocupadas: number;
    /** 0..1 */
    taxa_ocupacao: number;
  }[];
}

export interface DadosGovernanca {
  perfis: Record<string, { ativos: number; inativos: number }>;
  convites: Record<string, number>;
  auditoria_admin: { id: string; ocorrido_em: string; tabela: string; acao: string }[];
  configuracoes: Record<string, boolean>;
}

export interface DadosOpcionais {
  quilometragem: { habilitado: boolean; km_rodado?: number; viagens?: number };
  checklist: {
    habilitado: boolean;
    total?: number;
    com_avaria?: number;
    /** Até 20 mais recentes. */
    requisicoes_com_avaria?: {
      requisicao_id: string;
      momento: 'saida' | 'chegada';
      registrado_em: string;
    }[];
  };
}

// ---- Relatórios (GET /api/relatorios e rotas filhas) ----

export interface OpcaoFiltro {
  valor: string;
  rotulo: string;
}

/** Filtro declarado pelo catálogo; o formulário é gerado só a partir disto. */
export interface FiltroRelatorio {
  nome: string;
  rotulo: string;
  tipo: 'data' | 'enum' | 'bool';
  opcoes: OpcaoFiltro[] | null;
  obrigatorio: boolean;
  padrao: string | boolean | null;
}

export interface RelatorioCatalogo {
  tipo: string;
  rotulo: string;
  descricao: string;
  filtros: FiltroRelatorio[];
}

export interface RespostaRelatorios {
  relatorios: RelatorioCatalogo[];
  canais: { download: boolean; email: boolean; whatsapp: boolean };
  /** Chave do painel -> relatório e parâmetros que o servidor associa a ele. */
  exportacao_paineis: Record<
    string,
    { tipo: string; parametros: Record<string, string | number | boolean | null> }
  >;
  limite_linhas: number;
  fuso: string;
}

export interface PreviaRelatorio {
  tipo: string;
  rotulo: string;
  filtros: Record<string, string | number | boolean | null | undefined>;
  colunas: { chave: string; rotulo: string }[];
  linhas: Record<string, string>[];
  total_linhas: number;
  truncado: boolean;
}

export interface DestinatarioRelatorio {
  id: string;
  nome: string;
}

export interface ListaDestinatarios {
  itens: DestinatarioRelatorio[];
  /** A lista devolvida é parcial. */
  truncado?: boolean;
}

export type FormatoRelatorio = 'csv' | 'xlsx';

export interface EnvioRelatorio {
  id: string;
  status: 'pendente' | 'enviando' | 'enviado' | 'falhou';
  erro_codigo?: string | null;
  /** Motivo da falha, pronto para exibir. */
  mensagem?: string | null;
}

/** Notificação do próprio usuário. Título, texto e destino chegam prontos do servidor. */
export interface Notificacao {
  id: string;
  tipo: string;
  titulo: string;
  texto: string;
  critica: boolean;
  criada_em: string;
  lida_em: string | null;
  /** Caminho interno relativo (ex.: /requisicoes/detalhe?id=...) ou null. */
  destino: string | null;
}

export interface ListaNotificacoes {
  itens: Notificacao[];
  total: number;
  nao_lidas: number;
}
