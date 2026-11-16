-- Migration: atualizacao em tempo real (Fase 1, polling por versao). Nova migration; nao edita as anteriores.
-- Idempotente. Depende de 20261107120400 (auditoria), 20261107121900 (checklists), 20261107122410 (perfil_fotos),
-- 20261110120000 (notificacoes), 20261111120100 (requisicoes.motorista_id), 20261117120000 (papel vigilante) e
-- 20261119120300 (requisicoes_arquivadas).
--
-- Motivo (contrato aprovado em 2026-11-16): GET /api/atualizacoes devolve, por recurso, um valor opaco que muda
-- quando algo relevante PARA AQUELE PERFIL muda. Esta funcao devolve os MARCADORES CRUS (ids/carimbos/contagens);
-- o backend aplica HMAC-SHA256 com o perfil e o recurso antes de responder, entao nenhum id cru nem volume global
-- chega ao cliente (anti canal lateral).
--
-- 1. public.atualizacoes_versao(p_usuario uuid) -> jsonb
--    {"requisicoes": text, "notificacoes": text, "veiculos": text, "configuracoes": text,
--     "usuarios": text|null (so admin), "nao_lidas": int}
--    Papel e ativo lidos de public.perfis (nunca do chamador). Perfil inexistente ou inativo: todos os marcadores
--    '0' e nao_lidas 0 (o backend ja recusou antes; aqui e defesa em profundidade, sem revelar nada).
--    Relevancia de requisicoes (mesma visibilidade de ServicoRequisicoes.listar/obter):
--      autorizador/admin: todas; solicitante: as proprias; motorista: as proprias + as atribuidas a ele + as
--      designacoes em que ele entrou ou saiu (auditoria motorista_designado de/para); vigilante: as proprias,
--      as autorizada/em_uso, as que ele registrou e toda transicao que entra ou sai de autorizada/em_uso.
--    Marcador de requisicoes = max(auditoria.id) relevante (exceto leituras: localizacao_lida, e linhas sem
--      registro: localizacao_expurgada, acao_administrativa, acesso_negado, relatorio_*) + max(atualizado_em) das
--      requisicoes visiveis (pega qualquer UPDATE, auditado ou nao) + contagem/max(registrado_em) dos checklists
--      das visiveis + md5 do conjunto arquivado PELO PROPRIO usuario (desarquivar apaga a linha).
--    notificacoes: contagem, nao lidas, max(criada_em), max(lida_em) do proprio usuario (marcar lida muda).
--    veiculos: max(auditoria.id) tabela veiculos + max(veiculos.atualizado_em).
--    configuracoes: max(auditoria.id) tabela configuracoes exceto dados_exportados (leitura) + max(atualizado_em).
--    usuarios (admin): auditoria de perfis/convites/perfil_fotos exceto leituras e eventos de sessao (passkey
--      autenticada/falha/desafio/regressao, acesso_negado, consulta do historico de acessos) + perfis.atualizado_em
--      + estado de convites (contagem, criado/usado/revogado/expira) + perfil_fotos (contagem, criado/removida).
-- 2. Indices: auditoria (tabela, id) para o max por tabela; auditoria (tabela, registro_id, id) para o max por
--    requisicao visivel; requisicoes (motorista_id) parcial; requisicoes (atualizado_em).
-- Seguranca: SECURITY DEFINER, search_path vazio, STABLE (so leitura, nao grava nada, nem auditoria), EXECUTE so
-- para service_role (revogado de public, anon, authenticated). Nenhum dado pessoal no retorno.
-- Fuso: sem colunas novas (timestamptz, UTC; exibicao em America/Sao_Paulo). RLS: nao muda.

set local lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 1. Indices
-- ---------------------------------------------------------------------------
create index if not exists ix_auditoria_tabela_id
  on public.auditoria (tabela, id);
create index if not exists ix_auditoria_tabela_registro_id
  on public.auditoria (tabela, registro_id, id);
create index if not exists ix_requisicoes_motorista_id
  on public.requisicoes (motorista_id) where motorista_id is not null;
create index if not exists ix_requisicoes_atualizado_em
  on public.requisicoes (atualizado_em);

-- ---------------------------------------------------------------------------
-- 2. Funcao
-- ---------------------------------------------------------------------------
create or replace function public.atualizacoes_versao(p_usuario uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_role        text;
  v_ativo       boolean;
  v_staff       boolean;
  v_aud_req     bigint;
  v_upd_req     timestamptz;
  v_ck_qtd      bigint;
  v_ck_max      timestamptz;
  v_arq         text;
  v_req         text;
  v_not         text;
  v_nao_lidas   bigint;
  v_vei         text;
  v_cfg         text;
  v_usu         text;
  -- Acoes de requisicoes que NAO mudam o que a tela mostra (leitura) ou nao tem registro.
  c_ignorar_req constant text[] := array['localizacao_lida', 'localizacao_expurgada', 'acao_administrativa',
                                         'acesso_negado', 'relatorio_gerado', 'relatorio_enviado'];
  c_ignorar_usu constant text[] := array['passkey_autenticada', 'passkey_falha', 'passkey_desafio_invalido',
                                         'passkey_regressao_contador', 'acesso_negado'];
begin
  select p.role::text, p.ativo into v_role, v_ativo from public.perfis p where p.id = p_usuario;
  if p_usuario is null or v_role is null or not v_ativo then
    return jsonb_build_object('requisicoes', '0', 'notificacoes', '0', 'veiculos', '0',
                              'configuracoes', '0', 'usuarios', null, 'nao_lidas', 0);
  end if;
  v_staff := v_role in ('autorizador', 'admin');

  -- -- requisicoes ---------------------------------------------------------------
  if v_staff then
    select max(a.id) into v_aud_req
      from public.auditoria a
     where a.tabela in ('requisicoes', 'checklists') and a.acao <> all (c_ignorar_req);
    select max(r.atualizado_em) into v_upd_req from public.requisicoes r;
    select count(*), max(ck.registrado_em) into v_ck_qtd, v_ck_max from public.checklists ck;
  else
    -- Requisicoes visiveis ao perfil, no estado atual.
    with visiveis as (
      select r.id, r.atualizado_em
        from public.requisicoes r
       where r.usuario_id = p_usuario
          or (v_role = 'motorista' and r.motorista_id = p_usuario)
          or (v_role = 'vigilante' and (r.status::text in ('autorizada', 'em_uso')
                                        or r.saida_registrada_por = p_usuario
                                        or r.chegada_registrada_por = p_usuario))
    )
    select
      greatest(
        (select max(a.id) from public.auditoria a join visiveis v on v.id = a.registro_id
          where a.tabela = 'requisicoes' and a.acao <> all (c_ignorar_req)),
        (select max(a.id) from public.auditoria a join public.checklists ck on ck.id = a.registro_id
           join visiveis v on v.id = ck.requisicao_id
          where a.tabela = 'checklists'),
        -- Motorista: entrou ou saiu da viagem (a removida deixa de ser visivel, mas a lista muda).
        (select max(a.id) from public.auditoria a
          where v_role = 'motorista' and a.tabela = 'requisicoes' and a.acao = 'motorista_designado'
            and (a.de = p_usuario::text or a.para = p_usuario::text)),
        -- Vigilante: entrada/saida de autorizada/em_uso em qualquer requisicao (e o que ele enxerga).
        (select max(a.id) from public.auditoria a
          where v_role = 'vigilante' and a.tabela = 'requisicoes' and a.acao = 'transicao'
            and (a.de in ('autorizada', 'em_uso') or a.para in ('autorizada', 'em_uso')))
      ),
      (select max(v.atualizado_em) from visiveis v),
      (select count(*) from public.checklists ck join visiveis v on v.id = ck.requisicao_id),
      (select max(ck.registrado_em) from public.checklists ck join visiveis v on v.id = ck.requisicao_id)
    into v_aud_req, v_upd_req, v_ck_qtd, v_ck_max;
  end if;

  select md5(coalesce(string_agg(ra.requisicao_id::text, ',' order by ra.requisicao_id), ''))
    into v_arq
    from public.requisicoes_arquivadas ra
   where ra.usuario_id = p_usuario;

  v_req := concat_ws(':', coalesce(v_aud_req, 0), coalesce(extract(epoch from v_upd_req)::text, '0'),
                     coalesce(v_ck_qtd, 0), coalesce(extract(epoch from v_ck_max)::text, '0'), v_arq);

  -- -- notificacoes (so as do proprio usuario) -----------------------------------
  select concat_ws(':', count(*), count(*) filter (where n.lida_em is null),
                   coalesce(extract(epoch from max(n.criada_em))::text, '0'),
                   coalesce(extract(epoch from max(n.lida_em))::text, '0')),
         count(*) filter (where n.lida_em is null)
    into v_not, v_nao_lidas
    from public.notificacoes n
   where n.usuario_id = p_usuario;

  -- -- veiculos e configuracoes (o mesmo para todos os perfis) ---------------------
  select concat_ws(':',
           coalesce((select max(a.id) from public.auditoria a where a.tabela = 'veiculos'), 0),
           coalesce((select extract(epoch from max(ve.atualizado_em))::text from public.veiculos ve), '0'))
    into v_vei;
  select concat_ws(':',
           coalesce((select max(a.id) from public.auditoria a
                      where a.tabela = 'configuracoes' and a.acao <> 'dados_exportados'), 0),
           coalesce((select extract(epoch from max(cf.atualizado_em))::text from public.configuracoes cf), '0'))
    into v_cfg;

  -- -- usuarios (so admin) ---------------------------------------------------------
  if v_role = 'admin' then
    select concat_ws(':',
             coalesce((select max(a.id) from public.auditoria a
                        where a.tabela in ('perfis', 'convites', 'perfil_fotos')
                          and a.acao <> all (c_ignorar_usu)
                          and not (a.acao = 'acao_administrativa'
                                   and a.detalhes ->> 'recurso' = 'acessos')), 0),
             coalesce((select extract(epoch from max(p.atualizado_em))::text from public.perfis p), '0'),
             (select concat_ws('/', count(*),
                               coalesce(extract(epoch from max(cv.criado_em))::text, '0'),
                               coalesce(extract(epoch from max(cv.usado_em))::text, '0'),
                               coalesce(extract(epoch from max(cv.revogado_em))::text, '0'),
                               coalesce(extract(epoch from max(cv.expira_em))::text, '0'))
                from public.convites cv),
             (select concat_ws('/', count(*),
                               coalesce(extract(epoch from max(pf.criado_em))::text, '0'),
                               coalesce(extract(epoch from max(pf.removida_em))::text, '0'))
                from public.perfil_fotos pf))
      into v_usu;
  end if;

  return jsonb_build_object('requisicoes', v_req, 'notificacoes', v_not, 'veiculos', v_vei,
                            'configuracoes', v_cfg, 'usuarios', v_usu, 'nao_lidas', v_nao_lidas);
end;
$$;

comment on function public.atualizacoes_versao(uuid) is
  'Marcadores crus de versao por recurso e perfil para GET /api/atualizacoes (o backend aplica HMAC antes de responder). Somente leitura; sem dado pessoal. EXECUTE so service_role.';

revoke all on function public.atualizacoes_versao(uuid) from public, anon, authenticated;
grant execute on function public.atualizacoes_versao(uuid) to service_role;
