-- Migration: registrar_auditoria() passa a aceitar a acao 'localizacao_lida'. Nova migration; nao edita as
-- anteriores. Idempotente (create or replace + grants repetiveis). Depende de 20261109120000_relatorios.sql
-- (versao vigente da funcao; nenhuma migration posterior a redefine) e de 20261113120200 (CHECK ja aceita
-- a acao).
--
-- Motivo: o backend (ServicoLocalizacoes.listar) audita cada leitura das coordenadas do motorista via esta
-- RPC com p_acao = 'localizacao_lida', mas a lista fechada da funcao so tinha acao_administrativa,
-- acesso_negado, relatorio_gerado e relatorio_enviado: a leitura era recusada (AG003) e, como a auditoria
-- e feita antes de devolver os dados, o admin nao conseguia consultar.
--
-- O que muda: SO o acrescimo de 'localizacao_lida' a lista aceita + o bloco de regras dessa acao. Todo o
-- resto (assinatura, SECURITY DEFINER, search_path vazio, limite 64 de de/para, ator existente, regras de
-- relatorio, insert) e identico a versao de 20261109120000. Regras da nova acao:
--   * ator obrigatorio, existente, ATIVO e com role 'admin' (so o admin le coordenadas);
--   * tabela 'requisicoes';
--   * detalhes e objeto JSON e NUNCA contem as chaves latitude, longitude nem precisao* (em qualquer
--     nivel): coordenadas sao dado sensivel (LGPD) e nao vao para a auditoria.
-- Grants identicos: revoke de public/anon/authenticated; execute so para service_role.
-- Fuso: timestamptz (UTC; exibicao em America/Sao_Paulo).

create or replace function public.registrar_auditoria(
  p_tabela      text,
  p_registro_id uuid,
  p_acao        text,
  p_ator_id     uuid,
  p_de          text default null,
  p_para        text default null,
  p_detalhes    jsonb default '{}'::jsonb
)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_id bigint;
begin
  if p_acao not in ('acao_administrativa', 'acesso_negado', 'relatorio_gerado', 'relatorio_enviado',
                    'localizacao_lida') then
    raise exception 'Ação de auditoria não permitida por esta função.' using errcode = 'AG003';
  end if;
  if char_length(p_de) > 64 or char_length(p_para) > 64 then
    raise exception 'Campos de/para da auditoria aceitam no máximo 64 caracteres.' using errcode = '22023';
  end if;
  if p_ator_id is not null and not exists (select 1 from public.perfis where id = p_ator_id) then
    raise exception 'Responsável informado na auditoria não existe.' using errcode = 'AG002';
  end if;

  if p_acao in ('relatorio_gerado', 'relatorio_enviado') then
    if p_ator_id is null then
      raise exception 'Auditoria de relatório exige o responsável.' using errcode = 'AG002';
    end if;
    if p_tabela is distinct from (case p_acao when 'relatorio_gerado' then 'relatorios'
                                              else 'envios_email' end) then
      raise exception 'Tabela de auditoria incompatível com a ação de relatório.' using errcode = 'AG003';
    end if;
    if jsonb_typeof(coalesce(p_detalhes, '{}'::jsonb)) <> 'object'
       or exists (select 1 from jsonb_object_keys(coalesce(p_detalhes, '{}'::jsonb)) as k
                   where k <> all (array['tipo', 'filtros', 'formato', 'via', 'destinatario_id',
                                         'total_linhas']))
       or position('@' in coalesce(p_detalhes, '{}'::jsonb)::text) > 0 then
      raise exception 'Detalhes da auditoria de relatório fora da lista permitida.' using errcode = 'AG003';
    end if;
  end if;

  if p_acao = 'localizacao_lida' then
    if p_ator_id is null
       or not exists (select 1 from public.perfis
                       where id = p_ator_id and role = 'admin' and ativo) then
      raise exception 'Auditoria de leitura de localização exige um administrador ativo.'
        using errcode = 'AG002';
    end if;
    if p_tabela is distinct from 'requisicoes' then
      raise exception 'Tabela de auditoria incompatível com a ação de localização.' using errcode = 'AG003';
    end if;
    -- Nunca coordenadas na auditoria: recusa as chaves em qualquer nivel do JSON.
    if jsonb_typeof(coalesce(p_detalhes, '{}'::jsonb)) <> 'object'
       or coalesce(p_detalhes, '{}'::jsonb)::text ~* '"(latitude|longitude|precisao[a-z_]*)"[[:space:]]*:' then
      raise exception 'Detalhes da auditoria de localização não podem conter coordenadas.'
        using errcode = 'AG003';
    end if;
  end if;

  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
  values (p_tabela, p_registro_id, p_acao, p_ator_id, p_de, p_para, coalesce(p_detalhes, '{}'::jsonb))
  returning id into v_id;
  return v_id;
end;
$$;

revoke all on function public.registrar_auditoria(text, uuid, text, uuid, text, text, jsonb)
  from public, anon, authenticated;
grant execute on function public.registrar_auditoria(text, uuid, text, uuid, text, text, jsonb)
  to service_role;

-- ---------------------------------------------------------------------------
-- Autoverificacao
-- ---------------------------------------------------------------------------
do $$
declare
  v_oid oid := to_regprocedure('public.registrar_auditoria(text, uuid, text, uuid, text, text, jsonb)');
  v_def text;
  v_sec boolean;
  v_cfg text[];
begin
  if v_oid is null then
    raise exception 'registrar_auditoria ausente.';
  end if;
  select pg_get_functiondef(v_oid), prosecdef, proconfig into v_def, v_sec, v_cfg
    from pg_proc where oid = v_oid;
  if position('''localizacao_lida''' in v_def) = 0 then
    raise exception 'registrar_auditoria sem a acao localizacao_lida.';
  end if;
  foreach v_def in array array['acao_administrativa', 'acesso_negado', 'relatorio_gerado', 'relatorio_enviado'] loop
    if position('''' || v_def || '''' in pg_get_functiondef(v_oid)) = 0 then
      raise exception 'registrar_auditoria perdeu a acao %.', v_def;
    end if;
  end loop;
  if not v_sec or v_cfg is null or not ('search_path=""' = any (v_cfg)) then
    raise exception 'registrar_auditoria deve ser SECURITY DEFINER com search_path vazio.';
  end if;
  if has_function_privilege('anon', v_oid, 'execute')
     or has_function_privilege('authenticated', v_oid, 'execute')
     or not has_function_privilege('service_role', v_oid, 'execute') then
    raise exception 'Grants de registrar_auditoria divergentes (so service_role executa).';
  end if;
end
$$;
