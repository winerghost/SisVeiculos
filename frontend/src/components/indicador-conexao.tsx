'use client';

import { CheckCircle2, CloudOff, Wifi, WifiOff } from 'lucide-react';
import { useAuth } from '@/lib/auth';
import { useConectividade, type EstadoConexao } from '@/lib/conectividade';
import { cn } from '@/lib/utils';

const TEXTOS: Record<EstadoConexao, string> = {
  conectado: 'Conectado',
  sem_internet: 'Sem conexão com a internet',
  servidor_indisponivel: 'Servidor indisponível',
  restabelecida: 'Conexão restabelecida',
};

function Icone({ estado }: { estado: EstadoConexao }) {
  if (estado === 'sem_internet') return <WifiOff aria-hidden className="size-4" />;
  if (estado === 'servidor_indisponivel') return <CloudOff aria-hidden className="size-4" />;
  if (estado === 'restabelecida') return <CheckCircle2 aria-hidden className="size-4" />;
  return <Wifi aria-hidden className="size-4" />;
}

function Indicador() {
  const { estado, verificarAgora } = useConectividade();
  const falha = estado === 'sem_internet' || estado === 'servidor_indisponivel';
  return (
    // Altura fixa: o texto muda sem deslocar o conteúdo abaixo.
    <div className="flex h-11 items-center justify-between gap-2 px-5">
      <p
        role="status"
        aria-live="polite"
        className={cn(
          'flex items-center gap-1.5 text-sm font-medium transition-colors',
          falha ? 'text-[#B3261E]' : estado === 'restabelecida' ? 'text-primaria' : 'text-texto-2',
        )}
      >
        <Icone estado={estado} />
        {TEXTOS[estado]}
      </p>
      {falha ? (
        <button
          type="button"
          onClick={verificarAgora}
          className="min-h-11 rounded-padrao px-3 text-sm font-semibold text-primaria underline"
        >
          Tentar agora
        </button>
      ) : null}
    </div>
  );
}

/** Só aparece se a API habilitou o recurso (eu.recursos.indicador_conectividade). */
export function IndicadorConexao() {
  const { eu } = useAuth();
  if (eu?.recursos.indicador_conectividade !== true) return null;
  return <Indicador />;
}
