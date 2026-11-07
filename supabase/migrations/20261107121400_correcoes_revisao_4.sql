-- Migration 14: correcoes da quarta revisao (migrations 12 e 13).
-- Nao edita migrations anteriores: create or replace / drop trigger if exists + create
-- (create or replace preserva a ACL; revoke repetidos por seguranca). Idempotente.
--
-- Corrige:
--  M-A (medio) privado.exigir_admin_ativo tomava o lock consultivo COMPARTILHADO e
--             perfis_guarda_admin (e o seed) pedem o EXCLUSIVO na mesma chave
--             'sisveiculos.perfis_admin'. Quem segurava o compartilhado e depois alterava
--             role/ativo/unidade (ex.: revogar convites de um admin e depois desativa-lo;
--             criar veiculo e depois trocar unidade) promovia S -> X, e duas transacoes assim
--             davam deadlock (40P01). Agora o EXCLUSIVO (pg_advisory_xact_lock) e o UNICO
--             modo usado em exigir_admin_ativo: nao ha promocao nem mistura de modos
--             (X -> X na mesma sessao e reentrante). Custo: acoes administrativas (criar/
--             revogar convite, criar/alterar veiculo, mudar perfil) passam a se serializar
--             entre si; as transacoes sao curtas e o volume e baixo.
--  M-B (medio) UPDATE/INSERT multilinha em perfis: a linha era travada ANTES do BEFORE ROW
--             pedir o advisory. Duas transacoes (uma com o advisory e querendo a linha, outra
--             com a linha e querendo o advisory) davam deadlock. Agora ha um trigger BEFORE
--             INSERT OR UPDATE FOR EACH STATEMENT em perfis e em veiculos que toma o advisory
--             exclusivo ANTES de qualquer linha ser travada. Em veiculos tambem se aplica
--             (veiculos_validar chama exigir_admin_ativo por linha). Funcao security
--             definer, search_path vazio, sem EXECUTE para public/anon/authenticated, gatilho
--             em ENABLE ALWAYS. Reentrante: o lock ja tomado na mesma sessao (guarda por
--             linha, seed, exigir_admin_ativo) nao conflita; o seed segue funcionando (toma o
--             lock, o gatilho de comando o toma de novo). Efeito colateral aceito: QUALQUER
--             insert/update em perfis ou veiculos (inclusive so o nome, ou zero linhas) pega o
--             lock exclusivo e serializa as escritas nessas duas tabelas.
--             convites NAO ganhou gatilho de comando: consumir_convite trava a linha do
--             convite (FOR UPDATE) antes de inserir o perfil, e nenhum gatilho de comando
--             muda essa ordem (risco residual abaixo).
--  Baixo 1    perfis_guarda_admin so grava, e perfis_auditar so consome, a variavel de sessao
--             sisveiculos.ator_criacao_<id> quando new.role = 'admin'. No ramo nao admin a
--             variavel e limpa/ignorada (antes, uma variavel forjada para o id de um perfil
--             nao admin entrava como ator de 'perfil_criado').
--  Baixo 3    veiculos_auditar (migration 13) tinha v_chave/v_ator declarados com
--             "...::uuid;begin" colado e a chave duplicada. Reescrita com declaracao simples
--             e atribuicao no corpo.
--  Baixo 4    (documentacao) ver "CONTRATO DO BACKEND".
--  M-C        (testes) supabase/tests/test_seed_primeiro_admin.py (banco virgem) e
--             supabase/tests/test_correcoes_migration_14.py (X -> X, gatilho de comando).
--             test_correcoes_revisao_3.py: o teste "dois compartilhados nao se bloqueiam"
--             virou "duas acoes de convite se serializam", pois o compartilhado deixou de existir.
--
-- CONTRATO DO BACKEND (nao e garantido pelo banco):
--  * Uma escrita administrativa em perfis por transacao (um perfil por transacao) e
--    transacoes curtas: o advisory e mantido ate o COMMIT/ROLLBACK e bloqueia todas as outras
--    escritas em perfis/veiculos e acoes de convite.
--  * Definir lock_timeout curto na sessao do backend (ex.: SET LOCAL lock_timeout = '5s') e
--    tratar 55P03 (lock_not_available) e 40P01 (deadlock_detected) com nova tentativa.
--  * NAO usar SELECT ... FOR UPDATE/FOR SHARE em perfis ou veiculos antes de escrever neles
--    na mesma transacao (a linha seria travada antes do advisory).
--  * NAO misturar na mesma transacao a criacao/alteracao de requisicoes (o gatilho de
--    requisicoes faz FOR SHARE em perfis/veiculos) com escrita administrativa em perfis/
--    veiculos, nem consumir_convite (FOR UPDATE na linha do convite) com outras acoes
--    administrativas.
--  * INSERT ... ON CONFLICT DO UPDATE em veiculos/perfis: o BEFORE INSERT zera alterado_por
--    na linha proposta, entao EXCLUDED.alterado_por chega NULO (EXCLUDED reflete os efeitos
--    dos gatilhos BEFORE INSERT). Por isso "SET alterado_por = excluded.alterado_por" NAO
--    funciona: repasse o responsavel de novo no SET, como parametro/literal
--    ("... DO UPDATE SET descricao = excluded.descricao, alterado_por = %s"). Sem isso, o
--    ramo UPDATE e recusado com AG002 quando muda campo auditavel (veiculos: descricao,
--    placa, ativo; perfis: role, ativo, unidade). Preferir UPDATE separado do INSERT.
--
-- RISCOS RESIDUAIS:
--  * Deadlock por ordem de lock de LINHA fora dos gatilhos de comando: FOR SHARE de
--    requisicoes em perfis/veiculos; FOR UPDATE de consumir_convite na linha do convite
--    contra uma transacao administrativa que ja segura o advisory e toca o mesmo convite.
--    Mitigacao: contrato acima (lock_timeout + retry).
--  * Serializacao global de escritas em perfis/veiculos (aceita; baixo volume).
--  * SQL livre como service_role/postgres continua podendo forjar variaveis de sessao
--    (limite herdado das migrations 11 a 13).
--
-- PENDENTES (decisao do usuario; NAO tratados aqui): autoautorizacao, B6.
--
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- M-A. exigir_admin_ativo: somente lock consultivo EXCLUSIVO
-- ---------------------------------------------------------------------------
create or replace function privado.exigir_admin_ativo(p_ator uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role  public.role_usuario;
  v_ativo boolean;
begin
  if p_ator is null then
    raise exception 'Ação administrativa exige o responsável registrado.' using errcode = 'AG002';
  end if;
  -- Exclusivo (unico modo usado com esta chave): sem promocao S -> X. Reentrante na mesma
  -- sessao, entao nao conflita com o lock ja tomado por guarda, seed ou gatilho de comando.
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0));
  -- Leitura apos o lock (READ COMMITTED: enxerga o que ja foi comitado).
  select role, ativo into v_role, v_ativo from public.perfis where id = p_ator;
  if not found or not v_ativo or v_role <> 'admin' then
    raise exception 'Apenas administrador ativo pode executar esta ação.' using errcode = 'AG002';
  end if;
end;
$$;

revoke all on function privado.exigir_admin_ativo(uuid) from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- M-B. Gatilho de COMANDO: advisory antes de qualquer linha ser travada
-- ---------------------------------------------------------------------------
create or replace function public.travar_admin_comando()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  -- Mesma chave do guarda, de exigir_admin_ativo e do seed (reentrante na mesma sessao).
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0));
  return null;  -- gatilho de comando BEFORE: o retorno e ignorado
end;
$$;

revoke all on function public.travar_admin_comando() from public, anon, authenticated;

drop trigger if exists trg_perfis_trava_comando on public.perfis;
create trigger trg_perfis_trava_comando
  before insert or update on public.perfis
  for each statement execute function public.travar_admin_comando();
alter table public.perfis enable always trigger trg_perfis_trava_comando;

drop trigger if exists trg_veiculos_trava_comando on public.veiculos;
create trigger trg_veiculos_trava_comando
  before insert or update on public.veiculos
  for each statement execute function public.travar_admin_comando();
alter table public.veiculos enable always trigger trg_veiculos_trava_comando;

-- ---------------------------------------------------------------------------
-- Baixo 1. perfis_guarda_admin: ator_criacao_<id> so no ramo admin
-- ---------------------------------------------------------------------------
create or replace function public.perfis_guarda_admin()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator         uuid := new.alterado_por;
  v_outros       integer;
  v_muda_papel   boolean;
  v_muda_unidade boolean;
  v_chave        text;
begin
  new.alterado_por := null;  -- nunca persiste

  if tg_op = 'INSERT' then
    v_chave := 'sisveiculos.ator_criacao_' || replace(new.id::text, '-', '');
    if new.role = 'admin' then
      -- Mesma chave da migration 7 e do seed: serializa criacao e alteracao de admins.
      perform pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0));
      if exists (select 1 from public.perfis where role = 'admin' and ativo) then
        -- Havendo admin ativo, o responsavel precisa ser um deles.
        perform privado.exigir_admin_ativo(v_ator);
        -- Informa o ator a perfis_auditar (AFTER), por perfil.
        perform set_config(v_chave, v_ator::text, true);
      elsif coalesce(current_setting('sisveiculos.seed_em_curso', true), '') <> '1' then
        -- Bootstrap (sem admin ativo) so durante privado.semear_primeiro_admin.
        raise exception 'O primeiro administrador só pode ser criado pelo seed.' using errcode = 'AG002';
      else
        -- Seed: sem ator; descarta qualquer valor residual.
        perform set_config(v_chave, '', true);
      end if;
    elsif coalesce(current_setting(v_chave, true), '') <> '' then
      -- Baixo 1: perfil nao admin nao tem ator de criacao; limpa variavel forjada/residual.
      perform set_config(v_chave, '', true);
    end if;
    return new;
  end if;

  if new.id is distinct from old.id then
    raise exception 'O id do perfil é imutável.' using errcode = 'AG004';
  end if;

  v_muda_papel   := new.role is distinct from old.role or new.ativo is distinct from old.ativo;
  v_muda_unidade := new.unidade is distinct from old.unidade;

  -- Nem papel nem unidade mudaram (ex.: so o nome): nao e acao administrativa.
  if not v_muda_papel and not v_muda_unidade then
    return new;
  end if;

  -- Exclusivo ANTES de exigir_admin_ativo (tambem exclusivo): sem promocao de lock.
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0));

  perform privado.exigir_admin_ativo(v_ator);

  if v_muda_papel then
    if v_ator = old.id then
      raise exception 'O administrador não pode alterar o próprio papel nem desativar a si mesmo.'
        using errcode = 'AG008';
    end if;

    -- Deixaria de existir admin ativo alem deste?
    if old.role = 'admin' and old.ativo and not (new.role = 'admin' and new.ativo) then
      select count(*) into v_outros
        from public.perfis
       where role = 'admin' and ativo and id <> old.id;
      if v_outros = 0 then
        raise exception 'Deve existir ao menos um administrador ativo.' using errcode = 'AG009';
      end if;
    end if;

    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para)
    values ('perfis', old.id, 'perfil_alterado', v_ator,
            old.role::text || '/' || case when old.ativo then 'ativo' else 'inativo' end,
            new.role::text || '/' || case when new.ativo then 'ativo' else 'inativo' end);
  end if;

  if v_muda_unidade then
    -- Informa o ator a perfis_auditar (AFTER), por perfil.
    perform set_config('sisveiculos.ator_unidade_' || replace(new.id::text, '-', ''),
                       v_ator::text, true);
  end if;

  return new;
end;
$$;

revoke all on function public.perfis_guarda_admin() from public, anon, authenticated;

create or replace function public.perfis_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_chave text;
  v_ator  uuid;
begin
  if tg_op = 'INSERT' then
    v_chave := 'sisveiculos.ator_criacao_' || replace(new.id::text, '-', '');
    -- Baixo 1: so admin tem ator de criacao; para os demais a variavel e ignorada.
    if new.role = 'admin' then
      v_ator := nullif(current_setting(v_chave, true), '')::uuid;
    end if;
    if coalesce(current_setting(v_chave, true), '') <> '' then
      perform set_config(v_chave, '', true);  -- consumido/descartado
    end if;
    insert into public.auditoria (tabela, registro_id, acao, ator_id, para)
    values ('perfis', new.id, 'perfil_criado', v_ator,
            new.role::text || '/' || case when new.ativo then 'ativo' else 'inativo' end);
  elsif new.unidade is distinct from old.unidade then
    -- role/ativo sao auditados (com ator) pelo guarda; aqui so a unidade, com o ator do guarda.
    v_chave := 'sisveiculos.ator_unidade_' || replace(new.id::text, '-', '');
    v_ator := nullif(current_setting(v_chave, true), '')::uuid;
    if v_ator is not null then
      perform set_config(v_chave, '', true);  -- consumido
    end if;
    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para)
    values ('perfis', new.id, 'perfil_unidade_alterada', v_ator, old.unidade::text, new.unidade::text);
  end if;
  return null;
end;
$$;

revoke all on function public.perfis_auditar() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- Baixo 3. veiculos_auditar: declaracao simples (sem "...::uuid;begin" colado)
-- ---------------------------------------------------------------------------
create or replace function public.veiculos_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_campos text[];
  v_chave  text;
  v_ator   uuid;
begin
  v_chave := 'sisveiculos.ator_veiculo_' || replace(new.id::text, '-', '');
  v_ator  := nullif(current_setting(v_chave, true), '')::uuid;
  if v_ator is not null then
    perform set_config(v_chave, '', true);  -- consumido
  end if;

  if tg_op = 'INSERT' then
    insert into public.auditoria (tabela, registro_id, acao, ator_id)
    values ('veiculos', new.id, 'veiculo_criado', v_ator);
    return null;
  end if;

  v_campos := array_remove(array[
    case when new.descricao is distinct from old.descricao then 'descricao' end,
    case when new.placa     is distinct from old.placa     then 'placa' end,
    case when new.ativo     is distinct from old.ativo     then 'ativo' end
  ], null);

  if coalesce(array_length(v_campos, 1), 0) > 0 then
    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
    values ('veiculos', new.id, 'veiculo_alterado', v_ator,
            case when new.ativo is distinct from old.ativo then old.ativo::text end,
            case when new.ativo is distinct from old.ativo then new.ativo::text end,
            jsonb_build_object('campos', to_jsonb(v_campos)));
  end if;
  return null;
end;
$$;

revoke all on function public.veiculos_auditar() from public, anon, authenticated;
