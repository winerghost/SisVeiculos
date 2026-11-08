'use client';

import * as React from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import {
  dataHoraNumerica,
  decimal,
  duracao,
  duracaoEmRelacaoAoPrevisto,
  horaDe,
  numero,
  percentual,
  rotuloPeriodo,
} from '@/lib/formato';
import { useAcao } from '@/lib/use-acao';
import { useCarga } from '@/lib/use-carga';
import type {
  DadosGovernanca,
  DadosOperacao,
  DadosOpcionais,
  DadosTempos,
  DadosUnidades,
  DadosVeiculos,
  DadosVolume,
  EstatisticaTempo,
  FiltrosDashboard,
  MaisAntiga,
  PainelId,
  Veiculo,
} from '@/lib/tipos';
import { CartaoRequisicao } from '@/components/cartao-requisicao';
import { ExportarPainel } from '@/components/dashboards/exportar';
import { BarraFiltros } from '@/components/dashboards/filtros';
import {
  BarrasEmpilhadas,
  BarrasHorizontais,
  ComTabela,
  Kpi,
  TabelaSimples,
} from '@/components/dashboards/graficos';
import { Alerta } from '@/components/ui/alert';
import { rotuloRole } from '@/components/ui/chip';

const INTERVALO_MS = 30_000;

const Vazio = ({ children = 'Sem dados no período.' }: { children?: React.ReactNode }) => (
  <p className="rounded-xl border border-borda bg-white p-4 text-texto-2">{children}</p>
);

const Carregando = () => (
  <p role="status" className="text-texto-2">
    Carregando...
  </p>
);

function maisAntiga(m: MaisAntiga | null): string {
  return m ? `Mais antiga há ${duracao(m.decorrido_s)}` : 'Nenhuma aguardando';
}

/** "Operação agora": contadores, cartões das abertas e atualização a cada 30 s com a aba visível. */
export function PainelOperacao() {
  const op = useCarga(() => api.painel<DadosOperacao>('operacao'));
  const { recarregar } = op;
  const acao = useAcao(async () => {
    await recarregar();
  });

  React.useEffect(() => {
    let timer: number | undefined;
    const parar = () => {
      if (timer !== undefined) window.clearInterval(timer);
      timer = undefined;
    };
    const iniciar = () => {
      parar();
      timer = window.setInterval(() => void recarregar(), INTERVALO_MS);
    };
    const aoMudarVisibilidade = () => {
      if (document.visibilityState === 'visible') {
        void recarregar();
        iniciar();
      } else {
        parar();
      }
    };
    if (document.visibilityState === 'visible') iniciar();
    document.addEventListener('visibilitychange', aoMudarVisibilidade);
    return () => {
      parar();
      document.removeEventListener('visibilitychange', aoMudarVisibilidade);
    };
  }, [recarregar]);

  const d = op.dados?.dados;
  return (
    <>
      <ExportarPainel painel="operacao" efetivos={op.dados?.filtros} />
      {op.erro ? <Alerta className="mb-4">{op.erro}</Alerta> : null}
      {acao.erro ? <Alerta className="mb-4">{acao.erro}</Alerta> : null}
      {op.carregando ? <Carregando /> : null}
      {d ? (
        <>
          <dl className="grid grid-cols-2 gap-3">
            <Kpi
              rotulo="Livres"
              valor={d.veiculos.livres}
              detalhe={`de ${d.veiculos.ativos} ativos`}
            />
            <Kpi rotulo="Reservados" valor={d.veiculos.reservados} />
            <Kpi rotulo="Em uso" valor={d.veiculos.em_uso} />
            <Kpi
              rotulo="Aguardando autorização"
              valor={d.solicitadas.total}
              detalhe={maisAntiga(d.solicitadas.mais_antiga)}
            />
            <Kpi
              rotulo="Autorizadas sem saída"
              valor={d.autorizadas_sem_saida.total}
              detalhe={maisAntiga(d.autorizadas_sem_saida.mais_antiga)}
            />
            <Kpi
              rotulo="Tempo em uso"
              valor={
                d.em_uso.decorrido_medio_s === null ? '-' : duracao(d.em_uso.decorrido_medio_s)
              }
              detalhe={
                d.em_uso.decorrido_max_s === null
                  ? 'média'
                  : `média; maior: ${duracao(d.em_uso.decorrido_max_s)}`
              }
            />
          </dl>
          <p className="mt-2 text-sm text-texto-2">
            Atualizado às {horaDe(d.gerado_em)} (renova a cada 30 s com a tela aberta)
          </p>

          <h2 className="mt-5 mb-3 text-base font-medium text-texto-2">Requisições em aberto</h2>
          {d.itens.length === 0 ? (
            <Vazio>Nenhuma requisição em aberto.</Vazio>
          ) : (
            <ul className="flex flex-col gap-3">
              {d.itens.map((r) => (
                <CartaoRequisicao
                  key={r.id}
                  req={r}
                  mostrarSolicitante
                  ocupado={acao.ocupado}
                  onAcao={(req, a, c) => void acao.executar(req, a, c)}
                  aoRegistrarChecklist={recarregar}
                />
              ))}
            </ul>
          )}
        </>
      ) : null}
    </>
  );
}

function Volume({ d }: { d: DadosVolume }) {
  const t = d.totais;
  if (t.total === 0 && d.serie.length === 0) return <Vazio />;
  return (
    <div className="flex flex-col gap-4">
      <dl className="grid grid-cols-2 gap-3">
        <Kpi rotulo="Total" valor={numero(t.total)} />
        <Kpi
          rotulo="Autorizadas"
          valor={numero(t.autorizadas)}
          detalhe={percentual(t.pct_autorizadas)}
        />
        <Kpi rotulo="Negadas" valor={numero(t.negadas)} detalhe={percentual(t.pct_negadas)} />
        <Kpi
          rotulo="Canceladas"
          valor={numero(t.canceladas)}
          detalhe={percentual(t.pct_canceladas)}
        />
        <Kpi rotulo="Pendentes" valor={numero(t.solicitadas_pendentes)} className="col-span-2" />
      </dl>
      <ComTabela
        titulo="Requisições por período"
        grafico={
          <BarrasEmpilhadas
            descricao="Barras empilhadas de autorizadas, negadas e canceladas por período. Use Ver como tabela para os valores."
            series={[
              { nome: 'Autorizadas', padrao: 'solido' },
              { nome: 'Negadas', padrao: 'listras' },
              { nome: 'Canceladas', padrao: 'pontos' },
            ]}
            pontos={d.serie.map((s) => ({
              rotulo: rotuloPeriodo(s.periodo),
              partes: [s.autorizadas, s.negadas, s.canceladas],
            }))}
          />
        }
        tabela={{
          colunas: ['Período', 'Total', 'Autorizadas', 'Negadas', 'Canceladas'],
          linhas: d.serie.map((s) => [
            rotuloPeriodo(s.periodo),
            numero(s.total),
            numero(s.autorizadas),
            numero(s.negadas),
            numero(s.canceladas),
          ]),
        }}
      />
    </div>
  );
}

function CartaoTempo({
  titulo,
  e,
  assinado,
}: {
  titulo: string;
  e: EstatisticaTempo;
  assinado?: boolean;
}) {
  const f = (s: number | null | undefined) =>
    s === null || s === undefined ? '-' : assinado ? duracaoEmRelacaoAoPrevisto(s) : duracao(s);
  const medidas: { rotulo: string; s: number | null | undefined }[] = [
    { rotulo: 'Média', s: e.media_s },
    { rotulo: 'Mediana', s: e.mediana_s },
    ...(assinado ? [{ rotulo: 'Mínimo', s: e.min_s }] : []),
    { rotulo: 'Máximo', s: e.max_s },
  ];
  return (
    <ComTabela
      titulo={titulo}
      grafico={
        e.n === 0 ? (
          <Vazio />
        ) : (
          <>
            <p className="mb-3 text-sm text-texto-2">Amostras: {numero(e.n)}</p>
            <BarrasHorizontais
              itens={medidas.map((m) => ({
                rotulo: m.rotulo,
                valor: Math.abs(m.s ?? 0),
                texto: f(m.s),
              }))}
            />
          </>
        )
      }
      tabela={{
        colunas: ['Medida', 'Valor'],
        linhas: [['Amostras', numero(e.n)], ...medidas.map((m) => [m.rotulo, f(m.s)])],
      }}
    />
  );
}

function Tempos({ d }: { d: DadosTempos }) {
  return (
    <div className="flex flex-col gap-4">
      <CartaoTempo titulo="Espera pela autorização" e={d.espera_autorizacao} />
      <CartaoTempo titulo="Duração do uso" e={d.duracao_uso} />
      <CartaoTempo titulo="Diferença em relação ao previsto" e={d.saida_vs_prevista} assinado />
    </div>
  );
}

function Unidades({ d }: { d: DadosUnidades }) {
  if (d.itens.length === 0) return <Vazio />;
  return (
    <ComTabela
      titulo="Requisições por unidade"
      grafico={
        <BarrasHorizontais
          itens={d.itens.map((i) => ({
            rotulo: i.unidade,
            valor: i.requisicoes,
            texto: `${numero(i.requisicoes)} requisições · ${numero(i.usos)} usos · ${decimal(i.horas_uso)} h`,
          }))}
        />
      }
      tabela={{
        colunas: ['Unidade', 'Requisições', 'Usos', 'Horas de uso'],
        linhas: d.itens.map((i) => [
          i.unidade,
          numero(i.requisicoes),
          numero(i.usos),
          decimal(i.horas_uso),
        ]),
      }}
    />
  );
}

function Veiculos({ d, veiculos }: { d: DadosVeiculos; veiculos: Veiculo[] }) {
  if (d.itens.length === 0) return <Vazio />;
  const nome = (id: string) => veiculos.find((v) => v.id === id)?.descricao ?? id;
  return (
    <ComTabela
      titulo="Taxa de ocupação por veículo"
      grafico={
        <>
          <p className="mb-3 text-sm text-texto-2">
            Horas no período: {decimal(d.horas_periodo)} h
          </p>
          <BarrasHorizontais
            itens={d.itens.map((i) => ({
              rotulo: `${nome(i.veiculo_id)}${i.ativo ? '' : ' (inativo)'}`,
              valor: i.taxa_ocupacao,
              texto: `${percentual(i.taxa_ocupacao * 100)} · ${numero(i.usos)} usos · ${decimal(i.horas_ocupadas)} h`,
            }))}
          />
        </>
      }
      tabela={{
        colunas: ['Veículo', 'Usos', 'Horas de uso', 'Horas ocupadas', 'Ocupação'],
        linhas: d.itens.map((i) => [
          `${nome(i.veiculo_id)}${i.ativo ? '' : ' (inativo)'}`,
          numero(i.usos),
          decimal(i.horas_uso),
          decimal(i.horas_ocupadas),
          percentual(i.taxa_ocupacao * 100),
        ]),
      }}
    />
  );
}

const humanizar = (chave: string) => {
  const t = chave.replace(/_/g, ' ');
  return t.charAt(0).toUpperCase() + t.slice(1);
};

function Governanca({ d }: { d: DadosGovernanca }) {
  return (
    <div className="flex flex-col gap-4">
      <section className="rounded-xl border border-borda bg-white p-4">
        <h3 className="mb-2 text-lg font-semibold">Contas por perfil</h3>
        <TabelaSimples
          titulo="Contas por perfil"
          colunas={['Perfil', 'Ativas', 'Inativas']}
          linhas={Object.entries(d.perfis).map(([k, v]) => [
            rotuloRole(k),
            numero(v.ativos),
            numero(v.inativos),
          ])}
        />
      </section>
      <section className="rounded-xl border border-borda bg-white p-4">
        <h3 className="mb-2 text-lg font-semibold">Convites</h3>
        <TabelaSimples
          titulo="Convites"
          colunas={['Situação', 'Quantidade']}
          linhas={Object.entries(d.convites).map(([k, v]) => [humanizar(k), numero(v)])}
        />
      </section>
      <section className="rounded-xl border border-borda bg-white p-4">
        <h3 className="mb-2 text-lg font-semibold">Configurações</h3>
        <TabelaSimples
          titulo="Configurações"
          colunas={['Recurso', 'Situação']}
          linhas={Object.entries(d.configuracoes).map(([k, v]) => [
            humanizar(k),
            v ? 'Ligado' : 'Desligado',
          ])}
        />
      </section>
      <section className="rounded-xl border border-borda bg-white p-4">
        <h3 className="mb-2 text-lg font-semibold">Auditoria administrativa recente</h3>
        {d.auditoria_admin.length === 0 ? (
          <p className="text-texto-2">Nenhum registro.</p>
        ) : (
          <TabelaSimples
            titulo="Auditoria administrativa recente"
            colunas={['Quando', 'Tabela', 'Ação']}
            linhas={d.auditoria_admin.map((a) => [
              dataHoraNumerica(a.ocorrido_em),
              a.tabela,
              a.acao,
            ])}
          />
        )}
      </section>
    </div>
  );
}

function Opcionais({ d }: { d: DadosOpcionais }) {
  const km = d.quilometragem;
  const ck = d.checklist;
  return (
    <div className="flex flex-col gap-4">
      <section aria-label="Quilometragem" className="rounded-xl border border-borda bg-white p-4">
        <h3 className="text-lg font-semibold">Quilometragem</h3>
        {km.habilitado ? (
          <dl className="mt-3 grid grid-cols-2 gap-3">
            <Kpi rotulo="Km rodado" valor={numero(km.km_rodado ?? 0)} />
            <Kpi rotulo="Viagens" valor={numero(km.viagens ?? 0)} />
          </dl>
        ) : (
          <p className="mt-1 text-texto-2">Recurso desligado.</p>
        )}
      </section>
      <section aria-label="Checklist" className="rounded-xl border border-borda bg-white p-4">
        <h3 className="text-lg font-semibold">Checklist</h3>
        {ck.habilitado ? (
          <dl className="mt-3 grid grid-cols-2 gap-3">
            <Kpi rotulo="Checklists" valor={numero(ck.total ?? 0)} />
            <Kpi rotulo="Com avaria" valor={numero(ck.com_avaria ?? 0)} />
          </dl>
        ) : (
          <p className="mt-1 text-texto-2">Recurso desligado.</p>
        )}
        {ck.habilitado ? (
          <>
            <h4 className="mt-4 mb-2 font-medium text-texto-2">Requisições com avaria</h4>
            {(ck.requisicoes_com_avaria ?? []).length === 0 ? (
              <p className="text-texto-2">Nenhuma no período.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {(ck.requisicoes_com_avaria ?? []).map((a) => (
                  <li key={`${a.requisicao_id}:${a.momento}`}>
                    <Link
                      href={`/requisicoes/detalhe?id=${encodeURIComponent(a.requisicao_id)}`}
                      className="flex min-h-11 items-center rounded-padrao border border-borda px-3 text-primaria underline"
                    >
                      {a.momento === 'saida' ? 'Saída' : 'Chegada'} ·{' '}
                      {dataHoraNumerica(a.registrado_em)}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <p className="mt-1 text-texto-2">Recurso desligado.</p>
        )}
      </section>
    </div>
  );
}

/** Painéis com filtros (volume, tempos, unidades, veículos, governança e opcionais). */
export function PainelComFiltros({
  painel,
  unidades,
  veiculos,
}: {
  painel: Exclude<PainelId, 'operacao'>;
  unidades: string[];
  veiculos: Veiculo[];
}) {
  const [filtros, setFiltros] = React.useState<FiltrosDashboard>({});
  const r = useCarga(() => api.painel<unknown>(painel, filtros), JSON.stringify(filtros));
  const d = r.dados?.dados;

  return (
    <>
      {painel !== 'governanca' ? (
        <BarraFiltros
          aplicados={filtros}
          efetivos={r.dados?.filtros}
          unidades={unidades}
          veiculos={veiculos}
          comGranularidade={painel === 'volume'}
          onAplicar={setFiltros}
        />
      ) : null}
      <ExportarPainel painel={painel} efetivos={r.dados?.filtros} />
      {r.erro ? <Alerta className="mb-4">{r.erro}</Alerta> : null}
      {r.carregando ? <Carregando /> : null}
      {d ? (
        painel === 'volume' ? (
          <Volume d={d as DadosVolume} />
        ) : painel === 'tempos' ? (
          <Tempos d={d as DadosTempos} />
        ) : painel === 'unidades' ? (
          <Unidades d={d as DadosUnidades} />
        ) : painel === 'veiculos' ? (
          <Veiculos d={d as DadosVeiculos} veiculos={veiculos} />
        ) : painel === 'governanca' ? (
          <Governanca d={d as DadosGovernanca} />
        ) : (
          <Opcionais d={d as DadosOpcionais} />
        )
      ) : null}
    </>
  );
}
