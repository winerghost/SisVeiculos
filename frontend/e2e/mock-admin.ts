import type { Page, Route } from '@playwright/test';
import { recursos } from './recursos';

// API fake da administração e do aceite de convite (contrato em backend/app/rotas/admin.py e
// convites.py). As regras daqui existem só para o teste: o front real nunca as calcula.
const ADMIN_ID = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const ANA_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const CONVIDADO_ID = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';
const VEICULO_ID = '11111111-1111-4111-8111-111111111111';

interface Usuario {
  id: string;
  nome: string;
  unidade: string;
  role: string;
  ativo: boolean;
  email: string;
  ultimo_acesso_em: string | null;
}

export interface OpcoesAdmin {
  /** Responde 410 convite_expirado no aceite. */
  conviteExpirado?: boolean;
  /** Aceite sem `nome` responde 400 pedindo o campo (conta do Auth sem nome). */
  exigirNome?: boolean;
  /** Código do 400 do aceite sem nome (padrão: o novo `nome_obrigatorio`). */
  codigoNome?: string;
  /** As coleções não trazem `criar` em acoes_permitidas. */
  semCriar?: boolean;
  /** A listagem de usuários com busca devolve `busca_parcial: true`. */
  buscaParcial?: boolean;
  /** O primeiro reenvio de convite responde 429 reenvio_muito_recente. */
  reenvioRecente?: boolean;
  /** Fatores TOTP já cadastrados no Supabase (para a tela de MFA). */
  fatores?: number;
  /** /api/eu do admin responde 403 mfa_requerido até a verificação do cadastro terminar. */
  exigirMfa?: boolean;
  /** /api/eu do admin NÃO traz `ver_acessos` (a API recusa /admin/acessos com 403). */
  semVerAcessos?: boolean;
  /** Ana Souza (autorizadora) tem foto ativa: ganha foto_url e a ação `remover_foto`. */
  anaComFoto?: boolean;
  /** DELETE da foto de usuário responde 403 sem_permissao. */
  removerFotoNegado?: boolean;
  /** Os POSTs de troca de foto respondem com este erro da API. */
  trocarFotoErro?: { status: number; codigo: string; mensagem: string };
  /** Quantidade de registros de acesso devolvidos (padrão 45; 0 = estado vazio). */
  totalAcessos?: number;
  /** Chaves em `testes_disponiveis` (padrão: ['anthropic']; [] = sem botão de teste). */
  testesIA?: string[];
  /** Resultado de POST /admin/ia/testar (padrão 'ok'); 'conflito' responde 409. */
  resultadoTesteIA?: 'ok' | 'falha' | 'conflito';
  /** Texto de GET /api/saudacao (padrão null = sem banner). */
  saudacao?: string | null;
  /** Corpo completo de GET /api/saudacao, chamado a cada busca (sobrepõe `saudacao`). */
  saudacaoCorpo?: () => Record<string, unknown>;
  /** Inicia com a função saudação ligada (e a chave geral de IA). */
  saudacaoLigada?: boolean;
  /** POST /admin/ia/saudacoes/gerar responde 502 com esta mensagem. */
  erroGerarSaudacao?: string;
}

const ACESSO_BASE = Date.parse('2026-11-05T15:00:00Z');

const erro = (route: Route, status: number, codigo: string, mensagem: string) =>
  route.fulfill({ status, json: { erro: { codigo, mensagem } } });

export async function mockarAdmin(page: Page, opcoes: OpcoesAdmin = {}) {
  const usuarios: Usuario[] = [
    {
      id: ADMIN_ID,
      nome: 'Marcos Silva',
      unidade: 'UEA',
      role: 'admin',
      ativo: true,
      email: 'admin@agu.gov.br',
      ultimo_acesso_em: '2026-11-05T15:30:00Z',
    },
    {
      id: ANA_ID,
      nome: 'Ana Souza',
      unidade: 'UEA',
      role: 'autorizador',
      ativo: true,
      email: 'ana.souza.com.um.endereco.bem.longo.para.truncar@agu.gov.br',
      ultimo_acesso_em: null,
    },
  ];
  const convites: Record<string, unknown>[] = opcoes.reenvioRecente
    ? [
        {
          id: 'ffffffff-ffff-4fff-8fff-ffffffffffff',
          email: 'maria@gmail.com',
          role: 'solicitante',
          unidade: 'PU',
          estado: 'aberto',
          criado_em: new Date().toISOString(),
          expira_em: new Date(Date.now() + 7 * 864e5).toISOString(),
          usado_em: null,
          revogado_em: null,
          acoes_permitidas: ['reenviar', 'revogar'],
        },
      ]
    : [];
  // As 13 chaves de recursos da API (todas desligadas no início).
  const config: Record<string, boolean> = { ...recursos() };
  const ia: { mestre: boolean; saudacao: boolean; provedor: unknown; modelo: unknown } = {
    mestre: !!opcoes.saudacaoLigada,
    saudacao: !!opcoes.saudacaoLigada,
    provedor: null,
    modelo: null,
  };
  const frases: {
    id: string;
    ocasiao: string;
    texto: string;
    origem: 'manual' | 'ia';
    status: string;
    criado_em: string;
    acoes_permitidas: string[];
  }[] = [
    {
      id: 'f1',
      ocasiao: 'Madrugada',
      texto: 'Acordado, coruja noturna, {nome}?',
      origem: 'ia',
      status: 'pendente',
      criado_em: '2026-11-05T15:00:00Z',
      acoes_permitidas: ['aprovar', 'rejeitar'],
    },
    {
      id: 'f2',
      ocasiao: 'Natal',
      texto: 'Boas festas, {nome}!',
      origem: 'manual',
      status: 'aprovada',
      criado_em: '2026-11-05T15:00:00Z',
      acoes_permitidas: ['rejeitar'],
    },
  ];
  await page.route('**/api/saudacao', (route) =>
    route.fulfill({ json: opcoes.saudacaoCorpo?.() ?? { texto: opcoes.saudacao ?? null } }),
  );
  const acoesColecao = opcoes.semCriar ? [] : ['criar'];
  const corpos: {
    convidar: unknown[];
    aceitar: unknown[];
    senha: unknown[];
    config: unknown[];
    ia: Record<string, unknown>[];
    iaTestar: unknown[];
    saudacoes: unknown[];
    /** Corpos de POST /admin/usuarios/:id/foto/upload-url e /foto, e objetos enviados ao Storage. */
    uploadUrlFoto: unknown[];
    confirmarFoto: unknown[];
    uploadsStorage: string[];
    consultas: string[];
    /** Query string de cada GET /api/admin/acessos recebido. */
    acessos: string[];
    reenvios: number;
    /** IDs de usuário cuja foto foi removida pelo admin. */
    fotosRemovidas: string[];
  } = {
    fotosRemovidas: [],
    consultas: [],
    acessos: [],
    reenvios: 0,
    convidar: [],
    aceitar: [],
    senha: [],
    config: [],
    ia: [],
    iaTestar: [],
    saudacoes: [],
    uploadUrlFoto: [],
    confirmarFoto: [],
    uploadsStorage: [],
  };
  let aceito = false;
  let mfaConcluido = !opcoes.exigirMfa;

  const FOTO_UPLOAD_ID = 'abababab-abab-4bab-8bab-abababababab';
  await page.route('**/storage/v1/object/upload/sign/**', (route) => {
    const completo = decodeURIComponent(new URL(route.request().url()).pathname);
    corpos.uploadsStorage.push(completo.replace(/^.*\/object\/upload\/sign\/avatares\//, ''));
    return route.fulfill({ json: { Key: 'avatares/x' } });
  });

  // A foto assinada é cross-origin: sem este mock o teste dependeria da rede real.
  await page.route('**/storage/v1/object/sign/avatares/**', (route) =>
    route.fulfill({
      contentType: 'image/png',
      body: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
        'base64',
      ),
    }),
  );

  let anaFoto = !!opcoes.anaComFoto;
  const FOTO_URL = 'https://exemplo.supabase.co/storage/v1/object/sign/avatares/ana.jpg?token=t';
  const temFoto = (u: Usuario) => u.id === ANA_ID && anaFoto;

  const apresentar = (u: Usuario) => ({
    ...u,
    foto_url: temFoto(u) && u.ativo ? FOTO_URL : null,
    rotulo_perfil: `${u.unidade} · ${u.role}`,
    acoes_permitidas: [
      'editar',
      ...(temFoto(u) ? ['remover_foto'] : []),
      // Conta ativa de outra pessoa pode ter a foto trocada pelo admin.
      ...(u.ativo && u.id !== ADMIN_ID ? ['trocar_foto'] : []),
      ...(u.id === ADMIN_ID ? [] : ['alterar_role']),
      // Conta inativa também oferece `desativar` (refaz o bloqueio no Auth).
      ...(u.ativo ? (u.id === ADMIN_ID ? [] : ['desativar']) : ['ativar', 'desativar']),
    ],
  });

  const usuarioAuth = (id: string, email: string, fatores = 0) => ({
    id,
    aud: 'authenticated',
    email,
    app_metadata: {},
    user_metadata: {},
    created_at: '2026-01-01T00:00:00Z',
    factors: Array.from({ length: fatores }, (_, i) => ({
      id: `fator-${i}`,
      factor_type: 'totp',
      status: 'verified',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    })),
  });
  const sessao = (token: string, id: string, email: string) => ({
    access_token: token,
    token_type: 'bearer',
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    refresh_token: `refresh-${token}`,
    user: usuarioAuth(id, email),
  });

  // ---- Supabase Auth ----
  await page.route('**/auth/v1/token?grant_type=password', async (route) => {
    const c = route.request().postDataJSON() as { email: string; password: string };
    if (c.password !== 'senha-correta') {
      return route.fulfill({ status: 400, json: { error: 'invalid_grant' } });
    }
    await route.fulfill({ json: sessao('tok-admin', ADMIN_ID, c.email) });
  });
  await page.route('**/auth/v1/logout*', (route) => route.fulfill({ status: 204 }));
  await page.route('**/auth/v1/user', async (route) => {
    const m = route.request().method();
    const auth = route.request().headers()['authorization'] ?? '';
    if (m === 'PUT') {
      corpos.senha.push(route.request().postDataJSON());
      return route.fulfill({ json: usuarioAuth(CONVIDADO_ID, 'pedro@gmail.com') });
    }
    const id = auth.includes('convidado') ? CONVIDADO_ID : ADMIN_ID;
    await route.fulfill({ json: usuarioAuth(id, 'x@agu.gov.br', opcoes.fatores ?? 0) });
  });
  // Cadastro do autenticador TOTP.
  await page.route('**/auth/v1/factors', (route) =>
    route.fulfill({
      json: {
        id: 'fator-novo',
        type: 'totp',
        friendly_name: 'x',
        totp: {
          qr_code:
            'data:image/svg+xml;utf-8,<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>',
          secret: 'SEGREDOTOTP123',
          uri: 'otpauth://totp/x',
        },
      },
    }),
  );
  await page.route('**/auth/v1/factors/*/challenge', (route) =>
    route.fulfill({ json: { id: 'desafio-1', expires_at: Math.floor(Date.now() / 1000) + 300 } }),
  );
  await page.route('**/auth/v1/factors/*/verify', (route) => {
    mfaConcluido = true;
    return route.fulfill({ json: sessao('tok-admin', ADMIN_ID, 'admin@agu.gov.br') });
  });

  // ---- /api/eu ----
  await page.route('**/api/eu', async (route) => {
    const auth = route.request().headers()['authorization'] ?? '';
    if (auth === 'Bearer tok-convidado') {
      if (!aceito) {
        return erro(route, 403, 'conta_inativa', 'Conta sem acesso ao sistema.');
      }
      return route.fulfill({
        json: {
          id: CONVIDADO_ID,
          nome: 'Pedro Alves',
          unidade: 'CJU',
          rotulo_perfil: 'CJU · Solicitante',
          unidades: ['PU', 'PF', 'CJU', 'UEA'],
          role: 'solicitante',
          permissoes: ['criar_requisicao', 'ver_proprias_requisicoes'],
          recursos: recursos(),
        },
      });
    }
    if (auth !== 'Bearer tok-admin')
      return erro(route, 401, 'nao_autenticado', 'Autenticação necessária.');
    if (!mfaConcluido) {
      return erro(route, 403, 'mfa_requerido', 'Este perfil exige verificação em duas etapas.');
    }
    await route.fulfill({
      json: {
        id: ADMIN_ID,
        nome: 'Marcos Silva',
        unidade: 'UEA',
        rotulo_perfil: 'UEA · Administrador',
        unidades: ['PU', 'PF', 'CJU', 'UEA'],
        role: 'admin',
        permissoes: [
          'criar_requisicao',
          'ver_todas_requisicoes',
          'administrar',
          ...(opcoes.semVerAcessos ? [] : ['ver_acessos']),
        ],
        recursos: recursos(),
      },
    });
  });
  await page.route('**/api/requisicoes**', (route) =>
    route.fulfill({ json: { itens: [], total: 0 } }),
  );
  await page.route('**/api/veiculos', (route) => route.fulfill({ json: { itens: [] } }));

  // ---- Aceite de convite ----
  await page.route('**/api/convites/aceitar', async (route) => {
    corpos.aceitar.push(route.request().postDataJSON());
    if (opcoes.conviteExpirado) {
      return erro(
        route,
        410,
        'convite_expirado',
        'Este convite expirou. Peça um novo ao administrador.',
      );
    }
    const corpo = route.request().postDataJSON() as { nome?: string };
    if (opcoes.exigirNome && !corpo.nome) {
      return erro(
        route,
        400,
        opcoes.codigoNome ?? 'nome_obrigatorio',
        "O convite não traz o seu nome. Informe o campo 'nome' para concluir o cadastro.",
      );
    }
    aceito = true;
    await route.fulfill({ json: { id: CONVIDADO_ID, nome: 'Pedro Alves' } });
  });

  // ---- Administração ----
  await page.route('**/api/admin/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const partes = url.pathname.replace(/^\/api\/admin\//, '').split('/');
    const [recurso, id, acao] = partes;
    const m = req.method();

    if (recurso === 'usuarios') {
      if (!id && m === 'GET') {
        const role = url.searchParams.get('role');
        const ativo = url.searchParams.get('ativo');
        const q = url.searchParams.get('q') ?? '';
        corpos.consultas.push(url.search);
        if (/[,()"\*%]/.test(q)) {
          return erro(
            route,
            400,
            'requisicao_invalida',
            'O termo de busca contém caracteres inválidos.',
          );
        }
        const itens = usuarios
          .filter((u) => !q || `${u.nome} ${u.email}`.toLowerCase().includes(q.toLowerCase()))
          .filter((u) => !role || u.role === role)
          .filter((u) => !ativo || String(u.ativo) === ativo)
          .map(apresentar);
        return route.fulfill({
          json: {
            itens,
            total: itens.length,
            acoes_permitidas: acoesColecao,
            ...(opcoes.buscaParcial && q ? { busca_parcial: true } : {}),
          },
        });
      }
      if (!id && m === 'POST') {
        const c = req.postDataJSON() as Record<string, string>;
        corpos.convidar.push(c);
        const convite = {
          id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
          email: c.email,
          role: c.role,
          unidade: c.unidade,
          estado: 'aberto',
          criado_em: new Date().toISOString(),
          expira_em: new Date(Date.now() + 7 * 864e5).toISOString(),
          usado_em: null,
          revogado_em: null,
          acoes_permitidas: ['reenviar', 'revogar'],
        };
        convites.push(convite);
        return route.fulfill({ status: 201, json: { convite } });
      }
      const u = usuarios.find((x) => x.id === id);
      if (!u) return erro(route, 404, 'nao_encontrado', 'Usuário não encontrado.');
      if (m === 'GET') return route.fulfill({ json: apresentar(u) });
      if (acao === 'foto' && m === 'DELETE') {
        if (opcoes.removerFotoNegado) {
          return erro(route, 403, 'sem_permissao', 'Você não tem permissão para esta ação.');
        }
        corpos.fotosRemovidas.push(u.id);
        if (u.id === ANA_ID) anaFoto = false;
        return route.fulfill({ json: apresentar(u) });
      }
      if (acao === 'foto' && m === 'POST') {
        const sub = partes[3];
        const corpo = req.postDataJSON();
        if (opcoes.trocarFotoErro) {
          const e = opcoes.trocarFotoErro;
          return erro(route, e.status, e.codigo, e.mensagem);
        }
        if (sub === 'upload-url') {
          corpos.uploadUrlFoto.push(corpo);
          return route.fulfill({
            status: 201,
            json: {
              id: FOTO_UPLOAD_ID,
              url_upload: 'https://exemplo.supabase.co/storage/v1/object/upload/sign/x',
              token: 'token-de-teste',
              caminho: `tmp/${u.id}/${FOTO_UPLOAD_ID}`,
            },
          });
        }
        corpos.confirmarFoto.push(corpo);
        if (u.id === ANA_ID) anaFoto = true;
        return route.fulfill({ json: apresentar(u) });
      }
      if (m === 'PATCH') {
        Object.assign(u, req.postDataJSON());
        return route.fulfill({ json: apresentar(u) });
      }
      if (acao === 'desativar') u.ativo = false;
      if (acao === 'ativar') u.ativo = true;
      return route.fulfill({ json: apresentar(u) });
    }

    if (recurso === 'acessos' && m === 'GET') {
      corpos.acessos.push(url.search);
      if (opcoes.semVerAcessos) {
        return erro(route, 403, 'sem_permissao', 'Você não tem permissão para esta ação.');
      }
      const p = url.searchParams;
      const de = p.get('de');
      const ate = p.get('ate');
      if (de && ate && Date.parse(de) > Date.parse(ate)) {
        return erro(
          route,
          400,
          'requisicao_invalida',
          "O parâmetro 'de' não pode ser posterior a 'ate'.",
        );
      }
      const limite = Number(p.get('limite') ?? 50);
      const offset = Number(p.get('offset') ?? 0);
      const todos = Array.from({ length: opcoes.totalAcessos ?? 45 }, (_, i) => {
        const u = i % 3 === 0 ? usuarios[1] : usuarios[0];
        return {
          id: `a0000000-0000-4000-8000-${String(i).padStart(12, '0')}`,
          usuario_id: u?.id ?? '',
          nome: u?.nome ?? '',
          ocorrido_em: new Date(ACESSO_BASE - i * 864e5 * 0.5).toISOString(),
          metodo: i % 2 === 0 ? 'password' : 'totp',
          aal: i % 2 === 0 ? 'aal1' : 'aal2',
          ip_mascarado: '203.0.113.0/24',
          ua_familia: 'Chrome 130 / Windows',
          sessao_ref: `sess-${String(i).padStart(4, '0')}`,
        };
      })
        .filter((a) => !p.get('usuario_id') || a.usuario_id === p.get('usuario_id'))
        .filter((a) => !de || Date.parse(a.ocorrido_em) >= Date.parse(de))
        .filter((a) => !ate || Date.parse(a.ocorrido_em) < Date.parse(ate));
      return route.fulfill({
        json: { itens: todos.slice(offset, offset + limite), total: todos.length, limite, offset },
      });
    }

    if (recurso === 'convites') {
      if (id && acao === 'reenviar' && m === 'POST') {
        corpos.reenvios += 1;
        // Segunda tentativa seguida: 429 com a mensagem da API; depois libera.
        if (opcoes.reenvioRecente && corpos.reenvios === 1) {
          return erro(
            route,
            429,
            'reenvio_muito_recente',
            'Este convite foi reenviado há pouco. Aguarde alguns minutos.',
          );
        }
        const c = convites.find((x) => x.id === id);
        return route.fulfill({ json: { convite: c } });
      }
      return route.fulfill({ json: { itens: convites, total: convites.length } });
    }

    if (recurso === 'veiculos') {
      return route.fulfill({
        json: {
          itens: [
            {
              id: VEICULO_ID,
              descricao: 'Veículo 01',
              placa: 'ABC1D23',
              ativo: true,
              requisicao_aberta: false,
              acoes_permitidas: ['editar', 'desativar'],
            },
          ],
          acoes_permitidas: acoesColecao,
        },
      });
    }

    if (recurso === 'configuracoes') {
      if (m === 'PUT') {
        const pedido = req.postDataJSON() as Record<string, boolean>;
        corpos.config.push(pedido);
        // Dependência: o servidor recusa ligar Abastecimento sem Quilometragem.
        if (pedido.abastecimento_manutencao && !config.quilometragem) {
          return erro(
            route,
            409,
            'dependencia_recurso',
            "'Abastecimento e manutenção' exige 'Quilometragem' ligado.",
          );
        }
        // Dependência (contrato assumido): 'KM lançado pelo vigilante' exige Quilometragem.
        if (pedido.vigilante_km && !config.quilometragem && !pedido.quilometragem) {
          return erro(
            route,
            409,
            'dependencia_recurso',
            "'KM lançado pelo vigilante' exige 'Quilometragem' ligado.",
          );
        }
        // Dependência: 'Motorista registra' exige o Perfil Motorista ligado.
        if (pedido.motorista_registra && !config.motorista && !pedido.motorista) {
          return erro(
            route,
            409,
            'dependencia_recurso',
            "'Motorista registra saída e chegada' exige 'Perfil Motorista' ligado.",
          );
        }
        Object.assign(config, pedido);
      }
      const itens = [
        {
          chave: 'quilometragem',
          rotulo: 'Quilometragem',
          descricao: 'Exige km de saída e de chegada.',
          habilitado: config.quilometragem,
          exige: [],
          motivo_indisponibilidade: null,
          acoes_permitidas: [config.quilometragem ? 'desligar' : 'ligar'],
        },
        {
          chave: 'abastecimento_manutencao',
          rotulo: 'Abastecimento e manutenção',
          descricao: 'Registra abastecimentos e manutenções.',
          habilitado: config.abastecimento_manutencao,
          exige: ['quilometragem'],
          // Tela carregada antes de ligar a Quilometragem: ainda oferece "ligar" (estado velho).
          motivo_indisponibilidade: null,
          acoes_permitidas: [config.abastecimento_manutencao ? 'desligar' : 'ligar'],
        },
        {
          chave: 'motorista',
          rotulo: 'Perfil Motorista',
          descricao: 'Permite designar um motorista, que acompanha as próprias viagens.',
          habilitado: config.motorista,
          exige: [],
          motivo_indisponibilidade: null,
          acoes_permitidas: [config.motorista ? 'desligar' : 'ligar'],
        },
        {
          chave: 'motorista_registra',
          rotulo: 'Motorista registra saída e chegada',
          descricao: 'O motorista designado registra a saída e a chegada da própria viagem.',
          habilitado: config.motorista_registra,
          exige: ['motorista'],
          motivo_indisponibilidade: config.motorista ? null : 'Ligue antes o Perfil Motorista.',
          acoes_permitidas: config.motorista
            ? [config.motorista_registra ? 'desligar' : 'ligar']
            : [],
        },
        {
          chave: 'motorista_localizacao',
          rotulo: 'Localização no registro do motorista',
          descricao:
            'Coleta a localização do aparelho quando o motorista registra saída e chegada.',
          habilitado: config.motorista_localizacao,
          exige: ['motorista_registra'],
          motivo_indisponibilidade: config.motorista_registra
            ? null
            : 'Ligue antes o motorista registra saída e chegada.',
          acoes_permitidas: config.motorista_registra
            ? [config.motorista_localizacao ? 'desligar' : 'ligar']
            : [],
        },
        {
          chave: 'vigilante_km',
          rotulo: 'KM lançado pelo vigilante',
          descricao: 'O vigilante informa o km de saída e de chegada ao registrar saída e chegada.',
          habilitado: config.vigilante_km,
          exige: ['quilometragem'],
          motivo_indisponibilidade: config.quilometragem ? null : 'Ligue antes a Quilometragem.',
          acoes_permitidas: config.quilometragem
            ? [config.vigilante_km ? 'desligar' : 'ligar']
            : [],
        },
        {
          chave: 'autorizador_decide_propria',
          rotulo: 'Autorizador decide a própria requisição',
          descricao: 'Permite que autorizador e admin autorizem ou neguem a própria requisição.',
          habilitado: config.autorizador_decide_propria,
          exige: [],
          motivo_indisponibilidade: null,
          acoes_permitidas: [config.autorizador_decide_propria ? 'desligar' : 'ligar'],
        },
        {
          chave: 'autorizador_km',
          rotulo: 'KM pelo autorizador',
          descricao: 'Autorizador e admin informam o km de saída e de chegada.',
          habilitado: config.autorizador_km,
          exige: ['quilometragem'],
          motivo_indisponibilidade: config.quilometragem
            ? null
            : 'Ligue antes a Quilometragem para o autorizador lançar km.',
          acoes_permitidas: config.quilometragem
            ? [config.autorizador_km ? 'desligar' : 'ligar']
            : [],
        },
        {
          chave: 'mfa',
          rotulo: 'Verificação em duas etapas (MFA)',
          descricao: 'Exige segundo fator no login.',
          habilitado: false,
          exige: [],
          motivo_indisponibilidade: 'Para ligar, entre antes com o segundo fator (TOTP).',
          acoes_permitidas: [],
        },
      ];
      return route.fulfill({ json: { recursos: config, itens, acoes_permitidas: ['editar'] } });
    }
    if (recurso === 'ia' && id === 'saudacoes') {
      const ocasioes = ['Madrugada', 'Natal', 'Ano Novo'];
      const [sid, sacao] = [partes[2], partes[3]];
      if (!sid && m === 'GET') {
        const st = url.searchParams.get('status');
        return route.fulfill({
          json: { itens: frases.filter((f) => !st || f.status === st), ocasioes },
        });
      }
      if (!sid && m === 'POST') {
        const corpo = req.postDataJSON() as { ocasiao: string; texto: string };
        corpos.saudacoes.push(corpo);
        frases.push({
          id: `f${frases.length + 1}`,
          ...corpo,
          origem: 'manual',
          status: 'aprovada',
          criado_em: new Date().toISOString(),
          acoes_permitidas: ['rejeitar'],
        });
        return route.fulfill({ status: 201, json: frases[frases.length - 1] });
      }
      if (sid === 'gerar' && m === 'POST') {
        corpos.saudacoes.push(req.postDataJSON());
        if (opcoes.erroGerarSaudacao) {
          return erro(route, 502, 'ia_indisponivel', opcoes.erroGerarSaudacao);
        }
        frases.push({
          id: `f${frases.length + 1}`,
          ocasiao: 'Natal',
          texto: 'Feliz Natal, {nome}!',
          origem: 'ia',
          status: 'pendente',
          criado_em: new Date().toISOString(),
          acoes_permitidas: ['aprovar', 'rejeitar'],
        });
        return route.fulfill({ status: 201, json: { itens: [] } });
      }
      const alvo = frases.find((f) => f.id === sid);
      if ((sacao === 'aprovar' || sacao === 'rejeitar') && m === 'POST' && alvo) {
        corpos.saudacoes.push({ id: alvo.id, acao: sacao });
        alvo.status = sacao === 'aprovar' ? 'aprovada' : 'rejeitada';
        alvo.acoes_permitidas = sacao === 'aprovar' ? ['rejeitar'] : ['aprovar', 'excluir'];
        return route.fulfill({ json: alvo });
      }
      if (sid && !sacao && m === 'DELETE' && alvo) {
        corpos.saudacoes.push({ id: alvo.id, acao: 'excluir' });
        frases.splice(frases.indexOf(alvo), 1);
        return route.fulfill({ json: { id: alvo.id, excluida: true } });
      }
      return erro(route, 404, 'nao_encontrado', 'Recurso não encontrado.');
    }
    if (recurso === 'ia' && id === 'testar' && m === 'POST') {
      corpos.iaTestar.push(req.postDataJSON());
      if (opcoes.resultadoTesteIA === 'conflito') {
        return erro(
          route,
          409,
          'dependencia_recurso',
          'Ligue a chave geral de IA antes de testar a conexão.',
        );
      }
      const ok = opcoes.resultadoTesteIA !== 'falha';
      return route.fulfill({
        json: {
          ok,
          provedor: 'anthropic',
          modelo: 'claude-haiku-5-5',
          latencia_ms: ok ? 412 : null,
          tokens_entrada: ok ? 12 : 0,
          tokens_saida: ok ? 3 : 0,
          tipo: ok ? null : 'autenticacao',
          mensagem: ok
            ? 'Conexão com o provedor funcionando.'
            : 'O provedor recusou a chave configurada no servidor.',
        },
      });
    }
    if (recurso === 'ia') {
      if (m === 'PUT') {
        const pedido = req.postDataJSON() as Record<string, unknown>;
        corpos.ia.push(pedido);
        // O servidor é quem recusa: ligar a chave geral sem provedor escolhido.
        if (pedido.mestre === true && !ia.provedor && !pedido.provedor) {
          return erro(
            route,
            409,
            'dependencia_recurso',
            'Escolha um provedor com chave configurada antes de ligar a IA.',
          );
        }
        Object.assign(ia, pedido);
        if (pedido.provedor && !pedido.modelo) ia.modelo = 'claude-haiku-5-5';
      }
      const semProvedor = !ia.provedor;
      return route.fulfill({
        json: {
          servidor_habilitado: true,
          motivo_indisponibilidade: null,
          itens: [
            {
              chave: 'mestre',
              rotulo: 'Inteligência artificial',
              descricao: 'Chave geral.',
              habilitado: ia.mestre,
              motivo_indisponibilidade: null,
              acoes_permitidas: [ia.mestre ? 'desligar' : 'ligar'],
            },
            {
              chave: 'saudacao',
              rotulo: 'Saudação sazonal no login',
              descricao: 'Mensagem de boas-vindas por data comemorativa.',
              habilitado: ia.saudacao,
              motivo_indisponibilidade: ia.mestre ? null : 'Ligue antes a chave geral de IA.',
              acoes_permitidas: ia.mestre ? [ia.saudacao ? 'desligar' : 'ligar'] : [],
            },
          ],
          provedores: [
            {
              chave: 'openai',
              rotulo: 'OpenAI',
              chave_configurada: false,
              modelos: ['gpt-5.4-mini'],
              circuito_aberto: false,
            },
            {
              chave: 'anthropic',
              rotulo: 'Anthropic',
              chave_configurada: true,
              modelos: ['claude-haiku-5-5', 'claude-sonnet-5-5'],
              circuito_aberto: false,
            },
          ],
          provedor: semProvedor ? null : ia.provedor,
          modelo: ia.modelo,
          provedor_reserva: null,
          teto_tokens_mes: null,
          teto_efetivo_tokens_mes: 200000,
          acoes_permitidas: ['editar'],
          testes_disponiveis: opcoes.testesIA ?? ['anthropic'],
        },
      });
    }
    return erro(route, 404, 'nao_encontrado', 'Recurso não encontrado.');
  });

  return { corpos };
}
