import type { Page, Route } from '@playwright/test';
import { gruposAcao, recursos } from './recursos';

// API fake que imita o contrato do Flask (backend/app/rotas/api.py). As regras aqui existem
// só para o teste: o front real nunca as calcula.
type Papel = 'solicitante' | 'autorizador' | 'vigilante';

interface Req {
  id: string;
  usuario_id: string;
  nome_solicitante: string;
  unidade: string;
  hora_saida: string;
  trajeto: string;
  observacao: string | null;
  veiculo_id: string;
  status: 'solicitada' | 'autorizada' | 'em_uso' | 'concluida' | 'negada' | 'cancelada';
  autorizado_em: string | null;
  autorizado_por_nome: string | null;
  saida_registrada_por_nome: string | null;
  chegada_registrada_por_nome: string | null;
  saida_real_em: string | null;
  chegada_real_em: string | null;
  cancelada_em: string | null;
  criado_em: string;
  atualizado_em: string;
  km_saida: number | null;
  km_chegada: number | null;
  /** Perfil Motorista: designado na autorização. */
  motorista?: { id: string; nome: string } | null;
}

const VEICULO_ID = '11111111-1111-4111-8111-111111111111';
const PESSOAS: Record<Papel, { id: string; nome: string; permissoes: string[] }> = {
  solicitante: {
    id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    nome: 'Marcos Silva',
    permissoes: ['criar_requisicao', 'ver_proprias_requisicoes'],
  },
  autorizador: {
    id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    nome: 'Ana Souza',
    permissoes: [
      'criar_requisicao',
      'ver_todas_requisicoes',
      'autorizar',
      'negar',
      'registrar_saida',
      'registrar_chegada',
      'cancelar',
    ],
  },
  // Contrato assumido (docs/plano-motorista-vigilante.md §3): sem autorizar/negar/cancelar.
  vigilante: {
    id: '99999999-9999-4999-8999-999999999999',
    nome: 'Paulo Reis',
    permissoes: [
      'criar_requisicao',
      'ver_proprias_requisicoes',
      'registrar_saida',
      'registrar_chegada',
    ],
  },
};

const erro = (route: Route, status: number, codigo: string, mensagem: string) =>
  route.fulfill({ status, json: { erro: { codigo, mensagem } } });

export interface Opcoes {
  /** Faz /api/eu responder 403 mfa_requerido para o autorizador. */
  exigirMfa?: boolean;
  /** Faz a criação responder 409 veiculo_ocupado. */
  veiculoOcupado?: boolean;
  /** Recurso Quilometragem ligado: a API passa a mandar km em campos_acao. */
  km?: boolean;
  /** Recurso Checklist ligado: eu.recursos.checklist e checklist_pendente em campos_acao. */
  checklist?: boolean;
  /** Pendente só aparece depois do 409 checklist_obrigatorio (tela desatualizada). */
  pendenteSoApos409?: boolean;
  /** O primeiro envio ao Storage falha (500). */
  falharPrimeiroUpload?: boolean;
  /** O usuário logado já tem foto de perfil ativa (eu.foto_url e *_foto_url nas requisições). */
  comFoto?: boolean;
  /** A imagem da foto (URL assinada) responde 404, como após expirar. */
  fotoQuebrada?: boolean;
  /** O POST /api/eu/foto (confirmação) responde 400 com este código e mensagem. */
  erroConfirmacao?: { status: number; codigo: string; mensagem: string };
  /** O autorizador também recebe a permissão `administrar` (menu com 6 itens). */
  comAdmin?: boolean;
  /** Requisições já existentes (ex.: viagem em uso), mescladas sobre uma base. */
  sementes?: Partial<Req>[];
  /** GET de painel e prévia trazem `resumo_ia: true` (padrão false). */
  resumoIA?: boolean;
  /** Atraso (ms) nas respostas GET dos painéis, para testar a troca de filtro durante a recarga. */
  atrasoPainelMs?: number;
  /** POST .../resumo responde com este erro da API. */
  erroResumo?: { status: number; codigo: string; mensagem: string };
  /**
   * Recurso Motorista ligado: `campos_acao.autorizar` traz estes candidatos (e `motorista_padrao`
   * se informado). Sem isto a autorização não oferece motorista.
   */
  motoristas?: { id: string; nome: string }[];
  motoristaPadrao?: string;
  /** Marca a requisição como arquivada para o papel (ex.: sementes já arquivadas). */
  arquivadas?: { papel: Papel; indice: number }[];
}

export async function mockarApi(page: Page, opcoes: Opcoes = {}) {
  const reqs: Req[] = [];
  const corpos: unknown[] = [];
  const corposAcao: { acao: string; corpo: unknown }[] = [];
  const agora0 = new Date().toISOString();
  (opcoes.sementes ?? []).forEach((x, i) =>
    reqs.push({
      id: `33333333-3333-4333-8333-33333333333${i}`,
      usuario_id: PESSOAS.solicitante.id,
      nome_solicitante: 'Marcos Silva',
      unidade: 'UEA',
      hora_saida: agora0,
      trajeto: 'Sede -> Fórum',
      observacao: null,
      veiculo_id: VEICULO_ID,
      status: 'autorizada',
      autorizado_em: agora0,
      autorizado_por_nome: 'Ana Souza',
      saida_registrada_por_nome: null,
      chegada_registrada_por_nome: null,
      saida_real_em: null,
      chegada_real_em: null,
      cancelada_em: null,
      criado_em: agora0,
      atualizado_em: agora0,
      km_saida: null,
      km_chegada: null,
      ...x,
    }),
  );

  const papelDoToken = (route: Route): Papel | null => {
    const auth = route.request().headers()['authorization'] ?? '';
    if (auth === 'Bearer tok-solicitante') return 'solicitante';
    if (auth === 'Bearer tok-autorizador') return 'autorizador';
    if (auth === 'Bearer tok-vigilante') return 'vigilante';
    return null;
  };

  // Arquivar é pessoal: um conjunto de ids por papel (a regra real vive no backend).
  const arquivadasPor: Record<Papel, Set<string>> = {
    solicitante: new Set(),
    autorizador: new Set(),
    vigilante: new Set(),
  };
  for (const x of opcoes.arquivadas ?? []) {
    const r = reqs[x.indice];
    if (r) arquivadasPor[x.papel].add(r.id);
  }
  const organizacoes: { acao: string; id: string; papel: Papel }[] = [];
  const acoes = (papel: Papel, r: Req): string[] => {
    const a: string[] = [];
    if (papel === 'autorizador') {
      if (r.status === 'solicitada') a.push('autorizar', 'negar', 'cancelar');
      if (r.status === 'autorizada') a.push('registrar_saida', 'cancelar');
      if (r.status === 'em_uso') a.push('registrar_chegada');
      if (opcoes.checklist && ['autorizada', 'em_uso', 'concluida'].includes(r.status)) {
        a.push('ver_checklist');
      }
    } else if (papel === 'vigilante') {
      if (r.status === 'autorizada') a.push('registrar_saida');
      if (r.status === 'em_uso') a.push('registrar_chegada');
    } else if (r.status === 'solicitada' && r.usuario_id === PESSOAS.solicitante.id) {
      a.push('cancelar');
    }
    // Só concluída/negada/cancelada se arquivam: autorizador qualquer, solicitante a própria.
    const terminal = ['concluida', 'negada', 'cancelada'].includes(r.status);
    const dono = papel === 'solicitante' && r.usuario_id === PESSOAS.solicitante.id;
    if (terminal && (papel === 'autorizador' || dono)) {
      a.push(arquivadasPor[papel].has(r.id) ? 'desarquivar' : 'arquivar');
    }
    return a;
  };
  // Checklist: registros por requisição e fotos já enviadas ao "Storage".
  const checklists = new Map<string, Record<string, Record<string, unknown>>>();
  const fotosEnviadas = new Set<string>();
  const registrosChecklist: Record<string, unknown>[] = [];
  const pedidosUpload: unknown[] = [];
  let uploads = 0;
  let pendenteVisivel = !opcoes.pendenteSoApos409;
  const feito = (id: string, m: string) => !!checklists.get(id)?.[m];
  const camposAcao = (papel: Papel, r: Req) => {
    const a = acoes(papel, r);
    const o: Record<
      string,
      {
        campos_obrigatorios: string[];
        campos_opcionais?: string[];
        checklist_pendente?: string;
        motoristas?: { id: string; nome: string }[];
        motorista_padrao?: string | null;
      }
    > = {};
    if (opcoes.motoristas && a.includes('autorizar')) {
      o.autorizar = {
        campos_obrigatorios: [],
        campos_opcionais: ['motorista_id'],
        motoristas: opcoes.motoristas,
        motorista_padrao: opcoes.motoristaPadrao ?? null,
      };
    }
    if (!opcoes.km && !opcoes.checklist) return o;
    if (a.includes('registrar_saida')) {
      o.registrar_saida = { campos_obrigatorios: opcoes.km ? ['km_saida'] : [] };
      if (opcoes.checklist && pendenteVisivel && !feito(r.id, 'saida')) {
        o.registrar_saida.checklist_pendente = 'saida';
      }
    }
    if (a.includes('registrar_chegada')) {
      o.registrar_chegada = {
        campos_obrigatorios: !opcoes.km
          ? []
          : r.km_saida !== null
            ? ['km_chegada']
            : ['km_saida', 'km_chegada'],
      };
      if (opcoes.checklist && pendenteVisivel && !feito(r.id, 'chegada')) {
        o.registrar_chegada.checklist_pendente = 'chegada';
      }
    }
    return o;
  };
  // Linha do tempo no formato da API (a regra de etapas/situação vive no servidor; aqui só no teste).
  type Sit = 'concluida' | 'atual' | 'pendente' | 'negada' | 'cancelada';
  const etapa = (
    chave: string,
    rotulo: string,
    situacao: Sit,
    em: string | null,
    por: { nome?: string; papel?: string } | null,
    ant: string | null,
  ) => ({
    chave,
    rotulo,
    situacao,
    em,
    por,
    duracao_desde_anterior_s:
      em && ant ? Math.max(Math.round((Date.parse(em) - Date.parse(ant)) / 1000), 0) : null,
    decorrido_s: situacao === 'atual' ? 120 : null,
  });
  const linhaDoTempo = (r: Req) => {
    const solicitada = etapa(
      'solicitada',
      'Solicitada',
      'concluida',
      r.criado_em,
      { nome: r.nome_solicitante },
      null,
    );
    const ana = { nome: 'Ana Souza' };
    let etapas: ReturnType<typeof etapa>[];
    let desfecho: string;
    if (r.status === 'negada') {
      desfecho = 'negada';
      etapas = [solicitada, etapa('negada', 'Negada', 'negada', r.autorizado_em, ana, r.criado_em)];
    } else if (r.status === 'cancelada') {
      desfecho = 'cancelada';
      etapas = [
        solicitada,
        etapa(
          'cancelada',
          'Cancelada',
          'cancelada',
          r.cancelada_em,
          { papel: 'solicitante' },
          r.criado_em,
        ),
      ];
    } else {
      const i = ['solicitada', 'autorizada', 'em_uso', 'concluida'].indexOf(r.status);
      const sit = (k: number): Sit =>
        k < i || r.status === 'concluida' ? 'concluida' : k === i ? 'atual' : 'pendente';
      desfecho = r.status === 'concluida' ? 'concluida' : 'em_andamento';
      etapas = [
        { ...solicitada, situacao: sit(0), decorrido_s: sit(0) === 'atual' ? 120 : null },
        etapa(
          'autorizada',
          'Autorizada',
          sit(1),
          r.autorizado_em,
          r.autorizado_em ? ana : null,
          r.criado_em,
        ),
        etapa(
          'saida',
          'Saída registrada',
          sit(2),
          r.saida_real_em,
          r.saida_real_em ? ana : null,
          r.autorizado_em,
        ),
        etapa(
          'chegada',
          'Chegada registrada',
          sit(3),
          r.chegada_real_em,
          r.chegada_real_em ? ana : null,
          r.saida_real_em,
        ),
      ];
    }
    const atual = etapas.findIndex((e) => e.situacao === 'atual');
    return {
      desfecho,
      etapa_atual: atual < 0 ? etapas.length : atual + 1,
      total_etapas: etapas.length,
      texto_equivalente: `Requisição ${r.status}. ${etapas.map((e) => `${e.rotulo}: ${e.situacao}`).join('; ')}.`,
      gerado_em: new Date().toISOString(),
      etapas,
    };
  };
  const historico = (r: Req) =>
    (
      [
        ['Solicitada', r.criado_em],
        [r.status === 'negada' ? 'Negada' : 'Autorizada', r.autorizado_em],
        ['Saída registrada', r.saida_real_em],
        ['Chegada registrada', r.chegada_real_em],
        ['Cancelada', r.cancelada_em],
      ] as [string, string | null][]
    )
      .filter(([, q]) => q)
      .map(([rotulo, quando]) => ({
        rotulo,
        quando,
        por: rotulo === 'Autorizada' ? r.autorizado_por_nome : null,
        ...(rotulo === 'Saída registrada' && r.km_saida !== null ? { km: r.km_saida } : {}),
        ...(rotulo === 'Chegada registrada' && r.km_chegada !== null ? { km: r.km_chegada } : {}),
      }));
  // Foto de perfil: a URL assinada é cross-origin (Supabase) e curta; aqui só um marcador.
  let fotoAtiva = !!opcoes.comFoto;
  const FOTO_PERFIL_URL =
    'https://exemplo.supabase.co/storage/v1/object/sign/avatares/p.jpg?token=t';
  const fotoUrl = () => (fotoAtiva ? FOTO_PERFIL_URL : null);
  const pedidosFotoPerfil: unknown[] = [];
  const confirmacoesFoto: unknown[] = [];
  const uploadsAvatar: string[] = [];
  const ver = (papel: Papel, r: Req) => ({
    ...r,
    solicitante_foto_url: fotoUrl(),
    autorizado_por_foto_url: r.autorizado_por_nome ? fotoUrl() : null,
    saida_registrada_por_foto_url: r.saida_registrada_por_nome ? fotoUrl() : null,
    chegada_registrada_por_foto_url: r.chegada_registrada_por_nome ? fotoUrl() : null,
    veiculo_descricao: 'Veículo 01',
    historico: historico(r),
    linha_do_tempo: linhaDoTempo(r),
    acoes_permitidas: acoes(papel, r),
    // Cancelar é da categoria "solicitar" para o solicitante e "decidir" para a equipe.
    grupos_acao: gruposAcao(acoes(papel, r), papel === 'solicitante' ? 'solicitar' : 'decidir'),
    arquivada: arquivadasPor[papel].has(r.id),
    campos_acao: camposAcao(papel, r),
  });
  const ocupado = () => reqs.some((r) => ['solicitada', 'autorizada', 'em_uso'].includes(r.status));

  // Supabase Auth (só login).
  await page.route('**/auth/v1/token?grant_type=password', async (route) => {
    const corpo = route.request().postDataJSON() as { email: string; password: string };
    if (corpo.password !== 'senha-correta') {
      return route.fulfill({
        status: 400,
        json: { error: 'invalid_grant', error_description: 'Invalid login credentials' },
      });
    }
    const papel: Papel = corpo.email.startsWith('autorizador')
      ? 'autorizador'
      : corpo.email.startsWith('vigilante')
        ? 'vigilante'
        : 'solicitante';
    const p = PESSOAS[papel];
    await route.fulfill({
      json: {
        access_token: `tok-${papel}`,
        token_type: 'bearer',
        expires_in: 3600,
        expires_at: Math.floor(Date.now() / 1000) + 3600,
        refresh_token: `refresh-${papel}`,
        user: {
          id: p.id,
          aud: 'authenticated',
          email: corpo.email,
          app_metadata: {},
          user_metadata: {},
          created_at: '2026-01-01T00:00:00Z',
        },
      },
    });
  });
  // Consulta de fatores TOTP (tela de verificação do segundo fator): a conta já tem um.
  await page.route('**/auth/v1/user', (route) =>
    route.fulfill({
      json: {
        id: PESSOAS.autorizador.id,
        aud: 'authenticated',
        app_metadata: {},
        user_metadata: {},
        created_at: '2026-01-01T00:00:00Z',
        factors: [
          {
            id: 'fator-1',
            factor_type: 'totp',
            status: 'verified',
            created_at: '2026-01-01T00:00:00Z',
            updated_at: '2026-01-01T00:00:00Z',
          },
        ],
      },
    }),
  );
  await page.route('**/auth/v1/logout*', (route) => route.fulfill({ status: 204 }));

  await page.route('**/api/eu', async (route) => {
    const papel = papelDoToken(route);
    if (!papel) {
      return erro(
        route,
        401,
        'nao_autenticado',
        'Sessão inválida ou expirada. Faça login novamente.',
      );
    }
    if (papel === 'autorizador' && opcoes.exigirMfa) {
      return erro(
        route,
        403,
        'mfa_requerido',
        'Este perfil exige verificação em duas etapas. Conclua o segundo fator.',
      );
    }
    const p = PESSOAS[papel];
    await route.fulfill({
      json: {
        id: p.id,
        nome: p.nome,
        unidade: 'UEA',
        rotulo_perfil: {
          autorizador: 'UEA · Autorizador',
          vigilante: 'UEA · Vigilante',
          solicitante: 'UEA · Solicitante',
        }[papel],
        unidades: ['PU', 'PF', 'CJU', 'UEA'],
        role: papel,
        permissoes:
          opcoes.comAdmin && papel === 'autorizador'
            ? [...p.permissoes, 'administrar']
            : p.permissoes,
        recursos: recursos({
          checklist: !!opcoes.checklist,
          quilometragem: !!opcoes.km,
          vigilante_km: !!opcoes.km && papel === 'vigilante',
          motorista: !!opcoes.motoristas,
        }),
        foto_url: fotoUrl(),
      },
    });
  });

  // Foto de perfil (contrato: backend/app/rotas/api.py).
  await page.route('**/storage/v1/object/sign/avatares/**', (route) =>
    opcoes.fotoQuebrada
      ? route.fulfill({ status: 404, json: { error: 'not_found' } })
      : route.fulfill({
          contentType: 'image/png',
          body: Buffer.from(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
            'base64',
          ),
        }),
  );
  await page.route('**/api/eu/foto/upload-url', (route) => {
    const papel = papelDoToken(route);
    if (!papel) return erro(route, 401, 'nao_autenticado', 'Autenticação necessária.');
    pedidosFotoPerfil.push(route.request().postDataJSON());
    return route.fulfill({
      status: 201,
      json: {
        id: 'abababab-abab-4bab-8bab-abababababab',
        url_upload: 'https://exemplo.supabase.co/storage/v1/object/upload/sign/x',
        token: 'token-de-teste',
      },
    });
  });
  await page.route('**/api/eu/foto', (route) => {
    const papel = papelDoToken(route);
    if (!papel) return erro(route, 401, 'nao_autenticado', 'Autenticação necessária.');
    if (route.request().method() === 'DELETE') {
      fotoAtiva = false;
      return route.fulfill({ json: { foto_url: null } });
    }
    confirmacoesFoto.push(route.request().postDataJSON());
    if (opcoes.erroConfirmacao) {
      const e = opcoes.erroConfirmacao;
      return erro(route, e.status, e.codigo, e.mensagem);
    }
    fotoAtiva = true;
    return route.fulfill({ json: { foto_url: FOTO_PERFIL_URL } });
  });

  await page.route('**/api/veiculos', (route) =>
    route.fulfill({
      json: {
        itens: [
          {
            id: VEICULO_ID,
            descricao: 'Veículo 01',
            placa: 'ABC-1D23',
            disponivel: !ocupado(),
            motivo_indisponibilidade: ocupado()
              ? 'Há uma requisição em aberto para este veículo.'
              : null,
            acoes_permitidas: ocupado() ? [] : ['solicitar'],
          },
        ],
      },
    }),
  );

  await page.route(/\/api\/requisicoes(\?.*)?$/, async (route) => {
    const papel = papelDoToken(route);
    if (!papel) return erro(route, 401, 'nao_autenticado', 'Autenticação necessária.');
    if (route.request().method() === 'POST') {
      corpos.push(route.request().postDataJSON());
      if (opcoes.veiculoOcupado || ocupado()) {
        return erro(
          route,
          409,
          'veiculo_ocupado',
          'Este veículo já possui uma requisição em aberto. Ele só aceita novo pedido após a conclusão, negativa ou cancelamento da anterior.',
        );
      }
      const corpo = route.request().postDataJSON() as Record<string, string>;
      const agora = new Date().toISOString();
      const nova: Req = {
        id: '22222222-2222-4222-8222-222222222222',
        usuario_id: PESSOAS[papel].id,
        nome_solicitante: corpo.nome_solicitante ?? '',
        unidade: corpo.unidade ?? '',
        hora_saida: new Date(corpo.hora_saida ?? agora).toISOString(),
        trajeto: corpo.trajeto ?? '',
        observacao: corpo.observacao ?? null,
        veiculo_id: corpo.veiculo_id ?? '',
        status: 'solicitada',
        autorizado_em: null,
        autorizado_por_nome: null,
        saida_registrada_por_nome: null,
        chegada_registrada_por_nome: null,
        saida_real_em: null,
        chegada_real_em: null,
        cancelada_em: null,
        criado_em: agora,
        atualizado_em: agora,
        km_saida: null,
        km_chegada: null,
      };
      reqs.push(nova);
      return route.fulfill({ status: 201, json: ver(papel, nova) });
    }
    const params = new URL(route.request().url()).searchParams;
    const status = params.get('status');
    // Padrão da API: arquivadas=false (as arquivadas do usuário somem); true = só as arquivadas.
    const soArquivadas = params.get('arquivadas') === 'true';
    const itens = reqs
      .filter((r) => (papel !== 'solicitante' ? true : r.usuario_id === PESSOAS[papel].id))
      .filter((r) => !status || r.status === status)
      .filter((r) => arquivadasPor[papel].has(r.id) === soArquivadas)
      .map((r) => ver(papel, r));
    await route.fulfill({ json: { itens, total: itens.length } });
  });

  // Dashboards: solicitante recebe 403; autorizador vê os painéis básicos.
  const resumosPainel: { painel: string; query: Record<string, string> }[] = [];
  const consultasPainel: { painel: string; query: Record<string, string> }[] = [];
  await page.route(/\/api\/dashboards(\/[a-z]+){0,2}(\?.*)?$/, async (route) => {
    const papel = papelDoToken(route);
    if (!papel) return erro(route, 401, 'nao_autenticado', 'Autenticação necessária.');
    if (papel !== 'autorizador') {
      return erro(route, 403, 'sem_permissao', 'Você não tem permissão para esta ação.');
    }
    const url = new URL(route.request().url());
    const painel = url.pathname.split('/')[3];
    if (!painel) {
      return route.fulfill({
        json: {
          paineis: [
            { chave: 'operacao', rotulo: 'Operação agora' },
            { chave: 'volume', rotulo: 'Volume' },
            { chave: 'tempos', rotulo: 'Tempos' },
            { chave: 'unidades', rotulo: 'Unidades' },
            { chave: 'veiculos', rotulo: 'Veículos' },
            { chave: 'opcionais', rotulo: 'Opcionais' },
          ],
          fuso: 'America/Sao_Paulo',
          periodo_padrao_dias: 30,
          periodo_maximo_dias: 366,
        },
      });
    }
    const q = Object.fromEntries(url.searchParams.entries());
    if (url.pathname.split('/')[4] === 'resumo') {
      resumosPainel.push({ painel, query: q });
      if (opcoes.erroResumo) {
        const e = opcoes.erroResumo;
        return erro(route, e.status, e.codigo, e.mensagem);
      }
      return route.fulfill({
        json: {
          texto: `Resumo do painel ${painel}: houve 10 requisições no período. <b>não é HTML</b>`,
          gerado_por_ia: true,
          gerado_em: new Date().toISOString(),
        },
      });
    }
    consultasPainel.push({ painel, query: q });
    if (opcoes.atrasoPainelMs && q.unidade) {
      await new Promise((ok) => setTimeout(ok, opcoes.atrasoPainelMs));
    }
    // Só o que o painel usa: operação, apenas unidade/veículo; volume, também granularidade.
    const filtros: Record<string, string | null> =
      painel === 'operacao'
        ? { fuso: 'America/Sao_Paulo', unidade: q.unidade ?? null, veiculo: q.veiculo ?? null }
        : {
            inicio: q.inicio ?? '2026-10-10',
            fim: q.fim ?? '2026-11-08',
            fuso: 'America/Sao_Paulo',
            unidade: q.unidade ?? null,
            veiculo: q.veiculo ?? null,
            ...(painel === 'volume' ? { granularidade: q.granularidade ?? 'dia' } : {}),
          };
    const abertas = reqs.filter((r) => ['solicitada', 'autorizada', 'em_uso'].includes(r.status));
    const por = (st: string) => abertas.filter((r) => r.status === st);
    const maisAntiga = (l: Req[]) =>
      l.length ? { id: l[0]?.id, criado_em: l[0]?.criado_em, decorrido_s: 900 } : null;
    const dados: Record<string, unknown> = {
      operacao: {
        gerado_em: new Date().toISOString(),
        veiculos: {
          ativos: 1,
          livres: abertas.length ? 0 : 1,
          reservados: por('solicitada').length + por('autorizada').length,
          em_uso: por('em_uso').length,
        },
        solicitadas: {
          total: por('solicitada').length,
          mais_antiga: maisAntiga(por('solicitada')),
        },
        autorizadas_sem_saida: {
          total: por('autorizada').length,
          mais_antiga: maisAntiga(por('autorizada')),
        },
        em_uso: { total: por('em_uso').length, decorrido_medio_s: 3600, decorrido_max_s: 7200 },
      },
      volume: {
        totais: {
          total: 10,
          autorizadas: 6,
          negadas: 2,
          canceladas: 1,
          solicitadas_pendentes: 1,
          pct_autorizadas: 60,
          pct_negadas: 20,
          pct_canceladas: 10,
        },
        serie: [
          { periodo: '2026-11-05', total: 4, autorizadas: 3, negadas: 1, canceladas: 0 },
          { periodo: '2026-11-06', total: 6, autorizadas: 3, negadas: 1, canceladas: 1 },
        ],
        granularidade: filtros.granularidade ?? 'dia',
        fuso: 'America/Sao_Paulo',
      },
      tempos: {
        espera_autorizacao: { n: 5, media_s: 600, mediana_s: 540, max_s: 1800 },
        duracao_uso: { n: 4, media_s: 7200, mediana_s: 6600, max_s: 14400 },
        saida_vs_prevista: { n: 4, media_s: -300, mediana_s: -120, min_s: -1800, max_s: 900 },
      },
      opcionais: {
        quilometragem: { habilitado: false },
        checklist: {
          habilitado: true,
          total: 4,
          com_avaria: 1,
          requisicoes_com_avaria: [
            {
              requisicao_id: VEICULO_ID,
              momento: 'chegada',
              registrado_em: '2026-11-06T15:00:00Z',
            },
          ],
        },
      },
      unidades: {
        itens: [
          { unidade: 'PU', requisicoes: 4, usos: 3, horas_uso: 6.5 },
          { unidade: 'UEA', requisicoes: 2, usos: 2, horas_uso: 3 },
        ],
      },
      veiculos: {
        horas_periodo: 720,
        itens: [
          {
            veiculo_id: VEICULO_ID,
            ativo: true,
            usos: 5,
            horas_uso: 12,
            horas_ocupadas: 18,
            taxa_ocupacao: 0.025,
          },
        ],
      },
    };
    if (!(painel in dados))
      return erro(route, 404, 'painel_desconhecido', 'Painel não encontrado.');
    await route.fulfill({
      json: {
        painel,
        filtros,
        resumo_ia: opcoes.resumoIA === true,
        ...(painel === 'operacao'
          ? { itens_truncados: false, itens: abertas.map((r) => ver(papel, r)) }
          : {}),
        dados: dados[painel],
      },
    });
  });

  const FOTO_URL = 'https://exemplo.supabase.co/storage/v1/object/sign/checklists/';
  await page.route('**/storage/v1/object/sign/checklists/**', (route) =>
    route.fulfill({
      contentType: 'image/png',
      body: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
        'base64',
      ),
    }),
  );
  await page.route('**/storage/v1/object/upload/sign/**', async (route) => {
    uploads += 1;
    if (opcoes.falharPrimeiroUpload && uploads === 1) {
      return route.fulfill({ status: 500, json: { message: 'falha', error: 'erro' } });
    }
    const completo = decodeURIComponent(new URL(route.request().url()).pathname);
    if (completo.includes('/object/upload/sign/avatares/')) {
      uploadsAvatar.push(completo.replace(/^.*\/object\/upload\/sign\/avatares\//, ''));
      return route.fulfill({ json: { Key: 'avatares/x' } });
    }
    const caminho = completo.replace(/^.*\/object\/upload\/sign\/checklists\//, '');
    fotosEnviadas.add(caminho);
    await route.fulfill({ json: { Key: `checklists/${caminho}` } });
  });
  await page.route(/\/api\/requisicoes\/[^/]+\/checklist(\/.*)?$/, async (route) => {
    const papel = papelDoToken(route);
    if (!papel) return erro(route, 401, 'nao_autenticado', 'Autenticação necessária.');
    if (papel === 'solicitante') {
      return erro(route, 403, 'sem_permissao', 'Você não tem permissão para esta ação.');
    }
    const partes = new URL(route.request().url()).pathname.split('/');
    const id = partes[3] ?? '';
    const momento = partes[5];
    const r = reqs.find((x) => x.id === id);
    if (!r) return erro(route, 404, 'nao_encontrado', 'Requisição não encontrada.');
    if (route.request().method() === 'GET') {
      const c = checklists.get(id) ?? {};
      return route.fulfill({
        json: {
          requisicao_id: id,
          checklists: { saida: c.saida ?? null, chegada: c.chegada ?? null },
          url_expira_em_segundos: 300,
        },
      });
    }
    const corpo = (route.request().postDataJSON() ?? {}) as Record<string, unknown>;
    if (partes[6] === 'fotos') {
      pedidosUpload.push(corpo);
      const fotoId = `f0f0f0f0-0000-4000-8000-${String(uploads + pedidosUpload.length).padStart(12, '0')}`;
      return route.fulfill({
        status: 201,
        json: {
          foto_id: fotoId,
          mime: corpo.mime,
          caminho: `${id}/${momento}/${fotoId}.jpg`,
          url_upload: 'https://exemplo.supabase.co/storage/v1/object/upload/sign/x',
          token: 'token-de-teste',
        },
      });
    }
    registrosChecklist.push(corpo);
    if (feito(id, momento ?? '')) {
      return erro(
        route,
        409,
        'checklist_ja_registrado',
        'Já existe checklist registrado para este momento.',
      );
    }
    if (corpo.avarias === true && !corpo.descricao_avarias) {
      return erro(route, 400, 'checklist_invalido', 'Descreva as avarias em descricao_avarias.');
    }
    const fotos = (corpo.fotos ?? []) as { id: string; mime: string; tamanho: number }[];
    if (fotos.some((f) => ![...fotosEnviadas].some((c) => c.includes(f.id)))) {
      return erro(
        route,
        400,
        'foto_nao_enviada',
        'A foto 1 não foi enviada. Envie o arquivo antes de registrar.',
      );
    }
    const reg = {
      id: `c-${momento}`,
      momento,
      combustivel: corpo.combustivel,
      avarias: corpo.avarias,
      descricao_avarias: corpo.descricao_avarias ?? null,
      registrado_por_nome: 'Ana Souza',
      registrado_em: new Date().toISOString(),
      fotos: fotos.map((f, i) => ({
        id: f.id,
        posicao: i + 1,
        mime: f.mime,
        tamanho: f.tamanho,
        url: `${FOTO_URL}${f.id}.jpg?token=t`,
      })),
    };
    checklists.set(id, { ...(checklists.get(id) ?? {}), [momento ?? '']: reg });
    r.atualizado_em = new Date().toISOString();
    await route.fulfill({
      status: 201,
      json: { checklist: { id: reg.id, momento }, requisicao: ver(papel, r) },
    });
  });

  await page.route(/\/api\/requisicoes\/[^/?]+(\/[a-z]+)?(\?.*)?$/, async (route) => {
    const papel = papelDoToken(route);
    if (!papel) return erro(route, 401, 'nao_autenticado', 'Autenticação necessária.');
    const partes = new URL(route.request().url()).pathname.split('/');
    const id = partes[3];
    const acao = partes[4];
    if (acao === 'checklist') return route.fallback();
    const r = reqs.find((x) => x.id === id);
    if (!r) return erro(route, 404, 'nao_encontrado', 'Requisição não encontrada.');
    if (!acao) return route.fulfill({ json: ver(papel, r) });

    if (acao === 'arquivar' || acao === 'desarquivar') {
      if (!acoes(papel, r).includes(acao)) {
        // Idempotente na API real; aqui só a recusa por estado/permissão interessa ao teste.
        const ja = arquivadasPor[papel].has(r.id) === (acao === 'arquivar');
        if (!ja) {
          return erro(
            route,
            409,
            'transicao_invalida',
            'Só é possível arquivar ou desarquivar uma requisição concluída, negada ou cancelada.',
          );
        }
      }
      organizacoes.push({ acao, id: r.id, papel });
      if (acao === 'arquivar') arquivadasPor[papel].add(r.id);
      else arquivadasPor[papel].delete(r.id);
      return route.fulfill({ json: ver(papel, r) });
    }

    const mapa: Record<string, [string, Req['status']]> = {
      autorizar: ['autorizar', 'autorizada'],
      negar: ['negar', 'negada'],
      saida: ['registrar_saida', 'em_uso'],
      chegada: ['registrar_chegada', 'concluida'],
      cancelar: ['cancelar', 'cancelada'],
    };
    const alvo = mapa[acao];
    if (!alvo || !acoes(papel, r).includes(alvo[0])) {
      return erro(
        route,
        409,
        'transicao_invalida',
        'A requisição não está em um estado que permita esta ação.',
      );
    }
    if (opcoes.checklist && (acao === 'saida' || acao === 'chegada')) {
      if (!feito(r.id, acao)) {
        pendenteVisivel = true;
        return erro(
          route,
          409,
          'checklist_obrigatorio',
          'Registre o checklist antes de registrar a saída ou a chegada.',
        );
      }
    }
    const kmBody = (route.request().postDataJSON() ?? {}) as Record<string, unknown>;
    corposAcao.push({ acao, corpo: route.request().postData() ? kmBody : null });
    if (opcoes.km && (acao === 'saida' || acao === 'chegada')) {
      const pedidos = camposAcao(papel, r)[alvo[0]]?.campos_obrigatorios ?? [];
      if (pedidos.some((c) => typeof kmBody[c] !== 'number')) {
        return erro(route, 400, 'km_obrigatorio', 'Informe a quilometragem.');
      }
      const ini = (kmBody.km_saida as number | undefined) ?? r.km_saida;
      if (acao === 'chegada' && ini !== null && (kmBody.km_chegada as number) < (ini ?? 0)) {
        return erro(
          route,
          400,
          'km_invalido',
          'O km de chegada não pode ser menor que o km de saída.',
        );
      }
      if (typeof kmBody.km_saida === 'number') r.km_saida = kmBody.km_saida;
      if (typeof kmBody.km_chegada === 'number') r.km_chegada = kmBody.km_chegada;
    }
    const agora = new Date().toISOString();
    r.status = alvo[1];
    r.atualizado_em = agora;
    const nome = PESSOAS[papel].nome;
    if (acao === 'autorizar') {
      // Perfil Motorista: o corpo traz `motorista_id` opcional (a API real valida).
      if (typeof kmBody.motorista_id === 'string') {
        r.motorista = { id: kmBody.motorista_id, nome: 'Carlos Lima' };
      }
      r.autorizado_em = agora;
      r.autorizado_por_nome = nome;
    }
    if (acao === 'saida') {
      r.saida_real_em = agora;
      r.saida_registrada_por_nome = nome;
    }
    if (acao === 'chegada') {
      r.chegada_real_em = agora;
      r.chegada_registrada_por_nome = nome;
    }
    if (acao === 'cancelar') r.cancelada_em = agora;
    await route.fulfill({ json: ver(papel, r) });
  });

  return {
    reqs,
    organizacoes,
    consultasPainel,
    resumosPainel,
    corpos,
    corposAcao,
    registrosChecklist,
    pedidosUpload,
    pedidosFotoPerfil,
    confirmacoesFoto,
    uploadsAvatar,
  };
}
