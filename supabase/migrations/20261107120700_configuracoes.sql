-- Migration 8: configuracoes (recursos opcionais).
-- Motivo: quilometragem, abastecimento/manutencao, checklist e MFA ficam DESLIGADOS
-- por padrao e so o admin liga/desliga. O backend le esta tabela como fonte da
-- verdade; desligar nunca apaga dados ja gravados (esta tabela so guarda flags).
-- Modelagem: linha unica (id boolean fixo em true) com uma coluna por recurso.
-- Permite CHECK de dependencia no proprio banco; novo recurso = nova migration.
-- Dependencia: abastecimento_manutencao exige quilometragem (CHECK + AG010 amigavel).
-- Responsavel pela alteracao: coluna de transporte alterado_por (admin ativo),
-- validada e zerada pelo trigger, como em perfis (migration 7).
-- Auditoria: cada recurso alterado gera uma linha ('configuracao_alterada') com
-- de/para ('true'/'false') e detalhes {"recurso": "..."}.
-- Sem DELETE/TRUNCATE fisico. RLS deny-by-default; leitura por papel na migration 9.
-- Erros: AG002 responsavel ausente/inativo/nao admin; AG010 dependencia violada.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

create table if not exists public.configuracoes (
  id                        boolean primary key default true,
  quilometragem             boolean not null default false,
  abastecimento_manutencao  boolean not null default false,
  checklist                 boolean not null default false,
  mfa                       boolean not null default false,
  alterado_por              uuid,
  atualizado_em             timestamptz not null default now(),

  constraint ck_configuracoes_linha_unica check (id),
  constraint ck_configuracoes_abastecimento_exige_km
    check (not abastecimento_manutencao or quilometragem)
);

comment on table public.configuracoes is
  'Recursos opcionais (linha unica). Todos desligados por padrao. Alteracao so por admin ativo e auditada.';
comment on column public.configuracoes.alterado_por is
  'Transporte: admin responsavel. Validado e zerado pelo trigger; nunca persiste.';

-- Linha unica com tudo desligado (idempotente).
insert into public.configuracoes (id) values (true) on conflict (id) do nothing;

drop trigger if exists trg_configuracoes_atualizado_em on public.configuracoes;
create trigger trg_configuracoes_atualizado_em
  before update on public.configuracoes
  for each row execute function public.set_atualizado_em();

drop trigger if exists trg_configuracoes_sem_delete on public.configuracoes;
create trigger trg_configuracoes_sem_delete
  before delete on public.configuracoes
  for each row execute function public.bloquear_alteracao();
drop trigger if exists trg_configuracoes_sem_truncate on public.configuracoes;
create trigger trg_configuracoes_sem_truncate
  before truncate on public.configuracoes
  for each statement execute function public.bloquear_alteracao();

-- ---------------------------------------------------------------------------
-- Validacao + auditoria (BEFORE UPDATE). A auditoria e gravada aqui, e nao em
-- AFTER, porque alterado_por precisa ser zerado na mesma linha; se o UPDATE
-- falhar depois (ex.: CHECK), a linha de auditoria e desfeita junto.
-- ---------------------------------------------------------------------------
create or replace function public.configuracoes_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator  uuid := new.alterado_por;
  v_chave text;
begin
  new.alterado_por := null;  -- nunca persiste

  if new.quilometragem is not distinct from old.quilometragem
     and new.abastecimento_manutencao is not distinct from old.abastecimento_manutencao
     and new.checklist is not distinct from old.checklist
     and new.mfa is not distinct from old.mfa then
    return new;
  end if;

  perform privado.exigir_admin_ativo(v_ator);

  if new.abastecimento_manutencao and not new.quilometragem then
    raise exception 'Abastecimento e manutenção exigem a Quilometragem ligada.'
      using errcode = 'AG010';
  end if;

  for v_chave in
    select chave
      from jsonb_object_keys(to_jsonb(new)) as chave
     where chave = any (array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa'])
       and (to_jsonb(new) -> chave) is distinct from (to_jsonb(old) -> chave)
  loop
    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
    values ('configuracoes', null, 'configuracao_alterada', v_ator,
            to_jsonb(old) ->> v_chave, to_jsonb(new) ->> v_chave,
            jsonb_build_object('recurso', v_chave));
  end loop;

  return new;
end;
$$;

revoke all on function public.configuracoes_validar() from public, anon, authenticated;

drop trigger if exists trg_configuracoes_validar on public.configuracoes;
create trigger trg_configuracoes_validar
  before update on public.configuracoes
  for each row execute function public.configuracoes_validar();

-- ---------------------------------------------------------------------------
-- RLS deny-by-default: habilitado, sem politicas e sem grants aos papeis da API.
-- ---------------------------------------------------------------------------
alter table public.configuracoes enable row level security;

revoke all on table public.configuracoes from anon, authenticated;
