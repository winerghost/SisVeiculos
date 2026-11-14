-- Importação de dados não deve notificar a equipe (nem push) por requisição histórica.
-- A migration 20261124120000 passou a gravar auditoria `criada` com detalhes.origem='importacao'
-- por requisição importada; o gatilho de notificações tratava toda `criada` como pedido novo.
-- Redefine só notificacoes_gerar() (corpo atual + a guarda); grants inalterados.

CREATE OR REPLACE FUNCTION public.notificacoes_gerar()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
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
        -- Importação de dados (admin) grava `criada` só como trilha; não avisa a equipe nem gera push.
        if coalesce(new.detalhes ->> 'origem', '') = 'importacao' then
          return null;
        end if;
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
$function$;
