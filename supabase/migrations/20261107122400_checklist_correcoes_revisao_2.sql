-- Migration 24: correcoes da segunda revisao do CHECKLIST (B-a, B-b). Nao edita a 23. Idempotente.
--
-- B-a: `INSERT ... (id) values (null)` explicito passava pelo gatilho (new.id nulo) e o NOT NULL
-- da PK disparava 23502 com `DETAIL: Failing row contains (...)`, incluindo descricao_avarias.
-- Decisao: RECUSAR com 23502 e mensagem fixa (em vez de `coalesce(new.id, gen_random_uuid())`).
-- Justificativa: (1) nao mascara um valor invalido enviado de proposito: o contrato e "omitir o
-- id" (o DEFAULT gera) e quem manda nulo recebe erro claro; (2) mantem o gatilho sem efeito
-- colateral sobre chave primaria (a chave tambem alimenta a variavel de sessao das fotos, e um
-- id gerado aqui divergiria de qualquer id que o chamador julgasse ter); (3) mesmo padrao dos
-- demais campos obrigatorios. A verificacao de id vem ANTES de qualquer outra para nunca deixar o
-- Postgres montar o DETAIL com a linha.
--
-- B-b: os erros do gatilho agora preenchem os campos de diagnostico (schema, table, column,
-- constraint) via USING. Contrato para o backend: identificar o campo/regra pela MENSAGEM FIXA
-- ou por esses campos (diag.column_name para 23502; diag.constraint_name para 23514), nunca por
-- DETAIL (nao existe). Nunca repassar message_detail do Postgres ao cliente nem aos logs.
--
-- SECURITY DEFINER, search_path vazio e revokes mantidos: as funcoes-gatilho seguem sem EXECUTE
-- para ninguem da API (reafirmado abaixo, com assercao DO).
--
-- Fuso: nao se aplica.

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
  if new.id is null then
    raise exception 'Campo obrigatório do checklist ausente: id.'
      using errcode = '23502', schema = 'public', table = 'checklists', column = 'id';
  end if;
  if new.requisicao_id is null then
    raise exception 'Campo obrigatório do checklist ausente: requisicao_id.'
      using errcode = '23502', schema = 'public', table = 'checklists', column = 'requisicao_id';
  end if;
  if new.momento is null then
    raise exception 'Campo obrigatório do checklist ausente: momento.'
      using errcode = '23502', schema = 'public', table = 'checklists', column = 'momento';
  end if;
  if new.combustivel is null then
    raise exception 'Campo obrigatório do checklist ausente: combustivel.'
      using errcode = '23502', schema = 'public', table = 'checklists', column = 'combustivel';
  end if;
  if new.avarias is null then
    raise exception 'Campo obrigatório do checklist ausente: avarias.'
      using errcode = '23502', schema = 'public', table = 'checklists', column = 'avarias';
  end if;
  if new.registrado_por is null then
    raise exception 'Campo obrigatório do checklist ausente: registrado_por.'
      using errcode = '23502', schema = 'public', table = 'checklists', column = 'registrado_por';
  end if;

  -- Mesmas regras dos CHECKs (ordem alfabetica dos nomes, como o Postgres avalia), sem DETAIL.
  if new.avarias then
    if new.descricao_avarias is null or new.descricao_avarias !~ '\S' then
      raise exception 'Violação da regra ck_checklists_avarias_descricao: informe a descrição das avarias.'
        using errcode = '23514', schema = 'public', table = 'checklists',
              column = 'descricao_avarias', constraint = 'ck_checklists_avarias_descricao';
    end if;
  elsif new.descricao_avarias is not null then
    raise exception 'Violação da regra ck_checklists_avarias_descricao: sem avarias não há descrição.'
      using errcode = '23514', schema = 'public', table = 'checklists',
            column = 'descricao_avarias', constraint = 'ck_checklists_avarias_descricao';
  end if;

  if new.descricao_avarias is not null and char_length(new.descricao_avarias) > 500 then
    raise exception 'Violação da regra ck_checklists_descricao_tamanho: máximo de 500 caracteres.'
      using errcode = '23514', schema = 'public', table = 'checklists',
            column = 'descricao_avarias', constraint = 'ck_checklists_descricao_tamanho';
  end if;

  if new.momento not in ('saida', 'chegada') then
    raise exception 'Violação da regra ck_checklists_momento: momento inválido.'
      using errcode = '23514', schema = 'public', table = 'checklists',
            column = 'momento', constraint = 'ck_checklists_momento';
  end if;

  -- A partir daqui, igual a migration 23.
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

-- CREATE OR REPLACE preserva os privilegios, mas reafirma: nenhuma funcao-gatilho tem EXECUTE
-- para a API (nem service_role). Gatilho nao exige EXECUTE de quem dispara a escrita.
revoke all on function public.checklists_validar()            from public, anon, authenticated, service_role;
revoke all on function public.checklist_fotos_validar()       from public, anon, authenticated, service_role;
revoke all on function public.checklists_auditar()            from public, anon, authenticated, service_role;
revoke all on function public.requisicoes_validar_checklist() from public, anon, authenticated, service_role;

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
    if exists (select 1 from pg_proc p,
                      aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
                where p.oid = f::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE') then
      raise exception 'PUBLIC ainda executa %.', f;
    end if;
  end loop;

  if not exists (select 1 from pg_proc
                  where oid = 'public.checklists_validar()'::regprocedure
                    and prosecdef
                    and proconfig is not null
                    and position('search_path=' in array_to_string(proconfig, ',')) > 0) then
    raise exception 'checklists_validar sem SECURITY DEFINER ou search_path fixo.';
  end if;
end
$$;
