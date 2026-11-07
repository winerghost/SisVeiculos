-- Migration 13: sem SELECT direto pela API (M2) + ator na auditoria administrativa (M5).
-- Nao edita migrations anteriores: usa revoke/drop e create or replace
-- (create or replace preserva a ACL; revoke repetidos por seguranca).
--
-- ---------------------------------------------------------------------------
-- M2. Front nao le dados direto do PostgREST
-- ---------------------------------------------------------------------------
-- DECISAO: regra de ouro do projeto = o backend manda, o frontend so exibe; todos os
-- dados passam pelo Flask (service_role). A anon key embutida no front serve SOMENTE
-- para login (Supabase Auth). Por isso a leitura por papel da migration 9 (defesa em
-- profundidade) foi REMOVIDA DE PROPOSITO: com ela, um usuario com JWT valido poderia
-- consultar o PostgREST diretamente e contornar o que so o backend impoe (MFA exigido
-- no backend, rate limit, filtros de dono, minimo necessario da LGPD).
-- Efeito: authenticated e anon nao tem nenhum privilegio nas tabelas (SELECT negado
-- com 42501 para todos os papeis, inclusive admin); sem USAGE em privado nem EXECUTE em
-- privado.papel_atual(). Permanecem RLS ligado e as politicas explicitas de negacao.
-- Verificado: nenhum trigger/funcao depende desses grants. Todas as funcoes de
-- trigger e de dominio sao SECURITY DEFINER (rodam como dono) e privado.papel_atual()
-- so era usada pelas politicas removidas aqui. A funcao papel_atual() fica
-- sem uso e sem acesso (nao e apagada para nao mexer em objeto de migration anterior).
--
-- ---------------------------------------------------------------------------
-- M5. Ator na auditoria de perfis (troca de unidade) e veiculos (criacao/alteracao)
-- ---------------------------------------------------------------------------
-- Mesmo padrao das migrations 7/8: coluna de transporte alterado_por, validada pelo
-- trigger BEFORE com privado.exigir_admin_ativo e ZERADA na linha (nunca persiste).
--   * perfis.alterado_por (ja existe): agora tambem exigido na troca de unidade.
--   * veiculos.alterado_por (nova): exigido na criacao e em qualquer alteracao de
--     descricao, placa ou ativo.
-- COMPORTAMENTO QUANDO NULA (decisao): ausente, inativa ou nao-admin => recusado com
-- AG002. Coerente com "toda acao administrativa vai para auditoria": nao existe mais
-- troca de unidade nem criacao/alteracao de veiculo sem admin ativo identificado.
-- NAO muda: criacao de perfil (nao e troca de unidade; segue a regra da migration 12),
-- consumir_convite, o seed, update de perfil sem mudar unidade/role/ativo (ex.: nome)
-- e o veiculo de exemplo (inserido/desativado ANTES desta migration, nao e reprocessado).
-- O ator chega ao trigger AFTER de auditoria por variavel de sessao local por registro
-- (sisveiculos.ator_unidade_<id> e sisveiculos.ator_veiculo_<id>), como a migration 12
-- fez para 'perfil_criado'; o AFTER a consome e grava UMA linha com ator_id = o ator.
-- Licoes da migration 12 respeitadas: sem FOR SHARE novo na linha do ator (so o lock
-- consultivo compartilhado dentro de exigir_admin_ativo, mesma chave de sempre). Na
-- troca de unidade em perfis o guarda pega o lock EXCLUSIVO (como nas mudancas de
-- role/ativo) ANTES de exigir_admin_ativo, evitando promover compartilhado -> exclusivo
-- na mesma transacao. Em veiculos so ha o compartilhado (nada exclusivo no caminho).
-- Limite herdado: o backend nao deve misturar, na mesma transacao, alteracao de veiculo
-- (compartilhado) com alteracao de role/ativo/unidade de perfil (exclusivo).
-- Erros: AG002 responsavel ausente/inativo/nao admin.
--
-- PENDENTES (decisao do usuario; NAO tratados aqui):
--  autoautorizacao (autorizador autorizar a propria requisicao), B6.
--  M8: o veiculo de exemplo e FICTICIO (placa AAA0A00, desativado na migration 10); nada a fazer.
--  M4 (seed no pipeline) segue como anotado na migration 12.
--
-- Testes: supabase/tests/test_correcoes_revisao_4.py (novo); ajustes em test_rls_idor.py,
-- test_correcoes_revisao_2.py, test_correcoes_seguranca.py e dbhelpers.py.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- M2. Remocao da leitura por papel
-- ---------------------------------------------------------------------------
drop policy if exists perfis_leitura        on public.perfis;
drop policy if exists veiculos_leitura      on public.veiculos;
drop policy if exists requisicoes_leitura   on public.requisicoes;
drop policy if exists auditoria_leitura     on public.auditoria;
drop policy if exists configuracoes_leitura on public.configuracoes;

-- Nenhum privilegio de tabela para a API (convites ja nao tinha; repetido).
revoke all on table public.perfis, public.veiculos, public.requisicoes,
                    public.auditoria, public.configuracoes, public.convites
  from anon, authenticated;

revoke execute on function privado.papel_atual() from public, anon, authenticated;
revoke usage on schema privado from public, anon, authenticated;

-- Negacao explicita de leitura tambem para authenticated (anon e convites ja tinham, migration 10).
do $$
declare
  t text;
begin
  foreach t in array array['perfis', 'veiculos', 'requisicoes', 'auditoria', 'configuracoes']
  loop
    execute format('drop policy if exists %I on public.%I', t || '_authenticated_sem_leitura', t);
    execute format('create policy %I on public.%I for select to authenticated using (false)',
                   t || '_authenticated_sem_leitura', t);
  end loop;
end
$$;

-- RLS segue ligado em todas (idempotente; nunca desabilitar).
alter table public.perfis        enable row level security;
alter table public.veiculos      enable row level security;
alter table public.requisicoes   enable row level security;
alter table public.auditoria     enable row level security;
alter table public.configuracoes enable row level security;
alter table public.convites      enable row level security;

-- Trava de seguranca: a migration falha se sobrar qualquer acesso direto da API.
do $$
declare
  t text;
begin
  foreach t in array array['perfis', 'veiculos', 'requisicoes', 'auditoria', 'configuracoes', 'convites']
  loop
    if has_table_privilege('authenticated', 'public.' || t, 'select')
       or has_table_privilege('anon', 'public.' || t, 'select') then
      raise exception 'public.% ainda permite SELECT a anon/authenticated.', t;
    end if;
  end loop;
  if has_schema_privilege('authenticated', 'privado', 'usage')
     or has_function_privilege('authenticated', 'privado.papel_atual()', 'execute') then
    raise exception 'authenticated ainda acessa o schema privado.';
  end if;
end
$$;

-- ---------------------------------------------------------------------------
-- M5. veiculos: coluna de transporte + validacao (BEFORE) + auditoria com ator (AFTER)
-- ---------------------------------------------------------------------------
alter table public.veiculos add column if not exists alterado_por uuid;

comment on column public.veiculos.alterado_por is
  'Transporte: admin responsavel pela criacao/alteracao. Validado e zerado pelo trigger; nunca persiste.';

create or replace function public.veiculos_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator uuid := new.alterado_por;
begin
  new.alterado_por := null;  -- nunca persiste

  if tg_op = 'UPDATE' then
    -- Nada auditavel mudou: nao e acao administrativa.
    if new.descricao is not distinct from old.descricao
       and new.placa is not distinct from old.placa
       and new.ativo is not distinct from old.ativo then
      return new;
    end if;
  end if;

  -- Criacao/alteracao: responsavel precisa ser admin ativo (AG002 se nulo).
  perform privado.exigir_admin_ativo(v_ator);
  -- Informa o ator ao trigger de auditoria (AFTER), por veiculo.
  perform set_config('sisveiculos.ator_veiculo_' || replace(new.id::text, '-', ''),
                     v_ator::text, true);
  return new;
end;
$$;

revoke all on function public.veiculos_validar() from public, anon, authenticated;

drop trigger if exists trg_veiculos_validar on public.veiculos;
create trigger trg_veiculos_validar
  before insert or update on public.veiculos
  for each row execute function public.veiculos_validar();
alter table public.veiculos enable always trigger trg_veiculos_validar;

create or replace function public.veiculos_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_campos text[];
  v_chave  text := 'sisveiculos.ator_veiculo_' || replace(new.id::text, '-', '');
  v_ator   uuid := nullif(current_setting('sisveiculos.ator_veiculo_' || replace(new.id::text, '-', ''), true), '')::uuid;begin
  if v_ator is not null then
    perform set_config(v_chave, '', true);  -- consumido
  end if;

  if tg_op = 'INSERT' then
    insert into public.auditoria (tabela, registro_id, acao, ator_id)
    values ('veiculos', new.id, 'veiculo_criado', v_ator);
    return null;
  end if;

  v_campos := array_remove(array[
    case when new.descricao is distinct from old.descricao then 'descricao' end,
    case when new.placa     is distinct from old.placa     then 'placa' end,
    case when new.ativo     is distinct from old.ativo     then 'ativo' end
  ], null);

  if coalesce(array_length(v_campos, 1), 0) > 0 then
    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
    values ('veiculos', new.id, 'veiculo_alterado', v_ator,
            case when new.ativo is distinct from old.ativo then old.ativo::text end,
            case when new.ativo is distinct from old.ativo then new.ativo::text end,
            jsonb_build_object('campos', to_jsonb(v_campos)));
  end if;
  return null;
end;
$$;

revoke all on function public.veiculos_auditar() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- M5. perfis: troca de unidade exige admin ativo (guarda) e grava o ator (auditoria)
-- ---------------------------------------------------------------------------
create or replace function public.perfis_guarda_admin()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator         uuid := new.alterado_por;
  v_outros       integer;
  v_muda_papel   boolean;
  v_muda_unidade boolean;
begin
  new.alterado_por := null;  -- nunca persiste

  if tg_op = 'INSERT' then
    if new.role = 'admin' then
      -- Mesma chave da migration 7 e do seed: serializa criacao e alteracao de admins.
      perform pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0));
      if exists (select 1 from public.perfis where role = 'admin' and ativo) then
        -- Havendo admin ativo, o responsavel precisa ser um deles.
        perform privado.exigir_admin_ativo(v_ator);
        -- Informa o ator a perfis_auditar (AFTER), por perfil.
        perform set_config('sisveiculos.ator_criacao_' || replace(new.id::text, '-', ''),
                           v_ator::text, true);
      elsif coalesce(current_setting('sisveiculos.seed_em_curso', true), '') <> '1' then
        -- Bootstrap (sem admin ativo) so durante privado.semear_primeiro_admin.
        raise exception 'O primeiro administrador só pode ser criado pelo seed.' using errcode = 'AG002';
      end if;
    end if;
    return new;
  end if;

  if new.id is distinct from old.id then
    raise exception 'O id do perfil é imutável.' using errcode = 'AG004';
  end if;

  v_muda_papel   := new.role is distinct from old.role or new.ativo is distinct from old.ativo;
  v_muda_unidade := new.unidade is distinct from old.unidade;

  -- Nem papel nem unidade mudaram (ex.: so o nome): nao e acao administrativa.
  if not v_muda_papel and not v_muda_unidade then
    return new;
  end if;

  -- Serializa alteracoes de papel/ativo/unidade (mesma chave usada no seed). Exclusivo
  -- ANTES de exigir_admin_ativo (que pede o compartilhado): sem promocao de lock.
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0));

  perform privado.exigir_admin_ativo(v_ator);

  if v_muda_papel then
    if v_ator = old.id then
      raise exception 'O administrador não pode alterar o próprio papel nem desativar a si mesmo.'
        using errcode = 'AG008';
    end if;

    -- Deixaria de existir admin ativo alem deste?
    if old.role = 'admin' and old.ativo and not (new.role = 'admin' and new.ativo) then
      select count(*) into v_outros
        from public.perfis
       where role = 'admin' and ativo and id <> old.id;
      if v_outros = 0 then
        raise exception 'Deve existir ao menos um administrador ativo.' using errcode = 'AG009';
      end if;
    end if;

    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para)
    values ('perfis', old.id, 'perfil_alterado', v_ator,
            old.role::text || '/' || case when old.ativo then 'ativo' else 'inativo' end,
            new.role::text || '/' || case when new.ativo then 'ativo' else 'inativo' end);
  end if;

  if v_muda_unidade then
    -- Informa o ator a perfis_auditar (AFTER), por perfil.
    perform set_config('sisveiculos.ator_unidade_' || replace(new.id::text, '-', ''),
                       v_ator::text, true);
  end if;

  return new;
end;
$$;

revoke all on function public.perfis_guarda_admin() from public, anon, authenticated;

create or replace function public.perfis_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_chave text;
  v_ator  uuid;
begin
  if tg_op = 'INSERT' then
    v_chave := 'sisveiculos.ator_criacao_' || replace(new.id::text, '-', '');
    v_ator := nullif(current_setting(v_chave, true), '')::uuid;
    if v_ator is not null then
      perform set_config(v_chave, '', true);  -- consumido
    end if;
    insert into public.auditoria (tabela, registro_id, acao, ator_id, para)
    values ('perfis', new.id, 'perfil_criado', v_ator,
            new.role::text || '/' || case when new.ativo then 'ativo' else 'inativo' end);
  elsif new.unidade is distinct from old.unidade then
    -- role/ativo sao auditados (com ator) pelo guarda; aqui so a unidade, com o ator do guarda.
    v_chave := 'sisveiculos.ator_unidade_' || replace(new.id::text, '-', '');
    v_ator := nullif(current_setting(v_chave, true), '')::uuid;
    if v_ator is not null then
      perform set_config(v_chave, '', true);  -- consumido
    end if;
    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para)
    values ('perfis', new.id, 'perfil_unidade_alterada', v_ator, old.unidade::text, new.unidade::text);
  end if;
  return null;
end;
$$;

revoke all on function public.perfis_auditar() from public, anon, authenticated;
