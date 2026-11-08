import { supabase } from './supabase';
import type {
  Acao,
  ConfiguracoesAdmin,
  CorpoAcao,
  CorpoChecklist,
  Convite,
  DadosVeiculo,
  Eu,
  ListaAcessos,
  ListaConvites,
  ListaRequisicoes,
  MomentoChecklist,
  ListaUsuarios,
  ListaVeiculosAdmin,
  NovaRequisicao,
  NovoConvite,
  Requisicao,
  RespostaChecklists,
  RespostaFotoPerfil,
  Status,
  UploadFoto,
  UploadFotoPerfil,
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

  configuracoes: () => chamar<ConfiguracoesAdmin>('/admin/configuracoes'),
  atualizarConfiguracoes: (d: Record<string, boolean>) =>
    chamar<ConfiguracoesAdmin>('/admin/configuracoes', { method: 'PUT', body: JSON.stringify(d) }),
};

export const api = {
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
  requisicoes: (status?: Status) =>
    chamar<ListaRequisicoes>(`/requisicoes?limite=100${status ? `&status=${status}` : ''}`),
  requisicao: (id: string) => chamar<Requisicao>(`/requisicoes/${encodeURIComponent(id)}`),
  criar: (dados: NovaRequisicao) =>
    chamar<Requisicao>('/requisicoes', { method: 'POST', body: JSON.stringify(dados) }),
  /** Ações: autorizar, negar, saida, chegada, cancelar (rotas da API). */
  executar: (id: string, rota: RotaAcao, corpo?: CorpoAcao) =>
    chamar<Requisicao>(`/requisicoes/${encodeURIComponent(id)}/${rota}`, JSON_POST(corpo)),

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
