-- Migration: relatorios (Entrega 2, camada de banco). Nova migration; nao edita as anteriores. Idempotente.
--
-- Motivo: exportar relatorios (CSV/XLSX) e enviar por e-mail. A consulta e a agregacao ficam no banco
-- (uma ida por relatorio); o backend (unico chamador, service_role) so repassa e formata. Nenhuma regra
-- de negocio e decidida aqui: as RPCs rel_* apenas LEEM. QUEM pode pedir cada relatorio (role) e QUAIS
-- flags de PII pode ligar (p_incluir_nomes/p_incluir_trajeto/p_incluir_descricao) e checado pelo
-- backend. O solicitante so pode ver as proprias linhas: o backend forca p_usuario = JWT.
--
-- 1. Indices novos (confrontados com os de dashboards 20261108120000):
--      ix_requisicoes_hora_saida ... relatorio de movimentacao pela saida PREVISTA
--      ix_auditoria_ocorrido_em .... relatorio de auditoria por periodo (auditoria so cresce)
--    Ja atendem o resto: ix_requisicoes_criado_em / _unidade_criado_em / _saida_real_em / _usuario_id.
-- 2. RPCs de dados (SECURITY DEFINER, search_path vazio, STABLE, EXECUTE so service_role, jsonb):
--      rel_movimentacao, rel_resumo, rel_tempos, rel_pendencias, rel_auditoria,
--      rel_opcionais_km, rel_opcionais_avarias.
--    Formato comum de lista: {"total_linhas":int, "truncado":bool, "linhas":[...]}.
--      total_linhas = linhas devolvidas; truncado = existem MAIS linhas alem de p_limite.
--      p_limite vai de 1 a 5001 (teto duro; acima disso, erro AG023). O backend pede limite+1 e
--      considera truncado se vierem mais que `limite` linhas (ou truncado=true).
--    Janela [p_inicio, p_fim): fim EXCLUSIVO, no maximo 366 dias (privado.dash_validar_janela, AG023).
--    p_usuario (uuid, default null): quando informado filtra requisicoes.usuario_id (forca "so as minhas").
--    Fuso fixo America/Sao_Paulo nos agrupamentos por dia/semana/mes; timestamptz em UTC no resto.
-- 3. Auditoria: ck_auditoria_acao ganha 'relatorio_gerado' e 'relatorio_enviado' (as 17 acoes existentes
--    preservadas); ck_auditoria_tabela ganha 'relatorios' e 'envios_email' (as 7 existentes preservadas);
--    registrar_auditoria (versao da migration 11) passa a aceitar as duas acoes novas, com lista fechada
--    de chaves em `detalhes` (tipo, filtros, formato, via, destinatario_id, total_linhas), ator
--    obrigatorio, tabela fixa por acao e recusa de '@' (nunca e-mail, nunca conteudo do relatorio).
-- 4. public.envios_email: OUTBOX/controle, SEM o arquivo. Decisao: o backend gera o arquivo em memoria
--    e envia direto numa thread; a tabela guarda so metadados (quem pediu, para quem, tipo, formato,
--    filtros < 2 KB sem e-mail, status, tentativas, codigo curto de erro) e serve de cota por hora.
--    Se o processo morrer, o arquivo se perde e o envio fica 'pendente'/'enviando': o admin pede de
--    novo (outro registro). RPCs: reservar_envio_email (valida destinatario ativo e cota, atomico por
--    advisory lock) e marcar_envio_email (transicoes validadas no banco).
-- 5. configuracoes ganha relatorio_email e relatorio_whatsapp (boolean, padrao false) e
--    configuracoes_validar (versao da migration 8) passa a auditar as 6 chaves.
--
-- Erros AG novos (AG023 reaproveitado; AG024+ livres antes desta migration; a de IA nao criou nenhum):
--   AG023 parametro invalido de relatorio (janela, limite, agrupamento, campo de data, acao)
--   AG024 destinatario do envio ausente ou inativo
--   AG025 cota de envios por hora excedida
--   AG026 recurso desligado (relatorio por e-mail)
--   AG027 parametro invalido de envio (tipo, formato, filtros, cota maxima, status)
--   AG028 envio nao encontrado
-- Reaproveitados: AG001 transicao invalida de envio; AG002 solicitante/ator ausente ou inativo;
--   AG003 operacao proibida (DELETE/TRUNCATE, detalhes de auditoria fora da lista); AG004 coluna imutavel.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. Indices
-- ---------------------------------------------------------------------------
create index if not exists ix_requisicoes_hora_saida
  on public.requisicoes (hora_saida);
create index if not exists ix_auditoria_ocorrido_em
  on public.auditoria (ocorrido_em);

-- ---------------------------------------------------------------------------
-- 2. Configuracoes: relatorio_email e relatorio_whatsapp
-- ---------------------------------------------------------------------------
alter table public.configuracoes
  add column if not exists relatorio_email boolean not null default false;
alter table public.configuracoes
  add column if not exists relatorio_whatsapp boolean not null default false;

comment on column public.configuracoes.relatorio_email is
  'Recurso opcional: envio de relatorios por e-mail (desligado por padrao).';
comment on column public.configuracoes.relatorio_whatsapp is
  'Recurso opcional: envio de relatorios por WhatsApp (desligado por padrao; so a chave, sem tabela).';

-- Mesma funcao da migration 8 (validacao + auditoria), agora com 6 chaves. Nada do restante muda:
-- ator zerado, AG002 sem admin ativo, AG010 (abastecimento exige quilometragem), 1 linha de auditoria
-- por chave alterada.
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
     and new.relatorio_whatsapp is not distinct from old.relatorio_whatsapp then
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
                              'relatorio_email', 'relatorio_whatsapp'])
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
-- 3. Auditoria: novas acoes/tabelas e registrar_auditoria
-- ---------------------------------------------------------------------------
alter table public.auditoria drop constraint if exists ck_auditoria_acao;
alter table public.auditoria
  add constraint ck_auditoria_acao check (acao in (
    'criada', 'transicao',
    'convite_criado', 'convite_usado', 'convite_revogado',
    'perfil_criado', 'perfil_alterado', 'perfil_unidade_alterada', 'admin_semeado',
    'configuracao_alterada',
    'veiculo_criado', 'veiculo_alterado',
    'acao_administrativa', 'acesso_negado',
    'checklist_registrado',
    'foto_perfil_alterada', 'foto_perfil_removida',
    'relatorio_gerado', 'relatorio_enviado'
  ));

alter table public.auditoria drop constraint if exists ck_auditoria_tabela;
alter table public.auditoria
  add constraint ck_auditoria_tabela
  check (tabela in ('requisicoes', 'convites', 'perfis', 'veiculos', 'configuracoes', 'checklists',
                    'perfil_fotos', 'relatorios', 'envios_email'));

-- Autoverificacao: nenhuma acao/tabela anterior pode ter sido perdida.
do $$
declare
  v text;
  d_acao   text;
  d_tabela text;
begin
  select pg_get_constraintdef(oid) into d_acao from pg_constraint
   where conrelid = 'public.auditoria'::regclass and conname = 'ck_auditoria_acao';
  select pg_get_constraintdef(oid) into d_tabela from pg_constraint
   where conrelid = 'public.auditoria'::regclass and conname = 'ck_auditoria_tabela';

  foreach v in array array[
    'criada', 'transicao', 'convite_criado', 'convite_usado', 'convite_revogado',
    'perfil_criado', 'perfil_alterado', 'perfil_unidade_alterada', 'admin_semeado',
    'configuracao_alterada', 'veiculo_criado', 'veiculo_alterado', 'acao_administrativa',
    'acesso_negado', 'checklist_registrado', 'foto_perfil_alterada', 'foto_perfil_removida',
    'relatorio_gerado', 'relatorio_enviado'] loop
    if d_acao is null or position('''' || v || '''' in d_acao) = 0 then
      raise exception 'ck_auditoria_acao sem o valor %.', v;
    end if;
  end loop;
  foreach v in array array['requisicoes', 'convites', 'perfis', 'veiculos', 'configuracoes',
                           'checklists', 'perfil_fotos', 'relatorios', 'envios_email'] loop
    if d_tabela is null or position('''' || v || '''' in d_tabela) = 0 then
      raise exception 'ck_auditoria_tabela sem o valor %.', v;
    end if;
  end loop;
end
$$;

-- Registro de eventos pelo backend (service_role): lista fechada de acoes. Versao da migration 11 +
-- 'relatorio_gerado' (tabela 'relatorios') e 'relatorio_enviado' (tabela 'envios_email'). Para as duas
-- novas: ator obrigatorio e existente; `detalhes` so com as chaves tipo, filtros, formato, via,
-- destinatario_id, total_linhas (nunca conteudo do relatorio, nunca e-mail: qualquer '@' e recusado).
-- Tamanho de detalhes (< 2048 bytes) segue no CHECK ck_auditoria_detalhes_tamanho.
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
  if p_acao not in ('acao_administrativa', 'acesso_negado', 'relatorio_gerado', 'relatorio_enviado') then
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
-- 4. envios_email (outbox/controle; sem o arquivo)
-- ---------------------------------------------------------------------------
create table if not exists public.envios_email (
  id               uuid primary key default gen_random_uuid(),
  solicitado_por   uuid not null references public.perfis (id) on delete restrict,
  destinatario_id  uuid not null references public.perfis (id) on delete restrict,
  tipo_relatorio   text not null,
  formato          text not null,
  filtros          jsonb not null default '{}'::jsonb,
  status           text not null default 'pendente',
  tentativas       integer not null default 0,
  erro_codigo      text,
  criado_em        timestamptz not null default now(),
  enviado_em       timestamptz,
  atualizado_em    timestamptz not null default now(),

  constraint ck_envios_email_tipo check (tipo_relatorio ~ '^[a-z_]{1,40}$'),
  constraint ck_envios_email_formato check (formato in ('csv', 'xlsx')),
  constraint ck_envios_email_status check (status in ('pendente', 'enviando', 'enviado', 'falhou')),
  constraint ck_envios_email_filtros_objeto check (jsonb_typeof(filtros) = 'object'),
  constraint ck_envios_email_filtros_tamanho check (octet_length(filtros::text) < 2048),
  -- Sem e-mail em filtros (o destinatario e referenciado por id; LGPD).
  constraint ck_envios_email_filtros_sem_email check (position('@' in filtros::text) = 0),
  constraint ck_envios_email_tentativas check (tentativas between 0 and 100),
  constraint ck_envios_email_erro_formato
    check (erro_codigo is null or erro_codigo ~ '^[A-Za-z0-9_.-]{1,40}$'),
  -- erro so quando falhou; enviado_em so quando enviado; enviado exige ao menos uma tentativa.
  constraint ck_envios_email_erro_coerente check ((status = 'falhou') = (erro_codigo is not null)),
  constraint ck_envios_email_enviado_em_coerente check ((status = 'enviado') = (enviado_em is not null)),
  constraint ck_envios_email_pendente_sem_tentativa check (status <> 'pendente' or tentativas = 0),
  constraint ck_envios_email_enviado_com_tentativa check (status <> 'enviado' or tentativas >= 1)
);

comment on table public.envios_email is
  'Controle/outbox de envios de relatorio por e-mail. SEM arquivo, SEM e-mail (so ids). Base da cota por hora. Sem DELETE fisico.';
comment on column public.envios_email.filtros is
  'Filtros do relatorio (jsonb < 2 KB, sem e-mail). Nunca o conteudo gerado.';
comment on column public.envios_email.erro_codigo is
  'Codigo curto do ultimo erro (nunca mensagem do provedor nem dado pessoal).';

-- Contagem por hora (cota global) e por solicitante; FKs sem indice automatico.
create index if not exists ix_envios_email_criado_em on public.envios_email (criado_em);
create index if not exists ix_envios_email_solicitado_por_criado_em
  on public.envios_email (solicitado_por, criado_em);
create index if not exists ix_envios_email_destinatario_id on public.envios_email (destinatario_id);

drop trigger if exists trg_envios_email_atualizado_em on public.envios_email;
create trigger trg_envios_email_atualizado_em
  before update on public.envios_email
  for each row execute function public.set_atualizado_em();

-- Regras de estado no banco: nasce 'pendente'; identidade imutavel; transicoes permitidas:
--   pendente -> enviando | falhou ; enviando -> enviado | falhou ; falhou -> enviando (nova tentativa).
-- 'enviado' e terminal. Todo UPDATE precisa mudar o status.
create or replace function public.envios_email_validar()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.status <> 'pendente' or new.tentativas <> 0 or new.erro_codigo is not null
       or new.enviado_em is not null then
      raise exception 'Envio novo deve nascer pendente, sem tentativas.' using errcode = 'AG001';
    end if;
    return new;
  end if;

  if new.id is distinct from old.id
     or new.solicitado_por is distinct from old.solicitado_por
     or new.destinatario_id is distinct from old.destinatario_id
     or new.tipo_relatorio is distinct from old.tipo_relatorio
     or new.formato is distinct from old.formato
     or new.filtros is distinct from old.filtros
     or new.criado_em is distinct from old.criado_em then
    raise exception 'Campos de identidade do envio são imutáveis.' using errcode = 'AG004';
  end if;

  if new.status is not distinct from old.status
     or not ((old.status = 'pendente' and new.status in ('enviando', 'falhou'))
          or (old.status = 'enviando' and new.status in ('enviado', 'falhou'))
          or (old.status = 'falhou'   and new.status = 'enviando')) then
    raise exception 'Transição inválida do envio: % -> %.', old.status, new.status
      using errcode = 'AG001';
  end if;
  return new;
end;
$$;

revoke all on function public.envios_email_validar() from public, anon, authenticated;

drop trigger if exists trg_envios_email_validar on public.envios_email;
create trigger trg_envios_email_validar
  before insert or update on public.envios_email
  for each row execute function public.envios_email_validar();

-- Sem DELETE/TRUNCATE fisico.
drop trigger if exists trg_envios_email_sem_delete on public.envios_email;
create trigger trg_envios_email_sem_delete
  before delete on public.envios_email
  for each row execute function public.bloquear_alteracao();
drop trigger if exists trg_envios_email_sem_truncate on public.envios_email;
create trigger trg_envios_email_sem_truncate
  before truncate on public.envios_email
  for each statement execute function public.bloquear_alteracao();

-- RLS deny-by-default. Escrita SO pelas RPCs (SECURITY DEFINER); service_role apenas le.
alter table public.envios_email enable row level security;

drop policy if exists envios_email_sem_acesso_api on public.envios_email;
create policy envios_email_sem_acesso_api on public.envios_email
  for all to anon, authenticated using (false) with check (false);

revoke all on table public.envios_email from public, anon, authenticated;
revoke all on table public.envios_email from service_role;
grant select on table public.envios_email to service_role;

-- ---------------------------------------------------------------------------
-- 4.1 reservar_envio_email
-- ---------------------------------------------------------------------------
-- Cria o registro 'pendente' se: recurso relatorio_email ligado (AG026), solicitante ativo (AG002),
-- destinatario ativo (AG024) e cota global na ultima hora < p_max_por_hora (AG025). A contagem e o
-- INSERT rodam sob advisory lock transacional: duas reservas simultaneas nunca estouram a cota.
-- O destinatario e resolvido por id (o backend busca o e-mail no Auth); nenhum e-mail passa aqui.
-- Retorno: {"id": uuid}
create or replace function public.reservar_envio_email(
  p_solicitante   uuid,
  p_destinatario  uuid,
  p_tipo          text,
  p_formato       text,
  p_filtros       jsonb,
  p_max_por_hora  integer
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
     or p_max_por_hora is null or p_max_por_hora < 1 or p_max_por_hora > 1000 then
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

  insert into public.envios_email (id, solicitado_por, destinatario_id, tipo_relatorio, formato, filtros, criado_em)
  values (v_id, p_solicitante, p_destinatario, p_tipo, p_formato, v_filtros, v_agora);

  return jsonb_build_object('id', v_id);
end;
$$;

comment on function public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer) is
  'Reserva um envio de relatorio por e-mail (valida recurso, solicitante, destinatario ativo e cota/hora, sob lock). Somente service_role.';

-- ---------------------------------------------------------------------------
-- 4.2 marcar_envio_email
-- ---------------------------------------------------------------------------
-- Move o envio para 'enviando' (conta uma tentativa), 'enviado' (grava enviado_em) ou 'falhou'
-- (grava p_erro_codigo curto, padrao 'desconhecido'). A transicao e validada pelo trigger (AG001).
-- Retorno: {"id": uuid, "status": text, "tentativas": int}
create or replace function public.marcar_envio_email(
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
  v_tent integer;
  v_cod  text := p_erro_codigo;
begin
  if p_status is null or p_status not in ('enviando', 'enviado', 'falhou') then
    raise exception 'Status de envio inválido.' using errcode = 'AG027';
  end if;
  if p_status = 'falhou' then
    v_cod := coalesce(v_cod, 'desconhecido');
    if v_cod !~ '^[A-Za-z0-9_.-]{1,40}$' then
      raise exception 'Código de erro do envio inválido.' using errcode = 'AG027';
    end if;
  else
    v_cod := null;
  end if;

  perform 1 from public.envios_email e where e.id = p_id for update;
  if not found then
    raise exception 'Envio não encontrado.' using errcode = 'AG028';
  end if;

  update public.envios_email e
     set status      = p_status,
         erro_codigo = v_cod,
         tentativas  = e.tentativas + (case when p_status = 'enviando' then 1 else 0 end),
         enviado_em  = case when p_status = 'enviado' then clock_timestamp() else null end
   where e.id = p_id
  returning e.tentativas into v_tent;

  return jsonb_build_object('id', p_id, 'status', p_status, 'tentativas', v_tent);
end;
$$;

comment on function public.marcar_envio_email(uuid, text, text) is
  'Atualiza o status de um envio (enviando/enviado/falhou) com transicao validada no banco. Somente service_role.';

-- ---------------------------------------------------------------------------
-- 5. Validacao comum do limite (interna; ninguem da API executa)
-- ---------------------------------------------------------------------------
create schema if not exists privado;
revoke all on schema privado from public, anon, authenticated;

create or replace function privado.rel_validar_limite(p_limite integer)
returns void
language plpgsql
immutable
set search_path = ''
as $$
begin
  if p_limite is null or p_limite < 1 or p_limite > 5001 then
    raise exception 'Limite do relatório inválido: use de 1 a 5001.' using errcode = 'AG023';
  end if;
end;
$$;

revoke all on function privado.rel_validar_limite(integer)
  from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 6. rel_movimentacao
-- ---------------------------------------------------------------------------
-- Uma linha por requisicao cuja data escolhida cai na janela. p_campo_data: 'criacao' (criado_em),
-- 'saida_prevista' (hora_saida) ou 'saida_real' (saida_real_em; so quem ja saiu). Ordem estavel:
-- data escolhida, id. Chaves da linha (jsonb nao guarda ordem: use `colunas`):
--   codigo (8 primeiros chars do id), criada_em, unidade, veiculo_id, veiculo ("PLACA - descricao" ou
--   so descricao), status, hora_saida_prevista, autorizada_em (momento da decisao; para `negada` e a
--   negativa), saida_real_em, chegada_real_em, espera_autorizacao_s (autorizada_em - criada_em; null
--   em negada, como nos dashboards), duracao_uso_s (chegada - saida reais; null se nao concluida);
--   + km_saida, km_chegada .................. so se configuracoes.quilometragem ligada
--   + solicitante, autorizado_por, saida_por, chegada_por (nomes) ... so se p_incluir_nomes
--   + trajeto ............................... so se p_incluir_trajeto
-- Retorno: { campo_data, colunas:[...], colunas_opcionais:{km,nomes,trajeto}, total_linhas, truncado, linhas:[...] }
create or replace function public.rel_movimentacao(
  p_inicio          timestamptz,
  p_fim             timestamptz,
  p_campo_data      text default 'criacao',
  p_unidade         public.unidade default null,
  p_veiculo         uuid default null,
  p_usuario         uuid default null,
  p_incluir_nomes   boolean default false,
  p_incluir_trajeto boolean default false,
  p_limite          integer default 5001
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_km     boolean;
  v_nomes  boolean := coalesce(p_incluir_nomes, false);
  v_traj   boolean := coalesce(p_incluir_trajeto, false);
  v_cols   text[] := array['codigo', 'criada_em', 'unidade', 'veiculo_id', 'veiculo', 'status',
                           'hora_saida_prevista', 'autorizada_em', 'saida_real_em', 'chegada_real_em',
                           'espera_autorizacao_s', 'duracao_uso_s'];
  v_linhas jsonb;
  v_cont   integer;
begin
  perform privado.dash_validar_janela(p_inicio, p_fim);
  perform privado.rel_validar_limite(p_limite);
  if p_campo_data is null or p_campo_data not in ('criacao', 'saida_prevista', 'saida_real') then
    raise exception 'Campo de data inválido: use criacao, saida_prevista ou saida_real.'
      using errcode = 'AG023';
  end if;

  select coalesce(c.quilometragem, false) into v_km from public.configuracoes c where c.id;
  v_km := coalesce(v_km, false);
  if v_km then v_cols := v_cols || array['km_saida', 'km_chegada']; end if;
  if v_nomes then v_cols := v_cols || array['solicitante', 'autorizado_por', 'saida_por', 'chegada_por']; end if;
  if v_traj then v_cols := v_cols || array['trajeto']; end if;

  with ids as (
    select r.id, r.criado_em as k from public.requisicoes r
     where p_campo_data = 'criacao' and r.criado_em >= p_inicio and r.criado_em < p_fim
       and (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
       and (p_usuario is null or r.usuario_id = p_usuario)
    union all
    select r.id, r.hora_saida from public.requisicoes r
     where p_campo_data = 'saida_prevista' and r.hora_saida >= p_inicio and r.hora_saida < p_fim
       and (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
       and (p_usuario is null or r.usuario_id = p_usuario)
    union all
    select r.id, r.saida_real_em from public.requisicoes r
     where p_campo_data = 'saida_real' and r.saida_real_em >= p_inicio and r.saida_real_em < p_fim
       and (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
       and (p_usuario is null or r.usuario_id = p_usuario)
  ),
  sel as (
    select i.id, i.k, row_number() over (order by i.k, i.id) as n
      from (select * from ids order by k, id limit p_limite + 1) i
  )
  select coalesce(jsonb_agg(x.j order by x.n) filter (where x.n <= p_limite), '[]'::jsonb),
         count(*)
    into v_linhas, v_cont
    from (
      select s.n,
             jsonb_build_object(
               'codigo', left(r.id::text, 8),
               'criada_em', r.criado_em,
               'unidade', r.unidade,
               'veiculo_id', r.veiculo_id,
               'veiculo', case when v.placa is null then v.descricao else v.placa || ' - ' || v.descricao end,
               'status', r.status,
               'hora_saida_prevista', r.hora_saida,
               'autorizada_em', r.autorizado_em,
               'saida_real_em', r.saida_real_em,
               'chegada_real_em', r.chegada_real_em,
               'espera_autorizacao_s',
                 case when r.autorizado_em is not null and r.status <> 'negada'
                      then floor(extract(epoch from r.autorizado_em - r.criado_em))::bigint end,
               'duracao_uso_s',
                 case when r.saida_real_em is not null and r.chegada_real_em is not null
                      then floor(extract(epoch from r.chegada_real_em - r.saida_real_em))::bigint end)
             || case when v_km then jsonb_build_object('km_saida', r.km_saida, 'km_chegada', r.km_chegada)
                     else '{}'::jsonb end
             || case when v_nomes then jsonb_build_object(
                  'solicitante', r.nome_solicitante,
                  'autorizado_por', pa.nome,
                  'saida_por', ps.nome,
                  'chegada_por', pc.nome) else '{}'::jsonb end
             || case when v_traj then jsonb_build_object('trajeto', r.trajeto) else '{}'::jsonb end as j
        from sel s
        join public.requisicoes r on r.id = s.id
        join public.veiculos v on v.id = r.veiculo_id
        left join public.perfis pa on v_nomes and pa.id = r.autorizado_por
        left join public.perfis ps on v_nomes and ps.id = r.saida_registrada_por
        left join public.perfis pc on v_nomes and pc.id = r.chegada_registrada_por
    ) x;

  return jsonb_build_object(
    'campo_data', p_campo_data,
    'colunas', to_jsonb(v_cols),
    'colunas_opcionais', jsonb_build_object('km', v_km, 'nomes', v_nomes, 'trajeto', v_traj),
    'total_linhas', least(v_cont, p_limite),
    'truncado', v_cont > p_limite,
    'linhas', v_linhas);
end;
$$;

comment on function public.rel_movimentacao(timestamptz, timestamptz, text, public.unidade, uuid, uuid, boolean, boolean, integer) is
  'Relatorio: uma linha por requisicao (nomes e trajeto so por flag; km so se recurso ligado). Somente service_role.';

-- ---------------------------------------------------------------------------
-- 7. rel_resumo
-- ---------------------------------------------------------------------------
-- Requisicoes CRIADAS na janela, agrupadas. p_agrupar: dia | semana (segunda) | mes (fuso America/Sao_Paulo,
-- periodos sem ocorrencia preenchidos com zero) | unidade (as quatro, ou so p_unidade) | veiculo (ativos
-- mais os com ocorrencia). Contagens pelo status ATUAL (mesma regra de dash_volume_desfecho):
-- autorizadas = autorizada + em_uso + concluida; horas_uso = soma (chegada - saida) das concluidas
-- criadas na janela (2 casas). Linha: { grupo, rotulo, requisicoes, autorizadas, negadas, canceladas,
-- concluidas, horas_uso }. grupo: 'YYYY-MM-DD' (dia/semana), 'YYYY-MM' (mes), sigla, ou veiculo_id;
-- rotulo: igual ao grupo, exceto veiculo ("PLACA - descricao").
-- Retorno: { agrupar, total_linhas, truncado:false, linhas:[...] }
create or replace function public.rel_resumo(
  p_inicio    timestamptz,
  p_fim       timestamptz,
  p_agrupar   text default 'dia',
  p_unidade   public.unidade default null,
  p_veiculo   uuid default null,
  p_usuario   uuid default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_trunc  text;
  v_passo  interval;
  v_linhas jsonb;
begin
  perform privado.dash_validar_janela(p_inicio, p_fim);
  if p_agrupar is null or p_agrupar not in ('dia', 'semana', 'mes', 'unidade', 'veiculo') then
    raise exception 'Agrupamento inválido: use dia, semana, mes, unidade ou veiculo.' using errcode = 'AG023';
  end if;

  if p_agrupar in ('dia', 'semana', 'mes') then
    v_trunc := case p_agrupar when 'dia' then 'day' when 'semana' then 'week' else 'month' end;
    v_passo := case p_agrupar when 'dia' then interval '1 day'
                              when 'semana' then interval '1 week' else interval '1 month' end;
    with r as (
      select date_trunc(v_trunc, x.criado_em at time zone 'America/Sao_Paulo') as b,
             x.status, x.saida_real_em, x.chegada_real_em
        from public.requisicoes x
       where x.criado_em >= p_inicio and x.criado_em < p_fim
         and (p_unidade is null or x.unidade = p_unidade)
         and (p_veiculo is null or x.veiculo_id = p_veiculo)
         and (p_usuario is null or x.usuario_id = p_usuario)
    ),
    g as (
      select b, count(*) as n,
             count(*) filter (where status in ('autorizada', 'em_uso', 'concluida')) as aut,
             count(*) filter (where status = 'negada') as neg,
             count(*) filter (where status = 'cancelada') as can,
             count(*) filter (where status = 'concluida') as con,
             sum(extract(epoch from chegada_real_em - saida_real_em)) filter (where status = 'concluida') as seg
        from r group by b
    ),
    serie as (
      select gs as b
        from generate_series(
               date_trunc(v_trunc, p_inicio at time zone 'America/Sao_Paulo'),
               date_trunc(v_trunc, (p_fim - interval '1 microsecond') at time zone 'America/Sao_Paulo'),
               v_passo) as gs
    )
    select coalesce(jsonb_agg(jsonb_build_object(
             'grupo', to_char(s.b, case when p_agrupar = 'mes' then 'YYYY-MM' else 'YYYY-MM-DD' end),
             'rotulo', to_char(s.b, case when p_agrupar = 'mes' then 'YYYY-MM' else 'YYYY-MM-DD' end),
             'requisicoes', coalesce(g.n, 0), 'autorizadas', coalesce(g.aut, 0),
             'negadas', coalesce(g.neg, 0), 'canceladas', coalesce(g.can, 0),
             'concluidas', coalesce(g.con, 0),
             'horas_uso', round(coalesce(g.seg, 0) / 3600.0, 2)) order by s.b), '[]'::jsonb)
      into v_linhas
      from serie s left join g on g.b = s.b;

  elsif p_agrupar = 'unidade' then
    with g as (
      select x.unidade, count(*) as n,
             count(*) filter (where x.status in ('autorizada', 'em_uso', 'concluida')) as aut,
             count(*) filter (where x.status = 'negada') as neg,
             count(*) filter (where x.status = 'cancelada') as can,
             count(*) filter (where x.status = 'concluida') as con,
             sum(extract(epoch from x.chegada_real_em - x.saida_real_em)) filter (where x.status = 'concluida') as seg
        from public.requisicoes x
       where x.criado_em >= p_inicio and x.criado_em < p_fim
         and (p_veiculo is null or x.veiculo_id = p_veiculo)
         and (p_usuario is null or x.usuario_id = p_usuario)
       group by x.unidade
    )
    select coalesce(jsonb_agg(jsonb_build_object(
             'grupo', u.unidade, 'rotulo', u.unidade,
             'requisicoes', coalesce(g.n, 0), 'autorizadas', coalesce(g.aut, 0),
             'negadas', coalesce(g.neg, 0), 'canceladas', coalesce(g.can, 0),
             'concluidas', coalesce(g.con, 0),
             'horas_uso', round(coalesce(g.seg, 0) / 3600.0, 2)) order by u.unidade), '[]'::jsonb)
      into v_linhas
      from unnest(enum_range(null::public.unidade)) as u(unidade)
      left join g on g.unidade = u.unidade
     where p_unidade is null or u.unidade = p_unidade;

  else
    with g as (
      select x.veiculo_id, count(*) as n,
             count(*) filter (where x.status in ('autorizada', 'em_uso', 'concluida')) as aut,
             count(*) filter (where x.status = 'negada') as neg,
             count(*) filter (where x.status = 'cancelada') as can,
             count(*) filter (where x.status = 'concluida') as con,
             sum(extract(epoch from x.chegada_real_em - x.saida_real_em)) filter (where x.status = 'concluida') as seg
        from public.requisicoes x
       where x.criado_em >= p_inicio and x.criado_em < p_fim
         and (p_unidade is null or x.unidade = p_unidade)
         and (p_usuario is null or x.usuario_id = p_usuario)
       group by x.veiculo_id
    )
    select coalesce(jsonb_agg(jsonb_build_object(
             'grupo', v.id,
             'rotulo', case when v.placa is null then v.descricao else v.placa || ' - ' || v.descricao end,
             'requisicoes', coalesce(g.n, 0), 'autorizadas', coalesce(g.aut, 0),
             'negadas', coalesce(g.neg, 0), 'canceladas', coalesce(g.can, 0),
             'concluidas', coalesce(g.con, 0),
             'horas_uso', round(coalesce(g.seg, 0) / 3600.0, 2)) order by v.descricao, v.id), '[]'::jsonb)
      into v_linhas
      from public.veiculos v
      left join g on g.veiculo_id = v.id
     where (p_veiculo is null or v.id = p_veiculo)
       and (v.ativo or g.veiculo_id is not null);
  end if;

  return jsonb_build_object('agrupar', p_agrupar, 'total_linhas', jsonb_array_length(v_linhas),
                            'truncado', false, 'linhas', v_linhas);
end;
$$;

comment on function public.rel_resumo(timestamptz, timestamptz, text, public.unidade, uuid, uuid) is
  'Relatorio: contagens por dia/semana/mes/unidade/veiculo das requisicoes criadas na janela. Somente service_role.';

-- ---------------------------------------------------------------------------
-- 8. rel_tempos
-- ---------------------------------------------------------------------------
-- Mesmas definicoes de dash_tempos_fluxo, agrupadas (cada bloco usa o seu recorte de data):
--  * espera: criadas na janela e autorizadas (nao negadas); grupo pela data de criacao.
--  * uso: concluidas com saida real na janela; grupo pela saida real.
--  * saida_dif: saida real - hora_saida prevista (ASSINADA; negativo = antes), saidas reais na janela.
-- p_agrupar: unidade | veiculo | mes (fuso America/Sao_Paulo). So grupos com ao menos uma medida.
-- Linha: { grupo, rotulo, espera_n, espera_media_s, espera_mediana_s, espera_max_s,
--          uso_n, uso_media_s, uso_mediana_s, uso_max_s,
--          saida_dif_n, saida_dif_media_s, saida_dif_mediana_s, saida_dif_min_s, saida_dif_max_s }
-- (valores null se n = 0). Retorno: { agrupar, total_linhas, truncado:false, linhas:[...] }
create or replace function public.rel_tempos(
  p_inicio    timestamptz,
  p_fim       timestamptz,
  p_agrupar   text default 'unidade',
  p_unidade   public.unidade default null,
  p_veiculo   uuid default null,
  p_usuario   uuid default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_linhas jsonb;
begin
  perform privado.dash_validar_janela(p_inicio, p_fim);
  if p_agrupar is null or p_agrupar not in ('unidade', 'veiculo', 'mes') then
    raise exception 'Agrupamento inválido: use unidade, veiculo ou mes.' using errcode = 'AG023';
  end if;

  with base as (
    select r.unidade, r.veiculo_id, r.status, r.criado_em, r.autorizado_em, r.hora_saida,
           r.saida_real_em, r.chegada_real_em
      from public.requisicoes r
     where (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
       and (p_usuario is null or r.usuario_id = p_usuario)
       and ((r.criado_em >= p_inicio and r.criado_em < p_fim)
            or (r.saida_real_em >= p_inicio and r.saida_real_em < p_fim))
  ),
  ev as (
    select 'e' as t, b.unidade, b.veiculo_id, b.criado_em as ref,
           extract(epoch from b.autorizado_em - b.criado_em)::double precision as s
      from base b
     where b.criado_em >= p_inicio and b.criado_em < p_fim
       and b.autorizado_em is not null and b.status <> 'negada'
    union all
    select 'u', b.unidade, b.veiculo_id, b.saida_real_em,
           extract(epoch from b.chegada_real_em - b.saida_real_em)::double precision
      from base b
     where b.status = 'concluida' and b.saida_real_em >= p_inicio and b.saida_real_em < p_fim
    union all
    select 'd', b.unidade, b.veiculo_id, b.saida_real_em,
           extract(epoch from b.saida_real_em - b.hora_saida)::double precision
      from base b
     where b.saida_real_em >= p_inicio and b.saida_real_em < p_fim
  ),
  k as (
    select e.t, e.s,
           case p_agrupar when 'unidade' then e.unidade::text
                          when 'veiculo' then e.veiculo_id::text
                          else to_char(date_trunc('month', e.ref at time zone 'America/Sao_Paulo'), 'YYYY-MM') end as g,
           case when p_agrupar = 'unidade' then array_position(enum_range(null::public.unidade), e.unidade) end as ord
      from ev e
  ),
  a as (
    select k.g, min(k.ord) as ord,
           count(*) filter (where k.t = 'e') as e_n,
           round(avg(k.s) filter (where k.t = 'e'))::bigint as e_med,
           round((percentile_cont(0.5) within group (order by k.s) filter (where k.t = 'e'))::numeric)::bigint as e_mdn,
           round(max(k.s) filter (where k.t = 'e'))::bigint as e_max,
           count(*) filter (where k.t = 'u') as u_n,
           round(avg(k.s) filter (where k.t = 'u'))::bigint as u_med,
           round((percentile_cont(0.5) within group (order by k.s) filter (where k.t = 'u'))::numeric)::bigint as u_mdn,
           round(max(k.s) filter (where k.t = 'u'))::bigint as u_max,
           count(*) filter (where k.t = 'd') as d_n,
           round(avg(k.s) filter (where k.t = 'd'))::bigint as d_med,
           round((percentile_cont(0.5) within group (order by k.s) filter (where k.t = 'd'))::numeric)::bigint as d_mdn,
           round(min(k.s) filter (where k.t = 'd'))::bigint as d_min,
           round(max(k.s) filter (where k.t = 'd'))::bigint as d_max
      from k group by k.g
  )
  select coalesce(jsonb_agg(jsonb_build_object(
           'grupo', a.g,
           'rotulo', case when p_agrupar = 'veiculo'
                          then (select case when v.placa is null then v.descricao
                                            else v.placa || ' - ' || v.descricao end
                                  from public.veiculos v where v.id::text = a.g)
                          else a.g end,
           'espera_n', a.e_n, 'espera_media_s', a.e_med, 'espera_mediana_s', a.e_mdn, 'espera_max_s', a.e_max,
           'uso_n', a.u_n, 'uso_media_s', a.u_med, 'uso_mediana_s', a.u_mdn, 'uso_max_s', a.u_max,
           'saida_dif_n', a.d_n, 'saida_dif_media_s', a.d_med, 'saida_dif_mediana_s', a.d_mdn,
           'saida_dif_min_s', a.d_min, 'saida_dif_max_s', a.d_max)
         order by a.ord, a.g), '[]'::jsonb)
    into v_linhas
    from a;

  return jsonb_build_object('agrupar', p_agrupar, 'total_linhas', jsonb_array_length(v_linhas),
                            'truncado', false, 'linhas', v_linhas);
end;
$$;

comment on function public.rel_tempos(timestamptz, timestamptz, text, public.unidade, uuid, uuid) is
  'Relatorio: espera, duracao de uso e diferenca saida real x prevista, por unidade/veiculo/mes. Somente service_role.';

-- ---------------------------------------------------------------------------
-- 9. rel_pendencias
-- ---------------------------------------------------------------------------
-- Foto do momento (sem janela): solicitadas, autorizadas sem saida e em uso, mais urgentes primeiro
-- (solicitadas, autorizadas, em uso; dentro de cada uma a mais antiga primeiro). decorrido_s = tempo no
-- estado ATUAL (criacao / autorizacao / saida real). Sem conceito de "atrasado" (v1 sem prazo maximo).
-- Linha: { codigo, status, unidade, veiculo_id, veiculo, hora_saida_prevista, desde, decorrido_s
--          [, solicitante  so se p_incluir_nomes ] }
-- Retorno: { gerado_em, colunas, total_linhas, truncado, linhas }
create or replace function public.rel_pendencias(
  p_unidade       public.unidade default null,
  p_veiculo       uuid default null,
  p_limite        integer default 5001,
  p_usuario       uuid default null,
  p_incluir_nomes boolean default false
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_agora  timestamptz := now();
  v_nomes  boolean := coalesce(p_incluir_nomes, false);
  v_cols   text[] := array['codigo', 'status', 'unidade', 'veiculo_id', 'veiculo', 'hora_saida_prevista',
                           'desde', 'decorrido_s'];
  v_linhas jsonb;
  v_cont   integer;
begin
  perform privado.rel_validar_limite(p_limite);
  if v_nomes then v_cols := v_cols || array['solicitante']; end if;

  with ab as (
    select r.id,
           case r.status when 'solicitada' then 1 when 'autorizada' then 2 else 3 end as ord,
           case r.status when 'solicitada' then r.criado_em
                         when 'autorizada' then r.autorizado_em
                         else r.saida_real_em end as desde
      from public.requisicoes r
     where r.status in ('solicitada', 'autorizada', 'em_uso')
       and (p_unidade is null or r.unidade = p_unidade)
       and (p_veiculo is null or r.veiculo_id = p_veiculo)
       and (p_usuario is null or r.usuario_id = p_usuario)
  ),
  sel as (
    select s.id, s.ord, s.desde, row_number() over (order by s.ord, s.desde, s.id) as n
      from (select * from ab order by ord, desde, id limit p_limite + 1) s
  )
  select coalesce(jsonb_agg(x.j order by x.n) filter (where x.n <= p_limite), '[]'::jsonb), count(*)
    into v_linhas, v_cont
    from (
      select s.n,
             jsonb_build_object(
               'codigo', left(r.id::text, 8),
               'status', r.status,
               'unidade', r.unidade,
               'veiculo_id', r.veiculo_id,
               'veiculo', case when v.placa is null then v.descricao else v.placa || ' - ' || v.descricao end,
               'hora_saida_prevista', r.hora_saida,
               'desde', s.desde,
               'decorrido_s', greatest(0, floor(extract(epoch from v_agora - s.desde)))::bigint)
             || case when v_nomes then jsonb_build_object('solicitante', r.nome_solicitante)
                     else '{}'::jsonb end as j
        from sel s
        join public.requisicoes r on r.id = s.id
        join public.veiculos v on v.id = r.veiculo_id
    ) x;

  return jsonb_build_object('gerado_em', v_agora, 'colunas', to_jsonb(v_cols),
                            'total_linhas', least(v_cont, p_limite), 'truncado', v_cont > p_limite,
                            'linhas', v_linhas);
end;
$$;

comment on function public.rel_pendencias(public.unidade, uuid, integer, uuid, boolean) is
  'Relatorio: requisicoes abertas agora (solicitadas, autorizadas sem saida, em uso) com tempo no estado atual. Somente service_role.';

-- ---------------------------------------------------------------------------
-- 10. rel_auditoria
-- ---------------------------------------------------------------------------
-- Trilha de auditoria no periodo (ocorrido_em na janela), ordem ocorrido_em, id. NAO devolve
-- `detalhes` (jsonb livre). ator_nome e dado pessoal: a checagem de role (SOMENTE admin) e do BACKEND,
-- que e o unico chamador; esta funcao nao decide permissao. p_acao opcional (igualdade exata).
-- Linha: { ocorrido_em, tabela, acao, de, para, ator_nome, registro_id }
-- Retorno: { colunas, total_linhas, truncado, linhas }
create or replace function public.rel_auditoria(
  p_inicio  timestamptz,
  p_fim     timestamptz,
  p_acao    text default null,
  p_limite  integer default 5001
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_linhas jsonb;
  v_cont   integer;
begin
  perform privado.dash_validar_janela(p_inicio, p_fim);
  perform privado.rel_validar_limite(p_limite);
  if p_acao is not null and (char_length(p_acao) > 40 or p_acao !~ '^[a-z_]+$') then
    raise exception 'Ação de auditoria inválida.' using errcode = 'AG023';
  end if;

  with sel as (
    select a.id, a.ocorrido_em, row_number() over (order by a.ocorrido_em, a.id) as n
      from (select x.id, x.ocorrido_em from public.auditoria x
             where x.ocorrido_em >= p_inicio and x.ocorrido_em < p_fim
               and (p_acao is null or x.acao = p_acao)
             order by x.ocorrido_em, x.id limit p_limite + 1) a
  )
  select coalesce(jsonb_agg(x.j order by x.n) filter (where x.n <= p_limite), '[]'::jsonb), count(*)
    into v_linhas, v_cont
    from (
      select s.n,
             jsonb_build_object(
               'ocorrido_em', a.ocorrido_em, 'tabela', a.tabela, 'acao', a.acao,
               'de', a.de, 'para', a.para, 'ator_nome', p.nome, 'registro_id', a.registro_id) as j
        from sel s
        join public.auditoria a on a.id = s.id
        left join public.perfis p on p.id = a.ator_id
    ) x;

  return jsonb_build_object(
    'colunas', jsonb_build_array('ocorrido_em', 'tabela', 'acao', 'de', 'para', 'ator_nome', 'registro_id'),
    'total_linhas', least(v_cont, p_limite), 'truncado', v_cont > p_limite, 'linhas', v_linhas);
end;
$$;

comment on function public.rel_auditoria(timestamptz, timestamptz, text, integer) is
  'Relatorio: trilha de auditoria sem `detalhes`. Contem nome do ator: role admin checada no backend. Somente service_role.';

-- ---------------------------------------------------------------------------
-- 11. rel_opcionais_km
-- ---------------------------------------------------------------------------
-- Recurso Quilometragem DESLIGADO: devolve apenas {"habilitado": false} (dados gravados seguem intactos).
-- Ligado: viagens CONCLUIDAS com saida real na janela e km de saida e chegada preenchidos.
-- Linha: { codigo, unidade, veiculo_id, veiculo, saida_real_em, chegada_real_em, km_saida, km_chegada,
--          km_rodado }. totais cobrem TODAS as viagens do filtro (nao so as linhas devolvidas).
-- Retorno: { habilitado:true, colunas, totais:{viagens, km_rodado}, total_linhas, truncado, linhas }
create or replace function public.rel_opcionais_km(
  p_inicio  timestamptz,
  p_fim     timestamptz,
  p_unidade public.unidade default null,
  p_veiculo uuid default null,
  p_usuario uuid default null,
  p_limite  integer default 5001
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_km     boolean;
  v_linhas jsonb;
  v_cont   integer;
  v_viag   bigint;
  v_rodado bigint;
begin
  perform privado.dash_validar_janela(p_inicio, p_fim);
  perform privado.rel_validar_limite(p_limite);
  select coalesce(c.quilometragem, false) into v_km from public.configuracoes c where c.id;
  if not coalesce(v_km, false) then
    return jsonb_build_object('habilitado', false);
  end if;

  select count(*), coalesce(sum(r.km_chegada - r.km_saida), 0)::bigint into v_viag, v_rodado
    from public.requisicoes r
   where r.status = 'concluida' and r.saida_real_em >= p_inicio and r.saida_real_em < p_fim
     and r.km_saida is not null and r.km_chegada is not null
     and (p_unidade is null or r.unidade = p_unidade)
     and (p_veiculo is null or r.veiculo_id = p_veiculo)
     and (p_usuario is null or r.usuario_id = p_usuario);

  with sel as (
    select s.id, row_number() over (order by s.saida_real_em, s.id) as n
      from (select r.id, r.saida_real_em from public.requisicoes r
             where r.status = 'concluida' and r.saida_real_em >= p_inicio and r.saida_real_em < p_fim
               and r.km_saida is not null and r.km_chegada is not null
               and (p_unidade is null or r.unidade = p_unidade)
               and (p_veiculo is null or r.veiculo_id = p_veiculo)
               and (p_usuario is null or r.usuario_id = p_usuario)
             order by r.saida_real_em, r.id limit p_limite + 1) s
  )
  select coalesce(jsonb_agg(x.j order by x.n) filter (where x.n <= p_limite), '[]'::jsonb), count(*)
    into v_linhas, v_cont
    from (
      select s.n,
             jsonb_build_object(
               'codigo', left(r.id::text, 8), 'unidade', r.unidade, 'veiculo_id', r.veiculo_id,
               'veiculo', case when v.placa is null then v.descricao else v.placa || ' - ' || v.descricao end,
               'saida_real_em', r.saida_real_em, 'chegada_real_em', r.chegada_real_em,
               'km_saida', r.km_saida, 'km_chegada', r.km_chegada,
               'km_rodado', r.km_chegada - r.km_saida) as j
        from sel s
        join public.requisicoes r on r.id = s.id
        join public.veiculos v on v.id = r.veiculo_id
    ) x;

  return jsonb_build_object(
    'habilitado', true,
    'colunas', jsonb_build_array('codigo', 'unidade', 'veiculo_id', 'veiculo', 'saida_real_em',
                                 'chegada_real_em', 'km_saida', 'km_chegada', 'km_rodado'),
    'totais', jsonb_build_object('viagens', v_viag, 'km_rodado', v_rodado),
    'total_linhas', least(v_cont, p_limite), 'truncado', v_cont > p_limite, 'linhas', v_linhas);
end;
$$;

comment on function public.rel_opcionais_km(timestamptz, timestamptz, public.unidade, uuid, uuid, integer) is
  'Relatorio: km por viagem concluida, so se o recurso Quilometragem estiver ligado. Somente service_role.';

-- ---------------------------------------------------------------------------
-- 12. rel_opcionais_avarias
-- ---------------------------------------------------------------------------
-- Recurso Checklist DESLIGADO: devolve apenas {"habilitado": false}. Ligado: checklists COM avaria
-- registrados na janela (registrado_em), ordem registrado_em, id. descricao_avarias e TEXTO LIVRE
-- (pode conter dado pessoal): so sai com p_incluir_descricao = true (o backend decide; padrao false).
-- Linha: { codigo, momento, registrado_em, unidade, veiculo_id, veiculo, combustivel [, descricao_avarias] }
-- totais cobrem TODOS os checklists do filtro na janela.
-- Retorno: { habilitado:true, colunas, totais:{checklists, com_avaria}, total_linhas, truncado, linhas }
create or replace function public.rel_opcionais_avarias(
  p_inicio            timestamptz,
  p_fim               timestamptz,
  p_unidade           public.unidade default null,
  p_veiculo           uuid default null,
  p_usuario           uuid default null,
  p_incluir_descricao boolean default false,
  p_limite            integer default 5001
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_chk    boolean;
  v_desc   boolean := coalesce(p_incluir_descricao, false);
  v_cols   text[] := array['codigo', 'momento', 'registrado_em', 'unidade', 'veiculo_id', 'veiculo',
                           'combustivel'];
  v_linhas jsonb;
  v_cont   integer;
  v_total  bigint;
  v_com    bigint;
begin
  perform privado.dash_validar_janela(p_inicio, p_fim);
  perform privado.rel_validar_limite(p_limite);
  select coalesce(c.checklist, false) into v_chk from public.configuracoes c where c.id;
  if not coalesce(v_chk, false) then
    return jsonb_build_object('habilitado', false);
  end if;
  if v_desc then v_cols := v_cols || array['descricao_avarias']; end if;

  select count(*), count(*) filter (where k.avarias) into v_total, v_com
    from public.checklists k
    join public.requisicoes r on r.id = k.requisicao_id
   where k.registrado_em >= p_inicio and k.registrado_em < p_fim
     and (p_unidade is null or r.unidade = p_unidade)
     and (p_veiculo is null or r.veiculo_id = p_veiculo)
     and (p_usuario is null or r.usuario_id = p_usuario);

  with sel as (
    select s.id, row_number() over (order by s.registrado_em, s.id) as n
      from (select k.id, k.registrado_em from public.checklists k
              join public.requisicoes r on r.id = k.requisicao_id
             where k.avarias and k.registrado_em >= p_inicio and k.registrado_em < p_fim
               and (p_unidade is null or r.unidade = p_unidade)
               and (p_veiculo is null or r.veiculo_id = p_veiculo)
               and (p_usuario is null or r.usuario_id = p_usuario)
             order by k.registrado_em, k.id limit p_limite + 1) s
  )
  select coalesce(jsonb_agg(x.j order by x.n) filter (where x.n <= p_limite), '[]'::jsonb), count(*)
    into v_linhas, v_cont
    from (
      select s.n,
             jsonb_build_object(
               'codigo', left(r.id::text, 8), 'momento', k.momento, 'registrado_em', k.registrado_em,
               'unidade', r.unidade, 'veiculo_id', r.veiculo_id,
               'veiculo', case when v.placa is null then v.descricao else v.placa || ' - ' || v.descricao end,
               'combustivel', k.combustivel)
             || case when v_desc then jsonb_build_object('descricao_avarias', k.descricao_avarias)
                     else '{}'::jsonb end as j
        from sel s
        join public.checklists k on k.id = s.id
        join public.requisicoes r on r.id = k.requisicao_id
        join public.veiculos v on v.id = r.veiculo_id
    ) x;

  return jsonb_build_object(
    'habilitado', true,
    'colunas', to_jsonb(v_cols),
    'totais', jsonb_build_object('checklists', v_total, 'com_avaria', v_com),
    'total_linhas', least(v_cont, p_limite), 'truncado', v_cont > p_limite, 'linhas', v_linhas);
end;
$$;

comment on function public.rel_opcionais_avarias(timestamptz, timestamptz, public.unidade, uuid, uuid, boolean, integer) is
  'Relatorio: checklists com avaria, so se o recurso Checklist estiver ligado; texto livre so por flag. Somente service_role.';

-- ---------------------------------------------------------------------------
-- 13. Permissoes: so service_role executa (PUBLIC, anon e authenticated revogados)
-- ---------------------------------------------------------------------------
revoke all on function public.rel_movimentacao(timestamptz, timestamptz, text, public.unidade, uuid, uuid, boolean, boolean, integer) from public, anon, authenticated;
revoke all on function public.rel_resumo(timestamptz, timestamptz, text, public.unidade, uuid, uuid) from public, anon, authenticated;
revoke all on function public.rel_tempos(timestamptz, timestamptz, text, public.unidade, uuid, uuid) from public, anon, authenticated;
revoke all on function public.rel_pendencias(public.unidade, uuid, integer, uuid, boolean) from public, anon, authenticated;
revoke all on function public.rel_auditoria(timestamptz, timestamptz, text, integer) from public, anon, authenticated;
revoke all on function public.rel_opcionais_km(timestamptz, timestamptz, public.unidade, uuid, uuid, integer) from public, anon, authenticated;
revoke all on function public.rel_opcionais_avarias(timestamptz, timestamptz, public.unidade, uuid, uuid, boolean, integer) from public, anon, authenticated;
revoke all on function public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer) from public, anon, authenticated;
revoke all on function public.marcar_envio_email(uuid, text, text) from public, anon, authenticated;

grant execute on function public.rel_movimentacao(timestamptz, timestamptz, text, public.unidade, uuid, uuid, boolean, boolean, integer) to service_role;
grant execute on function public.rel_resumo(timestamptz, timestamptz, text, public.unidade, uuid, uuid) to service_role;
grant execute on function public.rel_tempos(timestamptz, timestamptz, text, public.unidade, uuid, uuid) to service_role;
grant execute on function public.rel_pendencias(public.unidade, uuid, integer, uuid, boolean) to service_role;
grant execute on function public.rel_auditoria(timestamptz, timestamptz, text, integer) to service_role;
grant execute on function public.rel_opcionais_km(timestamptz, timestamptz, public.unidade, uuid, uuid, integer) to service_role;
grant execute on function public.rel_opcionais_avarias(timestamptz, timestamptz, public.unidade, uuid, uuid, boolean, integer) to service_role;
grant execute on function public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer) to service_role;
grant execute on function public.marcar_envio_email(uuid, text, text) to service_role;

-- Autoverificacao: falha a migration se algum papel indevido executa, se PUBLIC executa ou se falta
-- SECURITY DEFINER / search_path fixo.
do $$
declare
  f text;
  r text;
begin
  foreach f in array array[
    'public.rel_movimentacao(timestamptz, timestamptz, text, public.unidade, uuid, uuid, boolean, boolean, integer)',
    'public.rel_resumo(timestamptz, timestamptz, text, public.unidade, uuid, uuid)',
    'public.rel_tempos(timestamptz, timestamptz, text, public.unidade, uuid, uuid)',
    'public.rel_pendencias(public.unidade, uuid, integer, uuid, boolean)',
    'public.rel_auditoria(timestamptz, timestamptz, text, integer)',
    'public.rel_opcionais_km(timestamptz, timestamptz, public.unidade, uuid, uuid, integer)',
    'public.rel_opcionais_avarias(timestamptz, timestamptz, public.unidade, uuid, uuid, boolean, integer)',
    'public.reservar_envio_email(uuid, uuid, text, text, jsonb, integer)',
    'public.marcar_envio_email(uuid, text, text)'
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
end
$$;
