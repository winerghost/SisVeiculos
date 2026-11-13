import { cn } from '@/lib/utils';
import type { SituacaoHorario } from '@/lib/tipos';

// Cores no padrão dos chips de status (docs/mockups/README.md): fundo claro e texto escuro,
// contraste acima de 4,5:1. A situação vem calculada pela API; aqui só vira cor e texto.
const ESTILO: Record<SituacaoHorario, { classe: string; rotulo: string; visivel: string | null }> =
  {
    no_prazo: { classe: 'bg-[#d9f2e6] text-[#0a5a3a]', rotulo: 'No prazo', visivel: null },
    proximo: {
      classe: 'bg-[#fff1d6] text-[#7a4a00]',
      rotulo: 'Saída em breve',
      visivel: 'em breve',
    },
    atrasado: {
      classe: 'bg-[#fde2e0] text-[#8a1f17]',
      rotulo: 'Horário vencido',
      visivel: 'vencido',
    },
  };

/**
 * Data e hora de saída prevista com a cor da situação informada pela API. Sem situação (null),
 * mostra o texto puro. A cor nunca é o único sinal: o estado também vai em texto.
 */
export function HoraSaida({
  texto,
  situacao,
  className,
}: {
  texto: string;
  situacao?: SituacaoHorario | null;
  className?: string;
}) {
  const e = situacao ? ESTILO[situacao] : undefined;
  if (!e) return <>{texto}</>;
  return (
    <span
      data-situacao-horario={situacao}
      className={cn('inline-block rounded-padrao px-2 py-0.5 font-semibold', e.classe, className)}
    >
      {texto}
      {e.visivel ? (
        <span className="font-medium"> · {e.visivel}</span>
      ) : (
        <span className="sr-only"> ({e.rotulo})</span>
      )}
    </span>
  );
}
