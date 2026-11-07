'use client';

import * as React from 'react';
import { api, ApiErro } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { rotuloCombustivel, rotuloMomento } from '@/lib/checklist';
import { dataHora } from '@/lib/formato';
import type { ChecklistRegistrado as Registro, MomentoChecklist } from '@/lib/tipos';
import { Alerta } from '@/components/ui/alert';

// A API recusa quem não pode ver (sem_permissao) ou o recurso desligado: nada é exibido.
const SILENCIOSOS = new Set(['sem_permissao', 'recurso_desligado', 'nao_encontrado']);

function Bloco({ c }: { c: Registro }) {
  return (
    <section
      aria-label={`Checklist de ${rotuloMomento(c.momento)}`}
      className="rounded-xl border border-borda bg-white p-4"
    >
      <h3 className="font-semibold">Checklist de {rotuloMomento(c.momento)}</h3>
      <p className="mt-1 text-texto-2">Combustível: {rotuloCombustivel(c.combustivel)}</p>
      <p className="text-texto-2">Avarias: {c.avarias ? 'Sim' : 'Não'}</p>
      {c.descricao_avarias ? (
        <p className="mt-1 break-words whitespace-pre-line">{c.descricao_avarias}</p>
      ) : null}
      {c.fotos.length > 0 ? (
        <ul className="mt-3 grid grid-cols-3 gap-2">
          {[...c.fotos]
            .sort((a, b) => a.posicao - b.posicao)
            .map((f) => (
              <li key={f.id}>
                {f.url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={f.url}
                    alt={`Foto ${f.posicao} do checklist de ${rotuloMomento(c.momento)}`}
                    loading="lazy"
                    className="aspect-square w-full rounded-padrao border border-borda object-cover"
                  />
                ) : (
                  <p className="text-sm text-texto-2">Foto indisponível</p>
                )}
              </li>
            ))}
        </ul>
      ) : null}
      <p className="mt-3 text-sm text-texto-2">
        Registrado em {dataHora(c.registrado_em)}
        {c.registrado_por_nome ? ` · por ${c.registrado_por_nome}` : ''}
      </p>
    </section>
  );
}

/**
 * Checklists já registrados (GET). As URLs assinadas expiram em ~300 s: ficam só em memória e são
 * buscadas de novo ao abrir ou quando `versao` muda. Exibe só se a API informar o recurso ligado.
 */
export function ChecklistRegistrado({
  requisicaoId,
  versao,
}: {
  requisicaoId: string;
  versao: string;
}) {
  const { eu, tratarErro } = useAuth();
  const ligado = eu?.recursos?.checklist === true;
  const [dados, setDados] = React.useState<Record<MomentoChecklist, Registro | null> | null>(null);
  const [erro, setErro] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!ligado) return;
    let ativo = true;
    api
      .checklists(requisicaoId)
      .then((r) => {
        if (!ativo) return;
        setDados(r.checklists);
        setErro(null);
      })
      .catch((e: unknown) => {
        if (!ativo) return;
        setDados(null);
        setErro(e instanceof ApiErro && SILENCIOSOS.has(e.codigo) ? null : tratarErro(e));
      });
    return () => {
      ativo = false;
    };
  }, [ligado, requisicaoId, versao, tratarErro]);

  if (!ligado) return null;
  const itens = [dados?.saida, dados?.chegada].filter((x): x is Registro => !!x);
  if (!erro && itens.length === 0) return null;
  return (
    <div className="mt-4 flex flex-col gap-3">
      {erro ? <Alerta>{erro}</Alerta> : null}
      {itens.map((c) => (
        <Bloco key={c.id} c={c} />
      ))}
    </div>
  );
}
