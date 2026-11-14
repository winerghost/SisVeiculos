'use client';

import * as React from 'react';
import { admin } from '@/lib/api';
import type { StatusSaudacao } from '@/lib/tipos';
import { cn } from '@/lib/utils';
import { useCarga } from '@/lib/use-carga';
import { useEnvio } from '@/lib/use-envio';
import { Alerta } from '@/components/ui/alert';
import { Spinner } from '@/components/ui/spinner';
import { Button } from '@/components/ui/button';
import { Input, Select } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ItemArrastavel, type ControleGesto } from '@/components/item-arrastavel';

const ABAS: { status: StatusSaudacao; rotulo: string }[] = [
  { status: 'pendente', rotulo: 'Pendentes' },
  { status: 'aprovada', rotulo: 'Aprovadas' },
  { status: 'rejeitada', rotulo: 'Rejeitadas' },
];

/**
 * Revisão das frases de saudação. Só exibe e envia a intenção do admin: as ocasiões, os status e
 * qualquer recusa (inclusive da geração por IA) vêm da API e aparecem como vieram.
 */
export function BlocoSaudacoes() {
  const [status, setStatus] = React.useState<StatusSaudacao>('pendente');
  const lista = useCarga(() => admin.saudacoes({ status }), status);
  const envio = useEnvio();
  const [ocasiaoNova, setOcasiaoNova] = React.useState('');
  const [ocasiaoGerar, setOcasiaoGerar] = React.useState('');
  const [aviso, setAviso] = React.useState<string | null>(null);
  // Qual botão disparou o envio (só ele mostra o spinner; os demais só ficam desabilitados).
  const [clicado, setClicado] = React.useState<string | null>(null);

  const ocasioes = lista.dados?.ocasioes ?? [];

  async function decidir(id: string, acao: 'aprovar' | 'rejeitar') {
    if (envio.ocupado) return;
    setAviso(null);
    setClicado(`${id}:${acao}`);
    const r = await envio.executar(() => admin.decidirSaudacao(id, acao));
    setClicado(null);
    if (r.ok) await lista.recarregar();
  }

  async function excluir(id: string) {
    if (envio.ocupado) return;
    setAviso(null);
    setClicado(`${id}:excluir`);
    const r = await envio.executar(() => admin.excluirSaudacao(id));
    setClicado(null);
    if (r.ok) await lista.recarregar();
  }

  async function adicionar(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const texto = String(new FormData(form).get('texto') ?? '').trim();
    if (envio.ocupado) return;
    setAviso(null);
    setClicado('adicionar');
    const r = await envio.executar(() =>
      admin.criarSaudacao({ ocasiao: ocasiaoNova || ocasioes[0] || '', texto }),
    );
    setClicado(null);
    if (!r.ok) return;
    form.reset();
    setAviso('Frase adicionada.');
    if (status === 'aprovada') await lista.recarregar();
    else setStatus('aprovada');
  }

  async function gerar(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const quantidade = Number(new FormData(e.currentTarget).get('quantidade'));
    if (envio.ocupado) return;
    setAviso(null);
    setClicado('gerar');
    const r = await envio.executar(() =>
      admin.gerarSaudacoes({ ocasiao: ocasiaoGerar || ocasioes[0] || '', quantidade }),
    );
    setClicado(null);
    if (!r.ok) return;
    setAviso('Frases geradas. Revise-as em Pendentes.');
    if (status === 'pendente') await lista.recarregar();
    else setStatus('pendente');
  }

  const opcoes = ocasioes.map((o) => (
    <option key={o} value={o}>
      {o}
    </option>
  ));

  return (
    <div className="mt-4 flex flex-col gap-4 rounded-xl border border-borda bg-white p-4">
      <h3 id="saudacoes-titulo" className="text-lg font-semibold">
        Saudações
      </h3>
      {lista.erro ? <Alerta>{lista.erro}</Alerta> : null}
      {envio.erro ? <Alerta>{envio.erro}</Alerta> : null}
      <div role="status" aria-live="polite">
        {aviso ? <p className="text-texto-2">{aviso}</p> : null}
      </div>

      <div role="group" aria-label="Filtrar frases por status" className="flex flex-wrap gap-2">
        {ABAS.map((a) => (
          <button
            key={a.status}
            type="button"
            aria-pressed={status === a.status}
            onClick={() => setStatus(a.status)}
            className={cn(
              'flex min-h-11 items-center rounded-full border px-4 text-base font-medium',
              status === a.status
                ? 'border-primaria bg-primaria text-white'
                : 'border-borda-campo bg-white text-texto',
            )}
          >
            {a.rotulo}
          </button>
        ))}
      </div>

      {lista.carregando ? (
        <p role="status" className="flex items-center gap-2 text-texto-2">
          <Spinner />
          Carregando...
        </p>
      ) : lista.dados && lista.dados.itens.length === 0 ? (
        <p className="text-texto-2">Nenhuma frase neste status.</p>
      ) : (
        <ul aria-labelledby="saudacoes-titulo" className="flex flex-col gap-3">
          {lista.dados?.itens.map((f) => {
            const conteudo = (controle?: ControleGesto) => (
              <>
                <p className="text-base">{f.texto}</p>
                <p className="text-sm text-texto-2">
                  {f.ocasiao} · {f.origem === 'ia' ? 'Gerada por IA' : 'Manual'}
                </p>
                {!controle?.confirmando &&
                (f.acoes_permitidas.includes('aprovar') ||
                  f.acoes_permitidas.includes('rejeitar') ||
                  (controle && f.acoes_permitidas.includes('excluir'))) ? (
                  <div className="flex flex-wrap gap-2">
                    {f.acoes_permitidas.includes('aprovar') ? (
                      <Button
                        type="button"
                        carregando={clicado === `${f.id}:aprovar`}
                        disabled={envio.ocupado}
                        onClick={() => void decidir(f.id, 'aprovar')}
                      >
                        {clicado === `${f.id}:aprovar` ? 'Aprovando...' : 'Aprovar'}
                      </Button>
                    ) : null}
                    {f.acoes_permitidas.includes('rejeitar') ? (
                      <Button
                        type="button"
                        variante="perigo"
                        carregando={clicado === `${f.id}:rejeitar`}
                        disabled={envio.ocupado}
                        onClick={() => void decidir(f.id, 'rejeitar')}
                      >
                        {clicado === `${f.id}:rejeitar` ? 'Rejeitando...' : 'Rejeitar'}
                      </Button>
                    ) : null}
                    {controle && f.acoes_permitidas.includes('excluir') ? (
                      <Button
                        type="button"
                        variante="contorno"
                        disabled={envio.ocupado}
                        onClick={() => controle.acionar('direita')}
                      >
                        Excluir
                      </Button>
                    ) : null}
                  </div>
                ) : null}
              </>
            );
            const classe = 'flex flex-col gap-2 rounded-padrao border border-borda p-3';
            if (status !== 'rejeitada') {
              return (
                <li key={f.id} className={classe}>
                  {conteudo()}
                </li>
              );
            }
            return (
              <ItemArrastavel
                key={f.id}
                className="border border-borda"
                ocupado={envio.ocupado}
                focavel
                esquerda={
                  f.acoes_permitidas.includes('aprovar')
                    ? {
                        rotulo: 'Aprovar',
                        cor: 'primaria',
                        onAcionar: () => void decidir(f.id, 'aprovar'),
                      }
                    : undefined
                }
                direita={
                  f.acoes_permitidas.includes('excluir')
                    ? {
                        rotulo: 'Excluir',
                        cor: 'erro',
                        onAcionar: () => void excluir(f.id),
                        confirmar: {
                          pergunta: 'Excluir definitivamente?',
                          rotulo: 'Confirmar',
                          rotuloAria: 'Confirmar exclusão',
                        },
                      }
                    : undefined
                }
              >
                {(controle) => conteudo(controle)}
              </ItemArrastavel>
            );
          })}
        </ul>
      )}

      <form
        onSubmit={(e) => void adicionar(e)}
        className="flex flex-col gap-3 border-t border-borda pt-4"
      >
        <h4 className="text-base font-semibold">Adicionar frase</h4>
        <div>
          <Label htmlFor="saud-ocasiao">Ocasião</Label>
          <Select
            id="saud-ocasiao"
            required
            value={ocasiaoNova || ocasioes[0] || ''}
            onChange={(e) => setOcasiaoNova(e.target.value)}
          >
            {opcoes}
          </Select>
        </div>
        <div>
          <Label htmlFor="saud-texto">Texto da frase</Label>
          <Input
            id="saud-texto"
            name="texto"
            required
            maxLength={80}
            aria-describedby="saud-dica"
          />
          <p id="saud-dica" className="mt-1 text-sm text-texto-2">
            Use {'{nome}'} onde o nome da pessoa deve aparecer.
          </p>
        </div>
        <Button
          type="submit"
          carregando={clicado === 'adicionar'}
          disabled={envio.ocupado || ocasioes.length === 0}
        >
          {clicado === 'adicionar' ? 'Adicionando...' : 'Adicionar frase'}
        </Button>
      </form>

      <form
        onSubmit={(e) => void gerar(e)}
        className="flex flex-col gap-3 border-t border-borda pt-4"
      >
        <h4 className="text-base font-semibold">Gerar com IA</h4>
        <div>
          <Label htmlFor="saud-gerar-ocasiao">Ocasião</Label>
          <Select
            id="saud-gerar-ocasiao"
            required
            value={ocasiaoGerar || ocasioes[0] || ''}
            onChange={(e) => setOcasiaoGerar(e.target.value)}
          >
            {opcoes}
          </Select>
        </div>
        <div>
          <Label htmlFor="saud-quantidade">Quantidade</Label>
          <Input
            id="saud-quantidade"
            name="quantidade"
            type="number"
            inputMode="numeric"
            required
            min={1}
            max={10}
            defaultValue={3}
          />
        </div>
        <Button
          type="submit"
          variante="contorno"
          carregando={clicado === 'gerar'}
          disabled={envio.ocupado || ocasioes.length === 0}
        >
          {clicado === 'gerar' ? 'Gerando...' : 'Gerar com IA'}
        </Button>
      </form>
    </div>
  );
}
