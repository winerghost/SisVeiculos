'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { CalendarDays } from 'lucide-react';
import { api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useCarga } from '@/lib/use-carga';
import { diaDoCalendario, montarHoraSaida, rotuloHoraSaida, sugestaoLocal } from '@/lib/formato';
import { AppShell } from '@/components/app-shell';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerTitle,
} from '@/components/ui/drawer';
import { Input, Select, Textarea, campoBase } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';

const HORAS = Array.from({ length: 24 }, (_, i) => i);
const MINUTOS = Array.from({ length: 60 }, (_, i) => i);
const dois = (n: number) => String(n).padStart(2, '0');

function Formulario() {
  const { eu, tratarErro } = useAuth();
  const router = useRouter();
  const veiculos = useCarga(() => api.veiculos());
  const [nome, setNome] = React.useState(eu?.nome ?? '');
  const [unidade, setUnidade] = React.useState(eu?.unidade ?? '');
  const [veiculoId, setVeiculoId] = React.useState('');
  const [trajeto, setTrajeto] = React.useState('');
  const [observacao, setObservacao] = React.useState('');
  const [erro, setErro] = React.useState<string | null>(null);
  const [enviando, setEnviando] = React.useState(false);

  // Hora de saída: valor confirmado e rascunho dentro do Drawer.
  const [aberto, setAberto] = React.useState(false);
  const [dia, setDia] = React.useState<Date | undefined>();
  const [hora, setHora] = React.useState(8);
  const [minuto, setMinuto] = React.useState(0);
  const [rDia, setRDia] = React.useState<Date | undefined>();
  const [rHora, setRHora] = React.useState(8);
  const [rMinuto, setRMinuto] = React.useState(0);
  const [abrindo, setAbrindo] = React.useState(false);

  async function abrir() {
    if (dia) {
      // Já confirmado: reabre com o valor escolhido (o servidor revalida no envio).
      setRDia(dia);
      setRHora(hora);
      setRMinuto(minuto);
      setAberto(true);
      return;
    }
    // Primeira abertura: começa na sugestão da API (agora do servidor, arredondado para cima).
    // Se a consulta falhar, usa o relógio do aparelho só como sugestão.
    setAbrindo(true);
    let s = sugestaoLocal();
    try {
      const ref = await api.horarioReferencia();
      const d = diaDoCalendario(ref.sugestao.data);
      if (d) s = { data: d, hora: ref.sugestao.hora, minuto: ref.sugestao.minuto };
    } catch {
      /* mantém a sugestão local */
    }
    setRDia(s.data);
    setRHora(s.hora);
    setRMinuto(s.minuto);
    setAbrindo(false);
    setAberto(true);
  }

  function confirmar() {
    setDia(rDia);
    setHora(rHora);
    setMinuto(rMinuto);
    setAberto(false);
  }

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    setEnviando(true);
    setErro(null);
    try {
      const criada = await api.criar({
        nome_solicitante: nome,
        unidade,
        // Sem data escolhida segue vazio: o servidor responde que o campo é obrigatório.
        hora_saida: dia ? montarHoraSaida(dia, hora, minuto) : '',
        trajeto,
        ...(observacao ? { observacao } : {}),
        veiculo_id: veiculoId,
      });
      router.replace(`/requisicoes/detalhe?id=${encodeURIComponent(criada.id)}`);
    } catch (err) {
      setErro(tratarErro(err));
      void veiculos.recarregar(); // a disponibilidade pode ter mudado
      setEnviando(false);
    }
  }

  return (
    <form onSubmit={(e) => void enviar(e)} className="flex flex-col gap-4">
      <p className="text-texto-2">
        Solicite o uso do veículo. Um autorizador vai analisar o pedido.
      </p>
      {erro ? <Alerta titulo="Não foi possível enviar">{erro}</Alerta> : null}
      {veiculos.erro ? <Alerta>{veiculos.erro}</Alerta> : null}

      <div>
        <Label htmlFor="nome">Nome do solicitante</Label>
        <Input
          id="nome"
          required
          autoComplete="name"
          value={nome}
          onChange={(e) => setNome(e.target.value)}
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label htmlFor="unidade">Unidade</Label>
          <Select
            id="unidade"
            required
            value={unidade}
            onChange={(e) => setUnidade(e.target.value)}
          >
            {(eu?.unidades ?? []).map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="veiculo">Veículo</Label>
          <Select
            id="veiculo"
            required
            value={veiculoId}
            onChange={(e) => setVeiculoId(e.target.value)}
          >
            <option value="">Selecione</option>
            {veiculos.dados?.map((v) => (
              // Disponibilidade vem da API (acoes_permitidas); o cliente não calcula.
              <option key={v.id} value={v.id} disabled={!v.acoes_permitidas.includes('solicitar')}>
                {v.descricao}
                {v.placa ? ` (${v.placa})` : ''}
                {v.motivo_indisponibilidade ? ` - ${v.motivo_indisponibilidade}` : ''}
              </option>
            ))}
          </Select>
        </div>
      </div>

      <div>
        <Label id="rotulo-hora-saida">Hora de saída</Label>
        <button
          type="button"
          onClick={() => void abrir()}
          disabled={abrindo}
          aria-haspopup="dialog"
          aria-labelledby="rotulo-hora-saida valor-hora-saida"
          className={cn(campoBase, 'flex h-12 items-center gap-3 text-left')}
        >
          <CalendarDays aria-hidden className="size-5 text-texto-2" />
          <span id="valor-hora-saida" className={dia ? '' : 'text-texto-2'}>
            {dia ? rotuloHoraSaida(dia, hora, minuto) : 'Escolha o dia e o horário'}
          </span>
        </button>
      </div>

      <div>
        <Label htmlFor="trajeto">Trajeto</Label>
        <Textarea
          id="trajeto"
          required
          value={trajeto}
          onChange={(e) => setTrajeto(e.target.value)}
          className="focus-visible:border-foco"
        />
      </div>

      <div>
        <Label htmlFor="observacao">
          Observação <span className="font-normal text-texto-2">(opcional)</span>
        </Label>
        <Textarea
          id="observacao"
          value={observacao}
          onChange={(e) => setObservacao(e.target.value)}
        />
      </div>

      <Button type="submit" tamanho="cheio" disabled={enviando} className="mt-4">
        Enviar requisição
      </Button>

      <Drawer open={aberto} onOpenChange={setAberto}>
        <DrawerContent>
          <DrawerTitle className="text-xl font-semibold">Hora de saída</DrawerTitle>
          <DrawerDescription className="mb-3 text-texto-2">
            Escolha o dia e o horário.
          </DrawerDescription>
          <Calendar mode="single" selected={rDia} onSelect={setRDia} defaultMonth={rDia} />
          <div className="mt-4 grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="hora">Hora</Label>
              <Select id="hora" value={rHora} onChange={(e) => setRHora(Number(e.target.value))}>
                {HORAS.map((h) => (
                  <option key={h} value={h}>
                    {dois(h)}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="minuto">Minuto</Label>
              <Select
                id="minuto"
                value={rMinuto}
                onChange={(e) => setRMinuto(Number(e.target.value))}
              >
                {MINUTOS.map((m) => (
                  <option key={m} value={m}>
                    {dois(m)}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <DrawerClose asChild>
              <Button type="button" variante="contorno" tamanho="cheio">
                Cancelar
              </Button>
            </DrawerClose>
            <Button type="button" tamanho="cheio" onClick={confirmar}>
              Confirmar
            </Button>
          </div>
        </DrawerContent>
      </Drawer>
    </form>
  );
}

export default function NovaRequisicao() {
  return (
    <AppShell titulo="Nova requisição" voltarPara="/requisicoes">
      <Formulario />
    </AppShell>
  );
}
