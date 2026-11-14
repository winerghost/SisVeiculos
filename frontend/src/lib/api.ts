import { supabase } from './supabase';
import type {
  Acao,
  AcaoOrganizar,
  ConfiguracaoIA,
  FraseSaudacao,
  ListaSaudacoes,
  RespostaSaudacao,
  ResumoGerado,
  StatusSaudacao,
  ResultadoTesteIA,
  ConfiguracoesAdmin,
  CorpoAcao,
  CorpoIA,
  CorpoChecklist,
  Convite,
  DadosVeiculo,
  EnvioRelatorio,
  ListaDestinatarios,
  ListaLocalizacoes,
  ListaMotoristas,
  FiltrosDashboard,
  FormatoRelatorio,
  Eu,
  ListaAcessos,
  ListaConvites,
  ListaNotificacoes,
  ListaRequisicoes,
  MomentoChecklist,
  ListaUsuarios,
  ListaVeiculosAdmin,
  HorarioReferencia,
  NovaRequisicao,
  NovoConvite,
  PainelId,
  PushConfig,
  ListaPasskeys,
  CredencialSerializada,
  RespostaOpcoesPasskey,
  InscricaoPush,
  PreviaRelatorio,
  Requisicao,
  RespostaChecklists,
  RespostaPainel,
  RespostaPaineis,
  RespostaRelatorios,
  RespostaFotoPerfil,
  Status,
  UploadFoto,
  UploadFotoPerfil,
  UploadFotoUsuario,
  UsuarioAdmin,
  Veiculo,
  VeiculoAdmin,
} from './tipos';

/** Erro devolvido pela API no formato {"erro": {"codigo", "mensagem"}}. */
export class ApiErro extends Error {
  constructor(
    public readonly status: number,
    public readonly codigo: string,
    mensagem: string,
  ) {
    super(mensagem);
  }
}

async function chamar<T>(caminho: string, init: RequestInit = {}): Promise<T> {
  const { data } = await supabase().auth.getSession();
  const token = data.session?.access_token;
  const cabecalhos = new Headers(init.headers);
  if (token) cabecalhos.set('Authorization', `Bearer ${token}`);
  if (init.body) cabecalhos.set('Content-Type', 'application/json');

  let resp: Response;
  try {
    resp = await fetch(`/api${caminho}`, { ...init, headers: cabecalhos, cache: 'no-store' });
  } catch {
    throw new ApiErro(0, 'sem_conexao', 'Sem conexão com o servidor. Tente novamente.');
  }
  let corpo: unknown = null;
  try {
    corpo = await resp.json();
  } catch {
    corpo = null;
  }
  if (!resp.ok) {
    const e = (corpo as { erro?: { codigo?: string; mensagem?: string } } | null)?.erro;
    // A mensagem vem do servidor e é exibida como veio; o fallback só cobre corpo ilegível.
    throw new ApiErro(
      resp.status,
      e?.codigo ?? 'erro',
      e?.mensagem ?? 'Não foi possível concluir a operação.',
    );
  }
  return corpo as T;
}

/** Nome do arquivo no Content-Disposition (filename* tem prioridade); sem cabeçalho, usa o padrão. */
function nomeDoArquivo(cabecalho: string | null, padrao: string): string {
  if (!cabecalho) return padrao;
  const utf8 = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(cabecalho);
  if (utf8?.[1]) {
    try {
      return decodeURIComponent(utf8[1].trim());
    } catch {
      /* cai para o filename simples */
    }
  }
  const simples = /filename\s*=\s*"?([^";]+)"?/i.exec(cabecalho);
  const nome = simples?.[1]?.trim();
  // Só o nome base: nunca aceita caminho vindo do cabeçalho.
  return nome ? (nome.split(/[\\/]/).pop() ?? padrao) : padrao;
}

/** Baixa um arquivo autenticado para a memória (Blob). Nada é gravado em cache ou storage. */
async function chamarArquivo(
  caminho: string,
  padrao: string,
): Promise<{ blob: Blob; nome: string }> {
  const { data } = await supabase().auth.getSession();
  const token = data.session?.access_token;
  const cabecalhos = new Headers();
  if (token) cabecalhos.set('Authorization', `Bearer ${token}`);
  let resp: Response;
  try {
    resp = await fetch(`/api${caminho}`, { headers: cabecalhos, cache: 'no-store' });
  } catch {
    throw new ApiErro(0, 'sem_conexao', 'Sem conexão com o servidor. Tente novamente.');
  }
  if (!resp.ok) {
    let corpo: { erro?: { codigo?: string; mensagem?: string } } | null = null;
    try {
      corpo = await resp.json();
    } catch {
      corpo = null;
    }
    throw new ApiErro(
      resp.status,
      corpo?.erro?.codigo ?? 'erro',
      corpo?.erro?.mensagem ?? 'Não foi possível gerar o arquivo.',
    );
  }
  return {
    blob: await resp.blob(),
    nome: nomeDoArquivo(resp.headers.get('Content-Disposition'), padrao),
  };
}

/** Monta a query só com os parâmetros preenchidos (os valores vão para a API como estão). */
function query(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}

const enc = encodeURIComponent;
const JSON_POST = (corpo?: unknown): RequestInit => ({
  method: 'POST',
  ...(corpo === undefined ? {} : { body: JSON.stringify(corpo) }),
});

/** Rotas de administração. O nome da ação (ativar, desativar, reenviar, revogar) vem de acoes_permitidas. */
export const admin = {
  veiculos: () => chamar<ListaVeiculosAdmin>('/admin/veiculos'),
  criarVeiculo: (d: DadosVeiculo) => chamar<VeiculoAdmin>('/admin/veiculos', JSON_POST(d)),
  editarVeiculo: (id: string, d: DadosVeiculo) =>
    chamar<VeiculoAdmin>(`/admin/veiculos/${enc(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(d),
    }),
  acaoVeiculo: (id: string, acao: 'ativar' | 'desativar') =>
    chamar<VeiculoAdmin>(`/admin/veiculos/${enc(id)}/${acao}`, JSON_POST()),
  /** Só com `rotacionar_codigo` em acoes_permitidas; invalida o QR impresso e devolve o novo código. */
  rotacionarCodigoVeiculo: (id: string) =>
    chamar<VeiculoAdmin>(`/admin/veiculos/${enc(id)}/rotacionar-codigo`, JSON_POST()),

  usuarios: (p: { q?: string; role?: string; ativo?: string; limite: number; offset: number }) =>
    chamar<ListaUsuarios>(`/admin/usuarios${query(p)}`),
  usuario: (id: string) => chamar<UsuarioAdmin>(`/admin/usuarios/${enc(id)}`),
  alterarUsuario: (id: string, d: Partial<Pick<UsuarioAdmin, 'nome' | 'unidade' | 'role'>>) =>
    chamar<UsuarioAdmin>(`/admin/usuarios/${enc(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(d),
    }),
  acaoUsuario: (id: string, acao: 'ativar' | 'desativar') =>
    chamar<UsuarioAdmin>(`/admin/usuarios/${enc(id)}/${acao}`, JSON_POST()),
  /** Só aparece com `remover_foto` em acoes_permitidas; responde com o usuário atualizado. */
  removerFotoUsuario: (id: string) =>
    chamar<UsuarioAdmin>(`/admin/usuarios/${enc(id)}/foto`, { method: 'DELETE' }),
  /** Só com `trocar_foto` em acoes_permitidas. */
  solicitarUploadFotoUsuario: (id: string, d: { mime: string; tamanho: number }) =>
    chamar<UploadFotoUsuario>(`/admin/usuarios/${enc(id)}/foto/upload-url`, JSON_POST(d)),
  confirmarFotoUsuario: (id: string, fotoId: string) =>
    chamar<UsuarioAdmin>(`/admin/usuarios/${enc(id)}/foto`, JSON_POST({ id: fotoId })),
  convidar: (d: NovoConvite) => chamar<{ convite: Convite }>('/admin/usuarios', JSON_POST(d)),

  convites: (p: { estado?: string; limite: number; offset: number }) =>
    chamar<ListaConvites>(`/admin/convites${query(p)}`),
  acaoConvite: (id: string, acao: 'reenviar' | 'revogar') =>
    chamar<{ convite: Convite }>(`/admin/convites/${enc(id)}/${acao}`, JSON_POST()),

  /** Histórico de acessos (só admin). Valores vão como estão; a API valida e devolve o erro. */
  acessos: (p: {
    usuario_id?: string;
    de?: string;
    ate?: string;
    limite: number;
    offset: number;
  }) => chamar<ListaAcessos>(`/admin/acessos${query(p)}`),

  /** Coordenadas gravadas nos registros de saída/chegada. A API responde 403 a quem não é admin. */
  localizacoes: (id: string) =>
    chamar<ListaLocalizacoes>(`/admin/requisicoes/${enc(id)}/localizacoes`),
  configuracoes: () => chamar<ConfiguracoesAdmin>('/admin/configuracoes'),
  atualizarConfiguracoes: (d: Record<string, boolean>) =>
    chamar<ConfiguracoesAdmin>('/admin/configuracoes', { method: 'PUT', body: JSON.stringify(d) }),
  ia: () => chamar<ConfiguracaoIA>('/admin/ia'),
  atualizarIA: (d: CorpoIA) =>
    chamar<ConfiguracaoIA>('/admin/ia', { method: 'PUT', body: JSON.stringify(d) }),
  testarIA: (provedor: string) =>
    chamar<ResultadoTesteIA>('/admin/ia/testar', {
      method: 'POST',
      body: JSON.stringify({ provedor }),
    }),
  saudacoes: (p: { status?: StatusSaudacao; ocasiao?: string }) =>
    chamar<ListaSaudacoes>(`/admin/ia/saudacoes${query(p)}`),
  criarSaudacao: (d: { ocasiao: string; texto: string }) =>
    chamar<FraseSaudacao>('/admin/ia/saudacoes', JSON_POST(d)),
  gerarSaudacoes: (d: { ocasiao: string; quantidade: number }) =>
    chamar<unknown>('/admin/ia/saudacoes/gerar', JSON_POST(d)),
  decidirSaudacao: (id: string, acao: 'aprovar' | 'rejeitar') =>
    chamar<FraseSaudacao>(`/admin/ia/saudacoes/${enc(id)}/${acao}`, JSON_POST()),
  excluirSaudacao: (id: string) =>
    chamar<{ id: string; excluida: boolean }>(`/admin/ia/saudacoes/${enc(id)}`, {
      method: 'DELETE',
    }),
};

export const api = {
  /** Saudação do dia, já pronta pela API (texto puro ou null). */
  saudacao: () => chamar<RespostaSaudacao>('/saudacao'),
  eu: () => chamar<Eu>('/eu'),
  /** Foto de perfil em 3 passos: pedir URL, enviar ao Storage (SDK) e confirmar. */
  solicitarUploadFotoPerfil: (d: { mime: string; tamanho: number }) =>
    chamar<UploadFotoPerfil>('/eu/foto/upload-url', JSON_POST(d)),
  confirmarFotoPerfil: (id: string) => chamar<RespostaFotoPerfil>('/eu/foto', JSON_POST({ id })),
  removerFotoPerfil: () => chamar<RespostaFotoPerfil>('/eu/foto', { method: 'DELETE' }),
  /** Aceite do convite: o usuário vem do JWT; o corpo só leva o token do link. */
  aceitarConvite: (token: string, nome?: string) =>
    chamar<Eu>('/convites/aceitar', JSON_POST(nome ? { token, nome } : { token })),
  veiculos: () => chamar<{ itens: Veiculo[] }>('/veiculos').then((r) => r.itens),
  /** Consulta pelo código lido no QR (404 uniforme se não existir). O código vai no corpo (POST), nunca na URL. Ler não autoriza nada. */
  veiculoPorCodigo: (codigo: string) =>
    chamar<Veiculo>('/veiculos/por-codigo', JSON_POST({ codigo })),
  /** `arquivadas: true` lista só as arquivadas do usuário; o padrão da API (false) as oculta. */
  requisicoes: (status?: Status, arquivadas?: boolean) =>
    chamar<ListaRequisicoes>(
      `/requisicoes?limite=100${status ? `&status=${status}` : ''}${arquivadas ? '&arquivadas=true' : ''}`,
    ),
  requisicao: (id: string) => chamar<Requisicao>(`/requisicoes/${encodeURIComponent(id)}`),
  /** Agora do servidor (Brasília) e sugestão de hora de saída; o cliente só exibe. */
  horarioReferencia: () => chamar<HorarioReferencia>('/requisicoes/horario-referencia'),
  criar: (dados: NovaRequisicao) =>
    chamar<Requisicao>('/requisicoes', { method: 'POST', body: JSON.stringify(dados) }),
  /** Seletor de motorista da autorização (staff). Recurso desligado: a API recusa. */
  motoristas: () => chamar<ListaMotoristas>('/motoristas'),
  /** Viagens atribuídas ao próprio motorista (Perfil Motorista). */
  viagensMotorista: (arquivadas?: boolean) =>
    chamar<ListaRequisicoes>(`/motorista/viagens${arquivadas ? '?arquivadas=true' : ''}`),
  viagemMotorista: (id: string) => chamar<Requisicao>(`/motorista/viagens/${enc(id)}`),
  /** Arquivar é pessoal e só com `arquivar`/`desarquivar` em acoes_permitidas; sem corpo. */
  organizar: (id: string, acao: AcaoOrganizar) =>
    chamar<Requisicao>(`/requisicoes/${enc(id)}/${acao}`, JSON_POST()),
  organizarViagem: (id: string, acao: AcaoOrganizar) =>
    chamar<Requisicao>(`/motorista/viagens/${enc(id)}/${acao}`, JSON_POST()),
  /** Troca o motorista (só com `atribuir_motorista` em acoes_permitidas); a API valida. */
  atribuirMotorista: (id: string, motoristaId: string) =>
    chamar<Requisicao>(
      `/requisicoes/${enc(id)}/motorista`,
      JSON_POST({ motorista_id: motoristaId }),
    ),
  /** Ações: autorizar, negar, saida, chegada, cancelar (rotas da API). */
  executar: (id: string, rota: RotaAcao, corpo?: CorpoAcao) =>
    chamar<Requisicao>(`/requisicoes/${encodeURIComponent(id)}/${rota}`, JSON_POST(corpo)),

  /** Painéis permitidos; 403 para quem não pode. O menu só aparece se esta chamada der certo. */
  paineisDashboard: () => chamar<RespostaPaineis>('/dashboards'),
  /** Os filtros vão como estão (só os preenchidos); o servidor valida e aplica os padrões. */
  painel: <T>(painel: PainelId, f: FiltrosDashboard = {}) =>
    chamar<RespostaPainel<T>>(`/dashboards/${enc(painel)}${query({ ...f })}`),

  /** Relatórios: o catálogo (filtrado pelo servidor) define telas, filtros e canais. */
  relatorios: () => chamar<RespostaRelatorios>('/relatorios'),
  /** Resumo narrativo do painel, com os mesmos filtros do GET (query string, sem corpo). */
  resumoPainel: (painel: PainelId, f: FiltrosDashboard = {}) =>
    chamar<ResumoGerado>(`/dashboards/${enc(painel)}/resumo${query({ ...f })}`, JSON_POST()),
  resumoRelatorio: (tipo: string, filtros: Record<string, string>) =>
    chamar<ResumoGerado>(`/relatorios/${enc(tipo)}/previa/resumo${query(filtros)}`, JSON_POST()),
  previaRelatorio: (tipo: string, filtros: Record<string, string>) =>
    chamar<PreviaRelatorio>(`/relatorios/${enc(tipo)}/previa${query(filtros)}`),
  arquivoRelatorio: (
    tipo: string,
    formato: FormatoRelatorio,
    via: 'download' | 'compartilhar',
    filtros: Record<string, string>,
  ) =>
    chamarArquivo(
      `/relatorios/${enc(tipo)}/arquivo${query({ ...filtros, formato, via })}`,
      `relatorio.${formato}`,
    ),
  destinatariosRelatorio: () => chamar<ListaDestinatarios>('/relatorios/destinatarios'),
  enviarRelatorioEmail: (
    tipo: string,
    d: {
      formato: FormatoRelatorio;
      filtros: Record<string, string | boolean | string[]>;
      destinatario_id: string;
    },
  ) => chamar<EnvioRelatorio>(`/relatorios/${enc(tipo)}/email`, JSON_POST(d)),
  envioRelatorio: (id: string) => chamar<EnvioRelatorio>(`/relatorios/envios/${enc(id)}`),

  /** Notificações do próprio usuário (todos os perfis). Texto e destino vêm prontos do servidor. */
  notificacoes: (p: { limite: number; offset: number; somente_nao_lidas: boolean }) =>
    chamar<ListaNotificacoes>(
      `/notificacoes${query({ ...p, somente_nao_lidas: String(p.somente_nao_lidas) })}`,
    ),
  contagemNotificacoes: () => chamar<{ nao_lidas: number }>('/notificacoes/contagem'),
  marcarNotificacaoLida: (id: string) =>
    chamar<{ id: string; lida_em: string }>(`/notificacoes/${enc(id)}/lida`, JSON_POST()),
  marcarNotificacoesLidas: () => chamar<{ marcadas: number }>('/notificacoes/lidas', JSON_POST()),

  /** Web Push: a API decide se está habilitado; a chave pública vai ao pushManager.subscribe. */
  pushConfig: () => chamar<PushConfig>('/push/config'),
  pushInscrever: (d: InscricaoPush) => chamar<unknown>('/push/inscricoes', JSON_POST(d)),
  pushRevogar: (endpoint: string) =>
    chamar<unknown>('/push/inscricoes', {
      method: 'DELETE',
      body: JSON.stringify({ endpoint }),
    }),

  /** Passkeys (WebAuthn): a API valida tudo; o cliente só repassa as opções e a credencial. */
  passkeys: () => chamar<ListaPasskeys>('/passkeys'),
  passkeysOpcoesRegistro: (d: { apelido?: string }) =>
    chamar<RespostaOpcoesPasskey>('/passkeys/registro/opcoes', JSON_POST(d)),
  passkeysConfirmarRegistro: (d: { credencial: CredencialSerializada; apelido?: string }) =>
    chamar<unknown>('/passkeys/registro/confirmar', JSON_POST(d)),
  passkeysOpcoesAutenticacao: () =>
    chamar<RespostaOpcoesPasskey>('/passkeys/autenticacao/opcoes', JSON_POST()),
  passkeysConfirmarAutenticacao: (d: { credencial: CredencialSerializada }) =>
    chamar<{ reautenticado_ate?: string | null }>('/passkeys/autenticacao/confirmar', JSON_POST(d)),
  passkeysRevogar: (id: string) => chamar<unknown>(`/passkeys/${enc(id)}`, { method: 'DELETE' }),

  /** Checklist (autorizador/admin). As URLs de leitura expiram: recarregue ao abrir. */
  checklists: (id: string) => chamar<RespostaChecklists>(`/requisicoes/${enc(id)}/checklist`),
  solicitarUploadFoto: (
    id: string,
    momento: MomentoChecklist,
    d: { mime: string; tamanho: number },
  ) => chamar<UploadFoto>(`/requisicoes/${enc(id)}/checklist/${momento}/fotos`, JSON_POST(d)),
  registrarChecklist: (id: string, momento: MomentoChecklist, d: CorpoChecklist) =>
    chamar<unknown>(`/requisicoes/${enc(id)}/checklist/${momento}`, JSON_POST(d)),
};

export type RotaAcao = 'autorizar' | 'negar' | 'saida' | 'chegada' | 'cancelar';

/** Mapeia o nome da ação (acoes_permitidas) para a rota e o rótulo. Só apresentação. */
export const ACOES: Record<
  Acao,
  { rota: RotaAcao; rotulo: string; variante: 'primario' | 'perigo' | 'sucesso' | 'contorno' }
> = {
  autorizar: { rota: 'autorizar', rotulo: 'Autorizar', variante: 'primario' },
  negar: { rota: 'negar', rotulo: 'Negar', variante: 'perigo' },
  registrar_saida: { rota: 'saida', rotulo: 'Registrar saída', variante: 'sucesso' },
  registrar_chegada: { rota: 'chegada', rotulo: 'Registrar chegada agora', variante: 'primario' },
  cancelar: { rota: 'cancelar', rotulo: 'Cancelar requisição', variante: 'contorno' },
};
