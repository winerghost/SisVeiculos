-- Migration 26: recurso opcional ABASTECIMENTO E MANUTENCAO, parte 2 (regras de registro, odometro, views).
-- Nova migration; nao edita as anteriores. Idempotente.
--
-- Motivo: a migration 25 criou as tabelas. Aqui ficam as regras que dependem de OUTRAS tabelas
-- (ator, requisicao, odometro) e as views de "registro vigente" (sem anulacao).
--
-- ODOMETRO NAO RETROCEDE (invariante dos dados, vale sempre, ligado ou desligado o recurso):
--  * Todo novo abastecimento/manutencao exige km >= maior km entre os registros VIGENTES do mesmo
--    veiculo, nas DUAS tabelas (view odometro_registros_vigentes). Violacao => AG015.
--  * Por que vale mesmo com o recurso desligado: o km e NOT NULL nestas tabelas, nao ha
--    "obrigatoriedade" a relaxar; e uma invariante do dado, como km_chegada >= km_saida na
--    migration 18. Com o recurso desligado o backend recusa os endpoints; o banco nao exige nada
--    do recurso (nem que a flag exista), apenas nao deixa gravar sequencia incoerente.
--  * Limite do escopo: nao confronta km_saida/km_chegada de requisicoes (outra tabela, imutavel
--    e sem anulacao: um erro de digitacao la travaria o abastecimento do veiculo para sempre).
--  * Ordem e a de REGISTRO (carimbo do servidor), nao a da data informada: nao e possivel lancar
--    depois um registro mais antigo com km menor que o ultimo vigente (anular e relancar resolve
--    erros de digitacao). Anular nunca quebra a sequencia (so remove um ponto).
--
-- CONCORRENCIA E LOCKS (contrato da migration 15 preservado):
--  * Dois registros simultaneos do mesmo veiculo se serializam por ADVISORY LOCK de transacao com
--    chave PROPRIA por veiculo: pg_advisory_xact_lock(hashtextextended('sisveiculos.odometro:' ||
--    veiculo_id, 0)). Nao usa 'sisveiculos.perfis_admin' nem FOR SHARE/FOR UPDATE em
--    perfis/veiculos/configuracoes: nenhuma espera nova entre registro e escrita administrativa.
--  * ORDEM: o advisory do odometro e tomado no gatilho BEFORE INSERT, depois de validacoes que
--    nao travam nada (leituras comuns, sem FOR SHARE) e antes de qualquer lock de linha da
--    transacao (as FKs, AFTER, tomam FOR KEY SHARE, que nao conflita com UPDATE de colunas nao
--    chave). Esta transacao NUNCA toma o advisory 'perfis_admin' (o registro nao faz acao
--    administrativa), logo nao ha ciclo. O backend NAO deve misturar, na mesma transacao,
--    registro de odometro e escrita administrativa em perfis/veiculos/convites.
--  * Custo: um lock por veiculo, mantido ate o fim da transacao (curta). Sem contencao entre
--    veiculos diferentes (colisao de hash so serializa a mais, nunca erra). A leitura pos-lock
--    enxerga o que ja comitou (READ COMMITTED; PostgREST usa este nivel). Em REPEATABLE READ o
--    snapshot ja estaria fixado e a serializacao nao bastaria: nao usar.
--  * lock_timeout da role (5s, migration 16) tambem vale para este advisory: 55P03 => retry.
--
-- ATOR: registrado_por/anulado_por precisa ser perfil ativo autorizador ou admin (AG002), lido SEM
-- lock (limite aceito B4 do checklist: um admin que desative o ator no mesmo instante pode nao
-- ser visto por esta transacao).
--
-- ERROS SEM CONTEUDO DA LINHA (licao M1/B-a/B-b do checklist):
--  * manutencoes tem texto livre (descricao). O gatilho BEFORE INSERT valida TODAS as regras que
--    os NOT NULL/CHECKs validariam, e recusa com MENSAGEM FIXA, sem DETAIL, preenchendo
--    schema/table/column/constraint. O Postgres so monta `Failing row contains (...)` depois dos
--    gatilhos BEFORE; como o gatilho recusa antes, a descricao nunca vai ao erro, ao PostgREST
--    nem ao log do servidor. Contrato do backend: identificar o campo pela mensagem fixa ou por
--    diag.column_name (23502) / diag.constraint_name (23514); nunca repassar message_detail.
--  * abastecimentos nao tem texto livre: os CHECKs da propria tabela bastam (a linha tem so
--    ids e numeros).
--  * Limite: erro de cast de parametro de RPC (22P02, enum) cita so o valor do parametro.
--
-- Erros novos: AG015 odometro retrocede; AG016 requisicao de outro veiculo. Reaproveitados: AG002.
-- Gatilhos e funcoes privadas: SECURITY DEFINER, search_path vazio, sem EXECUTE para ninguem da API
-- (nem service_role). Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. Views de registro vigente (sem anulacao). security_invoker: nao contornam RLS/grants.
-- ---------------------------------------------------------------------------
create or replace view public.abastecimentos_vigentes
with (security_invoker = true) as
select a.*
  from public.abastecimentos a
 where not exists (select 1 from public.abastecimentos_anulacoes n where n.abastecimento_id = a.id);

create or replace view public.manutencoes_vigentes
with (security_invoker = true) as
select m.*
  from public.manutencoes m
 where not exists (select 1 from public.manutencoes_anulacoes n where n.manutencao_id = m.id);

create or replace view public.odometro_registros_vigentes
with (security_invoker = true) as
select 'abastecimento'::text as origem, a.id, a.veiculo_id, a.km, a.registrado_em
  from public.abastecimentos_vigentes a
union all
select 'manutencao'::text, m.id, m.veiculo_id, m.km, m.registrado_em
  from public.manutencoes_vigentes m;

comment on view public.odometro_registros_vigentes is
  'Pontos do odometro (abastecimentos e manutencoes nao anulados). Maior km por veiculo = km atual conhecido.';

revoke all on public.abastecimentos_vigentes, public.manutencoes_vigentes,
  public.odometro_registros_vigentes from public, anon, authenticated, service_role;
grant select on public.abastecimentos_vigentes, public.manutencoes_vigentes,
  public.odometro_registros_vigentes to service_role;

-- ---------------------------------------------------------------------------
-- 2. Funcoes privadas
-- ---------------------------------------------------------------------------
create schema if not exists privado;

-- Ator que registra/anula: perfil ativo autorizador ou admin (sem lock).
create or replace function privado.exigir_registrador_ativo(p_ator uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role  public.role_usuario;
  v_ativo boolean;
begin
  select role, ativo into v_role, v_ativo from public.perfis where id = p_ator;
  if not found or not v_ativo or v_role not in ('autorizador', 'admin') then
    raise exception 'Apenas autorizador ou admin ativo registra abastecimento ou manutenção.'
      using errcode = 'AG002';
  end if;
end;
$$;

revoke all on function privado.exigir_registrador_ativo(uuid) from public, anon, authenticated, service_role;

-- Serializa por veiculo e confere o odometro. Chamada pelos gatilhos BEFORE INSERT.
create or replace function privado.exigir_odometro(p_veiculo_id uuid, p_km integer)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_max integer;
begin
  if p_veiculo_id is null or p_km is null then
    return;  -- NOT NULL responde
  end if;
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.odometro:' || p_veiculo_id::text, 0));
  -- Leitura depois do lock: enxerga o que a transacao concorrente ja comitou.
  select max(km) into v_max from public.odometro_registros_vigentes where veiculo_id = p_veiculo_id;
  if v_max is not null and p_km < v_max then
    raise exception 'O km informado é menor que o último registrado para o veículo.'
      using errcode = 'AG015';
  end if;
end;
$$;

revoke all on function privado.exigir_odometro(uuid, integer) from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 3. abastecimentos_validar (BEFORE INSERT)
-- ---------------------------------------------------------------------------
create or replace function public.abastecimentos_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_veiculo_da_requisicao uuid;
begin
  new.registrado_em := clock_timestamp();  -- carimbo do servidor

  -- Campos NOT NULL ausentes: o proprio NOT NULL responde (a linha so tem ids e numeros).
  if new.veiculo_id is null or new.km is null or new.registrado_por is null then
    return new;
  end if;

  -- Precedencia do km negativo sobre AG015 (o CHECK responderia depois).
  if new.km < 0 then
    raise exception 'Violação da regra ck_abastecimentos_km_nao_negativo: km não pode ser negativo.'
      using errcode = '23514', schema = 'public', table = 'abastecimentos',
            column = 'km', constraint = 'ck_abastecimentos_km_nao_negativo';
  end if;

  if new.abastecido_em is not null and new.abastecido_em > clock_timestamp() + interval '5 minutes' then
    raise exception 'Violação da regra ck_abastecimentos_data_nao_futura: data no futuro.'
      using errcode = '23514', schema = 'public', table = 'abastecimentos',
            column = 'abastecido_em', constraint = 'ck_abastecimentos_data_nao_futura';
  end if;

  if new.requisicao_id is not null then
    select veiculo_id into v_veiculo_da_requisicao from public.requisicoes where id = new.requisicao_id;
    if found and v_veiculo_da_requisicao is distinct from new.veiculo_id then
      raise exception 'A requisição informada pertence a outro veículo.' using errcode = 'AG016';
    end if;
    -- requisicao inexistente: a FK responde (23503)
  end if;

  perform privado.exigir_registrador_ativo(new.registrado_por);
  perform privado.exigir_odometro(new.veiculo_id, new.km);  -- por ultimo: toma o advisory
  return new;
end;
$$;

revoke all on function public.abastecimentos_validar() from public, anon, authenticated, service_role;

drop trigger if exists trg_abastecimentos_validar on public.abastecimentos;
create trigger trg_abastecimentos_validar
  before insert on public.abastecimentos
  for each row execute function public.abastecimentos_validar();
alter table public.abastecimentos enable always trigger trg_abastecimentos_validar;

-- ---------------------------------------------------------------------------
-- 4. manutencoes_validar (BEFORE INSERT): descricao e texto livre => erros sem conteudo
-- ---------------------------------------------------------------------------
create or replace function public.manutencoes_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  new.registrado_em := clock_timestamp();  -- carimbo do servidor

  -- Campos obrigatorios (NOT NULL). Mensagem fixa: so o nome do campo, nunca valores.
  if new.id is null then
    raise exception 'Campo obrigatório da manutenção ausente: id.'
      using errcode = '23502', schema = 'public', table = 'manutencoes', column = 'id';
  end if;
  if new.veiculo_id is null then
    raise exception 'Campo obrigatório da manutenção ausente: veiculo_id.'
      using errcode = '23502', schema = 'public', table = 'manutencoes', column = 'veiculo_id';
  end if;
  if new.tipo is null then
    raise exception 'Campo obrigatório da manutenção ausente: tipo.'
      using errcode = '23502', schema = 'public', table = 'manutencoes', column = 'tipo';
  end if;
  if new.realizada_em is null then
    raise exception 'Campo obrigatório da manutenção ausente: realizada_em.'
      using errcode = '23502', schema = 'public', table = 'manutencoes', column = 'realizada_em';
  end if;
  if new.km is null then
    raise exception 'Campo obrigatório da manutenção ausente: km.'
      using errcode = '23502', schema = 'public', table = 'manutencoes', column = 'km';
  end if;
  if new.registrado_por is null then
    raise exception 'Campo obrigatório da manutenção ausente: registrado_por.'
      using errcode = '23502', schema = 'public', table = 'manutencoes', column = 'registrado_por';
  end if;

  -- Mesmas regras dos CHECKs, em ordem alfabetica dos nomes, sem DETAIL.
  if new.descricao is not null and (char_length(new.descricao) > 200 or new.descricao !~ '\S') then
    raise exception 'Violação da regra ck_manutencoes_descricao: até 200 caracteres e com texto.'
      using errcode = '23514', schema = 'public', table = 'manutencoes',
            column = 'descricao', constraint = 'ck_manutencoes_descricao';
  end if;
  if new.km < 0 then
    raise exception 'Violação da regra ck_manutencoes_km_nao_negativo: km não pode ser negativo.'
      using errcode = '23514', schema = 'public', table = 'manutencoes',
            column = 'km', constraint = 'ck_manutencoes_km_nao_negativo';
  end if;
  if new.valor is not null and new.valor < 0 then
    raise exception 'Violação da regra ck_manutencoes_valor_nao_negativo: valor não pode ser negativo.'
      using errcode = '23514', schema = 'public', table = 'manutencoes',
            column = 'valor', constraint = 'ck_manutencoes_valor_nao_negativo';
  end if;
  if new.realizada_em > clock_timestamp() + interval '5 minutes' then
    raise exception 'Violação da regra ck_manutencoes_data_nao_futura: data no futuro.'
      using errcode = '23514', schema = 'public', table = 'manutencoes',
            column = 'realizada_em', constraint = 'ck_manutencoes_data_nao_futura';
  end if;

  perform privado.exigir_registrador_ativo(new.registrado_por);
  perform privado.exigir_odometro(new.veiculo_id, new.km);  -- por ultimo: toma o advisory
  return new;
end;
$$;

revoke all on function public.manutencoes_validar() from public, anon, authenticated, service_role;

drop trigger if exists trg_manutencoes_validar on public.manutencoes;
create trigger trg_manutencoes_validar
  before insert on public.manutencoes
  for each row execute function public.manutencoes_validar();
alter table public.manutencoes enable always trigger trg_manutencoes_validar;

-- ---------------------------------------------------------------------------
-- 5. Anulacoes (BEFORE INSERT): carimbo do servidor e ator autorizador/admin ativo.
-- A funcao serve as duas tabelas (mesmas colunas anulado_por/anulado_em). Sem texto livre.
-- Anular nao toma o advisory do odometro: remover um ponto nunca quebra a sequencia.
-- ---------------------------------------------------------------------------
create or replace function public.anulacoes_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  new.anulado_em := clock_timestamp();
  if new.anulado_por is null then
    return new;  -- NOT NULL responde
  end if;
  perform privado.exigir_registrador_ativo(new.anulado_por);
  return new;
end;
$$;

revoke all on function public.anulacoes_validar() from public, anon, authenticated, service_role;

drop trigger if exists trg_abastecimentos_anulacoes_validar on public.abastecimentos_anulacoes;
create trigger trg_abastecimentos_anulacoes_validar
  before insert on public.abastecimentos_anulacoes
  for each row execute function public.anulacoes_validar();
alter table public.abastecimentos_anulacoes enable always trigger trg_abastecimentos_anulacoes_validar;

drop trigger if exists trg_manutencoes_anulacoes_validar on public.manutencoes_anulacoes;
create trigger trg_manutencoes_anulacoes_validar
  before insert on public.manutencoes_anulacoes
  for each row execute function public.anulacoes_validar();
alter table public.manutencoes_anulacoes enable always trigger trg_manutencoes_anulacoes_validar;

-- ---------------------------------------------------------------------------
-- 6. Trava: sem EXECUTE para a API, SECURITY DEFINER com search_path vazio, gatilhos ativos.
-- ---------------------------------------------------------------------------
do $$
declare
  f text;
  r text;
begin
  foreach f in array array['privado.exigir_registrador_ativo(uuid)', 'privado.exigir_odometro(uuid, integer)',
                           'public.abastecimentos_validar()', 'public.manutencoes_validar()',
                           'public.anulacoes_validar()']
  loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if has_function_privilege(r, f::regprocedure, 'execute') then
        raise exception '% ainda executa %.', r, f;
      end if;
    end loop;
    if exists (select 1 from pg_proc p,
                      aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
                where p.oid = f::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE') then
      raise exception 'PUBLIC ainda executa %.', f;
    end if;
    if not exists (select 1 from pg_proc
                    where oid = f::regprocedure and prosecdef and proconfig is not null
                      and position('search_path=' in array_to_string(proconfig, ',')) > 0) then
      raise exception '% sem SECURITY DEFINER ou search_path fixo.', f;
    end if;
  end loop;
end
$$;
