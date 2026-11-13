'use client';

import * as React from 'react';
import { admin } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { Alerta } from '@/components/ui/alert';
import { dataHora } from '@/lib/formato';
import type { LocalizacaoRegistro } from '@/lib/tipos';
import { rotuloMomento } from '@/lib/checklist';

/** Link externo simples (sem mapa embutido, por causa da CSP). Coordenadas só na URL de destino. */
function urlMapa(lat: number, lon: number): string {
  return `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=17/${lat}/${lon}`;
}

/**
 * Coordenadas gravadas nos registros de saída e chegada. Quem monta a seção só o faz com
 * `ver_localizacao` em acoes_permitidas (só admin); sem registros nada aparece e os erros são
 * exibidos como vieram. `versao` muda quando a requisição avança e dispara nova leitura. Nada é
 * guardado no cliente.
 */
export function LocalizacaoRegistroSecao({
  requisicaoId,
  versao,
}: {
  requisicaoId: string;
  versao: string;
}) {
  const { tratarErro } = useAuth();
  const [itens, setItens] = React.useState<LocalizacaoRegistro[]>([]);
  const [erro, setErro] = React.useState<string | null>(null);
  React.useEffect(() => {
    let ativo = true;
    admin
      .localizacoes(requisicaoId)
      .then((r) => {
        if (!ativo) return;
        setItens(Array.isArray(r?.itens) ? r.itens : []);
        setErro(null);
      })
      .catch((e: unknown) => {
        if (!ativo) return;
        setItens([]);
        setErro(tratarErro(e));
      });
    return () => {
      ativo = false;
    };
  }, [requisicaoId, versao, tratarErro]);

  if (erro) return <Alerta className="mt-6">{erro}</Alerta>;
  if (itens.length === 0) return null;
  return (
    <>
      <h2 className="mt-6 mb-2 font-medium text-texto-2">Localização do registro</h2>
      <section
        aria-label="Localização do registro"
        className="rounded-xl border border-borda bg-white p-4"
      >
        <ul className="flex flex-col gap-4">
          {itens.map((l) => (
            <li key={`${l.momento}:${l.registrado_em}`}>
              <p className="font-semibold">Registro de {rotuloMomento(l.momento)}</p>
              <p className="text-texto-2">
                Coordenadas: {l.latitude}, {l.longitude}
              </p>
              {l.precisao_m !== null && l.precisao_m !== undefined ? (
                <p className="text-texto-2">Precisão: ±{Math.round(l.precisao_m)} m</p>
              ) : null}
              <p className="text-sm text-texto-2">
                Registrado em {dataHora(l.registrado_em)}
                {l.registrado_por_nome ? ` · por ${l.registrado_por_nome}` : ''}
              </p>
              <a
                href={urlMapa(l.latitude, l.longitude)}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-1 inline-flex min-h-11 items-center text-primaria underline underline-offset-4"
              >
                Abrir no mapa
              </a>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
