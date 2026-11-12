'use client';

import * as React from 'react';
import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { Download, Eye, Info, Mail, Share2 } from 'lucide-react';
import { api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { FORMATOS, paraCorpo, paraQuery, salvarArquivo, valoresEfetivos } from '@/lib/relatorios';
import type { EnvioRelatorio, FormatoRelatorio, PreviaRelatorio } from '@/lib/tipos';
import { useCarga } from '@/lib/use-carga';
import { useEnvio } from '@/lib/use-envio';
import { AppShell } from '@/components/app-shell';
import { AcompanharEnvio, DrawerEnvio } from '@/components/relatorios/envio';
import { ColunasRelatorio } from '@/components/relatorios/colunas';
import { FiltrosRelatorio } from '@/components/relatorios/filtros';
import { Previa } from '@/components/relatorios/previa';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Select } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

/** Pré-preenchimento vindo de "Exportar este painel": ?tipo=...&<filtro>=<valor>. */
function lerQuery(p: URLSearchParams) {
  const valores: Record<string, string> = {};
  p.forEach((v, k) => {
    if (k !== 'tipo') valores[k] = v;
  });
  return { tipo: p.get('tipo'), valores };
}

function Conteudo() {
  const cat = useCarga(() => api.relatorios());
  const params = useSearchParams();
  const [inicial] = React.useState(() => lerQuery(params));
  const [escolhido, setEscolhido] = React.useState<string | null>(inicial.tipo);
  const [valores, setValores] = React.useState<Record<string, string>>(inicial.valores);
  const [colunas, setColunas] = React.useState<string[] | null>(null);
  const [previa, setPrevia] = React.useState<PreviaRelatorio | null>(null);
  const [mensagem, setMensagem] = React.useState<string | null>(null);
  const [envioEmail, setEnvioEmail] = React.useState<EnvioRelatorio | null>(null);
  const [drawer, setDrawer] = React.useState<'email' | 'compartilhar'>('email');
  const [drawerAberto, setDrawerAberto] = React.useState(false);
  const acao = useEnvio();

  const dados = cat.dados;
  const relatorio =
    dados?.relatorios.find((r) => r.tipo === escolhido) ?? dados?.relatorios[0] ?? null;

  if (cat.carregando) {
    return (
      <p role="status" className="text-texto-2">
        Carregando...
      </p>
    );
  }
  if (cat.erro || !dados || !relatorio) {
    return <Alerta>{cat.erro ?? 'Nenhum relatório disponível.'}</Alerta>;
  }

  const filtros = relatorio.filtros;
  const efetivos = valoresEfetivos(filtros, valores);
  const consulta = paraQuery(efetivos, colunas);
  const disponiveis = relatorio.colunas_disponiveis ?? [];
  const { canais } = dados;

  const limpar = () => {
    setPrevia(null);
    setMensagem(null);
    acao.setErro(null);
  };

  async function visualizar() {
    limpar();
    const r = await acao.executar(() => api.previaRelatorio(relatorio!.tipo, consulta));
    if (r.ok) setPrevia(r.valor);
  }

  async function baixar(formato: FormatoRelatorio) {
    limpar();
    const r = await acao.executar(() =>
      api.arquivoRelatorio(relatorio!.tipo, formato, 'download', consulta),
    );
    if (r.ok) {
      salvarArquivo(r.valor.blob, r.valor.nome);
      setMensagem(`Arquivo "${r.valor.nome}" baixado.`);
    }
  }

  async function compartilhar(formato: FormatoRelatorio) {
    limpar();
    const mime = FORMATOS.find((f) => f.valor === formato)?.mime ?? '';
    // Feature detection: sem suporte a arquivos, nem gera o relatório.
    const suporta =
      typeof navigator.share === 'function' &&
      typeof navigator.canShare === 'function' &&
      navigator.canShare({ files: [new File([''], `relatorio.${formato}`, { type: mime })] });
    if (!suporta) {
      setMensagem('Este aparelho não permite compartilhar arquivos; use Baixar.');
      return;
    }
    const r = await acao.executar(() =>
      api.arquivoRelatorio(relatorio!.tipo, formato, 'compartilhar', consulta),
    );
    if (!r.ok) return;
    const arquivo = new File([r.valor.blob], r.valor.nome, { type: r.valor.blob.type || mime });
    try {
      await navigator.share({ files: [arquivo], title: relatorio!.rotulo });
      setMensagem('Relatório compartilhado.');
    } catch (e) {
      // Cancelar a janela de compartilhamento não é erro.
      if (!(e instanceof DOMException && e.name === 'AbortError')) {
        acao.setErro('Não foi possível compartilhar o arquivo. Use Baixar.');
      }
    }
  }

  async function enviarEmail(formato: FormatoRelatorio, destinatarioId: string) {
    limpar();
    setEnvioEmail(null);
    const r = await acao.executar(() =>
      api.enviarRelatorioEmail(relatorio!.tipo, {
        formato,
        filtros: paraCorpo(filtros, efetivos, colunas),
        destinatario_id: destinatarioId,
      }),
    );
    if (r.ok) setEnvioEmail(r.valor);
  }

  return (
    <div aria-busy={acao.ocupado}>
      <div>
        <Label htmlFor="rel-tipo">Relatório</Label>
        <Select
          id="rel-tipo"
          value={relatorio.tipo}
          onChange={(e) => {
            setEscolhido(e.target.value);
            setValores({});
            setColunas(null);
            setEnvioEmail(null);
            limpar();
          }}
        >
          {dados.relatorios.map((r) => (
            <option key={r.tipo} value={r.tipo}>
              {r.rotulo}
            </option>
          ))}
        </Select>
        <p className="mt-2 text-texto-2">{relatorio.descricao}</p>
      </div>

      <div className="mt-4">
        <FiltrosRelatorio
          key={relatorio.tipo}
          filtros={filtros}
          valores={efetivos}
          onAplicar={(v) => {
            setValores(v);
            limpar();
          }}
          onLimpar={() => {
            setValores({});
            limpar();
          }}
        />
      </div>

      {disponiveis.length > 0 ? (
        <div className="mt-4">
          <ColunasRelatorio
            key={relatorio.tipo}
            disponiveis={disponiveis}
            filtros={filtros}
            valores={efetivos}
            selecao={colunas}
            onAplicar={(c) => {
              setColunas(c);
              limpar();
            }}
            onRestaurar={() => {
              setColunas(null);
              limpar();
            }}
          />
        </div>
      ) : null}

      <div className="mt-4 flex flex-col gap-3">
        <Button
          type="button"
          tamanho="cheio"
          disabled={acao.ocupado}
          onClick={() => void visualizar()}
        >
          <Eye aria-hidden className="size-5" /> Visualizar
        </Button>
        <div className="grid grid-cols-2 gap-3">
          {canais.download
            ? FORMATOS.map((f) => (
                <Button
                  key={f.valor}
                  type="button"
                  variante="contorno"
                  disabled={acao.ocupado}
                  onClick={() => void baixar(f.valor)}
                >
                  <Download aria-hidden className="size-5" /> Baixar {f.rotulo}
                </Button>
              ))
            : null}
          {canais.email ? (
            <Button
              type="button"
              variante="contorno"
              disabled={acao.ocupado}
              onClick={() => {
                setDrawer('email');
                setDrawerAberto(true);
              }}
            >
              <Mail aria-hidden className="size-5" /> Enviar por e-mail
            </Button>
          ) : null}
          {canais.whatsapp ? (
            <Button
              type="button"
              variante="contorno"
              disabled={acao.ocupado}
              onClick={() => {
                setDrawer('compartilhar');
                setDrawerAberto(true);
              }}
            >
              <Share2 aria-hidden className="size-5" /> Compartilhar
            </Button>
          ) : null}
        </div>
      </div>

      <div aria-live="polite" className="mt-4">
        {acao.ocupado ? <p className="text-texto-2">Processando...</p> : null}
        {mensagem ? (
          <p className="flex gap-3 rounded-xl bg-[#e8ecf2] p-3 text-texto-2">
            <Info aria-hidden className="mt-0.5 size-5 shrink-0" />
            {mensagem}
          </p>
        ) : null}
      </div>
      {acao.erro ? <Alerta className="mt-4">{acao.erro}</Alerta> : null}
      {envioEmail ? (
        <AcompanharEnvio key={envioEmail.id} id={envioEmail.id} inicial={envioEmail} />
      ) : null}
      {previa ? <Previa previa={previa} filtros={filtros} consulta={paraQuery(efetivos)} /> : null}

      <DrawerEnvio
        modo={drawer}
        aberto={drawerAberto}
        onAbertoChange={setDrawerAberto}
        ocupado={acao.ocupado}
        onConfirmar={(formato, destinatarioId) => {
          setDrawerAberto(false);
          if (drawer === 'email') void enviarEmail(formato, destinatarioId);
          else void compartilhar(formato);
        }}
      />
    </div>
  );
}

export default function Relatorios() {
  const { eu } = useAuth();
  return (
    <AppShell titulo="Relatórios" sobretitulo={eu?.rotulo_perfil}>
      <Suspense fallback={null}>
        <Conteudo />
      </Suspense>
    </AppShell>
  );
}
