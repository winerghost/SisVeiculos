-- 20261204120100_email_relatorio_sem_observacao.sql
--
-- Auditoria 2026-11-12, item B4 (Baixa). public.reservar_envio_email (ultima definicao:
-- 20261115120000) nao barrava a reserva de relatorio que pede a observacao (texto livre). Hoje so o
-- backend restringe (dominio institucional). Esta migration redefine a funcao, MESMA assinatura,
-- mesmo retorno e mesmas permissoes, acrescentando UMA checagem logo apos a validacao de parametros:
--
--   recusa (AG027) quando p_filtros pede a observacao: chave 'incluir_observacao' presente e
--   diferente de false/null, OU o array 'colunas' contem 'observacao'.
--
-- Alternativa descartada: exigir papel admin/autorizador. Escolhida a recusa total por ser a mais
-- segura: (1) a observacao e texto livre e pode carregar qualquer dado pessoal (LGPD: minimizacao);
-- (2) um e-mail enviado nao pode ser revogado, ao contrario do download autenticado na tela;
-- (3) o papel do solicitante nao limita o destino final da mensagem (encaminhamento, caixa
-- compartilhada), entao o controle por papel nao reduz o risco que importa; (4) regra simples,
-- sem depender de perfis/roles, nao tem como ser contornada por erro de papel.
-- Reutiliza AG027 (o backend ja o traduz em RequisicaoInvalida 400 'envio_invalido', mensagem
-- generica): nenhum codigo novo. O backend ainda oferece o envio com observacao a dominio
-- institucional e so recebe a recusa depois de gerar o arquivo: deve recusar antes (ver relatorio).
-- Demais validacoes, ordem, dedupe, cotas, lock e grants: identicos a 20261115120000.

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
  v_dup     uuid;
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

  -- B4 (auditoria 2026-11-12): a observacao e texto livre (pode conter qualquer dado pessoal) e o
  -- e-mail sai do canal controlado sem como ser recolhido. Nenhum relatorio com observacao e
  -- reservado para envio, seja qual for o papel: so o download na tela a mostra. Pedido = chave
  -- incluir_observacao presente e diferente de false/null, ou 'observacao' em filtros.colunas.
  if coalesce(v_filtros->'incluir_observacao', 'null'::jsonb) not in ('false'::jsonb, 'null'::jsonb)
     or (jsonb_typeof(v_filtros->'colunas') = 'array' and (v_filtros->'colunas') ? 'observacao') then
    raise exception 'Parâmetros do envio inválidos: a observação não é enviada por e-mail.'
      using errcode = 'AG027';
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

  -- Dedupe (duplo clique/retentativa): envio em andamento identico nos ultimos 30 s e devolvido
  -- sem inserir e sem consumir cota. Sob o advisory lock global, duas chamadas simultaneas veem
  -- uma a linha da outra. Atende ix_envios_email_solicitado_por_criado_em.
  select e.id into v_dup
    from public.envios_email e
   where e.solicitado_por = p_solicitante
     and e.criado_em > v_agora - interval '30 seconds'
     and e.status in ('pendente', 'enviando')
     and e.destinatario_id = p_destinatario
     and e.tipo_relatorio = p_tipo
     and e.formato = p_formato
     and e.filtros = v_filtros
   order by e.criado_em desc
   limit 1;
  if v_dup is not null then
    return jsonb_build_object('id', v_dup, 'duplicado', true);
  end if;

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

  return jsonb_build_object('id', v_id, 'duplicado', false);
end;
$$;

comment on function public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer, integer) is
  'Reserva um envio de relatorio por e-mail (recurso, solicitante, destinatario ativo, recusa observacao (AG027), dedupe de 30 s para pendente/enviando identico, cota global/hora e cota opcional por usuario/hora, sob lock). Retorna {id, duplicado}. Somente service_role.';

revoke all on function public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer, integer)
  from public, anon, authenticated;
grant execute on function public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer, integer)
  to service_role;
