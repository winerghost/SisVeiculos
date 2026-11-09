-- Migration: IA opcional, Fase 1 (saudacao sazonal): frases adicionais. Nova migration; NAO edita as anteriores.
-- Idempotente.
--
-- Motivo: o catalogo base de saudacoes fica em CODIGO no backend. O banco guarda so frases ADICIONAIS
-- (manuais do admin ou geradas por IA), que so entram em uso depois de aprovadas por um admin ativo.
-- O texto e exibido na tela (e pode levar o primeiro nome via {nome}), por isso as regras de forma
-- ficam em CHECK: tamanho 3..80, sem '<', '>', 'http', sem caractere de controle (nem quebra de
-- linha) e unico placeholder permitido `{nome}`. A IA nunca decide regra de negocio (CLAUDE.md).
--
-- public.saudacoes
--   ocasiao ........ lista fixa (23 valores). Mexer na lista = nova migration.
--   origem ......... 'manual' | 'ia'.
--   status ......... 'pendente' | 'aprovada' | 'rejeitada'.
--   criado_por ..... perfis(id); NULL permitido para jobs (origem 'ia'). Origem 'manual' exige admin.
--   decidido_por/em  preenchidos se, e somente se, status <> 'pendente' (CHECK de coerencia).
--
-- Regras no banco (trigger BEFORE INSERT OR UPDATE, SECURITY DEFINER, search_path vazio):
--   * INSERT sempre nasce 'pendente' (AG001). Aprovar e uma transicao a parte, assim toda decisao
--     deixa sua propria linha de auditoria. criado_por, se informado, precisa ser admin ativo (AG002).
--   * UPDATE so muda status/decidido_por/decidido_em; qualquer outra coluna -> AG004.
--   * Transicoes: pendente -> aprovada | rejeitada; aprovada <-> rejeitada (admin retira/reabilita
--     uma frase). Nunca volta a 'pendente' (AG001). Decisao exige decidido_por = admin ativo (AG002).
--     decidido_em e carimbado pelo banco (now()), nunca vem do cliente.
--   * Sem DELETE nem TRUNCATE (AG003; revogados tambem de service_role). Sem UPDATE em massa de
--     colunas imutaveis: o service_role so recebe UPDATE em (status, decidido_por, decidido_em).
--
-- Auditoria (mesmo padrao de ia_configuracoes): REAPROVEITA acao='configuracao_alterada' e
-- tabela='configuracoes'; nenhum CHECK de auditoria e recriado e nenhuma acao nova e criada. A linha
-- sai do proprio trigger (some junto se o statement falhar): registro_id = id da saudacao, ator_id =
-- criado_por (criacao) ou decidido_por (decisao), de/para = status, detalhes
-- {"recurso":"saudacao","evento":"criada|aprovada|rejeitada","ocasiao":..,"origem":..}.
-- O TEXTO da frase nao vai para a auditoria (fica na propria linha, que e imutavel). Sem dado pessoal.
--
-- RLS: ligado, sem policy (deny-by-default); revoke all de public/anon/authenticated.
-- Erros: reaproveita AG001 (transicao invalida), AG002 (responsavel nao admin ativo), AG003 (sem
-- DELETE/TRUNCATE), AG004 (campo imutavel). Nenhum erro novo.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. Tabela
-- ---------------------------------------------------------------------------
create table if not exists public.saudacoes (
  id           uuid primary key default gen_random_uuid(),
  ocasiao      text not null,
  texto        text not null,
  origem       text not null,
  status       text not null default 'pendente',
  criado_por   uuid references public.perfis (id) on delete restrict,
  criado_em    timestamptz not null default now(),
  decidido_por uuid references public.perfis (id) on delete restrict,
  decidido_em  timestamptz,

  constraint ck_saudacoes_ocasiao check (ocasiao in (
    'madrugada', 'manha', 'tarde', 'noite', 'segunda', 'sexta', 'fim_de_semana',
    'ano_novo', 'vespera_ano_novo', 'carnaval', 'sexta_santa', 'pascoa', 'tiradentes',
    'trabalho', 'corpus_christi', 'independencia', 'padroeira', 'finados', 'republica',
    'consciencia_negra', 'dia_servidor', 'vespera_natal', 'natal')),
  constraint ck_saudacoes_texto_tamanho check (char_length(texto) between 3 and 80),
  constraint ck_saudacoes_texto_sem_markup check (texto !~ '[<>]'),
  constraint ck_saudacoes_texto_sem_link check (texto !~* 'http'),
  constraint ck_saudacoes_texto_sem_controle check (texto !~ '[[:cntrl:]]'),
  constraint ck_saudacoes_texto_aparado check (texto = btrim(texto)),
  -- unico placeholder permitido e {nome}: removido ele, nao pode sobrar chave.
  constraint ck_saudacoes_texto_placeholder check (replace(texto, '{nome}', '') !~ '[{}]'),
  constraint ck_saudacoes_origem check (origem in ('manual', 'ia')),
  constraint ck_saudacoes_status check (status in ('pendente', 'aprovada', 'rejeitada')),
  constraint ck_saudacoes_decisao_coerente check (
    (status = 'pendente' and decidido_por is null and decidido_em is null)
    or (status <> 'pendente' and decidido_por is not null and decidido_em is not null)),
  constraint uq_saudacoes_ocasiao_texto unique (ocasiao, texto)
);

comment on table public.saudacoes is
  'Frases adicionais de saudacao (catalogo base fica no backend). Entram em uso so se aprovadas por admin. Sem DELETE fisico; texto imutavel; toda criacao/decisao e auditada.';

-- Busca do sorteio: frases aprovadas de uma ocasiao.
create index if not exists ix_saudacoes_aprovadas_ocasiao
  on public.saudacoes (ocasiao) where status = 'aprovada';
-- Fila de moderacao do admin.
create index if not exists ix_saudacoes_pendentes
  on public.saudacoes (criado_em) where status = 'pendente';
-- FKs (checagem de on delete restrict em perfis).
create index if not exists ix_saudacoes_criado_por on public.saudacoes (criado_por) where criado_por is not null;
create index if not exists ix_saudacoes_decidido_por on public.saudacoes (decidido_por) where decidido_por is not null;

-- ---------------------------------------------------------------------------
-- 2. Sem DELETE nem TRUNCATE
-- ---------------------------------------------------------------------------
drop trigger if exists trg_saudacoes_sem_delete on public.saudacoes;
create trigger trg_saudacoes_sem_delete
  before delete on public.saudacoes
  for each row execute function public.bloquear_alteracao();
drop trigger if exists trg_saudacoes_sem_truncate on public.saudacoes;
create trigger trg_saudacoes_sem_truncate
  before truncate on public.saudacoes
  for each statement execute function public.bloquear_alteracao();

-- ---------------------------------------------------------------------------
-- 3. Validacao de estado + imutabilidade + auditoria
-- ---------------------------------------------------------------------------
create or replace function public.saudacoes_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.status is distinct from 'pendente' then
      raise exception 'Saudação nova deve nascer pendente.' using errcode = 'AG001';
    end if;
    if new.criado_por is null and new.origem = 'manual' then
      raise exception 'Saudação manual exige o responsável registrado.' using errcode = 'AG002';
    end if;
    if new.criado_por is not null then
      perform privado.exigir_admin_ativo(new.criado_por);
    end if;
    new.criado_em := now();  -- carimbo do banco

    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
    values ('configuracoes', new.id, 'configuracao_alterada', new.criado_por, null, new.status,
            jsonb_build_object('recurso', 'saudacao', 'evento', 'criada',
                               'ocasiao', new.ocasiao, 'origem', new.origem));
    return new;
  end if;

  -- UPDATE: so status/decidido_* podem mudar.
  if new.id is distinct from old.id
     or new.ocasiao is distinct from old.ocasiao
     or new.texto is distinct from old.texto
     or new.origem is distinct from old.origem
     or new.criado_por is distinct from old.criado_por
     or new.criado_em is distinct from old.criado_em then
    raise exception 'Campos id, ocasião, texto, origem e criação da saudação são imutáveis.'
      using errcode = 'AG004';
  end if;

  if new.status = old.status then
    if new.decidido_por is distinct from old.decidido_por
       or new.decidido_em is distinct from old.decidido_em then
      raise exception 'Decisão da saudação só muda junto com o status.' using errcode = 'AG004';
    end if;
    return new;
  end if;

  if not ((old.status = 'pendente' and new.status in ('aprovada', 'rejeitada'))
          or (old.status = 'aprovada' and new.status = 'rejeitada')
          or (old.status = 'rejeitada' and new.status = 'aprovada')) then
    raise exception 'Transição de saudação inválida: % -> %.', old.status, new.status
      using errcode = 'AG001';
  end if;

  perform privado.exigir_admin_ativo(new.decidido_por);
  new.decidido_em := now();  -- carimbo do banco

  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
  values ('configuracoes', new.id, 'configuracao_alterada', new.decidido_por, old.status, new.status,
          jsonb_build_object('recurso', 'saudacao',
                             'evento', case new.status when 'aprovada' then 'aprovada' else 'rejeitada' end,
                             'ocasiao', new.ocasiao, 'origem', new.origem));
  return new;
end;
$$;

revoke all on function public.saudacoes_validar() from public, anon, authenticated, service_role;

drop trigger if exists trg_saudacoes_validar on public.saudacoes;
create trigger trg_saudacoes_validar
  before insert or update on public.saudacoes
  for each row execute function public.saudacoes_validar();

-- ---------------------------------------------------------------------------
-- 4. RLS deny-by-default e privilegios
-- ---------------------------------------------------------------------------
alter table public.saudacoes enable row level security;
-- Sem policy para anon/authenticated (deny-by-default). O backend usa service_role.
revoke all on table public.saudacoes from public, anon, authenticated, service_role;
grant select, insert on table public.saudacoes to service_role;
grant update (status, decidido_por, decidido_em) on table public.saudacoes to service_role;

-- ---------------------------------------------------------------------------
-- Autoverificacao
-- ---------------------------------------------------------------------------
do $$
begin
  if not (select relrowsecurity from pg_class where oid = 'public.saudacoes'::regclass) then
    raise exception 'RLS desligado em public.saudacoes.';
  end if;
  if exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'saudacoes') then
    raise exception 'public.saudacoes nao deve ter policy (deny-by-default).';
  end if;
  if has_table_privilege('service_role', 'public.saudacoes', 'delete')
     or has_table_privilege('anon', 'public.saudacoes', 'select')
     or has_table_privilege('authenticated', 'public.saudacoes', 'select') then
    raise exception 'Privilegios indevidos em public.saudacoes.';
  end if;
end
$$;
