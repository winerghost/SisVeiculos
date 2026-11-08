-- Migration: notificacoes do motorista (somente sino). Nova migration; nao edita as anteriores. Idempotente.
--
-- Regra: o motorista e avisado no sino quando (a) e designado a uma viagem (na autorizacao ou depois,
-- atribuicao/troca), (b) a viagem dele e cancelada, (c) e removido/trocado (o antigo e avisado).
-- Nada para motorista quando so existe uma requisicao nova e ele nao foi designado.
--
-- Tipos novos (todos NAO criticos => email_status nulo => NUNCA entram na fila de e-mail; n8n nao e acionado):
--   motorista_designado          -> o motorista novo (auditoria 'motorista_designado', para = novo)
--   motorista_removido           -> o motorista antigo (auditoria 'motorista_designado', de = antigo, para diferente)
--   viagem_cancelada_motorista   -> o motorista da requisicao (auditoria 'transicao' para 'cancelada')
-- Todos levam requisicao_id (obrigatorio) e referencia_id deterministico derivado do id da auditoria
-- (md5 -> uuid): o indice unico de deduplicacao os cobre, entao reprocessar o mesmo evento nao duplica.
-- Destinatario: ativo, nunca o ator, e so com configuracoes.motorista ligada. Sem texto nem PII no banco.
-- A falha continua acessoria: o bloco de excecao do trigger nunca bloqueia o evento principal.
-- Fuso: timestamptz (UTC; exibicao em America/Sao_Paulo).

-- 1. Tipos permitidos e coerencia com requisicao_id
alter table public.notificacoes drop constraint if exists ck_notificacoes_tipo;
alter table public.notificacoes
  add constraint ck_notificacoes_tipo check (tipo in (
    'requisicao_nova', 'requisicao_autorizada', 'requisicao_negada', 'requisicao_saida',
    'requisicao_chegada', 'requisicao_cancelada_terceiros', 'requisicao_cancelada_pelo_solicitante',
    'checklist_avaria', 'convite_aceito', 'perfil_alterado_admin', 'configuracao_alterada',
    'sistema_falha_envio',
    'motorista_designado', 'motorista_removido', 'viagem_cancelada_motorista'));

alter table public.notificacoes drop constraint if exists ck_notificacoes_requisicao_coerente;
alter table public.notificacoes
  add constraint ck_notificacoes_requisicao_coerente check (
    (requisicao_id is not null) = (
      tipo like 'requisicao\_%' or tipo = 'checklist_avaria'
      or tipo in ('motorista_designado', 'motorista_removido', 'viagem_cancelada_motorista')));

-- 2. Deduplicacao tambem para os tipos do motorista
drop index if exists public.ux_notificacoes_dedupe;
create unique index if not exists ux_notificacoes_dedupe
  on public.notificacoes (
    usuario_id, tipo,
    coalesce(requisicao_id, '00000000-0000-0000-0000-000000000000'::uuid),
    coalesce(referencia_id, '00000000-0000-0000-0000-000000000000'::uuid))
  where tipo like 'requisicao\_%'
     or tipo in ('checklist_avaria', 'convite_aceito',
                 'motorista_designado', 'motorista_removido', 'viagem_cancelada_motorista');

-- 3. Gerador (mesma funcao da migration de notificacoes + 3 ramos do motorista)
create or replace function public.notificacoes_gerar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_dono      uuid;
  v_motorista uuid;
  v_ligado    boolean;
  v_checklist boolean;
  v_codigo    text;
  v_req       uuid;
  v_ref       uuid;
begin
  begin
    if new.tabela = 'requisicoes' and new.registro_id is not null then
      if new.acao = 'criada' then
        perform privado.notificar('requisicao_nova', new.registro_id, null, true, null, new.ator_id, 'staff');

      elsif new.acao = 'transicao' then
        select r.usuario_id, r.motorista_id into v_dono, v_motorista
          from public.requisicoes r where r.id = new.registro_id;
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
              -- Viagem cancelada: avisa o motorista designado (so sino, recurso ligado, nunca o ator).
              if v_motorista is not null then
                select coalesce(c.motorista, false) into v_ligado from public.configuracoes c where c.id;
                if coalesce(v_ligado, false) then
                  perform privado.notificar('viagem_cancelada_motorista', new.registro_id,
                                            md5('vcm:' || new.id::text)::uuid, false, null,
                                            new.ator_id, 'usuario', v_motorista);
                end if;
              end if;
            else null;
          end case;
        end if;

      elsif new.acao = 'motorista_designado' then
        -- de = motorista antigo, para = novo (texto de uuid; nulos quando nao havia / foi removido).
        select coalesce(c.motorista, false) into v_ligado from public.configuracoes c where c.id;
        if coalesce(v_ligado, false) then
          v_ref := md5('md:' || new.id::text)::uuid;
          if new.para is not null then
            perform privado.notificar('motorista_designado', new.registro_id, v_ref, false, null,
                                      new.ator_id, 'usuario', new.para::uuid);
          end if;
          if new.de is not null and new.de is distinct from new.para then
            perform privado.notificar('motorista_removido', new.registro_id, v_ref, false, null,
                                      new.ator_id, 'usuario', new.de::uuid);
          end if;
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
    raise warning 'notificacoes_gerar falhou (sqlstate %)', sqlstate;
  end;
  return null;
end;
$$;

revoke all on function public.notificacoes_gerar() from public, anon, authenticated;

-- 4. Trigger: passa a incluir a acao 'motorista_designado'
drop trigger if exists trg_auditoria_notificar on public.auditoria;
create trigger trg_auditoria_notificar
  after insert on public.auditoria
  for each row
  when (new.acao in ('criada', 'transicao', 'checklist_registrado', 'convite_usado',
                     'perfil_alterado', 'configuracao_alterada', 'motorista_designado'))
  execute function public.notificacoes_gerar();
alter table public.auditoria enable always trigger trg_auditoria_notificar;

-- Autoverificacao
do $$
begin
  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.notificacoes'::regclass and conname = 'ck_notificacoes_tipo'
                    and pg_get_constraintdef(oid) like '%motorista_removido%') then
    raise exception 'ck_notificacoes_tipo sem os tipos do motorista.';
  end if;
  if to_regclass('public.ux_notificacoes_dedupe') is null then
    raise exception 'ux_notificacoes_dedupe ausente.';
  end if;
end
$$;
