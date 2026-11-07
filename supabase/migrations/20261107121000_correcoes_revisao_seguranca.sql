-- Migration 10: correcoes da revisao de seguranca.
--
-- DECISAO (v1) sobre o ator: a autoria das acoes continua vindo das COLUNAS da
-- propria linha (autorizado_por, saida_registrada_por, chegada_registrada_por,
-- cancelada_por, alterado_por, criado_por...), nao de auth.uid()/set_config.
-- Consequencia assumida: o Flask (service_role) e confiavel para informar o ator
-- correto; o banco valida existencia, atividade e papel do ator, mas NAO consegue
-- provar que foi aquele usuario quem pediu a acao. Por isso o pytest de IDOR/
-- permissoes do BACKEND e obrigatorio (nao e opcional) quando o backend existir:
-- ele e a unica barreira contra um ator forjado.
--
-- NAO muda (decisoes pendentes do usuario): o autorizador pode autorizar a
-- propria requisicao; os codigos AG001..AG010 seguem iguais. Novos codigos:
--   AG011 veiculo inexistente ou inativo.
--
-- Conteudo:
--  1. requisicoes_validar reescrita: carimbos sempre do servidor (clock_timestamp),
--     carimbo enviado pelo chamador => AG004; criado_em forcado no INSERT; campos
--     descritivos congelados fora de 'solicitada' (AG004); veiculo precisa estar
--     ativo no INSERT e na troca (AG011); leitura de ator/veiculo com FOR SHARE
--     (serializa com desativacao concorrente).
--  2. Triggers de guarda em ENABLE ALWAYS (valem mesmo com session_replication_role=replica).
--  3. service_role: sem TRIGGER/REFERENCES/DELETE/TRUNCATE; em auditoria so SELECT.
--     Escrita em auditoria so por triggers (SECURITY DEFINER) e por
--     public.registrar_auditoria (lista fechada de acoes/tabelas).
--  4. CHECKs em auditoria (acao/tabela em lista; detalhes < 2048 bytes).
--  5. Auditoria de perfis (criacao e troca de unidade; role/ativo ja saem da
--     migration 7, sem duplicar) e de veiculos (criacao e alteracao). So ids/campos.
--  6. Default privileges: objetos futuros em public nascem sem acesso de anon/authenticated.
--  7. Veiculo ficticio da migration 2 desativado (ver aviso no bloco).
--  8. Indices nas FKs de requisicoes (autorizado_por, *_registrada_por, cancelada_por).
--  9. Politicas explicitas using(false)/with check(false) para escrita (e leitura
--     de convites), alem do deny-by-default.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. requisicoes_validar (substitui a da migration 5)
-- ---------------------------------------------------------------------------
create or replace function public.requisicoes_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator         uuid;
  v_role         public.role_usuario;
  v_ativo        boolean;
  v_veiculo_ativo boolean;
begin
  if tg_op = 'INSERT' then
    if new.status <> 'solicitada' then
      raise exception 'Requisição nova deve nascer como solicitada.' using errcode = 'AG001';
    end if;
    if new.autorizado_em is not null or new.saida_real_em is not null
       or new.chegada_real_em is not null or new.cancelada_em is not null then
      raise exception 'Carimbos de data/hora são definidos pelo servidor.' using errcode = 'AG004';
    end if;
    new.criado_em := clock_timestamp();

    select ativo into v_ativo from public.perfis where id = new.usuario_id for share;
    if not found or not v_ativo then
      raise exception 'Usuário solicitante inexistente ou inativo.' using errcode = 'AG002';
    end if;

    select ativo into v_veiculo_ativo from public.veiculos where id = new.veiculo_id for share;
    if not found or not v_veiculo_ativo then
      raise exception 'Veículo inexistente ou inativo.' using errcode = 'AG011';
    end if;
    return new;
  end if;

  -- UPDATE ------------------------------------------------------------------
  if new.id is distinct from old.id
     or new.usuario_id is distinct from old.usuario_id
     or new.criado_em is distinct from old.criado_em then
    raise exception 'Campos id, usuario_id e criado_em são imutáveis.' using errcode = 'AG004';
  end if;

  -- Estados terminais: linha congelada (atualizado_em e ajustado pelo outro trigger).
  if old.status in ('negada', 'cancelada', 'concluida') then
    if (to_jsonb(new) - 'atualizado_em') is distinct from (to_jsonb(old) - 'atualizado_em') then
      raise exception 'Requisição encerrada (%) não pode ser alterada.', old.status
        using errcode = 'AG001';
    end if;
    return new;
  end if;

  -- Carimbos nunca vem do chamador: qualquer diferenca em relacao a linha atual e recusada
  -- (o servidor os preenche abaixo, apos esta checagem).
  if new.autorizado_em   is distinct from old.autorizado_em
     or new.saida_real_em   is distinct from old.saida_real_em
     or new.chegada_real_em is distinct from old.chegada_real_em
     or new.cancelada_em    is distinct from old.cancelada_em then
    raise exception 'Carimbos de data/hora são definidos pelo servidor.' using errcode = 'AG004';
  end if;

  -- Campos descritivos so mudam enquanto 'solicitada'.
  if old.status <> 'solicitada' and (
        new.hora_saida       is distinct from old.hora_saida
     or new.trajeto          is distinct from old.trajeto
     or new.unidade          is distinct from old.unidade
     or new.nome_solicitante is distinct from old.nome_solicitante
     or new.observacao       is distinct from old.observacao) then
    raise exception 'Dados da requisição não podem mudar após a decisão do autorizador.'
      using errcode = 'AG004';
  end if;

  -- Veiculo so muda enquanto 'solicitada' e o novo precisa estar ativo.
  if new.veiculo_id is distinct from old.veiculo_id then
    if old.status <> 'solicitada' then
      raise exception 'O veículo não pode mudar após a decisão do autorizador.' using errcode = 'AG004';
    end if;
    select ativo into v_veiculo_ativo from public.veiculos where id = new.veiculo_id for share;
    if not found or not v_veiculo_ativo then
      raise exception 'Veículo inexistente ou inativo.' using errcode = 'AG011';
    end if;
  end if;

  -- Colunas de autorizacao, saida, chegada e cancelamento: gravadas uma vez, imutaveis.
  if (old.autorizado_por         is not null and new.autorizado_por         is distinct from old.autorizado_por)
  or (old.saida_registrada_por   is not null and new.saida_registrada_por   is distinct from old.saida_registrada_por)
  or (old.chegada_registrada_por is not null and new.chegada_registrada_por is distinct from old.chegada_registrada_por)
  or (old.cancelada_por          is not null and new.cancelada_por          is distinct from old.cancelada_por)
  then
    raise exception 'Dados de autorização, saída, chegada e cancelamento já gravados não podem mudar.'
      using errcode = 'AG004';
  end if;

  if new.status = old.status then
    -- Sem mudar de status, os responsaveis nao podem ser preenchidos (so na transicao).
    if new.autorizado_por         is distinct from old.autorizado_por
       or new.saida_registrada_por   is distinct from old.saida_registrada_por
       or new.chegada_registrada_por is distinct from old.chegada_registrada_por
       or new.cancelada_por          is distinct from old.cancelada_por then
      raise exception 'Responsáveis só são registrados junto da transição de status.'
        using errcode = 'AG004';
    end if;
    return new;  -- edicao de campos descritivos; CHECKs cuidam da coerencia.
  end if;

  -- Transicao permitida?
  if not (case old.status
            when 'solicitada' then new.status in ('autorizada', 'negada', 'cancelada')
            when 'autorizada' then new.status in ('em_uso', 'cancelada')
            when 'em_uso'     then new.status = 'concluida'
            else false
          end) then
    raise exception 'Transição inválida: % -> %.', old.status, new.status using errcode = 'AG001';
  end if;

  -- Ator da transicao: existe, esta ativo e tem papel adequado (FOR SHARE: nao pode
  -- ser desativado/rebaixado por outra transacao enquanto esta nao terminar).
  v_ator := public.requisicao_ator(new);
  if v_ator is null then
    raise exception 'Transição para % exige o responsável registrado.', new.status using errcode = 'AG002';
  end if;

  select role, ativo into v_role, v_ativo from public.perfis where id = v_ator for share;
  if not found or not v_ativo then
    raise exception 'Responsável pela transição inexistente ou inativo.' using errcode = 'AG002';
  end if;

  if new.status = 'cancelada' then
    -- admin/autorizador cancelam solicitada ou autorizada; solicitante so a propria e so 'solicitada'.
    if not (v_role in ('admin', 'autorizador')
            or (v_ator = old.usuario_id and old.status = 'solicitada')) then
      raise exception 'Sem permissão para cancelar esta requisição.' using errcode = 'AG002';
    end if;
  elsif v_role not in ('autorizador', 'admin') then
    raise exception 'Apenas autorizador ou admin pode autorizar, negar ou registrar saída/chegada.'
      using errcode = 'AG002';
  end if;

  -- Carimbos do servidor (sobrescrevem sempre).
  case new.status
    when 'autorizada', 'negada' then new.autorizado_em := clock_timestamp();
    when 'em_uso'               then new.saida_real_em := clock_timestamp();
    when 'concluida'            then new.chegada_real_em := clock_timestamp();
    when 'cancelada'            then new.cancelada_em := clock_timestamp();
    else null;
  end case;

  return new;
end;
$$;

revoke all on function public.requisicoes_validar() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- 4 (antes do uso). CHECKs em auditoria
-- ---------------------------------------------------------------------------
alter table public.auditoria drop constraint if exists ck_auditoria_acao;
alter table public.auditoria
  add constraint ck_auditoria_acao check (acao in (
    'criada', 'transicao',
    'convite_criado', 'convite_usado', 'convite_revogado',
    'perfil_criado', 'perfil_alterado', 'perfil_unidade_alterada', 'admin_semeado',
    'configuracao_alterada',
    'veiculo_criado', 'veiculo_alterado',
    'acao_administrativa', 'acesso_negado'
  ));

alter table public.auditoria drop constraint if exists ck_auditoria_tabela;
alter table public.auditoria
  add constraint ck_auditoria_tabela
  check (tabela in ('requisicoes', 'convites', 'perfis', 'veiculos', 'configuracoes'));

alter table public.auditoria drop constraint if exists ck_auditoria_detalhes_tamanho;
alter table public.auditoria
  add constraint ck_auditoria_detalhes_tamanho check (octet_length(detalhes::text) < 2048);

-- Registro de eventos pelo backend (service_role): lista fechada de acoes. As
-- transicoes e demais acoes de dominio saem SOMENTE dos triggers.
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
  if p_acao not in ('acao_administrativa', 'acesso_negado') then
    raise exception 'Ação de auditoria não permitida por esta função.' using errcode = 'AG003';
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
-- 5. Auditoria de perfis e veiculos (so ids/campos; sem dados pessoais)
-- ---------------------------------------------------------------------------
create or replace function public.perfis_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    insert into public.auditoria (tabela, registro_id, acao, ator_id, para)
    values ('perfis', new.id, 'perfil_criado', null,
            new.role::text || '/' || case when new.ativo then 'ativo' else 'inativo' end);
  elsif new.unidade is distinct from old.unidade then
    -- role/ativo sao auditados (com ator) pelo trigger da migration 7.
    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para)
    values ('perfis', new.id, 'perfil_unidade_alterada', null, old.unidade::text, new.unidade::text);
  end if;
  return null;
end;
$$;

revoke all on function public.perfis_auditar() from public, anon, authenticated;

drop trigger if exists trg_perfis_auditoria on public.perfis;
create trigger trg_perfis_auditoria
  after insert or update on public.perfis
  for each row execute function public.perfis_auditar();

create or replace function public.veiculos_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_campos text[];
begin
  if tg_op = 'INSERT' then
    insert into public.auditoria (tabela, registro_id, acao, ator_id)
    values ('veiculos', new.id, 'veiculo_criado', null);
    return null;
  end if;

  v_campos := array_remove(array[
    case when new.descricao is distinct from old.descricao then 'descricao' end,
    case when new.placa     is distinct from old.placa     then 'placa' end,
    case when new.ativo     is distinct from old.ativo     then 'ativo' end
  ], null);

  if coalesce(array_length(v_campos, 1), 0) > 0 then
    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
    values ('veiculos', new.id, 'veiculo_alterado', null,
            case when new.ativo is distinct from old.ativo then old.ativo::text end,
            case when new.ativo is distinct from old.ativo then new.ativo::text end,
            jsonb_build_object('campos', to_jsonb(v_campos)));
  end if;
  return null;
end;
$$;

revoke all on function public.veiculos_auditar() from public, anon, authenticated;

drop trigger if exists trg_veiculos_auditoria on public.veiculos;
create trigger trg_veiculos_auditoria
  after insert or update on public.veiculos
  for each row execute function public.veiculos_auditar();

-- ---------------------------------------------------------------------------
-- 3. Grants do service_role (e limpeza de privilegios sem uso)
-- ---------------------------------------------------------------------------
revoke trigger, references on table
  public.perfis, public.veiculos, public.requisicoes, public.auditoria,
  public.configuracoes, public.convites
  from service_role, anon, authenticated;

-- Sem DELETE/TRUNCATE fisico tambem por privilegio (alem dos triggers).
revoke delete, truncate on table
  public.perfis, public.veiculos, public.requisicoes,
  public.configuracoes, public.convites
  from service_role;

-- auditoria: service_role so le. Escrita via triggers/registrar_auditoria.
revoke all on table public.auditoria from service_role;
grant select on table public.auditoria to service_role;

-- ---------------------------------------------------------------------------
-- 6. Default privileges: objetos futuros em public sem acesso de anon/authenticated.
-- Vale para objetos criados pelo papel postgres (o das migrations). Cada migration
-- futura deve conceder explicitamente o que a API precisar.
-- ---------------------------------------------------------------------------
alter default privileges for role postgres in schema public revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on functions from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 7. Veiculo ficticio da migration 2: desativado se ainda for o de exemplo.
-- AVISO: em banco novo (db reset) nao sobra veiculo ativo. Quem precisar de um
-- (ex.: Playwright/e2e) deve cadastra-lo. Veiculo real ja cadastrado pelo admin
-- (outra placa/descricao) nao e afetado.
-- ---------------------------------------------------------------------------
update public.veiculos
   set ativo = false
 where placa = 'AAA0A00' and descricao = 'Veículo de exemplo' and ativo;

-- ---------------------------------------------------------------------------
-- 8. Indices nas FKs de requisicoes
-- ---------------------------------------------------------------------------
create index if not exists ix_requisicoes_autorizado_por
  on public.requisicoes (autorizado_por) where autorizado_por is not null;
create index if not exists ix_requisicoes_saida_registrada_por
  on public.requisicoes (saida_registrada_por) where saida_registrada_por is not null;
create index if not exists ix_requisicoes_chegada_registrada_por
  on public.requisicoes (chegada_registrada_por) where chegada_registrada_por is not null;
create index if not exists ix_requisicoes_cancelada_por
  on public.requisicoes (cancelada_por) where cancelada_por is not null;

-- ---------------------------------------------------------------------------
-- 9. Politicas explicitas de negacao (escrita em todas; leitura de convites e anon)
-- ---------------------------------------------------------------------------
do $$
declare
  t text;
begin
  foreach t in array array['perfis', 'veiculos', 'requisicoes', 'auditoria', 'configuracoes', 'convites']
  loop
    execute format('drop policy if exists %I on public.%I', t || '_sem_insert', t);
    execute format('create policy %I on public.%I for insert to anon, authenticated with check (false)',
                   t || '_sem_insert', t);
    execute format('drop policy if exists %I on public.%I', t || '_sem_update', t);
    execute format('create policy %I on public.%I for update to anon, authenticated using (false) with check (false)',
                   t || '_sem_update', t);
    execute format('drop policy if exists %I on public.%I', t || '_sem_delete', t);
    execute format('create policy %I on public.%I for delete to anon, authenticated using (false)',
                   t || '_sem_delete', t);
    execute format('drop policy if exists %I on public.%I', t || '_anon_sem_leitura', t);
    execute format('create policy %I on public.%I for select to anon using (false)',
                   t || '_anon_sem_leitura', t);
  end loop;

  drop policy if exists convites_sem_leitura on public.convites;
  create policy convites_sem_leitura on public.convites
    for select to authenticated using (false);
end
$$;

-- ---------------------------------------------------------------------------
-- 2. Triggers de guarda em ENABLE ALWAYS (por ultimo: inclui os criados acima)
-- ---------------------------------------------------------------------------
do $$
declare
  r record;
begin
  for r in
    select * from (values
      ('requisicoes',  'trg_requisicoes_atualizado_em'),
      ('requisicoes',  'trg_requisicoes_sem_delete'),
      ('requisicoes',  'trg_requisicoes_sem_truncate'),
      ('requisicoes',  'trg_requisicoes_validar'),
      ('requisicoes',  'trg_requisicoes_auditoria'),
      ('perfis',       'trg_perfis_atualizado_em'),
      ('perfis',       'trg_perfis_sem_delete'),
      ('perfis',       'trg_perfis_sem_truncate'),
      ('perfis',       'trg_perfis_guarda_admin'),
      ('perfis',       'trg_perfis_auditoria'),
      ('veiculos',     'trg_veiculos_atualizado_em'),
      ('veiculos',     'trg_veiculos_sem_delete'),
      ('veiculos',     'trg_veiculos_sem_truncate'),
      ('veiculos',     'trg_veiculos_auditoria'),
      ('auditoria',    'trg_auditoria_append_only'),
      ('auditoria',    'trg_auditoria_sem_truncate'),
      ('convites',     'trg_convites_sem_delete'),
      ('convites',     'trg_convites_sem_truncate'),
      ('convites',     'trg_convites_validar'),
      ('convites',     'trg_convites_auditoria'),
      ('configuracoes','trg_configuracoes_atualizado_em'),
      ('configuracoes','trg_configuracoes_sem_delete'),
      ('configuracoes','trg_configuracoes_sem_truncate'),
      ('configuracoes','trg_configuracoes_validar')
    ) as t(tabela, gatilho)
  loop
    execute format('alter table public.%I enable always trigger %I', r.tabela, r.gatilho);
  end loop;
end
$$;
