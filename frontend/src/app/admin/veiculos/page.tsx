'use client';

import * as React from 'react';
import { ChevronDown, Pencil, Plus, Printer, RefreshCw } from 'lucide-react';
import { admin } from '@/lib/api';
import { useCarga } from '@/lib/use-carga';
import { useEnvio } from '@/lib/use-envio';
import type { VeiculoAdmin } from '@/lib/tipos';
import { AdminShell } from '@/components/admin-shell';
import { Alerta } from '@/components/ui/alert';
import { Spinner } from '@/components/ui/spinner';
import { Button } from '@/components/ui/button';
import { Chip } from '@/components/ui/chip';
import { Dialogo } from '@/components/ui/dialogo';
import { QrVeiculo, imprimirQr } from '@/components/qr-veiculo';
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerTitle,
} from '@/components/ui/drawer';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';
import { BarraFiltros, FILTRO_VAZIO, type ValoresFiltro } from '@/components/tabela/barra-filtros';
import {
  TabelaDados,
  ajudanteColunas,
  type ColunaTabela,
  type SortingState,
} from '@/components/tabela/tabela-dados';
import { useTelaMedia } from '@/components/tabela/use-tela-media';

// A API devolve a lista inteira: busca, ordenação e paginação aqui são só apresentação.
const ORDENACOES = [
  { valor: 'descricao', rotulo: 'Descrição (A–Z)' },
  { valor: '-descricao', rotulo: 'Descrição (Z–A)' },
  { valor: 'placa', rotulo: 'Placa (A–Z)' },
  { valor: '-placa', rotulo: 'Placa (Z–A)' },
];
const normalizar = (t: string) =>
  t
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase();
const comparador = new Intl.Collator('pt-BR', { numeric: true, sensitivity: 'base' });

function organizarLista(itens: VeiculoAdmin[], q: string, ordenar: string): VeiculoAdmin[] {
  const termo = normalizar(q.trim());
  const filtrados = termo
    ? itens.filter((v) => normalizar(`${v.descricao} ${v.placa ?? ''}`).includes(termo))
    : itens;
  const valor = ordenar || 'descricao';
  const desc = valor.startsWith('-');
  const campo = (desc ? valor.slice(1) : valor) as 'descricao' | 'placa';
  return [...filtrados].sort((a, b) => {
    const r = comparador.compare(a[campo] ?? '', b[campo] ?? '');
    return desc ? -r : r;
  });
}

/** Gaveta de criação/edição. `alvo` null = criar. Erros da API aparecem na própria gaveta. */
function FormularioVeiculo({
  alvo,
  aoFechar,
  aoSalvar,
}: {
  alvo: VeiculoAdmin | null;
  aoFechar: () => void;
  aoSalvar: () => Promise<void>;
}) {
  const envio = useEnvio();
  const [descricao, setDescricao] = React.useState(alvo?.descricao ?? '');
  const [placa, setPlaca] = React.useState(alvo?.placa ?? '');

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    if (envio.ocupado) return;
    const dados = { descricao, placa };
    const r = await envio.executar(() =>
      alvo ? admin.editarVeiculo(alvo.id, dados) : admin.criarVeiculo(dados),
    );
    if (r.ok) {
      await aoSalvar();
      aoFechar();
    }
  }

  return (
    <Drawer open onOpenChange={(o) => (o ? undefined : aoFechar())}>
      <DrawerContent>
        <DrawerTitle className="text-xl font-semibold">
          {alvo ? 'Editar veículo' : 'Adicionar veículo'}
        </DrawerTitle>
        <DrawerDescription className="mb-3 text-texto-2">
          Descrição e placa do veículo.
        </DrawerDescription>
        <form onSubmit={(e) => void enviar(e)} className="flex flex-col gap-4">
          {envio.erro ? <Alerta>{envio.erro}</Alerta> : null}
          <div>
            <Label htmlFor="descricao">Descrição</Label>
            <Input
              id="descricao"
              required
              value={descricao}
              onChange={(e) => setDescricao(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="placa">Placa</Label>
            <Input
              id="placa"
              autoCapitalize="characters"
              value={placa}
              onChange={(e) => setPlaca(e.target.value)}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <DrawerClose asChild>
              <Button type="button" variante="contorno" tamanho="cheio">
                Cancelar
              </Button>
            </DrawerClose>
            <Button type="submit" tamanho="cheio" carregando={envio.ocupado}>
              {envio.ocupado ? 'Salvando...' : 'Salvar'}
            </Button>
          </div>
        </form>
      </DrawerContent>
    </Drawer>
  );
}

/** QR do veículo (só se a API devolveu o código), com Imprimir e Rotacionar código. */
function BlocoQr({ v, aoRotacionar }: { v: VeiculoAdmin; aoRotacionar: () => Promise<void> }) {
  const envio = useEnvio();
  const [confirmando, setConfirmando] = React.useState(false);
  const [bloqueada, setBloqueada] = React.useState(false);
  if (!v.codigo_publico) return null;
  const codigo = v.codigo_publico;
  // Rotacionar só aparece se a API listar a ação.
  const podeRotacionar = v.acoes_permitidas.includes('rotacionar_codigo');

  async function rotacionar() {
    const r = await envio.executar(() => admin.rotacionarCodigoVeiculo(v.id));
    if (r.ok) {
      setConfirmando(false);
      await aoRotacionar();
    }
  }

  return (
    <div className="mt-3 flex flex-col gap-3 border-t border-borda pt-3">
      <div className="flex items-center gap-4">
        <QrVeiculo
          codigoPublico={codigo}
          descricao={v.descricao}
          className="size-32 shrink-0 rounded-padrao border border-borda"
        />
        <p className="text-sm text-texto-2">
          QR Code do veículo. Ler o código não autoriza nada; imprima e fixe no veículo.
        </p>
      </div>
      {bloqueada ? (
        <Alerta>
          O navegador bloqueou a janela de impressão. Permita pop-ups e tente de novo.
        </Alerta>
      ) : null}
      {envio.erro && !confirmando ? <Alerta>{envio.erro}</Alerta> : null}
      <div className="flex flex-wrap gap-3">
        <Button
          variante="contorno"
          onClick={() => setBloqueada(!imprimirQr(codigo, v.descricao, v.placa))}
          aria-label={`Imprimir QR Code de ${v.descricao}`}
        >
          <Printer aria-hidden className="size-4" /> Imprimir
        </Button>
        {podeRotacionar ? (
          <Button
            variante="contorno"
            onClick={() => {
              envio.setErro(null);
              setConfirmando(true);
            }}
            aria-label={`Rotacionar código de ${v.descricao}`}
          >
            <RefreshCw aria-hidden className="size-4" /> Rotacionar código
          </Button>
        ) : null}
      </div>
      <Dialogo
        aberto={confirmando}
        titulo="Rotacionar código?"
        descricao="O QR Code impresso atual deixará de funcionar e será preciso imprimir e fixar o novo."
        onFechar={() => setConfirmando(false)}
      >
        {envio.erro ? <Alerta>{envio.erro}</Alerta> : null}
        <div className="grid grid-cols-2 gap-3">
          <Button
            data-autofocus
            variante="contorno"
            disabled={envio.ocupado}
            onClick={() => setConfirmando(false)}
          >
            Voltar
          </Button>
          <Button carregando={envio.ocupado} onClick={() => void rotacionar()}>
            {envio.ocupado ? 'Rotacionando...' : 'Rotacionar'}
          </Button>
        </div>
      </Dialogo>
    </div>
  );
}

/** Tabela (a partir de md): mesmas ações do cartão, todas vindas de acoes_permitidas. */
function TabelaVeiculos({
  itens,
  ordenar,
  aoOrdenar,
  ocupado,
  aoAlternar,
  aoEditar,
  aoRotacionar,
}: {
  itens: VeiculoAdmin[];
  ordenar: string;
  aoOrdenar: (o: string) => void;
  ocupado: boolean;
  aoAlternar: (v: VeiculoAdmin) => void;
  aoEditar: (v: VeiculoAdmin) => void;
  aoRotacionar: () => Promise<void>;
}) {
  const [pagina, setPagina] = React.useState({ pageIndex: 0, pageSize: 20 });
  const [qrAberto, setQrAberto] = React.useState<string | null>(null);
  // Busca ou ordenação mudou: volta para a primeira página.
  const assinatura = `${ordenar}|${itens.map((v) => v.id).join(',')}`;
  const [ultima, setUltima] = React.useState(assinatura);
  if (ultima !== assinatura) {
    setUltima(assinatura);
    if (pagina.pageIndex !== 0) setPagina((p) => ({ ...p, pageIndex: 0 }));
  }
  const valor = ordenar || 'descricao';
  const ordenacao: SortingState = [{ id: valor.replace(/^-/, ''), desc: valor.startsWith('-') }];
  const colunas = React.useMemo<ColunaTabela<VeiculoAdmin>[]>(() => {
    const h = ajudanteColunas<VeiculoAdmin>();
    return [
      h.accessor('descricao', {
        header: 'Descrição',
        cell: (c) => <span className="font-semibold break-words">{c.getValue()}</span>,
      }),
      h.accessor((v) => v.placa ?? '', {
        id: 'placa',
        header: 'Placa',
        cell: (c) => c.getValue() || <span className="text-texto-2">sem placa</span>,
      }),
      h.display({
        id: 'situacao',
        header: 'Situação',
        cell: ({ row: { original: v } }) =>
          !v.ativo ? (
            <Chip tom="perigo">Inativo</Chip>
          ) : v.requisicao_aberta ? (
            <Chip tom="sucesso">Requisição aberta</Chip>
          ) : (
            <Chip tom="info">Livre</Chip>
          ),
      }),
      h.display({
        id: 'disponivel',
        header: 'Disponível para agendamento',
        cell: ({ row: { original: v } }) => {
          const podeAlternar =
            v.acoes_permitidas.includes('ativar') || v.acoes_permitidas.includes('desativar');
          return podeAlternar ? (
            <Switch
              checked={v.ativo}
              disabled={ocupado}
              onChange={() => aoAlternar(v)}
              aria-label={`Disponível para agendamento: ${v.descricao}`}
            />
          ) : (
            <span>{v.ativo ? 'Sim' : 'Não'}</span>
          );
        },
      }),
      h.display({
        id: 'acoes',
        header: () => <span className="sr-only">Ações</span>,
        cell: ({ row: { original: v } }) => (
          <div className="flex flex-wrap justify-end gap-2">
            {v.acoes_permitidas.includes('editar') ? (
              <Button
                variante="contorno"
                className="h-11"
                onClick={() => aoEditar(v)}
                aria-label={`Editar ${v.descricao}`}
              >
                <Pencil aria-hidden className="size-4" /> Editar
              </Button>
            ) : null}
            {v.codigo_publico ? (
              <Button
                variante="contorno"
                className="h-11"
                aria-expanded={qrAberto === v.id}
                aria-controls={qrAberto === v.id ? `qr-${v.id}` : undefined}
                onClick={() => setQrAberto(qrAberto === v.id ? null : v.id)}
              >
                QR Code<span className="sr-only"> de {v.descricao}</span>
                <ChevronDown
                  aria-hidden
                  className={cn('size-4 transition-transform', qrAberto === v.id && 'rotate-180')}
                />
              </Button>
            ) : null}
          </div>
        ),
      }),
    ];
  }, [ocupado, aoAlternar, aoEditar, qrAberto]);

  return (
    <TabelaDados
      legenda="Veículos cadastrados"
      colunas={colunas}
      dados={itens}
      idDaLinha={(v) => v.id}
      ordenacao={ordenacao}
      aoOrdenar={(s) => {
        const p = s[0];
        aoOrdenar(p ? `${p.desc ? '-' : ''}${p.id}` : '');
      }}
      ordenacaoManual
      paginacao={pagina}
      aoPaginar={setPagina}
      aoMudarTamanho={(t) => setPagina({ pageIndex: 0, pageSize: t })}
      linhaExtra={(v) =>
        qrAberto === v.id && v.codigo_publico ? (
          <div id={`qr-${v.id}`} className="max-w-md">
            <BlocoQr v={v} aoRotacionar={aoRotacionar} />
          </div>
        ) : null
      }
    />
  );
}

function Conteudo() {
  const lista = useCarga(() => admin.veiculos(), '', { recursos: ['veiculos'] });
  const acao = useEnvio();
  const telaMedia = useTelaMedia();
  const [filtros, setFiltros] = React.useState<ValoresFiltro>(FILTRO_VAZIO);
  const [form, setForm] = React.useState<{ aberto: boolean; alvo: VeiculoAdmin | null }>({
    aberto: false,
    alvo: null,
  });
  const todos = lista.dados?.itens;
  const visiveis = React.useMemo(
    () => (todos ? organizarLista(todos, filtros.q, filtros.ordenar) : []),
    [todos, filtros.q, filtros.ordenar],
  );
  const editar = React.useCallback((v: VeiculoAdmin) => setForm({ aberto: true, alvo: v }), []);
  const { executar } = acao;
  const { recarregar } = lista;
  const alternar = React.useCallback(
    async (v: VeiculoAdmin) => {
      // A ação disponível (ativar/desativar) vem de acoes_permitidas.
      const nome = v.acoes_permitidas.includes('desativar') ? 'desativar' : 'ativar';
      await executar(() => admin.acaoVeiculo(v.id, nome));
      await recarregar();
    },
    [executar, recarregar],
  );
  const aoAlternar = React.useCallback((v: VeiculoAdmin) => void alternar(v), [alternar]);

  return (
    <>
      {lista.dados && lista.dados.itens.length > 0 ? (
        <BarraFiltros
          id="filtro-veiculos"
          valores={filtros}
          aoMudar={setFiltros}
          rotuloBusca="Buscar por descrição ou placa"
          ordenacoes={ORDENACOES}
          recolhivel={!telaMedia}
        />
      ) : null}
      {lista.erro ? <Alerta className="mb-4">{lista.erro}</Alerta> : null}
      {acao.erro ? <Alerta className="mb-4">{acao.erro}</Alerta> : null}

      {lista.carregando ? (
        <p role="status" className="flex items-center gap-2 text-texto-2">
          <Spinner />
          Carregando...
        </p>
      ) : lista.dados?.itens.length === 0 ? (
        <p className="text-texto-2">Nenhum veículo cadastrado.</p>
      ) : visiveis.length === 0 ? (
        <p className="text-texto-2">Nenhum veículo encontrado com essa busca.</p>
      ) : telaMedia ? (
        <TabelaVeiculos
          itens={visiveis}
          ordenar={filtros.ordenar}
          aoOrdenar={(o) => setFiltros((f) => ({ ...f, ordenar: o }))}
          ocupado={acao.ocupado}
          aoAlternar={aoAlternar}
          aoEditar={editar}
          aoRotacionar={lista.recarregar}
        />
      ) : (
        <ul className="lista-animada flex flex-col gap-3">
          {visiveis.map((v) => {
            const podeAlternar =
              v.acoes_permitidas.includes('ativar') || v.acoes_permitidas.includes('desativar');
            return (
              <li key={v.id} className="rounded-xl border border-borda bg-white p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="text-xl font-semibold break-words">{v.descricao}</h2>
                    <p className="text-texto-2">{v.placa ? `placa ${v.placa}` : 'sem placa'}</p>
                  </div>
                  {!v.ativo ? (
                    <Chip tom="perigo">Inativo</Chip>
                  ) : v.requisicao_aberta ? (
                    <Chip tom="sucesso">Requisição aberta</Chip>
                  ) : (
                    <Chip tom="info">Livre</Chip>
                  )}
                </div>
                <div className="mt-3 flex items-center justify-between gap-3 border-t border-borda pt-3">
                  <span id={`disp-${v.id}`} className="font-medium">
                    {v.ativo ? 'Disponível para agendamento' : 'Indisponível para agendamento'}
                  </span>
                  {podeAlternar ? (
                    <Switch
                      checked={v.ativo}
                      disabled={acao.ocupado}
                      onChange={() => aoAlternar(v)}
                      aria-labelledby={`disp-${v.id}`}
                    />
                  ) : null}
                </div>
                {v.acoes_permitidas.includes('editar') ? (
                  <Button
                    variante="contorno"
                    className="mt-3"
                    onClick={() => setForm({ aberto: true, alvo: v })}
                    aria-label={`Editar ${v.descricao}`}
                  >
                    <Pencil aria-hidden className="size-4" /> Editar
                  </Button>
                ) : null}
                <BlocoQr v={v} aoRotacionar={lista.recarregar} />
              </li>
            );
          })}
        </ul>
      )}

      {lista.dados?.acoes_permitidas.includes('criar') ? (
        <div className="mt-5">
          <Button tamanho="cheio" onClick={() => setForm({ aberto: true, alvo: null })}>
            <Plus aria-hidden /> Adicionar veículo
          </Button>
        </div>
      ) : null}

      {form.aberto ? (
        <FormularioVeiculo
          alvo={form.alvo}
          aoFechar={() => setForm({ aberto: false, alvo: null })}
          aoSalvar={lista.recarregar}
        />
      ) : null}
    </>
  );
}

export default function Veiculos() {
  return (
    <AdminShell titulo="Veículos" largo>
      <Conteudo />
    </AdminShell>
  );
}
