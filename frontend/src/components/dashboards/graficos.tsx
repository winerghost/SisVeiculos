'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

/** Cartão de indicador: rótulo, valor em destaque e detalhe opcional (sempre em texto). */
export function Kpi({
  rotulo,
  valor,
  detalhe,
  className,
}: {
  rotulo: string;
  valor: React.ReactNode;
  detalhe?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('rounded-xl border border-borda bg-white p-4', className)}>
      <dt className="text-sm font-medium text-texto-2">{rotulo}</dt>
      <dd className="mt-1 text-2xl font-semibold break-words">{valor}</dd>
      {detalhe ? <dd className="mt-0.5 text-sm text-texto-2">{detalhe}</dd> : null}
    </div>
  );
}

export interface Tabela {
  colunas: string[];
  linhas: React.ReactNode[][];
}

export function TabelaSimples({ titulo, colunas, linhas }: Tabela & { titulo: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">{titulo}</caption>
        <thead>
          <tr className="border-b border-borda">
            {colunas.map((c) => (
              <th key={c} scope="col" className="px-2 py-2 font-semibold">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {linhas.map((l, i) => (
            <tr key={i} className="border-b border-borda last:border-0">
              {l.map((c, j) =>
                j === 0 ? (
                  <th key={j} scope="row" className="px-2 py-2 font-medium">
                    {c}
                  </th>
                ) : (
                  <td key={j} className="px-2 py-2">
                    {c}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Gráfico com alternativa textual: o botão "Ver como tabela" troca o gráfico por uma tabela
 * com os mesmos valores (nunca só cor).
 */
export function ComTabela({
  titulo,
  grafico,
  tabela,
}: {
  titulo: string;
  grafico: React.ReactNode;
  tabela: Tabela;
}) {
  const [tabelaVisivel, setTabelaVisivel] = React.useState(false);
  const id = React.useId();
  return (
    <section aria-labelledby={id} className="rounded-xl border border-borda bg-white p-4">
      <div className="flex items-center justify-between gap-3">
        <h3 id={id} className="text-lg font-semibold">
          {titulo}
        </h3>
        <Button
          type="button"
          variante="contorno"
          aria-pressed={tabelaVisivel}
          onClick={() => setTabelaVisivel((v) => !v)}
          className="shrink-0"
        >
          Ver como tabela
        </Button>
      </div>
      <div className="mt-3">
        {tabelaVisivel ? <TabelaSimples titulo={titulo} {...tabela} /> : grafico}
      </div>
    </section>
  );
}

export interface ItemBarra {
  rotulo: string;
  /** Valor usado só para o comprimento da barra. */
  valor: number;
  /** Texto com o valor real (sempre visível). */
  texto: string;
}

/** Barras horizontais em CSS: o valor é sempre escrito, a barra é só reforço visual. */
export function BarrasHorizontais({ itens }: { itens: ItemBarra[] }) {
  const maximo = Math.max(...itens.map((i) => i.valor), 0);
  return (
    <ul className="flex flex-col gap-3">
      {itens.map((i) => (
        <li key={i.rotulo}>
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="font-medium">{i.rotulo}</span>
            <span className="text-right text-texto-2">{i.texto}</span>
          </div>
          <div aria-hidden className="mt-1 h-3 overflow-hidden rounded-full bg-[#e8ecf2]">
            <div
              className="h-full rounded-full bg-primaria"
              style={{ width: `${maximo > 0 ? Math.max((i.valor / maximo) * 100, 0) : 0}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}

export interface PontoEmpilhado {
  rotulo: string;
  partes: number[];
}

export interface SerieEmpilhada {
  nome: string;
  /** Tipo de preenchimento (além da cor, para não depender só dela). */
  padrao: 'solido' | 'listras' | 'pontos';
}

function Preenchimentos({ prefixo }: { prefixo: string }) {
  return (
    <defs>
      <pattern id={`${prefixo}-listras`} width="6" height="6" patternUnits="userSpaceOnUse">
        <rect width="6" height="6" fill="#fde2e0" />
        <path d="M0 6 L6 0" stroke="#b3261e" strokeWidth="2" />
      </pattern>
      <pattern id={`${prefixo}-pontos`} width="6" height="6" patternUnits="userSpaceOnUse">
        <rect width="6" height="6" fill="#e8ecf2" />
        <circle cx="3" cy="3" r="1.4" fill="#3b4658" />
      </pattern>
    </defs>
  );
}

const preenchimento = (prefixo: string, p: SerieEmpilhada['padrao']) =>
  p === 'solido' ? '#0f3d75' : `url(#${prefixo}-${p})`;

/** Barras verticais empilhadas em SVG. Os valores reais ficam na tabela alternativa. */
export function BarrasEmpilhadas({
  pontos,
  series,
  descricao,
}: {
  pontos: PontoEmpilhado[];
  series: SerieEmpilhada[];
  descricao: string;
}) {
  const prefixo = `g${React.useId().replace(/[^a-zA-Z0-9]/g, '')}`;
  const altura = 140;
  const larg = 20;
  const maximo = Math.max(...pontos.map((p) => p.partes.reduce((a, b) => a + b, 0)), 1);
  const largura = Math.max(pontos.length * larg, 1);
  return (
    <div>
      <svg
        role="img"
        aria-label={descricao}
        viewBox={`0 0 ${largura} ${altura}`}
        className="h-40 w-full"
        preserveAspectRatio="none"
      >
        <Preenchimentos prefixo={prefixo} />
        <line x1="0" y1={altura - 0.5} x2={largura} y2={altura - 0.5} stroke="#8493ab" />
        {pontos.map((p, i) => {
          let y = altura;
          return (
            <g key={p.rotulo}>
              {p.partes.map((v, k) => {
                const h = (v / maximo) * (altura - 2);
                y -= h;
                const serie = series[k];
                return h > 0 && serie ? (
                  <rect
                    key={serie.nome}
                    x={i * larg + 3}
                    y={y}
                    width={larg - 6}
                    height={h}
                    fill={preenchimento(prefixo, serie.padrao)}
                    stroke="#0f3d75"
                    strokeWidth="1"
                    vectorEffect="non-scaling-stroke"
                  />
                ) : null;
              })}
            </g>
          );
        })}
      </svg>
      <div className="mt-1 flex justify-between text-xs text-texto-2">
        <span>{pontos[0]?.rotulo}</span>
        <span>{pontos.length > 1 ? pontos[pontos.length - 1]?.rotulo : ''}</span>
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {series.map((s) => (
          <li key={s.nome} className="flex items-center gap-2">
            <svg aria-hidden viewBox="0 0 16 16" className="size-4 shrink-0">
              <rect
                width="16"
                height="16"
                fill={preenchimento(prefixo, s.padrao)}
                stroke="#0f3d75"
                strokeWidth="1"
              />
            </svg>
            {s.nome}
          </li>
        ))}
      </ul>
    </div>
  );
}
