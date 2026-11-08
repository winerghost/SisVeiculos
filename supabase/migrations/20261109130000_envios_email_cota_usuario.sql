-- Migration: cota de envios de e-mail por usuario (achado M2 da revisao). Nova migration; nao edita a anterior. Idempotente.
--
-- Motivo: a cota de reservar_envio_email (20261109120000) e GLOBAL; um unico solicitante podia
-- esgota-la e impedir os demais. Esta migration recria a funcao com o parametro final
-- p_max_por_usuario_hora (default null). Quando informado (1..1000), alem da cota global, conta os
-- envios do MESMO p_solicitante na ultima hora (todos os status), sob o mesmo advisory lock, e recusa
-- com AG025 e mensagem fixa distinta ("Limite de envios por hora do usuário excedido.").
-- Null = comportamento anterior (so a cota global). Chamadas antigas de 6 argumentos continuam
-- validas pelo default.
--
-- Assinatura: a de 6 argumentos e REMOVIDA (drop) e a de 7 e criada; assim nao ha duas sobrecargas
-- ambiguas nem a antiga fica exposta. Ordem das validacoes (inalterada, com a nova no fim):
--   parametros AG027 -> recurso AG026 -> solicitante AG002 -> destinatario AG024 ->
--   cota global AG025 -> cota por usuario AG025.
-- Erros: nenhum codigo novo. Indice ja existente atende a contagem: ix_envios_email_solicitado_por_criado_em.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

drop function if exists public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer);

create or replace function public.reservar_envio_email(
  p_solicitante           uuid,
  p_destinatario          uuid,
  p_tipo                  text,
  p_formato               text,
  p_filtros               jsonb,
  p_max_por_hora          integer,
  p_max_por_usuario_hora  integer default null
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_filtros jsonb := coalesce(p_filtros, '{}'::jsonb);
  v_ligado  boolean;
  v_ativo   boolean;
  v_agora   timestamptz;
  v_cont    integer;
  v_id      uuid := gen_random_uuid();
begin
  if p_tipo is null or p_tipo !~ '^[a-z_]{1,40}$'
     or p_formato is null or p_formato not in ('csv', 'xlsx')
     or jsonb_typeof(v_filtros) <> 'object'
     or octet_length(v_filtros::text) >= 2048
     or position('@' in v_filtros::text) > 0
     or p_max_por_hora is null or p_max_por_hora < 1 or p_max_por_hora > 1000
     or (p_max_por_usuario_hora is not null
         and (p_max_por_usuario_hora < 1 or p_max_por_usuario_hora > 1000)) then
    raise exception 'Parâmetros do envio inválidos.' using errcode = 'AG027';
  end if;

  select coalesce(c.relatorio_email, false) into v_ligado from public.configuracoes c where c.id;
  if not coalesce(v_ligado, false) then
    raise exception 'Envio de relatório por e-mail desligado.' using errcode = 'AG026';
  end if;

  select pf.ativo into v_ativo from public.perfis pf where pf.id = p_solicitante;
  if not coalesce(v_ativo, false) then
    raise exception 'Solicitante do envio ausente ou inativo.' using errcode = 'AG002';
  end if;

  select pf.ativo into v_ativo from public.perfis pf where pf.id = p_destinatario;
  if not coalesce(v_ativo, false) then
    raise exception 'Destinatário do envio ausente ou inativo.' using errcode = 'AG024';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.envios_email', 0));
  v_agora := clock_timestamp();
  select count(*) into v_cont from public.envios_email e where e.criado_em > v_agora - interval '1 hour';
  if v_cont >= p_max_por_hora then
    raise exception 'Limite de envios por hora atingido. Tente mais tarde.' using errcode = 'AG025';
  end if;

  if p_max_por_usuario_hora is not null then
    select count(*) into v_cont from public.envios_email e
     where e.solicitado_por = p_solicitante and e.criado_em > v_agora - interval '1 hour';
    if v_cont >= p_max_por_usuario_hora then
      raise exception 'Limite de envios por hora do usuário excedido.' using errcode = 'AG025';
    end if;
  end if;

  insert into public.envios_email (id, solicitado_por, destinatario_id, tipo_relatorio, formato, filtros, criado_em)
  values (v_id, p_solicitante, p_destinatario, p_tipo, p_formato, v_filtros, v_agora);

  return jsonb_build_object('id', v_id);
end;
$$;

comment on function public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer, integer) is
  'Reserva um envio de relatorio por e-mail (recurso, solicitante, destinatario ativo, cota global/hora e cota opcional por usuario/hora, sob lock). Somente service_role.';

revoke all on function public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer, integer)
  from public, anon, authenticated;
grant execute on function public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer, integer)
  to service_role;

-- Autoverificacao: so a assinatura nova existe; so service_role executa; SECURITY DEFINER e search_path fixo.
do $$
declare
  f constant text := 'public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer, integer)';
  r text;
begin
  if (select count(*) from pg_proc where oid = any (
        array(select p.oid from pg_proc p join pg_namespace n on n.oid = p.pronamespace
               where n.nspname = 'public' and p.proname = 'reservar_envio_email'))) <> 1 then
    raise exception 'reservar_envio_email deve ter uma unica assinatura.';
  end if;
  foreach r in array array['anon', 'authenticated'] loop
    if has_function_privilege(r, f::regprocedure, 'execute') then
      raise exception '% executa %.', r, f;
    end if;
  end loop;
  if not has_function_privilege('service_role', f::regprocedure, 'execute') then
    raise exception 'service_role sem EXECUTE em %.', f;
  end if;
  if exists (select 1 from pg_proc p2, aclexplode(coalesce(p2.proacl, acldefault('f', p2.proowner))) a
              where p2.oid = f::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE') then
    raise exception 'PUBLIC executa %.', f;
  end if;
  if not exists (select 1 from pg_proc p2
                  where p2.oid = f::regprocedure and p2.prosecdef
                    and array_to_string(p2.proconfig, ',') like '%search_path=%') then
    raise exception '% sem SECURITY DEFINER ou search_path fixo.', f;
  end if;
end
$$;
