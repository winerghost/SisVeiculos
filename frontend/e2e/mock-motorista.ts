import type { Page, Route } from '@playwright/test';
import { gruposAcao, recursos } from './recursos';

// API fake do Perfil Motorista (contrato provisório: GET /api/motorista/viagens[/<id>]). As regras
// daqui existem só para o teste: o front real nunca as calcula.
const MOTORISTA = { id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', nome: 'Carlos Lima' };
const VEICULO = 'Veículo 01 · ABC-1D23';

type Status = 'autorizada' | 'em_uso' | 'concluida';

interface Viagem {
  id: string;
  status: Status;
  trajeto: string;
  hora_saida: string;
  saida_real_em: string | null;
  chegada_real_em: string | null;
}

export interface OpcoesMotorista {
  /** A API não devolve ações (ex.: `motorista_registra` desligado): nenhum botão aparece. */
  semAcoes?: boolean;
  /** Recurso desligado: /api/motorista/viagens responde 403. */
  recursoDesligado?: boolean;
  /** Recurso `motorista_localizacao` ligado: a API pede latitude/longitude em campos_acao. */
  localizacao?: boolean;
  /** A API inclui `atribuir_motorista` em acoes_permitidas (viagem não concluída). */
  trocarMotorista?: boolean;
  /** Dados de GET /api/admin/requisicoes/<id>/localizacoes; sem isto a API responde 403. */
  localizacoesAdmin?: Record<string, unknown>[];
  /** GET /api/motoristas devolve `padrao` (contrato assumido: id do único motorista ativo). */
  motoristaPadrao?: boolean;
  /** Ids de viagens já arquivadas pelo motorista (a viagem concluída tem o id ...442). */
  arquivadasIds?: string[];
}

const OUTRO = { id: 'ffffffff-ffff-4fff-8fff-ffffffffffff', nome: 'Rita Alves' };

const erro = (route: Route, status: number, codigo: string, mensagem: string) =>
  route.fulfill({ status, json: { erro: { codigo, mensagem } } });

export async function mockarMotorista(page: Page, opcoes: OpcoesMotorista = {}) {
  const agora = new Date().toISOString();
  const viagens: Viagem[] = [
    {
      id: '44444444-4444-4444-8444-444444444441',
      status: 'autorizada',
      trajeto: 'Sede -> Fórum Federal',
      hora_saida: agora,
      saida_real_em: null,
      chegada_real_em: null,
    },
    {
      id: '44444444-4444-4444-8444-444444444442',
      status: 'concluida',
      trajeto: 'Sede -> Procuradoria Regional',
      hora_saida: '2026-11-01T12:00:00Z',
      saida_real_em: '2026-11-01T12:10:00Z',
      chegada_real_em: '2026-11-01T14:00:00Z',
    },
  ];
  const acoesRecebidas: string[] = [];
  const corposAcao: { acao: string; corpo: Record<string, unknown> }[] = [];
  const motoristasAtribuidos: unknown[] = [];
  let motorista = MOTORISTA;

  // Arquivar é pessoal do motorista; só viagens concluídas (a regra real vive no backend).
  const arquivadas = new Set<string>(opcoes.arquivadasIds ?? []);
  const organizacoes: { acao: string; id: string }[] = [];
  const acoes = (v: Viagem): string[] => {
    if (opcoes.semAcoes) return [];
    const troca = opcoes.trocarMotorista ? ['atribuir_motorista'] : [];
    // Só para o teste da seção de localização: a API real só devolve `ver_localizacao` ao admin.
    const local = opcoes.localizacoesAdmin && v.saida_real_em ? ['ver_localizacao'] : [];
    if (v.status === 'autorizada') return ['registrar_saida', ...troca];
    if (v.status === 'em_uso') return ['registrar_chegada', ...troca, ...local];
    return [...local, arquivadas.has(v.id) ? 'desarquivar' : 'arquivar'];
  };
  const camposAcao = (v: Viagem) => {
    if (!opcoes.localizacao) return {};
    const local = { campos_obrigatorios: ['latitude', 'longitude'] };
    if (v.status === 'autorizada') return { registrar_saida: local };
    if (v.status === 'em_uso') return { registrar_chegada: local };
    return {};
  };
  const etapa = (chave: string, rotulo: string, situacao: string, em: string | null) => ({
    chave,
    rotulo,
    situacao,
    em,
    por: em ? { nome: 'Ana Souza' } : null,
    duracao_desde_anterior_s: null,
    decorrido_s: null,
  });
  const ver = (v: Viagem) => {
    const i = ['autorizada', 'em_uso', 'concluida'].indexOf(v.status);
    const sit = (k: number) =>
      k < i || v.status === 'concluida' ? 'concluida' : k === i ? 'atual' : 'pendente';
    const etapas = [
      etapa('solicitada', 'Solicitada', 'concluida', v.hora_saida),
      etapa('autorizada', 'Autorizada', sit(0), v.hora_saida),
      etapa('saida', 'Saída registrada', sit(1), v.saida_real_em),
      etapa('chegada', 'Chegada registrada', sit(2), v.chegada_real_em),
    ];
    return {
      id: v.id,
      usuario_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      nome_solicitante: 'Marcos Silva',
      unidade: 'UEA',
      hora_saida: v.hora_saida,
      trajeto: v.trajeto,
      observacao: null,
      veiculo_id: '11111111-1111-4111-8111-111111111111',
      veiculo_descricao: VEICULO,
      status: v.status,
      autorizado_por: null,
      autorizado_em: v.hora_saida,
      saida_real_em: v.saida_real_em,
      chegada_real_em: v.chegada_real_em,
      saida_registrada_por: null,
      chegada_registrada_por: null,
      cancelada_por: null,
      cancelada_em: null,
      criado_em: v.hora_saida,
      atualizado_em: v.hora_saida,
      km_saida: null,
      km_chegada: null,
      autorizado_por_nome: 'Ana Souza',
      saida_registrada_por_nome: null,
      chegada_registrada_por_nome: null,
      historico: [],
      motorista,
      acoes_permitidas: acoes(v),
      // O motorista não tem ações de "decidir"/"solicitar"; só operar, designar, consultar, organizar.
      grupos_acao: gruposAcao(acoes(v), 'decidir'),
      arquivada: arquivadas.has(v.id),
      campos_acao: camposAcao(v),
      linha_do_tempo: {
        desfecho: v.status === 'concluida' ? 'concluida' : 'em_andamento',
        etapa_atual: etapas.findIndex((e) => e.situacao === 'atual') + 1 || etapas.length,
        total_etapas: etapas.length,
        texto_equivalente: `Viagem ${v.status}.`,
        gerado_em: agora,
        etapas,
      },
    };
  };
  const autenticado = (route: Route) =>
    (route.request().headers()['authorization'] ?? '') === 'Bearer tok-motorista';

  await page.route('**/auth/v1/token?grant_type=password', async (route) => {
    const corpo = route.request().postDataJSON() as { email: string; password: string };
    if (corpo.password !== 'senha-correta') {
      return route.fulfill({ status: 400, json: { error: 'invalid_grant' } });
    }
    await route.fulfill({
      json: {
        access_token: 'tok-motorista',
        token_type: 'bearer',
        expires_in: 3600,
        expires_at: Math.floor(Date.now() / 1000) + 3600,
        refresh_token: 'refresh-motorista',
        user: {
          id: MOTORISTA.id,
          aud: 'authenticated',
          email: corpo.email,
          app_metadata: {},
          user_metadata: {},
          created_at: '2026-01-01T00:00:00Z',
        },
      },
    });
  });
  await page.route('**/auth/v1/logout*', (route) => route.fulfill({ status: 204 }));

  await page.route('**/api/eu', (route) =>
    autenticado(route)
      ? route.fulfill({
          json: {
            id: MOTORISTA.id,
            nome: MOTORISTA.nome,
            unidade: 'UEA',
            rotulo_perfil: 'UEA · Motorista',
            unidades: ['PU', 'PF', 'CJU', 'UEA'],
            role: 'motorista',
            permissoes: ['ver_viagens_atribuidas'],
            recursos: recursos(
              opcoes.localizacao
                ? { motorista: true, motorista_registra: true, motorista_localizacao: true }
                : { motorista: true },
            ),
            foto_url: null,
          },
        })
      : erro(route, 401, 'nao_autenticado', 'Sessão inválida ou expirada.'),
  );
  await page.route('**/api/notificacoes/contagem', (route) =>
    route.fulfill({ json: { nao_lidas: 0 } }),
  );
  await page.route(/\/api\/(dashboards|relatorios)(\/.*)?(\?.*)?$/, (route) =>
    erro(route, 403, 'sem_permissao', 'Você não tem permissão para esta ação.'),
  );

  await page.route(/\/api\/motorista\/viagens(\/[^/?]+){0,2}(\?.*)?$/, async (route) => {
    if (!autenticado(route)) return erro(route, 401, 'nao_autenticado', 'Autenticação necessária.');
    if (opcoes.recursoDesligado) {
      return erro(route, 403, 'recurso_desabilitado', 'O Perfil Motorista está desligado.');
    }
    const url = new URL(route.request().url());
    const id = url.pathname.split('/')[4];
    const organizar = url.pathname.split('/')[5];
    if (!id) {
      // Padrão da API: arquivadas=false; true = só as arquivadas do motorista.
      const soArquivadas = url.searchParams.get('arquivadas') === 'true';
      const itens = viagens.filter((x) => arquivadas.has(x.id) === soArquivadas).map(ver);
      return route.fulfill({ json: { itens, total: itens.length } });
    }
    if (organizar === 'arquivar' || organizar === 'desarquivar') {
      const alvo = viagens.find((x) => x.id === id);
      if (!alvo) return erro(route, 404, 'nao_encontrado', 'Viagem não encontrada.');
      if (!acoes(alvo).includes(organizar) && arquivadas.has(id) === (organizar === 'arquivar')) {
        // idempotente
      } else if (!acoes(alvo).includes(organizar)) {
        return erro(
          route,
          409,
          'transicao_invalida',
          'Só é possível arquivar ou desarquivar uma requisição concluída, negada ou cancelada.',
        );
      }
      organizacoes.push({ acao: organizar, id });
      if (organizar === 'arquivar') arquivadas.add(id);
      else arquivadas.delete(id);
      return route.fulfill({ json: ver(alvo) });
    }
    const v = viagens.find((x) => x.id === id);
    return v
      ? route.fulfill({ json: ver(v) })
      : erro(route, 404, 'nao_encontrado', 'Viagem não encontrada.');
  });

  // Ações sobre a requisição (mesmas rotas de /saida e /chegada). Só vale o que a API permitiu.
  await page.route(/\/api\/requisicoes\/[^/]+\/(saida|chegada)$/, async (route) => {
    const partes = new URL(route.request().url()).pathname.split('/');
    const v = viagens.find((x) => x.id === partes[3]);
    const acao = partes[4] ?? '';
    const nome = acao === 'saida' ? 'registrar_saida' : 'registrar_chegada';
    if (!v || !acoes(v).includes(nome)) {
      return erro(route, 409, 'transicao_invalida', 'A viagem não permite esta ação agora.');
    }
    const corpo = (route.request().postDataJSON() ?? {}) as Record<string, unknown>;
    // Regra da API (só no teste): com o recurso ligado, sem coordenadas a ação é recusada.
    if (
      opcoes.localizacao &&
      (typeof corpo.latitude !== 'number' || typeof corpo.longitude !== 'number')
    ) {
      return erro(route, 400, 'localizacao_obrigatoria', 'A localização é obrigatória.');
    }
    acoesRecebidas.push(acao);
    corposAcao.push({ acao, corpo });
    const t = new Date().toISOString();
    if (acao === 'saida') {
      v.status = 'em_uso';
      v.saida_real_em = t;
    } else {
      v.status = 'concluida';
      v.chegada_real_em = t;
    }
    await route.fulfill({ json: ver(v) });
  });

  await page.route('**/api/motoristas', (route) =>
    route.fulfill({
      json: {
        itens: opcoes.motoristaPadrao ? [MOTORISTA] : [MOTORISTA, OUTRO],
        padrao: opcoes.motoristaPadrao ? MOTORISTA.id : null,
      },
    }),
  );
  await page.route(/\/api\/requisicoes\/[^/]+\/motorista$/, async (route) => {
    const corpo = route.request().postDataJSON() as { motorista_id: string };
    motoristasAtribuidos.push(corpo);
    motorista = corpo.motorista_id === OUTRO.id ? OUTRO : MOTORISTA;
    const v = viagens.find((x) => x.id === new URL(route.request().url()).pathname.split('/')[3]);
    await route.fulfill({ json: v ? ver(v) : {} });
  });
  await page.route(/\/api\/requisicoes\/[^/]+$/, async (route) => {
    const v = viagens.find((x) => x.id === new URL(route.request().url()).pathname.split('/')[3]);
    return v
      ? route.fulfill({ json: ver(v) })
      : erro(route, 404, 'nao_encontrado', 'Requisição não encontrada.');
  });
  await page.route(/\/api\/admin\/requisicoes\/[^/]+\/localizacoes$/, (route) =>
    opcoes.localizacoesAdmin
      ? route.fulfill({ json: { itens: opcoes.localizacoesAdmin } })
      : erro(route, 403, 'sem_permissao', 'Você não tem permissão para esta ação.'),
  );

  return { viagens, acoesRecebidas, corposAcao, motoristasAtribuidos, organizacoes };
}
