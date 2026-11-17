-- Migration: registrar_auditoria() - lista branca de chaves de `detalhes` tambem para
-- 'acao_administrativa' e 'acesso_negado' (achado B3 da auditoria 2026-11-12).
-- Nova migration; NAO edita 20261113120400 (versao vigente da funcao, da qual esta parte).
-- Idempotente (create or replace + revokes/grants repetiveis).
--
-- Motivo: nessas duas acoes `detalhes` era livre; um erro no backend poderia gravar dado pessoal
-- (e-mail, nome, telefone, filtros) na auditoria append-only, que nunca e apagada (LGPD).
--
-- Chaves levantadas no backend (grep em backend/app, 2026-11-16):
--   acao_administrativa:
--     services/acessos.py          detalhes={"recurso": "acessos"}       (tabela perfis, registro nulo)
--     services/admin_usuarios.py   detalhes={"campos": ["nome"]}         (tabela perfis)
--   acesso_negado: nenhuma chamada no backend hoje (so aceita detalhes vazio ou 'recurso').
--
-- Regras novas (so para essas duas acoes):
--   (a) detalhes deve ser objeto JSON;
--   (b) acao_administrativa: chaves exatas permitidas 'recurso' e 'campos';
--       acesso_negado: chave exata permitida 'recurso';
--   (c) 'recurso': string identificador (^[a-z][a-z0-9_]{0,63}$);
--   (d) 'campos': array (1 a 32 itens) de strings identificador (mesmo formato), sem valores;
--   (e) qualquer violacao -> AG003. Os valores ficam restritos a identificadores para que nenhum
--       dado pessoal (e-mail, nome, telefone, texto livre) entre pela porta dos valores.
-- Chave nova no backend exige migration nova ampliando a lista (e teste).
-- Mantidos sem mudanca: acoes aceitas, limite 64 de de/para, ator existente (AG002), regras de
-- relatorio_* e localizacao_lida, insert, SECURITY DEFINER, search_path vazio, grants (so service_role).
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
  v_id       bigint;
  v_det      jsonb := coalesce(p_detalhes, '{}'::jsonb);
  v_chaves   text[];
  v_campos   jsonb;
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

  -- NOVO (B3): lista branca para acao_administrativa e acesso_negado.
  if p_acao in ('acao_administrativa', 'acesso_negado') then
    v_chaves := case p_acao when 'acao_administrativa' then array['recurso', 'campos']
                            else array['recurso'] end;
    if jsonb_typeof(v_det) <> 'object'
       or exists (select 1 from jsonb_object_keys(v_det) as k where k <> all (v_chaves)) then
      raise exception 'Detalhes da auditoria administrativa fora da lista permitida.'
        using errcode = 'AG003';
    end if;
    if v_det ? 'recurso'
       and (jsonb_typeof(v_det -> 'recurso') <> 'string'
            or (v_det ->> 'recurso') !~ '^[a-z][a-z0-9_]{0,63}$') then
      raise exception 'Detalhes da auditoria administrativa fora da lista permitida.'
        using errcode = 'AG003';
    end if;
    if v_det ? 'campos' then
      v_campos := v_det -> 'campos';
      if jsonb_typeof(v_campos) <> 'array'
         or jsonb_array_length(v_campos) not between 1 and 32
         or exists (select 1 from jsonb_array_elements(v_campos) as e
                     where jsonb_typeof(e) <> 'string'
                        or (e #>> '{}') !~ '^[a-z][a-z0-9_]{0,63}$') then
        raise exception 'Detalhes da auditoria administrativa fora da lista permitida.'
          using errcode = 'AG003';
      end if;
    end if;
  end if;

  if p_acao in ('relatorio_gerado', 'relatorio_enviado') then
    if p_ator_id is null then
      raise exception 'Auditoria de relatório exige o responsável.' using errcode = 'AG002';
    end if;
    if p_tabela is distinct from (case p_acao when 'relatorio_gerado' then 'relatorios'
                                              else 'envios_email' end) then
      raise exception 'Tabela de auditoria incompatível com a ação de relatório.' using errcode = 'AG003';
    end if;
    if jsonb_typeof(v_det) <> 'object'
       or exists (select 1 from jsonb_object_keys(v_det) as k
                   where k <> all (array['tipo', 'filtros', 'formato', 'via', 'destinatario_id',
                                         'total_linhas']))
       or position('@' in v_det::text) > 0 then
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
    if p_registro_id is null then
      raise exception 'Auditoria de localização exige o registro (requisição) consultado.'
        using errcode = 'AG003';
    end if;
    if jsonb_typeof(v_det) <> 'object'
       or exists (select 1 from jsonb_object_keys(v_det) as k
                   where k <> all (array['requisicao_id', 'itens'])) then
      raise exception 'Detalhes da auditoria de localização fora da lista permitida.'
        using errcode = 'AG003';
    end if;
  end if;

  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
  values (p_tabela, p_registro_id, p_acao, p_ator_id, p_de, p_para, v_det)
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
  v_oid  oid := to_regprocedure('public.registrar_auditoria(text, uuid, text, uuid, text, text, jsonb)');
  v_def  text;
  v_sec  boolean;
  v_cfg  text[];
  v_acao text;
begin
  if v_oid is null then
    raise exception 'registrar_auditoria ausente.';
  end if;
  select pg_get_functiondef(v_oid), prosecdef, proconfig into v_def, v_sec, v_cfg
    from pg_proc where oid = v_oid;
  if position('array[''recurso'', ''campos'']' in v_def) = 0
     or position('else array[''recurso''] end' in v_def) = 0
     or position('^[a-z][a-z0-9_]{0,63}$' in v_def) = 0 then
    raise exception 'registrar_auditoria sem a lista branca de acao_administrativa/acesso_negado.';
  end if;
  -- Guarda herdada de 20261113120400: nada de lista negra de coordenadas.
  if position('precisao' in v_def) > 0 or position('~*' in v_def) > 0 then
    raise exception 'registrar_auditoria voltou a usar lista negra de coordenadas.';
  end if;
  -- Teste funcional: chave fora da lista deve ser recusada (a recusa nao grava linha).
  begin
    perform public.registrar_auditoria('perfis', null, 'acao_administrativa', null, null, null,
                                       '{"email": "x"}'::jsonb);
    raise exception 'registrar_auditoria aceitou chave fora da lista branca.' using errcode = 'P0001';
  exception when sqlstate 'AG003' then
    null;
  end;
  foreach v_acao in array array['acao_administrativa', 'acesso_negado', 'relatorio_gerado',
                                'relatorio_enviado', 'localizacao_lida'] loop
    if position('''' || v_acao || '''' in v_def) = 0 then
      raise exception 'registrar_auditoria perdeu a acao %.', v_acao;
    end if;
  end loop;
  if position('array[''requisicao_id'', ''itens'']' in v_def) = 0
     or position('''destinatario_id''' in v_def) = 0 then
    raise exception 'registrar_auditoria perdeu as listas brancas de relatorio/localizacao.';
  end if;
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
