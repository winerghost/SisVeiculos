-- ============================================================================
-- LIMPEZA DOS DADOS DE TESTE (ferramenta de PRE-LANCAMENTO; NAO e migration)
-- ============================================================================
-- Rode UMA vez, no SQL Editor do Supabase (papel postgres), depois dos testes com dados
-- ficticios e ANTES do uso real. NUNCA rode depois que o sistema tiver dados reais: ela apaga
-- requisicoes, checklists, fotos de perfil, acessos de login e (por padrao) a auditoria.
-- Passo a passo em linguagem simples: supabase/ops/README.md e docs/limpeza-pre-lancamento.md.
--
-- COMO USAR
--   1. Rode este arquivo como esta: e um ENSAIO (nao apaga nada). O resultado aparece como uma
--      mensagem de erro "ENSAIO: nada foi apagado" com as contagens. Isso e normal.
--   2. Para apagar de verdade, mude SOMENTE os parametros marcados abaixo e rode de novo.
--
-- O QUE FAZ (tudo em uma unica transacao: ou apaga tudo ou nao apaga nada)
--   APAGA : checklist_fotos, checklists, requisicoes, perfil_fotos, acessos_login e, se
--           v_apagar_auditoria for true, auditoria.
--   MANTEM: perfis (nao existe DELETE; desative usuarios de teste pelo sistema), veiculos,
--           configuracoes, convites, auth.* e storage.* (arquivos dos buckets 'avatares' e
--           'checklists' sao esvaziados pelo painel; o SQL NAO apaga arquivos).
--   Para apagar, desliga SOMENTE os gatilhos NOMEADOS que bloqueiam o DELETE dessas tabelas e os
--   religa com ENABLE ALWAYS. No fim compara o estado de TODOS os gatilhos do schema public com o
--   de antes; qualquer diferenca desfaz tudo.
--
-- ANTES DE RODAR (ensaio ou real): deixe o sistema PARADO (na VPS: `docker compose stop backend`,
--   ou rode fora de horario; depois `docker compose start backend`). Nao ha LOCK TABLE: o lock
--   SHARE ROW EXCLUSIVE vem implicitamente do ALTER TABLE ... DISABLE/ENABLE TRIGGER; a partir dali
--   escritas esperam e o lock_timeout de 15 s desfaz tudo com seguranca. O ENSAIO tambem faz isso.
--   Ligue o backend de novo logo depois da execucao real e da conferencia do resultado.
-- REGISTRO: na execucao real fica UMA linha em auditoria (acao_administrativa / limpeza_pre_lancamento,
--   sem dados pessoais) com as contagens apagadas. Com v_apagar_auditoria = true somem tambem os
--   eventos admin_semeado e perfil_criado dos perfis reais; use false para preserva-los.
-- EXCECAO UNICA pre-lancamento ao "sem DELETE fisico / auditoria append-only": dados ficticios,
--   gatilhos religados e verificados.
--
-- TRAVAS: (a) se o banco estiver LACRADO (comentario 'sisveiculos:lacrado') recusa sempre;
--   (b) so apaga com v_executar = true E a frase exata de v_confirmacao E o total de linhas do
--   escopo copiado do ensaio em v_confere_total (erro LD007 se nao bater); (c) parametro NULL e
--   recusado (LD003), pois NULL contornaria as comparacoes.
-- O banco NAO conhece o "ref" do projeto Supabase, entao nao ha como o script conferir o
-- ambiente sozinho. A defesa e a frase de confirmacao, que inclui o ref: confira no endereco do
-- navegador (supabase.com/dashboard/project/<ref>) que voce esta no projeto certo ao digita-la.
-- ============================================================================

do $limpeza$
declare
  -- ======================= PARAMETROS (so mude estes) ===========================
  -- true = apaga de verdade. false (padrao) = ENSAIO: faz tudo, mostra as contagens e DESFAZ.
  v_executar         boolean := false;
  -- Para apagar de verdade escreva exatamente:  APAGAR DADOS DE TESTE ofwvoztarroglekfwohs
  v_confirmacao      text    := '';
  -- SEGUNDA CONFIRMACAO: copie do ENSAIO o TOTAL de linhas das 6 tabelas do escopo (requisicoes +
  -- checklists + checklist_fotos + perfil_fotos + acessos_login + auditoria; "COPIE ESTE NUMERO ...").
  -- Na execucao real tem de ser igual ao total ATUAL (0 so vale se o total for 0); -1 nunca e
  -- aceito. Assim um texto salvo e reaproveitado depois nao apaga nada.
  v_confere_total    int     := -1;
  -- Identificador do projeto (faz parte da frase de confirmacao; nao e conferido pelo banco).
  v_ref_esperado     text    := 'ofwvoztarroglekfwohs';
  -- true = apaga tambem a auditoria (historico de acoes de teste). false = preserva a auditoria.
  v_apagar_auditoria boolean := true;
  -- true = depois de apagar, grava o LACRE (o script nunca mais roda neste banco).
  -- Prefira false e lacre no dia do lancamento (supabase/ops/lacrar_producao.sql).
  v_lacrar           boolean := false;
  -- ===================== FIM DOS PARAMETROS (nao mude abaixo) ===================

  c_lacre       constant text := 'sisveiculos:lacrado';
  c_descartavel constant text := 'sisveiculos:descartavel';
  -- Comentario padrao do banco `postgres` no Supabase/Postgres: conta como "sem comentario proprio".
  c_padrao      constant text := 'default administrative connection database';
  v_banco       text := current_database();
  v_comentario  text;
  v_dono        oid;
  v_frase       text;

  -- Ordem do DELETE: filhas primeiro (checklist_fotos -> checklists -> requisicoes).
  -- v_gatilhos[i] e o UNICO gatilho que bloqueia o DELETE de v_tabelas[i].
  v_tabelas   text[] := array['checklist_fotos', 'checklists', 'requisicoes',
                              'perfil_fotos', 'acessos_login', 'auditoria'];
  v_gatilhos  text[] := array['trg_checklist_fotos_imutavel', 'trg_checklists_imutavel',
                              'trg_requisicoes_sem_delete', 'trg_perfil_fotos_sem_delete',
                              'trg_acessos_login_imutavel', 'trg_auditoria_append_only'];
  v_mantidas  text[] := array['perfis', 'veiculos', 'configuracoes', 'convites'];
  v_qtd       int;                 -- quantas de v_tabelas entram (auditoria e a ultima)
  v_oids      oid[];
  i           int;
  n           bigint;
  v_estado    "char";
  v_texto     text;
  v_antes     bigint[] := '{}';    -- contagem das tabelas do escopo, antes
  v_apagadas  bigint[] := '{}';    -- linhas apagadas por tabela
  v_man_antes bigint[] := '{}';    -- contagem das mantidas, antes
  v_aud_antes bigint;
  v_snap_antes  text;
  v_snap_depois text;
  v_storage   text;
  v_rel       text := '';
  v_json      jsonb;
  v_esperado  bigint;
  v_total     bigint;
begin
  -- Parametro NULL tornaria as comparacoes abaixo NULL e contornaria as travas: recusa primeiro.
  if v_executar is null or v_confirmacao is null or v_confere_total is null
     or v_apagar_auditoria is null or v_lacrar is null or v_ref_esperado is null then
    raise exception 'RECUSADO: parametro nulo (todos os parametros do topo precisam de valor). Nada foi apagado.'
      using errcode = 'LD003';
  end if;

  -- Nao espera lock para sempre (ex.: outra sessao usando o sistema).
  perform set_config('lock_timeout', '15s', true);

  -- ---- 1. Lacre e contexto -------------------------------------------------------------
  select shobj_description(d.oid, 'pg_database'), d.datdba
    into v_comentario, v_dono
    from pg_database d where d.datname = v_banco;

  if coalesce(v_comentario, '') like c_lacre || '%' then
    raise exception 'RECUSADO: este banco esta LACRADO (sisveiculos:lacrado). Esta limpeza so existe para o pre-lancamento e nao pode mais rodar aqui. Nada foi apagado.'
      using errcode = 'LD002';
  end if;

  v_frase := 'APAGAR DADOS DE TESTE ' || v_ref_esperado;
  if v_executar and v_confirmacao is distinct from v_frase then
    raise exception 'RECUSADO: v_executar esta true, mas v_confirmacao nao e exatamente a frase "%". Nada foi apagado.', v_frase
      using errcode = 'LD003';
  end if;

  if v_lacrar then
    if coalesce(v_comentario, '') = c_descartavel then
      raise exception 'RECUSADO: v_lacrar = true em banco marcado como descartavel (sisveiculos:descartavel); o marcador de teste nao e sobrescrito. Nada foi apagado.'
        using errcode = 'LD006';
    end if;
    if btrim(coalesce(v_comentario, '')) <> '' and v_comentario <> c_padrao then
      raise exception 'RECUSADO: o banco ja tem um comentario proprio ("%"); o lacre nao o sobrescreve. Use v_lacrar = false. Nada foi apagado.', left(v_comentario, 80)
        using errcode = 'LD006';
    end if;
    if not pg_has_role(current_user, v_dono, 'usage') then
      raise exception 'RECUSADO: o papel atual (%) nao e dono do banco e nao pode gravar o lacre. Use v_lacrar = false. Nada foi apagado.', current_user
        using errcode = 'LD006';
    end if;
  end if;

  -- ---- 2. Estrutura esperada -------------------------------------------------------------
  v_qtd := case when v_apagar_auditoria then 6 else 5 end;

  for i in 1 .. 6 loop
    if to_regclass('public.' || v_tabelas[i]) is null then
      raise exception 'ESTRUTURA INESPERADA: a tabela public.% nao existe (as migrations 1 a 29 foram aplicadas?). Nada foi apagado.', v_tabelas[i]
        using errcode = 'LD004';
    end if;
  end loop;
  foreach v_texto in array v_mantidas loop
    if to_regclass('public.' || v_texto) is null then
      raise exception 'ESTRUTURA INESPERADA: a tabela public.% nao existe. Nada foi apagado.', v_texto
        using errcode = 'LD004';
    end if;
  end loop;

  select array_agg(to_regclass('public.' || v_tabelas[g])::oid)
    into v_oids from generate_series(1, v_qtd) g;

  -- O papel atual precisa ser dono das tabelas do escopo (ALTER TABLE ... TRIGGER exige).
  for i in 1 .. v_qtd loop
    if not pg_has_role(current_user, (select relowner from pg_class where oid = v_oids[i]), 'usage') then
      raise exception 'SEM PERMISSAO: o papel atual (%) nao e dono de public.%. Rode como postgres. Nada foi apagado.', current_user, v_tabelas[i]
        using errcode = 'LD004';
    end if;
  end loop;

  -- Cada gatilho a desligar deve existir e estar em ENABLE ALWAYS ('A').
  for i in 1 .. v_qtd loop
    select t.tgenabled into v_estado
      from pg_trigger t
     where t.tgrelid = v_oids[i] and t.tgname = v_gatilhos[i] and not t.tgisinternal;
    if v_estado is null then
      raise exception 'ESTRUTURA INESPERADA: gatilho % nao encontrado em public.%. Nada foi apagado.', v_gatilhos[i], v_tabelas[i]
        using errcode = 'LD004';
    elsif v_estado <> 'A' then
      raise exception 'ESTRUTURA INESPERADA: gatilho % de public.% nao esta em ENABLE ALWAYS (estado %). Nada foi apagado.', v_gatilhos[i], v_tabelas[i], v_estado
        using errcode = 'LD004';
    end if;
  end loop;

  -- Nenhum OUTRO gatilho de DELETE (ex.: auditoria de delete) nas tabelas do escopo.
  select string_agg(c.relname || '.' || t.tgname, ', ') into v_texto
    from pg_trigger t join pg_class c on c.oid = t.tgrelid
   where t.tgrelid = any (v_oids) and not t.tgisinternal
     and (t.tgtype & 8) <> 0 and t.tgenabled <> 'D'
     and t.tgname <> all (v_gatilhos[1:v_qtd]);
  if v_texto is not null then
    raise exception 'ESTRUTURA INESPERADA: ha gatilhos de DELETE nao previstos (%). Nada foi apagado.', v_texto
      using errcode = 'LD004';
  end if;

  -- Nenhuma tabela fora do escopo pode depender (FK) das que serao apagadas.
  select string_agg(distinct k.conrelid::regclass::text || ' -> ' || k.confrelid::regclass::text, '; ')
    into v_texto
    from pg_constraint k
   where k.contype = 'f' and k.confrelid = any (v_oids) and not (k.conrelid = any (v_oids));
  if v_texto is not null then
    raise exception 'ESTRUTURA INESPERADA: outras tabelas referenciam as que seriam apagadas (%). Nada foi apagado.', v_texto
      using errcode = 'LD004';
  end if;

  -- Segunda confirmacao: TOTAL de linhas das 6 tabelas do escopo (independe de v_apagar_auditoria).
  v_total := 0;
  for i in 1 .. 6 loop
    execute format('select count(*) from public.%I', v_tabelas[i]) into n;
    v_total := v_total + n;
  end loop;
  if v_executar and (v_confere_total < 0 or v_confere_total <> v_total) then
    raise exception 'RECUSADO: o numero digitado em v_confere_total (%) nao bate com o total atual de linhas do escopo: refaca o ensaio e copie o numero. Nada foi apagado.', v_confere_total
      using errcode = 'LD007';
  end if;

  -- ---- 3. Fotografia do ANTES -----------------------------------------------------------
  select coalesce(string_agg(c.relname || '.' || t.tgname || '=' || t.tgenabled::text, E'\n'
                             order by c.relname, t.tgname), '')
    into v_snap_antes
    from pg_trigger t
    join pg_class c on c.oid = t.tgrelid
    join pg_namespace s on s.oid = c.relnamespace
   where s.nspname = 'public';

  for i in 1 .. v_qtd loop
    execute format('select count(*) from public.%I', v_tabelas[i]) into n;
    v_antes := v_antes || n;
  end loop;
  foreach v_texto in array v_mantidas loop
    execute format('select count(*) from public.%I', v_texto) into n;
    v_man_antes := v_man_antes || n;
  end loop;
  select count(*) into v_aud_antes from public.auditoria;

  -- Arquivos nos buckets (so informativo; o SQL nao os apaga).
  begin
    if to_regclass('storage.objects') is not null then
      execute $q$select coalesce(string_agg(bucket_id || ': ' || qtd, ', ' order by bucket_id), 'nenhum arquivo')
                   from (select bucket_id, count(*) as qtd from storage.objects
                          where bucket_id in ('avatares', 'checklists') group by bucket_id) s$q$
        into v_storage;
    else
      v_storage := 'schema storage ausente';
    end if;
  exception when others then
    v_storage := 'nao foi possivel consultar';
  end;

  -- ---- 4. Desliga SO os gatilhos nomeados, apaga (filhas primeiro) e religa ---------------
  for i in 1 .. v_qtd loop
    execute format('alter table public.%I disable trigger %I', v_tabelas[i], v_gatilhos[i]);
  end loop;

  for i in 1 .. v_qtd loop
    execute format('delete from public.%I where true', v_tabelas[i]);
    get diagnostics n = row_count;
    v_apagadas := v_apagadas || n;
  end loop;

  -- >>> RELIGAR (nao mude esta linha de comentario: os testes dependem dela)
  for i in 1 .. v_qtd loop
    execute format('alter table public.%I enable always trigger %I', v_tabelas[i], v_gatilhos[i]);
  end loop;

  -- ---- 5. Verificacoes (qualquer falha desfaz TUDO) ---------------------------------------
  select coalesce(string_agg(c.relname || '.' || t.tgname || '=' || t.tgenabled::text, E'\n'
                             order by c.relname, t.tgname), '')
    into v_snap_depois
    from pg_trigger t
    join pg_class c on c.oid = t.tgrelid
    join pg_namespace s on s.oid = c.relnamespace
   where s.nspname = 'public';
  if v_snap_depois is distinct from v_snap_antes then
    raise exception 'VERIFICACAO FALHOU: o estado dos gatilhos do schema public mudou. Tudo foi desfeito.'
      using errcode = 'LD005';
  end if;

  for i in 1 .. v_qtd loop
    execute format('select count(*) from public.%I', v_tabelas[i]) into n;
    if n <> 0 then
      raise exception 'VERIFICACAO FALHOU: public.% ainda tem % linha(s). Tudo foi desfeito.', v_tabelas[i], n
        using errcode = 'LD005';
    end if;
  end loop;

  for i in 1 .. array_length(v_mantidas, 1) loop
    execute format('select count(*) from public.%I', v_mantidas[i]) into n;
    if n <> v_man_antes[i] then
      raise exception 'VERIFICACAO FALHOU: public.% mudou de % para % linha(s). Tudo foi desfeito.', v_mantidas[i], v_man_antes[i], n
        using errcode = 'LD005';
    end if;
  end loop;

  if not v_apagar_auditoria then
    select count(*) into n from public.auditoria;
    if n <> v_aud_antes then
      raise exception 'VERIFICACAO FALHOU: a auditoria mudou de % para % linha(s). Tudo foi desfeito.', v_aud_antes, n
        using errcode = 'LD005';
    end if;
  end if;

  -- ---- 6. Registro DURAVEL da limpeza (1 linha em auditoria, sem dados pessoais) -------------
  -- Feito com os gatilhos ja religados e verificados (o append-only so bloqueia UPDATE/DELETE).
  -- Usa tabela='perfis' e ator_id nulo porque os CHECKs ck_auditoria_tabela/ck_auditoria_acao so aceitam
  -- os valores ja existentes (nao ha acao/tabela propria para esta limpeza); registro_id tambem nulo.
  v_json := (select jsonb_object_agg(v_tabelas[g], v_apagadas[g]) from generate_series(1, v_qtd) g);
  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, detalhes)
  values ('perfis', null, 'acao_administrativa', null, 'limpeza_pre_lancamento',
          jsonb_build_object('apagadas', v_json, 'auditoria_apagada', v_apagar_auditoria, 'lacrado', v_lacrar));
  select count(*) into n from public.auditoria;
  v_esperado := (case when v_apagar_auditoria then 0 else v_aud_antes end) + 1;
  if n <> v_esperado then
    raise exception 'VERIFICACAO FALHOU: a auditoria deveria ter % linha(s) e tem %. Tudo foi desfeito.', v_esperado, n
      using errcode = 'LD005';
  end if;

  -- ---- 7. Lacre (opcional), ANTES do raise do ensaio: o rollback do ensaio o desfaz, mas
  --         assim o ensaio prova que o papel consegue comentar o banco ---------------------------
  if v_lacrar then
    execute format('comment on database %I is %L', v_banco, c_lacre);
    perform set_config('sisveiculos.comentario_anterior', coalesce(v_comentario, ''), true);
  end if;

  -- ---- 8. Relatorio -----------------------------------------------------------------------
  v_rel := rpad('TABELA', 18) || lpad('ANTES', 8) || lpad('APAGADAS', 10) || E'\n';
  for i in 1 .. v_qtd loop
    v_rel := v_rel || rpad(v_tabelas[i], 18) || lpad(v_antes[i]::text, 8) || lpad(v_apagadas[i]::text, 10) || E'\n';
  end loop;
  if not v_apagar_auditoria then
    v_rel := v_rel || rpad('auditoria', 18) || lpad(v_aud_antes::text, 8) || lpad('preservada', 10) || E'\n';
  end if;
  v_rel := v_rel || E'\nMANTIDAS (nao mudam): ';
  for i in 1 .. array_length(v_mantidas, 1) loop
    v_rel := v_rel || v_mantidas[i] || '=' || v_man_antes[i] || case when i < array_length(v_mantidas, 1) then ', ' else '' end;
  end loop;
  v_rel := v_rel || E'\nAuditoria depois da limpeza: '
                 || case when v_apagar_auditoria then '1 linha (o registro da limpeza)'
                         else (v_aud_antes + 1)::text || ' linhas (as ' || v_aud_antes || ' de antes + o registro da limpeza)' end
                 || E'\nGatilhos do schema public: identicos antes e depois (todos verificados).'
                 || E'\nArquivos nos buckets (o SQL NAO apaga; esvazie pelo painel; sem isso ficam arquivos com dado pessoal): ' || v_storage;
  if v_lacrar then
    v_rel := v_rel || E'\nLACRE ' || case when v_executar then 'gravado' else '(simulado no ensaio; nao gravado)' end
                   || ': este banco nao aceita mais esta limpeza. Comentario anterior substituido: '
                   || case when coalesce(v_comentario, '') = '' then '(vazio)' else '"' || v_comentario || '"' end
                   || E'\nPara desfazer (decisao consciente do dono do banco): comment on database ' || v_banco
                   || ' is ' || quote_literal(case when coalesce(v_comentario, '') = '' then c_padrao else v_comentario end) || ';';
  end if;

  if not v_executar then
    -- ENSAIO: o proprio erro desfaz tudo o que foi feito acima (inclusive o registro e o lacre).
    raise exception E'ENSAIO: nada foi apagado.\n\n*** COPIE ESTE NUMERO PARA v_confere_total: % ***\n\n%\n\nCopie ou fotografe esta mensagem ANTES de executar. Esta mensagem de erro e o resultado esperado do ensaio. Para apagar de verdade mude v_executar, v_confirmacao e v_confere_total (veja o topo do arquivo).', v_total, v_rel
      using errcode = 'LD001';
  end if;

  -- Guarda as contagens apagadas para a consulta final (o Studio pode nao exibir NOTICE).
  perform set_config('sisveiculos.apagadas', v_json::text, true);
  raise notice E'LIMPEZA CONCLUIDA.\n%', v_rel;
end
$limpeza$;

-- Resumo FINAL (so aparece se o bloco acima terminou sem erro). Depois da limpeza real, as
-- tabelas apagadas mostram 0 linhas (auditoria: 1, o registro da limpeza) e a coluna
-- apagadas_agora mostra quantas linhas foram apagadas. COPIE/FOTOGRAFE este resultado.
select r.tabela, r.linhas, a.j ->> r.tabela as apagadas_agora, r.situacao
from (
  select 1 as o, 'requisicoes' as tabela, count(*) as linhas, 'apagada' as situacao from public.requisicoes
  union all select 2, 'checklists', count(*), 'apagada' from public.checklists
  union all select 3, 'checklist_fotos', count(*), 'apagada' from public.checklist_fotos
  union all select 4, 'perfil_fotos', count(*), 'apagada' from public.perfil_fotos
  union all select 5, 'acessos_login', count(*), 'apagada' from public.acessos_login
  union all select 6, 'auditoria', count(*), 'apagada (se v_apagar_auditoria = true); resta o registro da limpeza' from public.auditoria
  union all select 7, 'perfis', count(*), 'mantida' from public.perfis
  union all select 8, 'veiculos', count(*), 'mantida' from public.veiculos
  union all select 9, 'configuracoes', count(*), 'mantida' from public.configuracoes
  union all select 10, 'convites', count(*), 'mantida' from public.convites
  union all select 11, 'LACRE do banco', 0, coalesce(nullif(shobj_description(
      (select oid from pg_database where datname = current_database()), 'pg_database'), ''), '(sem lacre)')
  union all select 12, 'Comentario anterior (so se lacrou agora)', 0,
      coalesce(nullif(current_setting('sisveiculos.comentario_anterior', true), ''), '-')
) r
cross join (select nullif(current_setting('sisveiculos.apagadas', true), '')::jsonb as j) a
order by r.o;
