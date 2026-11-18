-- Migration: dash_movimentacao_km (painel "Movimentacao e km"). Nova migration; nao edita as anteriores.
-- Idempotente (create index if not exists, create or replace, revoke/grant repetiveis).
--
-- Motivo: o painel novo "movimentacao" mostra, por periodo e por veiculo, quantas saidas e chegadas
-- REAIS ocorreram e quantos km foram rodados. A agregacao fica no banco (uma ida, sem trafegar linhas);
-- o backend so repassa o jsonb. A funcao apenas LE: nao decide regra nem grava nada. Quem pode ver
-- (admin/autorizador) e checado pelo backend, unico chamador (service_role).
--
-- Mesmo padrao de dash_volume_desfecho (20261108120000):
--  * SECURITY DEFINER, search_path vazio, STABLE, retorna jsonb; EXECUTE so service_role.
--  * Janela [p_inicio, p_fim) validada por privado.dash_validar_janela (AG023; max. 366 dias).
--  * Granularidade dia|semana|mes (AG023 fora disso); balde no fuso fixo America/Sao_Paulo
--    (AT TIME ZONE + date_trunc; semana comeca na segunda); periodo = 'YYYY-MM-DD' do inicio local
--    do balde; serie preenchida com zero (sem buracos).
--  * p_unidade e public.unidade (como nas demais dash_*): o backend valida e envia o texto; o
--    PostgREST converte para o enum.
--
-- Regras da contagem (todas com os filtros p_unidade/p_veiculo):
--  * saidas   = requisicoes com saida_real_em na janela, no balde de saida_real_em (qualquer status:
--               em_uso e concluida; o banco ja impede saida real em negada/cancelada/solicitada).
--  * chegadas = requisicoes com chegada_real_em na janela, no balde de chegada_real_em.
--  * km_rodado = soma de requisicoes.km_percorrido (coluna calculada pelo banco em 20261127120000,
--               km_chegada - km_saida) das viagens com CHEGADA no balde; viagens sem km nao somam.
--               So sai com o recurso Quilometragem ligado (configuracoes.quilometragem); desligado, a
--               chave km_rodado fica AUSENTE e quilometragem_habilitada = false (o backend tambem remove;
--               defesa em profundidade). Desligar o recurso nao apaga dados.
--  * por_veiculo: veiculos ativos (zerados se sem movimento) e inativos com movimento na janela;
--    ordenado por saidas desc, chegadas desc, veiculo_id.
-- Sem dado pessoal: so ids, contagens e km. Nada de nome, e-mail, trajeto, observacao nem localizacao.
--
-- Retorno:
--  { serie:[{periodo, saidas, chegadas, km_rodado?}],
--    por_veiculo:[{veiculo_id, saidas, chegadas, km_rodado?}],
--    quilometragem_habilitada, granularidade, fuso:'America/Sao_Paulo' }
--
-- Indice novo: chegada_real_em (parcial, so linhas com chegada), espelho de ix_requisicoes_saida_real_em
-- (20261108120000), pois a contagem de chegadas e o km filtram por intervalo de chegada_real_em.
-- RLS de requisicoes intacto. Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

set local lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 1. Indice
-- ---------------------------------------------------------------------------
create index if not exists ix_requisicoes_chegada_real_em
  on public.requisicoes (chegada_real_em)
  where chegada_real_em is not null;

-- ---------------------------------------------------------------------------
-- 2. dash_movimentacao_km
-- ---------------------------------------------------------------------------
create or replace function public.dash_movimentacao_km(
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
  v_km    boolean;
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
  select c.quilometragem into v_km from public.configuracoes c where c.id;
  v_km := coalesce(v_km, false);

  with sai as (
    select x.veiculo_id,
           date_trunc(v_trunc, x.saida_real_em at time zone 'America/Sao_Paulo') as b
      from public.requisicoes x
     where x.saida_real_em >= p_inicio and x.saida_real_em < p_fim
       and (p_unidade is null or x.unidade = p_unidade)
       and (p_veiculo is null or x.veiculo_id = p_veiculo)
  ),
  che as (
    select x.veiculo_id,
           date_trunc(v_trunc, x.chegada_real_em at time zone 'America/Sao_Paulo') as b,
           x.km_percorrido
      from public.requisicoes x
     where x.chegada_real_em >= p_inicio and x.chegada_real_em < p_fim
       and (p_unidade is null or x.unidade = p_unidade)
       and (p_veiculo is null or x.veiculo_id = p_veiculo)
  ),
  gs as (select b, count(*) as n from sai group by b),
  gc as (select b, count(*) as n, coalesce(sum(km_percorrido), 0) as km from che group by b),
  serie as (
    select s as b
      from generate_series(
             date_trunc(v_trunc, p_inicio at time zone 'America/Sao_Paulo'),
             date_trunc(v_trunc, (p_fim - interval '1 microsecond') at time zone 'America/Sao_Paulo'),
             v_passo) as s
  ),
  vs as (select veiculo_id, count(*) as n from sai group by veiculo_id),
  vc as (select veiculo_id, count(*) as n, coalesce(sum(km_percorrido), 0) as km from che group by veiculo_id),
  pv as (
    select v.id as veiculo_id,
           coalesce(vs.n, 0) as saidas,
           coalesce(vc.n, 0) as chegadas,
           coalesce(vc.km, 0) as km
      from public.veiculos v
      left join vs on vs.veiculo_id = v.id
      left join vc on vc.veiculo_id = v.id
     where (p_veiculo is null or v.id = p_veiculo)
       and (v.ativo or vs.veiculo_id is not null or vc.veiculo_id is not null)
  )
  select jsonb_build_object(
    'serie', coalesce((
      select jsonb_agg(
               jsonb_build_object(
                 'periodo', to_char(s.b, 'YYYY-MM-DD'),
                 'saidas', coalesce(gs.n, 0),
                 'chegadas', coalesce(gc.n, 0))
               || case when v_km then jsonb_build_object('km_rodado', coalesce(gc.km, 0))
                       else '{}'::jsonb end
             order by s.b)
        from serie s
        left join gs on gs.b = s.b
        left join gc on gc.b = s.b), '[]'::jsonb),
    'por_veiculo', coalesce((
      select jsonb_agg(
               jsonb_build_object('veiculo_id', pv.veiculo_id, 'saidas', pv.saidas, 'chegadas', pv.chegadas)
               || case when v_km then jsonb_build_object('km_rodado', pv.km) else '{}'::jsonb end
             order by pv.saidas desc, pv.chegadas desc, pv.veiculo_id)
        from pv), '[]'::jsonb),
    'quilometragem_habilitada', v_km,
    'granularidade', p_granularidade,
    'fuso', 'America/Sao_Paulo'
  ) into v_res;
  return v_res;
end;
$$;

comment on function public.dash_movimentacao_km(timestamptz, timestamptz, public.unidade, uuid, text) is
  'Painel: saidas/chegadas reais e km rodado por dia/semana/mes (fuso America/Sao_Paulo) e por veiculo; km so com Quilometragem ligada. Somente leitura; somente service_role.';

-- ---------------------------------------------------------------------------
-- 3. Permissoes: so service_role executa (PUBLIC, anon e authenticated revogados)
-- ---------------------------------------------------------------------------
revoke all on function public.dash_movimentacao_km(timestamptz, timestamptz, public.unidade, uuid, text)
  from public, anon, authenticated;
grant execute on function public.dash_movimentacao_km(timestamptz, timestamptz, public.unidade, uuid, text)
  to service_role;

-- Autoverificacao (mesma de 20261108120000): falha a migration se anon/authenticated/PUBLIC executam,
-- se service_role nao executa ou se falta SECURITY DEFINER / search_path fixo.
do $$
declare
  f constant text := 'public.dash_movimentacao_km(timestamptz, timestamptz, public.unidade, uuid, text)';
  r text;
begin
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
end
$$;
