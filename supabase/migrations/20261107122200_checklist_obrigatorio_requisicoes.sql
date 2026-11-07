-- Migration 22: recurso opcional CHECKLIST, parte 4 (obrigatoriedade na transicao da requisicao).
-- Nova migration; nao edita as anteriores. Idempotente.
--
-- Motivo: CLAUDE.md ("Recursos opcionais"): com o recurso ligado em configuracoes.checklist, o
-- checklist e obrigatorio na saida e na chegada. Modelo: requisicoes_validar_km (migration 18).
--
-- Regra (lida de configuracoes.checklist NA HORA da escrita, fonte da verdade unica):
--  * ligado: a transicao para em_uso exige checklist 'saida' existente; a transicao para
--    concluida exige checklist 'chegada' existente. Falta => AG013.
--  * desligado: o banco nao exige nada (e nao recusa checklist valido; ignorar a entrada e
--    recusar os endpoints com o recurso desligado e do BACKEND, como na quilometragem).
--
-- REGRA DE BORDA (recurso ligado com viagem ja em_uso, sem checklist de saida):
--  * A conclusao exige SOMENTE o checklist de chegada. O de saida NAO e exigido (a saida ja
--    ocorreu sem o recurso) e NAO pode mais ser registrado retroativamente (AG014, migration 20:
--    'saida' so com a requisicao 'autorizada'). O de chegada e registrado com a requisicao
--    em_uso, como sempre.
--  * Ligado com requisicao 'autorizada': o de saida e registrado nessa janela e passa a ser
--    exigido na saida.
--  * Desligado depois de registrado: os checklists permanecem (desligar nunca apaga).
--  * Cancelada com checklist de saida ja registrado: o checklist fica (imutavel) e a requisicao
--    e terminal.
--  * Corrida admin liga/desliga x transicao: inerente; vale o valor visivel ao statement
--    (mesma regra da quilometragem).
--
-- Gatilho BEFORE UPDATE OF status, ENABLE ALWAYS, SECURITY DEFINER com search_path vazio.
-- Leitura do flag SEM FOR SHARE (contrato da migration 15: sem espera nem ordem de lock nova
-- entre requisicoes e configuracoes). A leitura de checklists nao trava nada: checklists so
-- crescem (imutaveis, sem DELETE), logo nao ha estado intermediario a proteger.
-- ORDEM entre gatilhos BEFORE (alfabetica pelo nome): trg_requisicoes_validar (transicao, ator,
-- carimbos) -> trg_requisicoes_validar_km (AG012) -> trg_requisicoes_validar_pos_km_checklist
-- (AG013). O nome carrega "pos_km" de proposito: 'p' ordena depois de 'k'. Prioridade de erros:
-- AG001/AG002 > AG012 > AG013.
--
-- Erros: AG013 checklist obrigatorio (recurso ligado). O backend traduz o SQLSTATE.
-- RLS/grants: nao mudam. Fuso: nao se aplica.

create or replace function public.requisicoes_validar_checklist()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ligado boolean;
begin
  if new.status = old.status or new.status not in ('em_uso', 'concluida') then
    return new;
  end if;

  select checklist into v_ligado from public.configuracoes where id;
  if not coalesce(v_ligado, false) then
    return new;
  end if;

  if new.status = 'em_uso'
     and not exists (select 1 from public.checklists
                      where requisicao_id = new.id and momento = 'saida') then
    raise exception 'Checklist de saída é obrigatório.' using errcode = 'AG013';
  end if;

  if new.status = 'concluida'
     and not exists (select 1 from public.checklists
                      where requisicao_id = new.id and momento = 'chegada') then
    raise exception 'Checklist de chegada é obrigatório.' using errcode = 'AG013';
  end if;

  return new;
end;
$$;

revoke all on function public.requisicoes_validar_checklist() from public, anon, authenticated;

drop trigger if exists trg_requisicoes_validar_pos_km_checklist on public.requisicoes;
create trigger trg_requisicoes_validar_pos_km_checklist
  before update of status on public.requisicoes
  for each row execute function public.requisicoes_validar_checklist();
alter table public.requisicoes enable always trigger trg_requisicoes_validar_pos_km_checklist;

-- Funcoes novas (RPC registrar_checklist) e tabelas novas: PostgREST relê o schema.
notify pgrst, 'reload schema';
