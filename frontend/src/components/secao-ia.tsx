'use client';

import * as React from 'react';
import { admin } from '@/lib/api';
import type { CorpoIA, ResultadoTesteIA } from '@/lib/tipos';
import { useCarga } from '@/lib/use-carga';
import { useEnvio } from '@/lib/use-envio';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input, Select } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';

/**
 * Configurações de IA. Só exibe e envia a intenção do admin: o que pode ligar, quais modelos
 * existem e qualquer recusa (409/400/404) vêm da API e aparecem como vieram. As chaves de API
 * nunca chegam aqui (a API só informa se estão configuradas).
 */
export function SecaoIA() {
  const cfg = useCarga(() => admin.ia());
  const envio = useEnvio();
  const [teste, setTeste] = React.useState<ResultadoTesteIA | null>(null);

  async function testar(provedor: string) {
    setTeste(null);
    const r = await envio.executar(() => admin.testarIA(provedor));
    if (r.ok) setTeste(r.valor);
  }

  async function enviar(corpo: CorpoIA) {
    const r = await envio.executar(() => admin.atualizarIA(corpo));
    if (r.ok) cfg.setDados(r.valor);
    else await cfg.recarregar();
  }

  const d = cfg.dados;
  const editar = !!d?.acoes_permitidas.includes('editar');
  const ativo = d?.provedores.find((p) => p.chave === d.provedor);

  return (
    <section aria-labelledby="secao-ia" className="mt-8">
      <h2 id="secao-ia" className="text-xl font-semibold">
        Inteligência artificial
      </h2>
      <p className="mb-4 text-texto-2">
        Recursos opcionais de redação. A IA nunca decide autorização nem regras do sistema; sem ela,
        tudo segue funcionando com os textos padrão.
      </p>
      {cfg.erro ? <Alerta className="mb-4">{cfg.erro}</Alerta> : null}
      {envio.erro ? <Alerta className="mb-4">{envio.erro}</Alerta> : null}

      {cfg.carregando || !d ? (
        <p role="status" className="text-texto-2">
          Carregando...
        </p>
      ) : (
        <>
          {d.motivo_indisponibilidade ? (
            <p className="mb-4 rounded-padrao bg-[#fff1d6] px-3 py-2 text-[#7a4a00]">
              {d.motivo_indisponibilidade}
            </p>
          ) : null}

          <ul className="flex flex-col gap-3">
            {d.itens.map((i) => {
              const permitido =
                i.acoes_permitidas.includes('ligar') || i.acoes_permitidas.includes('desligar');
              return (
                <li
                  key={i.chave}
                  className="flex items-start justify-between gap-3 rounded-xl border border-borda bg-white p-4"
                >
                  <div className="min-w-0">
                    <h3 id={`ia-${i.chave}`} className="text-lg font-semibold">
                      {i.rotulo}
                    </h3>
                    <p className="text-texto-2">{i.descricao}</p>
                    {i.motivo_indisponibilidade && !d.motivo_indisponibilidade ? (
                      <p className="mt-2 rounded-padrao bg-[#fff1d6] px-3 py-1 text-[#7a4a00]">
                        {i.motivo_indisponibilidade}
                      </p>
                    ) : null}
                  </div>
                  <Switch
                    checked={i.habilitado}
                    disabled={!permitido || envio.ocupado}
                    onChange={() => void enviar({ [i.chave]: !i.habilitado })}
                    aria-labelledby={`ia-${i.chave}`}
                  />
                </li>
              );
            })}
          </ul>

          <div className="mt-4 flex flex-col gap-4 rounded-xl border border-borda bg-white p-4">
            <div>
              <Label htmlFor="ia-provedor">Provedor</Label>
              <Select
                id="ia-provedor"
                disabled={!editar || envio.ocupado}
                value={d.provedor ?? ''}
                onChange={(e) => void enviar({ provedor: e.target.value || null })}
              >
                <option value="">Nenhum</option>
                {d.provedores.map((p) => (
                  <option key={p.chave} value={p.chave} disabled={!p.chave_configurada}>
                    {p.rotulo}
                    {p.chave_configurada ? '' : ' (chave não configurada)'}
                    {p.circuito_aberto ? ' (indisponível no momento)' : ''}
                  </option>
                ))}
              </Select>
            </div>

            <div>
              <Label htmlFor="ia-modelo">Modelo</Label>
              <Select
                id="ia-modelo"
                disabled={!editar || envio.ocupado || !ativo}
                value={d.modelo ?? ''}
                onChange={(e) => void enviar({ modelo: e.target.value || null })}
              >
                {ativo?.modelos.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </Select>
            </div>

            <div>
              <Label htmlFor="ia-reserva">Provedor reserva (opcional)</Label>
              <Select
                id="ia-reserva"
                disabled={!editar || envio.ocupado}
                value={d.provedor_reserva ?? ''}
                onChange={(e) => void enviar({ provedor_reserva: e.target.value || null })}
              >
                <option value="">Nenhum</option>
                {d.provedores
                  .filter((p) => p.chave !== d.provedor)
                  .map((p) => (
                    <option key={p.chave} value={p.chave} disabled={!p.chave_configurada}>
                      {p.rotulo}
                      {p.chave_configurada ? '' : ' (chave não configurada)'}
                    </option>
                  ))}
              </Select>
              <p className="mt-1 text-texto-2">
                Usado quando o provedor ativo falha, antes de voltar ao texto padrão.
              </p>
            </div>

            <form
              className="flex flex-col gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                const bruto = String(new FormData(e.currentTarget).get('teto') ?? '').trim();
                void enviar({ teto_tokens_mes: bruto === '' ? null : Number(bruto) });
              }}
            >
              <Label htmlFor="ia-teto">Teto de tokens por mês (opcional)</Label>
              <Input
                key={d.teto_tokens_mes ?? 'sem-teto'}
                id="ia-teto"
                name="teto"
                type="number"
                inputMode="numeric"
                min={1}
                defaultValue={d.teto_tokens_mes ?? ''}
                disabled={!editar || envio.ocupado}
              />
              <p className="text-texto-2">
                Teto em vigor: {d.teto_efetivo_tokens_mes.toLocaleString('pt-BR')} tokens por mês
                (vale o menor entre este e o do servidor).
              </p>
              <Button type="submit" variante="contorno" disabled={!editar || envio.ocupado}>
                Salvar teto
              </Button>
            </form>
          </div>

          {d.testes_disponiveis.length > 0 ? (
            <div className="mt-4 flex flex-col gap-3 rounded-xl border border-borda bg-white p-4">
              <h3 className="text-lg font-semibold">Testar conexão</h3>
              <div className="flex flex-wrap gap-2">
                {d.testes_disponiveis.map((chave) => {
                  const rotulo = d.provedores.find((p) => p.chave === chave)?.rotulo ?? chave;
                  return (
                    <Button
                      key={chave}
                      type="button"
                      variante="contorno"
                      disabled={envio.ocupado}
                      onClick={() => void testar(chave)}
                    >
                      Testar {rotulo}
                    </Button>
                  );
                })}
              </div>
              <div role="status" aria-live="polite">
                {teste ? (
                  <div
                    className={`rounded-padrao px-3 py-2 ${
                      teste.ok ? 'bg-[#d9f2e6] text-[#0a5a3a]' : 'bg-[#fff1d6] text-[#7a4a00]'
                    }`}
                  >
                    <p className="font-semibold">{teste.mensagem}</p>
                    <p>Provedor: {teste.provedor}</p>
                    <p>Modelo: {teste.modelo}</p>
                    <p>Latência: {teste.latencia_ms === null ? '-' : `${teste.latencia_ms} ms`}</p>
                    <p>
                      Tokens: {teste.tokens_entrada} de entrada, {teste.tokens_saida} de saída
                    </p>
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
