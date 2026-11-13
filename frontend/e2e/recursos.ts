// As 13 chaves de `recursos` que a API devolve em /api/eu (catálogo de Configurações do backend:
// backend/app/services/admin_configuracoes.py). Todas desligadas por padrão, como no servidor.
export const CHAVES_RECURSOS = [
  'quilometragem',
  'abastecimento_manutencao',
  'checklist',
  'mfa',
  'relatorio_email',
  'relatorio_whatsapp',
  'notificacao_email_criticos',
  'motorista',
  'motorista_registra',
  'motorista_localizacao',
  'vigilante_km',
  'autorizador_decide_propria',
  'autorizador_km',
] as const;

export type ChaveRecurso = (typeof CHAVES_RECURSOS)[number];

/** Objeto `recursos` completo (13 chaves); `ligados` sobrepõe os que estiverem ligados. */
export function recursos(ligados: Partial<Record<ChaveRecurso, boolean>> = {}) {
  return Object.fromEntries(CHAVES_RECURSOS.map((c) => [c, ligados[c] === true])) as Record<
    ChaveRecurso,
    boolean
  >;
}

/** Agrupamento `grupos_acao` da API (só categorias não vazias). Só para os mocks. */
export const GRUPO_DA_ACAO: Record<string, string> = {
  autorizar: 'decidir',
  negar: 'decidir',
  registrar_saida: 'operar',
  registrar_chegada: 'operar',
  atribuir_motorista: 'designar',
  ver_checklist: 'consultar',
  ver_localizacao: 'consultar',
  arquivar: 'organizar',
  desarquivar: 'organizar',
};

export function gruposAcao(
  acoes: string[],
  grupoCancelar: 'solicitar' | 'decidir',
): Record<string, string[]> {
  const grupos: Record<string, string[]> = {};
  for (const a of acoes) {
    const g = a === 'cancelar' ? grupoCancelar : (GRUPO_DA_ACAO[a] ?? 'consultar');
    (grupos[g] ??= []).push(a);
  }
  // O Flask ordena as chaves do JSON alfabeticamente: o front não pode depender da ordem.
  return Object.fromEntries(Object.entries(grupos).sort(([a], [b]) => a.localeCompare(b)));
}
