'use client';

import * as React from 'react';
import { SlidersHorizontal } from 'lucide-react';
import { descreverFiltros } from '@/lib/relatorios';
import type { FiltroRelatorio } from '@/lib/tipos';
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
import { Switch } from '@/components/ui/switch';

/** Campo genérico: o tipo e as opções vêm do catálogo; nada é específico de um relatório. */
function Campo({
  f,
  valor,
  onChange,
}: {
  f: FiltroRelatorio;
  valor: string;
  onChange: (v: string) => void;
}) {
  const id = `rel-filtro-${f.nome}`;
  if (f.tipo === 'bool') {
    return (
      <div className="flex items-center justify-between gap-3">
        <span id={id} className="text-base font-medium">
          {f.rotulo}
        </span>
        <Switch
          checked={valor === 'true'}
          onChange={() => onChange(valor === 'true' ? 'false' : 'true')}
          aria-labelledby={id}
        />
      </div>
    );
  }
  return (
    <div>
      <Label htmlFor={id}>
        {f.rotulo}
        {f.obrigatorio ? <span className="text-erro"> (obrigatório)</span> : null}
      </Label>
      {f.tipo === 'data' ? (
        <Input
          id={id}
          type="date"
          required={f.obrigatorio}
          value={valor}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <Select
          id={id}
          required={f.obrigatorio}
          value={valor}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">{f.obrigatorio ? 'Selecione' : 'Sem filtro'}</option>
          {(f.opcoes ?? []).map((o) => (
            <option key={o.valor} value={o.valor}>
              {o.rotulo}
            </option>
          ))}
        </Select>
      )}
    </div>
  );
}

/** Botão "Filtros" + Drawer com o formulário gerado pelo catálogo, e o resumo do que vale agora. */
export function FiltrosRelatorio({
  filtros,
  valores,
  onAplicar,
  onLimpar,
}: {
  filtros: FiltroRelatorio[];
  valores: Record<string, string>;
  onAplicar: (v: Record<string, string>) => void;
  onLimpar: () => void;
}) {
  const [aberto, setAberto] = React.useState(false);
  const [rascunho, setRascunho] = React.useState(valores);

  if (filtros.length === 0) {
    return <p className="text-texto-2">Este relatório não tem filtros.</p>;
  }
  const resumo = descreverFiltros(filtros, valores);

  return (
    <div>
      <Button
        type="button"
        variante="contorno"
        onClick={() => {
          setRascunho(valores);
          setAberto(true);
        }}
      >
        <SlidersHorizontal aria-hidden className="size-5" /> Filtros
      </Button>
      <p className="mt-2 text-sm text-texto-2" aria-label="Filtros aplicados">
        {resumo.length > 0 ? resumo.join(' · ') : 'Nenhum filtro preenchido.'}
      </p>

      <Drawer open={aberto} onOpenChange={setAberto}>
        <DrawerContent>
          <DrawerTitle className="text-xl font-semibold">Filtros</DrawerTitle>
          <DrawerDescription className="mb-3 text-texto-2">
            O servidor valida os valores informados.
          </DrawerDescription>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              onAplicar(rascunho);
              setAberto(false);
            }}
            className="flex flex-col gap-3"
          >
            {filtros.map((f) => (
              <Campo
                key={f.nome}
                f={f}
                valor={rascunho[f.nome] ?? ''}
                onChange={(v) => setRascunho((r) => ({ ...r, [f.nome]: v }))}
              />
            ))}
            <div className="mt-2 grid grid-cols-2 gap-3">
              <Button
                type="button"
                variante="contorno"
                tamanho="cheio"
                onClick={() => {
                  onLimpar();
                  setAberto(false);
                }}
              >
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
