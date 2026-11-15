-- ============================================================================
-- LIMPEZA DOS DADOS DE TESTE (ferramenta de PRE-LANCAMENTO; NAO e migration)
-- ============================================================================
-- Rode UMA vez, no SQL Editor do Supabase (papel postgres), depois dos testes com dados
-- ficticios e ANTES do uso real. NUNCA rode depois que o sistema tiver dados reais.
-- Passo a passo em linguagem simples: supabase/ops/README.md e docs/limpeza-pre-lancamento.md.
--
-- COMO USAR
--   1. Rode este arquivo como esta: e um ENSAIO (nao apaga nada). O resultado aparece como uma
--      mensagem de erro "ENSAIO: nada foi apagado" com as contagens. Isso e normal.
--   2. Para apagar de verdade, mude SOMENTE os parametros marcados abaixo e rode de novo, no MESMO
--      modo do ensaio (v_manter_usuarios e v_apagar_auditoria iguais): o total muda com o modo.
--
-- CLASSIFICACAO DAS 21 TABELAS DE public (tabela fora desta lista => recusa LD004)
--   SEMPRE APAGADAS (dado operacional): checklist_fotos, checklists, notificacoes, localizacoes_viagem,
--     requisicoes_arquivadas, requisicoes, acessos_login, envios_email, ia_uso, desafios_webauthn,
--     reautenticacoes_webauthn.
--   AUDITORIA: apagada por padrao (v_apagar_auditoria = true); fica so a linha desta limpeza
--     (acao_administrativa / limpeza_pre_lancamento, so contagens, sem dados pessoais).
--   CONTAS (dependem de v_manter_usuarios): perfis, convites, perfil_fotos, push_inscricoes,
--     credenciais_webauthn e auth.users.
--     true (padrao): tudo mantido.
--     false: apaga as contas que NAO sao admin ativo (perfis + auth.users; o restante de auth.* sai pelo
--       cascade do proprio Auth), as perfil_fotos, push_inscricoes e credenciais_webauthn dessas contas e
--       TODOS os convites. Admins ativos ficam com perfil, fotos, push e passkeys.
--   SEMPRE MANTIDAS: veiculos (inclusive o km atual), configuracoes, ia_configuracoes, saudacoes.
--   storage.*: o SQL so CONTA os arquivos por bucket; quem apaga e app.ops.limpar_storage (DEPOIS deste).
--
-- COMO APAGA (tudo em uma unica transacao: ou apaga tudo ou nao apaga nada)
--   Desliga SOMENTE os gatilhos NOMEADOS que bloqueiam o DELETE das tabelas do modo escolhido e os
--   religa no MESMO modo de antes (ENABLE ALWAYS ou ENABLE padrao). No fim compara o estado de TODOS
--   os gatilhos do schema public com o de antes; qualquer diferenca desfaz tudo. Os gatilhos de
--   TRUNCATE (trg_*_sem_truncate) nunca sao tocados, nem as funcoes de expurgo e seus pisos de idade.
--   requisicoes_arquivadas nao tem gatilho de DELETE (desarquivar e DELETE por desenho).
--   No modo false, antes de apagar perfis confere se alguma linha que FICA (ex.: auditoria preservada,
--   saudacoes, foto de admin) aponta para conta a apagar: se houver, recusa (LD009) sem apagar nada.
--   PRE-REQUISITO: todas as migrations aplicadas (db push), inclusive 20261127120000 (km_percorrido) e
--   20261128120000/120100/120200 (telefone). Sem elas o script recusa (LD004).
--
-- ANTES DE RODAR (ensaio ou real): deixe o sistema PARADO (na VPS: `docker compose stop backend`;
--   depois `docker compose start backend`). Nao ha LOCK TABLE: o lock vem do ALTER TABLE ...
--   DISABLE/ENABLE TRIGGER; a partir dali escritas esperam e o lock_timeout de 15 s desfaz tudo.
-- REGISTRO: na execucao real fica UMA linha em auditoria (acao_administrativa / limpeza_pre_lancamento,
--   ator nulo, sem dados pessoais) com as contagens apagadas e o modo usado.
-- EXCECAO UNICA pre-lancamento ao "sem DELETE fisico / auditoria append-only": dados ficticios,
--   gatilhos religados e verificados.
--
-- TRAVAS: (a) banco LACRADO (comentario 'sisveiculos:lacrado') recusa sempre (LD002);
--   (b) so apaga com v_executar = true E a frase exata de v_confirmacao (LD003) E o total de linhas do
--   modo copiado do ensaio em v_confere_total (LD007); (c) parametro NULL e recusado (LD003);
--   (d) sem nenhum admin ativo recusa (LD008); (e) estrutura inesperada recusa (LD004);
--   (f) verificacao final falhou desfaz tudo (LD005); (g) lacre recusado (LD006);
--   (h) linha mantida aponta para conta a apagar (LD009).
-- O banco NAO conhece o "ref" do projeto Supabase: a defesa e a frase de confirmacao, que inclui o ref.
-- Confira no endereco do navegador (supabase.com/dashboard/project/<ref>) que voce esta no projeto certo.
-- ============================================================================

do $limpeza$
declare
  -- ======================= PARAMETROS (so mude estes) ===========================
  -- true = apaga de verdade. false (padrao) = ENSAIO: faz tudo, mostra as contagens e DESFAZ.
  v_executar         boolean := false;
  -- Frase exata: APAGAR DADOS DE TESTE ofwvoztarroglekfwohs
  --   + " E AUDITORIA" se v_apagar_auditoria = true (padrao)
  --   + " E USUARIOS"  se v_manter_usuarios = false
  -- Padrao (mantem usuarios, apaga auditoria): APAGAR DADOS DE TESTE ofwvoztarroglekfwohs E AUDITORIA
  -- So admins (e apaga auditoria):            APAGAR DADOS DE TESTE ofwvoztarroglekfwohs E AUDITORIA E USUARIOS
  v_confirmacao      text    := '';
  -- SEGUNDA CONFIRMACAO: copie do ENSAIO (feito no MESMO modo) o numero "COPIE ESTE NUMERO ...".
  -- Tem de ser igual ao total ATUAL de linhas que o modo apaga (0 so vale se o total for 0); -1 nunca
  -- e aceito. Assim um texto salvo e reaproveitado depois (ou em outro modo) nao apaga nada.
  v_confere_total    int     := -1;
  -- Identificador do projeto (faz parte da frase de confirmacao; nao e conferido pelo banco).
  v_ref_esperado     text    := 'ofwvoztarroglekfwohs';
  -- true (padrao) = mantem todas as contas (perfis, auth.users, convites, fotos, push, passkeys).
  -- false = apaga as contas que NAO sao admin ativo e todos os convites; EXIGE " E USUARIOS" na frase.
  v_manter_usuarios  boolean := true;
  -- true (padrao) = apaga a auditoria (historico de testes); fica so o registro desta limpeza.
  -- false = preserva a auditoria inteira (append-only). No modo false de usuarios, a auditoria
  -- preservada costuma apontar para as contas a apagar e a limpeza recusa (LD009).
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

  -- Entradas apagaveis, na ORDEM do DELETE (filhas primeiro). Arrays paralelos:
  --   v_esquemas/v_tabelas: tabela; v_gatilhos: UNICO gatilho de DELETE ('' = nao tem);
  --   v_filtros: '' = apaga todas as linhas; nome de coluna = apaga as linhas cuja coluna NAO e um
  --   admin ativo; v_grupos: 'sempre' | 'auditoria' (v_apagar_auditoria) | 'contas' (v_manter_usuarios = false).
  -- auditoria vem antes de perfis (auditoria.ator_id -> perfis); perfis antes de auth.users (perfis.id -> auth.users).
  v_esquemas  text[] := array['public', 'public', 'public', 'public', 'public', 'public', 'public', 'public',
                              'public', 'public', 'public', 'public', 'public', 'public', 'public', 'public',
                              'public', 'auth'];
  v_tabelas   text[] := array['checklist_fotos', 'checklists', 'notificacoes', 'localizacoes_viagem',
                              'requisicoes_arquivadas', 'requisicoes', 'acessos_login', 'envios_email',
                              'ia_uso', 'desafios_webauthn', 'reautenticacoes_webauthn',
                              'convites', 'perfil_fotos', 'push_inscricoes', 'credenciais_webauthn',
                              'auditoria', 'perfis', 'users'];
  v_gatilhos  text[] := array['trg_checklist_fotos_imutavel', 'trg_checklists_imutavel',
                              'trg_notificacoes_proteger', 'trg_localizacoes_viagem_imutavel',
                              '', 'trg_requisicoes_sem_delete', 'trg_acessos_login_imutavel',
                              'trg_envios_email_sem_delete', 'trg_ia_uso_imutavel',
                              'trg_desafios_webauthn_proteger', 'trg_reautenticacoes_webauthn_proteger',
                              'trg_convites_sem_delete', 'trg_perfil_fotos_sem_delete',
                              'trg_push_inscricoes_proteger', 'trg_credenciais_webauthn_proteger',
                              'trg_auditoria_append_only', 'trg_perfis_sem_delete', ''];
  v_filtros   text[] := array['', '', '', '', '', '', '', '', '', '', '',
                              '', 'usuario_id', 'usuario_id', 'usuario_id', '', 'id', 'id'];
  v_grupos    text[] := array['sempre', 'sempre', 'sempre', 'sempre', 'sempre', 'sempre', 'sempre', 'sempre',
                              'sempre', 'sempre', 'sempre', 'contas', 'contas', 'contas', 'contas',
                              'auditoria', 'contas', 'contas'];
  v_mantidas  text[] := array['veiculos', 'configuracoes', 'ia_configuracoes', 'saudacoes'];
  v_n         constant int := 18;  -- tamanho dos arrays paralelos acima
  i_perfis    constant int := 17;  -- posicao de perfis (checagem LD009 antes do DELETE dela)

  v_ativa     boolean[] := '{}';   -- entra no modo escolhido?
  v_rel_oids  oid[] := '{}';
  v_modos     text[] := '{}';      -- modo original ('A' ou 'O') do gatilho de cada entrada ('' sem gatilho)
  v_admins    uuid[];              -- admins ativos (ficam sempre)
  v_nome      text[] := '{}';      -- nome exibido (auth.users ou so o nome da tabela de public)
  v_antes     bigint[] := '{}';    -- linhas antes, por entrada
  v_alvo      bigint[] := '{}';    -- linhas que o modo "so admins" apagaria, por entrada
  v_apagadas  bigint[] := '{}';    -- linhas apagadas agora (0 nas inativas)
  v_man_antes bigint[] := '{}';    -- contagem das sempre mantidas, antes
  v_cond      text;
  v_oids_tot  oid[];               -- tabelas apagadas POR INTEIRO no modo
  i           int;
  n           bigint;
  v_estado    "char";
  v_texto     text;
  v_snap_antes  text;
  v_snap_depois text;
  v_storage   text;
  v_rel       text := '';
  v_json      jsonb := '{}';
  v_esperado  bigint;
  v_total     bigint;
  v_aud_antes bigint;
begin
  -- Parametro NULL tornaria as comparacoes abaixo NULL e contornaria as travas: recusa primeiro.
  if v_executar is null or v_confirmacao is null or v_confere_total is null or v_manter_usuarios is null
     or v_apagar_auditoria is null or v_lacrar is null or v_ref_esperado is null then
    raise exception 'RECUSADO: parametro nulo (todos os parametros do topo precisam de valor). Nada foi apagado.'
      using errcode = 'LD003';
  end if;

  -- Nao espera lock para sempre (ex.: outra sessao usando o sistema).
  perform set_config('lock_timeout', '15s', true);

  -- ---- 1. Lacre e frase -----------------------------------------------------------------
  select shobj_description(d.oid, 'pg_database'), d.datdba
    into v_comentario, v_dono
    from pg_database d where d.datname = v_banco;

  if coalesce(v_comentario, '') like c_lacre || '%' then
    raise exception 'RECUSADO: este banco esta LACRADO (sisveiculos:lacrado). Esta limpeza so existe para o pre-lancamento e nao pode mais rodar aqui. Nada foi apagado.'
      using errcode = 'LD002';
  end if;

  v_frase := 'APAGAR DADOS DE TESTE ' || v_ref_esperado
             || case when v_apagar_auditoria then ' E AUDITORIA' else '' end
             || case when v_manter_usuarios then '' else ' E USUARIOS' end;
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
  -- auth.users e sempre contada (relatorio dos dois modos): sem acesso ao schema auth, to_regclass
  -- falharia com erro generico; recusa antes com mensagem clara.
  if not exists (select 1 from pg_namespace where nspname = 'auth') then
    raise exception 'ESTRUTURA INESPERADA: o schema auth nao existe (isto e um projeto Supabase?). Nada foi apagado.'
      using errcode = 'LD004';
  end if;
  if not has_schema_privilege('auth', 'usage') then
    raise exception 'SEM PERMISSAO: o papel atual (%) nao acessa o schema auth. Rode como postgres. Nada foi apagado.', current_user
      using errcode = 'LD004';
  end if;
  for i in 1 .. v_n loop
    if to_regclass(format('%I.%I', v_esquemas[i], v_tabelas[i])) is null then
      raise exception 'ESTRUTURA INESPERADA: a tabela %.% nao existe (todas as migrations foram aplicadas?). Nada foi apagado.', v_esquemas[i], v_tabelas[i]
        using errcode = 'LD004';
    end if;
    v_rel_oids := v_rel_oids || to_regclass(format('%I.%I', v_esquemas[i], v_tabelas[i]))::oid;
    v_nome := v_nome || case when v_esquemas[i] = 'public' then v_tabelas[i] else v_esquemas[i] || '.' || v_tabelas[i] end;
    v_ativa := v_ativa || case v_grupos[i] when 'sempre' then true
                                           when 'auditoria' then v_apagar_auditoria
                                           else not v_manter_usuarios end;
  end loop;
  foreach v_texto in array v_mantidas loop
    if to_regclass('public.' || v_texto) is null then
      raise exception 'ESTRUTURA INESPERADA: a tabela public.% nao existe. Nada foi apagado.', v_texto
        using errcode = 'LD004';
    end if;
  end loop;

  -- Toda tabela de public precisa estar classificada (apagar/manter). Tabela nova => decidir antes.
  select string_agg(c.relname, ', ' order by c.relname) into v_texto
    from pg_class c join pg_namespace s on s.oid = c.relnamespace
   where s.nspname = 'public' and c.relkind in ('r', 'p') and not c.relispartition
     and c.relname <> all (v_tabelas[1:v_n - 1] || v_mantidas);
  if v_texto is not null then
    raise exception 'ESTRUTURA INESPERADA: tabela(s) de public nao classificada(s) nesta limpeza (%). Atualize o script antes. Nada foi apagado.', v_texto
      using errcode = 'LD004';
  end if;

  -- Migrations que precisam estar aplicadas ANTES (marcas que cada uma deixa no banco):
  --   20261114120000 'localizacao_expurgada' em ck_auditoria_acao (e 'acao_administrativa', usada no registro);
  --   20261127120000 requisicoes.km_percorrido; 20261128120000 perfis.telefone + 'perfil_telefone_alterado';
  --   20261128120100 convites.telefone; 20261128120200 convites_auditar com 'telefone_informado'.
  select pg_get_constraintdef(k.oid) into v_texto from pg_constraint k
   where k.conrelid = 'public.auditoria'::regclass and k.conname = 'ck_auditoria_acao';
  if v_texto is null or position('''localizacao_expurgada''' in v_texto) = 0
     or position('''acao_administrativa''' in v_texto) = 0 then
    raise exception 'ESTRUTURA INESPERADA: a migration 20261114120000 nao foi aplicada (ck_auditoria_acao sem localizacao_expurgada). Rode o db push antes. Nada foi apagado.'
      using errcode = 'LD004';
  end if;
  if position('''perfil_telefone_alterado''' in v_texto) = 0
     or not exists (select 1 from pg_attribute where attrelid = 'public.perfis'::regclass and attname = 'telefone' and not attisdropped) then
    raise exception 'ESTRUTURA INESPERADA: a migration 20261128120000 (telefone em perfis) nao foi aplicada. Rode o db push antes. Nada foi apagado.'
      using errcode = 'LD004';
  end if;
  if not exists (select 1 from pg_attribute where attrelid = 'public.requisicoes'::regclass and attname = 'km_percorrido' and not attisdropped) then
    raise exception 'ESTRUTURA INESPERADA: a migration 20261127120000 (km_percorrido) nao foi aplicada. Rode o db push antes. Nada foi apagado.'
      using errcode = 'LD004';
  end if;
  if not exists (select 1 from pg_attribute where attrelid = 'public.convites'::regclass and attname = 'telefone' and not attisdropped) then
    raise exception 'ESTRUTURA INESPERADA: a migration 20261128120100 (telefone em convites) nao foi aplicada. Rode o db push antes. Nada foi apagado.'
      using errcode = 'LD004';
  end if;
  if not exists (select 1 from pg_proc p join pg_namespace s on s.oid = p.pronamespace
                  where s.nspname = 'public' and p.proname = 'convites_auditar'
                    and position('telefone_informado' in p.prosrc) > 0) then
    raise exception 'ESTRUTURA INESPERADA: a migration 20261128120200 (expurgo do telefone do convite) nao foi aplicada. Rode o db push antes. Nada foi apagado.'
      using errcode = 'LD004';
  end if;

  -- Permissao: dono das tabelas de public do modo (ALTER TABLE ... TRIGGER exige); DELETE em auth.users.
  for i in 1 .. v_n loop
    continue when not v_ativa[i];
    if v_esquemas[i] = 'public' then
      if not pg_has_role(current_user, (select relowner from pg_class where oid = v_rel_oids[i]), 'usage') then
        raise exception 'SEM PERMISSAO: o papel atual (%) nao e dono de public.%. Rode como postgres. Nada foi apagado.', current_user, v_tabelas[i]
          using errcode = 'LD004';
      end if;
    elsif not has_table_privilege(v_rel_oids[i], 'delete') then
      raise exception 'SEM PERMISSAO: o papel atual (%) nao pode apagar de %. Rode como postgres. Nada foi apagado.', current_user, v_nome[i]
        using errcode = 'LD004';
    end if;
  end loop;

  -- Cada gatilho a desligar deve existir e estar LIGADO: 'A' (ENABLE ALWAYS) ou 'O' (ENABLE padrao).
  -- Desligado ('D') ou so em replica ('R') e recusado. O modo original e restaurado igual no fim.
  for i in 1 .. v_n loop
    if not v_ativa[i] or v_gatilhos[i] = '' then
      v_modos := v_modos || ''::text;
      continue;
    end if;
    v_estado := null;
    select t.tgenabled into v_estado
      from pg_trigger t
     where t.tgrelid = v_rel_oids[i] and t.tgname = v_gatilhos[i] and not t.tgisinternal;
    if v_estado is null then
      raise exception 'ESTRUTURA INESPERADA: gatilho % nao encontrado em %. Nada foi apagado.', v_gatilhos[i], v_nome[i]
        using errcode = 'LD004';
    elsif v_estado not in ('A', 'O') then
      raise exception 'ESTRUTURA INESPERADA: gatilho % de % nao esta ligado (estado %). Nada foi apagado.', v_gatilhos[i], v_nome[i], v_estado
        using errcode = 'LD004';
    end if;
    v_modos := v_modos || v_estado::text;
  end loop;

  -- Nenhum OUTRO gatilho de DELETE (ex.: auditoria de delete) nas tabelas do modo.
  select string_agg(c.relname || '.' || t.tgname, ', ') into v_texto
    from pg_trigger t join pg_class c on c.oid = t.tgrelid
   where t.tgrelid = any (array(select v_rel_oids[g] from generate_series(1, v_n) g where v_ativa[g]))
     and not t.tgisinternal and (t.tgtype & 8) <> 0 and t.tgenabled <> 'D'
     and t.tgname <> all (v_gatilhos);
  if v_texto is not null then
    raise exception 'ESTRUTURA INESPERADA: ha gatilhos de DELETE nao previstos (%). Nada foi apagado.', v_texto
      using errcode = 'LD004';
  end if;

  -- FKs: nenhuma tabela fora das apagadas POR INTEIRO pode depender delas; nada pode depender das
  -- apagadas em parte (fotos, push, passkeys), exceto perfis (LD009 abaixo) e auth.users (so o cascade
  -- do Auth e public.perfis).
  v_oids_tot := array(select v_rel_oids[g] from generate_series(1, v_n) g where v_ativa[g] and v_filtros[g] = '');
  select string_agg(distinct k.conrelid::regclass::text || ' -> ' || k.confrelid::regclass::text, '; ')
    into v_texto
    from pg_constraint k
   where k.contype = 'f'
     and ((k.confrelid = any (v_oids_tot) and not (k.conrelid = any (v_oids_tot)))
          or (k.confrelid = any (array(select v_rel_oids[g] from generate_series(1, v_n) g
                                         where v_ativa[g] and v_filtros[g] <> '' and g < i_perfis)))
          or (not v_manter_usuarios and k.confrelid = 'auth.users'::regclass
              and k.conrelid <> 'public.perfis'::regclass and k.confdeltype not in ('c', 'n')));
  if v_texto is not null then
    raise exception 'ESTRUTURA INESPERADA: outras tabelas referenciam as que seriam apagadas (%). Nada foi apagado.', v_texto
      using errcode = 'LD004';
  end if;

  -- ---- 3. Admins ativos (ficam sempre) e contagens ------------------------------------------
  select coalesce(array_agg(p.id order by p.id), '{}') into v_admins
    from public.perfis p where p.role = 'admin' and p.ativo;
  if cardinality(v_admins) = 0 then
    raise exception 'RECUSADO: nao ha nenhum admin ativo. Cadastre/reative um admin antes da limpeza. Nada foi apagado.'
      using errcode = 'LD008';
  end if;

  v_total := 0;
  for i in 1 .. v_n loop
    execute format('select count(*) from %I.%I', v_esquemas[i], v_tabelas[i]) into n;
    v_antes := v_antes || n;
    if v_filtros[i] <> '' then
      execute format('select count(*) from %I.%I where not (%I = any ($1))', v_esquemas[i], v_tabelas[i], v_filtros[i])
        into n using v_admins;
    end if;
    v_alvo := v_alvo || n;
    if v_ativa[i] then
      v_total := v_total + n;
    end if;
  end loop;
  -- Segunda confirmacao: TOTAL de linhas que ESTE modo apaga.
  if v_executar and (v_confere_total < 0 or v_confere_total <> v_total) then
    raise exception 'RECUSADO: o numero digitado em v_confere_total (%) nao bate com o total atual de linhas deste modo: refaca o ensaio no mesmo modo e copie o numero. Nada foi apagado.', v_confere_total
      using errcode = 'LD007';
  end if;

  -- ---- 4. Fotografia do ANTES -----------------------------------------------------------
  select coalesce(string_agg(c.relname || '.' || t.tgname || '=' || t.tgenabled::text, E'\n'
                             order by c.relname, t.tgname), '')
    into v_snap_antes
    from pg_trigger t
    join pg_class c on c.oid = t.tgrelid
    join pg_namespace s on s.oid = c.relnamespace
   where s.nspname = 'public';

  foreach v_texto in array v_mantidas loop
    execute format('select count(*) from public.%I', v_texto) into n;
    v_man_antes := v_man_antes || n;
  end loop;
  v_aud_antes := v_antes[16];

  -- Arquivos por bucket (so leitura; o SQL nao os apaga; so nome do bucket e contagem).
  begin
    if to_regclass('storage.objects') is not null then
      execute $q$select coalesce(string_agg(bucket_id || ': ' || qtd, ', ' order by bucket_id), 'nenhum arquivo')
                   from (select bucket_id, count(*) as qtd from storage.objects group by bucket_id) s$q$
        into v_storage;
    else
      v_storage := 'schema storage ausente';
    end if;
  exception when others then
    v_storage := 'nao foi possivel consultar';
  end;

  -- ---- 5. Desliga SO os gatilhos nomeados, apaga (filhas primeiro) e religa ---------------
  for i in 1 .. v_n loop
    if v_modos[i] <> '' then
      execute format('alter table %I.%I disable trigger %I', v_esquemas[i], v_tabelas[i], v_gatilhos[i]);
    end if;
  end loop;

  for i in 1 .. v_n loop
    if not v_ativa[i] then
      v_apagadas := v_apagadas || 0::bigint;
      continue;
    end if;
    if i = i_perfis then
      -- LD009: alguma linha que FICA aponta (FK) para conta a apagar? So contagens na mensagem.
      v_texto := null;
      for v_cond in
        select format('select count(*) from %s where %I is not null and not (%I = any ($1))',
                      k.conrelid::regclass, a.attname, a.attname) || '|' || k.conrelid::regclass::text || '.' || a.attname
          from pg_constraint k
          join pg_attribute a on a.attrelid = k.conrelid and a.attnum = k.conkey[1]
         where k.contype = 'f' and k.confrelid = 'public.perfis'::regclass
         order by 1
      loop
        execute split_part(v_cond, '|', 1) into n using v_admins;
        if n > 0 then
          v_texto := coalesce(v_texto || ', ', '') || split_part(v_cond, '|', 2) || '=' || n;
        end if;
      end loop;
      if v_texto is not null then
        raise exception 'RECUSADO: linhas que ficam apontam para contas que seriam apagadas (%). Com v_apagar_auditoria = false a auditoria preservada costuma causar isto: use true, ou mantenha os usuarios. Nada foi apagado.', v_texto
          using errcode = 'LD009';
      end if;
    end if;
    if v_filtros[i] = '' then
      execute format('delete from %I.%I where true', v_esquemas[i], v_tabelas[i]);
    else
      execute format('delete from %I.%I where not (%I = any ($1))', v_esquemas[i], v_tabelas[i], v_filtros[i])
        using v_admins;
    end if;
    get diagnostics n = row_count;
    v_apagadas := v_apagadas || n;
  end loop;

  -- >>> RELIGAR (nao mude esta linha de comentario: os testes dependem dela)
  for i in 1 .. v_n loop
    if v_modos[i] <> '' then
      execute format(case when v_modos[i] = 'A' then 'alter table %I.%I enable always trigger %I'
                          else 'alter table %I.%I enable trigger %I' end,
                     v_esquemas[i], v_tabelas[i], v_gatilhos[i]);
    end if;
  end loop;

  -- ---- 6. Verificacoes (qualquer falha desfaz TUDO) ---------------------------------------
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

  for i in 1 .. array_length(v_mantidas, 1) loop
    execute format('select count(*) from public.%I', v_mantidas[i]) into n;
    if n <> v_man_antes[i] then
      raise exception 'VERIFICACAO FALHOU: public.% mudou de % para % linha(s). Tudo foi desfeito.', v_mantidas[i], v_man_antes[i], n
        using errcode = 'LD005';
    end if;
  end loop;

  -- Cada entrada: ativa => sobra exatamente antes - alvo (so as linhas dos admins ativos, ou 0);
  -- inativa => nao mudou.
  for i in 1 .. v_n loop
    execute format('select count(*) from %I.%I', v_esquemas[i], v_tabelas[i]) into n;
    v_esperado := case when v_ativa[i] then v_antes[i] - v_alvo[i] else v_antes[i] end;
    if n <> v_esperado or (v_ativa[i] and v_apagadas[i] <> v_alvo[i]) then
      raise exception 'VERIFICACAO FALHOU: % deveria ter % linha(s) e tem %. Tudo foi desfeito.', v_nome[i], v_esperado, n
        using errcode = 'LD005';
    end if;
  end loop;

  -- Ao menos 1 admin ativo e, no modo so admins, so admins ativos em perfis.
  select count(*) filter (where p.role = 'admin' and p.ativo), count(*) filter (where not (p.role = 'admin' and p.ativo))
    into v_esperado, n from public.perfis p;
  if v_esperado < 1 or v_esperado <> cardinality(v_admins) or (not v_manter_usuarios and n <> 0) then
    raise exception 'VERIFICACAO FALHOU: admins ativos/perfis fora do esperado depois da limpeza. Tudo foi desfeito.'
      using errcode = 'LD005';
  end if;

  -- ---- 7. Registro DURAVEL da limpeza (1 linha em auditoria, sem dados pessoais) -------------
  -- Feito com os gatilhos ja religados e verificados (o append-only so bloqueia UPDATE/DELETE).
  -- Usa tabela='perfis' e ator_id nulo porque os CHECKs ck_auditoria_tabela/ck_auditoria_acao so aceitam
  -- os valores ja existentes (nao ha acao/tabela propria para esta limpeza); registro_id tambem nulo.
  for i in 1 .. v_n loop
    if v_ativa[i] then
      v_json := v_json || jsonb_build_object(v_nome[i], v_apagadas[i]);
    end if;
  end loop;
  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, detalhes)
  values ('perfis', null, 'acao_administrativa', null, 'limpeza_pre_lancamento',
          jsonb_build_object('apagadas', v_json, 'auditoria_apagada', v_apagar_auditoria,
                             'usuarios_mantidos', v_manter_usuarios, 'lacrado', v_lacrar));
  select count(*) into n from public.auditoria;
  v_esperado := (case when v_apagar_auditoria then 0 else v_aud_antes end) + 1;
  if n <> v_esperado then
    raise exception 'VERIFICACAO FALHOU: a auditoria deveria ter % linha(s) e tem %. Tudo foi desfeito.', v_esperado, n
      using errcode = 'LD005';
  end if;

  -- ---- 8. Lacre (opcional), ANTES do raise do ensaio: o rollback do ensaio o desfaz, mas
  --         assim o ensaio prova que o papel consegue comentar o banco ---------------------------
  if v_lacrar then
    execute format('comment on database %I is %L', v_banco, c_lacre);
    perform set_config('sisveiculos.comentario_anterior', coalesce(v_comentario, ''), true);
  end if;

  -- ---- 9. Relatorio (so contagens) ----------------------------------------------------------
  -- APAGADAS = este modo. MANTER_USU / SO_ADMINS = quanto cada valor de v_manter_usuarios apagaria
  -- (com o v_apagar_auditoria atual).
  v_rel := 'MODO: ' || case when v_manter_usuarios then 'mantem usuarios (v_manter_usuarios = true)'
                            else 'so admins ativos (v_manter_usuarios = false)' end
           || '; auditoria ' || case when v_apagar_auditoria then 'apagada' else 'preservada' end || E'\n\n'
           || rpad('TABELA', 26) || lpad('ANTES', 8) || lpad('APAGADAS', 11) || lpad('MANTER_USU', 12)
           || lpad('SO_ADMINS', 11) || E'\n';
  for i in 1 .. v_n loop
    v_rel := v_rel || rpad(v_nome[i], 26) || lpad(v_antes[i]::text, 8)
             || lpad(case when v_ativa[i] then v_apagadas[i]::text
                          when v_grupos[i] = 'auditoria' then 'preservada' else 'mantida' end, 11)
             || lpad(case v_grupos[i] when 'sempre' then v_antes[i]::text
                                      when 'auditoria' then case when v_apagar_auditoria then v_antes[i]::text else 'preservada' end
                                      else '0' end, 12)
             || lpad(case when v_grupos[i] = 'auditoria' and not v_apagar_auditoria then 'preservada'
                          else v_alvo[i]::text end, 11) || E'\n';
  end loop;
  v_rel := v_rel || E'\nMANTIDAS (nao mudam): ';
  for i in 1 .. array_length(v_mantidas, 1) loop
    v_rel := v_rel || v_mantidas[i] || '=' || v_man_antes[i] || case when i < array_length(v_mantidas, 1) then ', ' else '' end;
  end loop;
  v_rel := v_rel || E'\nCONTAS: ' || cardinality(v_admins) || ' admin(s) ativo(s) ficam sempre. Com v_manter_usuarios = false seriam apagadas '
                 || v_alvo[v_n] || ' conta(s) do Auth (auth.users) e ' || v_alvo[i_perfis] || ' perfil(is); com true, nenhuma.'
                 || E'\nAuditoria depois da limpeza: '
                 || case when v_apagar_auditoria then '1 linha (o registro da limpeza)'
                         else (v_aud_antes + 1)::text || ' linhas (as ' || v_aud_antes || ' de antes + o registro da limpeza)' end
                 || E'\nGatilhos do schema public: identicos antes e depois (todos verificados).'
                 || E'\nArquivos por bucket (o SQL NAO apaga; rode depois app.ops.limpar_storage): ' || v_storage;
  if v_lacrar then
    v_rel := v_rel || E'\nLACRE ' || case when v_executar then 'gravado' else '(simulado no ensaio; nao gravado)' end
                   || ': este banco nao aceita mais esta limpeza. Comentario anterior substituido: '
                   || case when coalesce(v_comentario, '') = '' then '(vazio)' else '"' || v_comentario || '"' end
                   || E'\nPara desfazer (decisao consciente do dono do banco): comment on database ' || v_banco
                   || ' is ' || quote_literal(case when coalesce(v_comentario, '') = '' then c_padrao else v_comentario end) || ';';
  end if;

  if not v_executar then
    -- ENSAIO: o proprio erro desfaz tudo o que foi feito acima (inclusive o registro e o lacre).
    raise exception E'ENSAIO: nada foi apagado.\n\n*** COPIE ESTE NUMERO PARA v_confere_total: % ***\n(vale so para este modo; mudou v_manter_usuarios ou v_apagar_auditoria, refaca o ensaio)\n\n%\n\nCopie ou fotografe esta mensagem ANTES de executar. Esta mensagem de erro e o resultado esperado do ensaio. Para apagar de verdade mude v_executar, v_confirmacao e v_confere_total (veja o topo do arquivo).', v_total, v_rel
      using errcode = 'LD001';
  end if;

  -- Guarda as contagens apagadas para a consulta final (o Studio pode nao exibir NOTICE).
  perform set_config('sisveiculos.apagadas', v_json::text, true);
  raise notice E'LIMPEZA CONCLUIDA.\n%', v_rel;
end
$limpeza$;

-- Resumo FINAL (so aparece se o bloco acima terminou sem erro). Mostra as linhas que restaram e,
-- em apagadas_agora, quantas foram apagadas nesta execucao (vazio = nao entrou no modo). COPIE/FOTOGRAFE.
select r.tabela, r.linhas, a.j ->> r.tabela as apagadas_agora, r.situacao
from (
  select 1 as o, 'checklist_fotos' as tabela, count(*) as linhas, 'apagada' as situacao from public.checklist_fotos
  union all select 2, 'checklists', count(*), 'apagada' from public.checklists
  union all select 3, 'notificacoes', count(*), 'apagada' from public.notificacoes
  union all select 4, 'localizacoes_viagem', count(*), 'apagada' from public.localizacoes_viagem
  union all select 5, 'requisicoes_arquivadas', count(*), 'apagada' from public.requisicoes_arquivadas
  union all select 6, 'requisicoes', count(*), 'apagada' from public.requisicoes
  union all select 7, 'acessos_login', count(*), 'apagada' from public.acessos_login
  union all select 8, 'envios_email', count(*), 'apagada' from public.envios_email
  union all select 9, 'ia_uso', count(*), 'apagada' from public.ia_uso
  union all select 10, 'desafios_webauthn', count(*), 'apagada' from public.desafios_webauthn
  union all select 11, 'reautenticacoes_webauthn', count(*), 'apagada' from public.reautenticacoes_webauthn
  union all select 12, 'auditoria', count(*), 'apagada se v_apagar_auditoria = true (resta o registro da limpeza)' from public.auditoria
  union all select 13, 'convites', count(*), 'contas: todos apagados se v_manter_usuarios = false' from public.convites
  union all select 14, 'perfil_fotos', count(*), 'contas: so as de admin ativo ficam se v_manter_usuarios = false' from public.perfil_fotos
  union all select 15, 'push_inscricoes', count(*), 'contas: so as de admin ativo ficam se v_manter_usuarios = false' from public.push_inscricoes
  union all select 16, 'credenciais_webauthn', count(*), 'contas: so as de admin ativo ficam se v_manter_usuarios = false' from public.credenciais_webauthn
  union all select 17, 'perfis', count(*), 'contas: so admins ativos ficam se v_manter_usuarios = false' from public.perfis
  union all select 18, 'auth.users', count(*), 'contas: so admins ativos ficam se v_manter_usuarios = false' from auth.users
  union all select 19, 'veiculos', count(*), 'mantida' from public.veiculos
  union all select 20, 'configuracoes', count(*), 'mantida' from public.configuracoes
  union all select 21, 'ia_configuracoes', count(*), 'mantida' from public.ia_configuracoes
  union all select 22, 'saudacoes', count(*), 'mantida' from public.saudacoes
  union all select 23, 'LACRE do banco', 0, coalesce(nullif(shobj_description(
      (select oid from pg_database where datname = current_database()), 'pg_database'), ''), '(sem lacre)')
  union all select 24, 'Comentario anterior (so se lacrou agora)', 0,
      coalesce(nullif(current_setting('sisveiculos.comentario_anterior', true), ''), '-')
) r
cross join (select nullif(current_setting('sisveiculos.apagadas', true), '')::jsonb as j) a
order by r.o;
