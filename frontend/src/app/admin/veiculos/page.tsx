'use client';

import * as React from 'react';
import { Pencil, Plus, Printer, RefreshCw } from 'lucide-react';
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

function Conteudo() {
  const lista = useCarga(() => admin.veiculos());
  const acao = useEnvio();
  const [form, setForm] = React.useState<{ aberto: boolean; alvo: VeiculoAdmin | null }>({
    aberto: false,
    alvo: null,
  });

  async function alternar(v: VeiculoAdmin) {
    // A ação disponível (ativar/desativar) vem de acoes_permitidas.
    const nome = v.acoes_permitidas.includes('desativar') ? 'desativar' : 'ativar';
    await acao.executar(() => admin.acaoVeiculo(v.id, nome));
    await lista.recarregar();
  }

  return (
    <>
      {lista.erro ? <Alerta className="mb-4">{lista.erro}</Alerta> : null}
      {acao.erro ? <Alerta className="mb-4">{acao.erro}</Alerta> : null}

      {lista.carregando ? (
        <p role="status" className="flex items-center gap-2 text-texto-2">
          <Spinner />
          Carregando...
        </p>
      ) : lista.dados?.itens.length === 0 ? (
        <p className="text-texto-2">Nenhum veículo cadastrado.</p>
      ) : (
        <ul className="lista-animada flex flex-col gap-3">
          {lista.dados?.itens.map((v) => {
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
                      onChange={() => void alternar(v)}
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
    <AdminShell titulo="Veículos">
      <Conteudo />
    </AdminShell>
  );
}
