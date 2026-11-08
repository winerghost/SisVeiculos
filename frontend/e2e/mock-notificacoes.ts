import type { Page, Route } from '@playwright/test';

// API fake das notificações (contrato combinado com o backend). Usada junto com mockarApi.
// Título, texto e destino vêm prontos, como no servidor: o front só exibe.
type Papel = 'solicitante' | 'autorizador';

export interface NotificacaoMock {
  id: string;
  tipo: string;
  titulo: string;
  texto: string;
  critica: boolean;
  criada_em: string;
  lida_em: string | null;
  destino: string | null;
}

export const REQ_ID = '33333333-3333-4333-8333-333333333330';

let seq = 0;
export function nova(parcial: Partial<NotificacaoMock> = {}, minutosAtras = 5): NotificacaoMock {
  seq += 1;
  return {
    id: `99999999-9999-4999-8999-${String(seq).padStart(12, '0')}`,
    tipo: 'generica',
    titulo: `Aviso ${seq}`,
    texto: `Texto do aviso ${seq}.`,
    critica: false,
    criada_em: new Date(Date.now() - minutosAtras * 60_000).toISOString(),
    lida_em: null,
    destino: null,
    ...parcial,
  };
}

export interface OpcoesNotificacoes {
  solicitante?: NotificacaoMock[];
  autorizador?: NotificacaoMock[];
  /** Quando o autorizador registra a chegada, o solicitante ganha uma notificação com destino. */
  notificarChegada?: boolean;
  /** GET responde com este erro. */
  erro?: { status: number; codigo: string; mensagem: string };
}

const erroApi = (route: Route, status: number, codigo: string, mensagem: string) =>
  route.fulfill({ status, json: { erro: { codigo, mensagem } } });

export async function mockarNotificacoes(page: Page, opcoes: OpcoesNotificacoes = {}) {
  const lista: Record<Papel, NotificacaoMock[]> = {
    solicitante: [...(opcoes.solicitante ?? [])],
    autorizador: [...(opcoes.autorizador ?? [])],
  };
  const marcadas: string[] = [];
  const consultas: { limite: number; offset: number; somente_nao_lidas: string }[] = [];
  const state = { todasMarcadas: 0, contagens: 0 };

  const papelDoToken = (route: Route): Papel | null => {
    const auth = route.request().headers()['authorization'] ?? '';
    if (auth === 'Bearer tok-solicitante') return 'solicitante';
    if (auth === 'Bearer tok-autorizador') return 'autorizador';
    return null;
  };
  const naoLidas = (p: Papel) => lista[p].filter((n) => n.lida_em === null).length;
  const ordenadas = (p: Papel) =>
    [...lista[p]].sort((a, b) => Date.parse(b.criada_em) - Date.parse(a.criada_em));

  await page.route(/\/api\/notificacoes(\/.*)?(\?.*)?$/, async (route) => {
    const papel = papelDoToken(route);
    if (!papel) return erroApi(route, 401, 'nao_autenticado', 'Autenticação necessária.');
    const url = new URL(route.request().url());
    const partes = url.pathname.split('/').filter(Boolean); // api, notificacoes, ...
    const metodo = route.request().method();
    const agora = new Date().toISOString();

    if (metodo === 'POST' && partes[2] === 'lidas') {
      const pendentes = lista[papel].filter((n) => n.lida_em === null);
      pendentes.forEach((n) => (n.lida_em = agora));
      state.todasMarcadas += 1;
      return route.fulfill({ json: { marcadas: pendentes.length } });
    }
    if (metodo === 'POST' && partes[3] === 'lida') {
      const n = lista[papel].find((x) => x.id === partes[2]);
      if (!n) return erroApi(route, 404, 'nao_encontrada', 'Notificação não encontrada.');
      n.lida_em ??= agora;
      marcadas.push(n.id);
      return route.fulfill({ json: { id: n.id, lida_em: n.lida_em } });
    }
    if (opcoes.erro) {
      return erroApi(route, opcoes.erro.status, opcoes.erro.codigo, opcoes.erro.mensagem);
    }
    if (partes[2] === 'contagem') {
      state.contagens += 1;
      return route.fulfill({ json: { nao_lidas: naoLidas(papel) } });
    }
    const q = url.searchParams;
    const limite = Number(q.get('limite') ?? 20);
    const offset = Number(q.get('offset') ?? 0);
    consultas.push({ limite, offset, somente_nao_lidas: q.get('somente_nao_lidas') ?? '' });
    const base = ordenadas(papel).filter(
      (n) => q.get('somente_nao_lidas') !== 'true' || n.lida_em === null,
    );
    await route.fulfill({
      json: {
        itens: base.slice(offset, offset + limite),
        total: base.length,
        nao_lidas: naoLidas(papel),
      },
    });
  });

  if (opcoes.notificarChegada) {
    // Registrada depois da rota de requisições do mockarApi: roda antes e repassa com fallback.
    await page.route(/\/api\/requisicoes\/[^/]+\/chegada$/, async (route) => {
      const id = new URL(route.request().url()).pathname.split('/')[3] ?? '';
      lista.solicitante.push(
        nova(
          {
            tipo: 'chegada_registrada',
            titulo: 'Chegada registrada',
            texto: 'A chegada do veículo da sua requisição foi registrada.',
            destino: `/requisicoes/detalhe?id=${id}`,
          },
          0,
        ),
      );
      await route.fallback();
    });
  }

  return {
    lista,
    marcadas,
    consultas,
    state,
    /** Simula uma notificação nova gerada no servidor. */
    adicionar: (papel: Papel, n: NotificacaoMock) => lista[papel].push(n),
  };
}
