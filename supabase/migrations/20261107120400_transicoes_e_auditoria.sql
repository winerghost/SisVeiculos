-- Migration 5: transicoes de estado + auditoria append-only + sem DELETE fisico.
-- Motivo: a maquina de estados da requisicao e a trilha de auditoria precisam valer
-- no banco, mesmo que alguem escreva direto com service_role.
--
-- Maquina de estados (trigger BEFORE em requisicoes):
--   solicitada -> autorizada | negada | cancelada
--   autorizada -> em_uso | cancelada
--   em_uso     -> concluida
--   negada, cancelada, concluida: terminais (linha congelada).
-- Quem pode agir (o ator vem das proprias colunas da linha, pois o Flask usa
-- service_role e auth.uid() seria nulo):
--   autorizada/negada -> autorizado_por; em_uso -> saida_registrada_por;
--   concluida -> chegada_registrada_por; cancelada -> cancelada_por (nova coluna).
--   Autorizar, negar, registrar saida e chegada: perfil ativo com role 'autorizador'
--   ou 'admin' (decisao do usuario: admin tambem e autorizador). Solicitante nunca.
--   Cancelar: admin ou autorizador (em solicitada ou autorizada); o solicitante so
--   cancela a PROPRIA requisicao e apenas enquanto 'solicitada'.
-- autorizado_por/autorizado_em passam a ser obrigatorios em autorizada, em_uso e concluida.
-- Carimbos (autorizado_em, saida_real_em, chegada_real_em, cancelada_em) sao
-- preenchidos com now() pelo trigger se vierem nulos.
-- Erros com SQLSTATE proprio para a API traduzir:
--   AG001 transicao invalida / requisicao encerrada
--   AG002 ator sem permissao, inativo ou ausente
--   AG003 operacao proibida (DELETE/TRUNCATE fisico, UPDATE em auditoria)
--   AG004 campo imutavel alterado
-- Auditoria: tabela append-only (UPDATE/DELETE/TRUNCATE bloqueados por trigger e
-- sem grant). Toda criacao e toda transicao de requisicao gera uma linha.
-- Sem dados pessoais em auditoria: apenas ids, status e detalhes tecnicos.
-- RLS ligado e deny-by-default; politicas por papel vem em migration propria.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- Novas colunas de cancelamento (quem e quando)
-- ---------------------------------------------------------------------------
alter table public.requisicoes
  add column if not exists cancelada_por uuid references public.perfis (id) on delete restrict,
  add column if not exists cancelada_em  timestamptz;

alter table public.requisicoes drop constraint if exists ck_requisicoes_cancelamento_par;
alter table public.requisicoes
  add constraint ck_requisicoes_cancelamento_par
  check ((cancelada_por is null) = (cancelada_em is null));

-- Coerencia status x colunas, agora com autorizacao obrigatoria e cancelamento.
alter table public.requisicoes drop constraint if exists ck_requisicoes_status_coerente;
alter table public.requisicoes
  add constraint ck_requisicoes_status_coerente
  check (
    case status
      when 'solicitada' then autorizado_por is null and cancelada_por is null
                             and saida_real_em is null and chegada_real_em is null
      when 'autorizada' then autorizado_por is not null and cancelada_por is null
                             and saida_real_em is null and chegada_real_em is null
      when 'negada'     then autorizado_por is not null and cancelada_por is null
                             and saida_real_em is null and chegada_real_em is null
      when 'cancelada'  then cancelada_por is not null
                             and saida_real_em is null and chegada_real_em is null
      when 'em_uso'     then autorizado_por is not null and cancelada_por is null
                             and saida_real_em is not null and chegada_real_em is null
      when 'concluida'  then autorizado_por is not null and cancelada_por is null
                             and saida_real_em is not null and chegada_real_em is not null
      else false
    end
  );

-- ---------------------------------------------------------------------------
-- auditoria (append-only)
-- ---------------------------------------------------------------------------
create table if not exists public.auditoria (
  id          bigint generated always as identity primary key,
  ocorrido_em timestamptz not null default now(),
  tabela      text not null,
  registro_id uuid,
  acao        text not null,
  ator_id     uuid references public.perfis (id) on delete restrict,
  de          text,
  para        text,
  detalhes    jsonb not null default '{}'::jsonb
);

comment on table public.auditoria is
  'Trilha append-only. UPDATE/DELETE/TRUNCATE bloqueados. Sem dados pessoais: so ids, status e detalhes tecnicos.';

create index if not exists ix_auditoria_registro
  on public.auditoria (tabela, registro_id, ocorrido_em);
-- ator_id: checagem da FK e "o que este usuario fez".
create index if not exists ix_auditoria_ator_id on public.auditoria (ator_id);

alter table public.auditoria enable row level security;

revoke all on table public.auditoria from anon, authenticated;
-- Defesa em profundidade: nem o service_role altera ou apaga (so INSERT/SELECT).
revoke update, delete, truncate on table public.auditoria from service_role;

-- ---------------------------------------------------------------------------
-- Bloqueio de alteracao/DELETE/TRUNCATE (append-only e sem DELETE fisico)
-- ---------------------------------------------------------------------------
create or replace function public.bloquear_alteracao()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'Operação % proibida em %: registros nunca são alterados/apagados fisicamente.',
    tg_op, tg_table_name
    using errcode = 'AG003';
end;
$$;

revoke all on function public.bloquear_alteracao() from public, anon, authenticated;

-- auditoria: nada de UPDATE, DELETE nem TRUNCATE.
drop trigger if exists trg_auditoria_append_only on public.auditoria;
create trigger trg_auditoria_append_only
  before update or delete on public.auditoria
  for each row execute function public.bloquear_alteracao();

drop trigger if exists trg_auditoria_sem_truncate on public.auditoria;
create trigger trg_auditoria_sem_truncate
  before truncate on public.auditoria
  for each statement execute function public.bloquear_alteracao();

-- perfis, veiculos, requisicoes: sem DELETE/TRUNCATE fisico (use status/ativo).
drop trigger if exists trg_perfis_sem_delete on public.perfis;
create trigger trg_perfis_sem_delete
  before delete on public.perfis
  for each row execute function public.bloquear_alteracao();
drop trigger if exists trg_perfis_sem_truncate on public.perfis;
create trigger trg_perfis_sem_truncate
  before truncate on public.perfis
  for each statement execute function public.bloquear_alteracao();

drop trigger if exists trg_veiculos_sem_delete on public.veiculos;
create trigger trg_veiculos_sem_delete
  before delete on public.veiculos
  for each row execute function public.bloquear_alteracao();
drop trigger if exists trg_veiculos_sem_truncate on public.veiculos;
create trigger trg_veiculos_sem_truncate
  before truncate on public.veiculos
  for each statement execute function public.bloquear_alteracao();

drop trigger if exists trg_requisicoes_sem_delete on public.requisicoes;
create trigger trg_requisicoes_sem_delete
  before delete on public.requisicoes
  for each row execute function public.bloquear_alteracao();
drop trigger if exists trg_requisicoes_sem_truncate on public.requisicoes;
create trigger trg_requisicoes_sem_truncate
  before truncate on public.requisicoes
  for each statement execute function public.bloquear_alteracao();

-- ---------------------------------------------------------------------------
-- Ator de uma transicao, deduzido das colunas da linha (status novo).
-- ---------------------------------------------------------------------------
create or replace function public.requisicao_ator(r public.requisicoes)
returns uuid
language sql
immutable
set search_path = ''
as $$
  select case r.status
    when 'solicitada' then r.usuario_id
    when 'autorizada' then r.autorizado_por
    when 'negada'     then r.autorizado_por
    when 'em_uso'     then r.saida_registrada_por
    when 'concluida'  then r.chegada_registrada_por
    when 'cancelada'  then r.cancelada_por
  end;
$$;

revoke all on function public.requisicao_ator(public.requisicoes) from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- Validacao de insercao e de transicao (BEFORE INSERT OR UPDATE)
-- SECURITY DEFINER para ler perfis independentemente do papel de quem escreve.
-- ---------------------------------------------------------------------------
create or replace function public.requisicoes_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator  uuid;
  v_role  public.role_usuario;
  v_ativo boolean;
begin
  if tg_op = 'INSERT' then
    if new.status <> 'solicitada' then
      raise exception 'Requisição nova deve nascer como solicitada.' using errcode = 'AG001';
    end if;
    select ativo into v_ativo from public.perfis where id = new.usuario_id;
    if not found or not v_ativo then
      raise exception 'Usuário solicitante inexistente ou inativo.' using errcode = 'AG002';
    end if;
    return new;
  end if;

  -- UPDATE ------------------------------------------------------------------
  if new.id is distinct from old.id
     or new.usuario_id is distinct from old.usuario_id
     or new.criado_em is distinct from old.criado_em then
    raise exception 'Campos id, usuario_id e criado_em são imutáveis.' using errcode = 'AG004';
  end if;

  -- Estados terminais: linha congelada (atualizado_em e ajustado pelo outro trigger).
  if old.status in ('negada', 'cancelada', 'concluida') then
    if (to_jsonb(new) - 'atualizado_em') is distinct from (to_jsonb(old) - 'atualizado_em') then
      raise exception 'Requisição encerrada (%) não pode ser alterada.', old.status
        using errcode = 'AG001';
    end if;
    return new;
  end if;

  -- Veiculo so muda enquanto 'solicitada'.
  if old.status <> 'solicitada' and new.veiculo_id is distinct from old.veiculo_id then
    raise exception 'O veículo não pode mudar após a decisão do autorizador.' using errcode = 'AG004';
  end if;

  -- Colunas de autorizacao, saida, chegada e cancelamento: gravadas uma vez, imutaveis.
  if (old.autorizado_por         is not null and new.autorizado_por         is distinct from old.autorizado_por)
  or (old.autorizado_em          is not null and new.autorizado_em          is distinct from old.autorizado_em)
  or (old.saida_real_em          is not null and new.saida_real_em          is distinct from old.saida_real_em)
  or (old.saida_registrada_por   is not null and new.saida_registrada_por   is distinct from old.saida_registrada_por)
  or (old.chegada_real_em        is not null and new.chegada_real_em        is distinct from old.chegada_real_em)
  or (old.chegada_registrada_por is not null and new.chegada_registrada_por is distinct from old.chegada_registrada_por)
  or (old.cancelada_por          is not null and new.cancelada_por          is distinct from old.cancelada_por)
  or (old.cancelada_em           is not null and new.cancelada_em           is distinct from old.cancelada_em)
  then
    raise exception 'Dados de autorização, saída, chegada e cancelamento já gravados não podem mudar.'
      using errcode = 'AG004';
  end if;

  if new.status = old.status then
    return new;  -- edicao de campos descritivos; CHECKs cuidam da coerencia.
  end if;

  -- Transicao permitida?
  if not (case old.status
            when 'solicitada' then new.status in ('autorizada', 'negada', 'cancelada')
            when 'autorizada' then new.status in ('em_uso', 'cancelada')
            when 'em_uso'     then new.status = 'concluida'
            else false
          end) then
    raise exception 'Transição inválida: % -> %.', old.status, new.status using errcode = 'AG001';
  end if;

  -- Ator da transicao: existe, esta ativo e tem papel adequado.
  v_ator := public.requisicao_ator(new);
  if v_ator is null then
    raise exception 'Transição para % exige o responsável registrado.', new.status using errcode = 'AG002';
  end if;

  select role, ativo into v_role, v_ativo from public.perfis where id = v_ator;
  if not found or not v_ativo then
    raise exception 'Responsável pela transição inexistente ou inativo.' using errcode = 'AG002';
  end if;

  if new.status = 'cancelada' then
    -- admin/autorizador cancelam solicitada ou autorizada; solicitante so a propria e so 'solicitada'.
    if not (v_role in ('admin', 'autorizador')
            or (v_ator = old.usuario_id and old.status = 'solicitada')) then
      raise exception 'Sem permissão para cancelar esta requisição.' using errcode = 'AG002';
    end if;
  elsif v_role not in ('autorizador', 'admin') then
    raise exception 'Apenas autorizador ou admin pode autorizar, negar ou registrar saída/chegada.'
      using errcode = 'AG002';
  end if;

  -- Carimbos automaticos.
  case new.status
    when 'autorizada', 'negada' then new.autorizado_em := coalesce(new.autorizado_em, now());
    when 'em_uso'               then new.saida_real_em := coalesce(new.saida_real_em, now());
    when 'concluida'            then new.chegada_real_em := coalesce(new.chegada_real_em, now());
    when 'cancelada'            then new.cancelada_em := coalesce(new.cancelada_em, now());
    else null;
  end case;

  return new;
end;
$$;

revoke all on function public.requisicoes_validar() from public, anon, authenticated;

drop trigger if exists trg_requisicoes_validar on public.requisicoes;
create trigger trg_requisicoes_validar
  before insert or update on public.requisicoes
  for each row execute function public.requisicoes_validar();

-- ---------------------------------------------------------------------------
-- Auditoria automatica (AFTER): criacao e toda mudanca de status.
-- ---------------------------------------------------------------------------
create or replace function public.requisicoes_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para)
    values ('requisicoes', new.id, 'criada', public.requisicao_ator(new), null, new.status::text);
  elsif new.status is distinct from old.status then
    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para)
    values ('requisicoes', new.id, 'transicao', public.requisicao_ator(new),
            old.status::text, new.status::text);
  end if;
  return null;
end;
$$;

revoke all on function public.requisicoes_auditar() from public, anon, authenticated;

drop trigger if exists trg_requisicoes_auditoria on public.requisicoes;
create trigger trg_requisicoes_auditoria
  after insert or update on public.requisicoes
  for each row execute function public.requisicoes_auditar();
