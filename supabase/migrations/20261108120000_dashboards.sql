-- Migration: dashboards (Entrega 1, camada de banco). Nova migration; nao edita as anteriores. Idempotente.
--
-- Motivo: o painel precisa de metricas agregadas (operacao agora, volume e desfecho, tempos do fluxo,
-- uso por unidade e por veiculo, governanca, recursos opcionais). A agregacao fica no banco (uma ida
-- por painel, sem trafegar linhas); o backend so repassa o jsonb. Regra de negocio nao e decidida
-- aqui: as funcoes apenas LEEM e resumem. Quem pode ver (role admin/autorizador) e checado pelo
-- backend, que e o unico chamador (service_role).
--
-- 1. Indices em requisicoes (confrontados com os existentes):
--    ja existiam: ix_requisicoes_usuario_id, _status, _veiculo_id, _autorizado_por, _saida_registrada_por,
--    _chegada_registrada_por, _cancelada_por e ux_requisicoes_veiculo_aberta (unico parcial por veiculo).
--    NOVOS: criado_em; (unidade, criado_em); saida_real_em (parcial, so linhas com saida real, pois toda
--    consulta de metricas filtra por intervalo e as nulas nunca entram); (status, criado_em) parcial
--    para os tres status abertos (fila "agora": solicitadas mais antigas primeiro).
--
-- 2. RPCs (todas SECURITY DEFINER, search_path vazio, STABLE, retornam jsonb; EXECUTE so service_role):
--    dash_operacao_agora, dash_volume_desfecho, dash_tempos_fluxo, dash_uso_unidade, dash_uso_veiculo,
--    dash_governanca, dash_opcionais. Formato dos retornos documentado em cada funcao.
--
-- Convencoes comuns:
--  * Janela [p_inicio, p_fim): inicio inclusivo, fim EXCLUSIVO. p_fim > p_inicio e no maximo 366 dias.
--  * Agrupamento por dia/semana/mes no fuso fixo America/Sao_Paulo (AT TIME ZONE); semana comeca na
--    segunda-feira (date_trunc). Um pedido criado 23:30 (-03) pertence ao dia LOCAL, nao ao dia UTC.
--  * Series preenchem periodos sem ocorrencia com zero (sem buracos para o grafico).
--  * Sem dado pessoal: so ids (uuid), contagens, tempos e enums. Nada de nome, e-mail, trajeto,
--    observacao nem descricao_avarias.
--  * Tempos em segundos (inteiros); horas com 2 casas; taxa de ocupacao entre 0 e 1 com 4 casas.
--  * Nenhum conceito de "atrasado" (CLAUDE.md: sem prazo maximo na v1).
--
-- Erro novo: AG023 = parametro de dashboard invalido (janela nula, fim <= inicio, janela > 366 dias,
-- granularidade fora de dia|semana|mes). AG024 em diante livres. (AG022 ja e a remocao de foto.)
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. Indices
-- ---------------------------------------------------------------------------
-- Series e totais por periodo de criacao.
create index if not exists ix_requisicoes_criado_em
  on public.requisicoes (criado_em);
-- Mesmo recorte por unidade (PU, PF, CJU, UEA).
create index if not exists ix_requisicoes_unidade_criado_em
  on public.requisicoes (unidade, criado_em);
-- Uso (duracao, horas, ocupacao) recortado por saida real.
create index if not exists ix_requisicoes_saida_real_em
  on public.requisicoes (saida_real_em)
  where saida_real_em is not null;
-- Fila operacional: requisicoes abertas por idade.
create index if not exists ix_requisicoes_abertas_criado_em
  on public.requisicoes (status, criado_em)
  where status in ('solicitada', 'autorizada', 'em_uso');

-- ---------------------------------------------------------------------------
-- 2. Validacao comum da janela (interna; ninguem da API executa)
-- ---------------------------------------------------------------------------
create schema if not exists privado;
revoke all on schema privado from public, anon, authenticated;

create or replace function privado.dash_validar_janela(p_inicio timestamptz, p_fim timestamptz)
returns void
language plpgsql
stable
set search_path = ''
as $$
begin
  if p_inicio is null or p_fim is null then
    raise exception 'Período do painel obrigatório.' using errcode = 'AG023';
  end if;
  if p_fim <= p_inicio then
    raise exception 'O fim do período deve ser posterior ao início.' using errcode = 'AG023';
  end if;
  if p_fim - p_inicio > interval '366 days' then
    raise exception 'Período máximo do painel: 366 dias.' using errcode = 'AG023';
  end if;
end;
$$;

revoke all on function privado.dash_validar_janela(timestamptz, timestamptz)
  from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 3. dash_operacao_agora
-- ---------------------------------------------------------------------------
-- Foto do momento (sem janela). p_unidade filtra apenas as REQUISICOES (listas e contagens); os
-- contadores de veiculos refletem a frota real (filtrados so por p_veiculo), pois a ocupacao de um
-- veiculo nao depende de quem o pediu. Um veiculo tem no maximo uma requisicao aberta (indice unico),
-- logo livres + reservados + em_uso = ativos (veiculo inativo com requisicao aberta nao entra).
-- Retorno:
--  { gerado_em, veiculos:{ativos,livres,reservados,em_uso},
--    solicitadas:{total, mais_antiga:{id,criado_em,decorrido_s}|null},
--    autorizadas_sem_saida:{total, mais_antiga:{id,autorizado_em,decorrido_s}|null},
--    em_uso:{total, decorrido_medio_s|null, decorrido_max_s|null},
--    abertas:[{id,status,veiculo_id,unidade,desde,decorrido_s}] }
-- abertas: lista completa por urgencia (solicitadas mais antigas primeiro, depois autorizadas por
-- autorizado_em, depois em_uso por saida_real_em). decorrido_s = tempo no estado ATUAL.
create or replace function public.dash_operacao_agora(
  p_unidade public.unidade default null,
  p_veiculo uuid default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_agora timestamptz := now();
  v_res   jsonb;
begin
  with vei as (
    select v.id,
           (select r.status from public.requisicoes r
             where r.veiculo_id = v.id and r.status in ('solicitada', 'autorizada', 'em_uso')
             limit 1) as st
      from public.veiculos v
     where v.ativo and (p_veiculo is null or v.id = p_veiculo)
  ),
  ab as (
    select r.id, r.status, r.veiculo_id, r.unidade,
           case r.status when 'solicitada' then r.criado_em
                         when 'autorizada' then r.autorizado_em
                         else r.saida_real_em end as desde
      from public.requisicoes r
     where r.status in ('solicitada', 'autorizada', 'em_uso')
       and (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
  ),
  ab2 as (
    select ab.*,
           greatest(0, floor(extract(epoch from v_agora - ab.desde)))::bigint as decorrido_s,
           case ab.status when 'solicitada' then 1 when 'autorizada' then 2 else 3 end as ord
      from ab
  )
  select jsonb_build_object(
    'gerado_em', v_agora,
    'veiculos', jsonb_build_object(
      'ativos',     (select count(*) from vei),
      'livres',     (select count(*) from vei where st is null),
      'reservados', (select count(*) from vei where st in ('solicitada', 'autorizada')),
      'em_uso',     (select count(*) from vei where st = 'em_uso')),
    'solicitadas', jsonb_build_object(
      'total', (select count(*) from ab2 where status = 'solicitada'),
      'mais_antiga', (select jsonb_build_object('id', id, 'criado_em', desde, 'decorrido_s', decorrido_s)
                        from ab2 where status = 'solicitada' order by desde, id limit 1)),
    'autorizadas_sem_saida', jsonb_build_object(
      'total', (select count(*) from ab2 where status = 'autorizada'),
      'mais_antiga', (select jsonb_build_object('id', id, 'autorizado_em', desde, 'decorrido_s', decorrido_s)
                        from ab2 where status = 'autorizada' order by desde, id limit 1)),
    'em_uso', jsonb_build_object(
      'total', (select count(*) from ab2 where status = 'em_uso'),
      'decorrido_medio_s', (select round(avg(decorrido_s))::bigint from ab2 where status = 'em_uso'),
      'decorrido_max_s',   (select max(decorrido_s) from ab2 where status = 'em_uso')),
    'abertas', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', id, 'status', status, 'veiculo_id', veiculo_id, 'unidade', unidade,
               'desde', desde, 'decorrido_s', decorrido_s)
             order by ord, desde, id)
        from ab2), '[]'::jsonb)
  ) into v_res;
  return v_res;
end;
$$;

comment on function public.dash_operacao_agora(public.unidade, uuid) is
  'Painel: situacao operacional agora (veiculos livres/reservados/em uso, fila e tempos no estado atual). Somente service_role.';

-- ---------------------------------------------------------------------------
-- 4. dash_volume_desfecho
-- ---------------------------------------------------------------------------
-- Requisicoes CRIADAS na janela. "autorizadas" = ja autorizadas (status autorizada, em_uso ou
-- concluida); negadas e canceladas contam pelo status atual; solicitadas_pendentes = ainda sem
-- decisao (total = autorizadas + negadas + canceladas + solicitadas_pendentes). pct_* em % com 1 casa
-- (null se total = 0).
-- Retorno:
--  { inicio, fim, granularidade, fuso,
--    totais:{total,autorizadas,negadas,canceladas,solicitadas_pendentes,
--            pct_autorizadas,pct_negadas,pct_canceladas},
--    serie:[{periodo:'YYYY-MM-DD' (inicio local do periodo), total,autorizadas,negadas,canceladas}] }
create or replace function public.dash_volume_desfecho(
  p_inicio timestamptz,
  p_fim timestamptz,
  p_unidade public.unidade default null,
  p_veiculo uuid default null,
  p_granularidade text default 'dia'
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_trunc text;
  v_passo interval;
  v_res   jsonb;
begin
  perform privado.dash_validar_janela(p_inicio, p_fim);
  if p_granularidade is null or p_granularidade not in ('dia', 'semana', 'mes') then
    raise exception 'Granularidade inválida: use dia, semana ou mes.' using errcode = 'AG023';
  end if;
  v_trunc := case p_granularidade when 'dia' then 'day' when 'semana' then 'week' else 'month' end;
  v_passo := case p_granularidade when 'dia' then interval '1 day'
                                  when 'semana' then interval '1 week'
                                  else interval '1 month' end;

  with r as (
    select date_trunc(v_trunc, x.criado_em at time zone 'America/Sao_Paulo') as b, x.status
      from public.requisicoes x
     where x.criado_em >= p_inicio and x.criado_em < p_fim
       and (p_unidade is null or x.unidade = p_unidade)
       and (p_veiculo is null or x.veiculo_id = p_veiculo)
  ),
  g as (
    select b,
           count(*) as total,
           count(*) filter (where status in ('autorizada', 'em_uso', 'concluida')) as autorizadas,
           count(*) filter (where status = 'negada') as negadas,
           count(*) filter (where status = 'cancelada') as canceladas
      from r group by b
  ),
  serie as (
    select gs as b
      from generate_series(
             date_trunc(v_trunc, p_inicio at time zone 'America/Sao_Paulo'),
             date_trunc(v_trunc, (p_fim - interval '1 microsecond') at time zone 'America/Sao_Paulo'),
             v_passo) as gs
  ),
  t as (
    select count(*) as total,
           count(*) filter (where status in ('autorizada', 'em_uso', 'concluida')) as autorizadas,
           count(*) filter (where status = 'negada') as negadas,
           count(*) filter (where status = 'cancelada') as canceladas,
           count(*) filter (where status = 'solicitada') as pendentes
      from r
  )
  select jsonb_build_object(
    'inicio', p_inicio, 'fim', p_fim, 'granularidade', p_granularidade, 'fuso', 'America/Sao_Paulo',
    'totais', (select jsonb_build_object(
                 'total', total, 'autorizadas', autorizadas, 'negadas', negadas,
                 'canceladas', canceladas, 'solicitadas_pendentes', pendentes,
                 'pct_autorizadas', case when total = 0 then null else round(100.0 * autorizadas / total, 1) end,
                 'pct_negadas',     case when total = 0 then null else round(100.0 * negadas / total, 1) end,
                 'pct_canceladas',  case when total = 0 then null else round(100.0 * canceladas / total, 1) end)
                 from t),
    'serie', coalesce((
      select jsonb_agg(jsonb_build_object(
               'periodo', to_char(s.b, 'YYYY-MM-DD'),
               'total', coalesce(g.total, 0), 'autorizadas', coalesce(g.autorizadas, 0),
               'negadas', coalesce(g.negadas, 0), 'canceladas', coalesce(g.canceladas, 0))
             order by s.b)
        from serie s left join g on g.b = s.b), '[]'::jsonb)
  ) into v_res;
  return v_res;
end;
$$;

comment on function public.dash_volume_desfecho(timestamptz, timestamptz, public.unidade, uuid, text) is
  'Painel: serie por dia/semana/mes (fuso America/Sao_Paulo) e totais/% de desfecho. Somente service_role.';

-- ---------------------------------------------------------------------------
-- 5. dash_tempos_fluxo
-- ---------------------------------------------------------------------------
-- Tres blocos, cada um com seu recorte de data:
--  * espera_autorizacao: criadas na janela e autorizadas (autorizado_em - criado_em; exclui negadas).
--  * duracao_uso: concluidas com saida_real_em na janela (chegada_real_em - saida_real_em).
--  * saida_vs_prevista: saidas reais na janela; saida_real_em - hora_saida, ASSINADA (negativo =
--    saiu antes do previsto, positivo = depois). Informativo, nao e "atraso".
-- Cada bloco: {n, media_s, mediana_s, max_s [, min_s]}; valores null se n = 0.
-- Retorno: { inicio, fim, espera_autorizacao, duracao_uso, saida_vs_prevista }
create or replace function public.dash_tempos_fluxo(
  p_inicio timestamptz,
  p_fim timestamptz,
  p_unidade public.unidade default null,
  p_veiculo uuid default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_res jsonb;
begin
  perform privado.dash_validar_janela(p_inicio, p_fim);

  with base as (
    select r.status, r.criado_em, r.autorizado_em, r.hora_saida, r.saida_real_em, r.chegada_real_em
      from public.requisicoes r
     where (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
       and ((r.criado_em >= p_inicio and r.criado_em < p_fim)
            or (r.saida_real_em >= p_inicio and r.saida_real_em < p_fim))
  ),
  esp as (
    select extract(epoch from autorizado_em - criado_em)::double precision as s
      from base
     where criado_em >= p_inicio and criado_em < p_fim
       and autorizado_em is not null and status <> 'negada'
  ),
  uso as (
    select extract(epoch from chegada_real_em - saida_real_em)::double precision as s
      from base
     where status = 'concluida' and saida_real_em >= p_inicio and saida_real_em < p_fim
  ),
  dif as (
    select extract(epoch from saida_real_em - hora_saida)::double precision as s
      from base
     where saida_real_em >= p_inicio and saida_real_em < p_fim
  )
  select jsonb_build_object(
    'inicio', p_inicio, 'fim', p_fim,
    'espera_autorizacao', (select jsonb_build_object(
        'n', count(*), 'media_s', round(avg(s))::bigint,
        'mediana_s', round(percentile_cont(0.5) within group (order by s))::bigint,
        'max_s', round(max(s))::bigint) from esp),
    'duracao_uso', (select jsonb_build_object(
        'n', count(*), 'media_s', round(avg(s))::bigint,
        'mediana_s', round(percentile_cont(0.5) within group (order by s))::bigint,
        'max_s', round(max(s))::bigint) from uso),
    'saida_vs_prevista', (select jsonb_build_object(
        'n', count(*), 'media_s', round(avg(s))::bigint,
        'mediana_s', round(percentile_cont(0.5) within group (order by s))::bigint,
        'min_s', round(min(s))::bigint, 'max_s', round(max(s))::bigint) from dif)
  ) into v_res;
  return v_res;
end;
$$;

comment on function public.dash_tempos_fluxo(timestamptz, timestamptz, public.unidade, uuid) is
  'Painel: espera por autorizacao, duracao de uso e diferenca saida real x prevista (segundos). Somente service_role.';

-- ---------------------------------------------------------------------------
-- 6. dash_uso_unidade
-- ---------------------------------------------------------------------------
-- Sempre lista as quatro unidades (ou so p_unidade, se informada), na ordem do enum (PU, PF, CJU, UEA).
-- requisicoes = criadas na janela; usos/horas_uso = concluidas com saida_real_em na janela.
-- Retorno: { inicio, fim, itens:[{unidade, requisicoes, usos, horas_uso}] }
create or replace function public.dash_uso_unidade(
  p_inicio timestamptz,
  p_fim timestamptz,
  p_unidade public.unidade default null,
  p_veiculo uuid default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_res jsonb;
begin
  perform privado.dash_validar_janela(p_inicio, p_fim);

  with cri as (
    select r.unidade, count(*) as n
      from public.requisicoes r
     where r.criado_em >= p_inicio and r.criado_em < p_fim
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
     group by r.unidade
  ),
  usos as (
    select r.unidade, count(*) as n,
           sum(extract(epoch from r.chegada_real_em - r.saida_real_em)) as seg
      from public.requisicoes r
     where r.status = 'concluida' and r.saida_real_em >= p_inicio and r.saida_real_em < p_fim
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
     group by r.unidade
  )
  select jsonb_build_object(
    'inicio', p_inicio, 'fim', p_fim,
    'itens', coalesce((
      select jsonb_agg(jsonb_build_object(
               'unidade', u.unidade,
               'requisicoes', coalesce(cri.n, 0),
               'usos', coalesce(usos.n, 0),
               'horas_uso', round(coalesce(usos.seg, 0) / 3600.0, 2))
             order by u.unidade)
        from unnest(enum_range(null::public.unidade)) as u(unidade)
        left join cri on cri.unidade = u.unidade
        left join usos on usos.unidade = u.unidade
       where p_unidade is null or u.unidade = p_unidade), '[]'::jsonb)
  ) into v_res;
  return v_res;
end;
$$;

comment on function public.dash_uso_unidade(timestamptz, timestamptz, public.unidade, uuid) is
  'Painel: requisicoes e horas de uso por unidade (PU, PF, CJU, UEA). Somente service_role.';

-- ---------------------------------------------------------------------------
-- 7. dash_uso_veiculo
-- ---------------------------------------------------------------------------
-- Por veiculo (ativos, mais inativos que tenham ocupacao na janela).
--  * usos/horas_uso: concluidas com saida_real_em na janela.
--  * horas_ocupadas/taxa_ocupacao: tempo em uso CORTADO a janela (inclui viagem em curso, contada ate
--    agora e limitada ao fim da janela) dividido pelas horas da janela inteira (p_fim - p_inicio).
--    Janela com futuro (p_fim > agora) reduz a taxa de proposito: o denominador e o periodo pedido.
-- Retorno: { inicio, fim, horas_periodo, itens:[{veiculo_id, ativo, usos, horas_uso,
--            horas_ocupadas, taxa_ocupacao}] }  (itens ordenados por taxa desc, veiculo_id)
create or replace function public.dash_uso_veiculo(
  p_inicio timestamptz,
  p_fim timestamptz,
  p_unidade public.unidade default null,
  p_veiculo uuid default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_agora   timestamptz := now();
  v_periodo numeric;
  v_res     jsonb;
begin
  perform privado.dash_validar_janela(p_inicio, p_fim);
  v_periodo := extract(epoch from p_fim - p_inicio);

  with usos as (
    select r.veiculo_id, count(*) as n,
           sum(extract(epoch from r.chegada_real_em - r.saida_real_em)) as seg
      from public.requisicoes r
     where r.status = 'concluida' and r.saida_real_em >= p_inicio and r.saida_real_em < p_fim
       and (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
     group by r.veiculo_id
  ),
  ocup as (
    select r.veiculo_id,
           sum(greatest(0, extract(epoch from
                 least(coalesce(r.chegada_real_em, v_agora), p_fim) - greatest(r.saida_real_em, p_inicio)))) as seg
      from public.requisicoes r
     where r.saida_real_em is not null
       and r.saida_real_em < p_fim
       and coalesce(r.chegada_real_em, v_agora) > p_inicio
       and (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
     group by r.veiculo_id
  ),
  it as (
    select v.id as veiculo_id, v.ativo,
           coalesce(usos.n, 0) as usos,
           round(coalesce(usos.seg, 0) / 3600.0, 2) as horas_uso,
           round(coalesce(ocup.seg, 0) / 3600.0, 2) as horas_ocupadas,
           round(least(1, coalesce(ocup.seg, 0) / v_periodo), 4) as taxa
      from public.veiculos v
      left join usos on usos.veiculo_id = v.id
      left join ocup on ocup.veiculo_id = v.id
     where (p_veiculo is null or v.id = p_veiculo)
       and (v.ativo or ocup.veiculo_id is not null or usos.veiculo_id is not null)
  )
  select jsonb_build_object(
    'inicio', p_inicio, 'fim', p_fim,
    'horas_periodo', round(v_periodo / 3600.0, 2),
    'itens', coalesce((
      select jsonb_agg(jsonb_build_object(
               'veiculo_id', veiculo_id, 'ativo', ativo, 'usos', usos, 'horas_uso', horas_uso,
               'horas_ocupadas', horas_ocupadas, 'taxa_ocupacao', taxa)
             order by taxa desc, veiculo_id)
        from it), '[]'::jsonb)
  ) into v_res;
  return v_res;
end;
$$;

comment on function public.dash_uso_veiculo(timestamptz, timestamptz, public.unidade, uuid) is
  'Painel: usos, horas e taxa de ocupacao por veiculo na janela. Somente service_role.';

-- ---------------------------------------------------------------------------
-- 8. dash_governanca
-- ---------------------------------------------------------------------------
-- Foto do momento, sem parametros (nao depende de janela, unidade nem veiculo).
-- Convites: usado (usado_em), revogado (revogado_em), expirado (aberto com expira_em <= agora),
-- pendente (aberto com expira_em > agora). Auditoria: ultimas 20 linhas de acoes administrativas
-- (tabelas perfis, veiculos, convites, configuracoes, perfil_fotos); so ids, sem de/para nem detalhes.
-- Retorno: { gerado_em,
--   perfis:{admin|autorizador|solicitante:{ativos,inativos}},
--   convites:{pendentes,expirados,usados,revogados},
--   auditoria_admin:[{id,ocorrido_em,tabela,registro_id,acao,ator_id}],
--   configuracoes:{quilometragem,abastecimento_manutencao,checklist,mfa} }
create or replace function public.dash_governanca()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_agora timestamptz := now();
  v_res   jsonb;
begin
  select jsonb_build_object(
    'gerado_em', v_agora,
    'perfis', (select jsonb_build_object(
        'admin', jsonb_build_object(
          'ativos', count(*) filter (where role = 'admin' and ativo),
          'inativos', count(*) filter (where role = 'admin' and not ativo)),
        'autorizador', jsonb_build_object(
          'ativos', count(*) filter (where role = 'autorizador' and ativo),
          'inativos', count(*) filter (where role = 'autorizador' and not ativo)),
        'solicitante', jsonb_build_object(
          'ativos', count(*) filter (where role = 'solicitante' and ativo),
          'inativos', count(*) filter (where role = 'solicitante' and not ativo)))
        from public.perfis),
    'convites', (select jsonb_build_object(
        'pendentes', count(*) filter (where usado_em is null and revogado_em is null and expira_em > v_agora),
        'expirados', count(*) filter (where usado_em is null and revogado_em is null and expira_em <= v_agora),
        'usados',    count(*) filter (where usado_em is not null),
        'revogados', count(*) filter (where revogado_em is not null))
        from public.convites),
    'auditoria_admin', coalesce((
        select jsonb_agg(jsonb_build_object(
                 'id', a.id, 'ocorrido_em', a.ocorrido_em, 'tabela', a.tabela,
                 'registro_id', a.registro_id, 'acao', a.acao, 'ator_id', a.ator_id)
               order by a.id desc)
          from (select * from public.auditoria
                 where tabela in ('perfis', 'veiculos', 'convites', 'configuracoes', 'perfil_fotos')
                 order by id desc limit 20) a), '[]'::jsonb),
    'configuracoes', (select jsonb_build_object(
        'quilometragem', c.quilometragem,
        'abastecimento_manutencao', c.abastecimento_manutencao,
        'checklist', c.checklist,
        'mfa', c.mfa)
        from public.configuracoes c where c.id)
  ) into v_res;
  return v_res;
end;
$$;

comment on function public.dash_governanca() is
  'Painel: perfis, convites, ultimas acoes administrativas (sem PII) e flags de configuracao. Somente service_role.';

-- ---------------------------------------------------------------------------
-- 9. dash_opcionais
-- ---------------------------------------------------------------------------
-- So devolve dados de um recurso se ele estiver LIGADO em configuracoes (desligado: apenas
-- {habilitado:false}; os dados gravados seguem intactos). NAO ha abastecimento/manutencao no banco.
--  * quilometragem: concluidas com saida_real_em na janela e km de saida/chegada preenchidos:
--    km_rodado = soma(km_chegada - km_saida); viagens = quantidade dessas concluidas.
--  * checklist: checklists registrados na janela (registrado_em); com_avaria; requisicoes_com_avaria
--    = ate 20 mais recentes, so ids (descricao_avarias e texto livre e nunca sai por aqui).
-- Retorno: { inicio, fim,
--   quilometragem:{habilitado:false} | {habilitado:true, km_rodado, viagens},
--   checklist:{habilitado:false} | {habilitado:true, total, com_avaria,
--              requisicoes_com_avaria:[{requisicao_id, momento, registrado_em}]} }
create or replace function public.dash_opcionais(
  p_inicio timestamptz,
  p_fim timestamptz,
  p_unidade public.unidade default null,
  p_veiculo uuid default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_km  boolean;
  v_chk boolean;
  v_q   jsonb;
  v_c   jsonb;
begin
  perform privado.dash_validar_janela(p_inicio, p_fim);
  select c.quilometragem, c.checklist into v_km, v_chk from public.configuracoes c where c.id;
  v_km := coalesce(v_km, false);
  v_chk := coalesce(v_chk, false);

  if v_km then
    select jsonb_build_object(
             'habilitado', true,
             'km_rodado', coalesce(sum(r.km_chegada - r.km_saida), 0)::bigint,
             'viagens', count(*))
      into v_q
      from public.requisicoes r
     where r.status = 'concluida' and r.saida_real_em >= p_inicio and r.saida_real_em < p_fim
       and r.km_saida is not null and r.km_chegada is not null
       and (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo);
  else
    v_q := jsonb_build_object('habilitado', false);
  end if;

  if v_chk then
    with ck as (
      select c.requisicao_id, c.momento, c.avarias, c.registrado_em
        from public.checklists c
        join public.requisicoes r on r.id = c.requisicao_id
       where c.registrado_em >= p_inicio and c.registrado_em < p_fim
         and (p_unidade is null or r.unidade = p_unidade)
         and (p_veiculo is null or r.veiculo_id = p_veiculo)
    )
    select jsonb_build_object(
             'habilitado', true,
             'total', (select count(*) from ck),
             'com_avaria', (select count(*) from ck where avarias),
             'requisicoes_com_avaria', coalesce((
                select jsonb_agg(jsonb_build_object(
                         'requisicao_id', requisicao_id, 'momento', momento, 'registrado_em', registrado_em)
                       order by registrado_em desc)
                  from (select * from ck where avarias order by registrado_em desc limit 20) x), '[]'::jsonb))
      into v_c;
  else
    v_c := jsonb_build_object('habilitado', false);
  end if;

  return jsonb_build_object('inicio', p_inicio, 'fim', p_fim, 'quilometragem', v_q, 'checklist', v_c);
end;
$$;

comment on function public.dash_opcionais(timestamptz, timestamptz, public.unidade, uuid) is
  'Painel: km rodado e checklists com avaria, so se o recurso estiver ligado. Somente service_role.';

-- ---------------------------------------------------------------------------
-- 10. Permissoes: so service_role executa (PUBLIC, anon e authenticated revogados)
-- ---------------------------------------------------------------------------
revoke all on function public.dash_operacao_agora(public.unidade, uuid) from public, anon, authenticated;
revoke all on function public.dash_volume_desfecho(timestamptz, timestamptz, public.unidade, uuid, text) from public, anon, authenticated;
revoke all on function public.dash_tempos_fluxo(timestamptz, timestamptz, public.unidade, uuid) from public, anon, authenticated;
revoke all on function public.dash_uso_unidade(timestamptz, timestamptz, public.unidade, uuid) from public, anon, authenticated;
revoke all on function public.dash_uso_veiculo(timestamptz, timestamptz, public.unidade, uuid) from public, anon, authenticated;
revoke all on function public.dash_governanca() from public, anon, authenticated;
revoke all on function public.dash_opcionais(timestamptz, timestamptz, public.unidade, uuid) from public, anon, authenticated;

grant execute on function public.dash_operacao_agora(public.unidade, uuid) to service_role;
grant execute on function public.dash_volume_desfecho(timestamptz, timestamptz, public.unidade, uuid, text) to service_role;
grant execute on function public.dash_tempos_fluxo(timestamptz, timestamptz, public.unidade, uuid) to service_role;
grant execute on function public.dash_uso_unidade(timestamptz, timestamptz, public.unidade, uuid) to service_role;
grant execute on function public.dash_uso_veiculo(timestamptz, timestamptz, public.unidade, uuid) to service_role;
grant execute on function public.dash_governanca() to service_role;
grant execute on function public.dash_opcionais(timestamptz, timestamptz, public.unidade, uuid) to service_role;

-- Autoverificacao: falha a migration se algum papel indevido executa, se PUBLIC executa ou se falta
-- SECURITY DEFINER / search_path fixo.
do $$
declare
  f text;
  r text;
begin
  foreach f in array array[
    'public.dash_operacao_agora(public.unidade, uuid)',
    'public.dash_volume_desfecho(timestamptz, timestamptz, public.unidade, uuid, text)',
    'public.dash_tempos_fluxo(timestamptz, timestamptz, public.unidade, uuid)',
    'public.dash_uso_unidade(timestamptz, timestamptz, public.unidade, uuid)',
    'public.dash_uso_veiculo(timestamptz, timestamptz, public.unidade, uuid)',
    'public.dash_governanca()',
    'public.dash_opcionais(timestamptz, timestamptz, public.unidade, uuid)'
  ] loop
    foreach r in array array['anon', 'authenticated'] loop
      if has_function_privilege(r, f::regprocedure, 'execute') then
        raise exception '% executa %.', r, f;
      end if;
    end loop;
    if not has_function_privilege('service_role', f::regprocedure, 'execute') then
      raise exception 'service_role sem EXECUTE em %.', f;
    end if;
    if exists (select 1 from pg_proc p2, aclexplode(coalesce(p2.proacl, acldefault('f', p2.proowner))) a
                where p2.oid = f::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE') then
      raise exception 'PUBLIC executa %.', f;
    end if;
    if not exists (select 1 from pg_proc p2
                    where p2.oid = f::regprocedure and p2.prosecdef
                      and array_to_string(p2.proconfig, ',') like '%search_path=%') then
      raise exception '% sem SECURITY DEFINER ou search_path fixo.', f;
    end if;
  end loop;
end
$$;
