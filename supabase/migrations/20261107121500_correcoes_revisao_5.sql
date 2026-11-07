-- Migration 15: correcoes da quinta revisao (deadlock residual em convites).
-- Nao edita migrations anteriores: create or replace / drop trigger if exists + create. Idempotente.
--
-- Problema (medio): na migration 14, consumir_convite travava a linha do convite (FOR UPDATE)
-- ANTES de qualquer advisory; o insert em perfis (gatilho) pedia o advisory depois. Uma acao
-- administrativa que ja segurava o advisory e tocava o MESMO convite (ex.: revogar) esperava a
-- linha, enquanto consumir_convite esperava o advisory: deadlock (40P01).
--
-- Correcao:
--  (a) consumir_convite passa a tomar, como PRIMEIRA instrucao, o advisory exclusivo
--      'sisveiculos.perfis_admin' (mesma chave de exigir_admin_ativo, guarda, seed e gatilhos de
--      comando), antes do SELECT ... FOR UPDATE do convite. O corpo e o da migration 12
--      (A1/A2/B4, SQLSTATE AG005/AG006/AG007) sem outra mudanca.
--  (b) Gatilho BEFORE INSERT OR UPDATE FOR EACH STATEMENT em public.convites chama
--      public.travar_admin_comando() (migration 14), ENABLE ALWAYS: criar/revogar convite
--      (e qualquer UPDATE multilinha) toma o advisory antes de travar linhas.
--  Resultado: em todos os caminhos a ordem e advisory -> linhas.
--
-- CUSTO: consumo, criacao e revogacao de convites passam a se serializar entre si e com as
-- escritas em perfis/veiculos (advisory exclusivo global mantido ate o fim da transacao).
-- Irrelevante na v1 (poucos convites, transacoes curtas).
--
-- CONTRATO DO BACKEND (atualiza o da migration 14; nao e garantido pelo banco):
--  * SEMPRE o advisory antes de linhas: nao fazer SELECT ... FOR UPDATE/FOR SHARE em convites,
--    perfis ou veiculos antes de a transacao ter tomado o advisory (as funcoes e os gatilhos
--    de comando ja o tomam; so vale para SQL manual do backend: chame
--    pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0)) primeiro).
--  * Transacoes curtas, SET LOCAL lock_timeout curto (ex.: '5s') e retry em 55P03 e 40P01.
--  * Segue valendo: nao misturar criacao/alteracao de requisicoes (FOR SHARE em perfis/veiculos
--    no gatilho) com escrita administrativa na mesma transacao.
--
-- RISCOS RESIDUAIS: FOR SHARE de requisicoes em perfis/veiculos continua fora do advisory;
-- SQL livre como service_role/postgres pode forjar variaveis de sessao (limite herdado).
--
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- (a) consumir_convite: advisory antes da linha do convite
-- ---------------------------------------------------------------------------
create or replace function public.consumir_convite(
  p_token_hash text,
  p_usuario_id uuid,
  p_nome       text
)
returns public.perfis
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_conv      public.convites;
  v_email     text;
  v_confirmado timestamptz;
  v_banido    timestamptz;
  v_apagado   timestamptz;
  v_perfil    public.perfis;
begin
  -- Primeira instrucao: advisory exclusivo antes de qualquer linha (reentrante na sessao).
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0));

  select * into v_conv from public.convites where token_hash = p_token_hash for update;
  if not found then
    raise exception 'Convite inválido.' using errcode = 'AG007';
  end if;
  if v_conv.usado_em is not null or v_conv.revogado_em is not null then
    raise exception 'Convite já usado ou revogado.' using errcode = 'AG005';
  end if;
  if v_conv.expira_em <= clock_timestamp() then
    raise exception 'Convite expirado.' using errcode = 'AG006';
  end if;

  select lower(u.email), u.email_confirmed_at, u.banned_until, u.deleted_at
    into v_email, v_confirmado, v_banido, v_apagado
    from auth.users u
   where u.id = p_usuario_id;

  -- A2: e-mail igual ao convidado E conta confirmada, ativa e nao apagada.
  -- Mesma mensagem/codigo para tudo (nao revela qual condicao falhou).
  if v_email is distinct from v_conv.email
     or v_confirmado is null
     or v_apagado is not null
     or (v_banido is not null and v_banido > clock_timestamp()) then
    raise exception 'Convite inválido.' using errcode = 'AG007';
  end if;

  -- A1: convite de admin cria perfil admin tendo como responsavel quem o emitiu
  -- (que precisa continuar admin ativo: o trigger de perfis revalida sob o lock).
  insert into public.perfis (id, nome, unidade, role, alterado_por)
  values (p_usuario_id, p_nome, v_conv.unidade, v_conv.role,
          case when v_conv.role = 'admin' then v_conv.criado_por end)
  returning * into v_perfil;

  -- B4: autoriza o UPDATE de uso apenas para este convite e este usuario (local a transacao).
  perform set_config('sisveiculos.convite_em_consumo', v_conv.id::text || ':' || p_usuario_id::text, true);
  update public.convites set usado_em = now(), usado_por = p_usuario_id where id = v_conv.id;
  perform set_config('sisveiculos.convite_em_consumo', '', true);

  return v_perfil;
end;
$$;

revoke all on function public.consumir_convite(text, uuid, text) from public, anon, authenticated;
grant execute on function public.consumir_convite(text, uuid, text) to service_role;

-- ---------------------------------------------------------------------------
-- (b) Gatilho de comando em convites
-- ---------------------------------------------------------------------------
drop trigger if exists trg_convites_trava_comando on public.convites;
create trigger trg_convites_trava_comando
  before insert or update on public.convites
  for each statement execute function public.travar_admin_comando();
alter table public.convites enable always trigger trg_convites_trava_comando;
