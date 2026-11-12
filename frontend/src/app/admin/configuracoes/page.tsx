'use client';

import * as React from 'react';
import { Info } from 'lucide-react';
import { admin } from '@/lib/api';
import { useCarga } from '@/lib/use-carga';
import { useEnvio } from '@/lib/use-envio';
import { AdminShell } from '@/components/admin-shell';
import { SecaoIA } from '@/components/secao-ia';
import { Alerta } from '@/components/ui/alert';
import { Switch } from '@/components/ui/switch';

function Conteudo() {
  const cfg = useCarga(() => admin.configuracoes());
  const envio = useEnvio();

  async function alternar(chave: string, habilitado: boolean) {
    // Qualquer regra (dependência entre recursos, MFA sem segundo fator) é do servidor:
    // o erro 409/403 é exibido como veio e a tela recarrega o estado real.
    const r = await envio.executar(() => admin.atualizarConfiguracoes({ [chave]: !habilitado }));
    if (r.ok) cfg.setDados(r.valor);
    else await cfg.recarregar();
  }

  // `exige` traz chaves; o rótulo vem do próprio item devolvido pela API (fallback: a chave).
  const rotuloDe = (chave: string) =>
    cfg.dados?.itens.find((x) => x.chave === chave)?.rotulo ?? chave;

  return (
    <>
      <p className="mb-4 text-texto-2">
        Recursos opcionais. Cada um só passa a valer para todos os usuários depois de habilitado
        aqui.
      </p>
      {cfg.erro ? <Alerta className="mb-4">{cfg.erro}</Alerta> : null}
      {envio.erro ? <Alerta className="mb-4">{envio.erro}</Alerta> : null}

      {cfg.carregando ? (
        <p role="status" className="text-texto-2">
          Carregando...
        </p>
      ) : (
        <ul className="lista-animada flex flex-col gap-3">
          {cfg.dados?.itens.map((i) => {
            const permitido =
              i.acoes_permitidas.includes('ligar') || i.acoes_permitidas.includes('desligar');
            return (
              <li
                key={i.chave}
                className="flex items-start justify-between gap-3 rounded-xl border border-borda bg-white p-4"
              >
                <div className="min-w-0">
                  <h2 id={`cfg-${i.chave}`} className="text-lg font-semibold">
                    {i.rotulo}
                  </h2>
                  <p className="text-texto-2">{i.descricao}</p>
                  {i.exige && i.exige.length > 0 ? (
                    <p className="mt-1 text-sm text-texto-2">
                      Exige: {i.exige.map((k) => rotuloDe(k)).join(', ')}
                    </p>
                  ) : null}
                  {i.motivo_indisponibilidade ? (
                    <p className="mt-2 rounded-padrao bg-[#fff1d6] px-3 py-1 text-[#7a4a00]">
                      {i.motivo_indisponibilidade}
                    </p>
                  ) : null}
                </div>
                <Switch
                  checked={i.habilitado}
                  disabled={!permitido || envio.ocupado}
                  onChange={() => void alternar(i.chave, i.habilitado)}
                  aria-labelledby={`cfg-${i.chave}`}
                />
              </li>
            );
          })}
        </ul>
      )}

      <p className="mt-4 flex gap-3 rounded-xl bg-[#e8ecf2] p-3 text-texto-2">
        <Info aria-hidden className="mt-0.5 size-5 shrink-0" />
        Desligar um recurso não apaga os dados já gravados. Toda alteração fica registrada na
        auditoria.
      </p>

      <SecaoIA />
    </>
  );
}

export default function Configuracoes() {
  return (
    <AdminShell titulo="Configurações">
      <Conteudo />
    </AdminShell>
  );
}
