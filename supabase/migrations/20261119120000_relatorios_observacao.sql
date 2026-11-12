-- Migration: relatorios_observacao. Nova migration; nao edita as anteriores. Idempotente.
--
-- Motivo: o relatorio passa a ter colunas selecionaveis (docs/analise-relatorio-campos-selecionaveis.md).
-- A observacao da requisicao (texto livre, ate 500 caracteres, pode conter dado pessoal) so entra
-- como OPT-IN: nova flag p_incluir_observacao (padrao false), no mesmo molde de p_incluir_nomes /
-- p_incluir_trajeto / p_incluir_descricao. Sem a flag, a chave NAO existe na linha nem em `colunas`.
-- QUEM pode ligar a flag e checado pelo backend (unico chamador, service_role); aqui so se le.
-- Fora do escopo desta migration (por decisao): prévia, resumo de IA e n8n nunca recebem observacao
-- (regra do backend); rel_resumo, rel_tempos, rel_auditoria e rel_opcionais_avarias nao mudam.
--
-- Funcoes afetadas (as que aceitam selecao de colunas): rel_movimentacao, rel_pendencias, rel_opcionais_km.
--   * O parametro novo vai NO FIM da lista (chamadas posicionais antigas continuam validas).
--   * Mudar a lista de parametros cria OUTRA funcao no Postgres; por isso a assinatura antiga e removida
--     (DROP ... IF EXISTS) antes de criar a nova, evitando sobrecarga ambigua no PostgREST/RPC.
--   * Corpo identico ao da migration 20261109120000, mais a coluna `observacao` (ultima de `colunas`)
--     e a chave `observacao` em `colunas_opcionais` de rel_movimentacao.
--   * Grants: PUBLIC/anon/authenticated sem EXECUTE; so service_role. SECURITY DEFINER, search_path vazio.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

drop function if exists public.rel_movimentacao(timestamptz, timestamptz, text, public.unidade, uuid, uuid, boolean, boolean, integer);
drop function if exists public.rel_pendencias(public.unidade, uuid, integer, uuid, boolean);
drop function if exists public.rel_opcionais_km(timestamptz, timestamptz, public.unidade, uuid, uuid, integer);

-- ---------------------------------------------------------------------------
-- 1. rel_movimentacao (+ observacao)
-- ---------------------------------------------------------------------------
create or replace function public.rel_movimentacao(
  p_inicio             timestamptz,
  p_fim                timestamptz,
  p_campo_data         text default 'criacao',
  p_unidade            public.unidade default null,
  p_veiculo            uuid default null,
  p_usuario            uuid default null,
  p_incluir_nomes      boolean default false,
  p_incluir_trajeto    boolean default false,
  p_limite             integer default 5001,
  p_incluir_observacao boolean default false
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_km     boolean;
  v_nomes  boolean := coalesce(p_incluir_nomes, false);
  v_traj   boolean := coalesce(p_incluir_trajeto, false);
  v_obs    boolean := coalesce(p_incluir_observacao, false);
  v_cols   text[] := array['codigo', 'criada_em', 'unidade', 'veiculo_id', 'veiculo', 'status',
                           'hora_saida_prevista', 'autorizada_em', 'saida_real_em', 'chegada_real_em',
                           'espera_autorizacao_s', 'duracao_uso_s'];
  v_linhas jsonb;
  v_cont   integer;
begin
  perform privado.dash_validar_janela(p_inicio, p_fim);
  perform privado.rel_validar_limite(p_limite);
  if p_campo_data is null or p_campo_data not in ('criacao', 'saida_prevista', 'saida_real') then
    raise exception 'Campo de data inválido: use criacao, saida_prevista ou saida_real.'
      using errcode = 'AG023';
  end if;

  select coalesce(c.quilometragem, false) into v_km from public.configuracoes c where c.id;
  v_km := coalesce(v_km, false);
  if v_km then v_cols := v_cols || array['km_saida', 'km_chegada']; end if;
  if v_nomes then v_cols := v_cols || array['solicitante', 'autorizado_por', 'saida_por', 'chegada_por']; end if;
  if v_traj then v_cols := v_cols || array['trajeto']; end if;
  if v_obs then v_cols := v_cols || array['observacao']; end if;

  with ids as (
    select r.id, r.criado_em as k from public.requisicoes r
     where p_campo_data = 'criacao' and r.criado_em >= p_inicio and r.criado_em < p_fim
       and (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
       and (p_usuario is null or r.usuario_id = p_usuario)
    union all
    select r.id, r.hora_saida from public.requisicoes r
     where p_campo_data = 'saida_prevista' and r.hora_saida >= p_inicio and r.hora_saida < p_fim
       and (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
       and (p_usuario is null or r.usuario_id = p_usuario)
    union all
    select r.id, r.saida_real_em from public.requisicoes r
     where p_campo_data = 'saida_real' and r.saida_real_em >= p_inicio and r.saida_real_em < p_fim
       and (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
       and (p_usuario is null or r.usuario_id = p_usuario)
  ),
  sel as (
    select i.id, i.k, row_number() over (order by i.k, i.id) as n
      from (select * from ids order by k, id limit p_limite + 1) i
  )
  select coalesce(jsonb_agg(x.j order by x.n) filter (where x.n <= p_limite), '[]'::jsonb),
         count(*)
    into v_linhas, v_cont
    from (
      select s.n,
             jsonb_build_object(
               'codigo', left(r.id::text, 8),
               'criada_em', r.criado_em,
               'unidade', r.unidade,
               'veiculo_id', r.veiculo_id,
               'veiculo', case when v.placa is null then v.descricao else v.placa || ' - ' || v.descricao end,
               'status', r.status,
               'hora_saida_prevista', r.hora_saida,
               'autorizada_em', r.autorizado_em,
               'saida_real_em', r.saida_real_em,
               'chegada_real_em', r.chegada_real_em,
               'espera_autorizacao_s',
                 case when r.autorizado_em is not null and r.status <> 'negada'
                      then floor(extract(epoch from r.autorizado_em - r.criado_em))::bigint end,
               'duracao_uso_s',
                 case when r.saida_real_em is not null and r.chegada_real_em is not null
                      then floor(extract(epoch from r.chegada_real_em - r.saida_real_em))::bigint end)
             || case when v_km then jsonb_build_object('km_saida', r.km_saida, 'km_chegada', r.km_chegada)
                     else '{}'::jsonb end
             || case when v_nomes then jsonb_build_object(
                  'solicitante', r.nome_solicitante,
                  'autorizado_por', pa.nome,
                  'saida_por', ps.nome,
                  'chegada_por', pc.nome) else '{}'::jsonb end
             || case when v_traj then jsonb_build_object('trajeto', r.trajeto) else '{}'::jsonb end
             || case when v_obs then jsonb_build_object('observacao', r.observacao) else '{}'::jsonb end as j
        from sel s
        join public.requisicoes r on r.id = s.id
        join public.veiculos v on v.id = r.veiculo_id
        left join public.perfis pa on v_nomes and pa.id = r.autorizado_por
        left join public.perfis ps on v_nomes and ps.id = r.saida_registrada_por
        left join public.perfis pc on v_nomes and pc.id = r.chegada_registrada_por
    ) x;

  return jsonb_build_object(
    'campo_data', p_campo_data,
    'colunas', to_jsonb(v_cols),
    'colunas_opcionais', jsonb_build_object('km', v_km, 'nomes', v_nomes, 'trajeto', v_traj,
                                            'observacao', v_obs),
    'total_linhas', least(v_cont, p_limite),
    'truncado', v_cont > p_limite,
    'linhas', v_linhas);
end;
$$;

comment on function public.rel_movimentacao(timestamptz, timestamptz, text, public.unidade, uuid, uuid, boolean, boolean, integer, boolean) is
  'Relatorio: uma linha por requisicao (nomes, trajeto e observacao so por flag; km so se recurso ligado). Somente service_role.';

-- ---------------------------------------------------------------------------
-- 2. rel_pendencias (+ observacao)
-- ---------------------------------------------------------------------------
create or replace function public.rel_pendencias(
  p_unidade            public.unidade default null,
  p_veiculo            uuid default null,
  p_limite             integer default 5001,
  p_usuario            uuid default null,
  p_incluir_nomes      boolean default false,
  p_incluir_observacao boolean default false
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_agora  timestamptz := now();
  v_nomes  boolean := coalesce(p_incluir_nomes, false);
  v_obs    boolean := coalesce(p_incluir_observacao, false);
  v_cols   text[] := array['codigo', 'status', 'unidade', 'veiculo_id', 'veiculo', 'hora_saida_prevista',
                           'desde', 'decorrido_s'];
  v_linhas jsonb;
  v_cont   integer;
begin
  perform privado.rel_validar_limite(p_limite);
  if v_nomes then v_cols := v_cols || array['solicitante']; end if;
  if v_obs then v_cols := v_cols || array['observacao']; end if;

  with ab as (
    select r.id,
           case r.status when 'solicitada' then 1 when 'autorizada' then 2 else 3 end as ord,
           case r.status when 'solicitada' then r.criado_em
                         when 'autorizada' then r.autorizado_em
                         else r.saida_real_em end as desde
      from public.requisicoes r
     where r.status in ('solicitada', 'autorizada', 'em_uso')
       and (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
       and (p_usuario is null or r.usuario_id = p_usuario)
  ),
  sel as (
    select s.id, s.ord, s.desde, row_number() over (order by s.ord, s.desde, s.id) as n
      from (select * from ab order by ord, desde, id limit p_limite + 1) s
  )
  select coalesce(jsonb_agg(x.j order by x.n) filter (where x.n <= p_limite), '[]'::jsonb), count(*)
    into v_linhas, v_cont
    from (
      select s.n,
             jsonb_build_object(
               'codigo', left(r.id::text, 8),
               'status', r.status,
               'unidade', r.unidade,
               'veiculo_id', r.veiculo_id,
               'veiculo', case when v.placa is null then v.descricao else v.placa || ' - ' || v.descricao end,
               'hora_saida_prevista', r.hora_saida,
               'desde', s.desde,
               'decorrido_s', greatest(0, floor(extract(epoch from v_agora - s.desde)))::bigint)
             || case when v_nomes then jsonb_build_object('solicitante', r.nome_solicitante)
                     else '{}'::jsonb end
             || case when v_obs then jsonb_build_object('observacao', r.observacao)
                     else '{}'::jsonb end as j
        from sel s
        join public.requisicoes r on r.id = s.id
        join public.veiculos v on v.id = r.veiculo_id
    ) x;

  return jsonb_build_object('gerado_em', v_agora, 'colunas', to_jsonb(v_cols),
                            'total_linhas', least(v_cont, p_limite), 'truncado', v_cont > p_limite,
                            'linhas', v_linhas);
end;
$$;

comment on function public.rel_pendencias(public.unidade, uuid, integer, uuid, boolean, boolean) is
  'Relatorio: requisicoes abertas agora (solicitadas, autorizadas sem saida, em uso); solicitante e observacao so por flag. Somente service_role.';

-- ---------------------------------------------------------------------------
-- 3. rel_opcionais_km (+ observacao)
-- ---------------------------------------------------------------------------
-- Recurso Quilometragem desligado continua devolvendo apenas {"habilitado": false}.
create or replace function public.rel_opcionais_km(
  p_inicio             timestamptz,
  p_fim                timestamptz,
  p_unidade            public.unidade default null,
  p_veiculo            uuid default null,
  p_usuario            uuid default null,
  p_limite             integer default 5001,
  p_incluir_observacao boolean default false
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_km     boolean;
  v_obs    boolean := coalesce(p_incluir_observacao, false);
  v_cols   text[] := array['codigo', 'unidade', 'veiculo_id', 'veiculo', 'saida_real_em',
                           'chegada_real_em', 'km_saida', 'km_chegada', 'km_rodado'];
  v_linhas jsonb;
  v_cont   integer;
  v_viag   bigint;
  v_rodado bigint;
begin
  perform privado.dash_validar_janela(p_inicio, p_fim);
  perform privado.rel_validar_limite(p_limite);
  select coalesce(c.quilometragem, false) into v_km from public.configuracoes c where c.id;
  if not coalesce(v_km, false) then
    return jsonb_build_object('habilitado', false);
  end if;
  if v_obs then v_cols := v_cols || array['observacao']; end if;

  select count(*), coalesce(sum(r.km_chegada - r.km_saida), 0)::bigint into v_viag, v_rodado
    from public.requisicoes r
   where r.status = 'concluida' and r.saida_real_em >= p_inicio and r.saida_real_em < p_fim
     and r.km_saida is not null and r.km_chegada is not null
     and (p_unidade is null or r.unidade = p_unidade)
     and (p_veiculo is null or r.veiculo_id = p_veiculo)
     and (p_usuario is null or r.usuario_id = p_usuario);

  with sel as (
    select s.id, row_number() over (order by s.saida_real_em, s.id) as n
      from (select r.id, r.saida_real_em from public.requisicoes r
             where r.status = 'concluida' and r.saida_real_em >= p_inicio and r.saida_real_em < p_fim
               and r.km_saida is not null and r.km_chegada is not null
               and (p_unidade is null or r.unidade = p_unidade)
               and (p_veiculo is null or r.veiculo_id = p_veiculo)
               and (p_usuario is null or r.usuario_id = p_usuario)
             order by r.saida_real_em, r.id limit p_limite + 1) s
  )
  select coalesce(jsonb_agg(x.j order by x.n) filter (where x.n <= p_limite), '[]'::jsonb), count(*)
    into v_linhas, v_cont
    from (
      select s.n,
             jsonb_build_object(
               'codigo', left(r.id::text, 8), 'unidade', r.unidade, 'veiculo_id', r.veiculo_id,
               'veiculo', case when v.placa is null then v.descricao else v.placa || ' - ' || v.descricao end,
               'saida_real_em', r.saida_real_em, 'chegada_real_em', r.chegada_real_em,
               'km_saida', r.km_saida, 'km_chegada', r.km_chegada,
               'km_rodado', r.km_chegada - r.km_saida)
             || case when v_obs then jsonb_build_object('observacao', r.observacao)
                     else '{}'::jsonb end as j
        from sel s
        join public.requisicoes r on r.id = s.id
        join public.veiculos v on v.id = r.veiculo_id
    ) x;

  return jsonb_build_object(
    'habilitado', true,
    'colunas', to_jsonb(v_cols),
    'totais', jsonb_build_object('viagens', v_viag, 'km_rodado', v_rodado),
    'total_linhas', least(v_cont, p_limite), 'truncado', v_cont > p_limite, 'linhas', v_linhas);
end;
$$;

comment on function public.rel_opcionais_km(timestamptz, timestamptz, public.unidade, uuid, uuid, integer, boolean) is
  'Relatorio: km por viagem concluida, so se o recurso Quilometragem estiver ligado; observacao so por flag. Somente service_role.';

-- ---------------------------------------------------------------------------
-- 4. Permissoes: so service_role executa (PUBLIC, anon e authenticated revogados)
-- ---------------------------------------------------------------------------
revoke all on function public.rel_movimentacao(timestamptz, timestamptz, text, public.unidade, uuid, uuid, boolean, boolean, integer, boolean) from public, anon, authenticated;
revoke all on function public.rel_pendencias(public.unidade, uuid, integer, uuid, boolean, boolean) from public, anon, authenticated;
revoke all on function public.rel_opcionais_km(timestamptz, timestamptz, public.unidade, uuid, uuid, integer, boolean) from public, anon, authenticated;

grant execute on function public.rel_movimentacao(timestamptz, timestamptz, text, public.unidade, uuid, uuid, boolean, boolean, integer, boolean) to service_role;
grant execute on function public.rel_pendencias(public.unidade, uuid, integer, uuid, boolean, boolean) to service_role;
grant execute on function public.rel_opcionais_km(timestamptz, timestamptz, public.unidade, uuid, uuid, integer, boolean) to service_role;

-- Autoverificacao: sem sobrecarga antiga; ninguem alem de service_role executa; SECURITY DEFINER e
-- search_path fixo preservados.
do $$
declare
  f text;
  r text;
begin
  if exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
              where n.nspname = 'public' and p.proname in ('rel_movimentacao', 'rel_pendencias', 'rel_opcionais_km')
              group by p.proname having count(*) > 1) then
    raise exception 'Sobrecarga duplicada em rel_movimentacao/rel_pendencias/rel_opcionais_km.';
  end if;

  foreach f in array array[
    'public.rel_movimentacao(timestamptz, timestamptz, text, public.unidade, uuid, uuid, boolean, boolean, integer, boolean)',
    'public.rel_pendencias(public.unidade, uuid, integer, uuid, boolean, boolean)',
    'public.rel_opcionais_km(timestamptz, timestamptz, public.unidade, uuid, uuid, integer, boolean)'
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
