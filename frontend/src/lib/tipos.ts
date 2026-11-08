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
  historico: EventoHistorico[];
  autorizado_por_nome: string | null;
  saida_registrada_por_nome: string | null;
  chegada_registrada_por_nome: string | null;
  /** URLs assinadas curtas das fotos de perfil (null sem foto ou conta inativa). */
  solicitante_foto_url?: string | null;
  autorizado_por_foto_url?: string | null;
  saida_registrada_por_foto_url?: string | null;
  chegada_registrada_por_foto_url?: string | null;
  veiculo_descricao: string | null;
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

export type CampoAcao = 'km_saida' | 'km_chegada';

/** Corpo opcional do POST /saida e /chegada: só os campos que a API pediu. */
export type CorpoAcao = Partial<Record<CampoAcao, number>>;

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
