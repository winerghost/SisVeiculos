-- Migration: RPCs atomicas para reenvio de convite e dedupe do envio de relatorio por e-mail.
-- Nova migration; nao edita as anteriores. Idempotente (create or replace / drop if exists).
-- Origem: AUDITORIA.md, etapas 3 e 4 (reenvio de convite e envio de relatorio por e-mail).
--
-- A. public.reenviar_convite(p_convite_id, p_ator, p_token_hash, p_expira_em) -> jsonb
--    Motivo: o backend entregava o e-mail ANTES de revogar o convite antigo e gravar o novo, com
--    controle de concorrencia so em memoria. Agora revogar o convite aberto e inserir o novo
--    (novo token_hash e validade; mesmo e-mail, papel e unidade) acontecem numa UNICA transacao,
--    sob SELECT ... FOR UPDATE no convite antigo. Duas chamadas simultaneas: a segunda espera o
--    commit da primeira, ve o convite ja revogado e recebe AG038. O backend deve enviar o e-mail
--    DEPOIS do retorno (se a entrega falhar, revoga o novo convite com a rotina existente).
--    A auditoria continua append-only e e gerada pelos triggers de convites (convite_revogado
--    e convite_criado), sem e-mail nem hash (LGPD). O indice ux_convites_email_aberto segue
--    valendo: o antigo e revogado antes do INSERT do novo, na mesma transacao.
--    Retorno: jsonb com as colunas publicas do NOVO convite (id, email, role, unidade, criado_por,
--    criado_em, expira_em, usado_em, usado_por, revogado_em, revogado_por). NUNCA devolve token_hash.
--    Erros:
--      AG002 responsavel (p_ator) ausente, inativo ou nao admin
--      AG007 convite inexistente
--      AG038 NOVO: convite nao reenviavel (ja usado, ja revogado ou reenviado por outra chamada
--            concorrente). Convite apenas expirado PODE ser reenviado (regra atual do backend).
--      AG039 NOVO: parametros invalidos (hash fora de 64 hex minusculos, validade nula, no passado
--            ou acima de 30 dias menos 1 minuto)
--      23505 token_hash ja existente (ux_convites_token_hash): tudo e desfeito, o antigo segue aberto
--
-- B. public.reservar_envio_email: mesma assinatura (7 argumentos), mesmas permissoes. Passa a
--    deduplicar: se houver envio 'pendente' ou 'enviando' do mesmo (solicitante, destinatario, tipo,
--    formato, filtros) criado nos ultimos 30 s, devolve esse envio, NAO insere e NAO consome cota
--    (a checagem fica antes das cotas, sob o mesmo advisory lock global, entao duplo clique
--    simultaneo tambem deduplica). Retorno: {"id": uuid, "duplicado": boolean}; 'duplicado' = true
--    quando devolveu o envio existente (o backend NAO deve enviar outro e-mail nesse caso). A chave
--    "id" continua presente: chamadores antigos seguem validos. Ordem das validacoes:
--    parametros AG027 -> recurso AG026 -> solicitante AG002 -> destinatario AG024 -> dedupe ->
--    cota global AG025 -> cota por usuario AG025. Envio ja 'enviado' ou 'falhou' nao deduplica.
--
-- C. public.reaper_envios_email(p_minutos integer default 15) -> integer
--    Motivo: o backend perde envios em memoria ao reiniciar; linhas 'pendente' (reservadas sem
--    envio) e 'enviando' (worker morto) ficariam presas para sempre. A funcao move para 'falhou'
--    com erro_codigo 'timeout' os envios 'pendente'/'enviando' parados ha mais de p_minutos
--    (usa atualizado_em, que o trigger renova a cada UPDATE), com FOR UPDATE SKIP LOCKED (como o
--    reaper de notificacoes: 15 min). Devolve quantos moveu. Idempotente. Nao ha pg_cron
--    presumido: o backend (ou o pipeline) chama a RPC periodicamente, p.ex. ao iniciar e a cada
--    poucos minutos. Um envio marcado 'falhou' pode voltar a 'enviando' (transicao ja permitida).
--    Atencao: se um worker lento marcar 'enviado' depois do reaper, a transicao falhou->enviado e
--    invalida (AG001); o backend deve tratar esse erro como benigno. Erro: AG027 (p_minutos fora
--    de 5..1440).
--
-- Permissoes: SECURITY DEFINER, search_path vazio, EXECUTE so para service_role (revoke de
-- public/anon/authenticated). RLS/grants de tabelas inalterados (deny-by-default). timestamptz
-- sempre (armazenado em UTC; exibicao em America/Sao_Paulo). Sem DELETE fisico.
-- Codigos novos: AG038, AG039 (proximo livre: AG040).

-- ---------------------------------------------------------------------------
-- A. reenviar_convite
-- ---------------------------------------------------------------------------
create or replace function public.reenviar_convite(
  p_convite_id  uuid,
  p_ator        uuid,
  p_token_hash  text,
  p_expira_em   timestamptz
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_agora timestamptz := clock_timestamp();
  v_ant   public.convites;
  v_novo  public.convites;
begin
  if p_convite_id is null
     or p_token_hash is null or p_token_hash !~ '^[0-9a-f]{64}$'
     or p_expira_em is null
     or p_expira_em <= v_agora
     or p_expira_em > v_agora + interval '30 days' - interval '1 minute' then
    raise exception 'Parâmetros do reenvio de convite inválidos.' using errcode = 'AG039';
  end if;

  -- Admin ativo primeiro (lock compartilhado de perfis; mesma ordem dos triggers de convites,
  -- evitando inversao de locks): ator invalido => AG002 sem tocar no convite.
  perform privado.exigir_admin_ativo(p_ator);

  -- Serializa reenvios do mesmo convite. READ COMMITTED: quem espera relê a linha atualizada.
  select * into v_ant from public.convites c where c.id = p_convite_id for update;
  if not found then
    raise exception 'Convite inválido.' using errcode = 'AG007';
  end if;
  if v_ant.usado_em is not null or v_ant.revogado_em is not null then
    raise exception 'Este convite já foi utilizado ou revogado.' using errcode = 'AG038';
  end if;

  -- revogado_em e carimbado pelo trigger; o par (revogado_em, revogado_por) e exigido pelo CHECK.
  update public.convites c
     set revogado_em = v_agora, revogado_por = p_ator
   where c.id = v_ant.id;

  -- criado_em e do servidor (trigger); o antigo ja saiu do indice parcial ux_convites_email_aberto.
  insert into public.convites (email, role, unidade, token_hash, criado_por, expira_em)
  values (v_ant.email, v_ant.role, v_ant.unidade, p_token_hash, p_ator, p_expira_em)
  returning * into v_novo;

  return jsonb_build_object(
    'id', v_novo.id,
    'email', v_novo.email,
    'role', v_novo.role,
    'unidade', v_novo.unidade,
    'criado_por', v_novo.criado_por,
    'criado_em', v_novo.criado_em,
    'expira_em', v_novo.expira_em,
    'usado_em', v_novo.usado_em,
    'usado_por', v_novo.usado_por,
    'revogado_em', v_novo.revogado_em,
    'revogado_por', v_novo.revogado_por
  );
end;
$$;

comment on function public.reenviar_convite(uuid, uuid, text, timestamptz) is
  'Revoga o convite aberto/expirado e insere o novo (mesmo e-mail, papel, unidade) numa unica transacao sob FOR UPDATE. AG038 se encerrado ou concorrente. Nao devolve token_hash. Somente service_role.';

revoke all on function public.reenviar_convite(uuid, uuid, text, timestamptz)
  from public, anon, authenticated;
grant execute on function public.reenviar_convite(uuid, uuid, text, timestamptz) to service_role;

-- ---------------------------------------------------------------------------
-- B. reservar_envio_email com dedupe (mesma assinatura da migration 20261109130000)
-- ---------------------------------------------------------------------------
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
  'Reserva um envio de relatorio por e-mail (recurso, solicitante, destinatario ativo, dedupe de 30 s para pendente/enviando identico, cota global/hora e cota opcional por usuario/hora, sob lock). Retorna {id, duplicado}. Somente service_role.';

revoke all on function public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer, integer)
  from public, anon, authenticated;
grant execute on function public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer, integer)
  to service_role;

-- ---------------------------------------------------------------------------
-- C. reaper de envios presos
-- ---------------------------------------------------------------------------
create or replace function public.reaper_envios_email(p_minutos integer default 15)
returns integer
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_min integer := coalesce(p_minutos, 15);
  v_qtd integer;
begin
  if v_min < 5 or v_min > 1440 then
    raise exception 'Prazo do reaper de envios inválido (5 a 1440 minutos).' using errcode = 'AG027';
  end if;

  with presos as (
    select x.id
      from public.envios_email x
     where x.status in ('pendente', 'enviando')
       and x.atualizado_em < clock_timestamp() - make_interval(mins => v_min)
     for update skip locked
  ),
  u as (
    update public.envios_email e
       set status = 'falhou', erro_codigo = 'timeout'
      from presos
     where e.id = presos.id
    returning e.id
  )
  select count(*)::integer into v_qtd from u;

  return v_qtd;
end;
$$;

comment on function public.reaper_envios_email(integer) is
  'Move para falhou/timeout os envios pendente ou enviando parados ha mais de p_minutos (padrao 15). Devolve a quantidade. Somente service_role.';

revoke all on function public.reaper_envios_email(integer) from public, anon, authenticated;
grant execute on function public.reaper_envios_email(integer) to service_role;

-- ---------------------------------------------------------------------------
-- Autoverificacao: permissoes e propriedades das tres funcoes.
-- ---------------------------------------------------------------------------
do $$
declare
  f text;
  r text;
begin
  foreach f in array array[
    'public.reenviar_convite(uuid, uuid, text, timestamptz)',
    'public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer, integer)',
    'public.reaper_envios_email(integer)'
  ] loop
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
  end loop;

  if (select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace
       where n.nspname = 'public' and p.proname = 'reservar_envio_email') <> 1 then
    raise exception 'reservar_envio_email deve ter uma unica assinatura.';
  end if;
end
$$;
