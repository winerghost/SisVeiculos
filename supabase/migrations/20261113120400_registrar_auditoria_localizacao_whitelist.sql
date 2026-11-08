-- Migration: registrar_auditoria() - bloco 'localizacao_lida' passa de lista negra (regex) para lista branca.
-- Nova migration; NAO edita 20261113120300. Idempotente (create or replace + grants repetiveis). Depende de
-- 20261113120300 (versao vigente da funcao).
--
-- Motivo: a regex que procurava "latitude"/"longitude"/"precisao*" era contornavel (lat, lng, coords,
-- altitude, precisao-m, Latitude, ...). Dado de localizacao e sensivel (LGPD); lista branca e o padrao ja
-- usado no bloco de relatorio (20261109120000).
--
-- O que muda (SO o bloco de 'localizacao_lida' e a autoverificacao):
--   (a) detalhes: objeto JSON cujas UNICAS chaves permitidas (nivel raiz, exatas) sao requisicao_id e itens;
--       qualquer outra chave -> AG003. Sem regex de coordenadas.
--   (b) p_registro_id passa a ser obrigatorio nessa acao (nulo -> AG003).
--   (c) autoverificacao usa variavel propria (v_acao) no loop, sem reutilizar v_def.
-- Mantidos: ator admin ATIVO existente (AG002), tabela 'requisicoes' (AG003), assinatura, SECURITY DEFINER,
-- search_path vazio, limite 64 de de/para, regras de relatorio, insert, revokes/grants (so service_role).
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
    -- Registro auditado obrigatorio (a requisicao cujas localizacoes foram lidas).
    if p_registro_id is null then
      raise exception 'Auditoria de localização exige o registro (requisição) consultado.'
        using errcode = 'AG003';
    end if;
    -- Lista branca (nunca coordenadas): so requisicao_id e itens, em chaves exatas (case-sensitive).
    if jsonb_typeof(coalesce(p_detalhes, '{}'::jsonb)) <> 'object'
       or exists (select 1 from jsonb_object_keys(coalesce(p_detalhes, '{}'::jsonb)) as k
                   where k <> all (array['requisicao_id', 'itens'])) then
      raise exception 'Detalhes da auditoria de localização fora da lista permitida.'
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
  v_acao text;
begin
  if v_oid is null then
    raise exception 'registrar_auditoria ausente.';
  end if;
  select pg_get_functiondef(v_oid), prosecdef, proconfig into v_def, v_sec, v_cfg
    from pg_proc where oid = v_oid;
  if position('''localizacao_lida''' in v_def) = 0 then
    raise exception 'registrar_auditoria sem a acao localizacao_lida.';
  end if;
  if position('precisao' in v_def) > 0 or position('~*' in v_def) > 0 then
    raise exception 'registrar_auditoria ainda usa lista negra de coordenadas.';
  end if;
  foreach v_acao in array array['acao_administrativa', 'acesso_negado', 'relatorio_gerado', 'relatorio_enviado'] loop
    if position('''' || v_acao || '''' in pg_get_functiondef(v_oid)) = 0 then
      raise exception 'registrar_auditoria perdeu a acao %.', v_acao;
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
