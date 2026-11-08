import type { Page, Route } from '@playwright/test';

// API fake dos relatórios (contrato combinado com o backend). Usada junto com mockarApi.
// As regras daqui existem só para o teste: o front real nunca as calcula.
export interface OpcoesRelatorios {
  email?: boolean;
  whatsapp?: boolean;
  /** O acompanhamento do e-mail termina em "falhou". */
  envioFalha?: boolean;
  /** A prévia vem truncada (total maior que as linhas mostradas). */
  truncado?: boolean;
  /** A lista de destinatários vem parcial. */
  destinatariosParcial?: boolean;
  /** O POST de e-mail responde com este erro. */
  erroEmail?: { status: number; codigo: string; mensagem: string };
  /** O arquivo responde 422 relatorio_muito_grande. */
  muitoGrande?: boolean;
}

const erro = (route: Route, status: number, codigo: string, mensagem: string) =>
  route.fulfill({ status, json: { erro: { codigo, mensagem } } });

const MOVIMENTACAO = {
  tipo: 'movimentacao',
  rotulo: 'Movimentação de veículos',
  descricao: 'Saídas e chegadas registradas no período.',
  filtros: [
    {
      nome: 'inicio',
      rotulo: 'Início',
      tipo: 'data',
      opcoes: null,
      obrigatorio: false,
      padrao: null,
    },
    { nome: 'fim', rotulo: 'Fim', tipo: 'data', opcoes: null, obrigatorio: false, padrao: null },
    {
      nome: 'unidade',
      rotulo: 'Unidade',
      tipo: 'enum',
      opcoes: [
        { valor: 'UEA', rotulo: 'UEA' },
        { valor: 'PU', rotulo: 'PU' },
      ],
      obrigatorio: false,
      padrao: null,
    },
    {
      nome: 'situacao',
      rotulo: 'Situação',
      tipo: 'enum',
      opcoes: [
        { valor: 'todas', rotulo: 'Todas' },
        { valor: 'concluidas', rotulo: 'Somente concluídas' },
      ],
      obrigatorio: false,
      padrao: null,
    },
    {
      nome: 'incluir_canceladas',
      rotulo: 'Incluir canceladas',
      tipo: 'bool',
      opcoes: null,
      obrigatorio: false,
      padrao: false,
    },
  ],
};
const PENDENCIAS = {
  tipo: 'pendencias',
  rotulo: 'Pendências',
  descricao: 'Requisições aguardando decisão ou registro.',
  filtros: [],
};
const USO = {
  tipo: 'uso_veiculos',
  rotulo: 'Uso dos veículos',
  descricao: 'Tempo de uso por veículo.',
  filtros: [
    {
      nome: 'inicio',
      rotulo: 'Início',
      tipo: 'data',
      opcoes: null,
      obrigatorio: true,
      padrao: null,
    },
  ],
};

export async function mockarRelatorios(page: Page, opcoes: OpcoesRelatorios = {}) {
  const consultas: { rota: string; query: Record<string, string> }[] = [];
  const corposEmail: unknown[] = [];
  let consultasEnvio = 0;

  const papel = (route: Route): 'solicitante' | 'autorizador' | null => {
    const auth = route.request().headers()['authorization'] ?? '';
    if (auth === 'Bearer tok-solicitante') return 'solicitante';
    if (auth === 'Bearer tok-autorizador') return 'autorizador';
    return null;
  };

  await page.route(/\/api\/relatorios(\/.*)?(\?.*)?$/, async (route) => {
    const quem = papel(route);
    if (!quem) return erro(route, 401, 'nao_autenticado', 'Autenticação necessária.');
    const url = new URL(route.request().url());
    const partes = url.pathname.split('/').slice(3); // depois de /api/relatorios
    const query = Object.fromEntries(url.searchParams.entries());

    if (partes.length === 0) {
      const autorizador = quem === 'autorizador';
      return route.fulfill({
        json: {
          relatorios: autorizador ? [MOVIMENTACAO, PENDENCIAS, USO] : [MOVIMENTACAO, PENDENCIAS],
          canais: {
            download: true,
            email: opcoes.email ?? false,
            whatsapp: opcoes.whatsapp ?? false,
          },
          exportacao_paineis: autorizador
            ? { volume: { tipo: 'movimentacao', parametros: { situacao: 'concluidas' } } }
            : {},
          limite_linhas: 5000,
          fuso: 'America/Sao_Paulo',
        },
      });
    }
    if (partes[0] === 'destinatarios') {
      return route.fulfill({
        json: {
          itens: [
            { id: 'dest-1', nome: 'Ana Souza' },
            { id: 'dest-2', nome: 'Carlos Lima' },
          ],
          truncado: opcoes.destinatariosParcial ?? false,
        },
      });
    }
    if (partes[0] === 'envios') {
      consultasEnvio += 1;
      if (consultasEnvio < 2) return route.fulfill({ json: { id: 'envio-1', status: 'enviando' } });
      return route.fulfill({
        json: opcoes.envioFalha
          ? {
              id: 'envio-1',
              status: 'falhou',
              erro_codigo: 'smtp_indisponivel',
              mensagem: 'O servidor de e-mail está indisponível. Tente mais tarde.',
            }
          : { id: 'envio-1', status: 'enviado', erro_codigo: null },
      });
    }

    const [tipo, acao] = partes;
    consultas.push({ rota: `${tipo}/${acao ?? ''}`, query });
    if (acao === 'previa') {
      return route.fulfill({
        json: {
          tipo,
          rotulo: 'Movimentação de veículos',
          filtros: { ...query },
          colunas: [
            { chave: 'data', rotulo: 'Data' },
            { chave: 'solicitante', rotulo: 'Solicitante' },
            { chave: 'situacao', rotulo: 'Situação' },
          ],
          linhas: [
            { data: '05/11/2026', solicitante: 'Marcos Silva', situacao: 'Concluída' },
            { data: '06/11/2026', solicitante: 'Ana Souza', situacao: 'Em uso' },
          ],
          total_linhas: opcoes.truncado ? 50 : 2,
          truncado: opcoes.truncado ?? false,
        },
      });
    }
    if (acao === 'arquivo') {
      if (opcoes.muitoGrande) {
        return erro(
          route,
          422,
          'relatorio_muito_grande',
          'O relatório passa de 5000 linhas; estreite os filtros.',
        );
      }
      if (query.inicio && query.fim && query.fim < query.inicio) {
        return erro(route, 422, 'periodo_invalido', 'A data final deve ser posterior à inicial.');
      }
      const ext = query.formato === 'xlsx' ? 'xlsx' : 'csv';
      return route.fulfill({
        status: 200,
        headers: {
          'content-type':
            ext === 'csv'
              ? 'text/csv; charset=utf-8'
              : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          'content-disposition': `attachment; filename="${tipo}_2026-11-08.${ext}"`,
        },
        body: 'data;solicitante\n05/11/2026;Marcos Silva\n',
      });
    }
    if (acao === 'email' && route.request().method() === 'POST') {
      corposEmail.push(route.request().postDataJSON());
      if (opcoes.erroEmail) {
        const e = opcoes.erroEmail;
        return erro(route, e.status, e.codigo, e.mensagem);
      }
      return route.fulfill({ status: 202, json: { id: 'envio-1', status: 'pendente' } });
    }
    return erro(route, 404, 'nao_encontrado', 'Recurso não encontrado.');
  });

  return { consultas, corposEmail };
}
