-- Migration 19: recurso opcional CHECKLIST, parte 1 (tabelas, imutabilidade, RLS e grants).
-- Nova migration; nao edita as anteriores. Idempotente.
--
-- Motivo: CLAUDE.md ("Recursos opcionais"): com o recurso ligado em configuracoes.checklist, o
-- checklist e obrigatorio na saida e na chegada (combustivel, avarias, fotos). Desligado por
-- padrao; desligar nunca apaga dados ja gravados. Esta migration so cria a ESTRUTURA e as
-- invariantes dos dados; as regras de registro/auditoria vem na 20, o bucket na 21 e a
-- obrigatoriedade na transicao da requisicao na 22.
--
-- Desenho (conjunto minimo):
--  * public.checklists: um por (requisicao_id, momento), momento = 'saida' | 'chegada'.
--    Campos: combustivel (enum fechado), avarias (boolean), descricao_avarias (texto, max 500,
--    obrigatoria se avarias = true e proibida se false), registrado_por (FK perfis) e
--    registrado_em (carimbo do servidor, definido na migration 20).
--  * public.checklist_fotos: ate 6 por checklist, filha de checklists por FK composta
--    (requisicao_id, momento). Metadados: mime_type (jpeg/png/webp), tamanho_bytes (1..5 MB) e
--    posicao (1..6).
--  * LIMITE DE 6 FOTOS ESTRUTURAL: posicao smallint com CHECK 1..6 e UNIQUE
--    (requisicao_id, momento, posicao). Sem contagem em trigger, portanto sem corrida entre
--    transacoes nem dependencia de visibilidade dentro de INSERT multilinha.
--  * CAMINHO NO STORAGE GERADO PELO BANCO: caminho = <requisicao_id>/<momento>/<id da foto>.<ext>
--    como coluna GENERATED ALWAYS ... STORED, a partir das proprias colunas da linha. A
--    extensao deriva do mime_type. Como (requisicao_id, momento) e a FK para o checklist, uma
--    foto NAO consegue apontar para caminho de outra requisicao nem de outro momento: o
--    caminho nao e informado, e calculado (INSERT com caminho explicito falha, 428C9). O
--    backend calcula o MESMO caminho antes (id da foto = uuid que ele gera) para pedir a URL
--    assinada de upload ao Storage.
--    momento e text + CHECK (e nao enum) porque a expressao gerada precisa ser IMMUTABLE e o
--    cast enum -> text e STABLE.
--  * IMUTAVEIS: UPDATE, DELETE e TRUNCATE bloqueados por trigger (AG003, public.bloquear_alteracao)
--    em ambas as tabelas, ENABLE ALWAYS, e sem o privilegio correspondente ao service_role.
--    Correcao de um checklist so por nova acao futura (ex.: checklist de retificacao), nunca
--    por edicao. Sem DELETE fisico.
--  * Sem dado pessoal nos campos estruturados. ATENCAO: descricao_avarias e texto livre (max 500)
--    e o usuario pode digitar nome/telefone. Por isso ela NUNCA vai para auditoria, logs ou n8n;
--    so o backend a le, para autorizador/admin. A UI deve orientar a descrever so o veiculo.
--
-- RLS/grants (deny-by-default):
--  * RLS ligado nas duas tabelas, sem politica permissiva, e politica explicita de negacao para
--    anon/authenticated. Nenhum privilegio para anon/authenticated.
--  * service_role: SOMENTE SELECT. A escrita passa por public.registrar_checklist (migration 20,
--    SECURITY DEFINER, execute so para service_role), que grava checklist e fotos na MESMA
--    transacao. Assim o service_role nunca tem INSERT/UPDATE/DELETE/TRUNCATE/TRIGGER/REFERENCES
--    direto nestas tabelas (o backend fala com o PostgREST por comando unico; a funcao e o
--    jeito de ter atomicidade).
--  * Tabelas antigas (perfis, veiculos, requisicoes, auditoria, configuracoes, convites) NAO sao
--    tocadas aqui.
--
-- Erros previstos nestas tabelas: 23514 (nome ck_checklists_* / ck_checklist_fotos_*),
-- 23505 (uq_checklists_requisicao_momento: ja existe checklist do momento;
-- uq_checklist_fotos_posicao), 23503 (requisicao/checklist inexistente), AG003 (UPDATE/DELETE).
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. Enum fechado do nivel de combustivel (idempotente)
-- ---------------------------------------------------------------------------
do $$
begin
  if not exists (select 1 from pg_type t join pg_namespace n on n.oid = t.typnamespace
                 where n.nspname = 'public' and t.typname = 'nivel_combustivel') then
    create type public.nivel_combustivel as enum
      ('vazio', 'um_quarto', 'meio', 'tres_quartos', 'cheio');
  end if;
end
$$;

-- ---------------------------------------------------------------------------
-- 2. checklists
-- ---------------------------------------------------------------------------
create table if not exists public.checklists (
  id                 uuid primary key default gen_random_uuid(),
  requisicao_id      uuid not null references public.requisicoes (id) on delete restrict,
  momento            text not null,
  combustivel        public.nivel_combustivel not null,
  avarias            boolean not null,
  descricao_avarias  text,
  registrado_por     uuid not null references public.perfis (id) on delete restrict,
  registrado_em      timestamptz not null default now(),

  constraint uq_checklists_requisicao_momento unique (requisicao_id, momento),
  constraint ck_checklists_momento check (momento in ('saida', 'chegada')),
  -- obrigatoria se avarias = true; proibida se false (sem texto solto sem avaria).
  constraint ck_checklists_avarias_descricao
    check ((avarias and descricao_avarias is not null and char_length(btrim(descricao_avarias)) >= 1)
           or (not avarias and descricao_avarias is null)),
  constraint ck_checklists_descricao_tamanho
    check (descricao_avarias is null or char_length(descricao_avarias) <= 500)
);

comment on table public.checklists is
  'Checklist de saida/chegada (recurso Checklist). Um por requisicao e momento. Imutavel: UPDATE/DELETE bloqueados. Escrita so via registrar_checklist.';
comment on column public.checklists.descricao_avarias is
  'Texto livre (max 500). Pode conter dado pessoal digitado pelo usuario: nunca vai para auditoria, logs ou n8n.';

-- registrado_por: checagem da FK e "o que este usuario registrou". (requisicao_id ja e coberto
-- pelo indice do UNIQUE.)
create index if not exists ix_checklists_registrado_por on public.checklists (registrado_por);

-- ---------------------------------------------------------------------------
-- 3. checklist_fotos
-- ---------------------------------------------------------------------------
create table if not exists public.checklist_fotos (
  id             uuid primary key default gen_random_uuid(),
  requisicao_id  uuid not null,
  momento        text not null,
  posicao        smallint not null,
  mime_type      text not null,
  tamanho_bytes  integer not null,
  -- Caminho no bucket privado 'checklists': <requisicao_id>/<momento>/<id>.<ext>. Calculado.
  caminho        text generated always as (
                   requisicao_id::text || '/' || momento || '/' || id::text || '.' ||
                   case mime_type
                     when 'image/jpeg' then 'jpg'
                     when 'image/png'  then 'png'
                     when 'image/webp' then 'webp'
                   end
                 ) stored,
  registrado_em  timestamptz not null default now(),

  constraint fk_checklist_fotos_checklist
    foreign key (requisicao_id, momento)
    references public.checklists (requisicao_id, momento) on delete restrict,
  constraint uq_checklist_fotos_posicao unique (requisicao_id, momento, posicao),
  constraint ck_checklist_fotos_posicao check (posicao between 1 and 6),
  constraint ck_checklist_fotos_mime check (mime_type in ('image/jpeg', 'image/png', 'image/webp')),
  -- 5 MB = 5242880 bytes; metadado validado pelo backend (e pelo limite do bucket, migration 21).
  constraint ck_checklist_fotos_tamanho check (tamanho_bytes between 1 and 5242880)
);

comment on table public.checklist_fotos is
  'Fotos de um checklist (ate 6; posicao 1..6 unica). Metadados e caminho no Storage privado; o arquivo fica no bucket checklists. Imutavel.';
comment on column public.checklist_fotos.caminho is
  'GERADO: <requisicao_id>/<momento>/<id>.<ext>. Nao informavel; impede apontar para caminho de outra requisicao.';

-- ---------------------------------------------------------------------------
-- 4. Imutabilidade e sem DELETE/TRUNCATE fisico (UPDATE tambem), mesmo para service_role
-- ---------------------------------------------------------------------------
drop trigger if exists trg_checklists_imutavel on public.checklists;
create trigger trg_checklists_imutavel
  before update or delete on public.checklists
  for each row execute function public.bloquear_alteracao();
alter table public.checklists enable always trigger trg_checklists_imutavel;

drop trigger if exists trg_checklists_sem_truncate on public.checklists;
create trigger trg_checklists_sem_truncate
  before truncate on public.checklists
  for each statement execute function public.bloquear_alteracao();
alter table public.checklists enable always trigger trg_checklists_sem_truncate;

drop trigger if exists trg_checklist_fotos_imutavel on public.checklist_fotos;
create trigger trg_checklist_fotos_imutavel
  before update or delete on public.checklist_fotos
  for each row execute function public.bloquear_alteracao();
alter table public.checklist_fotos enable always trigger trg_checklist_fotos_imutavel;

drop trigger if exists trg_checklist_fotos_sem_truncate on public.checklist_fotos;
create trigger trg_checklist_fotos_sem_truncate
  before truncate on public.checklist_fotos
  for each statement execute function public.bloquear_alteracao();
alter table public.checklist_fotos enable always trigger trg_checklist_fotos_sem_truncate;

-- ---------------------------------------------------------------------------
-- 5. RLS deny-by-default, negacao explicita e grants minimos
-- ---------------------------------------------------------------------------
alter table public.checklists      enable row level security;
alter table public.checklist_fotos enable row level security;

drop policy if exists checklists_sem_acesso_api on public.checklists;
create policy checklists_sem_acesso_api on public.checklists
  for all to anon, authenticated using (false) with check (false);

drop policy if exists checklist_fotos_sem_acesso_api on public.checklist_fotos;
create policy checklist_fotos_sem_acesso_api on public.checklist_fotos
  for all to anon, authenticated using (false) with check (false);

revoke all on table public.checklists, public.checklist_fotos from public, anon, authenticated;
-- service_role: so leitura. Escrita pela funcao registrar_checklist (migration 20).
revoke all on table public.checklists, public.checklist_fotos from service_role;
grant select on table public.checklists, public.checklist_fotos to service_role;

-- Trava: a migration falha se sobrar acesso direto indevido.
do $$
declare
  t text;
  p text;
begin
  foreach t in array array['checklists', 'checklist_fotos']
  loop
    if not (select relrowsecurity from pg_class where oid = ('public.' || t)::regclass) then
      raise exception 'public.% sem RLS.', t;
    end if;
    if has_table_privilege('anon', 'public.' || t, 'select, insert, update, delete')
       or has_table_privilege('authenticated', 'public.' || t, 'select, insert, update, delete') then
      raise exception 'public.% permite acesso direto a anon/authenticated.', t;
    end if;
    foreach p in array array['insert', 'update', 'delete', 'truncate', 'trigger', 'references'] loop
      if has_table_privilege('service_role', 'public.' || t, p) then
        raise exception 'service_role tem % em public.%.', p, t;
      end if;
    end loop;
  end loop;
end
$$;
