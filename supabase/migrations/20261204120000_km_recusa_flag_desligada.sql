-- 20261204120000_km_recusa_flag_desligada.sql
--
-- Auditoria 2026-11-12, item B5 (Baixa). Ate aqui o gatilho requisicoes_validar_km so tornava o km
-- OBRIGATORIO quando a flag do papel do ator estava ligada; com a flag desligada (ou a Quilometragem
-- desligada) aceitava km mesmo assim, e quem descartava era so o backend (RequisicaoService._km
-- devolve {} quando _pede_km e falso, entao nada e gravado). Esta migration fecha a lacuna no banco:
--
--   * v_obriga verdadeiro: comportamento identico ao de 20261201120000 (AG012 quando falta km).
--   * v_obriga falso (Quilometragem desligada, ou flag do papel do ator desligada: vigilante_km,
--     autorizador_km, motorista_km; solicitante/ator nulo nunca lancam km): a transicao NAO pode
--     GRAVAR km novo. Recusa com AG046 quando km_saida ou km_chegada MUDA (null -> valor) nesta
--     transicao. Km ja gravado antes (flag ligada na saida e desligada depois) continua intacto e a
--     chegada segue sem km (o valor antigo nao muda, entao nao conta como "gravar").
--
-- Inalterado: imutabilidade do km gravado (AG004), km so junto da transicao (AG004), INSERT nasce
-- 'solicitada' sem km (CHECKs), ordem dos gatilhos BEFORE (trg_requisicoes_validar valida o ator
-- antes deste), SECURITY DEFINER, search_path vazio, EXECUTE revogado de todos. A importacao de dados
-- usa INSERT (o gatilho retorna cedo) e nao e afetada. Codigo novo: AG046 (proximo livre: AG047).
-- Sem DELETE, sem mudanca de RLS/grants de tabela.

create or replace function public.requisicoes_validar_km()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ligado   boolean;
  v_vig_km   boolean;
  v_aut_km   boolean;
  v_mot_km   boolean;
  v_role     public.role_usuario;
  v_obriga   boolean := false;
begin
  if tg_op = 'INSERT' then
    return new;  -- nasce 'solicitada': os CHECKs recusam qualquer km.
  end if;

  -- Gravado uma vez, imutavel.
  if (old.km_saida is not null and new.km_saida is distinct from old.km_saida)
     or (old.km_chegada is not null and new.km_chegada is distinct from old.km_chegada) then
    raise exception 'Quilometragem já gravada não pode mudar.' using errcode = 'AG004';
  end if;

  -- Sem mudar de status, km nao muda (so entra junto da transicao, que e auditada).
  if new.status = old.status then
    if new.km_saida is distinct from old.km_saida
       or new.km_chegada is distinct from old.km_chegada then
      raise exception 'Quilometragem só é registrada junto da saída ou da chegada.'
        using errcode = 'AG004';
    end if;
    return new;
  end if;

  select quilometragem, vigilante_km, autorizador_km, motorista_km
    into v_ligado, v_vig_km, v_aut_km, v_mot_km
    from public.configuracoes where id;
  if coalesce(v_ligado, false) then
    -- A obrigatoriedade depende do PAPEL do ator da transicao (ja validado por trg_requisicoes_validar).
    select p.role into v_role
      from public.perfis p
     where p.id = public.requisicao_ator(new);
    v_obriga := case
                  when v_role = 'vigilante' then coalesce(v_vig_km, false)
                  when v_role in ('autorizador', 'admin') then coalesce(v_aut_km, false)
                  when v_role = 'motorista' then coalesce(v_mot_km, false)
                  else false
                end;
  end if;

  if v_obriga then
    if new.status = 'em_uso' and new.km_saida is null then
      raise exception 'Quilometragem de saída é obrigatória.' using errcode = 'AG012';
    end if;
    if new.status = 'concluida' and (new.km_saida is null or new.km_chegada is null) then
      raise exception 'Quilometragem de saída e de chegada é obrigatória.' using errcode = 'AG012';
    end if;
  else
    -- B5: sem flag de km aplicavel, a transicao nao grava km novo (old era null; a imutabilidade
    -- acima ja cobre old nao nulo).
    if new.km_saida is distinct from old.km_saida
       or new.km_chegada is distinct from old.km_chegada then
      raise exception 'Quilometragem não é registrada com o recurso desligado para este perfil.'
        using errcode = 'AG046';
    end if;
  end if;

  return new;
end;
$$;

revoke all on function public.requisicoes_validar_km() from public, anon, authenticated, service_role;
