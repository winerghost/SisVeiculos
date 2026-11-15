-- Migration: o telefone do convite so vive ate o encerramento (uso/revogacao) + rastro na auditoria.
-- Nova migration; NAO edita nenhuma anterior. Idempotente (create or replace, UPDATE que so toca
-- linhas com telefone). Depende de 20261128120100 (convites.telefone e os corpos vigentes de
-- convites_validar, consumir_convite e reenviar_convite) e de 20261107120500 (convites_auditar
-- vigente). NAO aplicar pelo servidor (db push e do pipeline).
--
-- Motivo (auditoria 2026-11-15, M1/B1): o telefone ficava para sempre em convites (imutavel, sem DELETE
-- fisico) e era copiado a cada reenvio, ferindo eliminacao e minimizacao (LGPD art. 15, 16 e 18, VI).
-- O numero so precisa esperar no convite ate o aceite; depois vive apenas em perfis.telefone.
--
-- 1. convites_validar: unica mudanca de telefone aceita e telefone -> NULL (inclusive em convite
--    encerrado, quando for o unico campo alterado). Qualquer outra segue AG004. Ao gravar uso ou
--    revogacao, o proprio gatilho zera o telefone: cobre a revogacao direta do backend
--    (UPDATE revogado_em/revogado_por) sem depender do chamador.
-- 2. consumir_convite: zera o telefone no mesmo UPDATE que grava o uso (ja copiado para perfis).
-- 3. reenviar_convite: zera o telefone da linha antiga no UPDATE que grava revogado_em; o convite
--    novo herda o numero (lido antes, em v_ant).
-- 4. convites_auditar: convite_criado ganha detalhes.telefone_informado (booleano, nunca o valor).
-- 5. Expurgo unico: telefone = NULL em convites usados, revogados ou expirados.
--    Efeito colateral aceito: reenviar um convite que JA estava expirado antes desta migration gera o
--    novo sem telefone (o admin informa de novo no perfil).
--
-- SECURITY DEFINER com search_path = '' e REVOKE/GRANT iguais aos vigentes. RLS e grants de
-- convites inalterados (sem privilegio para anon/authenticated). Fuso: timestamptz (UTC; exibicao
-- em America/Sao_Paulo).

set local lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 1. convites_validar (corpo de 20261128120100; muda so a regra do telefone)
-- ---------------------------------------------------------------------------
create or replace function public.convites_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    perform privado.exigir_admin_ativo(new.criado_por);
    if new.usado_em is not null or new.revogado_em is not null then
      raise exception 'Convite deve nascer aberto.' using errcode = 'AG001';
    end if;
    -- O chamador nao escolhe criado_em (ck_convites_prazo roda depois deste trigger).
    new.criado_em := clock_timestamp();
    return new;
  end if;

  -- UPDATE: identidade e conteudo do convite sao imutaveis. Telefone: so pode ir a NULL (LGPD).
  if new.id is distinct from old.id
     or new.email is distinct from old.email
     or new.role is distinct from old.role
     or new.unidade is distinct from old.unidade
     or (new.telefone is distinct from old.telefone and new.telefone is not null)
     or new.token_hash is distinct from old.token_hash
     or new.criado_por is distinct from old.criado_por
     or new.criado_em is distinct from old.criado_em
     or new.expira_em is distinct from old.expira_em then
    raise exception 'Dados do convite são imutáveis.' using errcode = 'AG004';
  end if;

  -- Convite encerrado (usado ou revogado): congelado; so o expurgo do telefone passa.
  if old.usado_em is not null or old.revogado_em is not null then
    if new.usado_em is distinct from old.usado_em
       or new.usado_por is distinct from old.usado_por
       or new.revogado_em is distinct from old.revogado_em
       or new.revogado_por is distinct from old.revogado_por then
      raise exception 'Convite já usado ou revogado.' using errcode = 'AG005';
    end if;
    return new;
  end if;

  -- Uso: so antes de expirar. O carimbo e sempre do servidor.
  if new.usado_em is not null then
    if old.expira_em <= clock_timestamp() then
      raise exception 'Convite expirado.' using errcode = 'AG006';
    end if;
    -- So durante consumir_convite e para o usuario cujo perfil acabou de ser criado.
    if coalesce(current_setting('sisveiculos.convite_em_consumo', true), '')
       is distinct from (new.id::text || ':' || coalesce(new.usado_por::text, '')) then
      raise exception 'O convite só pode ser usado por consumir_convite.' using errcode = 'AG004';
    end if;
    -- O perfil de usado_por tem de ter sido criado NESTA transacao (como consumir_convite faz).
    if not exists (select 1 from public.perfis
                    where id = new.usado_por and criado_em = transaction_timestamp()) then
      raise exception 'O convite só pode ser usado por consumir_convite.' using errcode = 'AG004';
    end if;
    new.usado_em := now();
    new.telefone := null;  -- ja copiado para perfis.telefone
  end if;

  -- Revogacao: so admin ativo.
  if new.revogado_em is not null then
    perform privado.exigir_admin_ativo(new.revogado_por);
    new.revogado_em := now();
    new.telefone := null;  -- convite encerrado nao guarda o numero
  end if;

  return new;
end;
$$;

revoke all on function public.convites_validar() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. consumir_convite (corpo de 20261128120100; zera o telefone no UPDATE de uso)
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
  v_conv       public.convites;
  v_email      text;
  v_confirmado timestamptz;
  v_banido     timestamptz;
  v_apagado    timestamptz;
  v_perfil     public.perfis;
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

  -- E-mail igual ao convidado E conta confirmada, ativa e nao apagada.
  -- Mesma mensagem/codigo para tudo (nao revela qual condicao falhou).
  if v_email is distinct from v_conv.email
     or v_confirmado is null
     or v_apagado is not null
     or (v_banido is not null and v_banido > clock_timestamp()) then
    raise exception 'Convite inválido.' using errcode = 'AG007';
  end if;

  -- Convite de admin cria perfil admin tendo como responsavel quem o emitiu
  -- (que precisa continuar admin ativo: o trigger de perfis revalida sob o lock).
  insert into public.perfis (id, nome, unidade, role, telefone, alterado_por)
  values (p_usuario_id, p_nome, v_conv.unidade, v_conv.role, v_conv.telefone,
          case when v_conv.role = 'admin' then v_conv.criado_por end)
  returning * into v_perfil;

  -- Autoriza o UPDATE de uso apenas para este convite e este usuario (local a transacao).
  -- O telefone ja esta no perfil: sai do convite no mesmo UPDATE.
  perform set_config('sisveiculos.convite_em_consumo', v_conv.id::text || ':' || p_usuario_id::text, true);
  update public.convites
     set usado_em = now(), usado_por = p_usuario_id, telefone = null
   where id = v_conv.id;
  perform set_config('sisveiculos.convite_em_consumo', '', true);

  return v_perfil;
end;
$$;

revoke all on function public.consumir_convite(text, uuid, text) from public, anon, authenticated;
grant execute on function public.consumir_convite(text, uuid, text) to service_role;

-- ---------------------------------------------------------------------------
-- 3. reenviar_convite (corpo de 20261128120100; zera o telefone da linha antiga)
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

  -- Admin ativo primeiro (mesma ordem dos triggers de convites): ator invalido => AG002.
  perform privado.exigir_admin_ativo(p_ator);

  -- Serializa reenvios do mesmo convite. READ COMMITTED: quem espera rele a linha atualizada.
  select * into v_ant from public.convites c where c.id = p_convite_id for update;
  if not found then
    raise exception 'Convite inválido.' using errcode = 'AG007';
  end if;
  if v_ant.usado_em is not null or v_ant.revogado_em is not null then
    raise exception 'Este convite já foi utilizado ou revogado.' using errcode = 'AG038';
  end if;

  -- A linha antiga encerra sem o numero; o novo convite o recebe de v_ant (lido acima).
  update public.convites c
     set revogado_em = v_agora, revogado_por = p_ator, telefone = null
   where c.id = v_ant.id;

  insert into public.convites (email, role, unidade, telefone, token_hash, criado_por, expira_em)
  values (v_ant.email, v_ant.role, v_ant.unidade, v_ant.telefone, p_token_hash, p_ator, p_expira_em)
  returning * into v_novo;

  return jsonb_build_object(
    'id', v_novo.id,
    'email', v_novo.email,
    'role', v_novo.role,
    'unidade', v_novo.unidade,
    'telefone', v_novo.telefone,
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
  'Revoga o convite aberto/expirado (zerando o telefone dele) e insere o novo (mesmo e-mail, papel, unidade e telefone) numa unica transacao sob FOR UPDATE. AG038 se encerrado ou concorrente. Nao devolve token_hash. Somente service_role.';

revoke all on function public.reenviar_convite(uuid, uuid, text, timestamptz)
  from public, anon, authenticated;
grant execute on function public.reenviar_convite(uuid, uuid, text, timestamptz) to service_role;

-- ---------------------------------------------------------------------------
-- 4. convites_auditar (corpo de 20261107120500 + telefone_informado, sem o valor)
-- ---------------------------------------------------------------------------
create or replace function public.convites_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    insert into public.auditoria (tabela, registro_id, acao, ator_id, detalhes)
    values ('convites', new.id, 'convite_criado', new.criado_por,
            jsonb_build_object('role', new.role, 'unidade', new.unidade, 'expira_em', new.expira_em,
                               'telefone_informado', new.telefone is not null));
  elsif old.usado_em is null and new.usado_em is not null then
    insert into public.auditoria (tabela, registro_id, acao, ator_id)
    values ('convites', new.id, 'convite_usado', new.usado_por);
  elsif old.revogado_em is null and new.revogado_em is not null then
    insert into public.auditoria (tabela, registro_id, acao, ator_id)
    values ('convites', new.id, 'convite_revogado', new.revogado_por);
  end if;
  return null;
end;
$$;

revoke all on function public.convites_auditar() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- 5. Expurgo unico (idempotente: so toca linhas que ainda tem telefone)
-- ---------------------------------------------------------------------------
update public.convites
   set telefone = null
 where telefone is not null
   and (usado_em is not null or revogado_em is not null or expira_em <= now());

-- Autoverificacao: nenhum convite encerrado guarda telefone.
do $$
begin
  if exists (select 1 from public.convites
              where telefone is not null and (usado_em is not null or revogado_em is not null)) then
    raise exception 'Expurgo incompleto: convite encerrado ainda guarda telefone.';
  end if;
end
$$;
