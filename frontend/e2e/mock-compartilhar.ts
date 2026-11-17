import type { Page, Route } from '@playwright/test';
import { mockarApi, type Opcoes } from './mock-api';
import { gruposAcao } from './recursos';

// Fixtures do compartilhamento da requisição como imagem (contratos: 01-analise-plano.md §5.1).
// As regras (ações permitidas, nomes ocultos para o solicitante, linha do tempo) são do backend;
// aqui só repetimos respostas prontas no formato da API. O front nunca as calcula.

export const ID_CONCLUIDA = 'c0a1b2c3-1111-4111-8111-000000000001';
export const ID_SOLICITADA = 'd4e5f6a7-2222-4222-8222-000000000002';
export const ID_SEM_RECURSO = 'e8f9a0b1-3333-4333-8333-000000000003';
export const ID_AUTORIZADA = 'f2a3b4c5-4444-4444-8444-000000000004';
export const ID_DO_SOLICITANTE = 'a7b8c9d0-5555-4555-8555-000000000005';

/** Nomes da equipe: nunca podem aparecer no cartão gerado para o solicitante. */
export const NOMES_EQUIPE = ['Ana Souza', 'Carlos Lima', 'Paulo Reis'];
export const OBSERVACAO = 'Levar processos físicos ao cartório.';
export const ACAO_IMAGEM = 'compartilhar_imagem';

export const FOTO_URL = 'https://exemplo.supabase.co/storage/v1/object/sign/avatares/p.jpg?token=t';
const VEICULO_ID = '11111111-1111-4111-8111-111111111111';
const SOLICITANTE_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';

type Sit = 'concluida' | 'atual' | 'pendente' | 'negada' | 'cancelada';
type Por = { nome?: string; papel?: string } | null;

function etapa(chave: string, rotulo: string, situacao: Sit, em: string | null, por: Por) {
  return {
    chave,
    rotulo,
    situacao,
    em,
    por,
    duracao_desde_anterior_s: em ? 1800 : null,
    decorrido_s: situacao === 'atual' ? 120 : null,
  };
}

function linha(desfecho: string, etapas: ReturnType<typeof etapa>[]) {
  const atual = etapas.findIndex((e) => e.situacao === 'atual');
  return {
    desfecho,
    etapa_atual: atual < 0 ? etapas.length : atual + 1,
    total_etapas: etapas.length,
    texto_equivalente: etapas.map((e) => `${e.rotulo}: ${e.situacao}`).join('; '),
    gerado_em: '2026-11-17T13:00:00Z',
    etapas,
  };
}

function base(id: string, acoes: string[], grupoCancelar: 'solicitar' | 'decidir') {
  return {
    id,
    usuario_id: SOLICITANTE_ID,
    nome_solicitante: 'Marcos Silva',
    unidade: 'UEA',
    trajeto: 'Sede -> Fórum Federal',
    observacao: null as string | null,
    veiculo_id: VEICULO_ID,
    veiculo_descricao: 'Veículo 01',
    autorizado_por: null as string | null,
    saida_registrada_por: null as string | null,
    chegada_registrada_por: null as string | null,
    cancelada_por: null,
    cancelada_em: null,
    criado_em: '2026-11-10T11:00:00Z',
    atualizado_em: '2026-11-10T11:00:00Z',
    hora_saida: '2026-11-10T12:00:00Z',
    autorizado_em: null as string | null,
    autorizado_por_nome: null as string | null,
    saida_real_em: null as string | null,
    chegada_real_em: null as string | null,
    saida_registrada_por_nome: null as string | null,
    chegada_registrada_por_nome: null as string | null,
    km_saida: null as number | null,
    km_chegada: null as number | null,
    km_percorrido: null as number | null,
    motorista: null as { id: string; nome: string } | null,
    situacao_horario: null as string | null,
    solicitante_foto_url: null,
    autorizado_por_foto_url: null,
    saida_registrada_por_foto_url: null,
    chegada_registrada_por_foto_url: null,
    historico: [],
    arquivada: false,
    campos_acao: {},
    acoes_permitidas: acoes,
    grupos_acao: gruposAcao(acoes, grupoCancelar),
  };
}

/** Concluída, vista pela equipe: motorista, km, observação e linha do tempo completa. */
function concluida() {
  const ana = { nome: 'Ana Souza' };
  const acoes = ['arquivar', ACAO_IMAGEM];
  return {
    ...base(ID_CONCLUIDA, acoes, 'decidir'),
    status: 'concluida',
    observacao: OBSERVACAO,
    autorizado_por: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    autorizado_em: '2026-11-10T11:30:00Z',
    autorizado_por_nome: 'Ana Souza',
    saida_real_em: '2026-11-10T12:05:00Z',
    saida_registrada_por_nome: 'Ana Souza',
    chegada_real_em: '2026-11-10T15:40:00Z',
    chegada_registrada_por_nome: 'Ana Souza',
    km_saida: 12000,
    km_chegada: 12085,
    km_percorrido: 85,
    motorista: { id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc', nome: 'Carlos Lima' },
    atualizado_em: '2026-11-10T15:40:00Z',
    linha_do_tempo: linha('concluida', [
      etapa('solicitada', 'Solicitada', 'concluida', '2026-11-10T11:00:00Z', {
        nome: 'Marcos Silva',
      }),
      etapa('autorizada', 'Autorizada', 'concluida', '2026-11-10T11:30:00Z', ana),
      etapa('saida', 'Saída registrada', 'concluida', '2026-11-10T12:05:00Z', ana),
      etapa('chegada', 'Chegada registrada', 'concluida', '2026-11-10T15:40:00Z', ana),
    ]),
  };
}

/** Solicitada, sem km nem motorista: o cartão mínimo (mais baixo que o da concluída). */
function solicitada(id: string, acoes: string[]) {
  return {
    ...base(id, acoes, 'solicitar'),
    status: 'solicitada',
    situacao_horario: 'atrasado',
    linha_do_tempo: linha('em_andamento', [
      etapa('solicitada', 'Solicitada', 'atual', '2026-11-10T11:00:00Z', { nome: 'Marcos Silva' }),
      etapa('autorizada', 'Autorizada', 'pendente', null, null),
      etapa('saida', 'Saída registrada', 'pendente', null, null),
      etapa('chegada', 'Chegada registrada', 'pendente', null, null),
    ]),
  };
}

/** Autorizada, vista pela equipe: há botões de ação no fim da tela (Registrar saída, Cancelar). */
function autorizada() {
  const acoes = ['registrar_saida', 'cancelar', ACAO_IMAGEM];
  return {
    ...base(ID_AUTORIZADA, acoes, 'decidir'),
    status: 'autorizada',
    situacao_horario: 'atrasado',
    autorizado_em: '2026-11-10T11:30:00Z',
    autorizado_por_nome: 'Ana Souza',
    linha_do_tempo: linha('em_andamento', [
      etapa('solicitada', 'Solicitada', 'concluida', '2026-11-10T11:00:00Z', {
        nome: 'Marcos Silva',
      }),
      etapa('autorizada', 'Autorizada', 'atual', '2026-11-10T11:30:00Z', { nome: 'Ana Souza' }),
      etapa('saida', 'Saída registrada', 'pendente', null, null),
      etapa('chegada', 'Chegada registrada', 'pendente', null, null),
    ]),
  };
}

/**
 * Em uso, como o SOLICITANTE recebe da API: sem ids nem nomes da equipe (só o papel), motorista
 * omitido. A minimização é do servidor (`_sem_equipe`); o cartão só herda.
 */
function doSolicitante() {
  const acoes = [ACAO_IMAGEM];
  return {
    ...base(ID_DO_SOLICITANTE, acoes, 'solicitar'),
    // Foto de perfil (URL assinada do Storage): a tela pode exibi-la, o cartão nunca a busca.
    solicitante_foto_url: FOTO_URL,
    status: 'em_uso',
    observacao: OBSERVACAO,
    autorizado_em: '2026-11-10T11:30:00Z',
    saida_real_em: '2026-11-10T12:05:00Z',
    linha_do_tempo: linha('em_andamento', [
      etapa('solicitada', 'Solicitada', 'concluida', '2026-11-10T11:00:00Z', {
        nome: 'Marcos Silva',
      }),
      etapa('autorizada', 'Autorizada', 'concluida', '2026-11-10T11:30:00Z', {
        papel: 'autorizador',
      }),
      etapa('saida', 'Saída registrada', 'atual', '2026-11-10T12:05:00Z', {
        papel: 'vigilante',
      }),
      etapa('chegada', 'Chegada registrada', 'pendente', null, null),
    ]),
  };
}

const FIXTURES: Record<string, () => Record<string, unknown>> = {
  [ID_CONCLUIDA]: concluida,
  [ID_SOLICITADA]: () => solicitada(ID_SOLICITADA, ['cancelar', ACAO_IMAGEM]),
  // Recurso `compartilhar_imagem` desligado: a API não manda a ação (só "Copiar link" aparece).
  [ID_SEM_RECURSO]: () => ({ ...concluida(), id: ID_SEM_RECURSO, acoes_permitidas: ['arquivar'] }),
  [ID_AUTORIZADA]: autorizada,
  [ID_DO_SOLICITANTE]: doSolicitante,
};

/** `sem-arquivos`: share existe, mas canShare({files}) é false (Safari antigo, alguns Android). */
export type ModoShare = 'ok' | 'abort' | 'ausente' | 'sem-arquivos';

export interface ChamadaShare {
  chaves: string[];
  title: unknown;
  text: unknown;
  arquivos: { nome: string; tipo: string; tamanho: number }[];
}

/**
 * Web Share API fake (antes de qualquer script da página). `ausente` remove canShare/share, como
 * no Firefox desktop. Também registra o texto de cada cartão montado para a geração (o nó existe
 * só durante a geração e fica fora da tela).
 */
export async function mockarShare(page: Page, modo: ModoShare) {
  await page.addInitScript((m: string) => {
    const w = window as unknown as {
      __shares: unknown[];
      __cartoes: { texto: string; ariaHidden: string | null; inert: boolean }[];
    };
    w.__shares = [];
    w.__cartoes = [];
    const nav = navigator as unknown as Record<string, unknown>;
    if (m === 'ausente') {
      Object.defineProperty(nav, 'canShare', { value: undefined, configurable: true });
      Object.defineProperty(nav, 'share', { value: undefined, configurable: true });
    } else {
      Object.defineProperty(nav, 'canShare', {
        configurable: true,
        value: (d: { files?: File[] }) =>
          m !== 'sem-arquivos' && Array.isArray(d?.files) && d.files.length > 0,
      });
      Object.defineProperty(nav, 'share', {
        configurable: true,
        value: async (d: Record<string, unknown>) => {
          const files = (d.files as File[] | undefined) ?? [];
          w.__shares.push({
            chaves: Object.keys(d).sort(),
            title: d.title,
            text: d.text,
            arquivos: files.map((f) => ({ nome: f.name, tipo: f.type, tamanho: f.size })),
          });
          if (m === 'abort') throw new DOMException('Share canceled', 'AbortError');
        },
      });
    }
    const registrar = (no: Node) => {
      if (!(no instanceof Element)) return;
      const alvos = no.matches('[data-testid="cartao-imagem"]')
        ? [no]
        : Array.from(no.querySelectorAll('[data-testid="cartao-imagem"]'));
      for (const c of alvos) {
        const host = c.closest('[aria-hidden]');
        w.__cartoes.push({
          texto: c.textContent ?? '',
          ariaHidden: host ? host.getAttribute('aria-hidden') : null,
          inert: !!c.closest('[inert]'),
        });
      }
    };
    new MutationObserver((ms) => ms.forEach((x) => x.addedNodes.forEach(registrar))).observe(
      document,
      { childList: true, subtree: true },
    );
  }, modo);
}

/** API fake base (login, /api/eu etc.) + GET /api/requisicoes/<id> com as fixtures acima. */
export async function mockarCompartilhar(page: Page, opcoes: Opcoes = {}) {
  const mock = await mockarApi(page, opcoes);
  const leituras: string[] = [];
  // Registrada depois de mockarApi: tem precedência. Só o GET do detalhe (sem subcaminhos).
  await page.route(/\/api\/requisicoes\/[0-9a-f-]{36}(\?.*)?$/, (route: Route) => {
    const auth = route.request().headers()['authorization'] ?? '';
    if (!auth.startsWith('Bearer tok-')) {
      return route.fulfill({
        status: 401,
        json: { erro: { codigo: 'nao_autenticado', mensagem: 'Autenticação necessária.' } },
      });
    }
    const id = new URL(route.request().url()).pathname.split('/').pop() ?? '';
    const f = FIXTURES[id];
    if (!f || route.request().method() !== 'GET') return route.fallback();
    leituras.push(id);
    return route.fulfill({ json: f() });
  });
  return { ...mock, leituras };
}
