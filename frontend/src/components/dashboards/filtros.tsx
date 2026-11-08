'use client';

import * as React from 'react';
import { SlidersHorizontal } from 'lucide-react';
import { rotuloPeriodo } from '@/lib/formato';
import type { FiltrosDashboard, Veiculo } from '@/lib/tipos';
import { Button } from '@/components/ui/button';
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerTitle,
} from '@/components/ui/drawer';
import { Input, Select } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

/**
 * Barra de filtros em Drawer. Os valores são só enviados à API (que valida e aplica o padrão
 * quando vazios); abaixo, mostra os filtros efetivos que o servidor devolveu.
 */
export function BarraFiltros({
  aplicados,
  efetivos,
  unidades,
  veiculos,
  comGranularidade,
  onAplicar,
}: {
  aplicados: FiltrosDashboard;
  efetivos: Record<string, string | null | undefined> | undefined;
  unidades: string[];
  veiculos: Veiculo[];
  comGranularidade: boolean;
  onAplicar: (f: FiltrosDashboard) => void;
}) {
  const [aberto, setAberto] = React.useState(false);
  const [rascunho, setRascunho] = React.useState<FiltrosDashboard>(aplicados);

  const mudar = (campo: keyof FiltrosDashboard) => (e: { target: { value: string } }) =>
    setRascunho((r) => ({ ...r, [campo]: e.target.value }));

  const abrir = () => {
    setRascunho(aplicados);
    setAberto(true);
  };
  const aplicar = (e: React.FormEvent) => {
    e.preventDefault();
    onAplicar(rascunho);
    setAberto(false);
  };
  const limpar = () => {
    onAplicar({});
    setAberto(false);
  };

  const descVeiculo = (id: string) => veiculos.find((v) => v.id === id)?.descricao ?? id;
  const partes: string[] = [];
  if (efetivos?.inicio || efetivos?.fim) {
    partes.push(
      `Período: ${rotuloPeriodo(efetivos.inicio ?? '')} a ${rotuloPeriodo(efetivos.fim ?? '')}`,
    );
  }
  if (efetivos?.unidade) partes.push(`Unidade: ${efetivos.unidade}`);
  if (efetivos?.veiculo) partes.push(`Veículo: ${descVeiculo(efetivos.veiculo)}`);
  if (efetivos?.granularidade) partes.push(`Agrupamento: ${efetivos.granularidade}`);

  return (
    <div className="mb-4">
      <Button type="button" variante="contorno" onClick={abrir}>
        <SlidersHorizontal aria-hidden className="size-5" /> Filtros
      </Button>
      {partes.length > 0 ? (
        <p className="mt-2 text-sm text-texto-2" aria-label="Filtros aplicados">
          {partes.join(' · ')}
        </p>
      ) : null}

      <Drawer open={aberto} onOpenChange={setAberto}>
        <DrawerContent>
          <DrawerTitle className="text-xl font-semibold">Filtros</DrawerTitle>
          <DrawerDescription className="mb-3 text-texto-2">
            Campos em branco usam o padrão do servidor.
          </DrawerDescription>
          <form onSubmit={aplicar} className="flex flex-col gap-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label htmlFor="filtro-inicio">Início</Label>
                <Input
                  id="filtro-inicio"
                  type="date"
                  value={rascunho.inicio ?? ''}
                  onChange={mudar('inicio')}
                />
              </div>
              <div>
                <Label htmlFor="filtro-fim">Fim</Label>
                <Input
                  id="filtro-fim"
                  type="date"
                  value={rascunho.fim ?? ''}
                  onChange={mudar('fim')}
                />
              </div>
            </div>
            <div>
              <Label htmlFor="filtro-unidade">Unidade</Label>
              <Select
                id="filtro-unidade"
                value={rascunho.unidade ?? ''}
                onChange={mudar('unidade')}
              >
                <option value="">Todas</option>
                {unidades.map((u) => (
                  <option key={u} value={u}>
                    {u}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="filtro-veiculo">Veículo</Label>
              <Select
                id="filtro-veiculo"
                value={rascunho.veiculo ?? ''}
                onChange={mudar('veiculo')}
              >
                <option value="">Todos</option>
                {veiculos.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.descricao}
                  </option>
                ))}
              </Select>
            </div>
            {comGranularidade ? (
              <div>
                <Label htmlFor="filtro-granularidade">Agrupar por</Label>
                <Select
                  id="filtro-granularidade"
                  value={rascunho.granularidade ?? ''}
                  onChange={mudar('granularidade')}
                >
                  <option value="">Padrão</option>
                  <option value="dia">Dia</option>
                  <option value="semana">Semana</option>
                  <option value="mes">Mês</option>
                </Select>
              </div>
            ) : null}
            <div className="mt-2 grid grid-cols-2 gap-3">
              <Button type="button" variante="contorno" tamanho="cheio" onClick={limpar}>
                Limpar
              </Button>
              <Button type="submit" tamanho="cheio">
                Aplicar
              </Button>
            </div>
            <DrawerClose asChild>
              <Button type="button" variante="link" className="self-center">
                Fechar
              </Button>
            </DrawerClose>
          </form>
        </DrawerContent>
      </Drawer>
    </div>
  );
}
