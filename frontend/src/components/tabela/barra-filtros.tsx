'use client';

import * as React from 'react';
import { Search, X } from 'lucide-react';
import { Input, Select } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

export interface Opcao {
  valor: string;
  rotulo: string;
}

export interface ValoresFiltro {
  q: string;
  status: string;
  veiculo_id: string;
  unidade: string;
  ordenar: string;
}

export const FILTRO_VAZIO: ValoresFiltro = {
  q: '',
  status: '',
  veiculo_id: '',
  unidade: '',
  ordenar: '',
};

/** Espera ~300 ms depois da última tecla antes de repassar o texto (menos chamadas à API). */
function useAtrasado(valor: string, ms = 300): string {
  const [atrasado, setAtrasado] = React.useState(valor);
  React.useEffect(() => {
    const t = window.setTimeout(() => setAtrasado(valor), ms);
    return () => window.clearTimeout(t);
  }, [valor, ms]);
  return atrasado;
}

/**
 * Busca e filtros da lista. Só coleta a intenção: quem filtra é a API (ou a página, no modo
 * cliente); erros de filtro inválido vêm da API e a página os exibe. Um filtro só aparece se a
 * página passar as opções dele (que vêm da API: veículos, unidades de `eu`).
 */
export function BarraFiltros({
  id,
  valores,
  aoMudar,
  rotuloBusca = 'Buscar',
  dicaBusca,
  status,
  veiculos,
  unidades,
  ordenacoes,
  recolhivel,
}: {
  /** Prefixo dos ids dos campos (há uma barra por tela). */
  id: string;
  valores: ValoresFiltro;
  aoMudar: (v: ValoresFiltro) => void;
  rotuloBusca?: string;
  dicaBusca?: string;
  status?: Opcao[];
  veiculos?: Opcao[];
  unidades?: Opcao[];
  ordenacoes?: Opcao[];
  /** Celular: os filtros ficam num bloco que abre e fecha (a busca segue sempre visível). */
  recolhivel?: boolean;
}) {
  const [texto, setTexto] = React.useState(valores.q);
  const atrasado = useAtrasado(texto);
  const atual = React.useRef(valores);
  React.useEffect(() => {
    atual.current = valores;
  });
  // O texto digitado só sobe depois da pausa; os selects sobem na hora.
  React.useEffect(() => {
    if (atrasado !== atual.current.q) aoMudar({ ...atual.current, q: atrasado });
  }, [atrasado, aoMudar]);

  const mudar = (campo: keyof ValoresFiltro) => (e: React.ChangeEvent<HTMLSelectElement>) =>
    aoMudar({ ...valores, [campo]: e.target.value });

  const select = (campo: keyof ValoresFiltro, rotulo: string, todos: string, opcoes?: Opcao[]) =>
    opcoes && opcoes.length > 0 ? (
      <div className="min-w-0">
        <Label htmlFor={`${id}-${campo}`}>{rotulo}</Label>
        <Select id={`${id}-${campo}`} value={valores[campo]} onChange={mudar(campo)}>
          {todos ? <option value="">{todos}</option> : null}
          {opcoes.map((o) => (
            <option key={o.valor} value={o.valor}>
              {o.rotulo}
            </option>
          ))}
        </Select>
      </div>
    ) : null;

  const campos = [
    select('status', 'Status', 'Todos', status),
    select('veiculo_id', 'Veículo', 'Todos', veiculos),
    select('unidade', 'Unidade', 'Todas', unidades),
    select('ordenar', 'Ordenar por', '', ordenacoes),
  ];
  const selects = campos.some(Boolean) ? (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {campos.map((c, i) => (c ? <React.Fragment key={i}>{c}</React.Fragment> : null))}
    </div>
  ) : null;
  // Só a contagem para o rótulo do bloco recolhido (ordenação não conta como filtro).
  const ativos = [valores.status, valores.veiculo_id, valores.unidade].filter(Boolean).length;

  const busca = `${id}-q`;
  return (
    <div role="search" aria-label="Busca e filtros" className="mb-4 flex flex-col gap-3">
      <div>
        <Label htmlFor={busca}>{rotuloBusca}</Label>
        <div className="relative">
          <Search
            aria-hidden
            className="pointer-events-none absolute top-1/2 left-3 size-5 -translate-y-1/2 text-texto-2"
          />
          <Input
            id={busca}
            type="search"
            inputMode="search"
            autoComplete="off"
            minLength={2}
            maxLength={80}
            value={texto}
            onChange={(e) => setTexto(e.target.value)}
            aria-describedby={dicaBusca ? `${busca}-dica` : undefined}
            className="pr-12 pl-10"
          />
          {texto ? (
            <button
              type="button"
              aria-label="Limpar busca"
              onClick={() => setTexto('')}
              className="absolute top-1/2 right-1 inline-flex size-11 -translate-y-1/2 items-center justify-center rounded-padrao text-texto-2 hover:bg-fundo"
            >
              <X aria-hidden className="size-5" />
            </button>
          ) : null}
        </div>
        {dicaBusca ? (
          <p id={`${busca}-dica`} className="mt-1 text-sm text-texto-2">
            {dicaBusca}
          </p>
        ) : null}
      </div>
      {recolhivel && selects ? (
        <details className="rounded-padrao border border-borda bg-white">
          <summary className="flex min-h-11 cursor-pointer items-center px-3 font-medium">
            Filtros e ordenação{ativos > 0 ? ` · ${ativos} ativo${ativos > 1 ? 's' : ''}` : ''}
          </summary>
          <div className="px-3 pb-3">{selects}</div>
        </details>
      ) : (
        selects
      )}
    </div>
  );
}
