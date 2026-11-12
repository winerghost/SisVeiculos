import { api } from '@/lib/api';
import type { FiltroRelatorio, PreviaRelatorio } from '@/lib/tipos';
import { ResumoIA } from '@/components/resumo-ia';
import { descreverFiltros } from '@/lib/relatorios';

/** Prévia em tabela acessível. Os textos já vêm prontos do servidor; só são exibidos. */
export function Previa({
  previa,
  filtros,
  consulta,
}: {
  previa: PreviaRelatorio;
  filtros: FiltroRelatorio[];
  /** Filtros em vigor no formulário (os mesmos usados na prévia); o resumo usa estes. */
  consulta: Record<string, string>;
}) {
  const usados = descreverFiltros(filtros, previa.filtros);
  return (
    <section aria-labelledby="rel-previa-titulo" className="mt-6">
      <h2 id="rel-previa-titulo" className="text-xl font-semibold">
        Prévia: {previa.rotulo}
      </h2>
      {usados.length > 0 ? (
        <p className="mt-1 text-sm text-texto-2">Filtros usados: {usados.join(' · ')}</p>
      ) : null}
      <ResumoIA
        key={`${previa.tipo}:${JSON.stringify(consulta)}`}
        habilitado={previa.resumo_ia === true}
        aviso="Resumo gerado por IA do relatório completo; confira com o arquivo do relatório"
        gerar={() => api.resumoRelatorio(previa.tipo, consulta)}
      />
      {previa.colunas_omitidas && previa.colunas_omitidas.length > 0 ? (
        <p role="note" className="mt-3 rounded-padrao bg-[#e8ecf2] px-3 py-2 text-texto-2">
          Não aparecem na prévia, só no arquivo:{' '}
          {previa.colunas_omitidas.map((k) => k.replace(/_/g, ' ')).join(', ')}.
        </p>
      ) : null}
      {previa.truncado ? (
        <p role="note" className="mt-3 rounded-padrao bg-[#fff1d6] px-3 py-2 text-[#7a4a00]">
          Mostrando as primeiras {previa.linhas.length} linhas. Baixe o arquivo para ver tudo.
        </p>
      ) : null}
      {previa.linhas.length === 0 ? (
        <p className="mt-3 rounded-xl border border-borda bg-white p-4 text-texto-2">
          Nenhum registro para os filtros escolhidos.
        </p>
      ) : (
        <div
          role="region"
          aria-label={`Tabela: ${previa.rotulo}`}
          tabIndex={0}
          className="mt-3 overflow-x-auto rounded-xl border border-borda bg-white"
        >
          <table className="w-full min-w-max border-collapse text-left text-sm">
            <caption className="sr-only">
              {previa.rotulo}: {previa.total_linhas} linhas
            </caption>
            <thead>
              <tr className="border-b border-borda bg-fundo">
                {previa.colunas.map((c) => (
                  <th key={c.chave} scope="col" className="px-3 py-2 font-semibold">
                    {c.rotulo}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {previa.linhas.map((l, i) => (
                <tr key={i} className="border-b border-borda last:border-0">
                  {previa.colunas.map((c) => (
                    <td key={c.chave} className="px-3 py-2 align-top">
                      {l[c.chave] ?? ''}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-2 text-sm text-texto-2">{previa.total_linhas} linhas.</p>
    </section>
  );
}
