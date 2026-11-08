import type { FiltroRelatorio, PreviaRelatorio } from '@/lib/tipos';
import { descreverFiltros } from '@/lib/relatorios';

/** Prévia em tabela acessível. Os textos já vêm prontos do servidor; só são exibidos. */
export function Previa({
  previa,
  filtros,
}: {
  previa: PreviaRelatorio;
  filtros: FiltroRelatorio[];
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
