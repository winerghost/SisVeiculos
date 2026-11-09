-- Migration: agenda o expurgo de 12 meses das localizacoes via pg_cron, SE a extensao estiver disponivel (S-03).
-- Nova migration; NAO edita a 20261114120000 (que criou public.expurgar_localizacoes_vencidas()). Idempotente.
--
-- Motivo (LGPD, minimizacao): o expurgo era so manual. Aqui o job mensal passa a existir assim que o
-- pg_cron estiver habilitado, sem falhar a migration quando nao estiver (stack local/CI, plano sem pg_cron).
--
-- Comportamento:
--   * pg_cron ja instalada            -> agenda (ou reagenda) o job 'expurgar-localizacoes'.
--   * disponivel mas nao instalada    -> tenta `create extension pg_cron`; se o papel nao puder, avisa (NOTICE).
--   * indisponivel / qualquer falha   -> NOTICE e segue; a migration NUNCA falha por causa do agendamento.
-- Nesse caso o admin habilita a extensao no painel e rode o bloco de "Agendamento" de
-- docs/lgpd-localizacao.md (ou reaplique este SQL).
--
-- Seguranca da execucao: o job roda `select public.expurgar_localizacoes_vencidas()` com o papel que
-- agendou (postgres, dono da funcao). A funcao e SECURITY DEFINER com search_path vazio, o marcador
-- transacional do gatilho so e ligado dentro dela, e cada execucao (mesmo com 0 linhas) grava UMA linha em
-- auditoria (acao 'localizacao_expurgada', apenas contagem e corte, nunca coordenadas). O comando do job usa
-- nome qualificado e nao recebe parametro. Cron em UTC: dia 1 de cada mes, 03:23 UTC (00:23 em Brasilia).
-- Monitoramento: a ausencia da linha mensal 'localizacao_expurgada' na auditoria indica job parado.

do $$
declare
  v_job constant text := 'expurgar-localizacoes';
  v_cmd constant text := 'select public.expurgar_localizacoes_vencidas()';
begin
  if not exists (select 1 from pg_extension where extname = 'pg_cron') then
    if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
      begin
        create extension if not exists pg_cron;
      exception when others then
        raise notice 'pg_cron disponivel mas nao foi possivel instala-la agora (%): habilite no painel e agende conforme docs/lgpd-localizacao.md.', sqlerrm;
      end;
    else
      raise notice 'pg_cron indisponivel neste banco: expurgo de localizacoes NAO agendado (ver docs/lgpd-localizacao.md).';
    end if;
  end if;

  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    begin
      -- cron.schedule com nome existente atualiza o job (idempotente).
      execute format('select cron.schedule(%L, %L, %L)', v_job, '23 3 1 * *', v_cmd);
    exception when others then
      raise notice 'Falha ao agendar o expurgo no pg_cron (%): agende manualmente conforme docs/lgpd-localizacao.md.', sqlerrm;
    end;
  end if;
end
$$;
