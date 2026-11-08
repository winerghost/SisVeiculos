-- Migration: notificacoes (Entrega 3a, camada de banco: central no app + e-mail de criticos).
-- Nova migration; nao edita as anteriores. Idempotente.
--
-- Motivo: avisar solicitantes, autorizadores e admins dos eventos que importam. A notificacao nasce NO
-- BANCO, na MESMA transacao do evento, por um trigger AFTER INSERT em public.auditoria (a auditoria ja e
-- gravada na transacao da transicao/acao administrativa; rollback do evento => sem notificacao).
-- A tabela guarda so tipo + referencia + flags (+ um codigo tecnico curto): SEM texto, SEM PII, SEM e-mail.
-- O texto e montado pelo backend; o e-mail do destinatario e resolvido pelo backend no Auth (usuario_id).
--
-- 1. public.notificacoes (RLS deny-by-default; service_role so SELECT; escrita so por RPC/trigger).
--    Colunas alem das pedidas (todas sem PII):
--      codigo               text curto ^[a-z0-9_.-]{1,40}$: detalhe tecnico do evento (momento do checklist
--                           'saida'/'chegada'; recurso de configuracao; 'papel_alterado'/'desativado';
--                           codigo da falha no sistema_falha_envio).
--      email_erro_codigo    ultimo codigo curto de erro do envio.
--      email_reservado_em   instante da ultima reserva (recupera 'enviando' travado e espaca novas tentativas).
-- 2. Tipos (lista fechada) e destinatarios (sempre ativos, nunca o ator do evento; um perfil tem um unico
--    role, entao quem e staff recebe 1 so por evento):
--      requisicao_autorizada, requisicao_negada, requisicao_chegada, requisicao_cancelada_terceiros
--                                      -> solicitante (dono)                  critica
--      requisicao_saida                -> solicitante (dono)                  nao critica
--      requisicao_nova                 -> autorizadores e admins              critica
--      requisicao_cancelada_pelo_solicitante -> autorizadores e admins        nao critica
--      checklist_avaria                -> autorizadores e admins (so com configuracoes.checklist ligada,
--                                         vem da auditoria 'checklist_registrado' com detalhes.avarias=true)  critica
--      convite_aceito                  -> OUTROS admins                       nao critica
--      perfil_alterado_admin           -> OUTROS admins, exceto o alvo; so mudanca de role ou desativacao
--                                         (de/para 'role/ativo' da auditoria; unidade e reativacao nao notificam) nao critica
--      configuracao_alterada           -> OUTROS admins                       nao critica
--      sistema_falha_envio             -> admins (via registrar_notificacao_sistema)  critica
--    Descartados: convite criado/revogado/expirado, login, leitura. Sem lembretes por tempo (3c).
-- 3. Falha do trigger NAO derruba a operacao principal: a notificacao e acessoria, e um bug aqui nao pode
--    impedir autorizar/registrar saida ou chegada. O trigger captura a excecao (subtransacao: nada parcial
--    fica) e emite RAISE WARNING so com o SQLSTATE (sem valores, sem PII). Custo: a falha e silenciosa
--    fora do log; o teste do caminho normal garante que o fluxo funciona.
-- 4. Deduplicacao: indice unico PARCIAL (usuario_id, tipo, requisicao_id, referencia_id; nulls como uuid
--    zero) so para os tipos de evento unico (requisicao_*, checklist_avaria, convite_aceito: o par
--    requisicao/referencia identifica o evento). perfil_alterado_admin, configuracao_alterada e
--    sistema_falha_envio se repetem legitimamente para a mesma chave e ficam FORA do indice (o sistema
--    tem janela propria de 1 hora por tipo/codigo). checklist_avaria usa referencia_id = id do checklist
--    (saida e chegada da mesma requisicao geram uma cada).
-- 5. configuracoes.notificacao_email_criticos (padrao false) + configuracoes_validar com as 7 chaves
--    auditadas. Ligada: notificacao critica nasce com email_status='pendente'; desligada: null.
--    reservar_emails_notificacao reavalia a elegibilidade (flag ainda ligada, perfil ativo, criada ha
--    menos de 24 h; senao marca 'ignorado').
-- 6. Imutabilidade/expurgo no padrao de acessos_login: UPDATE so em lida_em (null -> agora, depois
--    imutavel) e nas colunas email_* (maquina de estados no trigger); DELETE so dentro de
--    purgar_notificacoes (variavel local sisveiculos.expurgo_notificacoes, piso de 30 dias tambem no
--    trigger); TRUNCATE sempre bloqueado.
-- 7. Sem auditoria de leitura de notificacoes.
--
-- RPCs (SECURITY DEFINER, search_path vazio, EXECUTE so service_role, jsonb):
--   listar_notificacoes(p_usuario, p_limite=20, p_offset=0, p_somente_nao_lidas=false)
--   contar_notificacoes_nao_lidas(p_usuario)        marcar_notificacao_lida(p_usuario, p_id)
--   marcar_todas_lidas(p_usuario)                   registrar_notificacao_sistema(p_tipo, p_detalhe_codigo)
--   reservar_emails_notificacao(p_limite=20)        marcar_email_notificacao(p_id, p_status, p_erro_codigo)
--   purgar_notificacoes(p_dias=90)                  (piso 30)
--
-- Erros novos (AG029+ livres; relatorios foi ate AG028; a migration de IA nao criou nenhum):
--   AG029 notificacao nao encontrada (inexistente ou de outro usuario: mesma mensagem, sem revelar existencia)
--   AG030 parametro invalido de notificacao (usuario nulo, tipo, codigo, status, offset, p_dias fora de 30..3650)
-- Reaproveitados: AG001 transicao invalida do e-mail; AG003 DELETE/TRUNCATE fora do expurgo;
--   AG004 coluna imutavel; AG002 (configuracoes_validar sem admin ativo); AG010 (abastecimento exige km).
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. configuracoes: notificacao_email_criticos + configuracoes_validar (7 chaves)
-- ---------------------------------------------------------------------------
alter table public.configuracoes
  add column if not exists notificacao_email_criticos boolean not null default false;

comment on column public.configuracoes.notificacao_email_criticos is
  'Recurso opcional: e-mail para notificacoes criticas (desligado por padrao).';

-- Mesma funcao da migration de relatorios (validacao + auditoria), agora com 7 chaves. Nada mais muda:
-- ator zerado, AG002 sem admin ativo, AG010, uma linha de auditoria por chave alterada.
create or replace function public.configuracoes_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator  uuid := new.alterado_por;
  v_chave text;
begin
  new.alterado_por := null;  -- nunca persiste

  if new.quilometragem is not distinct from old.quilometragem
     and new.abastecimento_manutencao is not distinct from old.abastecimento_manutencao
     and new.checklist is not distinct from old.checklist
     and new.mfa is not distinct from old.mfa
     and new.relatorio_email is not distinct from old.relatorio_email
     and new.relatorio_whatsapp is not distinct from old.relatorio_whatsapp
     and new.notificacao_email_criticos is not distinct from old.notificacao_email_criticos then
    return new;
  end if;

  perform privado.exigir_admin_ativo(v_ator);

  if new.abastecimento_manutencao and not new.quilometragem then
    raise exception 'Abastecimento e manutenção exigem a Quilometragem ligada.'
      using errcode = 'AG010';
  end if;

  for v_chave in
    select chave
      from jsonb_object_keys(to_jsonb(new)) as chave
     where chave = any (array['quilometragem', 'abastecimento_manutencao', 'checklist', 'mfa',
                              'relatorio_email', 'relatorio_whatsapp', 'notificacao_email_criticos'])
       and (to_jsonb(new) -> chave) is distinct from (to_jsonb(old) -> chave)
  loop
    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
    values ('configuracoes', null, 'configuracao_alterada', v_ator,
            to_jsonb(old) ->> v_chave, to_jsonb(new) ->> v_chave,
            jsonb_build_object('recurso', v_chave));
  end loop;

  return new;
end;
$$;

revoke all on function public.configuracoes_validar() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. Tabela notificacoes
-- ---------------------------------------------------------------------------
create table if not exists public.notificacoes (
  id                  uuid primary key default gen_random_uuid(),
  usuario_id          uuid not null references public.perfis (id) on delete restrict,
  tipo                text not null,
  requisicao_id       uuid references public.requisicoes (id) on delete restrict,
  referencia_id       uuid,
  codigo              text,
  critica             boolean not null,
  criada_em           timestamptz not null default now(),
  lida_em             timestamptz,
  email_status        text,
  email_tentativas    integer not null default 0,
  email_enviado_em    timestamptz,
  email_erro_codigo   text,
  email_reservado_em  timestamptz,

  constraint ck_notificacoes_tipo check (tipo in (
    'requisicao_nova', 'requisicao_autorizada', 'requisicao_negada', 'requisicao_saida',
    'requisicao_chegada', 'requisicao_cancelada_terceiros', 'requisicao_cancelada_pelo_solicitante',
    'checklist_avaria', 'convite_aceito', 'perfil_alterado_admin', 'configuracao_alterada',
    'sistema_falha_envio')),
  -- requisicao_id obrigatorio exatamente nos tipos ligados a uma requisicao.
  constraint ck_notificacoes_requisicao_coerente check (
    (requisicao_id is not null) = (tipo like 'requisicao\_%' or tipo = 'checklist_avaria')),
  constraint ck_notificacoes_codigo_formato check (codigo is null or codigo ~ '^[a-z0-9_.-]{1,40}$'),
  constraint ck_notificacoes_sistema_com_codigo check (tipo <> 'sistema_falha_envio' or codigo is not null),
  constraint ck_notificacoes_email_status
    check (email_status is null or email_status in ('pendente', 'enviando', 'enviado', 'falhou', 'ignorado')),
  -- E-mail so existe para notificacao critica.
  constraint ck_notificacoes_email_so_critica check (email_status is null or critica),
  constraint ck_notificacoes_email_tentativas check (email_tentativas between 0 and 100),
  constraint ck_notificacoes_email_enviado_em_coerente
    check ((email_status = 'enviado') = (email_enviado_em is not null)),
  constraint ck_notificacoes_email_erro_formato
    check (email_erro_codigo is null or email_erro_codigo ~ '^[A-Za-z0-9_.-]{1,40}$')
);

comment on table public.notificacoes is
  'Central de notificacoes. So tipo + referencia + flags (sem texto, sem PII, sem e-mail). Nasce por trigger em auditoria. UPDATE so de lida_em e email_*; DELETE so no expurgo (>= 30 dias).';
comment on column public.notificacoes.referencia_id is
  'Id generico do objeto do evento (convite, perfil alvo, checklist). Sem FK de proposito.';
comment on column public.notificacoes.codigo is
  'Detalhe tecnico curto (momento do checklist, recurso alterado, tipo de alteracao de perfil, codigo da falha). Nunca texto livre.';

-- Listagem/contagem por usuario (nao lidas primeiro no indice; lida_em is null e pesquisavel no btree).
create index if not exists ix_notificacoes_usuario_lida_criada
  on public.notificacoes (usuario_id, lida_em, criada_em desc);
-- Fila de e-mail (pendente + retentativas + enviando travado). Pequeno: so linhas em andamento.
create index if not exists ix_notificacoes_email_fila
  on public.notificacoes (criada_em) where email_status in ('pendente', 'falhou', 'enviando');
-- Expurgo por idade; FK requisicao_id sem indice automatico.
create index if not exists ix_notificacoes_criada_em on public.notificacoes (criada_em);
create index if not exists ix_notificacoes_requisicao_id
  on public.notificacoes (requisicao_id) where requisicao_id is not null;

-- Deduplicacao dos eventos unicos (ver cabecalho, item 4). Nulls tratados como uuid zero.
create unique index if not exists ux_notificacoes_dedupe
  on public.notificacoes (
    usuario_id, tipo,
    coalesce(requisicao_id, '00000000-0000-0000-0000-000000000000'::uuid),
    coalesce(referencia_id, '00000000-0000-0000-0000-000000000000'::uuid))
  where tipo like 'requisicao\_%' or tipo in ('checklist_avaria', 'convite_aceito');

-- RLS deny-by-default; escrita so pelas RPCs/trigger SECURITY DEFINER; service_role apenas le.
alter table public.notificacoes enable row level security;

drop policy if exists notificacoes_sem_acesso_api on public.notificacoes;
create policy notificacoes_sem_acesso_api on public.notificacoes
  for all to anon, authenticated using (false) with check (false);

revoke all on table public.notificacoes from public, anon, authenticated, service_role;
grant select on table public.notificacoes to service_role;

-- ---------------------------------------------------------------------------
-- 3. Protecao: UPDATE restrito, DELETE so no expurgo, TRUNCATE nunca
-- ---------------------------------------------------------------------------
create or replace function public.notificacoes_proteger()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'DELETE' then
    if coalesce(current_setting('sisveiculos.expurgo_notificacoes', true), '') = '1'
       and old.criada_em < now() - interval '30 days' then
      return old;
    end if;
    raise exception 'Operação % proibida em notificacoes: o histórico só é removido pelo expurgo.', tg_op
      using errcode = 'AG003';
  end if;

  -- UPDATE ------------------------------------------------------------------
  if new.id is distinct from old.id
     or new.usuario_id is distinct from old.usuario_id
     or new.tipo is distinct from old.tipo
     or new.requisicao_id is distinct from old.requisicao_id
     or new.referencia_id is distinct from old.referencia_id
     or new.codigo is distinct from old.codigo
     or new.critica is distinct from old.critica
     or new.criada_em is distinct from old.criada_em then
    raise exception 'Campos de identidade da notificação são imutáveis.' using errcode = 'AG004';
  end if;

  -- lida_em: null -> agora (carimbo do servidor); depois imutavel.
  if old.lida_em is not null then
    if new.lida_em is distinct from old.lida_em then
      raise exception 'lida_em já gravado não pode mudar.' using errcode = 'AG004';
    end if;
  elsif new.lida_em is not null then
    new.lida_em := clock_timestamp();
  end if;

  -- E-mail: nunca nasce depois (null <-> valor) e segue a maquina de estados.
  if (old.email_status is null) <> (new.email_status is null) then
    raise exception 'O estado de e-mail não pode ser criado nem removido depois.' using errcode = 'AG004';
  end if;
  if new.email_status is not null then
    if new.email_status = old.email_status then
      if new.email_tentativas < old.email_tentativas
         or (old.email_status in ('enviado', 'ignorado')
             and (new.email_tentativas, new.email_enviado_em, new.email_erro_codigo, new.email_reservado_em)
                 is distinct from
                 (old.email_tentativas, old.email_enviado_em, old.email_erro_codigo, old.email_reservado_em)) then
        raise exception 'Estado de e-mail terminal ou tentativas decrescentes.' using errcode = 'AG001';
      end if;
    else
      if not ((old.email_status = 'pendente' and new.email_status in ('enviando', 'ignorado'))
           or (old.email_status = 'falhou'   and new.email_status in ('enviando', 'ignorado'))
           or (old.email_status = 'enviando' and new.email_status in ('enviado', 'falhou', 'ignorado'))) then
        raise exception 'Transição inválida do e-mail: % -> %.', old.email_status, new.email_status
          using errcode = 'AG001';
      end if;
      -- Entrar em 'enviando' conta exatamente uma tentativa; as demais transicoes nao mexem nelas.
      if new.email_tentativas is distinct from
         old.email_tentativas + (case when new.email_status = 'enviando' then 1 else 0 end) then
        raise exception 'Contagem de tentativas de e-mail inconsistente.' using errcode = 'AG001';
      end if;
    end if;
  end if;

  return new;
end;
$$;

revoke all on function public.notificacoes_proteger() from public, anon, authenticated;

drop trigger if exists trg_notificacoes_proteger on public.notificacoes;
create trigger trg_notificacoes_proteger
  before update or delete on public.notificacoes
  for each row execute function public.notificacoes_proteger();
alter table public.notificacoes enable always trigger trg_notificacoes_proteger;

drop trigger if exists trg_notificacoes_sem_truncate on public.notificacoes;
create trigger trg_notificacoes_sem_truncate
  before truncate on public.notificacoes
  for each statement execute function public.bloquear_alteracao();
alter table public.notificacoes enable always trigger trg_notificacoes_sem_truncate;

-- ---------------------------------------------------------------------------
-- 4. Geracao: helper interno + trigger em auditoria
-- ---------------------------------------------------------------------------
create schema if not exists privado;
revoke all on schema privado from public, anon, authenticated;

-- Cria uma notificacao por destinatario elegivel (perfil ativo, diferente do ator e do excluido).
-- p_destino: 'usuario' (p_usuario), 'staff' (autorizador/admin) ou 'admins'. ON CONFLICT DO NOTHING:
-- repeticao de evento unico (indice de deduplicacao) e silenciosa. Interna: ninguem da API executa.
create or replace function privado.notificar(
  p_tipo        text,
  p_requisicao  uuid,
  p_referencia  uuid,
  p_critica     boolean,
  p_codigo      text,
  p_ator        uuid,
  p_destino     text,
  p_usuario     uuid default null,
  p_excluir     uuid default null
)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_email boolean;
  v_n     integer;
begin
  select coalesce(c.notificacao_email_criticos, false) into v_email from public.configuracoes c where c.id;

  insert into public.notificacoes (usuario_id, tipo, requisicao_id, referencia_id, codigo, critica, email_status)
  select p.id, p_tipo, p_requisicao, p_referencia, p_codigo, p_critica,
         case when p_critica and coalesce(v_email, false) then 'pendente' end
    from public.perfis p
   where p.ativo
     and p.id is distinct from p_ator
     and p.id is distinct from p_excluir
     and ((p_destino = 'usuario' and p.id = p_usuario)
          or (p_destino = 'staff'  and p.role in ('autorizador', 'admin'))
          or (p_destino = 'admins' and p.role = 'admin'))
  on conflict do nothing;
  get diagnostics v_n = row_count;
  return v_n;
end;
$$;

revoke all on function privado.notificar(text, uuid, uuid, boolean, text, uuid, text, uuid, uuid)
  from public, anon, authenticated, service_role;

create or replace function public.notificacoes_gerar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_dono      uuid;
  v_checklist boolean;
  v_codigo    text;
  v_req       uuid;
begin
  begin
    if new.tabela = 'requisicoes' and new.registro_id is not null then
      if new.acao = 'criada' then
        perform privado.notificar('requisicao_nova', new.registro_id, null, true, null, new.ator_id, 'staff');

      elsif new.acao = 'transicao' then
        select r.usuario_id into v_dono from public.requisicoes r where r.id = new.registro_id;
        if v_dono is not null then
          case new.para
            when 'autorizada' then
              perform privado.notificar('requisicao_autorizada', new.registro_id, null, true, null,
                                        new.ator_id, 'usuario', v_dono);
            when 'negada' then
              perform privado.notificar('requisicao_negada', new.registro_id, null, true, null,
                                        new.ator_id, 'usuario', v_dono);
            when 'em_uso' then
              perform privado.notificar('requisicao_saida', new.registro_id, null, false, null,
                                        new.ator_id, 'usuario', v_dono);
            when 'concluida' then
              perform privado.notificar('requisicao_chegada', new.registro_id, null, true, null,
                                        new.ator_id, 'usuario', v_dono);
            when 'cancelada' then
              if new.ator_id is not distinct from v_dono then
                perform privado.notificar('requisicao_cancelada_pelo_solicitante', new.registro_id, null,
                                          false, null, new.ator_id, 'staff');
              else
                perform privado.notificar('requisicao_cancelada_terceiros', new.registro_id, null, true,
                                          null, new.ator_id, 'usuario', v_dono);
              end if;
            else null;
          end case;
        end if;
      end if;

    elsif new.tabela = 'checklists' and new.acao = 'checklist_registrado'
          and (new.detalhes ->> 'avarias') = 'true' then
      select coalesce(c.checklist, false) into v_checklist from public.configuracoes c where c.id;
      if coalesce(v_checklist, false) then
        v_req := (new.detalhes ->> 'requisicao_id')::uuid;
        perform privado.notificar('checklist_avaria', v_req, new.registro_id, true, new.para,
                                  new.ator_id, 'staff');
      end if;

    elsif new.tabela = 'convites' and new.acao = 'convite_usado' then
      perform privado.notificar('convite_aceito', null, new.registro_id, false, null, new.ator_id, 'admins');

    elsif new.tabela = 'perfis' and new.acao = 'perfil_alterado' then
      -- de/para = 'role/ativo'. Notifica troca de role ou desativacao; reativacao nao.
      if split_part(new.de, '/', 2) = 'ativo' and split_part(new.para, '/', 2) = 'inativo' then
        v_codigo := 'desativado';
      elsif split_part(new.de, '/', 1) is distinct from split_part(new.para, '/', 1) then
        v_codigo := 'papel_alterado';
      end if;
      if v_codigo is not null then
        perform privado.notificar('perfil_alterado_admin', null, new.registro_id, false, v_codigo,
                                  new.ator_id, 'admins', null, new.registro_id);
      end if;

    elsif new.tabela = 'configuracoes' and new.acao = 'configuracao_alterada' then
      v_codigo := new.detalhes ->> 'recurso';
      if v_codigo is null or v_codigo !~ '^[a-z0-9_.-]{1,40}$' then
        v_codigo := null;
      end if;
      perform privado.notificar('configuracao_alterada', null, null, false, v_codigo, new.ator_id, 'admins');
    end if;
  exception when others then
    -- Notificacao e acessoria: nunca bloqueia o evento. So o SQLSTATE vai ao log (sem valores/PII).
    raise warning 'notificacoes_gerar falhou (sqlstate %)', sqlstate;
  end;
  return null;
end;
$$;

revoke all on function public.notificacoes_gerar() from public, anon, authenticated;

drop trigger if exists trg_auditoria_notificar on public.auditoria;
create trigger trg_auditoria_notificar
  after insert on public.auditoria
  for each row
  when (new.acao in ('criada', 'transicao', 'checklist_registrado', 'convite_usado',
                     'perfil_alterado', 'configuracao_alterada'))
  execute function public.notificacoes_gerar();
alter table public.auditoria enable always trigger trg_auditoria_notificar;

-- ---------------------------------------------------------------------------
-- 5. RPCs de leitura/uso (por usuario)
-- ---------------------------------------------------------------------------
-- Retorno: {"total", "nao_lidas", "limite", "offset", "itens": [{id, tipo, requisicao_id, referencia_id,
-- codigo, critica, criada_em, lida_em}]}. total = linhas do filtro; nao_lidas = sempre as nao lidas do
-- usuario. Limite ajustado a 1..100; offset negativo vira 0; offset > 100000 => AG030.
create or replace function public.listar_notificacoes(
  p_usuario             uuid,
  p_limite              integer default 20,
  p_offset              integer default 0,
  p_somente_nao_lidas   boolean default false
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_limite integer := least(greatest(coalesce(p_limite, 20), 1), 100);
  v_offset integer := greatest(coalesce(p_offset, 0), 0);
  v_so_nao boolean := coalesce(p_somente_nao_lidas, false);
  v_res    jsonb;
begin
  if p_usuario is null then
    raise exception 'Usuário da notificação não informado.' using errcode = 'AG030';
  end if;
  if v_offset > 100000 then
    raise exception 'Deslocamento acima do máximo permitido.' using errcode = 'AG030';
  end if;

  with f as (
    select n.id, n.tipo, n.requisicao_id, n.referencia_id, n.codigo, n.critica, n.criada_em, n.lida_em
      from public.notificacoes n
     where n.usuario_id = p_usuario
       and (not v_so_nao or n.lida_em is null)
  ),
  pag as (
    select * from f order by f.criada_em desc, f.id desc limit v_limite offset v_offset
  )
  select jsonb_build_object(
           'total', (select count(*) from f),
           'nao_lidas', (select count(*) from public.notificacoes u
                          where u.usuario_id = p_usuario and u.lida_em is null),
           'limite', v_limite,
           'offset', v_offset,
           'itens', coalesce((select jsonb_agg(to_jsonb(pag) order by pag.criada_em desc, pag.id desc)
                                from pag), '[]'::jsonb))
    into v_res;
  return v_res;
end;
$$;

-- Retorno: {"nao_lidas": int}
create or replace function public.contar_notificacoes_nao_lidas(p_usuario uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_n bigint;
begin
  if p_usuario is null then
    raise exception 'Usuário da notificação não informado.' using errcode = 'AG030';
  end if;
  select count(*) into v_n from public.notificacoes n where n.usuario_id = p_usuario and n.lida_em is null;
  return jsonb_build_object('nao_lidas', v_n);
end;
$$;

-- Marca como lida SO se for do dono. Inexistente e alheia => AG029 (mesma mensagem). Idempotente: ja lida
-- devolve o lida_em original. Retorno: {"id", "lida_em"}
create or replace function public.marcar_notificacao_lida(p_usuario uuid, p_id uuid)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_lida timestamptz;
begin
  if p_usuario is null or p_id is null then
    raise exception 'Notificação não encontrada.' using errcode = 'AG029';
  end if;

  update public.notificacoes n
     set lida_em = clock_timestamp()
   where n.id = p_id and n.usuario_id = p_usuario and n.lida_em is null;

  select n.lida_em into v_lida
    from public.notificacoes n
   where n.id = p_id and n.usuario_id = p_usuario;
  if not found then
    raise exception 'Notificação não encontrada.' using errcode = 'AG029';
  end if;
  return jsonb_build_object('id', p_id, 'lida_em', v_lida);
end;
$$;

-- Retorno: {"marcadas": int}
create or replace function public.marcar_todas_lidas(p_usuario uuid)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_n integer;
begin
  if p_usuario is null then
    raise exception 'Usuário da notificação não informado.' using errcode = 'AG030';
  end if;
  update public.notificacoes n set lida_em = clock_timestamp()
   where n.usuario_id = p_usuario and n.lida_em is null;
  get diagnostics v_n = row_count;
  return jsonb_build_object('marcadas', v_n);
end;
$$;

-- ---------------------------------------------------------------------------
-- 6. RPC de sistema: sistema_falha_envio para admins ativos (dedupe de 1 hora por tipo/codigo/admin)
-- ---------------------------------------------------------------------------
-- Retorno: {"criadas": int}. Serializada por advisory lock transacional (duas chamadas simultaneas nao
-- furam a janela). Critica: segue a flag de e-mail como as demais.
create or replace function public.registrar_notificacao_sistema(p_tipo text, p_detalhe_codigo text)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_email boolean;
  v_n     integer;
begin
  if p_tipo is distinct from 'sistema_falha_envio'
     or p_detalhe_codigo is null or p_detalhe_codigo !~ '^[a-z0-9_.-]{1,40}$' then
    raise exception 'Parâmetros da notificação de sistema inválidos.' using errcode = 'AG030';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.notif_sistema:' || p_tipo || ':' || p_detalhe_codigo, 0));
  select coalesce(c.notificacao_email_criticos, false) into v_email from public.configuracoes c where c.id;

  insert into public.notificacoes (usuario_id, tipo, codigo, critica, email_status)
  select p.id, p_tipo, p_detalhe_codigo, true, case when coalesce(v_email, false) then 'pendente' end
    from public.perfis p
   where p.ativo and p.role = 'admin'
     and not exists (select 1 from public.notificacoes x
                      where x.usuario_id = p.id and x.tipo = p_tipo and x.codigo = p_detalhe_codigo
                        and x.criada_em > clock_timestamp() - interval '1 hour');
  get diagnostics v_n = row_count;
  return jsonb_build_object('criadas', v_n);
end;
$$;

-- ---------------------------------------------------------------------------
-- 7. RPCs da fila de e-mail
-- ---------------------------------------------------------------------------
-- reservar_emails_notificacao: devolve [{id, usuario_id, tipo, requisicao_id, referencia_id, codigo}]
-- (jsonb array) e move pendente/falhou(<3 tentativas, 2 min apos a ultima reserva) -> enviando, contando a
-- tentativa. FOR UPDATE SKIP LOCKED: workers simultaneos nunca recebem a mesma linha. Antes de reservar:
--  * pendente/falhou(<3) inelegivel (flag desligada, perfil inativo ou criada ha mais de 24 h) => ignorado;
--  * enviando travado ha mais de 15 min => falhou ('timeout'), elegivel a nova tentativa se < 3.
create or replace function public.reservar_emails_notificacao(p_limite integer default 20)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_limite integer := least(greatest(coalesce(p_limite, 20), 1), 100);
  v_ligado boolean;
  v_res    jsonb;
begin
  select coalesce(c.notificacao_email_criticos, false) into v_ligado from public.configuracoes c where c.id;

  update public.notificacoes n
     set email_status = 'falhou', email_erro_codigo = 'timeout'
   where n.id in (select x.id from public.notificacoes x
                   where x.email_status = 'enviando'
                     and x.email_reservado_em < clock_timestamp() - interval '15 minutes'
                   for update skip locked);

  update public.notificacoes n
     set email_status = 'ignorado', email_erro_codigo = 'inelegivel'
   where n.id in (select x.id from public.notificacoes x
                   where (x.email_status = 'pendente' or (x.email_status = 'falhou' and x.email_tentativas < 3))
                     and (not coalesce(v_ligado, false)
                          or x.criada_em < clock_timestamp() - interval '24 hours'
                          or not exists (select 1 from public.perfis p where p.id = x.usuario_id and p.ativo))
                   for update skip locked);

  with c as (
    select x.id
      from public.notificacoes x
     where x.email_status = 'pendente'
        or (x.email_status = 'falhou' and x.email_tentativas < 3
            and x.email_reservado_em < clock_timestamp() - interval '2 minutes')
     order by x.criada_em, x.id
     limit v_limite
       for update skip locked
  ),
  u as (
    update public.notificacoes n
       set email_status = 'enviando',
           email_tentativas = n.email_tentativas + 1,
           email_reservado_em = clock_timestamp(),
           email_erro_codigo = null
      from c
     where n.id = c.id
    returning n.id, n.usuario_id, n.tipo, n.requisicao_id, n.referencia_id, n.codigo, n.criada_em
  )
  select coalesce(jsonb_agg(jsonb_build_object(
           'id', u.id, 'usuario_id', u.usuario_id, 'tipo', u.tipo, 'requisicao_id', u.requisicao_id,
           'referencia_id', u.referencia_id, 'codigo', u.codigo) order by u.criada_em, u.id), '[]'::jsonb)
    into v_res
    from u;
  return v_res;
end;
$$;

-- marcar_email_notificacao: resultado do envio. p_status 'enviado' | 'falhou' | 'ignorado'. 'falhou' e
-- 'ignorado' aceitam p_erro_codigo (^[A-Za-z0-9_.-]{1,40}$; 'falhou' sem codigo vira 'desconhecido').
-- Transicao validada pelo trigger (AG001). Sem e-mail_status (notificacao sem e-mail) ou inexistente => AG029.
-- Retorno: {"id", "email_status", "email_tentativas"}
create or replace function public.marcar_email_notificacao(
  p_id           uuid,
  p_status       text,
  p_erro_codigo  text default null
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_cod  text := p_erro_codigo;
  v_tent integer;
begin
  if p_status is null or p_status not in ('enviado', 'falhou', 'ignorado') then
    raise exception 'Status de e-mail da notificação inválido.' using errcode = 'AG030';
  end if;
  if p_status = 'falhou' then
    v_cod := coalesce(v_cod, 'desconhecido');
  elsif p_status = 'enviado' then
    v_cod := null;
  end if;
  if v_cod is not null and v_cod !~ '^[A-Za-z0-9_.-]{1,40}$' then
    raise exception 'Código de erro do e-mail inválido.' using errcode = 'AG030';
  end if;

  perform 1 from public.notificacoes n where n.id = p_id and n.email_status is not null for update;
  if not found then
    raise exception 'Notificação não encontrada.' using errcode = 'AG029';
  end if;

  update public.notificacoes n
     set email_status = p_status,
         email_erro_codigo = v_cod,
         email_enviado_em = case when p_status = 'enviado' then clock_timestamp() else null end
   where n.id = p_id
  returning n.email_tentativas into v_tent;

  return jsonb_build_object('id', p_id, 'email_status', p_status, 'email_tentativas', v_tent);
end;
$$;

-- ---------------------------------------------------------------------------
-- 8. Expurgo (padrao de purgar_acessos_login): p_dias 30..3650, padrao 90. Retorna linhas removidas.
-- ---------------------------------------------------------------------------
create or replace function public.purgar_notificacoes(p_dias integer default 90)
returns integer
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_n integer;
begin
  if p_dias is null or p_dias < 30 or p_dias > 3650 then
    raise exception 'Prazo de retenção fora do intervalo permitido (30 a 3650 dias).' using errcode = 'AG030';
  end if;

  -- Variavel LOCAL: vale so nesta transacao e e desligada logo apos o DELETE.
  perform set_config('sisveiculos.expurgo_notificacoes', '1', true);
  delete from public.notificacoes where criada_em < now() - make_interval(days => p_dias);
  get diagnostics v_n = row_count;
  perform set_config('sisveiculos.expurgo_notificacoes', '', true);
  return v_n;
end;
$$;

-- ---------------------------------------------------------------------------
-- 9. Permissoes: EXECUTE so service_role
-- ---------------------------------------------------------------------------
revoke all on function public.listar_notificacoes(uuid, integer, integer, boolean) from public, anon, authenticated;
revoke all on function public.contar_notificacoes_nao_lidas(uuid) from public, anon, authenticated;
revoke all on function public.marcar_notificacao_lida(uuid, uuid) from public, anon, authenticated;
revoke all on function public.marcar_todas_lidas(uuid) from public, anon, authenticated;
revoke all on function public.registrar_notificacao_sistema(text, text) from public, anon, authenticated;
revoke all on function public.reservar_emails_notificacao(integer) from public, anon, authenticated;
revoke all on function public.marcar_email_notificacao(uuid, text, text) from public, anon, authenticated;
revoke all on function public.purgar_notificacoes(integer) from public, anon, authenticated;

grant execute on function public.listar_notificacoes(uuid, integer, integer, boolean) to service_role;
grant execute on function public.contar_notificacoes_nao_lidas(uuid) to service_role;
grant execute on function public.marcar_notificacao_lida(uuid, uuid) to service_role;
grant execute on function public.marcar_todas_lidas(uuid) to service_role;
grant execute on function public.registrar_notificacao_sistema(text, text) to service_role;
grant execute on function public.reservar_emails_notificacao(integer) to service_role;
grant execute on function public.marcar_email_notificacao(uuid, text, text) to service_role;
grant execute on function public.purgar_notificacoes(integer) to service_role;

-- ---------------------------------------------------------------------------
-- 10. Agendamento CONDICIONAL do expurgo com pg_cron (nunca falha a migration)
-- ---------------------------------------------------------------------------
do $$
begin
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    begin
      perform cron.schedule('purgar-notificacoes', '27 3 * * *', 'select public.purgar_notificacoes(90)');
    exception when others then
      raise notice 'pg_cron presente, mas o agendamento do expurgo de notificacoes falhou; agende manualmente.';
    end;
  else
    raise notice 'pg_cron ausente: expurgo de notificacoes nao agendado (job interno do backend).';
  end if;
end
$$;
