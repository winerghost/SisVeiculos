-- Migration: codigo publico OPACO do veiculo (conteudo do QR Code, Fase 3 de docs/plano-recursos-web-nativos.md).
-- Nova migration; nao edita as anteriores. Idempotente (reaplicavel). Depende de 20261107121300 (veiculos_validar,
-- alterado_por) e 20261107121400 (travar_admin_comando, exigir_admin_ativo).
--
-- Motivo: o QR impresso no veiculo precisa de um identificador que NAO exponha o uuid interno (id) nem dado pessoal.
-- `veiculos.codigo_publico` so IDENTIFICA o veiculo: NAO e segredo de autorizacao nem credencial. Ler o codigo
-- nao autoriza nada; o backend continua exigindo JWT, papel e `acoes_permitidas` (404 uniforme na busca).
--
-- 1. COLUNA codigo_publico text NOT NULL, default gerado no servidor: replace(gen_random_uuid()::text, '-', ''),
--    32 caracteres [0-9a-f] (122 bits aleatorios do CSPRNG do Postgres). Escolha: gen_random_uuid() e nativa
--    (core, PG13+; as demais migrations ja a usam), dispensa extensao (pgcrypto/gen_random_bytes vive no schema
--    `extensions` no Supabase e exigiria qualificar o schema) e nao depende de search_path.
--    CHECK ck_veiculos_codigo_publico_formato: ^[a-z0-9]{16,32}$ (folga p/ mudar o gerador sem migration).
--    Indice unico ux_veiculos_codigo_publico.
--    BACKFILL sem lock longo: ADD COLUMN nullable sem default (so catalogo, sem reescrever a tabela), depois o
--    default (so catalogo), UPDATE das linhas sem codigo, CHECK NOT VALID + VALIDATE (so SHARE UPDATE EXCLUSIVE)
--    e, por ultimo, SET NOT NULL (um CHECK passa com NULL, entao o NOT NULL e necessario; varre a tabela uma vez,
--    aceitavel: a tabela da frota e minuscula). lock_timeout de 5 s: falha em vez de enfileirar escritas.
--    O UPDATE do backfill roda ANTES do gatilho de imutabilidade existir.
--
-- 2. IMUTAVEL por UPDATE comum: gatilho BEFORE UPDATE (ENABLE ALWAYS) recusa qualquer mudanca em codigo_publico
--    com AG003 (operacao proibida; mensagem fixa, sem valores), exceto quando a RPC de rotacao marca a transacao
--    (variavel local sisveiculos.rotacao_codigo_<id> = '1', consumida pelo gatilho). Mesmo limite herdado das
--    migrations 12/13: SQL livre como postgres/service_role com acesso a set_config poderia forjar a marca; o
--    service_role da API NAO tem UPDATE direto na tabela (RLS/grants inalterados), so executa a RPC.
--    veiculos_validar NAO muda: ela so age quando descricao/placa/ativo mudam; sozinha nao protegeria o codigo.
--
-- 3. ROTACAO SOMENTE por public.rotacionar_codigo_veiculo(p_veiculo, p_ator) -> text (novo codigo):
--    SECURITY DEFINER, search_path vazio, EXECUTE so para service_role. p_ator = admin ativo validado no
--    backend (JWT); segunda barreira: privado.exigir_admin_ativo (AG002; toma o advisory de admins, o mesmo do
--    gatilho de comando em veiculos, entao nao ha inversao de ordem de lock). Veiculo inexistente: P0002 (mensagem
--    fixa). Argumento nulo: 23502. Gera o novo codigo e grava UMA linha em auditoria:
--      tabela 'veiculos', registro_id = veiculo, acao 'veiculo_alterado' (ck_auditoria_acao NAO muda),
--      ator_id = p_ator, de/para NULL, detalhes = {"campos": ["codigo_publico"], "rotacao": true}.
--    NUNCA grava o codigo antigo nem o novo (so o fato da rotacao). Efeito: QR impresso com o codigo antigo
--    deixa de funcionar (a busca por codigo passa a dar 404); o backend devolve o novo codigo ao admin.
--
-- Sem DELETE fisico (gatilhos existentes). RLS e grants da tabela inalterados (deny-by-default).
-- Desligar o recurso leitor_qr_veiculos nunca apaga o codigo (so a API recusa o endpoint).
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

set local lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 1. Coluna, backfill, CHECK, NOT NULL e indice unico
-- ---------------------------------------------------------------------------
alter table public.veiculos add column if not exists codigo_publico text;

alter table public.veiculos
  alter column codigo_publico set default replace(gen_random_uuid()::text, '-', '');

-- Backfill: gen_random_uuid() e volatil, entao cada linha recebe um valor proprio.
update public.veiculos
   set codigo_publico = replace(gen_random_uuid()::text, '-', '')
 where codigo_publico is null;

alter table public.veiculos drop constraint if exists ck_veiculos_codigo_publico_formato;
alter table public.veiculos
  add constraint ck_veiculos_codigo_publico_formato
  check (codigo_publico ~ '^[a-z0-9]{16,32}$') not valid;
alter table public.veiculos validate constraint ck_veiculos_codigo_publico_formato;

alter table public.veiculos alter column codigo_publico set not null;

create unique index if not exists ux_veiculos_codigo_publico
  on public.veiculos (codigo_publico);

comment on column public.veiculos.codigo_publico is
  'Identificador publico OPACO do veiculo (conteudo do QR). NAO e segredo de autorizacao nem credencial: so identifica o veiculo; ler o codigo nao autoriza nada. Gerado no servidor (aleatorio), imutavel por UPDATE comum; so rotacionar_codigo_veiculo troca (e audita o fato, sem o valor).';

-- ---------------------------------------------------------------------------
-- 2. Imutabilidade por UPDATE comum
-- ---------------------------------------------------------------------------
create or replace function public.veiculos_codigo_publico_guarda()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_chave text := 'sisveiculos.rotacao_codigo_' || replace(new.id::text, '-', '');
begin
  if coalesce(current_setting(v_chave, true), '') = '1' then
    perform set_config(v_chave, '', true);  -- consumida: vale para um unico UPDATE
    return new;
  end if;
  raise exception 'O código público do veículo só pode ser trocado pela rotação.'
    using errcode = 'AG003', schema = 'public', table = 'veiculos', column = 'codigo_publico';
end;
$$;

revoke all on function public.veiculos_codigo_publico_guarda()
  from public, anon, authenticated, service_role;

drop trigger if exists trg_veiculos_codigo_publico_guarda on public.veiculos;
create trigger trg_veiculos_codigo_publico_guarda
  before update on public.veiculos
  for each row
  when (old.codigo_publico is distinct from new.codigo_publico)
  execute function public.veiculos_codigo_publico_guarda();
alter table public.veiculos enable always trigger trg_veiculos_codigo_publico_guarda;

-- ---------------------------------------------------------------------------
-- 3. RPC de rotacao (unico caminho para trocar o codigo)
-- ---------------------------------------------------------------------------
create or replace function public.rotacionar_codigo_veiculo(p_veiculo uuid, p_ator uuid)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_novo text := replace(gen_random_uuid()::text, '-', '');
begin
  if p_veiculo is null then
    raise exception 'Campo obrigatório ausente: veiculo.'
      using errcode = '23502', schema = 'public', table = 'veiculos', column = 'id';
  end if;

  -- Responsavel: admin ativo (AG002 se nulo/invalido). Toma o advisory de admins (reentrante).
  perform privado.exigir_admin_ativo(p_ator);

  -- Marca a transacao para o gatilho de imutabilidade liberar ESTE update.
  perform set_config('sisveiculos.rotacao_codigo_' || replace(p_veiculo::text, '-', ''), '1', true);

  update public.veiculos set codigo_publico = v_novo where id = p_veiculo;
  if not found then
    perform set_config('sisveiculos.rotacao_codigo_' || replace(p_veiculo::text, '-', ''), '', true);
    raise exception 'Veículo inexistente.' using errcode = 'P0002';
  end if;

  -- Auditoria: so o fato da rotacao. Sem codigo antigo nem novo.
  insert into public.auditoria (tabela, registro_id, acao, ator_id, detalhes)
  values ('veiculos', p_veiculo, 'veiculo_alterado', p_ator,
          jsonb_build_object('campos', jsonb_build_array('codigo_publico'), 'rotacao', true));

  return v_novo;
end;
$$;

revoke all on function public.rotacionar_codigo_veiculo(uuid, uuid) from public, anon, authenticated;
grant execute on function public.rotacionar_codigo_veiculo(uuid, uuid) to service_role;

-- ---------------------------------------------------------------------------
-- Autoverificacao: a migration falha se algo ficar fora do contrato
-- ---------------------------------------------------------------------------
do $$
declare
  f text := 'public.rotacionar_codigo_veiculo(uuid, uuid)';
  r text;
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'veiculos'
                    and column_name = 'codigo_publico' and data_type = 'text' and is_nullable = 'NO'
                    and column_default is not null) then
    raise exception 'veiculos.codigo_publico deve ser text not null com default.';
  end if;
  if exists (select 1 from public.veiculos where codigo_publico is null
                or codigo_publico !~ '^[a-z0-9]{16,32}$') then
    raise exception 'veiculos com codigo_publico invalido apos o backfill.';
  end if;
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.veiculos'::regclass
                    and conname = 'ck_veiculos_codigo_publico_formato' and convalidated) then
    raise exception 'ck_veiculos_codigo_publico_formato ausente ou nao validado.';
  end if;
  if not exists (select 1 from pg_indexes
                  where schemaname = 'public' and indexname = 'ux_veiculos_codigo_publico'
                    and indexdef like 'CREATE UNIQUE INDEX%') then
    raise exception 'ux_veiculos_codigo_publico ausente ou nao unico.';
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.veiculos'::regclass
                  and tgname = 'trg_veiculos_codigo_publico_guarda' and tgenabled = 'A' and not tgisinternal) then
    raise exception 'trg_veiculos_codigo_publico_guarda ausente ou nao ENABLE ALWAYS.';
  end if;

  foreach r in array array['anon', 'authenticated'] loop
    if has_function_privilege(r, f::regprocedure, 'execute') then
      raise exception '% executa %.', r, f;
    end if;
  end loop;
  if not has_function_privilege('service_role', f::regprocedure, 'execute') then
    raise exception 'service_role nao executa %.', f;
  end if;
  if exists (select 1 from pg_proc x, aclexplode(coalesce(x.proacl, acldefault('f', x.proowner))) a
              where x.oid = f::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE') then
    raise exception 'PUBLIC executa %.', f;
  end if;
  if not exists (select 1 from pg_proc x
                  where x.oid = f::regprocedure and x.prosecdef
                    and array_to_string(x.proconfig, ',') like '%search_path=%') then
    raise exception '% sem SECURITY DEFINER ou search_path fixo.', f;
  end if;

  f := 'public.veiculos_codigo_publico_guarda()';
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if has_function_privilege(r, f::regprocedure, 'execute') then
      raise exception '% executa %.', r, f;
    end if;
  end loop;

  -- RLS e grants da tabela inalterados (deny-by-default).
  if not (select relrowsecurity from pg_class where oid = 'public.veiculos'::regclass) then
    raise exception 'RLS desligado em public.veiculos.';
  end if;
end
$$;
