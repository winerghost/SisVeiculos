-- Migration 7: sempre ao menos um admin ativo + sem auto-rebaixamento/auto-desativacao.
-- Motivo: a regra vale no banco, mesmo para escrita direta com service_role.
-- Como o Flask usa service_role (auth.uid() nulo), o responsavel vem de uma coluna
-- de transporte da propria linha, como na migration 5: perfis.alterado_por.
--   * Qualquer mudanca de role ou ativo exige alterado_por = admin ativo.
--   * alterado_por = o proprio perfil alterado => recusado (AG008).
--   * Se a mudanca tirasse o ultimo admin ativo => recusado (AG009).
-- alterado_por e consumido pelo trigger (volta a NULL ao gravar) para nunca ficar
-- um valor velho valendo como autorizacao numa proxima alteracao. Por isso o
-- backend deve informar alterado_por em TODO update de role/ativo.
-- Concorrencia: dois admins se desativando ao mesmo tempo poderiam deixar zero
-- admins. Um advisory lock transacional serializa essas alteracoes; apos obte-lo,
-- o responsavel e revalidado com dados ja comitados (READ COMMITTED).
-- Observacao: como o responsavel precisa ser OUTRO admin ativo, ele proprio
-- permanece admin; a contagem (AG009) e uma trava adicional de seguranca.
-- Auditoria: toda mudanca de role/ativo gera linha ('perfil_alterado'), com
-- de/para no formato 'role/ativo' ou 'role/inativo', sem dados pessoais.
-- Erros: AG002 responsavel ausente/inativo/nao admin; AG004 id imutavel;
--        AG008 auto-alteracao; AG009 ultimo admin ativo.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

alter table public.perfis add column if not exists alterado_por uuid;

comment on column public.perfis.alterado_por is
  'Transporte: admin responsavel pela alteracao de role/ativo. Validado e zerado pelo trigger; nunca persiste.';

create or replace function public.perfis_guarda_admin()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator   uuid := new.alterado_por;
  v_outros integer;
begin
  new.alterado_por := null;  -- nunca persiste

  if tg_op = 'INSERT' then
    return new;
  end if;

  if new.id is distinct from old.id then
    raise exception 'O id do perfil é imutável.' using errcode = 'AG004';
  end if;

  -- Nada de role/ativo mudou: nao e acao administrativa sobre papel.
  if new.role is not distinct from old.role and new.ativo is not distinct from old.ativo then
    return new;
  end if;

  -- Serializa alteracoes de papel/ativo (mesma chave usada no seed, migration 9).
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0));

  perform privado.exigir_admin_ativo(v_ator);

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

  return new;
end;
$$;

revoke all on function public.perfis_guarda_admin() from public, anon, authenticated;

drop trigger if exists trg_perfis_guarda_admin on public.perfis;
create trigger trg_perfis_guarda_admin
  before insert or update on public.perfis
  for each row execute function public.perfis_guarda_admin();
