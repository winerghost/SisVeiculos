-- Migration: corrige os regex de push (p256dh) e passkeys (credential_id, chave_publica).
-- Nova migration; NAO edita nenhuma anterior. Idempotente. NAO aplicar pelo servidor: supabase db push e do pipeline.
--
-- Problema (achado em 2026-11-15): o motor de regex do Postgres aceita no maximo 255 em uma repeticao
-- {m,n} (RE_DUP_MAX). Os padroes abaixo, de 20261120120000_web_push e 20261122120000_passkeys_webauthn,
-- passam disso e dao "invalid regular expression: invalid repetition count(s)" em TODA avaliacao:
--   * ck_push_inscricoes_p256dh e registrar_inscricao_push: '^[A-Za-z0-9_-]{16,256}=*$'
--   * ck_credenciais_webauthn_credential_id e registrar_credencial_webauthn: '^[A-Za-z0-9_-]{16,1024}$'
--   * ck_credenciais_webauthn_chave e registrar_credencial_webauthn: '^[A-Za-z0-9_-]{16,4096}$'
--   Efeito: nenhuma inscricao de push e nenhuma passkey consegue ser gravada (as tabelas ficam vazias).
--
-- Correcao: a MESMA regra, escrita sem repeticao limitada. O regex confere so os caracteres
-- ('+' sem limite) e o tamanho vai para char_length:
--   * p256dh: base64url + '=' opcionais no fim; a parte sem '=' tem de 16 a 256 caracteres
--     (char_length(rtrim(p256dh, '=')), igual ao '{16,256}=*' original).
--   * credential_id: base64url com 16 a 1024 caracteres.
--   * chave_publica: base64url com 16 a 4096 caracteres.
--   ck_push_inscricoes_auth ('{8,128}') esta dentro do limite e nao muda.
-- As funcoes sao recriadas com o corpo IDENTICO ao vigente (nenhuma migration posterior as redefine);
-- so a linha do regex muda. SECURITY DEFINER, search_path = '', REVOKE/GRANT iguais.
-- Sem tabela nova: RLS nao muda. Fuso: nao se aplica.

set local lock_timeout = '5s';

-- 1) Constraints (as tabelas estao vazias por causa do bug; a validacao e imediata)
alter table public.push_inscricoes
  drop constraint if exists ck_push_inscricoes_p256dh;
alter table public.push_inscricoes
  add constraint ck_push_inscricoes_p256dh
  check (p256dh ~ '^[A-Za-z0-9_-]+=*$' and char_length(rtrim(p256dh, '=')) between 16 and 256);

alter table public.credenciais_webauthn
  drop constraint if exists ck_credenciais_webauthn_credential_id;
alter table public.credenciais_webauthn
  add constraint ck_credenciais_webauthn_credential_id
  check (credential_id ~ '^[A-Za-z0-9_-]+$' and char_length(credential_id) between 16 and 1024);

alter table public.credenciais_webauthn
  drop constraint if exists ck_credenciais_webauthn_chave;
alter table public.credenciais_webauthn
  add constraint ck_credenciais_webauthn_chave
  check (chave_publica ~ '^[A-Za-z0-9_-]+$' and char_length(chave_publica) between 16 and 4096);

-- 2) registrar_inscricao_push: corpo de 20261120120000_web_push, so o teste do p256dh muda
create or replace function public.registrar_inscricao_push(
  p_usuario  uuid,
  p_endpoint text,
  p_p256dh   text,
  p_auth     text
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_hash text;
  v_id   uuid;
begin
  if p_usuario is null
     or p_endpoint is null or p_endpoint !~ '^https://[^[:space:]]+$' or length(p_endpoint) > 2048
     or p_p256dh is null or p_p256dh !~ '^[A-Za-z0-9_-]+=*$'
     or char_length(rtrim(p_p256dh, '=')) not between 16 and 256
     or p_auth is null or p_auth !~ '^[A-Za-z0-9_-]{8,128}=*$' then
    raise exception 'Dados da inscrição de push inválidos.' using errcode = 'AG042';
  end if;

  v_hash := encode(sha256(convert_to(p_endpoint, 'UTF8')), 'hex');
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.push_endpoint:' || v_hash, 0));

  select i.id into v_id from public.push_inscricoes i where i.endpoint_hash = v_hash for update;
  if found then
    update public.push_inscricoes i
       set usuario_id = p_usuario, p256dh = p_p256dh, auth = p_auth, falhas = 0, revogada_em = null
     where i.id = v_id;
    return jsonb_build_object('id', v_id, 'reatribuida', true);
  end if;

  insert into public.push_inscricoes (usuario_id, endpoint, endpoint_hash, p256dh, auth)
  values (p_usuario, p_endpoint, v_hash, p_p256dh, p_auth)
  returning id into v_id;
  return jsonb_build_object('id', v_id, 'reatribuida', false);
end;
$$;

revoke all on function public.registrar_inscricao_push(uuid, text, text, text) from public, anon, authenticated;
grant execute on function public.registrar_inscricao_push(uuid, text, text, text) to service_role;

-- 3) registrar_credencial_webauthn: corpo de 20261122120000_passkeys_webauthn, so os testes de formato mudam
create or replace function public.registrar_credencial_webauthn(
  p_usuario       uuid,
  p_credential_id text,
  p_chave_publica text,
  p_contador      bigint,
  p_transportes   text[],
  p_apelido       text
)
returns uuid
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_id uuid;
begin
  if p_usuario is null or p_credential_id is null or p_chave_publica is null or p_contador is null
     or p_contador < 0
     or p_credential_id !~ '^[A-Za-z0-9_-]+$' or char_length(p_credential_id) not between 16 and 1024
     or p_chave_publica !~ '^[A-Za-z0-9_-]+$' or char_length(p_chave_publica) not between 16 and 4096
     or (p_transportes is not null
         and (cardinality(p_transportes) > 5
              or not (p_transportes <@ array['usb', 'nfc', 'ble', 'internal', 'hybrid']::text[])))
     or (p_apelido is not null and (char_length(p_apelido) not between 1 and 40 or p_apelido <> btrim(p_apelido)))
  then
    raise exception 'Dados da passkey inválidos.' using errcode = 'AG050';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.webauthn_credential:' || p_credential_id, 0));
  if exists (select 1 from public.credenciais_webauthn c where c.credential_id = p_credential_id) then
    raise exception 'Passkey já cadastrada.' using errcode = 'AG055';
  end if;

  insert into public.credenciais_webauthn (usuario_id, credential_id, chave_publica, contador, transportes, apelido)
  values (p_usuario, p_credential_id, p_chave_publica, p_contador, p_transportes, p_apelido)
  returning id into v_id;

  insert into public.auditoria (tabela, registro_id, acao, ator_id, detalhes)
  values ('perfis', p_usuario, 'passkey_cadastrada', p_usuario,
          jsonb_build_object('recurso', 'passkey', 'credencial', v_id));
  return v_id;
end;
$$;

revoke all on function public.registrar_credencial_webauthn(uuid, text, text, bigint, text[], text)
  from public, anon, authenticated;
grant execute on function public.registrar_credencial_webauthn(uuid, text, text, bigint, text[], text)
  to service_role;

-- 4) Checagem (falha => a migration inteira e desfeita)
do $$
declare
  v_def text;
begin
  -- Nenhuma constraint de public com repeticao acima de 255 nas tabelas corrigidas.
  for v_def in
    select pg_get_constraintdef(c.oid)
      from pg_constraint c
     where c.conrelid in ('public.push_inscricoes'::regclass, 'public.credenciais_webauthn'::regclass)
       and c.contype = 'c'
  loop
    if v_def ~ '\{[0-9]+,(25[6-9]|2[6-9][0-9]|[3-9][0-9]{2}|[0-9]{4,})\}' then
      raise exception 'regex_push_passkeys: constraint ainda com repeticao > 255: %', v_def;
    end if;
  end loop;

  -- As funcoes nao podem mais conter os padroes quebrados.
  if exists (
    select 1 from pg_proc p
     where p.oid in ('public.registrar_inscricao_push(uuid,text,text,text)'::regprocedure,
                     'public.registrar_credencial_webauthn(uuid,text,text,bigint,text[],text)'::regprocedure)
       and (p.prosrc like '%{16,256}%' or p.prosrc like '%{16,1024}%' or p.prosrc like '%{16,4096}%')
  ) then
    raise exception 'regex_push_passkeys: funcao ainda com o regex antigo';
  end if;

  -- Os padroes novos compilam e aplicam a regra (16..256 / 16..1024 / 16..4096).
  if not (repeat('a', 256) || '==' ~ '^[A-Za-z0-9_-]+=*$' and char_length(rtrim(repeat('a', 256) || '==', '=')) = 256)
     or repeat('a', 4096) !~ '^[A-Za-z0-9_-]+$' then
    raise exception 'regex_push_passkeys: autoverificacao dos padroes falhou';
  end if;

  -- Privilegios iguais aos de antes.
  if has_function_privilege('anon', 'public.registrar_inscricao_push(uuid,text,text,text)', 'execute')
     or has_function_privilege('authenticated', 'public.registrar_inscricao_push(uuid,text,text,text)', 'execute')
     or has_function_privilege('anon', 'public.registrar_credencial_webauthn(uuid,text,text,bigint,text[],text)', 'execute')
     or has_function_privilege('authenticated', 'public.registrar_credencial_webauthn(uuid,text,text,bigint,text[],text)', 'execute')
     or not has_function_privilege('service_role', 'public.registrar_inscricao_push(uuid,text,text,text)', 'execute')
     or not has_function_privilege('service_role', 'public.registrar_credencial_webauthn(uuid,text,text,bigint,text[],text)', 'execute')
  then
    raise exception 'regex_push_passkeys: privilegios inesperados nas funcoes';
  end if;
end;
$$;
