-- Migration: saudacoes, exclusao fisica SOMENTE de frases rejeitadas. Nova migration; NAO edita a
-- 20261115120100 nem a 20261115120200. Idempotente.
--
-- Motivo: frases rejeitadas sao lixo de moderacao (texto descartado, nunca usado). O admin precisa poder
-- limpa-las. Pendentes e aprovadas continuam sem DELETE (aprovada esta/esteve em uso; pendente aguarda decisao;
-- para tirar uma aprovada de uso, rejeita-se primeiro, o que deixa sua propria linha de auditoria).
--
-- O que muda:
--   1. trg_saudacoes_sem_delete passa a usar public.saudacoes_proteger_delete() em vez de bloquear_alteracao():
--      DELETE de linha com status <> 'rejeitada' -> AG003 (mensagem clara). TRUNCATE segue bloqueado (AG003).
--      O DELETE de rejeitada exige ainda o ator: o marcador transacional `sisveiculos.saudacao_ator` (uuid
--      de admin ativo) so e posto por public.excluir_saudacao(); sem ele -> AG002. Assim o unico caminho de
--      exclusao e o RPC, e toda exclusao sai auditada, na mesma transacao (some junto se falhar).
--   2. ck_auditoria_acao ganha 'saudacao_excluida' (24 valores; os 23 vigentes preservados, lista de
--      20261114120000). ck_auditoria_tabela NAO muda: usa tabela='configuracoes', como criada/aprovada/rejeitada.
--   3. public.excluir_saudacao(p_id uuid, p_ator uuid) -> boolean: SECURITY DEFINER, search_path vazio, EXECUTE
--      so service_role. p_ator precisa ser admin ativo (AG002). Devolve true se apagou, false se o id nao existe.
--   4. service_role recebe DELETE em public.saudacoes (nada a anon/authenticated; RLS ligado e sem policy).
--      TRUNCATE continua revogado.
--
-- Confianca: p_ator e DECLARADO pelo chamador (o RPC valida que e admin ativo, nao que e quem chamou). A
-- seguranca real e que EXECUTE/DELETE existem so para service_role, usado apenas no backend, que obtem o
-- ator do JWT. p_id nulo nao casa nenhuma linha (id = null) e devolve false, sem erro.
--
-- Auditoria: auditoria NAO tem FK para saudacoes (registro_id uuid solto), entao apagar a frase nao quebra
-- nem apaga auditoria; ela continua append-only. Linha gravada: tabela='configuracoes', acao='saudacao_excluida',
-- registro_id = id da frase apagada, ator_id = p_ator, de='rejeitada', para=null, detalhes
-- {"recurso":"saudacao","evento":"excluida","ocasiao":..,"origem":..}. O TEXTO nao vai para a auditoria.
-- As linhas antigas da frase (criada/rejeitada/...) permanecem.
-- Fuso: timestamptz (UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. Auditoria: nova acao 'saudacao_excluida' (preserva as 23 existentes)
-- ---------------------------------------------------------------------------
alter table public.auditoria drop constraint if exists ck_auditoria_acao;
alter table public.auditoria
  add constraint ck_auditoria_acao check (acao in (
    'criada', 'transicao',
    'convite_criado', 'convite_usado', 'convite_revogado',
    'perfil_criado', 'perfil_alterado', 'perfil_unidade_alterada', 'admin_semeado',
    'configuracao_alterada',
    'veiculo_criado', 'veiculo_alterado',
    'acao_administrativa', 'acesso_negado',
    'checklist_registrado',
    'foto_perfil_alterada', 'foto_perfil_removida',
    'relatorio_gerado', 'relatorio_enviado',
    'motorista_designado',
    'localizacao_registrada',
    'localizacao_lida',
    'localizacao_expurgada',
    'saudacao_excluida'
  ));

-- ---------------------------------------------------------------------------
-- 2. Gatilho de DELETE: so rejeitada, so com ator, sempre auditado
-- ---------------------------------------------------------------------------
create or replace function public.saudacoes_proteger_delete()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator uuid;
begin
  if old.status <> 'rejeitada' then
    raise exception 'Só é possível excluir saudação rejeitada (status atual: %).', old.status
      using errcode = 'AG003';
  end if;

  v_ator := nullif(coalesce(current_setting('sisveiculos.saudacao_ator', true), ''), '')::uuid;
  if v_ator is null then
    raise exception 'Exclusão de saudação exige o responsável (use public.excluir_saudacao).'
      using errcode = 'AG002';
  end if;
  perform privado.exigir_admin_ativo(v_ator);

  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
  values ('configuracoes', old.id, 'saudacao_excluida', v_ator, old.status, null,
          jsonb_build_object('recurso', 'saudacao', 'evento', 'excluida',
                             'ocasiao', old.ocasiao, 'origem', old.origem));
  return old;
end;
$$;

revoke all on function public.saudacoes_proteger_delete() from public, anon, authenticated, service_role;

drop trigger if exists trg_saudacoes_sem_delete on public.saudacoes;
create trigger trg_saudacoes_sem_delete
  before delete on public.saudacoes
  for each row execute function public.saudacoes_proteger_delete();
-- Nao pode ser contornado por session_replication_role = replica.
alter table public.saudacoes enable always trigger trg_saudacoes_sem_delete;

-- ---------------------------------------------------------------------------
-- 3. RPC de exclusao (unico caminho)
-- ---------------------------------------------------------------------------
create or replace function public.excluir_saudacao(p_id uuid, p_ator uuid)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_n integer;
begin
  perform privado.exigir_admin_ativo(p_ator);
  -- Variavel LOCAL: vale so nesta transacao e e desligada logo apos o DELETE.
  perform set_config('sisveiculos.saudacao_ator', p_ator::text, true);
  delete from public.saudacoes where id = p_id;
  get diagnostics v_n = row_count;
  perform set_config('sisveiculos.saudacao_ator', '', true);
  return v_n > 0;
end;
$$;

revoke all on function public.excluir_saudacao(uuid, uuid) from public, anon, authenticated;
grant execute on function public.excluir_saudacao(uuid, uuid) to service_role;

comment on function public.excluir_saudacao(uuid, uuid) is
  'Apaga uma saudacao REJEITADA (outro status -> AG003), exige admin ativo (AG002) e grava auditoria saudacao_excluida. true se apagou, false se nao existe.';

-- ---------------------------------------------------------------------------
-- 4. Privilegios e comentario
-- ---------------------------------------------------------------------------
grant delete on table public.saudacoes to service_role;
revoke truncate on table public.saudacoes from service_role;

comment on table public.saudacoes is
  'Frases adicionais de saudacao (catalogo base fica no backend). Entram em uso so se aprovadas por admin. DELETE fisico so de frases rejeitadas, via public.excluir_saudacao (auditado); texto imutavel; toda criacao/decisao/exclusao e auditada.';

-- ---------------------------------------------------------------------------
-- Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v text;
  d text;
begin
  select pg_get_constraintdef(oid) into d from pg_constraint
   where conrelid = 'public.auditoria'::regclass and conname = 'ck_auditoria_acao';
  foreach v in array array[
    'criada', 'transicao', 'convite_criado', 'convite_usado', 'convite_revogado',
    'perfil_criado', 'perfil_alterado', 'perfil_unidade_alterada', 'admin_semeado',
    'configuracao_alterada', 'veiculo_criado', 'veiculo_alterado', 'acao_administrativa',
    'acesso_negado', 'checklist_registrado', 'foto_perfil_alterada', 'foto_perfil_removida',
    'relatorio_gerado', 'relatorio_enviado', 'motorista_designado', 'localizacao_registrada',
    'localizacao_lida', 'localizacao_expurgada', 'saudacao_excluida'] loop
    if d is null or position('''' || v || '''' in d) = 0 then
      raise exception 'ck_auditoria_acao sem o valor %.', v;
    end if;
  end loop;

  if not (select relrowsecurity from pg_class where oid = 'public.saudacoes'::regclass) then
    raise exception 'RLS desligado em public.saudacoes.';
  end if;
  if exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'saudacoes') then
    raise exception 'public.saudacoes nao deve ter policy (deny-by-default).';
  end if;
  if not has_table_privilege('service_role', 'public.saudacoes', 'delete')
     or has_table_privilege('service_role', 'public.saudacoes', 'truncate')
     or has_table_privilege('anon', 'public.saudacoes', 'delete')
     or has_table_privilege('authenticated', 'public.saudacoes', 'delete')
     or has_table_privilege('anon', 'public.saudacoes', 'select')
     or has_table_privilege('authenticated', 'public.saudacoes', 'select') then
    raise exception 'Privilegios incorretos em public.saudacoes.';
  end if;
  if not exists (select 1 from pg_trigger t
                  where t.tgrelid = 'public.saudacoes'::regclass
                    and t.tgname = 'trg_saudacoes_sem_delete'
                    and t.tgenabled = 'A' and not t.tgisinternal
                    and t.tgfoid = to_regprocedure('public.saudacoes_proteger_delete()')) then
    raise exception 'trg_saudacoes_sem_delete ausente, nao ALWAYS ou com funcao errada.';
  end if;
  if has_function_privilege('anon', 'public.excluir_saudacao(uuid,uuid)', 'execute')
     or has_function_privilege('authenticated', 'public.excluir_saudacao(uuid,uuid)', 'execute')
     or not has_function_privilege('service_role', 'public.excluir_saudacao(uuid,uuid)', 'execute') then
    raise exception 'EXECUTE incorreto em public.excluir_saudacao.';
  end if;
end
$$;
