import type { Page, Route } from '@playwright/test';

// API fake que imita o contrato do Flask (backend/app/rotas/api.py). As regras aqui existem
// só para o teste: o front real nunca as calcula.
type Papel = 'solicitante' | 'autorizador';

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
  /** Requisições já existentes (ex.: viagem em uso), mescladas sobre uma base. */
  sementes?: Partial<Req>[];
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
    return null;
  };

  const acoes = (papel: Papel, r: Req): string[] => {
    const a: string[] = [];
    if (papel === 'autorizador') {
      if (r.status === 'solicitada') a.push('autorizar', 'negar', 'cancelar');
      if (r.status === 'autorizada') a.push('registrar_saida', 'cancelar');
      if (r.status === 'em_uso') a.push('registrar_chegada');
    } else if (r.status === 'solicitada' && r.usuario_id === PESSOAS.solicitante.id) {
      a.push('cancelar');
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
    if (!opcoes.km && !opcoes.checklist) return {};
    const a = acoes(papel, r);
    const o: Record<string, { campos_obrigatorios: string[]; checklist_pendente?: string }> = {};
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
    acoes_permitidas: acoes(papel, r),
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
    const papel: Papel = corpo.email.startsWith('autorizador') ? 'autorizador' : 'solicitante';
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
        rotulo_perfil: papel === 'autorizador' ? 'UEA · Autorizador' : 'UEA · Solicitante',
        unidades: ['PU', 'PF', 'CJU', 'UEA'],
        role: papel,
        permissoes: p.permissoes,
        recursos: opcoes.checklist ? { checklist: true } : {},
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
    const status = new URL(route.request().url()).searchParams.get('status');
    const itens = reqs
      .filter((r) => (papel === 'autorizador' ? true : r.usuario_id === PESSOAS[papel].id))
      .filter((r) => !status || r.status === status)
      .map((r) => ver(papel, r));
    await route.fulfill({ json: { itens, total: itens.length } });
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
    if (papel !== 'autorizador') {
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
    corpos,
    corposAcao,
    registrosChecklist,
    pedidosUpload,
    pedidosFotoPerfil,
    confirmacoesFoto,
    uploadsAvatar,
  };
}
