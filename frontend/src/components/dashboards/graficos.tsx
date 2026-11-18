'use client';

import * as React from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  LabelList,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from 'recharts';
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
    <div className={cn('min-w-0 rounded-xl border border-borda bg-white p-4', className)}>
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
              <th key={c} scope="col" className="px-2 py-2 font-semibold whitespace-nowrap">
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
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 id={id} className="min-w-0 text-lg font-semibold break-words">
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

// ---- Infraestrutura dos gráficos (Recharts) ----

// Paleta AGU usada dentro do SVG (o Tailwind não alcança atributos do Recharts).
const COR = {
  primaria: '#0f3d75',
  texto: '#0e1b2e',
  texto2: '#55627a',
  eixo: '#8493ab',
  trilho: '#e8ecf2',
  erro: '#b3261e',
  erroFundo: '#fde2e0',
  neutro: '#3b4658',
} as const;

const CONSULTA_MOVIMENTO = '(prefers-reduced-motion: reduce)';

function assinarMovimento(aviso: () => void) {
  const m = window.matchMedia(CONSULTA_MOVIMENTO);
  m.addEventListener('change', aviso);
  return () => m.removeEventListener('change', aviso);
}

/** `true` quando o sistema pede menos movimento (aí os gráficos aparecem sem animar). */
export function useMovimentoReduzido(): boolean {
  return React.useSyncExternalStore(
    assinarMovimento,
    () => window.matchMedia(CONSULTA_MOVIMENTO).matches,
    () => true,
  );
}

interface Caixa {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Caixa cartesiana do elemento que recebe o rótulo (barra ou ponto), se houver. */
function caixaDe(viewBox: unknown): Caixa | null {
  if (!viewBox || typeof viewBox !== 'object') return null;
  const v = viewBox as Partial<Record<keyof Caixa, unknown>>;
  if (
    typeof v.x !== 'number' ||
    typeof v.y !== 'number' ||
    typeof v.width !== 'number' ||
    typeof v.height !== 'number'
  ) {
    return null;
  }
  return { x: v.x, y: v.y, width: v.width, height: v.height };
}

const textoDe = (v: unknown): string =>
  typeof v === 'string' || typeof v === 'number' ? String(v) : '';

/** Rolagem horizontal quando há muitos pontos: os valores escritos nunca se sobrepõem. */
function AreaRolavel({
  descricao,
  larguraMinima,
  children,
}: {
  descricao: string;
  larguraMinima: number;
  children: React.ReactNode;
}) {
  return (
    <div
      role="group"
      aria-label={descricao}
      // Focável para quem usa teclado conseguir rolar o gráfico largo.
      tabIndex={0}
      className="overflow-x-auto rounded-padrao focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foco"
    >
      <div aria-hidden style={{ minWidth: larguraMinima }}>
        {children}
      </div>
    </div>
  );
}

const eixoX = {
  dataKey: 'rotulo',
  interval: 'preserveStartEnd' as const,
  minTickGap: 12,
  tickLine: false,
  tick: { fontSize: 12, fill: COR.texto2 },
  axisLine: { stroke: COR.eixo },
};

export interface ItemBarra {
  rotulo: string;
  /** Valor usado só para o comprimento da barra. */
  valor: number;
  /** Texto com o valor real (sempre visível). */
  texto: string;
}

const LINHA_BARRA = 60;
const ESPESSURA_BARRA = 12;

/**
 * Barras horizontais: rótulo e valor sempre escritos acima de cada barra; a barra é só reforço
 * visual. `maximo` fixa a escala (ex.: 1 para taxas de 0 a 1); sem ele, a maior barra ocupa tudo.
 */
export function BarrasHorizontais({
  itens,
  maximo,
  descricao,
}: {
  itens: ItemBarra[];
  maximo?: number;
  descricao?: string;
}) {
  const reduzido = useMovimentoReduzido();
  const topo = Math.max(maximo ?? 0, ...itens.map((i) => i.valor), 0) || 1;
  // `trilho` é uma barra cheia por trás de cada valor: carrega os rótulos mesmo quando o valor é 0.
  const dados = itens.map((i) => ({
    rotulo: i.rotulo,
    texto: i.texto,
    valor: Math.max(i.valor, 0),
    trilho: topo,
  }));
  const rotuloAcessivel = descricao ?? itens.map((i) => `${i.rotulo}: ${i.texto}`).join('; ');
  return (
    <div role="img" aria-label={rotuloAcessivel}>
      <ResponsiveContainer width="100%" height={itens.length * LINHA_BARRA + 16}>
        <BarChart
          data={dados}
          layout="vertical"
          margin={{ top: 16, right: 0, bottom: 0, left: 0 }}
          barGap={-ESPESSURA_BARRA}
          accessibilityLayer={false}
        >
          <XAxis type="number" hide domain={[0, topo]} />
          <YAxis type="category" dataKey="rotulo" hide />
          <Bar
            dataKey="trilho"
            barSize={ESPESSURA_BARRA}
            fill={COR.trilho}
            radius={ESPESSURA_BARRA / 2}
            isAnimationActive={false}
          >
            <LabelList
              dataKey="rotulo"
              content={(p) => {
                const c = caixaDe(p.viewBox);
                return c ? (
                  <text x={c.x} y={c.y - 24} fill={COR.texto} fontSize={14} fontWeight={500}>
                    {textoDe(p.value)}
                  </text>
                ) : null;
              }}
            />
            <LabelList
              dataKey="texto"
              content={(p) => {
                const c = caixaDe(p.viewBox);
                return c ? (
                  <text x={c.x} y={c.y - 6} fill={COR.texto2} fontSize={14}>
                    {textoDe(p.value)}
                  </text>
                ) : null;
              }}
            />
          </Bar>
          <Bar
            dataKey="valor"
            barSize={ESPESSURA_BARRA}
            fill={COR.primaria}
            radius={ESPESSURA_BARRA / 2}
            isAnimationActive={!reduzido}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
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

/** Padrões SVG num SVG oculto; `url(#id)` vale em qualquer SVG do mesmo documento. */
function Preenchimentos({ prefixo }: { prefixo: string }) {
  return (
    <svg aria-hidden width="0" height="0" className="absolute">
      <defs>
        <pattern id={`${prefixo}-listras`} width="6" height="6" patternUnits="userSpaceOnUse">
          <rect width="6" height="6" fill={COR.erroFundo} />
          <path d="M0 6 L6 0" stroke={COR.erro} strokeWidth="2" />
        </pattern>
        <pattern id={`${prefixo}-pontos`} width="6" height="6" patternUnits="userSpaceOnUse">
          <rect width="6" height="6" fill={COR.trilho} />
          <circle cx="3" cy="3" r="1.4" fill={COR.neutro} />
        </pattern>
      </defs>
    </svg>
  );
}

const preenchimento = (prefixo: string, p: SerieEmpilhada['padrao']) =>
  p === 'solido' ? COR.primaria : `url(#${prefixo}-${p})`;

function Legenda({ prefixo, series }: { prefixo: string; series: SerieEmpilhada[] }) {
  return (
    <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm">
      {series.map((s) => (
        <li key={s.nome} className="flex items-center gap-2">
          <svg aria-hidden viewBox="0 0 16 16" className="size-4 shrink-0">
            <rect
              width="16"
              height="16"
              fill={preenchimento(prefixo, s.padrao)}
              stroke={COR.primaria}
              strokeWidth="1"
            />
          </svg>
          {s.nome}
        </li>
      ))}
    </ul>
  );
}

const ALTURA_VERTICAL = 200;

/** Valor escrito sobre a barra (ou sobre a pilha), centralizado. */
function ValorNoTopo(p: { viewBox?: unknown; value?: unknown }) {
  const c = caixaDe(p.viewBox);
  const t = textoDe(p.value);
  if (!c || t === '') return null;
  return (
    <text
      x={c.x + c.width / 2}
      y={c.y - 6}
      textAnchor="middle"
      fill={COR.texto}
      fontSize={12}
      fontWeight={600}
    >
      {t}
    </text>
  );
}

/** Valor dentro do segmento, só se couber (os demais seguem na tabela). */
function ValorNoSegmento({
  viewBox,
  value,
  solido,
}: {
  viewBox?: unknown;
  value?: unknown;
  solido: boolean;
}) {
  const c = caixaDe(viewBox);
  const t = textoDe(value);
  if (!c || t === '' || t === '0' || c.height < 16 || c.width < 18) return null;
  return (
    <text
      x={c.x + c.width / 2}
      y={c.y + c.height / 2}
      dominantBaseline="central"
      textAnchor="middle"
      fontSize={11}
      fontWeight={600}
      fill={solido ? '#ffffff' : COR.texto}
      // Halo branco sobre listras e pontos para manter o contraste do número.
      stroke={solido ? 'none' : '#ffffff'}
      strokeWidth={solido ? 0 : 3}
      paintOrder="stroke"
    >
      {t}
    </text>
  );
}

function BarrasVerticais({
  pontos,
  series,
  descricao,
  empilhado,
}: {
  pontos: PontoEmpilhado[];
  series: SerieEmpilhada[];
  descricao: string;
  empilhado: boolean;
}) {
  const prefixo = `g${React.useId().replace(/[^a-zA-Z0-9]/g, '')}`;
  const reduzido = useMovimentoReduzido();
  const dados = pontos.map((p) => {
    const linha: Record<string, string | number> = {
      rotulo: p.rotulo,
      total: p.partes.reduce((a, b) => a + b, 0),
      // Série mais alta com valor: é nela que o total da pilha é escrito.
      topo: p.partes.reduce((acc, v, k) => (v > 0 ? k : acc), -1),
    };
    series.forEach((_, k) => {
      linha[`s${k}`] = p.partes[k] ?? 0;
    });
    return linha;
  });
  const porPonto = empilhado ? 48 : 28 + series.length * 24;
  return (
    <div>
      <Preenchimentos prefixo={prefixo} />
      <AreaRolavel descricao={descricao} larguraMinima={pontos.length * porPonto}>
        <ResponsiveContainer width="100%" height={ALTURA_VERTICAL}>
          <BarChart
            data={dados}
            margin={{ top: 22, right: 4, bottom: 0, left: 4 }}
            accessibilityLayer={false}
          >
            <XAxis {...eixoX} />
            <YAxis hide />
            {series.map((s, k) => (
              <Bar
                key={s.nome}
                dataKey={`s${k}`}
                name={s.nome}
                stackId={empilhado ? 'pilha' : undefined}
                fill={preenchimento(prefixo, s.padrao)}
                stroke={COR.primaria}
                strokeWidth={1}
                maxBarSize={56}
                isAnimationActive={!reduzido}
              >
                {empilhado ? (
                  <LabelList
                    dataKey={`s${k}`}
                    content={(p) => (
                      <ValorNoSegmento
                        viewBox={p.viewBox}
                        value={p.value}
                        solido={s.padrao === 'solido'}
                      />
                    )}
                  />
                ) : null}
                <LabelList
                  dataKey={(d: Record<string, string | number>) =>
                    empilhado ? (d.topo === k ? d.total : '') : d[`s${k}`]
                  }
                  content={ValorNoTopo}
                />
              </Bar>
            ))}
          </BarChart>
        </ResponsiveContainer>
      </AreaRolavel>
      <Legenda prefixo={prefixo} series={series} />
    </div>
  );
}

/**
 * Barras verticais empilhadas: total escrito no topo de cada pilha e o valor de cada segmento
 * quando couber. Todos os valores ficam também na tabela alternativa.
 */
export function BarrasEmpilhadas(props: {
  pontos: PontoEmpilhado[];
  series: SerieEmpilhada[];
  descricao: string;
}) {
  return <BarrasVerticais {...props} empilhado />;
}

/** Barras verticais lado a lado (uma por série), com o valor escrito no topo de cada barra. */
export function BarrasAgrupadas(props: {
  pontos: PontoEmpilhado[];
  series: SerieEmpilhada[];
  descricao: string;
}) {
  return <BarrasVerticais {...props} empilhado={false} />;
}

/** Ponto de uma série temporal simples: `texto` é o valor formatado, sempre escrito. */
export type PontoSerie = ItemBarra;

/** Linha com área (série no tempo), com marcador e valor escrito em cada ponto. */
export function AreaSerie({ pontos, descricao }: { pontos: PontoSerie[]; descricao: string }) {
  const reduzido = useMovimentoReduzido();
  return (
    <AreaRolavel descricao={descricao} larguraMinima={pontos.length * 64}>
      <ResponsiveContainer width="100%" height={ALTURA_VERTICAL}>
        <AreaChart
          data={pontos}
          margin={{ top: 24, right: 28, bottom: 0, left: 28 }}
          accessibilityLayer={false}
        >
          <XAxis {...eixoX} />
          <YAxis hide domain={[0, 'auto']} />
          <Area
            type="linear"
            dataKey="valor"
            stroke={COR.primaria}
            strokeWidth={2}
            fill={COR.primaria}
            fillOpacity={0.12}
            dot={{ r: 4, fill: COR.primaria, stroke: '#ffffff', strokeWidth: 2 }}
            activeDot={false}
            isAnimationActive={!reduzido}
          >
            <LabelList
              dataKey="texto"
              position="top"
              offset={10}
              fontSize={12}
              fontWeight={600}
              fill={COR.texto}
            />
          </Area>
        </AreaChart>
      </ResponsiveContainer>
    </AreaRolavel>
  );
}
