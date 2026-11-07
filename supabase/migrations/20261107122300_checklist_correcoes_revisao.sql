-- Migration 23: correcoes da revisao de seguranca do CHECKLIST (M1, B1, B9).
-- Nova migration; nao edita as anteriores (19-22). Idempotente.
--
-- M1 (vazamento de texto livre em erro/log): quando um CHECK ou NOT NULL falha, o Postgres
-- anexa ao erro `DETAIL: Failing row contains (...)` com a LINHA INTEIRA, inclusive
-- descricao_avarias (texto livre que pode ter dado pessoal). O PostgREST devolve o DETAIL ao
-- cliente e o servidor o grava no log. Correcao: o gatilho BEFORE INSERT checklists_validar
-- (que roda ANTES dos CHECKs/NOT NULL, tanto na RPC registrar_checklist quanto em INSERT
-- direto) valida os mesmos casos e recusa com MENSAGEM FIXA, sem DETAIL e sem conteudo da
-- linha. SQLSTATE e nome da constraint (campo `constraint` do erro e dentro da mensagem)
-- continuam os mesmos, entao o contrato do backend nao muda:
--   * 23514 ck_checklists_avarias_descricao  (avarias=true sem texto util; avarias=false com texto)
--   * 23514 ck_checklists_descricao_tamanho  (mais de 500 caracteres)
--   * 23514 ck_checklists_momento            (momento fora de saida/chegada)
--   * 23502 (campo obrigatorio nulo: requisicao_id, momento, combustivel, avarias, registrado_por)
-- Os CHECKs da tabela continuam como defesa de dados (ultima barreira).
-- Mudanca de prioridade de erros: a validacao de conteudo agora vem ANTES de AG014 (janela) e
-- AG002 (ator), para nunca deixar o Postgres montar o DETAIL com a linha.
-- Limite aceito: erro de parametros (ex.: cast do enum combustivel, 22P02) cita so o valor do
-- proprio parametro, nunca a descricao.
-- SECURITY DEFINER, search_path vazio e EXECUTE so para service_role (na RPC) mantidos.
--
-- B1: ck_checklists_avarias_descricao usava btrim(texto) (so remove espaco): "\n\t" passava
-- como descricao nao vazia. A constraint e recriada com `~ '\S'` (exige ao menos um caractere
-- que nao seja espaco em branco: espaco, tab, CR, LF, FF, VT). A mesma regra vale no gatilho.
-- DROP/ADD CONSTRAINT e DDL (nao dispara o gatilho de imutabilidade). Se existir linha antiga
-- violando a regra (so possivel com descricao so de espacos em branco nao-espaco), o ADD falha
-- e a migration reverte: tratar caso a caso (tabela imutavel). Em banco novo nao ha linhas.
--
-- B9: as funcoes-gatilho do checklist nao sao chamadas pela API: o EXECUTE explicito do
-- service_role (default privileges do Supabase) e revogado, por consistencia. Gatilho nao exige
-- EXECUTE de quem dispara a escrita.
--
-- Fuso: nao se aplica.

-- ---------------------------------------------------------------------------
-- 1. checklists_validar: conteudo validado antes do INSERT, com mensagem fixa
-- ---------------------------------------------------------------------------
create or replace function public.checklists_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_status public.status_requisicao;
  v_role   public.role_usuario;
  v_ativo  boolean;
begin
  new.registrado_em := clock_timestamp();  -- carimbo do servidor

  -- Campos obrigatorios (NOT NULL). Mensagem fixa: so o nome do campo, nunca valores.
  if new.requisicao_id is null then
    raise exception 'Campo obrigatório do checklist ausente: requisicao_id.' using errcode = '23502';
  end if;
  if new.momento is null then
    raise exception 'Campo obrigatório do checklist ausente: momento.' using errcode = '23502';
  end if;
  if new.combustivel is null then
    raise exception 'Campo obrigatório do checklist ausente: combustivel.' using errcode = '23502';
  end if;
  if new.avarias is null then
    raise exception 'Campo obrigatório do checklist ausente: avarias.' using errcode = '23502';
  end if;
  if new.registrado_por is null then
    raise exception 'Campo obrigatório do checklist ausente: registrado_por.' using errcode = '23502';
  end if;

  -- Mesmas regras dos CHECKs (ordem alfabetica dos nomes, como o Postgres avalia), sem DETAIL.
  if new.avarias then
    if new.descricao_avarias is null or new.descricao_avarias !~ '\S' then
      raise exception 'Violação da regra ck_checklists_avarias_descricao: informe a descrição das avarias.'
        using errcode = '23514', constraint = 'ck_checklists_avarias_descricao';
    end if;
  elsif new.descricao_avarias is not null then
    raise exception 'Violação da regra ck_checklists_avarias_descricao: sem avarias não há descrição.'
      using errcode = '23514', constraint = 'ck_checklists_avarias_descricao';
  end if;

  if new.descricao_avarias is not null and char_length(new.descricao_avarias) > 500 then
    raise exception 'Violação da regra ck_checklists_descricao_tamanho: máximo de 500 caracteres.'
      using errcode = '23514', constraint = 'ck_checklists_descricao_tamanho';
  end if;

  if new.momento not in ('saida', 'chegada') then
    raise exception 'Violação da regra ck_checklists_momento: momento inválido.'
      using errcode = '23514', constraint = 'ck_checklists_momento';
  end if;

  -- A partir daqui, igual a migration 20.
  select status into v_status from public.requisicoes where id = new.requisicao_id for share;
  if not found then
    return new;  -- a FK responde (23503)
  end if;

  if not ((new.momento = 'saida' and v_status = 'autorizada')
          or (new.momento = 'chegada' and v_status = 'em_uso')) then
    raise exception 'Checklist de % não pode ser registrado com a requisição %.', new.momento, v_status
      using errcode = 'AG014';
  end if;

  select role, ativo into v_role, v_ativo from public.perfis where id = new.registrado_por;
  if not found or not v_ativo or v_role not in ('autorizador', 'admin') then
    raise exception 'Apenas autorizador ou admin ativo registra checklist.' using errcode = 'AG002';
  end if;

  -- Libera as fotos deste checklist apenas nesta transacao.
  perform set_config('sisveiculos.checklist_aberto_' || replace(new.id::text, '-', ''), '1', true);
  return new;
end;
$$;

-- O gatilho trg_checklists_validar (migration 20) continua ligado a esta funcao (ENABLE ALWAYS).

-- ---------------------------------------------------------------------------
-- 2. B1: ck_checklists_avarias_descricao com \S (qualquer espaco em branco nao conta)
-- ---------------------------------------------------------------------------
alter table public.checklists drop constraint if exists ck_checklists_avarias_descricao;
alter table public.checklists
  add constraint ck_checklists_avarias_descricao
  check ((avarias and descricao_avarias is not null and descricao_avarias ~ '\S')
         or (not avarias and descricao_avarias is null));

-- ---------------------------------------------------------------------------
-- 3. B9: funcoes-gatilho sem EXECUTE para ninguem da API (nem service_role)
-- ---------------------------------------------------------------------------
revoke all on function public.checklists_validar()            from public, anon, authenticated, service_role;
revoke all on function public.checklist_fotos_validar()       from public, anon, authenticated, service_role;
revoke all on function public.checklists_auditar()            from public, anon, authenticated, service_role;
revoke all on function public.requisicoes_validar_checklist() from public, anon, authenticated, service_role;

-- A RPC continua so para service_role (reafirma).
revoke all on function public.registrar_checklist(uuid, text, public.nivel_combustivel, boolean, text, uuid, jsonb)
  from public, anon, authenticated;
grant execute on function public.registrar_checklist(uuid, text, public.nivel_combustivel, boolean, text, uuid, jsonb)
  to service_role;

-- Trava: a migration falha se sobrar EXECUTE indevido ou se a constraint nao tiver a regra nova.
do $$
declare
  f text;
  r text;
begin
  foreach f in array array['public.checklists_validar()', 'public.checklist_fotos_validar()',
                           'public.checklists_auditar()', 'public.requisicoes_validar_checklist()']
  loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if has_function_privilege(r, f::regprocedure, 'execute') then
        raise exception '% ainda executa %.', r, f;
      end if;
    end loop;
  end loop;

  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.checklists'::regclass
                    and conname = 'ck_checklists_avarias_descricao'
                    and position('\S' in pg_get_constraintdef(oid)) > 0) then
    raise exception 'ck_checklists_avarias_descricao sem a regra \S.';
  end if;
end
$$;
